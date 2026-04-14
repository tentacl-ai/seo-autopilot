"""Tests for SPA detection and Playwright fallback."""

from seo_autopilot.sources.renderer import is_spa_likely, SPA_INDICATORS


class TestSPADetection:
    def test_spa_detected_react(self):
        html = '<html><body><div id="root"></div><script type="module" src="/app.js"></script></body></html>'
        assert is_spa_likely(html, word_count=5) is True

    def test_spa_detected_nextjs(self):
        html = '<html><body><div id="__next"></div><script>__NEXT_DATA__={}</script></body></html>'
        assert is_spa_likely(html, word_count=10) is True

    def test_spa_detected_nuxt(self):
        html = '<html><body><div id="__nuxt"></div></body></html>'
        assert is_spa_likely(html, word_count=3) is True

    def test_spa_detected_vue(self):
        html = '<html><body><div id="app"></div><script type="module" src="/main.js"></script></body></html>'
        assert is_spa_likely(html, word_count=8) is True

    def test_not_spa_enough_content(self):
        """Wenn genug Woerter da sind, kein Fallback noetig."""
        html = '<html><body><div id="root"></div></body></html>'
        assert is_spa_likely(html, word_count=100) is False

    def test_not_spa_static_site(self):
        """Normale statische Seite ohne SPA-Indikatoren."""
        html = '<html><body><h1>Hello</h1><p>World</p></body></html>'
        assert is_spa_likely(html, word_count=2) is False

    def test_not_spa_ssr_content(self):
        """SSR-Seite mit id=root aber genuegend Content."""
        html = '<html><body><div id="root"><h1>Hello</h1><p>Lots of content here</p></div></body></html>'
        assert is_spa_likely(html, word_count=60) is False

    def test_threshold_exact(self):
        """Genau am Schwellwert — kein Fallback."""
        html = '<html><body><div id="root"></div></body></html>'
        assert is_spa_likely(html, word_count=50) is False

    def test_threshold_below(self):
        """Knapp unter Schwellwert — Fallback."""
        html = '<html><body><div id="root"></div></body></html>'
        assert is_spa_likely(html, word_count=49) is True

    def test_case_insensitive(self):
        """SPA-Indikatoren case-insensitive."""
        html = '<html><body><DIV ID="ROOT"></DIV></body></html>'
        assert is_spa_likely(html, word_count=5) is True


class TestPageDataRenderedVia:
    def test_default_httpx(self):
        from seo_autopilot.sources.crawler import PageData
        page = PageData(url="https://example.com")
        assert page.rendered_via == "httpx"
