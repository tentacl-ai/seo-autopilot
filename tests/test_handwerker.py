"""
Tests für den Handwerker (v1.13): Der Autopilot repariert je Seite, nur mit
plausiblen Texten, und geratene Vorlagen laufen nie automatisch.

Jeder Test hier beschreibt einen Fehler, der vorher wirklich passiert wäre:
- Ein Titel für /projekte/ wäre in die Startseite geschrieben worden.
- „20+ Jahre Erfahrung" aus einer Vorlage wäre live gegangen.
- Alt-Texte hätten dekorative Bilder (alt="") überschrieben.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import seo_autopilot.ausfuehrung as ausfuehrung
from seo_autopilot import handwerker as hw
from seo_autopilot.adapters.static_files import StaticFilesAdapter
from seo_autopilot.agents.apply import ApplyAgent
from seo_autopilot.ausfuehrung import freigaben, tabelle_anlegen

SEITE = """<!doctype html><html lang="de"><head>
<title>Alt</title>
<meta name="description" content="Alte Beschreibung der Seite, die lang genug ist um zu zaehlen." />
<meta property="og:title" content="OG Alt" />
<meta property="og:image" content="https://x.de/img/og.jpg" />
</head><body><main><h1>Buchungssystem</h1>
<p>Der Gast waehlt seinen Platz auf dem Lageplan. Kurtaxe rechnet das System.</p>
<img src="/img/plan.png">
<img src="/img/deko.svg" alt="">
<img src="/img/mitalt.png" alt="schon da">
</main></body></html>"""


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(
        "<html><head><title>Home</title></head><body></body></html>", encoding="utf-8"
    )
    (tmp_path / "projekte").mkdir()
    (tmp_path / "projekte" / "index.html").write_text(SEITE, encoding="utf-8")
    (tmp_path / "img").mkdir()
    try:
        from PIL import Image

        Image.new("RGB", (640, 360), "white").save(tmp_path / "img" / "plan.png")
    except ImportError:  # pragma: no cover
        pass
    return tmp_path


# ---------------------------------------------------------------------------
# Datei-Zuordnung
# ---------------------------------------------------------------------------


class TestDateiZuordnung:
    def test_unterseite_trifft_ihre_datei(self, site):
        assert (
            hw.datei_fuer_seite(site, "https://x.de/projekte/")
            == site / "projekte" / "index.html"
        )

    def test_startseite(self, site):
        assert hw.datei_fuer_seite(site, "https://x.de/") == site / "index.html"

    def test_unbekannte_seite_ergibt_nichts_statt_startseite(self, site):
        assert hw.datei_fuer_seite(site, "https://x.de/gibt-es-nicht/") is None

    def test_pfad_ausbruch_wird_abgewiesen(self, site):
        assert hw.datei_fuer_seite(site, "https://x.de/../../etc/passwd") is None

    def test_fix_fuer_unterseite_aendert_nie_die_startseite(self, site):
        """Der Fehler, der zaehlte: jede Aenderung landete in index.html."""
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {
                "type": "missing_title",
                "seite": "https://x.de/projekte/",
                "suggestion": "Buchungssystem fuer Campingplaetze mit Lageplan",
            }
        )
        assert r.success and r.files_changed == ["projekte/index.html"]
        assert (
            "Buchungssystem fuer Campingplaetze"
            in (site / "projekte" / "index.html").read_text()
        )
        assert "<title>Home</title>" in (site / "index.html").read_text()

    def test_fix_fuer_unbekannte_seite_schreibt_nichts(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {
                "type": "missing_title",
                "seite": "https://x.de/nix/",
                "suggestion": "Egal was, das darf nirgends landen",
            }
        )
        assert not r.success
        assert "<title>Home</title>" in (site / "index.html").read_text()


# ---------------------------------------------------------------------------
# Plausibilitaet
# ---------------------------------------------------------------------------


class TestPlausibilitaet:
    def test_erfundene_erfahrung_faellt_durch(self):
        ok, grund = hw.plausibel(
            "missing_meta_description",
            "Alles ueber tentacl. Kostenlose Erstberatung, 20+ Jahre Erfahrung und mehr.",
        )
        assert not ok and "Behauptung" in grund

    def test_verbotenes_wort_faellt_durch(self):
        ok, grund = hw.plausibel(
            "missing_title",
            "Kostenlos: Broetchen-Service fuer Campingplaetze",
            {"verbotene_woerter": ["kostenlos"]},
        )
        assert not ok and "kostenlos" in grund

    def test_zu_langer_titel_faellt_durch(self):
        ok, _ = hw.plausibel("missing_title", "x" * 80)
        assert not ok

    def test_guter_titel_geht_durch(self):
        ok, _ = hw.plausibel(
            "missing_title", "Broetchen-Service fuer Campingplaetze per Handy"
        )
        assert ok

    def test_antwort_artefakte_werden_gesaeubert(self):
        assert (
            hw.saeubern('TITLE: "Broetchen-Service fuer Campingplaetze"')
            == "Broetchen-Service fuer Campingplaetze"
        )

    def test_html_faellt_durch(self):
        ok, _ = hw.plausibel("missing_og_title", "Titel <b>fett</b> und laenger")
        assert not ok


# ---------------------------------------------------------------------------
# Neue Reparaturen im Adapter
# ---------------------------------------------------------------------------


class TestReparaturen:
    def test_alt_text_nur_an_bilder_ohne_alt(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {
                "type": "images_without_alt",
                "seite": "https://x.de/projekte/",
                "bilder": {
                    "/img/plan.png": "Lageplan mit freien Plaetzen",
                    "/img/deko.svg": "NIE",
                    "/img/mitalt.png": "NIE",
                },
            }
        )
        html = (site / "projekte" / "index.html").read_text()
        assert r.success
        assert '<img alt="Lageplan mit freien Plaetzen" src="/img/plan.png">' in html
        assert '<img src="/img/deko.svg" alt="">' in html  # dekorativ bleibt
        assert 'alt="schon da"' in html and "NIE" not in html

    def test_twitter_card_ergaenzt_aus_og(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {"type": "missing_twitter_card", "seite": "https://x.de/projekte/"}
        )
        html = (site / "projekte" / "index.html").read_text()
        assert r.success
        assert 'name="twitter:card" content="summary_large_image"' in html
        assert 'name="twitter:title" content="OG Alt"' in html
        assert 'name="twitter:image" content="https://x.de/img/og.jpg"' in html
        # zweiter Lauf: nichts mehr zu tun, kein Doppel
        r2 = a.apply_fix(
            {"type": "missing_twitter_card", "seite": "https://x.de/projekte/"}
        )
        assert r2.commit_hash == "already-applied"
        assert html.count("twitter:card") == 1

    def test_bildmasse_aus_lokaler_datei(self, site):
        pytest.importorskip("PIL")
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {"type": "image_missing_dimensions", "seite": "https://x.de/projekte/"}
        )
        html = (site / "projekte" / "index.html").read_text()
        assert r.success
        assert '<img width="640" height="360" src="/img/plan.png">' in html
        # nur das gemessene Bild (lief bis 18.09.2026 nie, weil Pillow fehlte —
        # die alte Pruefung schaute versehentlich auf den Text VOR plan.png,
        # in dem das reparierte Tag selbst steht)
        assert '<img src="/img/deko.svg" alt="">' in html
        assert '<img src="/img/mitalt.png" alt="schon da">' in html

    def test_jsonld_webpage_mit_daten(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix(
            {
                "type": "no_jsonld",
                "seite": "https://x.de/projekte/",
                "publisher": "X",
                "domain": "https://x.de",
            }
        )
        html = (site / "projekte" / "index.html").read_text()
        assert r.success
        assert (
            '"@type": "WebPage"' in html
            and '"dateModified"' in html
            and '"name": "OG Alt"' in html
        )
        # zweiter Lauf: kein zweiter Block
        a.apply_fix({"type": "no_jsonld", "seite": "https://x.de/projekte/"})
        assert (site / "projekte" / "index.html").read_text().count(
            "application/ld+json"
        ) == 1


# ---------------------------------------------------------------------------
# Ausfuehrungssteuerung
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    pfad = str(tmp_path / "t.db")
    tabelle_anlegen(pfad)
    monkeypatch.setattr(ausfuehrung, "standard_db_pfad", lambda: pfad)
    return pfad


def _lauf(site, fixes):
    projekt = SimpleNamespace(
        id="test",
        betriebsart="autopilot",
        auto_fix_enabled=False,
        auto_fix_config={},
        adapter_type="static",
        adapter_config={"root_path": str(site)},
    )
    ctx = SimpleNamespace(
        agent_results={"content": SimpleNamespace(fixes=fixes)}, force_apply=False
    )
    agent = ApplyAgent(
        project_id="test", audit_id="a1", project_config=projekt, context=ctx
    )
    return asyncio.run(agent.run())


class TestAutopilotGrenzen:
    def test_vorlage_laeuft_auch_im_autopilot_nie(self, site, db):
        fix = {
            "type": "missing_title",
            "source": "template",
            "seite": "https://x.de/projekte/",
            "suggestion": "Geraten | X",
            "priority": "high",
        }
        r = _lauf(site, [fix])
        assert r.metrics.get("fixes_applied", 0) == 0
        assert "<title>Alt</title>" in (site / "projekte" / "index.html").read_text()
        offen = freigaben(db)
        assert len(offen) == 1 and "Vorlagen" in offen[0].begruendung

    def test_ki_vorschlag_laeuft_im_autopilot(self, site, db):
        fix = {
            "type": "missing_title",
            "source": "claude",
            "seite": "https://x.de/projekte/",
            "suggestion": "Buchungssystem fuer Campingplaetze",
            "priority": "high",
        }
        r = _lauf(site, [fix])
        assert r.metrics["fixes_applied"] == 1
        assert (
            "Buchungssystem fuer Campingplaetze"
            in (site / "projekte" / "index.html").read_text()
        )

    def test_sicherer_low_befund_wird_ausgefuehrt(self, site, db):
        fix = {
            "type": "missing_twitter_card",
            "source": "regel",
            "seite": "https://x.de/projekte/",
            "priority": "low",
        }
        r = _lauf(site, [fix])
        assert r.metrics["fixes_applied"] == 1

    def test_unsicherer_low_befund_bleibt_liegen(self, site, db):
        fix = {
            "type": "missing_canonical",
            "source": "regel",
            "seite": "https://x.de/projekte/",
            "url": "https://x.de/projekte/",
            "priority": "low",
        }
        r = _lauf(site, [fix])
        assert r.metrics.get("fixes_applied", 0) == 0


class TestGitWurzelOberhalb:
    """root = repo/frontend/dist: Der Commit muss trotzdem passieren."""

    def test_commit_landet_im_uebergeordneten_repo(self, tmp_path):
        import subprocess

        repo = tmp_path / "repo"
        dist = repo / "frontend" / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text(
            "<html><head><title>Home</title></head></html>", encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "add",
                ".",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "-m",
                "init",
            ],
            check=True,
        )
        a = StaticFilesAdapter({"root_path": str(dist)})
        assert a._has_git
        r = a.apply_fix(
            {
                "type": "missing_title",
                "seite": "https://x.de/",
                "suggestion": "Buchungssystem fuer Campingplaetze mit Lageplan",
            }
        )
        assert r.success and r.commit_hash not in ("no-git", None, "not-tracked")
        log = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True
        ).stdout
        assert "init" in log and len(log.strip().splitlines()) == 2

    def test_ignorierte_datei_wird_geaendert_aber_nicht_committet(self, tmp_path):
        import subprocess

        repo = tmp_path / "repo"
        (repo / "x").mkdir(parents=True)
        (repo / ".gitignore").write_text("x/\n", encoding="utf-8")
        (repo / "x" / "index.html").write_text(
            "<html><head><title>A</title></head></html>", encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "add",
                ".",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "-m",
                "init",
            ],
            check=True,
        )
        a = StaticFilesAdapter({"root_path": str(repo)})
        r = a.apply_fix(
            {
                "type": "missing_title",
                "seite": "https://x.de/x/",
                "suggestion": "Buchungssystem fuer Campingplaetze mit Lageplan",
            }
        )
        assert r.success and r.commit_hash == "not-tracked"
        assert "Buchungssystem" in (repo / "x" / "index.html").read_text()


class TestKuerzen:
    def test_kein_haengender_trenner(self):
        from seo_autopilot.adapters.static_files import kuerzen

        assert kuerzen(
            "Gebaeudemanagement – Monitoring, Predictive Maintenance & Protokolle | tentacl",
            70,
        ).endswith("Protokolle")
        assert kuerzen("kurz", 70) == "kurz"

    def test_jsonld_ohne_git_hat_kein_erfundenes_datepublished(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        a.apply_fix({"type": "no_jsonld", "seite": "https://x.de/projekte/"})
        html = (site / "projekte" / "index.html").read_text()
        assert '"dateModified"' in html and '"datePublished"' not in html
