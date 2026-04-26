"""Tests for GEO Audit — AI citability."""

import pytest
from seo_autopilot.analyzers.geo_audit import GEOAuditor


@pytest.fixture
def auditor():
    return GEOAuditor()


@pytest.fixture
def well_structured_page():
    return {
        "url": "https://example.com/was-ist-seo",
        "title": "Was ist SEO? 10 wichtige Fakten fuer 2026",
        "h1": ["Was ist SEO?"],
        "h2": ["Was bedeutet SEO?", "Wie funktioniert SEO?", "Warum ist SEO wichtig?"],
        "word_count": 1200,
        "meta_description": "SEO steht fuer Suchmaschinenoptimierung. 93% aller Online-Erfahrungen beginnen mit einer Suchmaschine.",
        "schema_types": ["Article", "Organization"],
        "schema_data": [
            {
                "@type": "Article",
                "headline": "Was ist SEO?",
                "datePublished": "2026-03-01",
                "dateModified": "2026-04-10",
                "author": {"@type": "Person", "name": "Max"},
            },
            {
                "@type": "Organization",
                "name": "Tentacl",
                "sameAs": ["https://linkedin.com/company/tentacl"],
            },
        ],
    }


@pytest.fixture
def poor_page():
    return {
        "url": "https://example.com/about",
        "title": "About",
        "h1": [],
        "h2": [],
        "word_count": 50,
        "meta_description": "",
        "schema_types": [],
        "schema_data": [],
    }


class TestAICrawlerAccess:
    def test_detects_blocked_gptbot(self):
        robots = "User-agent: GPTBot\nDisallow: /\n"
        auditor = GEOAuditor(robots_txt_content=robots)
        blocked = auditor.check_ai_crawler_access()
        assert "GPTBot" in blocked

    def test_detects_blocked_claudebot(self):
        robots = "User-agent: ClaudeBot\nDisallow: /\n"
        auditor = GEOAuditor(robots_txt_content=robots)
        blocked = auditor.check_ai_crawler_access()
        assert "ClaudeBot" in blocked

    def test_no_block_when_allowed(self):
        robots = "User-agent: Googlebot\nAllow: /\n"
        auditor = GEOAuditor(robots_txt_content=robots)
        blocked = auditor.check_ai_crawler_access()
        assert blocked == []

    def test_no_robots_txt(self):
        auditor = GEOAuditor()
        blocked = auditor.check_ai_crawler_access()
        assert blocked == []


class TestPageAnalysis:
    def test_well_structured_page_high_score(self, auditor, well_structured_page):
        result = auditor.analyze_page(well_structured_page)
        assert result["geo_score"] >= 70
        assert result["checks"]["answer_first"] is True
        assert result["checks"]["structured_format"] is True
        assert result["checks"]["entity_clarity"] is True
        assert result["checks"]["freshness_signals"] is True

    def test_poor_page_low_score(self, auditor, poor_page):
        result = auditor.analyze_page(poor_page)
        assert result["geo_score"] < 50

    def test_no_false_positive_on_well_structured_page(
        self, auditor, well_structured_page
    ):
        result = auditor.analyze_page(well_structured_page)
        # Should have few/no issues (except possibly paragraph_length)
        critical_issues = [i for i in result["issues"] if i["severity"] == "critical"]
        assert len(critical_issues) == 0

    def test_detects_missing_answer_first(self, auditor, poor_page):
        result = auditor.analyze_page(poor_page)
        assert result["checks"]["answer_first"] is False
        issue_types = [i["type"] for i in result["issues"]]
        assert "geo_answer_first" in issue_types

    def test_detects_low_fact_density(self, auditor):
        page = {
            "url": "https://example.com/text",
            "title": "Ein einfacher Text ohne Zahlen",
            "h1": ["Text"],
            "h2": [],
            "word_count": 80,
            "meta_description": "Hier steht nur Prosa ohne Fakten.",
            "schema_types": [],
            "schema_data": [],
        }
        result = auditor.analyze_page(page)
        assert result["checks"]["fact_density"] is False

    def test_geo_score_calculated_correctly(self, auditor, well_structured_page):
        result = auditor.analyze_page(well_structured_page)
        # Score must be between 0 and 100
        assert 0 <= result["geo_score"] <= 100
        # Sum of all weights = 100
        total_weight = sum(c["weight"] for c in GEO_CHECKS.values())
        assert total_weight == 100


class TestSiteAnalysis:
    def test_site_analysis_averages(self, auditor, well_structured_page, poor_page):
        result = auditor.analyze_site([well_structured_page, poor_page])
        assert result["pages_analyzed"] == 2
        assert "avg_geo_score" in result
        assert len(result["page_scores"]) == 2

    def test_blocked_crawler_site_level_issue(self):
        robots = "User-agent: GPTBot\nDisallow: /\n"
        auditor = GEOAuditor(robots_txt_content=robots)
        page = {
            "url": "https://example.com",
            "title": "Home",
            "h1": ["Home"],
            "h2": [],
            "word_count": 200,
            "meta_description": "Test",
            "schema_types": [],
            "schema_data": [],
        }
        result = auditor.analyze_site([page])
        issue_types = [i["type"] for i in result["issues"]]
        assert "geo_ai_crawler_blocked" in issue_types


# Import for fixture access
from seo_autopilot.analyzers.geo_audit import GEO_CHECKS
