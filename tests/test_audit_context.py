"""AuditContext aggregation + scoring tests."""

from datetime import datetime

from seo_autopilot.agents.base import AgentResult, AgentStatus
from seo_autopilot.core.audit_context import AuditContext
from seo_autopilot.core.project_manager import ProjectConfig


def _ctx():
    cfg = ProjectConfig(id="t", domain="https://example.com", name="Example")
    return AuditContext(audit_id="a1", project_id="t", project_config=cfg)


def _result(agent, issues=None, fixes=None):
    return AgentResult(
        status=AgentStatus.COMPLETED,
        agent_name=agent,
        project_id="t",
        audit_id="a1",
        issues=issues or [],
        fixes=fixes or [],
    )


def test_add_result_merges_issues_and_fixes():
    ctx = _ctx()
    ctx.add_result(
        "analyzer",
        _result(
            "analyzer",
            issues=[{"type": "missing_title", "severity": "high"}],
            fixes=[{"type": "title", "suggestion": "ok"}],
        ),
    )
    ctx.add_result(
        "keyword",
        _result(
            "keyword", issues=[{"type": "low_ctr_opportunity", "severity": "medium"}]
        ),
    )
    assert len(ctx.all_issues) == 2
    assert len(ctx.all_fixes) == 1


def test_strategy_result_replaces_issues_instead_of_appending():
    ctx = _ctx()
    ctx.add_result(
        "analyzer",
        _result("analyzer", issues=[{"type": "missing_title", "severity": "high"}]),
    )
    ctx.add_result(
        "keyword",
        _result(
            "keyword", issues=[{"type": "striking_distance", "severity": "medium"}]
        ),
    )
    assert len(ctx.all_issues) == 2

    ranked = [
        {"type": "missing_title", "severity": "high", "priority": "high"},
        {"type": "striking_distance", "severity": "medium", "priority": "medium"},
    ]
    ctx.add_result("strategy", _result("strategy", issues=ranked))
    assert len(ctx.all_issues) == 2  # not 4


def test_score_calculation_conservative():
    ctx = _ctx()
    ctx.all_issues = [
        {"severity": "high"},
        {"severity": "high"},
        {"severity": "medium"},
        {"severity": "medium"},
        {"severity": "medium"},
        {"severity": "low"},
        {"severity": "low"},
        {"severity": "low"},
    ]
    score = ctx.calculate_score()
    # Formula since v1.2: high min(50, 3*n), medium min(30, 1*n), low min(20, 0.3*n)
    # 2*3=6 + 3*1=3 + 3*0.3=0.9 = 9.9 -> 90.1
    assert score == 90.1


def test_summary_contains_expected_keys():
    ctx = _ctx()
    ctx.all_issues = [{"severity": "high", "category": "meta"}]
    ctx.completed_at = datetime.utcnow()
    ctx.calculate_score()
    s = ctx.summary()
    for key in (
        "audit_id",
        "project_id",
        "domain",
        "score",
        "issues_total",
        "issues_by_severity",
        "issues_by_category",
    ):
        assert key in s
