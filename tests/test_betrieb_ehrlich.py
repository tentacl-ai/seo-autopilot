"""
Tests fuer v1.15 Stufe 1 ("ehrlicher Betrieb", 18.09.2026).

Jeder Test beschreibt einen Fehler, der im Live-Betrieb wirklich passiert ist:
- Bildmasse wurden als "behoben" gefuehrt, obwohl 27 von 33 Bildern live keine
  hatten (Bild ohne lokale Datei -> nichts geaendert -> "already-applied").
- /seo-check/ liegt in einem anderen Webroot -> jeden Tag zwei FileNotFoundError
  als "fehlgeschlagen" im Aenderungsbuch.
- Ein abgestuerzter Audit endete mit "done" und Exit 0.
- Der PageSpeed-Schluessel lag in .env, wurde im Cron (ohne cd) nie gelesen.
"""

import asyncio
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import seo_autopilot.ausfuehrung as ausfuehrung
from seo_autopilot.adapters.static_files import StaticFilesAdapter
from seo_autopilot.agents.apply import ApplyAgent
from seo_autopilot.ausfuehrung import tabelle_anlegen

SEITE = """<!doctype html><html lang="de"><head><title>T</title></head>
<body><main><h1>X</h1>
<img src="https://cdn.fremd.de/bild.jpg">
</main></body></html>"""


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    (tmp_path / "seite").mkdir()
    (tmp_path / "seite" / "index.html").write_text(SEITE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def db(tmp_path, monkeypatch):
    pfad = str(tmp_path / "t.db")
    tabelle_anlegen(pfad)
    monkeypatch.setattr(ausfuehrung, "standard_db_pfad", lambda: pfad)
    return pfad


class TestNichtBehebbarIstNichtBehoben:
    def test_bild_ohne_lokale_datei_ist_nicht_behebbar(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {"type": "image_missing_dimensions", "seite": "https://x.de/seite/"}
        )
        assert not r.success
        assert r.commit_hash != "already-applied"
        assert r.nicht_behebbar and "cdn.fremd.de" in r.nicht_behebbar

    def test_seite_ohne_datei_ist_nicht_behebbar_statt_fehler(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {
                "type": "missing_twitter_card",
                "seite": "https://x.de/anderer-webroot/",
            }
        )
        assert not r.success
        assert r.error is None
        assert "keine Datei" in (r.nicht_behebbar or "")

    def test_wirklich_erledigt_bleibt_already_applied(self, site):
        (site / "seite" / "index.html").write_text(
            '<html><body><img src="/a.png" width="1" height="1"></body></html>',
            encoding="utf-8",
        )
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {"type": "image_missing_dimensions", "seite": "https://x.de/seite/"}
        )
        assert r.success and r.commit_hash == "already-applied"

    def test_apply_agent_zaehlt_es_weder_als_erfolg_noch_als_fehler(self, site, db):
        projekt = SimpleNamespace(
            id="test",
            betriebsart="autopilot",
            auto_fix_enabled=False,
            auto_fix_config={},
            adapter_type="static",
            adapter_config={"root_path": str(site)},
        )
        fix = {
            "type": "image_missing_dimensions",
            "source": "regel",
            "seite": "https://x.de/seite/",
            "priority": "low",
        }
        ctx = SimpleNamespace(
            agent_results={"content": SimpleNamespace(fixes=[fix])}, force_apply=False
        )
        r = asyncio.run(
            ApplyAgent(
                project_id="test", audit_id="a1", project_config=projekt, context=ctx
            ).run()
        )
        assert r.metrics["fixes_applied"] == 0
        assert r.metrics["fixes_failed"] == 0
        assert r.metrics["fixes_nicht_behebbar"] == 1
        # Persistenz liest success -> Befund bleibt "open"
        assert not any(f.get("success") for f in ctx.applied_fixes)


class TestAbsturzIstKeinErfolg:
    def test_run_endet_mit_exit_1_bei_fehlgeschlagenem_audit(self, monkeypatch):
        import seo_autopilot.api.main as api_main
        from seo_autopilot.cli import main as cli_main

        async def kaputt(project_id, force_apply=False):
            api_main.LAUF_STATUS["a-kaputt"] = "failed"
            return "a-kaputt"

        async def nichts():
            return None

        monkeypatch.setattr(api_main, "run_audit_for_project", kaputt)
        import seo_autopilot.core.project_manager as pm_mod
        import seo_autopilot.db.database as db_mod

        monkeypatch.setattr(
            pm_mod.ProjectManager, "get_project", lambda s, i: SimpleNamespace(id=i)
        )
        monkeypatch.setattr(db_mod.db, "initialize", nichts)
        monkeypatch.setattr(db_mod.db, "close", nichts)

        res = CliRunner().invoke(cli_main.cli, ["run", "--project-id", "x"])
        assert res.exit_code == 1
        assert "failed" in res.output


