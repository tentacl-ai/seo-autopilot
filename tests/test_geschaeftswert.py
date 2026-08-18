"""
Tests des Geschäftswert-Moduls.

Der wichtigste Test in dieser Datei ist nicht, dass richtig gerechnet wird,
sondern dass NICHT gerechnet wird, wo die Zahlen fehlen. Eine erfundene Zahl
sieht aus wie eine Tatsache und verteilt die gesamte Arbeit nach Bauchgefühl.
"""

import pytest

from seo_autopilot.geschaeftswert import (
    MIN_BESUCHER,
    MIN_BESUCHER_FUER_LEERLAUF,
    SeitenWert,
    Ziel,
    als_text,
    bewerte_seiten,
    fehlende_angaben,
    lies_ziele,
    unterschaetzte_seiten,
    verschenktes_geld,
)


def _projekt(ziele=None, waehrung="EUR"):
    if ziele is None:
        return {"enabled": True}
    return {
        "enabled": True,
        "geschaeftswert": {"waehrung": waehrung, "ziele": ziele},
    }


VOLLES_ZIEL = {
    "name": "Kontaktanfrage",
    "wert_pro_abschluss": 2500,
    "abschlussquote": 0.15,
}


class TestKeineErfundenenZahlen:
    def test_ohne_konfiguration_keine_ziele(self):
        ziele, _ = lies_ziele(_projekt())
        assert ziele == []

    def test_ziel_ohne_wert_ist_unvollstaendig(self):
        ziel = Ziel(name="Anfrage", abschlussquote=0.2)
        assert not ziel.vollstaendig
        assert ziel.wert_je_anfrage is None

    def test_ziel_ohne_quote_ist_unvollstaendig(self):
        ziel = Ziel(name="Anfrage", wert_pro_abschluss=1000)
        assert not ziel.vollstaendig
        assert ziel.wert_je_anfrage is None

    def test_quote_null_gilt_nicht_als_angabe(self):
        """Rechnerisch gueltig, aber fast immer ein Eingabefehler."""
        ziel = Ziel(name="A", wert_pro_abschluss=1000, abschlussquote=0)
        assert not ziel.vollstaendig

    def test_quote_ueber_eins_wird_abgelehnt(self):
        ziel = Ziel(name="A", wert_pro_abschluss=1000, abschlussquote=1.4)
        assert not ziel.vollstaendig

    def test_unlesbare_zahl_wird_nicht_zu_null(self):
        """Sonst rutschte ein Tippfehler lautlos als Wert 0 durch."""
        ziele, _ = lies_ziele(
            _projekt([{"name": "Anfrage", "wert_pro_abschluss": "zweitausend"}])
        )
        assert len(ziele) == 1
        assert not ziele[0].vollstaendig

    def test_seite_ohne_ziel_hat_keinen_wert_nicht_wert_null(self):
        """'Wissen wir nicht' und 'bringt nichts' duerfen nie gleich aussehen."""
        bewertet = bewerte_seiten(
            [{"url": "https://x.de/a", "besucher": 100, "anfragen": 5}], []
        )
        assert bewertet[0].wert is None
        assert not bewertet[0].bezifferbar


class TestWertJeAnfrage:
    def test_wert_ergibt_sich_aus_abschluss_und_quote(self):
        ziel = Ziel(name="A", wert_pro_abschluss=2500, abschlussquote=0.15)
        assert ziel.wert_je_anfrage == 375.0

    def test_seitenwert_multipliziert_mit_anfragen(self):
        bewertet = bewerte_seiten(
            [{"url": "https://x.de/kontakt", "besucher": 200, "anfragen": 4}],
            [Ziel(name="A", wert_pro_abschluss=2500, abschlussquote=0.15)],
        )
        assert bewertet[0].wert == 1500.0

    def test_wert_je_besucher_ist_der_vergleichsmassstab(self):
        s = SeitenWert(url="/a", besucher=100, anfragen=2, wert_je_anfrage=375.0)
        assert s.wert_je_besucher == 7.5

    def test_zu_wenige_besucher_ergeben_keine_quote(self):
        s = SeitenWert(
            url="/a", besucher=MIN_BESUCHER - 1, anfragen=1, wert_je_anfrage=375.0
        )
        assert s.anfragequote is None
        assert s.wert_je_besucher is None


class TestZielZuordnung:
    def test_ziel_ohne_seitenliste_gilt_ueberall(self):
        ziel = Ziel(name="A", wert_pro_abschluss=100, abschlussquote=0.5)
        assert ziel.gilt_fuer("/beliebig")

    def test_seitenliste_grenzt_ein(self):
        ziel = Ziel(
            name="A", wert_pro_abschluss=100, abschlussquote=0.5,
            seiten=["/finanzierung"],
        )
        assert ziel.gilt_fuer("/finanzierung/factoring")
        assert not ziel.gilt_fuer("/blog/artikel")

    def test_zuordnung_arbeitet_auf_pfaden_nicht_auf_ganzen_adressen(self):
        bewertet = bewerte_seiten(
            [{"url": "https://joseph.de/finanzierung/factoring",
              "besucher": 100, "anfragen": 2}],
            [Ziel(name="Finanzierung", wert_pro_abschluss=2000,
                  abschlussquote=0.1, seiten=["/finanzierung"])],
        )
        assert bewertet[0].bezifferbar
        assert bewertet[0].ziel_name == "Finanzierung"


