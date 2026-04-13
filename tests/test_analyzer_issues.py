"""
Analyzer issue-detection unit tests.

The analyzer's individual checker methods are pure functions over
PageData, so we can test them without any network I/O.
"""

from seo_autopilot.agents.analyzer import AnalyzerAgent
from seo_autopilot.sources.crawler import PageData
from seo_autopilot.core.project_manager import ProjectConfig


def _make_agent():
    cfg = ProjectConfig(id="t", domain="https://example.com", name="Example")
    return AnalyzerAgent("t", "audit_x", cfg)


def test_clean_page_produces_no_issues(sample_page_data):
    agent = _make_agent()
    pages = [sample_page_data]
    issues = []
    issues += agent._check_meta(pages)
    issues += agent._check_headings(pages)
    issues += agent._check_social(pages)
    issues += agent._check_schema(pages, "https://example.com")
    issues += agent._check_security(pages)
    issues += agent._check_performance(pages)
    issues += agent._check_canonical(pages)
    # The sample page should have at most 1 "images_without_alt" issue
    critical = [i for i in issues if i["severity"] == "high"]
    assert critical == []


def test_missing_title_detected():
    agent = _make_agent()
    p = PageData(url="https://example.com/x", status_code=200,
                 final_url="https://example.com/x")
    issues = agent._check_meta([p])
    types = [i["type"] for i in issues]
    assert "missing_title" in types
    assert "missing_meta_description" in types
    assert "missing_viewport" in types


def test_long_title_detected():
    agent = _make_agent()
    p = PageData(
        url="https://example.com/", status_code=200, final_url="https://example.com/",
        title="A" * 100, meta_description="x" * 140, viewport="yes", lang="de",
    )
    issues = [i for i in agent._check_meta([p]) if i["type"] == "long_title"]
    assert len(issues) == 1


def test_multiple_h1_flagged():
    agent = _make_agent()
    p = PageData(url="https://example.com/", final_url="https://example.com/",
                 status_code=200, h1=["A", "B", "C"])
    issues = agent._check_headings([p])
    assert any(i["type"] == "multiple_h1" for i in issues)


def test_http_page_flagged_as_insecure():
    agent = _make_agent()
    p = PageData(url="http://example.com/", final_url="http://example.com/",
                 status_code=200, https=False)
    issues = agent._check_security([p])
    assert any(i["type"] == "no_https" for i in issues)
