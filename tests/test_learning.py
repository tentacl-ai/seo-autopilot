"""Tests für die Lernschleife (widerlegte Befunde).

Die Lernschleife hat zwei Aufgaben, und beide werden hier einzeln bewiesen:

  1. Sie muss ein echtes Muster ROT melden — ein Befundtyp, der wiederholt
     und quer über mehrere Projekte widerlegt wurde, ist ein Analyzer-Bug.
  2. Sie darf bei einem Einzelfall NICHT anschlagen, sonst ist der Bericht
     nach zwei Wochen unlesbar und niemand schaut mehr hinein.

Dazu kommt die harte Betriebsregel: Ein kaputter Datenbankpfad kostet uns
Statistik, aber niemals einen Audit-Lauf.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from seo_autopilot.learning import (
    TABELLE,
    Muster,
    bericht_als_text,
    muster_bericht,
    record_refuted,
    tabelle_anlegen,
)

JETZT = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def befund(typ, grund="widerlegt am Live-HTML", **extra):
    daten = {
        "type": typ,
        "category": "compliance",
        "title": f"Befund {typ}",
        "severity": "high",
        "refuted_reason": grund,
    }
    daten.update(extra)
    return daten


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "learning.db")


def _tabellen(pfad):
    con = sqlite3.connect(pfad)
    try:
        return {
            r[0]
            for r in con.execute("select name from sqlite_master where type='table'")
        }
    finally:
        con.close()


class TestTabelle:
    def test_tabelle_wird_angelegt(self, db):
        assert tabelle_anlegen(db) is True
        assert TABELLE in _tabellen(db)

    def test_anlegen_ist_idempotent(self, db):
        """Zweiter Aufruf darf nicht knallen — es gibt bewusst kein alembic."""
        assert tabelle_anlegen(db) is True
        assert tabelle_anlegen(db) is True
        assert tabelle_anlegen(db) is True
        assert TABELLE in _tabellen(db)

    def test_anlegen_zerstoert_bestehende_daten_nicht(self, db):
        record_refuted(db, "joseph", "a1", [befund("missing_impressum")])
        tabelle_anlegen(db)
        con = sqlite3.connect(db)
        try:
            assert con.execute(f"select count(*) from {TABELLE}").fetchone()[0] == 1
        finally:
            con.close()

    def test_bestehende_audit_db_wird_nur_ergaenzt(self, db):
        """Die Tabelle kommt zu einer echten Audit-DB dazu, ohne sie anzufassen."""
        con = sqlite3.connect(db)
        con.executescript(
            "create table seo_audits (id text, project_id text);"
            "insert into seo_audits values ('a1','joseph');"
        )
        con.commit()
        con.close()

        record_refuted(db, "joseph", "a1", [befund("missing_impressum")])

        con = sqlite3.connect(db)
        try:
            assert con.execute("select count(*) from seo_audits").fetchone()[0] == 1
            assert TABELLE in _tabellen(db)
        finally:
            con.close()


class TestSpeichern:
    def test_speichern_zaehlt_und_schreibt_felder(self, db):
        n = record_refuted(
            db,
            "joseph",
            "audit-7",
            [
                befund(
                    "missing_impressum",
                    "Impressum ist unter /impressum erreichbar (HTTP 200)",
                    affected_url="https://joseph-hehenwarter.de/impressum",
                )
            ],
            zeitpunkt=JETZT,
        )
        assert n == 1

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            r = con.execute(f"select * from {TABELLE}").fetchone()
        finally:
            con.close()

        assert r["project_id"] == "joseph"
        assert r["audit_id"] == "audit-7"
        assert r["issue_type"] == "missing_impressum"
        assert r["category"] == "compliance"
        assert "erreichbar" in r["refuted_reason"]
        assert r["affected_url"] == "https://joseph-hehenwarter.de/impressum"
        assert r["detected_at"].startswith("2026-08-17")

    def test_mehrere_befunde_auf_einmal(self, db):
        n = record_refuted(
            db,
            "joseph",
            "audit-7",
            [befund("missing_impressum"), befund("missing_datenschutz")],
        )
        assert n == 2

    def test_leere_liste_schreibt_nichts_legt_aber_tabelle_an(self, db):
        assert record_refuted(db, "joseph", "audit-7", []) == 0
        assert TABELLE in _tabellen(db)

    def test_affected_url_aus_affected_items(self, db):
        """Viele Analyzer legen die Adresse nur als JSON in affected_items ab."""
        record_refuted(
            db,
            "topal",
            "a1",
            [
                befund(
                    "orphan_page",
                    affected_items='[{"url": "https://topal.de/kontakt"}]',
                )
            ],
        )
        con = sqlite3.connect(db)
        try:
            url = con.execute(f"select affected_url from {TABELLE}").fetchone()[0]
        finally:
            con.close()
        assert url == "https://topal.de/kontakt"

    def test_muell_im_befund_bricht_nichts(self, db):
        n = record_refuted(db, "joseph", "a1", [None, "kaputt", befund("noindex")])
        assert n == 1


class TestMusterBericht:
    def test_muster_ueber_mehrere_projekte_taucht_auf(self, db):
        """3 Widerlegungen bei 2 Projekten = Analyzer-Bug, muss sichtbar sein."""
        record_refuted(db, "joseph", "a1", [befund("missing_impressum")], JETZT)
        record_refuted(db, "joseph", "a2", [befund("missing_impressum")], JETZT)
        record_refuted(db, "topal", "b1", [befund("missing_impressum")], JETZT)

        muster = muster_bericht(db, jetzt=JETZT)

        assert len(muster) == 1
        m = muster[0]
        assert m.issue_type == "missing_impressum"
        assert m.treffer == 3
        assert m.projekte == 2
        assert m.projekt_namen == ["joseph", "topal"]
        assert m.systematisch is True
        assert m.urteil == "Analyzer-Bug"

    def test_einzelfall_taucht_nicht_auf(self, db):
        record_refuted(db, "joseph", "a1", [befund("missing_org_schema")], JETZT)
        assert muster_bericht(db, jetzt=JETZT) == []

    def test_zwei_treffer_reichen_nicht(self, db):
        """Schwelle ist 3 — zwei Zufaelle sind noch kein Muster."""
        record_refuted(db, "joseph", "a1", [befund("noindex")], JETZT)
        record_refuted(db, "topal", "b1", [befund("noindex")], JETZT)
        assert muster_bericht(db, jetzt=JETZT) == []
        # Mit abgesenkter Schwelle wird derselbe Datenstand sichtbar:
        assert len(muster_bericht(db, min_treffer=2, jetzt=JETZT)) == 1

    def test_ein_projekt_gilt_als_beobachten_nicht_als_bug(self, db):
        for i in range(4):
            record_refuted(db, "joseph", f"a{i}", [befund("images_without_alt")], JETZT)
        m = muster_bericht(db, jetzt=JETZT)[0]
        assert m.treffer == 4
        assert m.projekte == 1
        assert m.systematisch is False
        assert m.urteil == "beobachten"

    def test_zeitfenster_wird_respektiert(self, db):
        alt = JETZT - timedelta(days=45)
        for i in range(3):
            record_refuted(db, "joseph", f"alt{i}", [befund("missing_impressum")], alt)
        assert muster_bericht(db, jetzt=JETZT) == []
        # Fenster weit genug aufmachen -> derselbe Datenstand erscheint.
        assert len(muster_bericht(db, tage=90, jetzt=JETZT)) == 1

    def test_alte_treffer_zaehlen_nicht_zur_schwelle(self, db):
        record_refuted(
            db, "joseph", "alt", [befund("noindex")], JETZT - timedelta(days=45)
        )
        record_refuted(db, "joseph", "neu1", [befund("noindex")], JETZT)
        record_refuted(db, "topal", "neu2", [befund("noindex")], JETZT)
        # Nur 2 Treffer im Fenster -> kein Muster.
        assert muster_bericht(db, jetzt=JETZT) == []

    def test_sortierung_nach_haeufigkeit(self, db):
        for i in range(5):
            record_refuted(db, f"p{i}", "a", [befund("missing_impressum")], JETZT)
        for i in range(3):
            record_refuted(db, f"p{i}", "a", [befund("missing_datenschutz")], JETZT)

        typen = [m.issue_type for m in muster_bericht(db, jetzt=JETZT)]
        assert typen == ["missing_impressum", "missing_datenschutz"]

    def test_bericht_trennt_typen(self, db):
        for i in range(3):
            record_refuted(db, f"p{i}", "a", [befund("noindex")], JETZT)
            record_refuted(db, f"p{i}", "a", [befund("orphan_page")], JETZT)
        muster = muster_bericht(db, jetzt=JETZT)
        assert {m.issue_type for m in muster} == {"noindex", "orphan_page"}
        assert all(m.treffer == 3 for m in muster)


class TestRobustheit:
    def test_kaputte_db_bricht_speichern_nicht_ab(self, tmp_path):
        """Kein Ausnahmefall darf nach oben durchschlagen — das kostet ein Audit."""
        kaputt = tmp_path / "kaputt.db"
        kaputt.write_bytes(b"das ist keine sqlite-datei, sondern muell" * 20)

        assert record_refuted(str(kaputt), "joseph", "a1", [befund("noindex")]) == 0

    def test_kaputte_db_bricht_bericht_nicht_ab(self, tmp_path):
        kaputt = tmp_path / "kaputt.db"
        kaputt.write_bytes(b"das ist keine sqlite-datei, sondern muell" * 20)

        assert muster_bericht(str(kaputt)) == []

    def test_unbeschreibbarer_pfad_bricht_nichts_ab(self, tmp_path):
        pfad = str(tmp_path / "gibt" / "es" / "nicht" / "x.db")
        assert tabelle_anlegen(pfad) is False
        assert record_refuted(pfad, "joseph", "a1", [befund("noindex")]) == 0
        assert muster_bericht(pfad) == []

    def test_bericht_ohne_tabelle_ist_leer(self, tmp_path):
        """Frische Audit-DB, noch nie etwas widerlegt -> leer, kein Fehler."""
        pfad = str(tmp_path / "frisch.db")
        sqlite3.connect(pfad).close()
        assert muster_bericht(pfad) == []


class TestTextausgabe:
    def test_leerer_bericht_sagt_das_klar(self):
        text = bericht_als_text([])
        assert "Keine wiederkehrenden Fehlalarme" in text

    def test_tabelle_enthaelt_zahlen_und_urteil(self):
        text = bericht_als_text(
            [
                Muster(
                    issue_type="missing_impressum",
                    treffer=5,
                    projekte=3,
                    projekt_namen=["joseph", "topal", "bh"],
                    beispiel_begruendung="Impressum ist unter /impressum erreichbar",
                )
            ]
        )
        assert "missing_impressum" in text
        assert "5" in text
        assert "Analyzer-Bug" in text
        assert "joseph" in text
        assert "/impressum erreichbar" in text


class TestCli:
    def test_learnings_kommando_zeigt_muster(self, db):
        from click.testing import CliRunner

        from seo_autopilot.cli.main import cli

        for p in ("joseph", "topal", "bh"):
            record_refuted(db, p, "a1", [befund("missing_impressum")])

        res = CliRunner().invoke(cli, ["learnings", "--db", db])
        assert res.exit_code == 0, res.output
        assert "missing_impressum" in res.output
        assert "Analyzer-Bug" in res.output

    def test_learnings_kommando_ohne_daten(self, db):
        from click.testing import CliRunner

        from seo_autopilot.cli.main import cli

        res = CliRunner().invoke(cli, ["learnings", "--db", db])
        assert res.exit_code == 0, res.output
        assert "Keine wiederkehrenden Fehlalarme" in res.output
