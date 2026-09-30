"""KI-Sichtbarkeit mit Gedaechtnis: Verlauf, Quellen, Wettbewerber."""

import json
import sys
from pathlib import Path

import pytest

from seo_autopilot import ki_verlauf as kv
from seo_autopilot.kundenbericht import _ki_gedaechtnis_html

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ki_sichtbarkeit as ks  # noqa: E402


def _ergebnis(je_frage):
    return {"host": "x.de", "fragen": len(je_frage), "modelle": {"Claude": "claude-opus-5"},
            "ergebnisse": je_frage}


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "t.db")


class TestBewerten:
    def test_wettbewerber_im_vollen_text_auch_nach_300_zeichen(self):
        text = "Einleitung. " * 40 + "Empfehlenswert ist die Anderes Retreat GmbH."
        e = ks.bewerten({"text": text, "quellen": ["https://www.portal.de/liste"]}, "x.de", ["x.de"],
                        [{"name": "Anderes Retreat", "host": "anderes.de"}, {"name": "Niemand", "host": "nie.de"}])
        assert len(e["auszug"]) == 300 and "Anderes Retreat" not in e["auszug"]
        assert e["wettbewerber"] == ["Anderes Retreat"]
        assert e["genannt"] == "nein"

    def test_wettbewerber_ueber_zitierte_quelle(self):
        e = ks.bewerten({"text": "Siehe Liste.", "quellen": ["https://www.anderes.de/"]}, "x.de", ["x.de"],
                        [{"name": "Anderes", "host": "anderes.de"}])
        assert e["wettbewerber"] == ["Anderes"]

    def test_name_nur_als_ganzes_wort(self):
        w = [{"name": "Endel", "host": "endel.io"}, {"name": "Brain.fm", "host": "brain.fm"}]
        e = ks.bewerten({"text": "Ein Pendel beruhigt.", "quellen": []}, "x.de", ["x.de"], w)
        assert e["wettbewerber"] == []
        e = ks.bewerten({"text": "Apps wie Endel oder Brain.fm.", "quellen": []}, "x.de", ["x.de"], w)
        assert e["wettbewerber"] == ["Endel", "Brain.fm"]

    def test_ohne_wettbewerberliste_wie_bisher(self):
        e = ks.bewerten({"text": "x.de ist gut", "quellen": []}, "x.de", ["x.de"])
        assert e["genannt"] == "erwaehnt" and e["wettbewerber"] == []


class TestGedaechtnis:
    def test_fehler_zaehlt_nicht_als_nicht_genannt(self, db):
        n = kv.speichere(db, "p", _ergebnis({"F1": {
            "Claude": {"genannt": "verlinkt", "quellen": ["x.de"]},
            "ChatGPT": {"genannt": "fehler", "fehler": "Timeout"},
        }}), "2026-09-28")
        assert n == 1
        assert kv.verlauf(db, "p") == [{"datum": "2026-09-28", "je_ki": {"Claude": {"genannt": 1, "von": 1}}}]

    def test_verlauf_quellen_und_luecken(self, db):
        kv.speichere(db, "p", _ergebnis({
            "F1": {"Claude": {"genannt": "nein", "quellen": ["portal.de", "x.de"], "wettbewerber": ["Anders"]}},
            "F2": {"Claude": {"genannt": "erwaehnt", "quellen": ["portal.de"], "wettbewerber": ["Anders"]}},
        }), "2026-09-21")
        kv.speichere(db, "p", _ergebnis({
            "F1": {"Claude": {"genannt": "verlinkt", "quellen": ["portal.de", "blog.de"]}},
            "F2": {"Claude": {"genannt": "nein", "quellen": [], "wettbewerber": ["Anders", "Dritter"]}},
        }), "2026-09-28")
        a = kv.auswertung(db, "p", "x.de")
        assert [l["je_ki"]["Claude"]["genannt"] for l in a["verlauf"]] == [1, 1]
        assert a["quellen"][0] == {"domain": "portal.de", "antworten": 3}
        assert all(q["domain"] != "x.de" for q in a["quellen"])  # eigene Seite nicht
        wb = a["wettbewerb"]
        assert wb["datum"] == "2026-09-28" and wb["wir"] == 1
        assert wb["luecken"] == {"F2": ["Anders", "Dritter"]}

    def test_nachtragen_aus_alten_berichten(self, db, tmp_path):
        ablage = tmp_path / "ablage"
        ablage.mkdir()
        for tag, g in [("2026-09-16", "nein"), ("2026-09-21", "verlinkt")]:
            (ablage / f"bericht-{tag}.json").write_text(json.dumps({"ki_sichtbarkeit": _ergebnis(
                {"F1": {"Gemini": {"genannt": g, "quellen": ["a.de"]}}})}), encoding="utf-8")
        (ablage / "bericht-2026-09-28.json").write_text('{"ki_sichtbarkeit": {"fehler": "x"}}', encoding="utf-8")
        assert kv.nachtragen(db, "p", ablage) == 2
        assert [l["datum"] for l in kv.verlauf(db, "p")] == ["2026-09-16", "2026-09-21"]


class TestHtml:
    def test_zeigt_verlauf_quellen_wettbewerb_und_escaped(self):
        g = {
            "verlauf": [
                {"datum": "2026-09-21", "je_ki": {"Claude": {"genannt": 0, "von": 3}}},
                {"datum": "2026-09-28", "je_ki": {"Claude": {"genannt": 2, "von": 3}}},
            ],
            "quellen": [{"domain": "portal.de", "antworten": 4}],
            "wettbewerb": {"wir": 2, "antworten": 3, "andere": {"<b>Böse</b>": 1},
                           "luecken": {"Wer hilft?": ["<b>Böse</b>"]}},
        }
        html = "".join(_ki_gedaechtnis_html(g))
        assert "2 von 3" in html and "28.09." in html
        assert "portal.de (4×)" in html
        assert "<b>Böse</b>" not in html and "&lt;b&gt;Böse" in html

    def test_ohne_daten_nichts(self):
        assert _ki_gedaechtnis_html({}) == []
