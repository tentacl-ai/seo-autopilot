"""Kopf des Audits 'wo wir stehen' (Robert 18.09.2026)."""

import sqlite3

from seo_autopilot.kundenbericht import _abschnitt_stand, wo_wir_stehen


def _db(tmp_path):
    pfad = str(tmp_path / "a.db")
    con = sqlite3.connect(pfad)
    con.execute(
        "create table seo_audits (id text, project_id text, status text, score real,"
        " started_at text, total_pages int)"
    )
    con.execute(
        "create table change_log (id text, project_id text, urheber text, status text,"
        " zeitpunkt text)"
    )
    for i, score in enumerate([60.0, 72.0]):
        con.execute(
            "insert into seo_audits values (?,?,?,?,?,?)",
            (f"a{i}", "p", "completed", score, f"2026-09-1{i+6} 07:00:00", 40),
        )
    con.execute(
        "insert into change_log values ('c','p','autopilot','angewendet', datetime('now'))"
    )
    con.commit()
    con.close()
    return pfad


def test_note_verlauf_und_eigene_arbeit(tmp_path):
    st = wo_wir_stehen(_db(tmp_path), "p")
    assert st["note"] == 72.0
    assert st["note_vor_woche"] == 60.0
    assert st["selbst_erledigt"] == 1


def test_abschnitt_nennt_note_und_arbeit(tmp_path):
    st = wo_wir_stehen(_db(tmp_path), "p")
    html = "".join(_abschnitt_stand({"wo_wir_stehen": st}))
    assert "Wo wir stehen" in html
    assert "72" in html and "1 Änderung" in html


def test_ohne_lauf_kein_abschnitt():
    assert _abschnitt_stand({"wo_wir_stehen": {}}) == []


def test_zeitstempel_des_berichts_bleibt_unangetastet(tmp_path):
    """Der Schluessel 'stand' ist seit jeher der Zeitstempel - der neue Abschnitt
    darf ihn nicht ueberschreiben (sonst bricht als_html, 18.09.2026)."""
    from datetime import datetime

    from seo_autopilot.kundenbericht import als_html

    b = {
        "name": "X",
        "host": "x.de",
        "stand": datetime.now().isoformat(timespec="minutes"),
        "hinweise": [],
        "wo_wir_stehen": wo_wir_stehen(_db(tmp_path), "p"),
    }
    html = als_html(b)
    assert "Wo wir stehen" in html and "Wochenbericht" in html
