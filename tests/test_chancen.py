"""
Tests des Chancen-Motors.

Zwei Dinge müssen stimmen: Die Rangfolge muss dem gesunden Menschenverstand
folgen (wertvolle Seite vor unwichtiger), und das System muss offenlegen, auf
welcher Grundlage es sortiert — Umsatz oder ersatzweise Besucherzahlen.
"""

import pytest

from seo_autopilot.chancen import (
    AUFWAND_GROSS,
    AUFWAND_KLEIN,
    MAX_JE_SEITE,
    MIN_MESSUNGEN_FUER_ERFAHRUNG,
    SICHERHEIT_NEUTRAL,
    ist_pflichtseite,
    als_text,
    bewerte_chancen,
    potenzial,
    sicherheit_je_typ,
)
from seo_autopilot.changelog_book import AKTION_META_TITLE
from seo_autopilot.wirkung import (
    URTEIL_UNVERAENDERT,
    URTEIL_VERBESSERT,
    Messung,
    speichere_messung,
    tabelle_anlegen,
)


def _befund(typ="short_title", url="https://x.de/a", position=None, besucher=0):
    return {
        "type": typ,
        "title": f"Befund {typ}",
        "url": url,
        "position": position,
        "besucher": besucher,
    }


class TestPotenzial:
    def test_seite_eins_unten_hat_den_groessten_hebel(self):
        """Von Platz 8 auf 3 ist ein grosser Klicksprung und gut erreichbar."""
        assert potenzial(8.0) > potenzial(2.0)
        assert potenzial(8.0) > potenzial(15.0)

    def test_weit_hinten_ist_kaum_erreichbar(self):
        assert potenzial(60.0) < potenzial(15.0)

    def test_ohne_position_mittlerer_wert(self):
        assert 0 < potenzial(None) < 1.0


class TestRangfolge:
    def test_wertvollere_seite_kommt_zuerst(self):
        chancen = bewerte_chancen(
            [_befund(url="https://x.de/wichtig"), _befund(url="https://x.de/egal")],
            projekt="p",
            seitenwerte={
                "https://x.de/wichtig": {"wert": 5000.0, "besucher": 100},
                "https://x.de/egal": {"wert": 50.0, "besucher": 100},
            },
        )
        assert chancen[0].url == "https://x.de/wichtig"

    def test_kleiner_aufwand_schlaegt_grossen_bei_gleichem_nutzen(self):
        chancen = bewerte_chancen(
            [
                _befund(typ="short_title", url="https://x.de/a", besucher=100),
                _befund(typ="thin_content", url="https://x.de/b", besucher=100),
            ],
            projekt="p",
        )
        assert chancen[0].issue_type == "short_title"
        assert chancen[0].aufwand == AUFWAND_KLEIN
        assert chancen[1].aufwand == AUFWAND_GROSS

    def test_ohne_geschaeftswert_zaehlen_besucher(self):
        chancen = bewerte_chancen(
            [
                _befund(url="https://x.de/viel", besucher=1000),
                _befund(url="https://x.de/wenig", besucher=10),
            ],
            projekt="p",
        )
        assert chancen[0].url == "https://x.de/viel"
        assert not chancen[0].nach_umsatz

    def test_geschaeftswert_schlaegt_besucherzahl(self):
        """Der eigentliche Zweck von Phase 3: Umsatz vor Reichweite."""
        chancen = bewerte_chancen(
            [
                _befund(url="https://x.de/ratgeber", besucher=5000),
                _befund(url="https://x.de/factoring", besucher=20),
            ],
            projekt="p",
            seitenwerte={
                "https://x.de/ratgeber": {"wert": 0.0, "besucher": 5000},
                "https://x.de/factoring": {"wert": 9000.0, "besucher": 20},
            },
        )
        assert chancen[0].url == "https://x.de/factoring"
        assert chancen[0].nach_umsatz


