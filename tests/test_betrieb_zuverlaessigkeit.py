"""Regression tests for observed production failures, no customer systems."""

import asyncio
import importlib.util
import json
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from seo_autopilot import betriebssicherheit as bs, betriebsjob, health
from seo_autopilot.agents.keyword import KeywordAgent
from seo_autopilot.sources.base import DataSourceError


def test_failed_and_skipped_with_error_are_partial_not_success():
    assert bs.agenten_fehler(
        {
            "gsc": SimpleNamespace(status="skipped", errors=["403"]),
            "ok": SimpleNamespace(status="completed", errors=[]),
        }
    ) == ["gsc"]
    assert bs.agenten_fehler(
        {"trend": SimpleNamespace(status="completed", metrics={"had_error": True})}
    ) == ["trend"]


def test_ki_failure_does_not_count_as_negative_answer():
    data = {
        "genannt_je_ki": {"ChatGPT": 0, "Claude": 1},
        "ergebnisse": {
            "a": {"ChatGPT": {"genannt": "fehler"}, "Claude": {"genannt": "verlinkt"}},
            "b": {"ChatGPT": {"genannt": "nein"}, "Claude": {"genannt": "nein"}},
        },
    }
    assert bs.ki_abdeckung(data)["ChatGPT"] == {
        "erfolgreich": 1,
        "fehler": 1,
        "genannt": 0,
    }
    assert bs.ki_abdeckung(data)["Claude"]["genannt"] == 1


@pytest.mark.asyncio
async def test_gsc_failure_keeps_ga4_results(monkeypatch):
    p = SimpleNamespace(enabled_sources=["gsc", "ga4"], source_config={})
    agent = KeywordAgent("p", "a", p)

    async def gsc():
        raise DataSourceError("403")

    async def ga4():
        return object()

    async def nothing(*a):
        pass

    monkeypatch.setattr(agent, "emit_started", nothing)
    monkeypatch.setattr(agent, "emit_result", nothing)
    monkeypatch.setattr(agent, "_pull_gsc_analytics", gsc)
    monkeypatch.setattr(agent, "_pull_ga4_analytics", ga4)
    monkeypatch.setattr(
        agent, "_ga4_metrics", lambda v: {"ga4_available": v is not None}
    )
    monkeypatch.setattr(agent, "_find_high_bounce_pages", lambda v: [])
    result = await agent.run()
    assert result.metrics["ga4_available"] is True
    assert result.metrics["gsc_available"] is False
    assert result.errors


@pytest.mark.asyncio
async def test_only_rolled_back_persistence_is_retried_with_same_id(monkeypatch):
    from seo_autopilot.db import persistence as p

    calls = []

    async def attempt(ctx, uid):
        calls.append(uid)
        if len(calls) < 3:
            raise OperationalError(
                "insert", {}, sqlite3.OperationalError("database is locked")
            )
        return uid

    async def no_wait(n):
        pass

    monkeypatch.setattr(p, "_persist_once", attempt)
    monkeypatch.setattr(p.asyncio, "sleep", no_wait)
    uid = await p.persist_audit(object())
    assert calls == [uid] * 3


@pytest.mark.asyncio
async def test_other_db_errors_are_never_retried(monkeypatch):
    from seo_autopilot.db import persistence as p

    calls = []

    async def attempt(*args):
        calls.append(1)
        raise OperationalError("insert", {}, sqlite3.OperationalError("no such table"))

    monkeypatch.setattr(p, "_persist_once", attempt)
    with pytest.raises(OperationalError):
        await p.persist_audit(object())
    assert len(calls) == 1


def test_wrapper_records_nonzero_and_retains_last_success(tmp_path):
    assert betriebsjob.ausfuehren([sys.executable, "-c", "pass"], "test", tmp_path) == 0
    first = json.loads((tmp_path / "test.json").read_text())
    assert (
        betriebsjob.ausfuehren(
            [sys.executable, "-c", "raise SystemExit(2)"], "test", tmp_path
        )
        == 2
    )
    failed = json.loads((tmp_path / "test.json").read_text())
    assert (
        failed["status"] == "failed" and failed["last_success"] == first["last_success"]
    )


