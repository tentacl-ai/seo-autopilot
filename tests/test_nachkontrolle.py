"""Nachkontrolle: ein Auto-Fix darf nichts Wichtiges mitreissen (Regeln nach claude-seo drift)."""

import subprocess

import pytest

from seo_autopilot.adapters.static_files import StaticFilesAdapter
from seo_autopilot.nachkontrolle import pruefe

SEITE = """<!doctype html><html lang="de"><head>
<title>Campingplatz-Software für Betreiber</title>
<meta name="description" content="Buchung, Kasse und Gäste in einem System.">
<link rel="canonical" href="https://x.de/software/">
<link rel="alternate" hreflang="en" href="https://x.de/en/software/">
<meta property="og:title" content="Campingplatz-Software">
<script type="application/ld+json">{"@type":"Organization","name":"X"}</script>
</head><body><main><h1>Campingplatz-Software</h1>
<p>Buchung, Kasse und Gäste in einem System. Für kleine und mittlere Plätze.</p>
</main></body></html>"""


class TestRegeln:
    def test_unveraendert_ist_ok(self):
        assert pruefe(SEITE, SEITE).ok

    def test_hinzufuegen_ist_ok(self):
        neu = SEITE.replace("</main>", "<section><h2>Fragen</h2><p>Antwort.</p></section></main>")
        assert pruefe(SEITE, neu).ok

    def test_titeltext_aendern_ist_ok(self):
        assert pruefe(SEITE, SEITE.replace("für Betreiber", "– jetzt testen")).ok

    @pytest.mark.parametrize(
        "kaputt, erwartet",
        [
            (SEITE.replace("<title>Campingplatz-Software für Betreiber</title>", ""), "Seitentitel"),
            (SEITE.replace("<h1>Campingplatz-Software</h1>", ""), "H1"),
            (SEITE.replace('<meta name="description" content="Buchung, Kasse und Gäste in einem System.">', ""), "meta description"),
            (SEITE.replace('<link rel="canonical" href="https://x.de/software/">', ""), "Canonical"),
            (SEITE.replace("https://x.de/software/\"", "https://x.de/\""), "Canonical würde sich ändern"),
            (SEITE.replace("</head>", '<meta name="robots" content="noindex"></head>'), "noindex"),
            (SEITE.replace('<script type="application/ld+json">{"@type":"Organization","name":"X"}</script>', ""), "JSON-LD"),
            (SEITE.replace('<meta property="og:title" content="Campingplatz-Software">', ""), "og:"),
            (SEITE.replace('<link rel="alternate" hreflang="en" href="https://x.de/en/software/">', ""), "hreflang"),
            (SEITE.replace("Buchung, Kasse und Gäste in einem System. Für kleine und mittlere Plätze.", ""), "Text"),
        ],
    )
    def test_verlust_wird_erkannt(self, kaputt, erwartet):
        e = pruefe(SEITE, kaputt)
        assert not e.ok
        assert any(erwartet in v for v in e.verluste), e.verluste

    def test_canonical_fix_darf_canonical_aendern(self):
        neu = SEITE.replace("https://x.de/software/\"", "https://x.de/software\"")
        assert pruefe(SEITE, neu, "missing_canonical").ok


def _git(site, *args):
    subprocess.run(["git", "-C", str(site), *args], check=True, capture_output=True)


@pytest.fixture
def site(tmp_path):
    site = tmp_path / "site"
    (site / "software").mkdir(parents=True)
    (site / "software" / "index.html").write_text(SEITE, encoding="utf-8")
    _git(site, "init", "-q")
    _git(site, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(site, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "start")
    return site


class TestAdapterRolltZurueck:
    def test_fix_der_titel_zerstoert_wird_nicht_committet(self, site, monkeypatch):
        a = StaticFilesAdapter({"root_path": str(site)})

        def kaputter_fix(suggestion, dateien):
            for p in dateien:
                p.write_text(p.read_text(encoding="utf-8").replace(
                    "<title>Campingplatz-Software für Betreiber</title>", ""), encoding="utf-8")
            return [str(p.relative_to(a.root)) for p in dateien]

        monkeypatch.setattr(a, "apply_meta_description", kaputter_fix)
        r = a.apply_fix({"type": "missing_meta_description", "suggestion": "x",
                         "seite": "https://x.de/software/"})
        assert not r.success
        assert r.nicht_behebbar and "Seitentitel" in r.nicht_behebbar
        assert (site / "software" / "index.html").read_text(encoding="utf-8") == SEITE
        anzahl = subprocess.run(["git", "-C", str(site), "rev-list", "--count", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        assert anzahl == "1"  # kein Commit

    def test_sauberer_fix_geht_durch(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        r = a.apply_fix({"type": "missing_meta_description",
                         "suggestion": "Neue Beschreibung für die Trefferliste.",
                         "seite": "https://x.de/software/"})
        assert r.nicht_behebbar is None
        assert r.success
