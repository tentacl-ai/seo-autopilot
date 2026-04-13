"""Tests for Internal Link Graph Analyzer."""

import pytest
from seo_autopilot.analyzers.link_graph import LinkGraph


@pytest.fixture
def graph():
    return LinkGraph()


@pytest.fixture
def site_pages():
    """Typical site structure: Home -> About, Blog -> Post1, Post2."""
    return [
        {"url": "https://example.com", "status_code": 200, "outlink_urls": ["https://example.com/about", "https://example.com/blog"]},
        {"url": "https://example.com/about", "status_code": 200, "outlink_urls": ["https://example.com"]},
        {"url": "https://example.com/blog", "status_code": 200, "outlink_urls": ["https://example.com/blog/post-1", "https://example.com/blog/post-2"]},
        {"url": "https://example.com/blog/post-1", "status_code": 200, "outlink_urls": ["https://example.com/blog"]},
        {"url": "https://example.com/blog/post-2", "status_code": 200, "outlink_urls": ["https://example.com/blog"]},
        {"url": "https://example.com/orphan", "status_code": 200, "outlink_urls": []},  # Nobody links here
    ]


class TestOrphanPages:
    def test_detects_orphan_pages(self, graph, site_pages):
        issues = graph.detect_issues(site_pages, "https://example.com")
        orphan_issues = [i for i in issues if i["type"] == "orphan_page"]
        orphan_urls = [i["affected_url"] for i in orphan_issues]
        assert "https://example.com/orphan" in orphan_urls

    def test_homepage_is_never_orphan(self, graph, site_pages):
        issues = graph.detect_issues(site_pages, "https://example.com")
        orphan_urls = [i["affected_url"] for i in issues if i["type"] == "orphan_page"]
        assert "https://example.com" not in orphan_urls


class TestClickDepth:
    def test_detects_deep_pages(self, graph):
        pages = [
            {"url": "https://example.com", "status_code": 200, "outlink_urls": ["https://example.com/l1"]},
            {"url": "https://example.com/l1", "status_code": 200, "outlink_urls": ["https://example.com/l2"]},
            {"url": "https://example.com/l2", "status_code": 200, "outlink_urls": ["https://example.com/l3"]},
            {"url": "https://example.com/l3", "status_code": 200, "outlink_urls": ["https://example.com/l4"]},
            {"url": "https://example.com/l4", "status_code": 200, "outlink_urls": []},
        ]
        issues = graph.detect_issues(pages, "https://example.com")
        deep = [i for i in issues if i["type"] == "deep_page"]
        assert len(deep) >= 1  # l4 is depth 4

    def test_homepage_depth_zero(self, graph, site_pages):
        graph.build(site_pages, "https://example.com")
        depths = graph.click_depth()
        assert depths.get("https://example.com") == 0


class TestBrokenLinks:
    def test_detects_broken_internal_links(self, graph):
        pages = [
            {"url": "https://example.com", "status_code": 200, "outlink_urls": ["https://example.com/broken"]},
            {"url": "https://example.com/broken", "status_code": 404, "outlink_urls": []},
        ]
        issues = graph.detect_issues(pages, "https://example.com")
        broken = [i for i in issues if i["type"] == "broken_internal_link"]
        assert len(broken) == 1
        assert broken[0]["severity"] == "high"


class TestPageRank:
    def test_calculates_internal_pagerank(self, graph, site_pages):
        graph.build(site_pages, "https://example.com")
        pr = graph.pagerank()
        assert len(pr) > 0
        # Homepage and blog should have higher PR (more incoming links)
        assert pr["https://example.com/blog"] > pr["https://example.com/orphan"]

    def test_pagerank_sums_to_approximately_one(self, graph, site_pages):
        graph.build(site_pages, "https://example.com")
        pr = graph.pagerank()
        total = sum(pr.values())
        assert 0.5 < total < 1.5  # PageRank with dangling nodes does not sum exactly to 1


class TestLinkEquitySink:
    def test_detects_equity_sink(self, graph):
        pages = [
            {"url": "https://example.com", "status_code": 200, "outlink_urls": ["https://example.com/sink"]},
            {"url": "https://example.com/a", "status_code": 200, "outlink_urls": ["https://example.com/sink"]},
            {"url": "https://example.com/b", "status_code": 200, "outlink_urls": ["https://example.com/sink"]},
            {"url": "https://example.com/c", "status_code": 200, "outlink_urls": ["https://example.com/sink"]},
            {"url": "https://example.com/d", "status_code": 200, "outlink_urls": ["https://example.com/sink"]},
            {"url": "https://example.com/sink", "status_code": 200, "outlink_urls": []},  # 5 incoming, 0 outgoing
        ]
        issues = graph.detect_issues(pages, "https://example.com")
        sinks = [i for i in issues if i["type"] == "link_equity_sink"]
        assert len(sinks) == 1
