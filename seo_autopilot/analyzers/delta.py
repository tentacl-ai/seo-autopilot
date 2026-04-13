"""
Delta / Regression Engine — Audit-over-audit comparison.

Compares the current audit with the previous one and detects:
- New issues (regressions)
- Resolved issues
- Score changes
- CWV trends
- GEO score delta

Turns a one-time tool into a continuous SEO monitoring system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class DeltaReport:
    """Result of comparing two audit runs."""
    current_audit_id: str
    previous_audit_id: str
    score_current: float = 0.0
    score_previous: float = 0.0
    score_delta: float = 0.0

    new_issues: List[Dict[str, Any]] = field(default_factory=list)
    resolved_issues: List[Dict[str, Any]] = field(default_factory=list)
    persistent_issues: List[Dict[str, Any]] = field(default_factory=list)
    regressed_issues: List[Dict[str, Any]] = field(default_factory=list)

    improved_pages: List[Dict[str, Any]] = field(default_factory=list)
    degraded_pages: List[Dict[str, Any]] = field(default_factory=list)

    cwv_changes: Dict[str, Any] = field(default_factory=dict)
    geo_score_delta: float = 0.0

    issues_summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_audit_id": self.current_audit_id,
            "previous_audit_id": self.previous_audit_id,
            "score_current": self.score_current,
            "score_previous": self.score_previous,
            "score_delta": self.score_delta,
            "new_issues_count": len(self.new_issues),
            "resolved_issues_count": len(self.resolved_issues),
            "persistent_issues_count": len(self.persistent_issues),
            "regressed_issues_count": len(self.regressed_issues),
            "improved_pages": self.improved_pages,
            "degraded_pages": self.degraded_pages,
            "cwv_changes": self.cwv_changes,
            "geo_score_delta": self.geo_score_delta,
            "issues_summary": self.issues_summary,
            "is_regression": self.score_delta < -5,
        }

    @property
    def has_regression(self) -> bool:
        """Significant regression: score drops by > 5 points or new critical issues."""
        if self.score_delta < -5:
            return True
        critical_new = [i for i in self.new_issues if i.get("severity") in ("critical", "high")]
        return len(critical_new) >= 2


class DeltaAnalyzer:
    """Compares two audit results."""

    def compare(
        self,
        current_issues: List[Dict[str, Any]],
        previous_issues: List[Dict[str, Any]],
        current_score: float = 0.0,
        previous_score: float = 0.0,
        current_audit_id: str = "",
        previous_audit_id: str = "",
        current_metrics: Optional[Dict[str, Any]] = None,
        previous_metrics: Optional[Dict[str, Any]] = None,
    ) -> DeltaReport:
        """Compares two audit runs and produces a DeltaReport.

        Issues are compared via a fingerprint:
        (type, affected_url) — same issue type on same URL = same issue.
        """
        report = DeltaReport(
            current_audit_id=current_audit_id,
            previous_audit_id=previous_audit_id,
            score_current=current_score,
            score_previous=previous_score,
            score_delta=round(current_score - previous_score, 1),
        )

        current_metrics = current_metrics or {}
        previous_metrics = previous_metrics or {}

        # Create issue fingerprints
        current_fps = {_fingerprint(i): i for i in current_issues}
        previous_fps = {_fingerprint(i): i for i in previous_issues}

        current_keys = set(current_fps.keys())
        previous_keys = set(previous_fps.keys())

        # New issues (in current, not in previous)
        for fp in current_keys - previous_keys:
            report.new_issues.append(current_fps[fp])

        # Resolved issues (in previous, not in current)
        for fp in previous_keys - current_keys:
            report.resolved_issues.append(previous_fps[fp])

        # Persistent issues (in both)
        for fp in current_keys & previous_keys:
            current_issue = current_fps[fp]
            previous_issue = previous_fps[fp]
            report.persistent_issues.append(current_issue)

            # Regression: issue got worse (severity increased)
            if _severity_rank(current_issue.get("severity", "")) > _severity_rank(previous_issue.get("severity", "")):
                report.regressed_issues.append({
                    **current_issue,
                    "previous_severity": previous_issue.get("severity"),
                })

        # Page-level comparison (issues per URL)
        current_by_url = _group_by_url(current_issues)
        previous_by_url = _group_by_url(previous_issues)

        all_urls = set(current_by_url.keys()) | set(previous_by_url.keys())
        for url in all_urls:
            curr_count = len(current_by_url.get(url, []))
            prev_count = len(previous_by_url.get(url, []))
            if curr_count < prev_count:
                report.improved_pages.append({
                    "url": url,
                    "issues_before": prev_count,
                    "issues_after": curr_count,
                    "delta": curr_count - prev_count,
                })
            elif curr_count > prev_count:
                report.degraded_pages.append({
                    "url": url,
                    "issues_before": prev_count,
                    "issues_after": curr_count,
                    "delta": curr_count - prev_count,
                })

        # CWV comparison
        report.cwv_changes = _compare_cwv(current_metrics, previous_metrics)

        # GEO score delta
        current_geo = current_metrics.get("geo_avg_score", 0)
        previous_geo = previous_metrics.get("geo_avg_score", 0)
        if current_geo and previous_geo:
            report.geo_score_delta = round(current_geo - previous_geo, 1)

        # Summary
        report.issues_summary = {
            "total_current": len(current_issues),
            "total_previous": len(previous_issues),
            "new": len(report.new_issues),
            "resolved": len(report.resolved_issues),
            "persistent": len(report.persistent_issues),
            "regressed": len(report.regressed_issues),
        }

        return report

    def generate_alert_message(self, report: DeltaReport) -> Optional[str]:
        """Generates a Telegram alert message on regression.

        Returns None if there is no regression.
        """
        if not report.has_regression:
            return None

        lines = [
            "REGRESSION ALERT — seo-autopilot",
            "",
            f"Score: {report.score_previous} -> {report.score_current} ({report.score_delta:+.1f})",
            "",
        ]

        if report.new_issues:
            lines.append(f"New issues: {len(report.new_issues)}")
            for issue in report.new_issues[:5]:
                lines.append(f"  [{issue.get('severity', '?'):6}] {issue.get('title', '')[:60]}")

        if report.regressed_issues:
            lines.append(f"\nDegraded: {len(report.regressed_issues)}")
            for issue in report.regressed_issues[:3]:
                lines.append(
                    f"  {issue.get('previous_severity', '?')} -> {issue.get('severity', '?')}: "
                    f"{issue.get('title', '')[:50]}"
                )

        if report.degraded_pages:
            lines.append(f"\nDegraded pages: {len(report.degraded_pages)}")
            for page in report.degraded_pages[:3]:
                lines.append(f"  {page['url']}: {page['issues_before']} -> {page['issues_after']} issues")

        return "\n".join(lines)


def _fingerprint(issue: Dict[str, Any]) -> str:
    """Unique fingerprint for an issue: (type, affected_url)."""
    return f"{issue.get('type', '')}::{issue.get('affected_url', '')}"


def _severity_rank(severity: str) -> int:
    """Numeric rank for severity comparison."""
    ranks = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return ranks.get(severity.lower(), 0)


def _group_by_url(issues: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    """Groups issues by affected_url."""
    groups: Dict[str, List[Dict]] = {}
    for issue in issues:
        url = issue.get("affected_url", "")
        if url not in groups:
            groups[url] = []
        groups[url].append(issue)
    return groups


def _compare_cwv(current: Dict, previous: Dict) -> Dict[str, Any]:
    """Compares CWV metrics between two audits."""
    changes = {}
    psi_keys = [
        ("lighthouse_performance", "Performance Score"),
        ("lighthouse_seo", "SEO Score"),
    ]
    for key, label in psi_keys:
        curr_val = current.get(key)
        prev_val = previous.get(key)
        if curr_val is not None and prev_val is not None:
            changes[label] = {
                "current": curr_val,
                "previous": prev_val,
                "delta": curr_val - prev_val,
            }

    # GEO Score
    curr_geo = current.get("geo_avg_score")
    prev_geo = previous.get("geo_avg_score")
    if curr_geo is not None and prev_geo is not None:
        changes["GEO Score"] = {
            "current": curr_geo,
            "previous": prev_geo,
            "delta": round(curr_geo - prev_geo, 1),
        }

    return changes
