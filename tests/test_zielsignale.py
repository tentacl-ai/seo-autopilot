import json
from datetime import date

from seo_autopilot.zielsignale import lade_zielsignale


def _projekt(tmp_path):
    return {
        "source_config": {
            "conversions": {
                "weekly_report_glob": str(tmp_path / "bericht-*.json"),
                "goal_name": "Bewerbung abgesendet",
                "goal_page": "/retreats",
            }
        }
    }


def _schreibe(tmp_path, name, start, woche, vorwoche):
    (tmp_path / name).write_text(
        json.dumps(
            {
                "zeitraum": {"start": f"{start}T00:00:00+02:00"},
                "ziele": {
                    "woche": {"bewerbungen": woche, "nach_kanal": {"Google": woche}},
                    "vorwoche": {"bewerbungen": vorwoche},
                },
            }
        ),
        encoding="utf-8",
    )


def test_wochen_werden_ueber_mehrere_berichte_nicht_doppelt_gezaehlt(tmp_path):
    _schreibe(tmp_path, "bericht-1.json", "2026-09-07", 1, 2)
    _schreibe(tmp_path, "bericht-2.json", "2026-09-14", 3, 1)

    signal = lade_zielsignale(
        _projekt(tmp_path), tage=28, heute=date(2026, 9, 23)
    )

    assert signal["gesamt"] == 6  # 31.08.: 2, 07.09.: 1, 14.09.: 3
    assert [w["start"] for w in signal["wochen"]] == [
        "2026-08-31",
        "2026-09-07",
        "2026-09-14",
    ]
    assert signal["nach_kanal"] == {"Google": 4}


def test_fehlende_konfiguration_liefert_leer(tmp_path):
    assert lade_zielsignale({}, heute=date(2026, 9, 23)) == {}


def test_kaputte_datei_stoppt_andere_wochen_nicht(tmp_path):
    (tmp_path / "bericht-kaputt.json").write_text("{", encoding="utf-8")
    _schreibe(tmp_path, "bericht-ok.json", "2026-09-14", 1, 0)

    signal = lade_zielsignale(
        _projekt(tmp_path), tage=28, heute=date(2026, 9, 23)
    )

    assert signal["gesamt"] == 1
