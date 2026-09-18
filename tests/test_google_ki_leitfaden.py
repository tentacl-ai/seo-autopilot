"""
Google-Leitfaden "Optimizing your website for generative AI features on Google
Search" (Search Central, Stand 10.07.2026): llms.txt, KI-Chunking, Schreiben
speziell fuer KI und Structured Data ohne Rich Result haben KEINE Wirkung.
Der Autopilot darf solche Befunde zeigen, aber weder benoten noch "reparieren".
"""

import asyncio
from types import SimpleNamespace

import pytest

import seo_autopilot.ausfuehrung as ausfuehrung
from seo_autopilot.agents.apply import ApplyAgent
from seo_autopilot.ausfuehrung import freigaben, tabelle_anlegen
from seo_autopilot.befund_arten import HINWEIS, art_von
from seo_autopilot.note import berechne_note

WIRKUNGSLOS = [
    "missing_llms_txt",
    "llms_no_links",
    "missing_ai_txt",
    "geo_paragraph_length",
    "geo_answer_first",
    "geo_fact_density",
    "no_jsonld",
    "schema_rich_result_opportunity",
]


@pytest.mark.parametrize("typ", WIRKUNGSLOS)
def test_wirkungslose_taktik_ist_nur_hinweis(typ):
    assert art_von(typ) == HINWEIS


def test_hinweise_kosten_keine_punkte():
    issues = [
        {"type": t, "severity": "high", "affected_url": f"https://x.de/{i}"}
        for i, t in enumerate(WIRKUNGSLOS * 5)
    ]
    assert berechne_note(issues, seiten=15).note == 100.0


def test_echte_fehler_zaehlen_weiter():
    issues = [{"type": "broken_internal_link", "severity": "high"}]
    assert berechne_note(issues, seiten=15).note < 100.0


def test_autopilot_baut_kein_json_ld_mehr_ein(tmp_path, monkeypatch):
    pfad = str(tmp_path / "t.db")
    tabelle_anlegen(pfad)
    monkeypatch.setattr(ausfuehrung, "standard_db_pfad", lambda: pfad)
    (tmp_path / "index.html").write_text("<html><head></head></html>", "utf-8")
    projekt = SimpleNamespace(
        id="t",
        betriebsart="autopilot",
        auto_fix_enabled=False,
        auto_fix_config={},
        adapter_type="static",
        adapter_config={"root_path": str(tmp_path)},
    )
    fix = {
        "type": "no_jsonld",
        "source": "regel",
        "seite": "https://x.de/",
        "priority": "high",
    }
    ctx = SimpleNamespace(
        agent_results={"content": SimpleNamespace(fixes=[fix])}, force_apply=False
    )
    r = asyncio.run(
        ApplyAgent(
            project_id="t", audit_id="a", project_config=projekt, context=ctx
        ).run()
    )
    assert r.metrics.get("fixes_applied", 0) == 0
    assert "ld+json" not in (tmp_path / "index.html").read_text()
    assert freigaben(pfad) == []  # auch nicht zur Freigabe vorgelegt
