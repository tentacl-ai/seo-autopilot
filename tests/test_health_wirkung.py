"""
Tests der Wächter-Prüfung für die Wirkungsmessung.

Die Wirkungsmessung ist besonders anfällig für einen stillen Ausfall: Sie
meldet auch im gesunden Normalbetrieb wochenlang „nichts fällig" — genau wie
eine kaputte. Deshalb muss der Wächter sie überwachen, und deshalb wird hier
jeder Ausfall zuerst rot bewiesen.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from seo_autopilot.changelog_book import AKTION_META_TITLE, notiere_aenderung
from seo_autopilot.health import run_selfcheck
from seo_autopilot.wirkung import Messung, speichere_messung, tabelle_anlegen

CRON_GESUND = (
    "45 11 * * * . /opt/scripts/telegram.env && cd /opt/odoo/docs/seo-autopilot "
    "&& /opt/.../venv/bin/python3 -m seo_autopilot.cli.main wirkung --messen\n"
    "0 10 * * * /opt/.../python3 -m "
    "seo_autopilot.cli.main run --project-id joseph\n"
)

CRON_OHNE_WIRKUNG = (
    "0 10 * * * cd /opt/odoo/docs/seo-autopilot && /opt/.../python3 -m "
    "seo_autopilot.cli.main run --project-id joseph\n"
)

CRON_OHNE_CD = (
    "45 11 * * * . /opt/scripts/telegram.env && /opt/.../venv/bin/python3 -m "
    "seo_autopilot.cli.main wirkung --messen\n"
)


@pytest.fixture
def umgebung(tmp_path):
    """Minimal lauffähige DB + Projektliste, damit der Wächter durchläuft."""
    db = tmp_path / "seo.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        create table alembic_version (version_num text);
        create table seo_audits (
            id text primary key, project_id text, started_at text,
            status text, score real, issues_found integer, total_pages integer
        );
        create table seo_issues (id text primary key, audit_id text);
        create table seo_projects (id text primary key);
        """
    )
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
        "    enabled_sources: []\n",
        encoding="utf-8",
    )
    tabelle_anlegen(str(db))
    return db, projekte


def _befunde_zu_wirkung(report):
    return [b for b in report.befunde if "irkung" in b.titel]


def _faellige_aenderung(db, tage_her=30):
    notiere_aenderung(
        str(db),
        "joseph",
        AKTION_META_TITLE,
        ziel_url="https://example.com/",
        vorher="Alt",
        nachher="Neu",
        zeitpunkt=datetime.now(timezone.utc) - timedelta(days=tage_her),
    )


class TestCronUeberwachung:
    """Der Cron-Check greift erst, wenn überhaupt etwas protokolliert wurde.

    Deshalb legt jeder Test hier eine FRISCHE Änderung an: Sie ist noch nicht
    messbar (das braucht sieben Tage), macht den Lauf aber ab sofort nötig.
    """

    @pytest.fixture(autouse=True)
    def _mit_aenderung(self, umgebung):
        db, _ = umgebung
        _faellige_aenderung(db, tage_her=0)

    def test_fehlender_cron_wird_gemeldet(self, umgebung):
        db, projekte = umgebung
        report = run_selfcheck(
            db_pfad=str(db), projects_pfad=str(projekte), crontab_text=CRON_OHNE_WIRKUNG
        )
        treffer = _befunde_zu_wirkung(report)
        assert treffer, "fehlender Cron muss auffallen"
        assert "läuft nicht automatisch" in treffer[0].titel

    def test_gesunder_cron_meldet_nichts(self, umgebung):
        db, projekte = umgebung
        report = run_selfcheck(
            db_pfad=str(db), projects_pfad=str(projekte), crontab_text=CRON_GESUND
        )
        assert _befunde_zu_wirkung(report) == []

    def test_cron_ohne_cd_wird_gemeldet(self, umgebung):
        """Genau dieser Fehler ist beim Einrichten zweimal passiert.

        Seit die Projektliste absolut aufgelöst wird, ist es keine Störung
        mehr, sondern ein Hinweis — relative --db/--projects-Angaben würden
        weiterhin ins Leere laufen.
        """
        db, projekte = umgebung
        report = run_selfcheck(
            db_pfad=str(db), projects_pfad=str(projekte), crontab_text=CRON_OHNE_CD
        )
        treffer = _befunde_zu_wirkung(report)
        assert treffer
        assert treffer[0].schwere == "warnung"
        assert "cd" in treffer[0].abhilfe

    def test_auskommentierter_cron_zaehlt_nicht(self, umgebung):
        db, projekte = umgebung
        report = run_selfcheck(
            db_pfad=str(db),
            projects_pfad=str(projekte),
            crontab_text="# 45 11 * * * ... seo_autopilot.cli.main wirkung --messen\n",
        )
        treffer = _befunde_zu_wirkung(report)
        assert treffer, "ein auskommentierter Eintrag laeuft nicht"
        assert "läuft nicht automatisch" in treffer[0].titel


