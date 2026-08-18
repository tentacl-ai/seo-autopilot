"""
Tests der Ausführungssteuerung.

Der wichtigste Test dieser Datei ist `test_sperre_laesst_sich_nicht_aushebeln`.
Alles andere ist Komfort; die Sperrliste ist die Zusage, dass ein
unbeaufsichtigt laufendes System keine Website aus dem Index nehmen kann.
"""

from datetime import datetime, timedelta, timezone

import pytest

from seo_autopilot.ausfuehrung import (
    BETRIEBSART_AUTOPILOT,
    BETRIEBSART_BEOBACHTER,
    BETRIEBSART_COPILOT,
    BETRIEBSART_STANDARD,
    GESPERRT,
    STATUS_ABGELEHNT,
    STATUS_FREIGEGEBEN,
    STATUS_OFFEN,
    WEG_AUSFUEHREN,
    WEG_FREIGABE,
    WEG_NICHTS,
    als_text,
    betriebsart_von,
    entscheide,
    entscheiden,
    freigaben,
    ist_gesperrt,
    tabelle_anlegen,
    veraltete,
    zur_freigabe,
)


@pytest.fixture
def db(tmp_path):
    pfad = str(tmp_path / "t.db")
    tabelle_anlegen(pfad)
    return pfad


def _fix(typ="short_title", url="https://x.de/a"):
    return {
        "type": typ,
        "title": f"Befund {typ}",
        "url": url,
        "suggestion": "Neuer Titel",
    }


class TestHarteSperre:
    def test_sperre_laesst_sich_nicht_aushebeln(self):
        """Kern der Zusage: Kein Modus und keine Whitelist umgeht die Sperre.

        Wer 'missing_canonical' in whitelist_extra einträgt und Autopilot
        einschaltet, bekommt trotzdem eine Freigabe-Anfrage — sonst könnte
        eine Konfigurationszeile eine Website aus dem Index nehmen.
        """
        for typ in GESPERRT:
            weg, grund = entscheide(typ, BETRIEBSART_AUTOPILOT, in_whitelist=True)
            assert weg == WEG_FREIGABE, f"{typ} darf nie automatisch laufen"
            assert "Freigabe" in grund

    def test_canonical_und_robots_stehen_auf_der_liste(self):
        """Beide standen frueher in der Whitelist und liefen automatisch."""
        assert ist_gesperrt("missing_canonical")
        assert ist_gesperrt("missing_robots_txt")

    def test_geloeschte_seiten_und_umzuege_gesperrt(self):
        assert ist_gesperrt("delete_page")
        assert ist_gesperrt("url_migration")

    def test_harmloser_eingriff_ist_nicht_gesperrt(self):
        assert ist_gesperrt("short_title") is None
        assert ist_gesperrt("missing_meta_description") is None

    def test_jede_sperre_hat_eine_begruendung(self):
        """Ohne Begründung wirkt eine Sperre willkürlich und wird umgangen."""
        for typ, grund in GESPERRT.items():
            assert grund and len(grund) > 20, f"{typ} ohne verständlichen Grund"


class TestBetriebsarten:
    def test_neue_projekte_beobachten_nur(self):
        assert betriebsart_von({}) == BETRIEBSART_BEOBACHTER
        assert BETRIEBSART_STANDARD == BETRIEBSART_BEOBACHTER

    def test_tippfehler_faellt_auf_die_sichere_seite(self):
        """Ein Vertipper darf nie MEHR erlauben als gewollt."""
        assert betriebsart_von({"betriebsart": "autopilott"}) == BETRIEBSART_BEOBACHTER
        assert betriebsart_von({"betriebsart": "AUTO"}) == BETRIEBSART_BEOBACHTER

    def test_grossschreibung_ist_egal(self):
        assert betriebsart_von({"betriebsart": "Copilot"}) == BETRIEBSART_COPILOT

    def test_alte_konfiguration_verliert_die_ausfuehrung_nicht(self):
        """Wer auto_fix_enabled hatte, soll nach dem Update nicht stillstehen."""
        assert betriebsart_von({"auto_fix_enabled": True}) == BETRIEBSART_AUTOPILOT

    def test_beobachter_aendert_nichts(self):
        weg, _ = entscheide("short_title", BETRIEBSART_BEOBACHTER)
        assert weg == WEG_NICHTS

    def test_copilot_legt_alles_vor(self):
        weg, grund = entscheide("short_title", BETRIEBSART_COPILOT)
        assert weg == WEG_FREIGABE
        assert "Copilot" in grund

    def test_autopilot_fuehrt_unbedenkliches_aus(self):
        weg, _ = entscheide("short_title", BETRIEBSART_AUTOPILOT, in_whitelist=True)
        assert weg == WEG_AUSFUEHREN

    def test_autopilot_legt_unbekanntes_vor(self):
        weg, grund = entscheide(
            "irgendwas_neues", BETRIEBSART_AUTOPILOT, in_whitelist=False
        )
        assert weg == WEG_FREIGABE
        assert "Liste" in grund