def test_wrapper_timeout_is_recorded_and_not_retried(tmp_path):
    code = betriebsjob.ausfuehren(
        [sys.executable, "-c", "import time;time.sleep(5)"],
        "slow",
        tmp_path,
        timeout=0.1,
    )
    assert code == 124
    assert json.loads((tmp_path / "slow.json").read_text())["status"] == "timeout"
    assert len((tmp_path / "slow.jsonl").read_text().splitlines()) == 1


def test_concurrent_jobs_are_serialized(tmp_path):
    output = tmp_path / "events"
    program = "import pathlib,time; p=pathlib.Path(__import__('sys').argv[1]); f=p.open('a'); f.write('start\\n'); f.flush(); time.sleep(.2); f.write('end\\n'); f.close()"
    threads = [
        threading.Thread(
            target=betriebsjob.ausfuehren,
            args=([sys.executable, "-c", program, str(output)], name, tmp_path),
        )
        for name in ["one", "two"]
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert output.read_text().splitlines() == ["start", "end", "start", "end"]


def test_historical_gap_is_seen_even_after_today_succeeds():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE seo_audits(project_id,started_at)")
    now = datetime(2026, 11, 14, 14, tzinfo=timezone.utc)
    for n in range(15):
        if n != 3:
            con.execute(
                "INSERT INTO seo_audits VALUES (?,?)",
                ("p", (now - timedelta(days=n)).isoformat()),
            )
    report = health.HealthReport()
    health._pruefe_laufluecken(con, "p", {"schedule_cron": "0 8 * * *"}, now, report)
    assert len(report.kritisch) == 1 and "2026-11-11" in report.kritisch[0].detail


def test_health_does_not_latch_its_own_old_exit_code(tmp_path):
    (tmp_path / "selfcheck.json").write_text(
        json.dumps({"job": "selfcheck", "status": "failed", "exit_code": 2})
    )
    report = health.HealthReport()
    health._pruefe_betriebsjobs(tmp_path, datetime.now(timezone.utc), report)
    assert report.befunde == []


def test_ki_radar_releases_writer_before_next_network_request(tmp_path):
    from seo_autopilot import ki_sichtbarkeit as k

    con = k.db_oeffnen(str(tmp_path / "db"))

    def fetch(url):
        other = sqlite3.connect(tmp_path / "db", timeout=0.01)
        try:
            other.execute("INSERT INTO ki_laeufe VALUES (?,?)", (url, "test"))
            other.commit()
        finally:
            other.close()
        return "Crawler-TestBot"

    k.radar_quellen(con, fetch, True)
    assert con.execute("SELECT count(*) FROM ki_laeufe").fetchone()[0] == len(k.QUELLEN)


def test_incomplete_ki_report_shows_outage_instead_of_no_mentions():
    from seo_autopilot import kundenbericht as kb

    ks = {
        "fragen": 1,
        "genannt_je_ki": {"ChatGPT": 0},
        "ergebnisse": {"frage": {"ChatGPT": {"genannt": "fehler"}}},
    }
    notices = kb.hinweise({"ki_sichtbarkeit": ks})
    assert any("unvollständig" in x for x in notices)
    assert not any("Keine KI" in x for x in notices)
    html = "".join(kb._abschnitt_ki({"ki_sichtbarkeit": ks}))
    assert "0 von 0 erfolgreichen Antworten" in html and "ausgefallen" in html


@pytest.mark.asyncio
async def test_partial_audit_is_persisted_partial_and_does_not_close_approvals(
    monkeypatch,
):
    from seo_autopilot.api import main as api
    from seo_autopilot.core.project_manager import ProjectConfig
    import seo_autopilot.identitaet as identity
    import seo_autopilot.ausfuehrung as approvals

    p = ProjectConfig(
        id="test",
        name="Test",
        domain="https://example.invalid",
        notifications_enabled=False,
    )

    class A:
        name = "keyword"

        def __init__(self, *a, **k):
            pass

        async def run(self):
            from seo_autopilot.agents.base import AgentStatus

            return SimpleNamespace(
                status=AgentStatus.SKIPPED,
                errors=["403"],
                issues=[],
                fixes=[],
                metrics={},
                log_output="",
            )

    recorded = []

    async def save(ctx):
        recorded.append(ctx.status)

    async def noop(*a, **k):
        pass

    def forbidden(*a, **k):
        raise AssertionError("incomplete audit must not close approvals")

    for name in [
        "AnalyzerAgent",
        "KeywordAgent",
        "TrendsAgent",
        "StrategyAgent",
        "ContentAgent",
        "ApplyAgent",
    ]:
        monkeypatch.setattr(api, name, A)
    monkeypatch.setattr(api.project_manager, "get_project", lambda _: p)
    monkeypatch.setattr(api.project_manager, "update_project", lambda *a, **k: None)
    monkeypatch.setattr(identity, "sicherstellen", noop)
    monkeypatch.setattr(api.event_bus, "emit", noop)
    monkeypatch.setattr(api, "persist_audit", save)
    monkeypatch.setattr(api, "render_html_report", lambda ctx: None)
    monkeypatch.setattr(approvals, "erledigte_schliessen", forbidden)
    aid = await api.run_audit_for_project("test")
    assert api.LAUF_STATUS[aid] == "partial" and recorded == ["partial"]


def test_generic_alt_is_reported_without_demanding_a_caption():
    from seo_autopilot.analyzers.image_audit import ImageAuditor, extract_images

    images = extract_images(
        '<figure><img src="/foto.webp" alt="Foto" width="10" height="10"></figure>',
        "https://x.invalid",
    )
    issues = ImageAuditor()._pruefe_metadaten("https://x.invalid", images)
    assert any(i["type"] == "image_uninformative_alt" for i in issues)
    assert not any(i["type"] == "image_figure_without_caption" for i in issues)


def test_cron_plan_preserves_other_jobs_and_does_not_increase_mail_frequency():
    from seo_autopilot.betriebsplan import vorbereiten

    original = "0 7 * * * /opt/odoo/docs/seo-autopilot/venv/bin/python3 -m seo_autopilot.cli.main run --project-id tentacl-ai >> /tmp/log 2>&1\n40 7 * * 1 cd /opt/odoo/docs/seo-autopilot && venv/bin/python3 -m seo_autopilot.cli.main kundenbericht --senden >> /tmp/mail 2>&1\n15 2 * * * /opt/backup.sh\n"
    planned, changes, schedules = vorbereiten(original)
    assert "15 2 * * * /opt/backup.sh" in planned
    assert schedules["audit-tentacl-ai"] == ["15 4 * * *"]
    assert schedules["kundenbericht"] == ["40 7 * * 1"]
    assert "betriebsjob kundenbericht" in planned and planned.count("fachquellen") == 2
    again, _, _ = vorbereiten(original)
    assert planned == again


def test_unknown_seo_command_blocks_cron_migration():
    from seo_autopilot.betriebsplan import vorbereiten

    with pytest.raises(ValueError):
        vorbereiten(
            "0 1 * * * venv/bin/python3 -m seo_autopilot.unknown --apply >> /tmp/log"
        )


def test_official_source_baseline_change_and_failure(monkeypatch):
    from seo_autopilot import fachquellen as f

    monkeypatch.setattr(f, "QUELLEN", {"Source": "https://example.invalid/guide"})

    def html(text):
        return "<main>" + text * 50 + "</main>"

    first = f.vergleichen({}, lambda _: html("Original guidance. "))
    assert first["aenderungen"] == [] and first["fehler"] == []
    changed = f.vergleichen(first, lambda _: html("New guidance. "))
    assert len(changed["aenderungen"]) == 1 and changed["aenderungen"][0]["diff"]

    def offline(_):
        raise OSError("offline")

    failed = f.vergleichen(changed, offline)
    assert failed["fehler"] == ["Source"] and failed["quellen"] == changed["quellen"]


def test_missed_weekly_job_is_detected_even_without_a_status_file(tmp_path):
    state = tmp_path / "betrieb"
    state.mkdir()
    (tmp_path / "betriebsplan.json").write_text(json.dumps({"ki-radar": ["5 8 * * 1"]}))
    report = health.HealthReport()
    health._pruefe_betriebsjobs(
        state, datetime(2026, 11, 14, 14, tzinfo=timezone.utc), report
    )
    assert any("nicht gestartet" in b.titel for b in report.kritisch)


def test_weekly_action_report_does_not_claim_proposals_as_completed(tmp_path):
    from seo_autopilot import kundenbericht as kb

    con = sqlite3.connect(tmp_path / "db")
    con.execute(
        "CREATE TABLE change_log(project_id,urheber,status,zeitpunkt,aktion,ziel_url,begruendung,git_commit)"
    )
    rows = [
        (
            "p",
            "autopilot",
            "angewendet",
            "2026-11-13",
            "alt",
            "https://x",
            "Neues Bild",
            "abc",
        ),
        (
            "p",
            "mensch",
            "angewendet",
            "2026-11-13",
            "title",
            "https://x",
            "Fremde Arbeit",
            "def",
        ),
        (
            "p",
            "autopilot",
            "fehlgeschlagen",
            "2026-11-13",
            "title",
            "https://x",
            "Fehler",
            "",
        ),
    ]
    con.executemany("INSERT INTO change_log VALUES(?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    from datetime import date

    actions = kb.erledigt_diese_woche(str(tmp_path / "db"), "p", date(2026, 11, 14))
    assert len(actions) == 1 and actions[0]["begruendung"] == "Neues Bild"
    html = "".join(kb._abschnitt_erledigt({"erledigt_diese_woche": actions}))
    assert "Neues Bild" in html and "Fremde Arbeit" not in html


def test_image_baseline_is_not_claimed_as_new_assets():
    assert bs.bild_delta(None, [{"page": "p", "src": "x", "alt": "Bild"}])["neu"] == []
    assert bs.bild_delta(None, [])["grundstand"] is True


def test_new_images_and_changed_descriptions_are_distinguished():
    before = [{"page": "p", "src": "x", "alt": "Original"}]
    after = [
        {"page": "p", "src": "x", "alt": "Genauer Bildinhalt"},
        {"page": "p", "src": "y", "alt": "Neues Bild"},
    ]
    delta = bs.bild_delta(before, after)
    assert [i["src"] for i in delta["neu"]] == ["y"]
    assert [i["src"] for i in delta["geaendert"]] == ["x"]
    assert bs.bild_delta(after, after)["neu"] == []


def test_stopping_wrapper_stops_child_and_releases_lock(tmp_path):
    import os, signal, subprocess

    pidfile = tmp_path / "child.pid"
    child = "import os,pathlib,time;pathlib.Path(__import__('sys').argv[1]).write_text(str(os.getpid()));time.sleep(30)"
    launcher = "from pathlib import Path;from seo_autopilot.betriebsjob import ausfuehren;import sys;ausfuehren([sys.executable,'-c',sys.argv[2],sys.argv[3]],'signal',Path(sys.argv[1]))"
    process = subprocess.Popen(
        [sys.executable, "-c", launcher, str(tmp_path), child, str(pidfile)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pidfile.exists()
        pid = int(pidfile.read_text())
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
        assert not Path("/proc").joinpath(str(pid)).exists()
        assert (
            json.loads((tmp_path / "signal.json").read_text())["status"]
            == "interrupted"
        )
        assert (
            betriebsjob.ausfuehren(
                [sys.executable, "-c", "pass"], "after", tmp_path, wait=0.1
            )
            == 0
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
