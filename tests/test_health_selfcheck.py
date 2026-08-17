"""Tests für den Selbstprüfungs-Wächter.

Wichtig: Jeder Test weist zuerst nach, dass der Wächter im Fehlerfall ROT
wird. Ein Wächter, den man nur im grünen Zustand gesehen hat, ist kein
Beweis für irgendetwas.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from seo_autopilot import health
from seo_autopilot.health import MAX_ALTER_STUNDEN, run_selfcheck

JETZT = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def umgebung(tmp_path):
    """Baut eine minimale, gesunde Installation auf."""
    db = tmp_path / "test.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table alembic_version (version_num text);
        create table seo_projects (id text);
        create table seo_issues (id text, audit_id text);
        create table seo_audits (
            id text, project_id text, score real, status text,
            issues_found int, total_pages int, started_at text
        );
        """)
    con.commit()
    con.close()

    projects = tmp_path / "projects.yaml"

    def schreibe_projekte(daten):
        projects.write_text(yaml.safe_dump(daten), encoding="utf-8")

    def audit(project_id, stunden_her=1, score=70.0, pages=15, status="completed"):
        con = sqlite3.connect(db)
        con.execute(
            "insert into seo_audits values (?,?,?,?,?,?,?)",
            (
                f"a-{project_id}-{stunden_her}",
                project_id,
                score,
                status,
                10,
                pages,
                (JETZT - timedelta(hours=stunden_her)).isoformat(),
            ),
        )
        con.commit()
        con.close()

    return {
        "db": str(db),
        "projects": str(projects),
        "schreibe_projekte": schreibe_projekte,
        "audit": audit,
    }


CRON_MIT_BEISPIEL = "0 7 * * * ... --project-id beispiel >> log 2>&1\n"


def _pruefe(umgebung, crontab=CRON_MIT_BEISPIEL):
    return run_selfcheck(
        db_pfad=umgebung["db"],
        projects_pfad=umgebung["projects"],
        jetzt=JETZT,
        crontab_text=crontab,
    )


def _gesund(quellen=None, source_config=None):
    """Eine Installation ohne jeden Mangel.

    Der PageSpeed-Schlüssel gehört ausdrücklich dazu: ohne ihn liefert Google
    keine Core Web Vitals, und genau das soll der Wächter melden (siehe
    ``TestWarnungen.test_pagespeed_ohne_schluessel``). Ein Platzhalterwert
    genügt — geprüft wird nur, ob überhaupt etwas hinterlegt ist.
    """
    return {
        "projects": {
            "beispiel": {
                "domain": "https://example.com",
                "enabled": True,
                "enabled_sources": quellen if quellen is not None else [],
                "source_config": (
                    source_config
                    if source_config is not None
                    else {"pagespeed": {"api_key": "platzhalter"}}
                ),
            }
        }
    }


class TestGesunderZustand:
    def test_frischer_lauf_ist_gruen(self, umgebung):
        umgebung["schreibe_projekte"](_gesund())
        umgebung["audit"]("beispiel", stunden_her=2)
        report = _pruefe(umgebung)
        assert report.exit_code == 0, report.as_text()
        assert "OK" in report.as_text()


class TestAusfaelleWerdenRot:
    def test_projekt_ohne_jeden_lauf(self, umgebung):
        """Der reale topal-Fall: konfiguriert, aber nie ausgeführt."""
        umgebung["schreibe_projekte"](_gesund())
        report = _pruefe(umgebung)
        assert report.exit_code == 2
        assert any("Noch nie gelaufen" in b.titel for b in report.kritisch)

    def test_veralteter_lauf(self, umgebung):
        umgebung["schreibe_projekte"](_gesund())
        umgebung["audit"]("beispiel", stunden_her=MAX_ALTER_STUNDEN + 5)
        report = _pruefe(umgebung)
        assert report.exit_code == 2
        assert any("kein Lauf" in b.titel for b in report.kritisch)

    def test_lauf_ohne_erfasste_seiten(self, umgebung):
        """Der reale joseph-Fall: Domain zeigte ins Leere, Crawl lieferte nichts."""
        umgebung["schreibe_projekte"](_gesund())
        umgebung["audit"]("beispiel", stunden_her=1, pages=0)
        report = _pruefe(umgebung)
        assert report.exit_code == 2
        assert any("keine Seite erfasst" in b.titel for b in report.kritisch)

    def test_fehlende_migrationsmarke(self, umgebung, tmp_path):
        """Der reale Mai-Fall: Speicherung scheiterte wochenlang still."""
        umgebung["schreibe_projekte"](_gesund())
        umgebung["audit"]("beispiel", stunden_her=1)
        con = sqlite3.connect(umgebung["db"])
        con.execute("drop table alembic_version")
        con.commit()
        con.close()
        report = _pruefe(umgebung)
        assert report.exit_code == 2
        assert any("Migrationskontrolle" in b.titel for b in report.kritisch)

    def test_fehlende_datenbank(self, tmp_path):
        p = tmp_path / "projects.yaml"
        p.write_text(yaml.safe_dump(_gesund()), encoding="utf-8")
        report = run_selfcheck(
            db_pfad=str(tmp_path / "gibtsnicht.db"), projects_pfad=str(p), jetzt=JETZT
        )
        assert report.exit_code == 2


