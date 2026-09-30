"""Rangliste aus der Search Console (Nachbau OpenSEO-Rank-Tracking ohne DataForSEO)."""

import sqlite3
from datetime import date

import pytest

from seo_autopilot import rangliste as rl
from seo_autopilot.historie import _verbinde as historie_verbinde

PROJEKT = {
    "enabled_sources": ["gsc"],
    "source_config": {
        "gsc": {"property_url": "sc-domain:x.de", "credentials_path": "/x"}
    },
}


class FakeGSC:
    """Liefert je Aufruf die naechste Antwort; zaehlt Abfragen."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    async def pull_range(self, prop, von, bis, dims, limit):
        self.aufrufe.append((von, bis, tuple(dims or ())))
        return self.antworten.pop(0) if self.antworten else []


def _z(keys, pos, impr=100, klicks=5):
    return {"keys": keys, "position": pos, "impressions": impr, "clicks": klicks}


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "t.db")


class TestWochen:
    def test_letzte_volle_woche_beachtet_verzug(self):
        # Mi 30.09.2026 - 3 Tage = So 27.09. -> Woche 21.-27.09. ist fertig
        assert rl.letzte_volle_woche(date(2026, 9, 30)) == (
            date(2026, 9, 21),
            date(2026, 9, 27),
        )
        # Mo 28.09. - 3 = Fr 25.09. -> erst die Woche davor
        assert rl.letzte_volle_woche(date(2026, 9, 28)) == (
            date(2026, 9, 14),
            date(2026, 9, 20),
        )

    def test_wochen_aelteste_zuerst(self):
        w = rl.wochen(3, date(2026, 9, 30))
        assert [m for m, _ in w] == [
            date(2026, 9, 7),
            date(2026, 9, 14),
            date(2026, 9, 21),
        ]
        assert rl.wochenschluessel(date(2026, 9, 21)) == "2026-W39"


class TestBegriffe:
    def test_konfigurierte_begriffe_normalisiert_und_begrenzt(self, db):
        p = {
            **PROJEKT,
            "rangliste": {
                "begriffe": ["  Camping Software ", "camping software"]
                + [f"b{i}" for i in range(80)]
            },
        }
        b = rl.begriffe_fuer(db, "x", p)
        assert b[0] == "camping software" and len(b) == rl.MAX_BEGRIFFE

    def test_ohne_konfig_die_staerksten_aus_der_historie(self, db):
        with historie_verbinde(db) as conn:
            for monat, wert, impr, voll in [
                ("2026-08", "a", 10, 1),
                ("2026-08", "b", 500, 1),
                ("2026-09", "c", 999, 0),  # laufender Monat zaehlt nicht
                ("2026-08", "site:x.de", 800, 1),  # Suchbefehl, keine Nachfrage
                ("2026-01", "uralt", 5000, 1),  # aelter als die letzten 3 Monate
                ("2026-07", "d", 1, 1),
                ("2026-06", "e", 1, 1),
            ]:
                conn.execute(
                    "insert into gsc_historie (project_id, property_url, monat, dimension, wert, "
                    "impressionen, vollstaendig, abgerufen_am) values ('x','p',?,'begriff',?,?,?,'t')",
                    (monat, wert, impr, voll),
                )
        assert rl.begriffe_fuer(db, "x", PROJEKT) == ["b", "a", "d", "e"]


class TestImport:
    @pytest.mark.asyncio
    async def test_woche_mit_geraeten_und_nicht_gefunden(self, db):
        p = {
            **PROJEKT,
            "rangliste": {"begriffe": ["camping software", "gibt es nicht"]},
        }
        gsc = FakeGSC(
            [
                [_z(["Camping Software"], 7.4, 300), _z(["anderes"], 2.0)],
                [
                    _z(["camping software", "MOBILE"], 9.1, 200),
                    _z(["camping software", "DESKTOP"], 3.3, 100),
                ],
            ]
        )
        e = await rl.importiere(
            db, "x", p, nachholen=1, heute=date(2026, 9, 30), quelle=gsc
        )
        assert e["geholt"] == 1 and len(gsc.aufrufe) == 2
        woche, zeilen = rl.tabelle(db, "x")
        assert woche == "2026-W39"
        cs = next(z for z in zeilen if z.begriff == "camping software")
        assert (cs.position, cs.handy, cs.computer, cs.impressionen) == (
            7.4,
            9.1,
            3.3,
            300,
        )
        weg = next(z for z in zeilen if z.begriff == "gibt es nicht")
        assert weg.position is None  # nicht gefunden, trotzdem gefuehrt

    @pytest.mark.asyncio
    async def test_abfragefehler_speichert_nichts(self, db):
        p = {**PROJEKT, "rangliste": {"begriffe": ["a"]}}

        class Kaputt(FakeGSC):
            async def pull_range(self, *a):
                return None

        e = await rl.importiere(
            db, "x", p, nachholen=2, heute=date(2026, 9, 30), quelle=Kaputt([])
        )
        assert e["geholt"] == 0
        assert rl.tabelle(db, "x") == (None, [])  # kein falsches "nicht gefunden"

    @pytest.mark.asyncio
    async def test_nachholen_und_vorhandene_wochen_ueberspringen(self, db):
        p = {**PROJEKT, "rangliste": {"begriffe": ["a"]}}
        antworten = []
        for pos in (12.0, 9.0, 8.0, 7.0, 5.0):
            antworten += [[_z(["a"], pos)], []]
        gsc = FakeGSC(antworten)
        e = await rl.importiere(
            db, "x", p, nachholen=5, heute=date(2026, 9, 30), quelle=gsc
        )
        assert e["geholt"] == 5
        _, zeilen = rl.tabelle(db, "x")
        assert (zeilen[0].position, zeilen[0].vorwoche, zeilen[0].vor_4_wochen) == (
            5.0,
            7.0,
            12.0,
        )
        text = rl.bericht_text(db, "x")
        assert "▲2.0" in text and "▲7.0" in text
        e2 = await rl.importiere(
            db, "x", p, nachholen=5, heute=date(2026, 9, 30), quelle=FakeGSC([])
        )
        assert e2 == {"geholt": 0, "vorhanden": 5, "fehler": None}

    @pytest.mark.asyncio
    async def test_ohne_archiv_begriffe_direkt_aus_search_console(self, db):
        gsc = FakeGSC(
            [
                [
                    _z(["klein"], 3.0, 5),
                    _z(["Gross"], 8.0, 900),
                    _z(["site:x.de"], 1.0, 999),
                ],
                [_z(["gross"], 8.0, 900)],
                [],
            ]
        )
        e = await rl.importiere(
            db, "x", PROJEKT, nachholen=1, heute=date(2026, 9, 30), quelle=gsc
        )
        assert e["geholt"] == 1
        assert [z.begriff for z in rl.tabelle(db, "x")[1]] == ["gross", "klein"]

    @pytest.mark.asyncio
    async def test_ohne_search_console_kein_absturz(self, db):
        e = await rl.importiere(db, "x", {"enabled_sources": []}, quelle=FakeGSC([]))
        assert e["fehler"] == "keine Search Console konfiguriert"


class TestTrend:
    @pytest.mark.parametrize(
        "jetzt, vorher, erwartet",
        [
            (5.0, 7.0, "▲2.0"),
            (9.0, 7.0, "▼2.0"),
            (7.3, 7.0, "="),
            (5.0, None, "neu"),
            (None, 4.0, "weg"),
        ],
    )
    def test_trend(self, jetzt, vorher, erwartet):
        assert rl._trend(jetzt, vorher) == erwartet


class TestImKundenbericht:
    def test_abschnitt_mit_trend_und_escaping(self, tmp_path):
        from seo_autopilot.kundenbericht import (
            _abschnitt_rangliste,
            rangliste_fuer_bericht,
        )

        db = str(tmp_path / "k.db")
        begriffe = ["camping software", "<b>böse</b>", "fehlt"]
        rl.speichere_woche(db, "p", "2026-W38", begriffe,
                           [_z(["camping software"], 9.0)], [])  # fmt: skip
        rl.speichere_woche(db, "p", "2026-W39", begriffe,
                           [_z(["camping software"], 4.0), _z(["<b>böse</b>"], 30.0)], [])  # fmt: skip
        daten = rangliste_fuer_bericht(db, "p")
        assert (
            daten["woche"] == "2026-W39"
            and daten["gefunden"] == 2
            and daten["seite1"] == 1
        )
        html = "".join(_abschnitt_rangliste({"rangliste": daten}))
        assert "Rangliste" in html and "▲5.0" in html and "neu" in html
        assert "<b>böse</b>" not in html and "&lt;b&gt;" in html
        assert [z["begriff"] for z in daten["zeilen"]] == [
            "camping software",
            "<b>böse</b>",
        ]
        assert "nicht bei Google gefunden: fehlt" in html

    def test_ohne_daten_kein_abschnitt(self, tmp_path):
        from seo_autopilot.kundenbericht import (
            _abschnitt_rangliste,
            rangliste_fuer_bericht,
        )

        assert rangliste_fuer_bericht(str(tmp_path / "k.db"), "p") == {}
        assert _abschnitt_rangliste({"rangliste": {}}) == []
        assert _abschnitt_rangliste({"rangliste": {"fehler": "x"}}) == []
