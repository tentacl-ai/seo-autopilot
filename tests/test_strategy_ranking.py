"""StrategyAgent ranking / ROI tests."""

import asyncio

from seo_autopilot.agents.base import AgentResult, AgentStatus
from seo_autopilot.agents.strategy import StrategyAgent
from seo_autopilot.core.audit_context import AuditContext
from seo_autopilot.core.project_manager import ProjectConfig


def _ctx_with_issues(issues):
    cfg = ProjectConfig(id="t", domain="https://example.com", name="Example")
    ctx = AuditContext(audit_id="a1", project_id="t", project_config=cfg)
    ctx.all_issues = list(issues)
    return ctx


def test_strategy_sorts_highest_priority_first():
    issues = [
        {"type": "images_without_alt", "severity": "low"},
        {"type": "missing_title", "severity": "high"},
        {"type": "missing_canonical", "severity": "low"},
        {"type": "low_ctr_opportunity", "severity": "high", "keyword": "x"},
    ]
    ctx = _ctx_with_issues(issues)
    cfg = ctx.project_config
    agent = StrategyAgent("t", "a1", cfg, context=ctx)
    result = asyncio.get_event_loop().run_until_complete(agent.run())
    assert result.status == AgentStatus.COMPLETED
    ranked = result.issues
    assert ranked[0]["severity"] == "high"
    assert ranked[0]["priority"] == "high"
    # every issue was annotated with effort + impact + roi
    for i in ranked:
        assert "effort_hours" in i
        assert "impact_score" in i
        assert "roi" in i


def test_quick_wins_identified():
    issues = [
        {"type": "missing_title", "severity": "high"},
        {"type": "slow_response", "severity": "medium"},
    ]
    ctx = _ctx_with_issues(issues)
    agent = StrategyAgent("t", "a1", ctx.project_config, context=ctx)
    result = asyncio.get_event_loop().run_until_complete(agent.run())
    assert result.metrics["quick_wins"] >= 1
    assert result.metrics["total_effort_hours"] > 0
