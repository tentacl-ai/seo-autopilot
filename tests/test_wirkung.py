"""
Tests der Wirkungsmessung.

Schwerpunkt liegt bewusst auf den Sperren, nicht auf dem Normalfall: Eine
Wirkungsmessung, die zu gern "verbessert" meldet, richtet mehr Schaden an als
gar keine — sie führt dazu, dass wirkungslose Maßnahmen wiederholt werden.
Deshalb wird hier vor allem geprüft, dass KEIN Urteil gefällt wird, wo keines
zulässig ist.
"""

import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from seo_autopilot.changelog_book import (
    AKTION_META_DESCRIPTION,
    AKTION_META_TITLE,
    STATUS_ZURUECKGENOMMEN,
    URHEBER_AUTOPILOT,
    URHEBER_MENSCH,
    notiere_aenderung,
)
from seo_autopilot.wirkung import (
    GSC_VERZUG_TAGE,
    MESSFENSTER,
    MIN_IMPRESSIONEN,
    MIN_IMPRESSIONEN_PRO_TAG,
    URTEIL_NICHT_ZURECHENBAR,
    URTEIL_UNVERAENDERT,
    URTEIL_VERBESSERT,
    URTEIL_VERSCHLECHTERT,
    URTEIL_ZU_WENIG_DATEN,
    Messung,
    als_text,
    beurteile,
    bilanz,
    bilanz_als_text,
    faellige_messungen,
    fensterbereiche,
    messbar_ab,
    mindest_impressionen,
    messungen,
    miss_eine,
    speichere_messung,
    tabelle_anlegen,
)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Frische Datenbank je Test."""
    pfad = str(tmp_path / "test.db")
    tabelle_anlegen(pfad)
    return pfad


def _fenster(clicks=0, impressions=0, position=0.0):
    """Ein Kennzahlensatz, wie ihn die Search Console liefert."""
    return {
        "clicks": clicks,
        "impressions": impressions,
        "position": position,
        "ctr": 0.0,
        "hat_daten": impressions > 0,
    }


def _trage_ein(db, projekt="joseph", url="https://example.com/", tage_her=30,
               aktion=AKTION_META_TITLE, urheber=URHEBER_AUTOPILOT, status=None):
    """Legt eine Änderung im Änderungsbuch an und gibt ihre ID zurück."""
    zeitpunkt = datetime.now(timezone.utc) - timedelta(days=tage_her)
    kwargs = {}
    if status:
        kwargs["status"] = status
    return notiere_aenderung(
        db,
        projekt,
        aktion,
        ziel_url=url,
        urheber=urheber,
        vorher="Alt",
        nachher="Neu",
        zeitpunkt=zeitpunkt,
        **kwargs,
    )


def _holer(vorher, nachher):
    """Baut ein hole_fenster(), das erst `vorher`, dann `nachher` liefert."""
    aufrufe = {"n": 0}

    async def hole(url, von, bis):
        aufrufe["n"] += 1
        return vorher if aufrufe["n"] == 1 else nachher

    return hole


# ---------------------------------------------------------------------------
# Zeitfenster
# ---------------------------------------------------------------------------


class TestFensterbereiche:
    def test_aenderungstag_gehoert_in_kein_fenster(self):
        """Am Tag der Änderung stand die Seite teils alt, teils neu online."""
        tag = date(2026, 6, 15)
        (v_von, v_bis), (n_von, n_bis) = fensterbereiche(tag, 7)

        assert v_bis == date(2026, 6, 14), "Vorher-Fenster endet am Vortag"
        assert n_von == date(2026, 6, 16), "Nachher-Fenster beginnt am Folgetag"
        assert tag not in (v_bis, n_von)

    def test_beide_fenster_sind_gleich_lang(self):
        """Ungleiche Fenster würden jeden Vergleich verzerren."""
        tag = date(2026, 6, 15)
        for laenge in MESSFENSTER:
            (v_von, v_bis), (n_von, n_bis) = fensterbereiche(tag, laenge)
            vorher_tage = (v_bis - v_von).days + 1
            nachher_tage = (n_bis - n_von).days + 1
            assert vorher_tage == laenge
            assert nachher_tage == laenge

    def test_messbar_erst_nach_gsc_verzug(self):
        """Zu früh gemessen heisst: halb leeres Nachher-Fenster."""
        tag = date(2026, 6, 15)
        _, (_, nachher_bis) = fensterbereiche(tag, 7)

        frueheste = messbar_ab(tag, 7)

        assert frueheste == nachher_bis + timedelta(days=GSC_VERZUG_TAGE)
        assert frueheste > nachher_bis, "Verzug der Search Console eingerechnet"


# ---------------------------------------------------------------------------
# Urteil
# ---------------------------------------------------------------------------


class TestBeurteile:
    def test_zu_wenig_daten_kein_urteil(self):
        """Sprung von 2 auf 5 Klicks sieht nach +150 % aus und heisst nichts."""
        urteil, notiz = beurteile(
            _fenster(clicks=2, impressions=10, position=8.0),
            _fenster(clicks=5, impressions=12, position=3.0),
        )
        assert urteil == URTEIL_ZU_WENIG_DATEN
        assert "10" in notiz
        assert str(mindest_impressionen(7)) in notiz, "nennt die noetige Zahl"

    def test_schwelle_ist_einschliessend(self):
        """Genau die Mindestmenge reicht — sonst waere die Grenze willkuerlich."""
        noetig = mindest_impressionen(7)
        urteil, _ = beurteile(
            _fenster(clicks=3, impressions=noetig, position=8.0),
            _fenster(clicks=9, impressions=noetig * 3, position=4.0),
        )
        assert urteil != URTEIL_ZU_WENIG_DATEN

    def test_position_nach_vorn_ist_verbesserung(self):
        urteil, notiz = beurteile(
            _fenster(clicks=10, impressions=400, position=12.0),
            _fenster(clicks=25, impressions=520, position=6.5),
        )
        assert urteil == URTEIL_VERBESSERT
        assert "+5.5" in notiz, "Vorzeichen gedreht: positiv = nach vorn"

    def test_position_zurueck_ist_verschlechterung(self):
        urteil, notiz = beurteile(
            _fenster(clicks=25, impressions=520, position=6.5),
            _fenster(clicks=10, impressions=400, position=12.0),
        )
        assert urteil == URTEIL_VERSCHLECHTERT
        assert "-5.5" in notiz

    def test_kleine_positionsbewegung_ist_rauschen(self):
        """Positionen schwanken taeglich, ohne dass jemand etwas tut."""
        urteil, _ = beurteile(
            _fenster(clicks=20, impressions=400, position=8.0),
            _fenster(clicks=21, impressions=410, position=7.6),
        )
        assert urteil == URTEIL_UNVERAENDERT

    def test_bei_patt_entscheiden_die_einblendungen(self):
        urteil, notiz = beurteile(
            _fenster(clicks=20, impressions=400, position=8.0),
            _fenster(clicks=30, impressions=600, position=7.9),
        )
        assert urteil == URTEIL_VERBESSERT
        assert "Einblendungen" in notiz

    def test_neu_sichtbar_zaehlt_trotz_fehlender_vorher_daten(self):
        """Von 'kommt nicht vor' zu 'wird gefunden' ist eine echte Aussage."""
        urteil, notiz = beurteile(
            _fenster(clicks=0, impressions=0, position=0.0),
            _fenster(clicks=4, impressions=180, position=9.0),
        )
        assert urteil == URTEIL_VERBESSERT
        assert "nicht in der Suche sichtbar" in notiz

    def test_neu_sichtbar_aber_nur_minimal_bleibt_ohne_urteil(self):
        """Drei Einblendungen sind kein Durchbruch."""
        urteil, _ = beurteile(
            _fenster(clicks=0, impressions=0, position=0.0),
            _fenster(clicks=0, impressions=3, position=40.0),
        )
        assert urteil == URTEIL_ZU_WENIG_DATEN

    def test_ganz_verschwunden_ist_verschlechterung(self):
        urteil, notiz = beurteile(
            _fenster(clicks=30, impressions=800, position=4.0),
            _fenster(clicks=0, impressions=0, position=0.0),
        )
        assert urteil == URTEIL_VERSCHLECHTERT
        assert "nicht mehr auf" in notiz

    def test_klicks_allein_kippen_kein_urteil(self):
        """Klicks schwanken staerker durch Saison als durch unsere Arbeit."""
        urteil, _ = beurteile(
            _fenster(clicks=10, impressions=400, position=8.0),
            _fenster(clicks=40, impressions=405, position=8.0),
        )
        assert urteil == URTEIL_UNVERAENDERT


class TestDatenmengeSkaliertMitFenster:
    """30 Einblendungen sind in 7 Tagen duenn und in 56 Tagen nichts."""

    def test_lange_fenster_verlangen_mehr_daten(self):
        vorher = _fenster(clicks=5, impressions=100, position=9.0)
        nachher = _fenster(clicks=12, impressions=140, position=4.0)

        kurz, _ = beurteile(vorher, nachher, fenster_tage=7)
        lang, notiz = beurteile(vorher, nachher, fenster_tage=56)

        assert kurz == URTEIL_VERBESSERT, "100 Einblendungen in 7 Tagen reichen"
        assert lang == URTEIL_ZU_WENIG_DATEN, "in 56 Tagen sind 100 zu wenig"
        assert "280" in notiz, "nennt die noetige Zahl fuer dieses Fenster"

    def test_mindestmenge_waechst_mit_der_fensterlaenge(self):
        assert mindest_impressionen(7) < mindest_impressionen(56)

    def test_untergrenze_gilt_auch_bei_winzigem_fenster(self):
        assert mindest_impressionen(1) == MIN_IMPRESSIONEN


class TestWidersprechendeSignale:
    """Position vorn, aber weniger Sichtbarkeit — kein Erfolg.

    Genau dieser Fall trat beim ersten Live-Lauf auf (joseph-Startseite:
    Position 6,7 → 2,8 bei gleichzeitig weniger Einblendungen und Klicks).
    Ohne diese Regel haette das Werkzeug eine Massnahme belohnt, die
    Sichtbarkeit gekostet hat.
    """

    def test_position_vorn_aber_weniger_sichtbarkeit_ist_kein_erfolg(self):
        urteil, notiz = beurteile(
            _fenster(clicks=16, impressions=93, position=6.5),
            _fenster(clicks=9, impressions=76, position=3.5),
            fenster_tage=14,
        )
        assert urteil == URTEIL_UNVERAENDERT
        assert "Widersprüchlich" in notiz
        assert "Suchbegriff-Mix" in notiz

    def test_position_vorn_mit_mehr_sichtbarkeit_bleibt_erfolg(self):
        """Die Regel darf echte Verbesserungen nicht wegschneiden."""
        urteil, _ = beurteile(
            _fenster(clicks=16, impressions=400, position=6.5),
            _fenster(clicks=30, impressions=520, position=3.5),
            fenster_tage=14,
        )
        assert urteil == URTEIL_VERBESSERT

    def test_position_zurueck_bleibt_verschlechterung(self):
        """Widerspruchsregel gilt nur in eine Richtung — sie beschoenigt nicht."""
        urteil, _ = beurteile(
            _fenster(clicks=30, impressions=520, position=3.5),
            _fenster(clicks=9, impressions=300, position=9.0),
            fenster_tage=14,
        )
        assert urteil == URTEIL_VERSCHLECHTERT

    def test_gleiche_klicks_kippen_nicht(self):
        """Nur wenn BEIDE Signale zurueckgehen, ist es widerspruechlich."""
        urteil, _ = beurteile(
            _fenster(clicks=16, impressions=400, position=6.5),
            _fenster(clicks=16, impressions=380, position=3.5),
            fenster_tage=14,
        )
        assert urteil == URTEIL_VERBESSERT


# ---------------------------------------------------------------------------
# Fälligkeit
# ---------------------------------------------------------------------------


class TestFaelligkeit:
    def test_frische_aenderung_ist_noch_nicht_faellig(self, db):
        _trage_ein(db, tage_her=2)
        assert faellige_messungen(db) == []

    def test_aeltere_aenderung_wird_faellig(self, db):
        _trage_ein(db, tage_her=30)
        faellig = faellige_messungen(db)
        fenster = {f for _, f in faellig}
        assert 7 in fenster and 14 in fenster
        assert 56 not in fenster, "56-Tage-Fenster ist nach 30 Tagen nicht durch"

    def test_kein_zweites_mal_messen(self, db):
        """Zahlen eines abgeschlossenen Zeitraums aendern sich nicht mehr."""
        change_id = _trage_ein(db, tage_her=30)
        vorher = len(faellige_messungen(db))

        speichere_messung(db, _messung(change_id, fenster=7))

        nachher = len(faellige_messungen(db))
        assert nachher == vorher - 1

    def test_zurueckgenommene_aenderung_wird_nicht_gemessen(self, db):
        """Was nicht mehr online steht, erklaert keine heutigen Rankings."""
        _trage_ein(db, tage_her=30, status=STATUS_ZURUECKGENOMMEN)
        assert faellige_messungen(db) == []

    def test_aenderung_ohne_adresse_wird_uebersprungen(self, db):
        """Eine Datei ohne oeffentliche URL ist nicht nachschlagbar."""
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            datei_pfad="/var/www/index.html",
            zeitpunkt=datetime.now(timezone.utc) - timedelta(days=30),
        )
        assert faellige_messungen(db) == []

    def test_filter_auf_projekt(self, db):
        _trage_ein(db, projekt="joseph", tage_her=30)
        _trage_ein(db, projekt="tentacl-ai", tage_her=30, url="https://tentacl.ai/")

        nur_joseph = faellige_messungen(db, project_id="joseph")

        assert nur_joseph
        assert all(a.project_id == "joseph" for a, _ in nur_joseph)


# ---------------------------------------------------------------------------
# Messen
# ---------------------------------------------------------------------------


def _messung(change_id, fenster=7, urteil=URTEIL_VERBESSERT,
             aktion=AKTION_META_TITLE, urheber=URHEBER_AUTOPILOT,
             projekt="joseph", pos_vorher=10.0, pos_nachher=5.0):
    return Messung(
        id=str(uuid.uuid4()),
        change_id=change_id,
        project_id=projekt,
        ziel_url="https://example.com/",
        aktion=aktion,
        urheber=urheber,
        fenster_tage=fenster,
        geaendert_am="2026-06-15",
        gemessen_am=date.today().isoformat(),
        vorher_von="2026-06-08",
        vorher_bis="2026-06-14",
        nachher_von="2026-06-16",
        nachher_bis="2026-06-22",
        vorher_position=pos_vorher,
        nachher_position=pos_nachher,
        vorher_impressionen=400,
        nachher_impressionen=500,
        urteil=urteil,
    )


class TestMissEine:
    @pytest.mark.asyncio
    async def test_speichert_ergebnis(self, db):
        change_id = _trage_ein(db, tage_her=30)
        aenderung = _lade_aenderung(db, change_id)

        m = await miss_eine(
            db,
            aenderung,
            7,
            _holer(
                _fenster(clicks=10, impressions=400, position=12.0),
                _fenster(clicks=25, impressions=500, position=6.0),
            ),
        )

        assert m is not None
        assert m.urteil == URTEIL_VERBESSERT
        assert len(messungen(db)) == 1

    @pytest.mark.asyncio
    async def test_abfragefehler_speichert_nichts(self, db):
        """Sonst wuerde eine kaputte Abfrage als 'keine Wirkung' verbucht."""
        change_id = _trage_ein(db, tage_her=30)
        aenderung = _lade_aenderung(db, change_id)

        async def kaputt(url, von, bis):
            return None

        m = await miss_eine(db, aenderung, 7, kaputt)

        assert m is None
        assert messungen(db) == []
        assert faellige_messungen(db), "bleibt faellig, wird erneut versucht"

    @pytest.mark.asyncio
    async def test_zweite_aenderung_macht_wirkung_unzurechenbar(self, db):
        """Titel UND Beschreibung geaendert — welche hat gewirkt?"""
        change_id = _trage_ein(db, tage_her=30, aktion=AKTION_META_TITLE)
        _trage_ein(db, tage_her=28, aktion=AKTION_META_DESCRIPTION)
        aenderung = _lade_aenderung(db, change_id)
        alle = _alle_aenderungen(db)

        m = await miss_eine(
            db,
            aenderung,
            7,
            _holer(
                _fenster(clicks=10, impressions=400, position=12.0),
                _fenster(clicks=25, impressions=500, position=6.0),
            ),
            alle_aenderungen=alle,
        )

        assert m.urteil == URTEIL_NICHT_ZURECHENBAR
        assert AKTION_META_DESCRIPTION in m.notiz

    @pytest.mark.asyncio
    async def test_aenderung_an_anderer_seite_stoert_nicht(self, db):
        """Nur dieselbe Adresse macht eine Messung unzurechenbar."""
        change_id = _trage_ein(db, tage_her=30, url="https://example.com/a")
        _trage_ein(db, tage_her=28, url="https://example.com/b")
        aenderung = _lade_aenderung(db, change_id)

        m = await miss_eine(
            db,
            aenderung,
            7,
            _holer(
                _fenster(clicks=10, impressions=400, position=12.0),
                _fenster(clicks=25, impressions=500, position=6.0),
            ),
            alle_aenderungen=_alle_aenderungen(db),
        )

        assert m.urteil == URTEIL_VERBESSERT

    @pytest.mark.asyncio
    async def test_fremde_aenderung_wird_gemessen_aber_markiert(self, db):
        change_id = _trage_ein(db, tage_her=30, urheber=URHEBER_MENSCH)
        aenderung = _lade_aenderung(db, change_id)

        m = await miss_eine(
            db,
            aenderung,
            7,
            _holer(
                _fenster(clicks=10, impressions=400, position=12.0),
                _fenster(clicks=25, impressions=500, position=6.0),
            ),
        )

        assert m.urteil == URTEIL_VERBESSERT
        assert m.belastbar
        assert not m.uns_zurechenbar, "fremde Arbeit ist nicht unser Erfolg"


# ---------------------------------------------------------------------------
# Bilanz
# ---------------------------------------------------------------------------


class TestBilanz:
    def test_trefferquote_je_aktionsart(self, db):
        for urteil in (URTEIL_VERBESSERT, URTEIL_VERBESSERT, URTEIL_VERSCHLECHTERT):
            speichere_messung(db, _messung(str(uuid.uuid4()), urteil=urteil))

        zeilen = bilanz(db)

        assert len(zeilen) == 1
        assert zeilen[0]["belastbar"] == 3
        assert zeilen[0]["besser"] == 2
        assert zeilen[0]["trefferquote"] == pytest.approx(0.67, abs=0.01)

    def test_urteilslose_messungen_verwaessern_die_quote_nicht(self, db):
        speichere_messung(db, _messung(str(uuid.uuid4()), urteil=URTEIL_VERBESSERT))
        speichere_messung(
            db, _messung(str(uuid.uuid4()), urteil=URTEIL_ZU_WENIG_DATEN)
        )
        speichere_messung(
            db, _messung(str(uuid.uuid4()), urteil=URTEIL_NICHT_ZURECHENBAR)
        )

        zeile = bilanz(db)[0]

        assert zeile["belastbar"] == 1
        assert zeile["trefferquote"] == 1.0
        assert zeile["ohne_urteil"] == 2, "bleibt sichtbar, zaehlt aber nicht mit"

    def test_fremde_aenderungen_schoenen_die_eigene_quote_nicht(self, db):
        speichere_messung(
            db, _messung(str(uuid.uuid4()), urteil=URTEIL_VERSCHLECHTERT)
        )
        for _ in range(3):
            speichere_messung(
                db,
                _messung(
                    str(uuid.uuid4()),
                    urteil=URTEIL_VERBESSERT,
                    urheber=URHEBER_MENSCH,
                ),
            )

        eigene = bilanz(db, nur_eigene=True)[0]

        assert eigene["belastbar"] == 1
        assert eigene["besser"] == 0

    def test_positionsschnitt_wird_gemittelt(self, db):
        speichere_messung(
            db, _messung(str(uuid.uuid4()), pos_vorher=10.0, pos_nachher=6.0)
        )
        speichere_messung(
            db, _messung(str(uuid.uuid4()), pos_vorher=10.0, pos_nachher=8.0)
        )

        zeile = bilanz(db)[0]

        assert zeile["positions_schnitt"] == pytest.approx(3.0)

    def test_leere_bilanz_bleibt_verstaendlich(self, db):
        assert "keine eigenen Messungen" in bilanz_als_text(bilanz(db))


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


class TestDarstellung:
    def test_leere_liste_erklaert_die_wartezeit(self):
        text = als_text([])
        assert "7 Tage" in text

    def test_fremde_messungen_werden_ausgewiesen(self, db):
        speichere_messung(
            db,
            _messung(str(uuid.uuid4()), urteil=URTEIL_VERBESSERT, urheber=URHEBER_MENSCH),
        )
        text = als_text(messungen(db))
        assert "nicht unser Verdienst" in text

    def test_bilanz_tabelle_zeigt_quote(self, db):
        speichere_messung(db, _messung(str(uuid.uuid4()), urteil=URTEIL_VERBESSERT))
        text = bilanz_als_text(bilanz(db))
        assert "100%" in text
        assert AKTION_META_TITLE in text


# ---------------------------------------------------------------------------
# Hilfen zum Nachladen
# ---------------------------------------------------------------------------


def _alle_aenderungen(db):
    from seo_autopilot.changelog_book import aenderungen

    return aenderungen(db, tage=0)


def _lade_aenderung(db, change_id):
    for a in _alle_aenderungen(db):
        if a.id == change_id:
            return a
    raise AssertionError(f"Änderung {change_id} nicht gefunden")