class TestFrischeInstallation:
    """Ohne protokollierte Änderung gibt es nichts zu messen.

    Ein Wächter, der auf einer frischen Installation schon meckert, erzieht
    zum Wegsehen — und genau dann übersieht man die echten Meldungen.
    """

    def test_leeres_aenderungsbuch_meldet_nichts(self, umgebung):
        db, projekte = umgebung
        report = run_selfcheck(
            db_pfad=str(db),
            projects_pfad=str(projekte),
            crontab_text=CRON_OHNE_WIRKUNG,
        )
        assert _befunde_zu_wirkung(report) == []

    def test_erste_aenderung_macht_den_lauf_noetig(self, umgebung):
        db, projekte = umgebung
        _faellige_aenderung(db, tage_her=0)

        report = run_selfcheck(
            db_pfad=str(db),
            projects_pfad=str(projekte),
            crontab_text=CRON_OHNE_WIRKUNG,
        )

        assert _befunde_zu_wirkung(report), "ab jetzt muss der Lauf existieren"


class TestLiegengebliebeneMessungen:
    def test_faellig_aber_nie_gemessen(self, umgebung):
        db, projekte = umgebung
        _faellige_aenderung(db)

        report = run_selfcheck(
            db_pfad=str(db), projects_pfad=str(projekte), crontab_text=CRON_GESUND
        )

        treffer = _befunde_zu_wirkung(report)
        assert treffer, "faellige Messungen ohne jedes Ergebnis muessen auffallen"
        assert "noch nie gemessen" in treffer[0].titel

    def test_veraltete_messung_bei_faelliger_arbeit(self, umgebung):
        db, projekte = umgebung
        _faellige_aenderung(db)
        alt = (datetime.now(timezone.utc) - timedelta(days=12)).date().isoformat()
        speichere_messung(
            str(db),
            Messung(
                id="m1",
                change_id="fremd",
                project_id="joseph",
                ziel_url="https://example.com/x",
                aktion=AKTION_META_TITLE,
                urheber="autopilot",
                fenster_tage=7,
                geaendert_am="2026-07-01",
                gemessen_am=alt,
                vorher_von="2026-06-24",
                vorher_bis="2026-06-30",
                nachher_von="2026-07-02",
                nachher_bis="2026-07-08",
            ),
        )

        report = run_selfcheck(
            db_pfad=str(db), projects_pfad=str(projekte), crontab_text=CRON_GESUND
        )

        treffer = _befunde_zu_wirkung(report)
        assert treffer
        assert "seit 12 Tagen ohne Ergebnis" in treffer[0].titel

    def test_frische_messung_ist_in_ordnung(self, umgebung):
        """Ein Lauf von gestern ist normal — die Search Console hinkt ohnehin."""
        db, projekte = umgebung
        _faellige_aenderung(db)
        gestern = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        speichere_messung(
            str(db),
            Messung(
                id="m2",
                change_id="fremd",
                project_id="joseph",
                ziel_url="https://example.com/x",
                aktion=AKTION_META_TITLE,
                urheber="autopilot",
                fenster_tage=7,
                geaendert_am="2026-07-01",
                gemessen_am=gestern,
                vorher_von="2026-06-24",
                vorher_bis="2026-06-30",
                nachher_von="2026-07-02",
                nachher_bis="2026-07-08",
            ),
        )

        report = run_selfcheck(
            db_pfad=str(db), projects_pfad=str(projekte), crontab_text=CRON_GESUND
        )

        assert _befunde_zu_wirkung(report) == []

    def test_ohne_faellige_arbeit_keine_meldung(self, umgebung):
        """Ein stiller Wächter ist richtig, solange nichts zu tun ist."""
        db, projekte = umgebung
        report = run_selfcheck(
            db_pfad=str(db), projects_pfad=str(projekte), crontab_text=CRON_GESUND
        )
        assert _befunde_zu_wirkung(report) == []