class TestErfahrungswerte:
    @pytest.fixture
    def db(self, tmp_path):
        pfad = str(tmp_path / "t.db")
        tabelle_anlegen(pfad)
        return pfad

    def _messung(self, i, urteil):
        return Messung(
            id=f"m{i}",
            change_id=f"c{i}",
            project_id="p",
            ziel_url="https://x.de/a",
            aktion=AKTION_META_TITLE,
            urheber="autopilot",
            fenster_tage=7,
            geaendert_am="2026-07-01",
            gemessen_am="2026-07-10",
            vorher_von="2026-06-24",
            vorher_bis="2026-06-30",
            nachher_von="2026-07-02",
            nachher_bis="2026-07-08",
            urteil=urteil,
        )

    def test_zu_wenige_messungen_ergeben_keine_erfahrung(self, db):
        """Eine Quote aus zwei Messungen ist keine Erfahrung."""
        for i in range(MIN_MESSUNGEN_FUER_ERFAHRUNG - 1):
            speichere_messung(db, self._messung(i, URTEIL_VERBESSERT))
        assert sicherheit_je_typ(db) == {}

    def test_genug_messungen_liefern_trefferquote(self, db):
        for i in range(4):
            urteil = URTEIL_VERBESSERT if i < 3 else URTEIL_UNVERAENDERT
            speichere_messung(db, self._messung(i, urteil))

        werte = sicherheit_je_typ(db)

        assert werte[AKTION_META_TITLE] == pytest.approx(0.75)

    def test_erfahrung_fliesst_in_die_bewertung(self, db):
        for i in range(4):
            speichere_messung(db, self._messung(i, URTEIL_VERBESSERT))

        chancen = bewerte_chancen(
            [_befund(besucher=100)], projekt="p", db_pfad=db
        )

        assert chancen[0].sicherheit_belegt
        assert chancen[0].sicherheit == pytest.approx(1.0)
        assert "Trefferquote" in chancen[0].begruendung

    def test_ohne_datenbank_neutrale_sicherheit(self):
        chancen = bewerte_chancen([_befund(besucher=100)], projekt="p")
        assert chancen[0].sicherheit == SICHERHEIT_NEUTRAL
        assert not chancen[0].sicherheit_belegt
        assert "keine Erfahrungswerte" in chancen[0].begruendung


class TestPflichtseiten:
    """Beim ersten Live-Lauf belegte das Impressum fuenf von sechs Plaetzen."""

    def test_impressum_und_datenschutz_erkannt(self):
        assert ist_pflichtseite("https://x.de/impressum")
        assert ist_pflichtseite("https://x.de/datenschutz")
        assert ist_pflichtseite("/agb")

    def test_normale_seite_ist_keine_pflichtseite(self):
        assert not ist_pflichtseite("https://x.de/finanzierung/factoring")
        assert not ist_pflichtseite("https://x.de/")

    def test_pflichtseite_verliert_gegen_verkaufsseite(self):
        chancen = bewerte_chancen(
            [
                _befund(url="https://x.de/impressum", besucher=500, position=5.0),
                _befund(url="https://x.de/leistung", besucher=200, position=5.0),
            ],
            projekt="p",
        )
        assert chancen[0].url == "https://x.de/leistung"

    def test_daempfer_wird_im_bericht_genannt(self):
        chancen = bewerte_chancen(
            [_befund(url="https://x.de/impressum", besucher=500)], projekt="p"
        )
        assert "Pflichtseite" in chancen[0].begruendung

    def test_mit_geschaeftswert_kein_daempfer_noetig(self):
        """Ein echter Wert regelt die Gewichtung selbst."""
        chancen = bewerte_chancen(
            [_befund(url="https://x.de/impressum", besucher=500)],
            projekt="p",
            seitenwerte={"https://x.de/impressum": {"wert": 800.0, "besucher": 500}},
        )
        assert "Pflichtseite" not in chancen[0].begruendung


class TestEineSeiteFlutetNicht:
    def test_hoechstens_zwei_befunde_je_seite(self):
        befunde = [
            _befund(typ=f"typ_{i}", url="https://x.de/a", besucher=100)
            for i in range(6)
        ]
        text = als_text(bewerte_chancen(befunde, projekt="p"), anzahl=10)
        assert text.count("https://x.de/a") == MAX_JE_SEITE
        assert "weitere Befund" in text

    def test_andere_seiten_kommen_dadurch_zum_zug(self):
        befunde = [
            _befund(typ=f"typ_{i}", url="https://x.de/viel", besucher=1000)
            for i in range(5)
        ] + [_befund(typ="einzeln", url="https://x.de/wenig", besucher=10)]
        text = als_text(bewerte_chancen(befunde, projekt="p"), anzahl=5)
        assert "https://x.de/wenig" in text


class TestOffenlegung:
    def test_bericht_warnt_wenn_nach_besuchern_sortiert_wird(self):
        text = als_text(bewerte_chancen([_befund(besucher=100)], projekt="p"))
        assert "NICHT nach Umsatz" in text

    def test_bericht_warnt_bei_fehlenden_erfahrungswerten(self):
        text = als_text(bewerte_chancen([_befund(besucher=100)], projekt="p"))
        assert "keine Erfahrungswerte" in text

    def test_keine_euro_prognose_im_bericht(self):
        """Die Punktzahl ist eine Rangfolge, keine Vorhersage."""
        text = als_text(
            bewerte_chancen(
                [_befund(besucher=100)],
                projekt="p",
                seitenwerte={"https://x.de/a": {"wert": 5000.0, "besucher": 100}},
            )
        )
        assert "EUR" not in text

    def test_leere_liste_bleibt_verstaendlich(self):
        assert "Keine bewertbaren" in als_text([])

    def test_befund_ohne_typ_wird_uebersprungen(self):
        assert bewerte_chancen([{"url": "https://x.de/a"}], projekt="p") == []
