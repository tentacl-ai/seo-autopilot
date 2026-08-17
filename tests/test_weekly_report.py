"""Tests für den Wochenbericht.

Der Bericht geht an den Auftraggeber, nicht an einen Entwickler. Deshalb
prüfen wir hier nicht nur, dass Zahlen herauskommen, sondern auch, dass sie
richtig gedeutet werden (besser/schlechter), dass ein Projekt ohne Daten
sauber als "keine Daten" auftaucht statt als Absturz oder als Null-Note,
und dass 23 gleichartige Befunde EIN Punkt sind und nicht 23 Zeilen.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from seo_autopilot.weekly_report import (
    SCHWELLE_VERAENDERUNG,
    als_html,
    als_text,
    baue_wochenbericht,
    schreibe_html,
)

JETZT = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)

SCHEMA = """
create table seo_audits (
    id text, project_id text, tenant_id text, started_at text, completed_at text,
    status text, total_pages int, total_keywords int, issues_found int, score real,
    gsc_clicks int, gsc_impressions int, gsc_ctr real, gsc_avg_position real
);
create table seo_issues (
    id text, project_id text, audit_id text, category text, type text,
    severity text, title text, description text, count int,
    fix_suggestion text, estimated_impact text, detected_at text
);
create table seo_keywords (
    id text, project_id text, audit_id text, query text, clicks int,
    impressions int, ctr real, position real, best_page text
);
"""


@pytest.fixture
def umgebung(tmp_path):
    """Leere, aber vollständige Installation: DB mit Tabellen + Projektliste."""
    db = tmp_path / "test.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.commit()
    con.close()

    projects = tmp_path / "projects.yaml"

    def schreibe_projekte(daten):
        projects.write_text(yaml.safe_dump({"projects": daten}), encoding="utf-8")

    def audit(
        audit_id,
        project_id,
        tage_her=0,
        score=50.0,
        pages=15,
        klicks=None,
        impressionen=None,
        ctr=None,
        position=None,
    ):
        con = sqlite3.connect(db)
        con.execute(
            "insert into seo_audits (id, project_id, started_at, status, "
            "total_pages, issues_found, score, gsc_clicks, gsc_impressions, "
            "gsc_ctr, gsc_avg_position) values (?,?,?,?,?,?,?,?,?,?,?)",
            (
                audit_id,
                project_id,
                (JETZT - timedelta(days=tage_her)).isoformat(),
                "completed",
                pages,
                0,
                score,
                klicks,
                impressionen,
                ctr,
                position,
            ),
        )
        con.commit()
        con.close()
        return audit_id

    def befund(audit_id, typ, severity="high", titel=None, anzahl=1, wie_oft=1):
        con = sqlite3.connect(db)
        for i in range(wie_oft):
            con.execute(
                "insert into seo_issues (id, audit_id, category, type, severity, "
                "title, count) values (?,?,?,?,?,?,?)",
                (
                    f"{audit_id}-{typ}-{severity}-{i}",
                    audit_id,
                    "meta",
                    typ,
                    severity,
                    titel or typ,
                    anzahl,
                ),
            )
        con.commit()
        con.close()

    # Standard: ein aktives Projekt
    schreibe_projekte(
        {
            "demo": {
                "name": "Demo GmbH",
                "domain": "https://www.demo.de",
                "enabled": True,
            }
        }
    )

    return {
        "db": str(db),
        "projects": str(projects),
        "schreibe_projekte": schreibe_projekte,
        "audit": audit,
        "befund": befund,
        "tmp": tmp_path,
    }


def _bericht(umgebung, tage=7):
    return baue_wochenbericht(
        db_pfad=umgebung["db"],
        projects_pfad=umgebung["projects"],
        tage=tage,
        jetzt=JETZT,
    )


# ---------------------------------------------------------------------------
# Entwicklung: besser / schlechter / unverändert
# ---------------------------------------------------------------------------


def test_score_verbesserung_wird_als_verbessert_erkannt(umgebung):
    umgebung["audit"]("alt", "demo", tage_her=6, score=46.0)
    umgebung["audit"]("neu", "demo", tage_her=0, score=72.9)

    bericht = _bericht(umgebung)
    stand = bericht.projekte[0]

    assert stand.hat_daten is True
    assert stand.score == 72.9
    assert stand.score_vorher == 46.0
    assert stand.veraenderung == pytest.approx(26.9)
    assert stand.trend == "verbessert"
    assert stand.pfeil == "▲"
    assert bericht.verbessert == [stand]
    assert "verbessert" in als_text(bericht)


def test_score_verschlechterung_wird_als_verschlechtert_erkannt(umgebung):
    umgebung["audit"]("alt", "demo", tage_her=6, score=80.0)
    umgebung["audit"]("neu", "demo", tage_her=0, score=52.3)

    bericht = _bericht(umgebung)
    stand = bericht.projekte[0]

    assert stand.trend == "verschlechtert"
    assert stand.veraenderung == pytest.approx(-27.7)
    assert stand.pfeil == "▼"
    assert bericht.verschlechtert == [stand]
    text = als_text(bericht)
    assert "verschlechtert" in text
    # Das Minuszeichen darf nicht als "+" durchrutschen
    assert "+27,7" not in text


def test_kleine_schwankung_gilt_als_unveraendert(umgebung):
    umgebung["audit"]("alt", "demo", tage_her=5, score=50.0)
    umgebung["audit"]("neu", "demo", tage_her=0, score=50.0 + SCHWELLE_VERAENDERUNG / 2)

    stand = _bericht(umgebung).projekte[0]
    assert stand.trend == "unveraendert"
    assert stand.pfeil == "→"


def test_vergleich_greift_auf_lauf_vor_dem_fenster_zurueck(umgebung):
    """Nur ein Lauf im Fenster: dann ist der Lauf davor der Vergleichspunkt."""
    umgebung["audit"]("vorwoche", "demo", tage_her=9, score=40.0)
    umgebung["audit"]("heute", "demo", tage_her=0, score=60.0)

    stand = _bericht(umgebung, tage=7).projekte[0]
    assert stand.laeufe == 1
    assert stand.score_vorher == 40.0
    assert stand.trend == "verbessert"


# ---------------------------------------------------------------------------
# Projekte ohne Daten
# ---------------------------------------------------------------------------


def test_projekt_ohne_lauf_im_fenster_wird_als_keine_daten_ausgewiesen(umgebung):
    umgebung["audit"]("uralt", "demo", tage_her=30, score=60.0)

    bericht = _bericht(umgebung, tage=7)
    stand = bericht.projekte[0]

    assert stand.hat_daten is False
    assert stand.score is None
    assert "Kein Lauf im Zeitraum" in stand.hinweis
    assert bericht.ohne_daten == [stand]
    text = als_text(bericht)
    assert "Keine Daten" in text
    assert "1 ohne Daten" in text


def test_projekt_ohne_jeden_lauf_meldet_noch_nie_geprueft(umgebung):
    bericht = _bericht(umgebung)
    stand = bericht.projekte[0]

    assert stand.hat_daten is False
    assert stand.hinweis == "Noch nie geprüft"
    assert "Noch nie geprüft" in als_text(bericht)


def test_leere_datenbank_ergibt_gueltigen_bericht_statt_ausnahme(umgebung):
    umgebung["schreibe_projekte"](
        {
            "a": {"name": "A", "domain": "https://a.de"},
            "b": {"name": "B", "domain": "https://b.de"},
        }
    )
    bericht = _bericht(umgebung)

    assert len(bericht.projekte) == 2
    assert bericht.mit_daten == []
    assert bericht.durchschnittsnote is None
    assert bericht.klicks_gesamt == 0
    text = als_text(bericht)
    assert "SEO-Wochenbericht" in text
    assert "2 ohne Daten" in text
    assert als_html(bericht).startswith("<!DOCTYPE html>")


def test_fehlende_datenbankdatei_stuerzt_nicht_ab(umgebung, tmp_path):
    bericht = baue_wochenbericht(
        db_pfad=str(tmp_path / "gibt-es-nicht.db"),
        projects_pfad=umgebung["projects"],
        tage=7,
        jetzt=JETZT,
    )
    assert len(bericht.projekte) == 1
    assert bericht.projekte[0].hat_daten is False
    assert "Datenbank" in bericht.projekte[0].hinweis
    assert als_text(bericht)


def test_deaktivierte_projekte_tauchen_nicht_auf(umgebung):
    umgebung["schreibe_projekte"](
        {
            "aktiv": {"name": "Aktiv", "domain": "https://aktiv.de", "enabled": True},
            "aus": {"name": "Aus", "domain": "https://aus.de", "enabled": False},
        }
    )
    bericht = _bericht(umgebung)
    assert [p.schluessel for p in bericht.projekte] == ["aktiv"]


def test_ohne_projektliste_kommt_ein_leerer_aber_gueltiger_bericht(tmp_path):
    bericht = baue_wochenbericht(
        db_pfad=str(tmp_path / "keine.db"),
        projects_pfad=str(tmp_path / "keine.yaml"),
        jetzt=JETZT,
    )
    assert bericht.projekte == []
    assert "Keine aktiven Projekte" in als_text(bericht)
    assert "Keine aktiven Projekte" in als_html(bericht)


# ---------------------------------------------------------------------------
# Zusammenfassen statt aufzählen
# ---------------------------------------------------------------------------


def test_mehrfache_befundtypen_werden_zusammengefasst(umgebung):
    a = umgebung["audit"]("heute", "demo", tage_her=0, score=30.0)
    umgebung["befund"](a, "unreachable_page", "high", wie_oft=23)
    umgebung["befund"](a, "thin_content", "medium", wie_oft=4)

    stand = _bericht(umgebung).projekte[0]

    # 27 Einzelbefunde, aber nur zwei Punkte im Bericht
    assert len(stand.top_punkte) == 2
    punkt = stand.top_punkte[0]
    assert punkt.typ == "unreachable_page"
    assert punkt.anzahl == 23
    assert punkt.anzahl_text == "23×"
    assert stand.schwere_befunde == 23
    assert stand.mittlere_befunde == 4

    text = als_text(bericht=_bericht(umgebung))
    assert text.count("nicht erreichbar") == 1


def test_hoechstens_drei_punkte_schwer_vor_mittel(umgebung):
    a = umgebung["audit"]("heute", "demo", tage_her=0, score=30.0)
    umgebung["befund"](a, "thin_content", "medium", wie_oft=50)
    umgebung["befund"](a, "geo_freshness_signals", "medium", wie_oft=40)
    umgebung["befund"](a, "missing_h1", "medium", wie_oft=30)
    umgebung["befund"](a, "missing_meta_description", "high", wie_oft=2)
    umgebung["befund"](a, "geo_ai_crawler_blocked", "critical", wie_oft=1)

    stand = _bericht(umgebung).projekte[0]

    assert len(stand.top_punkte) == 3
    assert [p.schwere for p in stand.top_punkte] == ["schwer", "schwer", "mittel"]
    # Schwer schlägt Menge: der 50er-Mittelbefund steht hinter den schweren
    assert {p.typ for p in stand.top_punkte[:2]} == {
        "missing_meta_description",
        "geo_ai_crawler_blocked",
    }
    assert stand.top_punkte[2].typ == "thin_content"


def test_leichte_befunde_verstopfen_den_bericht_nicht(umgebung):
    a = umgebung["audit"]("heute", "demo", tage_her=0, score=90.0)
    umgebung["befund"](a, "schema_rich_result_opportunity", "info", wie_oft=99)
    umgebung["befund"](a, "irgendwas_kleines", "low", wie_oft=99)

    stand = _bericht(umgebung).projekte[0]
    assert stand.top_punkte == []
    assert stand.schwere_befunde == 0
    assert stand.mittlere_befunde == 0
    assert "Keine schweren oder mittleren Punkte offen" in als_text(_bericht(umgebung))


def test_unbekannter_befundtyp_faellt_auf_den_titel_zurueck(umgebung):
    a = umgebung["audit"]("heute", "demo", tage_her=0, score=30.0)
    umgebung["befund"](
        a,
        "brandneuer_check",
        "high",
        titel="Etwas Neues: https://demo.de/seite",
    )

    stand = _bericht(umgebung).projekte[0]
    assert stand.top_punkte[0].titel == "Etwas Neues"
    assert stand.top_punkte[0].empfehlung


def test_befunde_mit_count_groesser_eins_werden_addiert(umgebung):
    a = umgebung["audit"]("heute", "demo", tage_her=0, score=30.0)
    umgebung["befund"](a, "orphan_page", "medium", anzahl=5, wie_oft=3)

    stand = _bericht(umgebung).projekte[0]
    assert stand.top_punkte[0].anzahl == 15
    assert stand.mittlere_befunde == 15


# ---------------------------------------------------------------------------
# Search-Console-Zahlen
# ---------------------------------------------------------------------------


def test_suchzahlen_werden_uebernommen_und_deutsch_formatiert(umgebung):
    umgebung["audit"](
        "heute",
        "demo",
        tage_her=0,
        score=45.7,
        klicks=10,
        impressionen=359,
        ctr=2.79,
        position=37.13,
    )
    bericht = _bericht(umgebung)
    stand = bericht.projekte[0]

    assert stand.klicks == 10
    assert stand.position == pytest.approx(37.13)
    assert bericht.klicks_gesamt == 10

    text = als_text(bericht)
    assert "10 Klicks über Google" in text
    assert "Klickrate 2,8 %" in text  # Komma statt Punkt, kein "CTR"
    assert "Platz 37,1" in text
    assert "CTR" not in text


def test_ohne_suchzahlen_wird_das_offen_gesagt(umgebung):
    umgebung["audit"]("heute", "demo", tage_her=0, score=46.7)
    stand = _bericht(umgebung).projekte[0]

    assert stand.klicks is None
    assert "Search Console nicht verbunden" in als_text(_bericht(umgebung))


def test_suchzahlen_fallen_auf_letzten_lauf_mit_werten_zurueck(umgebung):
    """Nicht jeder Lauf bekommt Google-Zahlen — dann gilt die letzte bekannte."""
    umgebung["audit"](
        "frueher", "demo", tage_her=3, score=50.0, klicks=7, position=12.0
    )
    umgebung["audit"]("heute", "demo", tage_her=0, score=55.0)

    stand = _bericht(umgebung).projekte[0]
    assert stand.klicks == 7
    assert stand.position == 12.0


# ---------------------------------------------------------------------------
# Textausgabe
# ---------------------------------------------------------------------------


def test_text_beginnt_mit_gesamtzeile_ueber_alle_projekte(umgebung):
    umgebung["schreibe_projekte"](
        {
            "gut": {"name": "Gut", "domain": "https://gut.de"},
            "schlecht": {"name": "Schlecht", "domain": "https://schlecht.de"},
        }
    )
    umgebung["audit"]("g1", "gut", tage_her=6, score=60.0)
    umgebung["audit"]("g2", "gut", tage_her=0, score=80.0)
    umgebung["audit"]("s1", "schlecht", tage_her=6, score=50.0)
    umgebung["audit"]("s2", "schlecht", tage_her=0, score=20.0)

    bericht = _bericht(umgebung)
    zeilen = als_text(bericht).splitlines()

    assert zeilen[0].startswith("SEO-Wochenbericht")
    gesamt = zeilen[2]
    assert gesamt.startswith("Gesamt:")
    assert "2 Projekte" in gesamt
    assert "Durchschnittsnote 50 von 100" in gesamt
    assert "1 besser" in gesamt
    assert "1 schlechter" in gesamt
    # Sorgenkind zuerst
    assert [p.schluessel for p in bericht.projekte] == ["schlecht", "gut"]


def test_text_vermeidet_doppelte_domain_und_falschen_plural(umgebung):
    umgebung["schreibe_projekte"](
        {
            "b": {
                "name": "BiancaAI (lovebianca.ai)",
                "domain": "https://www.lovebianca.ai",
            }
        }
    )
    umgebung["audit"]("a1", "b", tage_her=5, score=50.0)
    umgebung["audit"]("a2", "b", tage_her=0, score=50.0)

    text = als_text(_bericht(umgebung))

    # Domain steckt schon im Namen -> nicht zweimal
    assert text.count("lovebianca.ai") == 1
    assert "in 2 Durchläufen" in text
    assert "Durchlaufen" not in text
    # Bei gleichem Wert keine sinnlose "+0 Punkte"-Angabe
    assert "unverändert gegenüber der Vorwoche" in text
    assert "+0" not in text


def test_kompakter_text_laesst_empfehlungen_weg(umgebung):
    """Für Telegram: die Punkte bleiben, die langen Empfehlungen fallen weg."""
    a = umgebung["audit"]("heute", "demo", tage_her=0, score=30.0)
    umgebung["befund"](a, "unreachable_page", "high", wie_oft=3)

    bericht = _bericht(umgebung)
    lang = als_text(bericht)
    kurz = als_text(bericht, kompakt=True)

    assert "nicht erreichbar" in kurz  # der Punkt selbst bleibt
    assert "Diese Seiten aus dem Menü" in lang
    assert "Diese Seiten aus dem Menü" not in kurz
    assert len(kurz) < len(lang)


def test_text_nennt_keine_englischen_fachbegriffe_der_rohdaten(umgebung):
    a = umgebung["audit"]("heute", "demo", tage_her=0, score=8.9)
    umgebung["befund"](
        a, "geo_answer_first", "high", titel="GEO: First 150 words contain no answer"
    )
    text = als_text(_bericht(umgebung))

    assert "First 150 words" not in text
    assert "ersten Sätze" in text
    assert "Note: 9 von 100" in text


# ---------------------------------------------------------------------------
# HTML-Ausgabe
# ---------------------------------------------------------------------------


def test_html_enthaelt_keine_externen_verweise(umgebung):
    umgebung["audit"](
        "heute", "demo", tage_her=0, score=45.0, klicks=3, impressionen=322
    )
    umgebung["befund"]("heute", "unreachable_page", "high", wie_oft=5)

    seite = als_html(_bericht(umgebung))

    assert "<script" not in seite.lower()
    assert "http://" not in seite
    assert "https://" not in seite  # auch die Projektdomain steht ohne Vorsatz drin
    assert "<link" not in seite.lower()
    assert "@import" not in seite
    assert "cdn" not in seite.lower()
    assert "url(" not in seite.lower()  # keine nachgeladenen Hintergrundbilder


def test_html_ist_hell_und_eigenstaendig(umgebung):
    umgebung["audit"]("heute", "demo", tage_her=6, score=40.0)
    umgebung["audit"]("heute2", "demo", tage_her=0, score=70.0)
    seite = als_html(_bericht(umgebung))

    assert seite.startswith("<!DOCTYPE html>")
    assert seite.rstrip().endswith("</html>")
    assert "#faf9f7" in seite  # heller Hintergrund
    assert "#1a1a1a" in seite
    assert "#e8540a" in seite  # Akzentfarbe
    assert 'name="viewport"' in seite  # mobilfreundlich
    assert "@media (max-width: 520px)" in seite
    assert "Demo GmbH" in seite
    assert "demo.de" in seite
    assert "verbessert" in seite


def test_html_maskiert_gefaehrliche_zeichen_aus_der_konfiguration(umgebung):
    umgebung["schreibe_projekte"](
        {"boese": {"name": "<script>alert(1)</script>", "domain": "https://x.de"}}
    )
    seite = als_html(_bericht(umgebung))

    assert "<script>alert(1)</script>" not in seite
    assert "&lt;script&gt;" in seite


def test_schreibe_html_legt_die_datei_an(umgebung, tmp_path):
    umgebung["audit"]("heute", "demo", tage_her=0, score=50.0)
    ziel = tmp_path / "unterordner" / "woche.html"

    pfad = schreibe_html(_bericht(umgebung), str(ziel))

    assert pfad.exists()
    inhalt = pfad.read_text(encoding="utf-8")
    assert "SEO-Wochenbericht" in inhalt
    assert "Demo GmbH" in inhalt
