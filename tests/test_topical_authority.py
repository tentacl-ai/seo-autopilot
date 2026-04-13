"""Tests for Topical Authority Analyzer."""

import pytest
from seo_autopilot.analyzers.topical_authority import TopicalAuthorityAnalyzer


@pytest.fixture
def analyzer():
    return TopicalAuthorityAnalyzer()


@pytest.fixture
def clustered_site():
    """Site with recognizable cluster structure."""
    return [
        {"url": "https://example.com/blog/seo-grundlagen", "title": "SEO Grundlagen Guide", "h1": ["SEO Grundlagen"], "h2": ["Was ist SEO?", "Warum SEO?"], "word_count": 2000, "internal_links": 15, "schema_types": ["Article"]},
        {"url": "https://example.com/blog/seo-onpage", "title": "SEO On-Page Optimierung", "h1": ["On-Page SEO"], "h2": ["Title Tags", "Meta Descriptions"], "word_count": 1200, "internal_links": 8, "schema_types": ["Article"]},
        {"url": "https://example.com/blog/seo-offpage", "title": "SEO Off-Page Strategien", "h1": ["Off-Page SEO"], "h2": ["Backlinks", "Social Signals"], "word_count": 1000, "internal_links": 6, "schema_types": ["Article"]},
        {"url": "https://example.com/blog/seo-technisch", "title": "Technisches SEO", "h1": ["Technisches SEO"], "h2": ["Crawling", "Indexierung"], "word_count": 900, "internal_links": 5, "schema_types": ["Article"]},
        {"url": "https://example.com/about", "title": "Ueber uns", "h1": ["Ueber uns"], "h2": [], "word_count": 300, "internal_links": 2, "schema_types": []},
    ]


@pytest.fixture
def flat_site():
    """Site without cluster structure — completely different topics."""
    return [
        {"url": "https://example.com/pizza-rezept", "title": "Pizza backen", "h1": ["Pizza"], "h2": [], "word_count": 500, "internal_links": 1, "schema_types": []},
        {"url": "https://example.com/gitarre-lernen", "title": "Gitarre spielen", "h1": ["Gitarre"], "h2": [], "word_count": 500, "internal_links": 1, "schema_types": []},
        {"url": "https://example.com/wandern-alpen", "title": "Wanderungen Alpen", "h1": ["Wandern"], "h2": [], "word_count": 500, "internal_links": 1, "schema_types": []},
        {"url": "https://example.com/auto-reparatur", "title": "KFZ Werkstatt", "h1": ["Reparatur"], "h2": [], "word_count": 500, "internal_links": 1, "schema_types": []},
        {"url": "https://example.com/yoga-kurs", "title": "Yoga Anfaenger", "h1": ["Yoga"], "h2": [], "word_count": 500, "internal_links": 1, "schema_types": []},
    ]


class TestClusterDetection:
    def test_detects_existing_cluster_structure(self, analyzer, clustered_site):
        clusters = analyzer.detect_clusters(clustered_site)
        assert len(clusters) >= 1
        blog_cluster = [c for c in clusters if "blog" in c.cluster_id.lower()]
        assert len(blog_cluster) == 1
        assert len(blog_cluster[0].cluster_urls) >= 3

    def test_identifies_pillar_page(self, analyzer, clustered_site):
        clusters = analyzer.detect_clusters(clustered_site)
        blog_cluster = [c for c in clusters if "blog" in c.cluster_id.lower()][0]
        # Pillar should be the page with the most links/content
        assert blog_cluster.pillar_url is not None

    def test_no_clusters_on_flat_site(self, analyzer, flat_site):
        clusters = analyzer.detect_clusters(flat_site)
        # Flat URLs without shared path prefix -> no path clusters
        path_clusters = [c for c in clusters if c.cluster_id.startswith("path_")]
        assert len(path_clusters) == 0


class TestIssueDetection:
    def test_detects_no_clusters_issue(self, analyzer, flat_site):
        clusters = analyzer.detect_clusters(flat_site)
        issues = analyzer.detect_issues(clusters, flat_site)
        types = [i["type"] for i in issues]
        assert "no_topic_clusters_detected" in types

    def test_detects_orphan_pages(self, analyzer, clustered_site):
        clusters = analyzer.detect_clusters(clustered_site)
        issues = analyzer.detect_issues(clusters, clustered_site)
        types = [i["type"] for i in issues]
        # "about" page belongs to no cluster
        assert "orphan_cluster_page" in types

    def test_detects_cannibalization_within_cluster(self, analyzer):
        pages = [
            {"url": "https://example.com/blog/seo-tipps", "title": "SEO Tipps fuer Anfaenger", "h1": ["SEO Tipps"], "h2": [], "word_count": 800, "internal_links": 5, "schema_types": []},
            {"url": "https://example.com/blog/seo-tipps-2", "title": "SEO Tipps fuer Profis", "h1": ["SEO Tipps"], "h2": [], "word_count": 800, "internal_links": 5, "schema_types": []},
            {"url": "https://example.com/blog/seo-tools", "title": "SEO Tools Vergleich", "h1": ["SEO Tools"], "h2": [], "word_count": 800, "internal_links": 5, "schema_types": []},
        ]
        clusters = analyzer.detect_clusters(pages)
        issues = analyzer.detect_issues(clusters, pages)
        types = [i["type"] for i in issues]
        assert "cluster_cannibalization" in types

    def test_detects_coverage_gaps_via_gsc(self, analyzer, clustered_site):
        clusters = analyzer.detect_clusters(clustered_site)
        gsc_keywords = [
            {"query": "seo audit tool", "page": "https://example.com/blog/seo-grundlagen", "impressions": 100},
            {"query": "seo blog strategie", "page": "", "impressions": 50},  # Gap: no ranking
        ]
        issues = analyzer.detect_issues(clusters, clustered_site, gsc_keywords=gsc_keywords)
        types = [i["type"] for i in issues]
        # "seo blog strategie" has "blog" in label match
        assert "cluster_coverage_gap" in types

    def test_calculates_cluster_authority_score(self, analyzer, clustered_site):
        clusters = analyzer.detect_clusters(clustered_site)
        blog_cluster = [c for c in clusters if "blog" in c.cluster_id.lower()][0]
        assert blog_cluster.authority_score > 0
        assert 0 <= blog_cluster.internal_link_coverage <= 1.0
