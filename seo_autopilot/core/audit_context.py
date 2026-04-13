"""
AuditContext: shared state passed between agents within a single audit run.

Each agent reads from the context (e.g. StrategyAgent reads all previous
issues) and writes its own result. After the full pipeline the context
is serialized into the database and used for reports + notifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .project_manager import ProjectConfig


@dataclass
class AuditContext:
    audit_id: str
    project_id: str
    project_config: ProjectConfig
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Per-agent results keyed by agent name ("analyzer", "keyword", ...)
    agent_results: Dict[str, Any] = field(default_factory=dict)

    # Accumulated issues with agent attribution
    all_issues: List[Dict[str, Any]] = field(default_factory=list)
    all_fixes: List[Dict[str, Any]] = field(default_factory=list)

    # Overall metrics
    score: Optional[float] = None
    status: str = "running"  # running | completed | failed
    error: Optional[str] = None

    def add_result(self, agent_name: str, result: Any) -> None:
        """
        Record the result of one agent.

        - "strategy" does not produce new issues, it re-prioritizes existing
          ones; its result.issues replaces all_issues in-place.
        - Every other agent appends its issues/fixes to the shared lists.
        """
        self.agent_results[agent_name] = result

        if agent_name == "strategy":
            ranked = getattr(result, "issues", []) or []
            if ranked:
                self.all_issues = [dict(i) for i in ranked]
        else:
            for issue in getattr(result, "issues", []) or []:
                annotated = dict(issue)
                annotated.setdefault("source_agent", agent_name)
                self.all_issues.append(annotated)

        for fix in getattr(result, "fixes", []) or []:
            annotated = dict(fix)
            annotated.setdefault("source_agent", agent_name)
            self.all_fixes.append(annotated)

    def issues_by_severity(self) -> Dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for issue in self.all_issues:
            sev = (issue.get("severity") or "low").lower()
            counts[sev] = counts.get(sev, 0) + 1
        return counts

    def issues_by_category(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for issue in self.all_issues:
            cat = issue.get("category") or "other"
            out[cat] = out.get(cat, 0) + 1
        return out

    def calculate_score(self) -> float:
        """
        Simple weighted score: 100 - (3*high + 1.5*medium + 0.5*low), floor 0.
        Intentionally conservative so a clean site scores high.
        """
        sev = self.issues_by_severity()
        penalty = 3 * sev["high"] + 1.5 * sev["medium"] + 0.5 * sev["low"]
        self.score = max(0.0, round(100.0 - penalty, 1))
        return self.score

    def summary(self) -> Dict[str, Any]:
        """Concise summary usable for notifications + API responses."""
        return {
            "audit_id": self.audit_id,
            "project_id": self.project_id,
            "project_name": self.project_config.name,
            "domain": self.project_config.domain,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": (
                (self.completed_at - self.started_at).total_seconds()
                if self.completed_at else None
            ),
            "status": self.status,
            "score": self.score,
            "issues_total": len(self.all_issues),
            "issues_by_severity": self.issues_by_severity(),
            "issues_by_category": self.issues_by_category(),
            "fixes_total": len(self.all_fixes),
            "agents": {
                name: {
                    "status": getattr(res, "status", None).value if getattr(res, "status", None) else None,
                    "duration_seconds": getattr(res, "duration_seconds", None),
                    "issues": len(getattr(res, "issues", []) or []),
                    "log": getattr(res, "log_output", ""),
                }
                for name, res in self.agent_results.items()
            },
        }
