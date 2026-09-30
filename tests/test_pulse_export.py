"""Schnappschuss fuer die Steuerzentrale (Seite "Sichtbarkeit")."""

import json

import pytest

from seo_autopilot import ki_verlauf as kv
from seo_autopilot import pulse_export as pe
from seo_autopilot import rangliste as rl


@pytest.fixture
def umgebung(tmp_path):
    db = str(tmp_path / "t.db")
    projects = tmp_path / "projects.yaml"
    projects.write_text(
        "projects:\n"
        "  p:\n    name: Probe\n    domain: https://probe.de\n"
        "    bericht: {aktiv: true}\n"
        "  q:\n    domain: https://q.de\n",
        encoding="utf-8",
    )
    return db, str(projects)


def _z(keys, pos, impr=100, klicks=5):
    return {"keys": keys, "position": pos, "impressions": impr, "clicks": klicks}


def _ki(db, datum, genannt):
    kv.speichere(
        db,
        "p",
        {
            "modelle": {},
            "ergebnisse": {
                "Wer hilft?": {
                    "ChatGPT": {
                        "genannt": genannt,
                        "quellen": ["andere.de"],
                        "wettbewerber": ["Andere GmbH"],
                    },
                    "Claude": {"genannt": "fehler"},
                }
            },
        },
        datum,
    )


class TestSchnappschuss:
    def test_alle_teile_aus_vorhandenen_daten(self, umgebung):
        db, projects = umgebung
        begriffe = ["probe", "fehlt"]
        rl.speichere_woche(db, "p", "2026-W38", begriffe, [_z(["probe"], 12.0)], [])
        rl.speichere_woche(db, "p", "2026-W39", begriffe, [_z(["probe"], 8.0)], [])
        _ki(db, "2026-09-21", "nein")
        _ki(db, "2026-09-28", "verlinkt")

        s = pe.schnappschuss(db, projects, "p")
        assert s["version"] == 1 and s["host"] == "probe.de" and s["name"] == "Probe"
        g = s["google"]
        assert g["woche"] == "2026-W39" and g["seite1"] == 1
        assert g["nicht_gefunden"] == ["fehlt"]
        assert [(w["woche"], w["gefunden"], w["seite1"]) for w in g["verlauf"]] == [
            ("2026-W38", 1, 0),
            ("2026-W39", 1, 1),
        ]
        k = s["ki"]
        assert [v["je_ki"]["ChatGPT"]["genannt"] for v in k["verlauf"]] == [0, 1]
        assert "Claude" not in k["verlauf"][-1]["je_ki"]  # Fehler zaehlt nicht
        assert k["fragen"] == [
            {
                "frage": "Wer hilft?",
                "je_ki": {"ChatGPT": "verlinkt"},
                "andere": ["Andere GmbH"],
            }
        ]
        assert k["quellen"][0]["domain"] == "andere.de"
        assert "website" in s

    def test_ohne_daten_leere_teile_statt_absturz(self, umgebung):
        db, projects = umgebung
        s = pe.schnappschuss(db, projects, "p")
        assert s["google"] == {} and s["ki"] == {}

    def test_kaputter_teil_verhindert_nicht_den_rest(self, umgebung, monkeypatch):
        db, projects = umgebung

        def kaputt(*a, **k):
            raise RuntimeError("weg")

        monkeypatch.setattr(pe.kb, "wo_wir_stehen", kaputt)
        s = pe.schnappschuss(db, projects, "p")
        assert "weg" in s["website"]["fehler"] and s["google"] == {}


class TestSchreiben:
    def test_atomar_lesbar_und_nur_berichtsprojekte(self, umgebung, tmp_path):
        db, projects = umgebung
        ziel = tmp_path / "pulse"
        assert pe.main(["--db", db, "--projects", projects, "--ziel", str(ziel)]) == 0
        dateien = sorted(p.name for p in ziel.iterdir())
        assert dateien == ["p.json"]  # q hat keinen Bericht, keine Reste von .tmp
        assert (
            json.loads((ziel / "p.json").read_text(encoding="utf-8"))["projekt"] == "p"
        )
        assert oct((ziel / "p.json").stat().st_mode)[-3:] == "644"

    def test_unbekanntes_projekt(self, umgebung, tmp_path):
        db, projects = umgebung
        assert pe.main(["--db", db, "--projects", projects, "--projekt", "x",
                        "--ziel", str(tmp_path)]) == 2  # fmt: skip
