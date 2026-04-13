"""
StrategyAgent: prioritizes every real issue collected so far in the
audit context by impact/effort and produces an action plan.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from ..core.event_bus import EventType
from .base import Agent, AgentResult, AgentStatus

logger = logging.getLogger(__name__)


# Effort estimates per issue type (hours). Tuneable.
EFFORT_BY_TYPE: Dict[str, float] = {
    "missing_title": 0.25,
    "short_title": 0.25,
    "long_title": 0.25,
    "missing_meta_description": 0.25,
    "short_meta_description": 0.25,
    "long_meta_description": 0.25,
    "missing_h1": 0.5,
    "multiple_h1": 0.5,
    "missing_viewport": 0.25,
    "missing_html_lang": 0.25,
    "missing_canonical": 0.5,
    "noindex_detected": 0.25,
    "missing_og_title": 0.25,
    "missing_og_image": 1.0,
    "missing_twitter_card": 0.25,
    "images_without_alt": 0.5,
    "missing_organization_schema": 1.5,
    "no_jsonld": 1.5,
    "no_https": 4.0,
    "missing_security_headers": 1.0,
    "slow_response": 8.0,
    "fetch_error": 2.0,
    "low_ctr_opportunity": 1.5,
    "striking_distance": 3.0,
    "poor_lighthouse_performance": 8.0,
    "moderate_lighthouse_performance": 4.0,
    "poor_lcp": 4.0,
    "poor_cls": 2.0,
    "poor_tbt": 6.0,
    "low_lighthouse_seo": 2.0,
    "low_accessibility": 4.0,
}

IMPACT_BY_TYPE: Dict[str, int] = {
    "noindex_detected": 100,
    "no_https": 90,
    "missing_meta_description": 70,
    "missing_title": 80,
    "missing_h1": 40,
    "missing_canonical": 30,
    "missing_organization_schema": 50,
    "missing_og_image": 40,
    "low_ctr_opportunity": 85,
    "striking_distance": 75,
    "slow_response": 70,
    "missing_security_headers": 35,
    "images_without_alt": 25,
    "poor_lighthouse_performance": 90,
    "moderate_lighthouse_performance": 60,
    "poor_lcp": 80,
    "poor_cls": 60,
    "poor_tbt": 70,
    "low_lighthouse_seo": 50,
    "low_accessibility": 40,
}

DEFAULT_EFFORT = 2.0
DEFAULT_IMPACT = 30


class StrategyAgent(Agent):
    @property
    def name(self) -> str:
        return "strategy"

    @property
    def event_type(self) -> EventType:
        return EventType.STRATEGY_COMPLETED

    async def run(self) -> AgentResult:
        start_time = datetime.utcnow()
        result = AgentResult(
            status=AgentStatus.RUNNING,
            agent_name=self.name,
            project_id=self.project_id,
            audit_id=self.audit_id,
        )

        try:
            await self.emit_started()

            # Pull issues from every previous agent in the shared context
            all_issues: List[Dict[str, Any]] = (
                list(self.context.all_issues) if self.context else []
            )

            ranked = self._rank(all_issues)

            # Group into quick-wins, this-week, backlog
            quick_wins = [
                i
                for i in ranked
                if i["effort_hours"] <= 0.5 and i["impact_score"] >= 40
            ]
            this_week = [
                i for i in ranked if i not in quick_wins and i["effort_hours"] <= 4
            ]
            backlog = [i for i in ranked if i not in quick_wins and i not in this_week]

            result.metrics.update(
                {
                    "total_issues": len(all_issues),
                    "quick_wins": len(quick_wins),
                    "this_week": len(this_week),
                    "backlog": len(backlog),
                    "total_effort_hours": round(
                        sum(i["effort_hours"] for i in ranked), 1
                    ),
                    "top_actions": ranked[:10],
                }
            )
            result.issues = ranked  # overwrite with prioritized list
            result.status = AgentStatus.COMPLETED
            result.log_output = (
                f"Prioritized {len(ranked)} issues: "
                f"{len(quick_wins)} quick-wins, {len(this_week)} this-week, {len(backlog)} backlog"
            )
            logger.info(result.log_output)

        except Exception as exc:  # pragma: no cover
            result.status = AgentStatus.FAILED
            result.errors.append(str(exc))
            result.log_output = f"Strategy failed: {exc}"
            logger.exception("Strategy error")

        finally:
            result.duration_seconds = (datetime.utcnow() - start_time).total_seconds()
            await self.emit_result(result)

        return result

    def _rank(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Assign effort + impact + priority and sort by ROI."""
        ranked = []
        for issue in issues:
            copy = dict(issue)
            t = copy.get("type", "")
            effort = EFFORT_BY_TYPE.get(t, DEFAULT_EFFORT)
            impact = IMPACT_BY_TYPE.get(t, DEFAULT_IMPACT)
            sev = copy.get("severity", "low")
            # Severity boosts impact
            impact_boost = {"high": 1.5, "medium": 1.0, "low": 0.6}.get(sev, 1.0)
            adj_impact = impact * impact_boost
            roi = adj_impact / max(effort, 0.1)
            copy["effort_hours"] = effort
            copy["impact_score"] = round(adj_impact, 1)
            copy["roi"] = round(roi, 1)
            if sev == "high" or adj_impact >= 60:
                copy["priority"] = "high"
            elif effort <= 1 or adj_impact >= 35:
                copy["priority"] = "medium"
            else:
                copy["priority"] = "low"
            ranked.append(copy)

        priority_weight = {"high": 0, "medium": 1, "low": 2}
        ranked.sort(key=lambda x: (priority_weight[x["priority"]], -x["roi"]))
        return ranked
