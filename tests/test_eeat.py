"""Tests for E-E-A-T Signal Analyzer (Phase 10)."""

import pytest
from seo_autopilot.analyzers.eeat import EEATAnalyzer, _url_matches


@pytest.fixture
def analyzer():
    return EEATAnalyzer()


def _page(url, schema_types=None, schema_data=None, https=True):
    return {
        "url": url,
        "schema_types": schema_types or [],
        "schema_data": schema_data or [],
        "https": https,
    }


DOMAIN = "https://example.com"


class TestLegalPages:
    def test_missing_impressum(self, analyzer):
        pages = [_page("https://example.com/"), _page("https://example.com/about")]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_impressum" in types

    def test_missing_datenschutz(self, analyzer):
        pages = [_page("https://example.com/"), _page("https://example.com/impressum")]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_datenschutz" in types

    def test_finds_impressum_and_datenschutz(self, analyzer):
        pages = [
            _page("https://example.com/"),
            _page("https://example.com/impressum"),
            _page("https://example.com/datenschutz"),
        ]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_impressum" not in types
        assert "missing_datenschutz" not in types

    def test_english_legal_pages_detected(self, analyzer):
        pages = [
            _page("https://example.com/"),
            _page("https://example.com/imprint"),
            _page("https://example.com/privacy-policy"),
        ]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_impressum" not in types
        assert "missing_datenschutz" not in types


class TestContactPage:
    def test_missing_contact(self, analyzer):
        pages = [_page("https://example.com/")]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_contact_page" in types

    def test_kontakt_detected(self, analyzer):
        pages = [_page("https://example.com/kontakt")]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_contact_page" not in types


class TestOrgSchema:
    def test_missing_org_schema(self, analyzer):
        pages = [_page("https://example.com/")]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_org_schema" in types

    def test_org_schema_no_sameas(self, analyzer):
        pages = [_page("https://example.com/", schema_types=["Organization"],
                        schema_data=[{"@type": "Organization", "name": "Example"}])]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_org_schema" not in types
        assert "org_schema_no_sameas" in types

    def test_org_schema_with_sameas(self, analyzer):
        pages = [_page("https://example.com/", schema_types=["Organization"],
                        schema_data=[{
                            "@type": "Organization",
                            "name": "Example",
                            "sameAs": [
                                "https://www.linkedin.com/company/example",
                                "https://www.wikidata.org/wiki/Q12345",
                            ],
                        }])]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "missing_org_schema" not in types
        assert "org_schema_no_sameas" not in types
        assert "LinkedIn" in result["signals"]["org_sameas"]
        assert "Wikidata" in result["signals"]["org_sameas"]


class TestAuthorSchema:
    def test_articles_missing_author(self, analyzer):
        pages = [_page("https://example.com/blog/post-1",
                        schema_types=["Article"],
                        schema_data=[{"@type": "Article", "headline": "Test"}])]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "articles_missing_author" in types

    def test_articles_with_author(self, analyzer):
        pages = [_page("https://example.com/blog/post-1",
                        schema_types=["Article"],
                        schema_data=[{
                            "@type": "Article",
                            "headline": "Test",
                            "author": {"@type": "Person", "name": "Max"},
                            "datePublished": "2026-01-01",
                            "dateModified": "2026-03-01",
                        }])]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "articles_missing_author" not in types
        assert "articles_missing_date_published" not in types

    def test_no_articles_no_author_issue(self, analyzer):
        """Sites without articles should not flag missing author."""
        pages = [_page("https://example.com/")]
        result = analyzer.analyze(pages, DOMAIN)
        types = [i["type"] for i in result["issues"]]
        assert "articles_missing_author" not in types


class TestEEATScore:
    def test_perfect_score(self, analyzer):
        """False-positive test: well-configured site gets high score, no critical issues."""
        pages = [
            _page("https://example.com/", schema_types=["Organization"], schema_data=[{
                "@type": "Organization", "name": "Ex",
                "sameAs": ["https://linkedin.com/company/ex",
                           "https://wikidata.org/wiki/Q1",
                           "https://github.com/ex"],
            }]),
            _page("https://example.com/impressum"),
            _page("https://example.com/datenschutz"),
            _page("https://example.com/kontakt"),
            _page("https://example.com/about"),
            _page("https://example.com/blog/a", schema_types=["Article"], schema_data=[{
                "@type": "Article", "headline": "A",
                "author": {"@type": "Person", "name": "Max"},
                "datePublished": "2026-01-01",
                "dateModified": "2026-03-01",
            }]),
        ]
        result = analyzer.analyze(pages, DOMAIN)
        assert result["score"] == 100
        # No critical/high issues
        severe = [i for i in result["issues"] if i["severity"] in ("critical", "high")]
        assert severe == []

    def test_empty_site_low_score(self, analyzer):
        pages = [_page("https://example.com/")]
        result = analyzer.analyze(pages, DOMAIN)
        assert result["score"] < 40

    def test_score_is_capped_at_100(self, analyzer):
        """Score should never exceed 100."""
        pages = [
            _page("https://example.com/", schema_types=["Organization"], schema_data=[{
                "@type": "Organization", "name": "Ex",
                "sameAs": ["https://linkedin.com/c/x", "https://wikidata.org/w/Q1",
                           "https://github.com/x", "https://youtube.com/x",
                           "https://facebook.com/x"],
            }]),
            _page("https://example.com/impressum"),
            _page("https://example.com/datenschutz"),
            _page("https://example.com/kontakt"),
            _page("https://example.com/about"),
        ]
        result = analyzer.analyze(pages, DOMAIN)
        assert result["score"] <= 100


class TestUrlMatching:
    def test_matches_subpath(self):
        assert _url_matches("https://example.com/de/impressum", ["impressum"])

    def test_ignores_query_params(self):
        assert _url_matches("https://example.com/privacy?ref=footer", ["privacy"])

    def test_no_false_match(self):
        assert not _url_matches("https://example.com/blog/post", ["impressum"])
