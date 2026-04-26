"""Tests for Robots.txt + Sitemap Audit (Phase 9)."""

import pytest
from seo_autopilot.analyzers.robots_sitemap import (
    RobotsSitemapAuditor,
    RobotsResult,
    SitemapResult,
    SitemapUrl,
)


@pytest.fixture
def auditor():
    return RobotsSitemapAuditor()


# ---------------------------------------------------------------------------
# robots.txt tests
# ---------------------------------------------------------------------------


class TestRobotsIssues:
    def test_missing_robots_txt(self, auditor):
        robots = RobotsResult(exists=False, status_code=404)
        issues = auditor.detect_robots_issues(robots)
        types = [i["type"] for i in issues]
        assert "missing_robots_txt" in types

    def test_ai_crawler_blocked(self, auditor):
        robots = RobotsResult(
            exists=True,
            status_code=200,
            raw=(
                "User-agent: GPTBot\nDisallow: /\n"
                "User-agent: ClaudeBot\nDisallow: /\n"
                "User-agent: *\nDisallow:\n"
            ),
        )
        auditor._parse_robots(robots)
        issues = auditor.detect_robots_issues(robots)
        types = [i["type"] for i in issues]
        assert "ai_crawler_blocked" in types
        ai_issue = [i for i in issues if i["type"] == "ai_crawler_blocked"][0]
        assert "GPTBot" in ai_issue["title"]
        assert "ClaudeBot" in ai_issue["title"]

    def test_css_js_blocked(self, auditor):
        robots = RobotsResult(
            exists=True,
            status_code=200,
            raw=("User-agent: *\nDisallow: /static/\nDisallow: /assets/\n"),
        )
        auditor._parse_robots(robots)
        issues = auditor.detect_robots_issues(robots)
        types = [i["type"] for i in issues]
        assert "css_js_blocked" in types

    def test_missing_sitemap_directive(self, auditor):
        robots = RobotsResult(
            exists=True, status_code=200, raw=("User-agent: *\nDisallow: /admin/\n")
        )
        auditor._parse_robots(robots)
        issues = auditor.detect_robots_issues(robots)
        types = [i["type"] for i in issues]
        assert "missing_sitemap_directive" in types

    def test_wildcard_disallow(self, auditor):
        robots = RobotsResult(
            exists=True, status_code=200, raw=("User-agent: *\nDisallow: /\n")
        )
        auditor._parse_robots(robots)
        issues = auditor.detect_robots_issues(robots)
        types = [i["type"] for i in issues]
        assert "wildcard_disallow" in types
        wc = [i for i in issues if i["type"] == "wildcard_disallow"][0]
        assert wc["severity"] == "critical"

    def test_clean_robots_no_issues(self, auditor):
        """False-positive test: well-configured robots.txt produces no issues."""
        robots = RobotsResult(
            exists=True,
            status_code=200,
            raw=(
                "User-agent: *\n"
                "Disallow: /admin/\n"
                "Disallow: /api/\n\n"
                "Sitemap: https://example.com/sitemap.xml\n"
            ),
        )
        auditor._parse_robots(robots)
        issues = auditor.detect_robots_issues(robots)
        assert issues == []

    def test_sitemap_directive_parsed(self, auditor):
        robots = RobotsResult(
            exists=True,
            status_code=200,
            raw=(
                "Sitemap: https://example.com/sitemap.xml\n"
                "Sitemap: https://example.com/sitemap-blog.xml\n"
                "User-agent: *\nDisallow: /admin/\n"
            ),
        )
        auditor._parse_robots(robots)
        assert len(robots.sitemap_directives) == 2
        assert "https://example.com/sitemap.xml" in robots.sitemap_directives


# ---------------------------------------------------------------------------
# sitemap.xml tests
# ---------------------------------------------------------------------------