class TestVerschenktesGeld:
    def test_viele_besucher_ohne_anfrage_faellt_auf(self):
        bewertet = bewerte_seiten(
            [{"url": "/a", "besucher": 400, "anfragen": 0}],
            [Ziel(name="A", wert_pro_abschluss=2000, abschlussquote=0.1)],
        )
        assert len(verschenktes_geld(bewertet)) == 1

    def test_wenige_besucher_ohne_anfrage_ist_normal(self):
        bewertet = bewerte_seiten(
            [{"url": "/a", "besucher": MIN_BESUCHER_FUER_LEERLAUF - 1, "anfragen": 0}],
            [Ziel(name="A", wert_pro_abschluss=2000, abschlussquote=0.1)],
        )
        assert verschenktes_geld(bewertet) == []

    def test_seite_mit_anfragen_ist_kein_leerlauf(self):
        bewertet = bewerte_seiten(
            [{"url": "/a", "besucher": 400, "anfragen": 3}],
            [Ziel(name="A", wert_pro_abschluss=2000, abschlussquote=0.1)],
        )
        assert verschenktes_geld(bewertet) == []


class TestUnterschaetzteSeiten:
    def test_wenig_besucher_hoher_wert_wird_gefunden(self):
        """Genau die Seite, die eine Priorisierung nach Besuchern uebersieht."""
        bewertet = bewerte_seiten(
            [
                {"url": "/ratgeber", "besucher": 1000, "anfragen": 1},
                {"url": "/factoring", "besucher": 40, "anfragen": 4},
            ],
            [Ziel(name="A", wert_pro_abschluss=2500, abschlussquote=0.15)],
        )
        treffer = unterschaetzte_seiten(bewertet)
        assert [s.url for s in treffer] == ["/factoring"]

    def test_ohne_bezifferbare_seiten_leer(self):
        bewertet = bewerte_seiten([{"url": "/a", "besucher": 100, "anfragen": 5}], [])
        assert unterschaetzte_seiten(bewertet) == []


class TestFehlendeAngaben:
    def test_projekt_ohne_abschnitt_wird_gemeldet(self):
        offen = fehlende_angaben({"joseph": _projekt()})
        assert len(offen) == 1
        assert "kein Abschnitt" in offen[0]["grund"]

    def test_unvollstaendiges_ziel_wird_benannt(self):
        offen = fehlende_angaben(
            {"joseph": _projekt([{"name": "Anfrage", "wert_pro_abschluss": 1000}])}
        )
        assert len(offen) == 1
        assert "Abschlussquote" in offen[0]["fehlt"]

    def test_vollstaendiges_projekt_taucht_nicht_auf(self):
        offen = fehlende_angaben({"joseph": _projekt([VOLLES_ZIEL])})
        assert offen == []

    def test_abgeschaltete_projekte_werden_uebersprungen(self):
        offen = fehlende_angaben({"tot": {"enabled": False}})
        assert offen == []


class TestDarstellung:
    def test_ohne_zahlen_erklaert_der_bericht_was_fehlt(self):
        text = als_text([], fehlende_angaben({"joseph": _projekt()}))
        assert "für keine Seite bezifferbar" in text
        assert "nicht geschätzt" in text
        assert "joseph" in text

    def test_bericht_nennt_summe_und_seiten(self):
        bewertet = bewerte_seiten(
            [{"url": "https://x.de/kontakt", "besucher": 200, "anfragen": 4}],
            [Ziel(name="A", wert_pro_abschluss=2500, abschlussquote=0.15)],
        )
        text = als_text(bewertet)
        assert "1.500,00 EUR" in text
        assert "/kontakt" in text

    def test_null_anfragen_ueberall_ist_ein_messproblem(self):
        """Sonst liest sich fehlende Erfassung wie ein vernichtendes Ergebnis."""
        bewertet = bewerte_seiten(
            [
                {"url": "/a", "besucher": 200, "anfragen": 0},
                {"url": "/b", "besucher": 150, "anfragen": 0},
            ],
            [Ziel(name="A", wert_pro_abschluss=2500, abschlussquote=0.15)],
        )
        text = als_text(bewertet)
        assert "Anfragen-Erfassung fehlt" in text

    def test_bei_echten_anfragen_kein_messproblem_hinweis(self):
        bewertet = bewerte_seiten(
            [
                {"url": "/a", "besucher": 200, "anfragen": 3},
                {"url": "/b", "besucher": 150, "anfragen": 0},
            ],
            [Ziel(name="A", wert_pro_abschluss=2500, abschlussquote=0.15)],
        )
        assert "Anfragen-Erfassung fehlt" not in als_text(bewertet)

    def test_deutsche_zahlenschreibweise(self):
        """Punkt als Tausender-, Komma als Dezimaltrenner — nicht beides Punkt."""
        bewertet = bewerte_seiten(
            [{"url": "/k", "besucher": 200, "anfragen": 4}],
            [Ziel(name="A", wert_pro_abschluss=2500, abschlussquote=0.15)],
        )
        text = als_text(bewertet)
        assert "1.500,00 EUR" in text
        assert "1.500.00" not in text
        assert "2,0 %" in text

    def test_unbewertete_seiten_werden_getrennt_ausgewiesen(self):
        bewertet = bewerte_seiten(
            [
                {"url": "/kontakt", "besucher": 200, "anfragen": 4},
                {"url": "/blog", "besucher": 90, "anfragen": 0},
            ],
            [Ziel(name="A", wert_pro_abschluss=2500, abschlussquote=0.15,
                  seiten=["/kontakt"])],
        )
        text = als_text(bewertet)
        assert "nicht bewertet (nicht: bewertet mit null)" in text