class TestWarnungen:
    def test_gsc_aktiviert_aber_unkonfiguriert(self, umgebung):
        """Der reale joseph-Fall: 'gsc' aktiv, aber ohne Zugangsdaten."""
        umgebung["schreibe_projekte"](_gesund(quellen=["gsc"]))
        umgebung["audit"]("beispiel", stunden_her=1)
        report = _pruefe(umgebung)
        assert report.exit_code == 1
        assert any("nicht konfiguriert" in b.titel for b in report.warnungen)

    def test_pagespeed_ohne_schluessel(self, umgebung, monkeypatch):
        """Ohne Schlüssel gibt es KEINE Core Web Vitals — das muss auffallen.

        Google beantwortet schlüssellose Anfragen aus einem gemeinsamen
        Tageskontingent, das dauerhaft erschöpft ist (HTTP 429). Im Log stand
        dazu nur ein beiläufiges "PageSpeed unavailable".
        """
        monkeypatch.setattr(health, "_globaler_pagespeed_schluessel", lambda: None)
        umgebung["schreibe_projekte"](_gesund(source_config={}))
        umgebung["audit"]("beispiel", stunden_her=1)
        report = _pruefe(umgebung)
        assert report.exit_code == 1
        assert any(
            "Geschwindigkeitsmessung" in b.titel for b in report.warnungen
        ), report.as_text()

    def test_pagespeed_schluessel_global_hinterlegt(self, umgebung, monkeypatch):
        """Der globale Weg zählt genauso wie der projektweise."""
        monkeypatch.setattr(
            health, "_globaler_pagespeed_schluessel", lambda: "platzhalter"
        )
        umgebung["schreibe_projekte"](_gesund(source_config={}))
        umgebung["audit"]("beispiel", stunden_her=1)
        report = _pruefe(umgebung)
        assert not any("Geschwindigkeitsmessung" in b.titel for b in report.befunde)

    def test_schluessel_aus_der_env_datei_zaehlt_mit(self, monkeypatch):
        """Der Schlüssel darf in der .env stehen — os.environ sieht ihn nicht.

        Genau diese Lücke hat den echten Fehler verschleiert: In der .env lag
        ein gültiger Schlüssel, der Analyzer hat ihn nie gelesen, und ein
        Wächter, der nur os.environ prüft, hätte danebengelegen.
        """
        from seo_autopilot.core.config import settings

        monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
        monkeypatch.setattr(settings, "PAGESPEED_API_KEY", "aus-der-env-datei")
        assert health._globaler_pagespeed_schluessel() == "aus-der-env-datei"

    def test_score_einbruch(self, umgebung):
        umgebung["schreibe_projekte"](_gesund())
        umgebung["audit"]("beispiel", stunden_her=30, score=80.0)
        umgebung["audit"]("beispiel", stunden_her=1, score=50.0)
        report = _pruefe(umgebung)
        assert any("eingebrochen" in b.titel for b in report.warnungen)

    def test_kleine_schwankung_ist_kein_alarm(self, umgebung):
        umgebung["schreibe_projekte"](_gesund())
        umgebung["audit"]("beispiel", stunden_her=30, score=72.0)
        umgebung["audit"]("beispiel", stunden_her=1, score=70.0)
        report = _pruefe(umgebung)
        assert not any("eingebrochen" in b.titel for b in report.befunde)

    def test_deaktivierte_projekte_werden_uebersprungen(self, umgebung):
        daten = _gesund()
        daten["projects"]["beispiel"]["enabled"] = False
        umgebung["schreibe_projekte"](daten)
        report = _pruefe(umgebung)
        assert report.geprueft == 0
        assert report.exit_code == 0


def test_fehlender_zeitplan_wird_gemeldet(umgebung):
    """Der reale topal-Fall: aktiv, aber von keinem Cron aufgerufen."""
    umgebung["schreibe_projekte"](_gesund())
    umgebung["audit"]("beispiel", stunden_her=1)
    report = _pruefe(umgebung, crontab="0 7 * * * --project-id anderes\n")
    assert report.exit_code == 1
    assert any("Kein Zeitplan" in b.titel for b in report.warnungen)
