"""Tests for Canonical Engine."""

import pytest
from seo_autopilot.analyzers.canonical_engine import (
    CanonicalEngine,
    PageCanonicalData,
    _normalize_url,
)


@pytest.fixture
def engine():
    return CanonicalEngine(sitemap_urls={
        "https://example.com",
        "https://example.com/about",
        "https://example.com/blog",
    })


class TestCanonicalResolution:
    def test_resolves_from_http_header_first(self, engine):
        page = PageCanonicalData(
            url="https://example.com/page",
            canonical="https://example.com/html-canonical",
            http_link_canonical="https://example.com/header-canonical",
        )
        res = engine.resolve(page)
        assert res.resolved_canonical == "https://example.com/header-canonical"
        assert res.signal_source == "http_header"

    def test_resolves_from_html_when_no_header(self, engine):
        page = PageCanonicalData(
            url="https://example.com/page",
            canonical="https://example.com/html-canonical",
        )
        res = engine.resolve(page)
        assert res.resolved_canonical == "https://example.com/html-canonical"
        assert res.signal_source == "html"

    def test_resolves_from_sitemap_when_no_canonical(self, engine):
        page = PageCanonicalData(url="https://example.com/about")
        res = engine.resolve(page)
        assert res.resolved_canonical == "https://example.com/about"
        assert res.signal_source == "sitemap"

    def test_self_referencing_detected(self, engine):
        page = PageCanonicalData(
            url="https://example.com/page",
            canonical="https://example.com/page",
        )
        res = engine.resolve(page)
        assert res.is_self_referencing is True


class TestConflictDetection:
    def test_detects_missing_canonical(self, engine):
        pages = [PageCanonicalData(url="https://example.com/new-page")]
        issues = engine.detect_conflicts(pages)
        types = [i["type"] for i in issues]
        assert "canonical_missing" in types

    def test_detects_canonical_pointing_to_redirect(self, engine):
        pages = [
            PageCanonicalData(
                url="https://example.com/page-a",
                canonical="https://example.com/page-b",
            ),
            PageCanonicalData(
                url="https://example.com/page-b",
                status_code=301,
            ),
        ]
        issues = engine.detect_conflicts(pages)
        types = [i["type"] for i in issues]
        assert "canonical_points_to_redirect" in types

    def test_detects_canonical_pointing_to_404(self, engine):
        pages = [
            PageCanonicalData(
                url="https://example.com/page-a",
                canonical="https://example.com/page-b",
            ),
            PageCanonicalData(
                url="https://example.com/page-b",
                status_code=404,
            ),
        ]
        issues = engine.detect_conflicts(pages)
        types = [i["type"] for i in issues]
        assert "canonical_points_to_error" in types
        assert issues[0]["severity"] == "critical"

    def test_detects_canonical_conflicts_with_sitemap(self, engine):
        pages = [
            PageCanonicalData(
                url="https://example.com/about",
                canonical="https://example.com/about-new",
            ),
        ]
        issues = engine.detect_conflicts(pages)
        types = [i["type"] for i in issues]
        assert "canonical_conflicts_sitemap" in types

    def test_detects_canonical_pointing_to_noindex(self, engine):
        pages = [
            PageCanonicalData(
                url="https://example.com/page-a",
                canonical="https://example.com/page-b",
            ),
            PageCanonicalData(
                url="https://example.com/page-b",
                robots_meta="noindex, nofollow",
            ),
        ]
        issues = engine.detect_conflicts(pages)
        types = [i["type"] for i in issues]
        assert "canonical_points_to_noindex" in types

    def test_detects_canonical_chain(self, engine):
        pages = [
            PageCanonicalData(
                url="https://example.com/a",
                canonical="https://example.com/b",
            ),
            PageCanonicalData(
                url="https://example.com/b",
                canonical="https://example.com/c",
            ),
            PageCanonicalData(url="https://example.com/c"),
        ]
        issues = engine.detect_conflicts(pages)
        types = [i["type"] for i in issues]
        assert "canonical_chain" in types

    def test_detects_canonical_conflicts_hreflang(self, engine):
        pages = [
            PageCanonicalData(
                url="https://example.com/de",
                canonical="https://example.com/main",
                hreflang=[
                    {"hreflang": "de", "href": "https://example.com/de"},
                    {"hreflang": "en", "href": "https://example.com/en"},
                ],
            ),
        ]
        issues = engine.detect_conflicts(pages)
        types = [i["type"] for i in issues]
        assert "canonical_conflicts_hreflang" in types


class TestCanonicalPair:
    def test_is_canonical_pair(self, engine):
        pages = [
            PageCanonicalData(
                url="https://example.com/a",
                canonical="https://example.com/b",
            ),
            PageCanonicalData(url="https://example.com/b"),
        ]
        resolutions = engine.resolve_all(pages)
        assert engine.is_canonical_pair(
            "https://example.com/a", "https://example.com/b", resolutions
        ) is True

    def test_not_canonical_pair(self, engine):
        pages = [
            PageCanonicalData(url="https://example.com/a"),
            PageCanonicalData(url="https://example.com/b"),
        ]
        resolutions = engine.resolve_all(pages)
        assert engine.is_canonical_pair(
            "https://example.com/a", "https://example.com/b", resolutions
        ) is False


class TestNormalizeUrl:
    def test_trailing_slash_removed(self):
        assert _normalize_url("https://example.com/page/") == "https://example.com/page"

    def test_root_keeps_slash(self):
        assert _normalize_url("https://example.com/") == "https://example.com/"

    def test_lowercase_host(self):
        assert _normalize_url("https://Example.COM/Page") == "https://example.com/Page"
