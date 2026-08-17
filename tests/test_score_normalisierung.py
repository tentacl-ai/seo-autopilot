"""Tests für die Normierung des Scores auf die Seitenzahl.

Anlass (2026-08-17): Als die Crawl-Limits an die echte Seitenzahl angepasst
wurden, fiel tentacl.ai von 8,9 auf 3,2 und lovebianca von 45,7 auf 14,0 —
ohne dass sich an den Websites irgendetwas geändert hatte. Die Note bestrafte
gründlicheres Prüfen. Diese Tests halten die Korrektur fest.
"""

from types import SimpleNamespace

import pytest

from seo_autopilot.core.audit_context import AuditContext


def _kontext(issues, seiten=15):
    ctx = AuditContext(
        audit_id="a1",
        project_id="p1",
        project_config=SimpleNamespace(name="Test", domain="https://example.com"),
    )
    ctx.all_issues = list(issues)
    ctx.agent_results["analyzer"] = SimpleNamespace(metrics={"pages_crawled": seiten})
    return ctx


def _befunde(high=0, medium=0, low=0):
    return (
        [{"severity": "high"} for _ in range(high)]
        + [{"severity": "medium"} for _ in range(medium)]
        + [{"severity": "low"} for _ in range(low)]
    )


class TestNormierung:
    def test_referenzgroesse_verhaelt_sich_wie_frueher(self):
        """Bei genau 15 Seiten muss die alte Rechnung herauskommen."""
        ctx = _kontext(_befunde(high=5, medium=10, low=10), seiten=15)
        # alt: 100 - (3*5) - (1*10) - (0.3*10) = 72.0
        assert ctx.calculate_score() == 72.0

    def test_gruendlicherer_crawl_bestraft_nicht_mehr(self):
        """Doppelt so viele Seiten UND doppelt so viele Befunde = gleiche Note.

        Das ist der Kern: gleiche Befunddichte, gleiche Bewertung.
        """
        klein = _kontext(_befunde(high=4, medium=8, low=10), seiten=15)
        gross = _kontext(_befunde(high=8, medium=16, low=20), seiten=30)
        assert klein.calculate_score() == gross.calculate_score()

    def test_echte_verschlechterung_wird_weiter_erkannt(self):
        """Mehr Befunde bei GLEICHER Seitenzahl muss die Note senken."""
        vorher = _kontext(_befunde(high=2, medium=5), seiten=20)
        nachher = _kontext(_befunde(high=8, medium=15), seiten=20)
        assert nachher.calculate_score() < vorher.calculate_score()

    def test_kleine_website_mit_wenig_befunden_bleibt_gut(self):
        ctx = _kontext(_befunde(high=0, medium=2, low=1), seiten=4)
        assert ctx.calculate_score() > 80

    def test_kleine_website_mit_vielen_befunden_wird_schlecht(self):
        """Vier Seiten mit 10 schweren Befunden sind wirklich schlecht."""
        ctx = _kontext(_befunde(high=10, medium=20), seiten=4)
        assert ctx.calculate_score() < 30


class TestRobustheit:
    def test_ohne_analyzer_ergebnis_wird_nicht_normiert(self):
        """Unbekannte Seitenzahl -> alte Rechnung, keine Verzerrung."""
        ctx = AuditContext(
            audit_id="a1",
            project_id="p1",
            project_config=SimpleNamespace(name="T", domain="https://e.com"),
        )
        ctx.all_issues = _befunde(high=2, medium=3, low=3)
        # exakt die ursprüngliche Formel: 100 - 6 - 3 - 0.9
        assert ctx.calculate_score() == 90.1
        assert ctx.crawled_pages() is None

    def test_null_seiten_faellt_auf_alte_rechnung_zurueck(self):
        ctx = _kontext(_befunde(high=2, medium=3, low=3), seiten=0)
        assert ctx.calculate_score() == 90.1

    def test_keine_befunde_ergibt_hundert(self):
        ctx = _kontext([], seiten=25)
        assert ctx.calculate_score() == 100.0

    def test_score_bleibt_in_der_skala(self):
        ctx = _kontext(_befunde(high=500, medium=500, low=500), seiten=2)
        wert = ctx.calculate_score()
        assert 0.0 <= wert <= 100.0

    def test_deckelung_greift_weiterhin(self):
        """Auch normiert dürfen die Abzüge 50/30/20 nicht überschreiten."""
        ctx = _kontext(_befunde(high=100, medium=100, low=100), seiten=15)
        assert ctx.calculate_score() == 0.0

    @pytest.mark.parametrize("seiten", [1, 4, 15, 23, 40, 100])
    def test_verschiedene_groessen_liefern_gueltige_werte(self, seiten):
        ctx = _kontext(_befunde(high=3, medium=6, low=9), seiten=seiten)
        wert = ctx.calculate_score()
        assert 0.0 <= wert <= 100.0

    def test_seitenzahl_wird_aus_den_metriken_gelesen(self):
        ctx = _kontext(_befunde(high=1), seiten=42)
        assert ctx.crawled_pages() == 42