class TestSitemapIssues:
    def test_missing_sitemap(self, auditor):
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml", exists=False, status_code=404
        )
        issues = auditor.detect_sitemap_issues(sitemap)
        types = [i["type"] for i in issues]
        assert "missing_sitemap" in types

    def test_parse_error(self, auditor):
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml",
            exists=True,
            status_code=200,
            parse_error="not well-formed",
        )
        issues = auditor.detect_sitemap_issues(sitemap)
        types = [i["type"] for i in issues]
        assert "sitemap_parse_error" in types

    def test_empty_sitemap(self, auditor):
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml",
            exists=True,
            status_code=200,
            urls=[],
        )
        issues = auditor.detect_sitemap_issues(sitemap)
        types = [i["type"] for i in issues]
        assert "empty_sitemap" in types

    def test_broken_urls_in_sitemap(self, auditor):
        urls = [SitemapUrl(loc="https://example.com/gone")]
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml",
            exists=True,
            status_code=200,
            urls=urls,
        )
        url_status = {"https://example.com/gone": 404}
        issues = auditor.detect_sitemap_issues(sitemap, url_status=url_status)
        types = [i["type"] for i in issues]
        assert "sitemap_broken_url" in types

    def test_non_canonical_urls(self, auditor):
        urls = [
            SitemapUrl(loc="https://example.com/page", lastmod="2026-01-01"),
            SitemapUrl(loc="https://example.com/page?ref=123", lastmod="2026-01-01"),
        ]
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml",
            exists=True,
            status_code=200,
            urls=urls,
        )
        canonical = {"https://example.com/page"}
        issues = auditor.detect_sitemap_issues(sitemap, canonical_urls=canonical)
        types = [i["type"] for i in issues]
        assert "sitemap_non_canonical_urls" in types

    def test_missing_pages_not_in_sitemap(self, auditor):
        urls = [SitemapUrl(loc="https://example.com/", lastmod="2026-01-01")]
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml",
            exists=True,
            status_code=200,
            urls=urls,
        )
        crawled = {
            "https://example.com/",
            "https://example.com/about",
            "https://example.com/blog",
        }
        issues = auditor.detect_sitemap_issues(sitemap, crawled_urls=crawled)
        types = [i["type"] for i in issues]
        assert "sitemap_missing_pages" in types

    def test_stale_lastmod(self, auditor):
        urls = [SitemapUrl(loc="https://example.com/old", lastmod="2020-01-01")]
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml",
            exists=True,
            status_code=200,
            urls=urls,
        )
        issues = auditor.detect_sitemap_issues(sitemap)
        types = [i["type"] for i in issues]
        assert "sitemap_stale_lastmod" in types

    def test_no_lastmod(self, auditor):
        urls = [SitemapUrl(loc="https://example.com/page")]
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml",
            exists=True,
            status_code=200,
            urls=urls,
        )
        issues = auditor.detect_sitemap_issues(sitemap)
        types = [i["type"] for i in issues]
        assert "sitemap_no_lastmod" in types

    def test_clean_sitemap_no_issues(self, auditor):
        """False-positive test: healthy sitemap produces no issues."""
        urls = [
            SitemapUrl(loc="https://example.com/", lastmod="2026-04-01"),
            SitemapUrl(loc="https://example.com/about", lastmod="2026-03-15"),
        ]
        sitemap = SitemapResult(
            url="https://example.com/sitemap.xml",
            exists=True,
            status_code=200,
            urls=urls,
        )
        canonical = {"https://example.com/", "https://example.com/about"}
        issues = auditor.detect_sitemap_issues(sitemap, canonical_urls=canonical)
        assert issues == []


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------


class TestSitemapParsing:
    def test_parse_urlset(self, auditor):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.com/</loc><lastmod>2026-04-01</lastmod></url>"
            "<url><loc>https://example.com/about</loc></url>"
            "</urlset>"
        )
        result = SitemapResult(
            url="https://example.com/sitemap.xml", exists=True, status_code=200
        )
        auditor._parse_sitemap(result, xml)
        assert len(result.urls) == 2
        assert result.urls[0].loc == "https://example.com/"
        assert result.urls[0].lastmod == "2026-04-01"
        assert result.urls[1].lastmod is None
        assert not result.is_index

    def test_parse_sitemap_index(self, auditor):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>"
            "<sitemap><loc>https://example.com/sitemap-2.xml</loc></sitemap>"
            "</sitemapindex>"
        )
        result = SitemapResult(
            url="https://example.com/sitemap.xml", exists=True, status_code=200
        )
        auditor._parse_sitemap(result, xml)
        assert result.is_index
        assert len(result.child_sitemaps) == 2

    def test_parse_invalid_xml(self, auditor):
        result = SitemapResult(
            url="https://example.com/sitemap.xml", exists=True, status_code=200
        )
        auditor._parse_sitemap(result, "<not valid xml")
        assert result.parse_error is not None
