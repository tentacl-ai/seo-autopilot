"""Backlinks aus dem Common-Crawl-Domaingraphen (ohne Netz: Graph im Test nachgebaut)."""

from datetime import date
from pathlib import Path

import pytest

from seo_autopilot import backlinks as bl

# Mini-Graph: 1 = ai.tentacl (wir), 2 = de.blog, 3 = com.portal, 4 = at.beispiel-coach (wir), 5 = de.spam
KNOTEN = [
    ["1", "ai.tentacl"],
    ["2", "de.blog"],
    ["3", "com.portal"],
    ["4", "at.beispiel-coach"],
    ["5", "de.spam"],
]
KANTEN = [["2", "1"], ["3", "1"], ["1", "1"], ["4", "1"], ["5", "4"], ["2", "3"]]
RAENGE = [
    ["#harmonicc_pos", "#host_rev"],
    ["120", "com.portal"],
    ["5000", "de.blog"],
    ["99", "at.beispiel-coach"],
]


def falscher_strom(url, schluessel_datei: Path, spalte, ausgabe):
    werte = set(schluessel_datei.read_text(encoding="utf-8").split())
    if url.endswith("vertices.txt.gz"):
        zeilen = KNOTEN
    elif url.endswith("edges.txt.gz"):
        zeilen = KANTEN
    else:  # ranks: Spalte 1 = Platz, Spalte 5 = Domain -> hier auf zwei Spalten verkuerzt
        zeilen = [[z[0], "", "", "", z[1]] for z in RAENGE]
    return [[z[n - 1] for n in ausgabe] for z in zeilen if z[spalte - 1] in werte]


class TestNamen:
    @pytest.mark.parametrize(
        "bis,name",
        [
            (date(2026, 9, 1), "cc-main-2026-jul-aug-sep"),
            (date(2026, 1, 1), "cc-main-2025-26-nov-dec-jan"),
            (date(2026, 2, 1), "cc-main-2025-26-dec-jan-feb"),
        ],
    )
    def test_graphname_wie_common_crawl(self, bis, name):
        assert bl.graph_name(bis) == name
        assert bl.graph_ende(name) == bis

    def test_neuester_der_existiert(self):
        da = {"cc-main-2026-jul-aug-sep"}
        assert (
            bl.neuester_graph(date(2026, 10, 5), pruefe=lambda g: g in da)
            == "cc-main-2026-jul-aug-sep"
        )
        assert bl.neuester_graph(date(2026, 10, 5), pruefe=lambda g: False) is None

    def test_domain_und_umkehr(self):
        assert bl.domain_von("https://www.Tentacl.ai/") == "tentacl.ai"
        assert bl.umgekehrt("beispiel-coach.at") == "at.beispiel-coach"


class TestGraph:
    def test_verlinkende_domains_mit_rang_ohne_selbstlink(self):
        e = bl.aus_graph_lesen(
            "g", ["tentacl.ai", "beispiel-coach.at", "neu.de"], falscher_strom
        )
        assert e["tentacl.ai"] == {
            "blog.de": 5000,
            "portal.com": 120,
            "beispiel-coach.at": 99,
        }
        assert e["beispiel-coach.at"] == {
            "spam.de": None
        }  # ohne Rang = unbedeutend/unbekannt
        assert e["neu.de"] is None  # nicht im Graphen, nicht "0 Links"

    def test_einlesen_speichern_auswerten_mit_vergleich(self, tmp_path):
        db = str(tmp_path / "t.db")
        projekte = {
            "tentacl-ai": {"domain": "https://tentacl.ai"},
            "neu": {"domain": "https://neu.de"},
        }
        bl.speichere(
            db,
            "tentacl-ai",
            "cc-main-2026-jun-jul-aug",
            {"blog.de": 5000, "alt.de": None},
        )
        assert bl.einlesen(
            db, projekte, "cc-main-2026-jul-aug-sep", falscher_strom
        ) == {"tentacl-ai": 3, "neu": 0}
        assert (
            bl.einlesen(db, projekte, "cc-main-2026-jul-aug-sep", falscher_strom) == {}
        )  # schon eingelesen

        a = bl.auswertung(db, "tentacl-ai")
        assert (
            a["graph"] == "cc-main-2026-jul-aug-sep"
            and a["domains"] == 3
            and a["davor"] == 2
        )
        assert a["neu"] == ["beispiel-coach.at", "portal.com"] and a["weg"] == ["alt.de"]
        assert [w["domain"] for w in a["wichtigste"]] == [
            "beispiel-coach.at",
            "portal.com",
            "blog.de",
        ]
        n = bl.auswertung(db, "neu")
        assert n["im_graphen"] is False and n["domains"] == 0 and n["davor"] is None

    def test_abgebrochener_strom_speichert_nichts(self, tmp_path):
        db = str(tmp_path / "t.db")

        def kaputt(*a, **k):
            raise RuntimeError("Stream abgebrochen")

        with pytest.raises(RuntimeError):
            bl.einlesen(db, {"p": {"domain": "https://tentacl.ai"}}, "g", kaputt)
        assert bl.eingelesen(db, "g") == set() and bl.auswertung(db, "p") == {}


class TestImBericht:
    def test_zeitraum_lesbar(self):
        assert bl.graph_text("cc-main-2026-jul-aug-sep") == "Juli bis Sep. 2026"
        assert bl.graph_text("cc-main-2025-26-nov-dec-jan") == "Nov. bis Jan. 2026"

    def test_abschnitt_mit_neu_weg_und_escaping(self, tmp_path):
        from seo_autopilot.kundenbericht import (
            _abschnitt_backlinks,
            backlinks_fuer_bericht,
        )

        db = str(tmp_path / "t.db")
        bl.speichere(db, "p", "cc-main-2026-jun-jul-aug", {"alt.de": None})
        bl.speichere(db, "p", "cc-main-2026-jul-aug-sep", {"<b>x</b>.de": 1234567})
        daten = backlinks_fuer_bericht(db, "p")
        html = "".join(_abschnitt_backlinks({"backlinks": daten}))
        assert "Juli bis Sep. 2026" in html and "(vorher 1)" in html
        assert "Nicht mehr verlinkt: alt.de" in html and "1.234.567" in html
        assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;.de" in html

    def test_nicht_im_graphen_und_ohne_daten(self, tmp_path):
        from seo_autopilot.kundenbericht import (
            _abschnitt_backlinks,
            backlinks_fuer_bericht,
        )

        db = str(tmp_path / "t.db")
        assert (
            _abschnitt_backlinks({"backlinks": backlinks_fuer_bericht(db, "p")}) == []
        )
        bl.speichere(db, "p", "cc-main-2026-jul-aug-sep", None)
        html = "".join(
            _abschnitt_backlinks({"backlinks": backlinks_fuer_bericht(db, "p")})
        )
        assert "noch nicht drin" in html
