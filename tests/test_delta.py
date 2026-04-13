"""Tests for Delta / Regression Engine."""

import pytest
from seo_autopilot.analyzers.delta import DeltaAnalyzer, DeltaReport


@pytest.fixture
def analyzer():
    return DeltaAnalyzer()


@pytest.fixture
def issue_a():
    return {"type": "missing_title", "severity": "high", "affected_url": "https://example.com/a", "title": "Missing title"}


@pytest.fixture
def issue_b():
    return {"type": "missing_h1", "severity": "medium", "affected_url": "https://example.com/b", "title": "Missing H1"}


@pytest.fixture
def issue_c():
    return {"type": "poor_lcp", "severity": "high", "affected_url": "https://example.com/c", "title": "Poor LCP"}


class TestNewAndResolvedIssues:
    def test_detects_new_issues(self, analyzer, issue_a, issue_b, issue_c):
        previous = [issue_a, issue_b]
        current = [issue_a, issue_b, issue_c]
        report = analyzer.compare(current, previous)
        assert len(report.new_issues) == 1
        assert report.new_issues[0]["type"] == "poor_lcp"

    def test_detects_resolved_issues(self, analyzer, issue_a, issue_b, issue_c):
        previous = [issue_a, issue_b, issue_c]
        current = [issue_a]
        report = analyzer.compare(current, previous)
        assert len(report.resolved_issues) == 2

    def test_persistent_issues(self, analyzer, issue_a, issue_b):
        report = analyzer.compare([issue_a, issue_b], [issue_a, issue_b])
        assert len(report.persistent_issues) == 2
        assert len(report.new_issues) == 0
        assert len(report.resolved_issues) == 0


class TestScoreRegression:
    def test_detects_score_regression(self, analyzer):
        report = analyzer.compare(
            current_issues=[], previous_issues=[],
            current_score=65.0, previous_score=80.0,
        )
        assert report.score_delta == -15.0
        assert report.has_regression is True

    def test_no_regression_on_improvement(self, analyzer):
        report = analyzer.compare(
            current_issues=[], previous_issues=[],
            current_score=85.0, previous_score=70.0,
        )
        assert report.score_delta == 15.0
        assert report.has_regression is False

    def test_small_drop_not_regression(self, analyzer):
        report = analyzer.compare(
            current_issues=[], previous_issues=[],
            current_score=78.0, previous_score=80.0,
        )
        assert report.has_regression is False


class TestSeverityRegression:
    def test_detects_regressed_issue_severity(self, analyzer):
        prev = [{"type": "poor_lcp", "severity": "medium", "affected_url": "https://example.com/x", "title": "LCP"}]
        curr = [{"type": "poor_lcp", "severity": "high", "affected_url": "https://example.com/x", "title": "LCP worse"}]
        report = analyzer.compare(curr, prev)
        assert len(report.regressed_issues) == 1
        assert report.regressed_issues[0]["previous_severity"] == "medium"


class TestPageLevelDelta:
    def test_detects_improved_pages(self, analyzer):
        prev = [
            {"type": "a", "severity": "low", "affected_url": "https://example.com/page", "title": "A"},
            {"type": "b", "severity": "low", "affected_url": "https://example.com/page", "title": "B"},
        ]
        curr = [
            {"type": "a", "severity": "low", "affected_url": "https://example.com/page", "title": "A"},
        ]
        report = analyzer.compare(curr, prev)
        assert len(report.improved_pages) == 1
        assert report.improved_pages[0]["url"] == "https://example.com/page"

    def test_detects_degraded_pages(self, analyzer):
        prev = [{"type": "a", "severity": "low", "affected_url": "https://example.com/page", "title": "A"}]
        curr = [
            {"type": "a", "severity": "low", "affected_url": "https://example.com/page", "title": "A"},
            {"type": "b", "severity": "low", "affected_url": "https://example.com/page", "title": "B"},
            {"type": "c", "severity": "low", "affected_url": "https://example.com/page", "title": "C"},
        ]
        report = analyzer.compare(curr, prev)
        assert len(report.degraded_pages) == 1


class TestDeltaEndpoint:
    def test_delta_returns_structured_diff(self, analyzer):
        report = analyzer.compare(
            current_issues=[{"type": "x", "severity": "high", "affected_url": "u", "title": "X"}],
            previous_issues=[],
            current_score=75.0,
            previous_score=80.0,
            current_audit_id="audit-2",
            previous_audit_id="audit-1",
        )
        d = report.to_dict()
        assert d["current_audit_id"] == "audit-2"
        assert d["score_delta"] == -5.0
        assert d["new_issues_count"] == 1
        assert d["resolved_issues_count"] == 0
        assert "is_regression" in d


class TestRegressionAlert:
    def test_regression_triggers_alert(self, analyzer):
        report = analyzer.compare(
            current_issues=[
                {"type": "a", "severity": "high", "affected_url": "u1", "title": "Issue A"},
                {"type": "b", "severity": "high", "affected_url": "u2", "title": "Issue B"},
                {"type": "c", "severity": "critical", "affected_url": "u3", "title": "Issue C"},
            ],
            previous_issues=[],
            current_score=50.0,
            previous_score=80.0,
        )
        assert report.has_regression is True
        msg = analyzer.generate_alert_message(report)
        assert msg is not None
        assert "REGRESSION" in msg
        assert "Score:" in msg

    def test_no_alert_on_improvement(self, analyzer):
        report = analyzer.compare(
            current_issues=[],
            previous_issues=[{"type": "a", "severity": "high", "affected_url": "u", "title": "A"}],
            current_score=90.0,
            previous_score=70.0,
        )
        msg = analyzer.generate_alert_message(report)
        assert msg is None


class TestCWVDelta:
    def test_compares_cwv_metrics(self, analyzer):
        report = analyzer.compare(
            current_issues=[], previous_issues=[],
            current_metrics={"lighthouse_performance": 85, "geo_avg_score": 75.0},
            previous_metrics={"lighthouse_performance": 70, "geo_avg_score": 60.0},
        )
        assert "Performance Score" in report.cwv_changes
        assert report.cwv_changes["Performance Score"]["delta"] == 15
        assert report.geo_score_delta == 15.0
