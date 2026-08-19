"""
Tests der Wächter-Prüfung für die Search-Console-Historie.

Der Historien-Import ist der stillste Dienst im ganzen Autopiloten: Im
Normalbetrieb überspringt er alle archivierten Monate und schreibt eine
freundliche Zeile ins Log — genau wie ein Import, dessen Zugang seit Wochen
abgelaufen ist. Der Unterschied fällt erst auf, wenn jemand nach der Historie
fragt, und dann ist der fehlende Monat bei Google unwiederbringlich weg
(16-Monats-Grenze).

Deshalb überwacht der Wächter zwei Dinge — jedes hier zuerst rot bewiesen:
1. Läuft der Import überhaupt automatisch (Cron vorhanden)?
2. Fehlt der zuletzt abgeschlossene Monat im Archiv?
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from seo_autopilot.health import run_selfcheck
from seo_autopilot.historie import _verbinde, TABELLE

CRON_GESUND = (
    "15 11 * * * /opt/.../venv/bin/python3 "
    "-m seo_autopilot.cli.main historie --importieren\n"
    "0 10 * * * /opt/.../python3 -m seo_autopilot.cli.main run --project-id joseph\n"
)

CRON_OHNE_HISTORIE = (
    "0 10 * * * /opt/.../python3 -m seo_autopilot.cli.main run --project-id joseph\n"
)


@pytest.fixture
def umgebung(tmp_path):
    """Minimal lauffähige DB + Projektliste mit einem GSC-Projekt."""
    db = tmp_path / "seo.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        create table alembic_version (version_num text);
        create table seo_audits (
            id text primary key, project_id text, started_at text,
            status text, score real, issues_found integer, total_pages integer
        );
        create table seo_issues (id text primary key, audit_id text);
        create table seo_projects (id text primary key);
        """)
    jetzt = datetime.now(timezone.utc)
    con.execute(
        "insert into seo_audits "
        "(id, project_id, started_at, status, score, issues_found, total_pages) "
        "values (?, ?, ?, ?, ?, ?, ?)",
        ("a1", "joseph", jetzt.isoformat(), "completed", 70.0, 12, 17),
    )
    con.commit()
    con.close()

    projekte = tmp_path / "projects.yaml"
    projekte.write_text(
        "projects:\n"
        "  joseph:\n"
        "    domain: https://example.com\n"
        "    enabled: true\n"
        "    schedule_cron: '0 10 * * *'\n"
        "    enabled_sources:\n"
        "    - gsc\n"
        "    source_config:\n"
        "      gsc:\n"
        "        property_url: https://example.com/\n"
        "        credentials_path: /dev/null\n",
        encoding="utf-8",
    )
    return db, projekte


def _befunde_zur_historie(report):
    return [b for b in report.befunde if "istorie" in b.titel]


def _archiviere(db, monat, project_id="joseph"):
    with _verbinde(str(db)) as conn:
        conn.execute(
            f"insert or replace into {TABELLE} (project_id, property_url, monat, "
            f"dimension, wert, klicks, impressionen, ctr, position, vollstaendig, "
            f"abgerufen_am) values (?, 'https://example.com/', ?, 'gesamt', '', "
            f"5, 50, 10.0, 5.0, 1, '2026-08-19')",
            (project_id, monat),
        )


JETZT = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def test_fehlender_cron_wird_gemeldet(umgebung):
    """Ohne Cron gehen Monate verloren, die Google nie wieder herausgibt."""
    db, projekte = umgebung
    _archiviere(db, "2026-07")
    report = run_selfcheck(
        db_pfad=str(db),
        projects_pfad=str(projekte),
        crontab_text=CRON_OHNE_HISTORIE,
        jetzt=JETZT,
    )
    treffer = _befunde_zur_historie(report)
    assert treffer, "fehlender Cron muss auffallen"
    assert "läuft nicht automatisch" in treffer[0].titel


def test_gesunder_zustand_meldet_nichts(umgebung):
    db, projekte = umgebung
    _archiviere(db, "2026-07")
    report = run_selfcheck(
        db_pfad=str(db),
        projects_pfad=str(projekte),
        crontab_text=CRON_GESUND,
        jetzt=JETZT,
    )
    assert _befunde_zur_historie(report) == []


def test_fehlender_vormonat_wird_gemeldet(umgebung):
    """Der zuletzt abgeschlossene Monat muss im Archiv stehen."""
    db, projekte = umgebung
    _archiviere(db, "2026-05")  # Juli fehlt
    report = run_selfcheck(
        db_pfad=str(db),
        projects_pfad=str(projekte),
        crontab_text=CRON_GESUND,
        jetzt=JETZT,
    )
    treffer = _befunde_zur_historie(report)
    assert treffer, "Lücke im Archiv muss auffallen"
    assert "2026-07" in treffer[0].detail


def test_projekt_ohne_search_console_wird_nicht_bemaengelt(umgebung, tmp_path):
    """topal hat kein GSC — das ist eine bekannte Tatsache, kein Ausfall."""
    db, _ = umgebung
    projekte = tmp_path / "ohne_gsc.yaml"
    projekte.write_text(
        "projects:\n"
        "  joseph:\n"
        "    domain: https://example.com\n"
        "    enabled: true\n"
        "    schedule_cron: '0 10 * * *'\n"
        "    enabled_sources: []\n",
        encoding="utf-8",
    )
    report = run_selfcheck(
        db_pfad=str(db),
        projects_pfad=str(projekte),
        crontab_text=CRON_GESUND,
        jetzt=JETZT,
    )
    assert _befunde_zur_historie(report) == []


def test_frisches_monatsende_wird_noch_nicht_bemaengelt(umgebung):
    """Am 2. des Monats liefert Google den Vormonat noch nicht vollstaendig."""
    db, projekte = umgebung
    _archiviere(db, "2026-06")  # Juli fehlt, aber es ist erst der 2. August
    report = run_selfcheck(
        db_pfad=str(db),
        projects_pfad=str(projekte),
        crontab_text=CRON_GESUND,
        jetzt=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
    )
    assert _befunde_zur_historie(report) == []


def test_leeres_archiv_wird_nicht_bemaengelt(umgebung):
    """Frische Installation: noch nie importiert ist kein Ausfall, sondern neu."""
    db, projekte = umgebung
    report = run_selfcheck(
        db_pfad=str(db),
        projects_pfad=str(projekte),
        crontab_text=CRON_GESUND,
        jetzt=JETZT,
    )
    assert _befunde_zur_historie(report) == []