class TestEnvDateiAbsolut:
    def test_env_datei_wird_unabhaengig_vom_arbeitsordner_gefunden(self):
        from pathlib import Path

        from seo_autopilot.core.config import Settings

        env_file = Settings.Config.env_file
        assert Path(env_file).is_absolute()
        assert Path(env_file).name == ".env"


class TestWaechterSiehtStilleAusfaelle:
    def test_fehlendes_pillow_ist_kritisch(self, tmp_path):
        from seo_autopilot.health import HealthReport, _pruefe_werkzeug

        r = HealthReport()
        _pruefe_werkzeug(r, modul_da=lambda m: m != "PIL", browser_ordner=tmp_path)
        titel = [b.titel for b in r.kritisch]
        assert "Pillow fehlt" in titel
        # Playwright ist "da", aber kein Chromium im Ordner -> auch kritisch
        assert any("Browser" in t for t in titel)

    def test_alles_da_meldet_nichts(self, tmp_path):
        from seo_autopilot.health import HealthReport, _pruefe_werkzeug

        (tmp_path / "chromium_headless_shell-1").mkdir()
        r = HealthReport()
        _pruefe_werkzeug(r, modul_da=lambda m: True, browser_ordner=tmp_path)
        assert not r.kritisch

    def test_alte_freigaben_und_abgeschaltetes_projekt(self, tmp_path):
        import sqlite3
        from datetime import datetime, timezone

        from seo_autopilot.health import HealthReport, _pruefe_freigaben

        pfad = str(tmp_path / "t.db")
        tabelle_anlegen(pfad)
        con = sqlite3.connect(pfad)
        for pid, datum in (
            ("alt", "2026-08-01T00:00:00+00:00"),
            ("aus", "2026-09-17T00:00:00+00:00"),
            ("frisch", "2026-09-17T00:00:00+00:00"),
        ):
            con.execute(
                "insert into freigaben (id, project_id, erstellt_am, issue_type, titel, status) "
                "values (?, ?, ?, 'x', 't', 'offen')",
                (pid, pid, datum),
            )
        con.commit()
        r = HealthReport()
        _pruefe_freigaben(
            con,
            {"alt": {}, "aus": {"enabled": False}, "frisch": {}},
            datetime(2026, 9, 18, tzinfo=timezone.utc),
            r,
        )
        projekte = {b.projekt for b in r.warnungen}
        assert projekte == {"alt", "aus"}


class TestErledigteFreigabenSchliessen:
    def test_verschwundener_befund_wird_geschlossen(self, db):
        import sqlite3

        from seo_autopilot.ausfuehrung import erledigte_schliessen, freigaben

        con = sqlite3.connect(db)
        for fid, typ in (("1", "weg"), ("2", "noch_da")):
            con.execute(
                "insert into freigaben (id, project_id, erstellt_am, issue_type, titel, status) "
                "values (?, 'p', '2026-09-01T00:00:00+00:00', ?, 't', 'offen')",
                (fid, typ),
            )
        con.commit()
        assert erledigte_schliessen(db, "p", {"noch_da"}) == 1
        assert [f.issue_type for f in freigaben(db, project_id="p")] == ["noch_da"]
        # unbekannter Stand -> nichts anfassen
        assert erledigte_schliessen(db, "p", None) == 0


class TestSuchOperatorenSindKeineChancen:
    def test_site_abfrage_wird_ignoriert(self):
        from seo_autopilot.agents.keyword import echte_suchbegriffe

        kws = [
            {"query": "site:tentacl.ai", "position": 16, "impressions": 500},
            {"query": "campingplatz software", "position": 14, "impressions": 500},
            {"query": "", "position": 14, "impressions": 500},
        ]
        assert [k["query"] for k in echte_suchbegriffe(kws)] == [
            "campingplatz software"
        ]


class TestFremdeAenderungenNichtMitcommitten:
    def test_datei_mit_offenen_aenderungen_bleibt_unberuehrt(self, tmp_path):
        import subprocess

        (tmp_path / "seite").mkdir()
        datei = tmp_path / "seite" / "index.html"
        datei.write_text(
            "<html><head><title>T</title></head><body></body></html>", "utf-8"
        )
        for cmd in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "x"],
        ):
            subprocess.run(["git", *cmd], cwd=tmp_path, check=True)
        # eine andere Sitzung arbeitet gerade an der Datei
        datei.write_text(datei.read_text() + "<!-- halb fertig -->", "utf-8")
        a = StaticFilesAdapter({"root_path": str(tmp_path)})
        r = a.apply_fix(
            {"type": "missing_twitter_card", "seite": "https://x.de/seite/"}
        )
        assert not r.success
        assert "fremde" in (r.nicht_behebbar or "")
        assert datei.read_text().endswith("<!-- halb fertig -->")