class TestFreigabeSchlange:
    def test_vorschlag_landet_in_der_schlange(self, db):
        assert zur_freigabe(db, "p", _fix(), "weil")
        offen = freigaben(db)
        assert len(offen) == 1
        assert offen[0].status == STATUS_OFFEN

    def test_derselbe_befund_kommt_nicht_taeglich_wieder(self, db):
        """Sonst steht dieselbe Zeile nach einer Woche siebenmal da."""
        zur_freigabe(db, "p", _fix(), "weil")
        zweiter = zur_freigabe(db, "p", _fix(), "weil")
        assert zweiter is None
        assert len(freigaben(db)) == 1

    def test_abgelehnter_vorschlag_wird_nicht_neu_gefragt(self, db):
        kennung = zur_freigabe(db, "p", _fix(), "weil")
        entscheiden(db, kennung, STATUS_ABGELEHNT, notiz="wollen wir nicht")

        assert zur_freigabe(db, "p", _fix(), "weil") is None
        assert freigaben(db, status=STATUS_OFFEN) == []

    def test_verschiedene_seiten_sind_verschiedene_vorschlaege(self, db):
        zur_freigabe(db, "p", _fix(url="https://x.de/a"), "weil")
        zur_freigabe(db, "p", _fix(url="https://x.de/b"), "weil")
        assert len(freigaben(db)) == 2

    def test_entscheidung_wird_protokolliert(self, db):
        kennung = zur_freigabe(db, "p", _fix(), "weil")
        assert entscheiden(db, kennung, STATUS_FREIGEGEBEN, von="robert")

        eintrag = freigaben(db, status=STATUS_FREIGEGEBEN)[0]
        assert eintrag.entschieden_von == "robert"
        assert eintrag.entschieden_am

    def test_unbekannter_status_wird_abgelehnt(self, db):
        kennung = zur_freigabe(db, "p", _fix(), "weil")
        assert not entscheiden(db, kennung, "vielleicht")

    def test_gesperrte_vorschlaege_sind_markiert(self, db):
        zur_freigabe(db, "p", _fix(typ="missing_canonical"), "gesperrt")
        eintrag = freigaben(db)[0]
        assert eintrag.ist_gesperrt
        assert eintrag.gesperrt_grund

    def test_nur_gesperrte_filtern(self, db):
        zur_freigabe(db, "p", _fix(typ="short_title"), "a")
        zur_freigabe(db, "p", _fix(typ="missing_canonical"), "b")
        assert len(freigaben(db, nur_gesperrte=True)) == 1


class TestVeraltung:
    def test_alte_vorschlaege_werden_erkannt(self, db):
        alt = datetime.now(timezone.utc) - timedelta(days=45)
        zur_freigabe(db, "p", _fix(), "weil", jetzt=alt)
        assert len(veraltete(db)) == 1

    def test_frische_vorschlaege_gelten_nicht_als_veraltet(self, db):
        zur_freigabe(db, "p", _fix(), "weil")
        assert veraltete(db) == []


class TestDarstellung:
    def test_leere_schlange_bleibt_verstaendlich(self):
        assert "Keine offenen" in als_text([])

    def test_gesperrte_werden_hervorgehoben(self, db):
        zur_freigabe(db, "p", _fix(typ="missing_canonical"), "gesperrt weil")
        text = als_text(freigaben(db))
        assert "🔒" in text
        assert "brauchen immer eine Freigabe" in text

    def test_bericht_nennt_den_freigabe_befehl(self, db):
        zur_freigabe(db, "p", _fix(), "weil")
        assert "freigabe --ja" in als_text(freigaben(db))
