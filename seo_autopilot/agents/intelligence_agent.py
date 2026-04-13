"""
Intelligence Agent — Algorithm Impact Analysis + Alerts.

Detects confirmed algorithm events (2+ sources) and analyzes
the impact per project using Claude API. Sends Telegram alerts
for CRITICAL events with concrete action items.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import settings
from ..core.project_manager import ProjectManager
from ..sources.intelligence import AlgorithmEvent, IntelligenceFeed

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


@dataclass
class ProjectImpact:
    """Impact assessment for a single project."""
    project_id: str
    domain: str
    score: Optional[float] = None
    risk_level: str = "LOW"  # LOW / MEDIUM / HIGH
    actions: List[str] = field(default_factory=list)


@dataclass
class ImpactReport:
    """Full impact report for a confirmed event."""
    event: AlgorithmEvent
    impacts: List[ProjectImpact] = field(default_factory=list)
    analyzed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event.event_id,
            "event_title": self.event.title,
            "priority": self.event.priority,
            "sources": self.event.sources,
            "confirmed": self.event.confirmed,
            "first_seen": self.event.first_seen.isoformat() if self.event.first_seen else None,
            "analyzed_at": self.analyzed_at.isoformat() if self.analyzed_at else None,
            "impacts": [
                {
                    "project_id": imp.project_id,
                    "domain": imp.domain,
                    "score": imp.score,
                    "risk_level": imp.risk_level,
                    "actions": imp.actions,
                }
                for imp in self.impacts
            ],
        }


class IntelligenceAgent:
    """Analyzes algorithm events and generates impact reports."""

    def __init__(
        self,
        feed: Optional[IntelligenceFeed] = None,
        project_manager: Optional[ProjectManager] = None,
    ):
        self.feed = feed or IntelligenceFeed()
        self.project_manager = project_manager
        self._events: List[AlgorithmEvent] = []
        self._reports: List[ImpactReport] = []

    async def poll_feeds(self) -> List[AlgorithmEvent]:
        """Poll feeds and detect confirmed events."""
        items = self.feed.poll_feeds()
        events = self.feed.detect_events(items)
        self._events.extend(events)
        logger.info(f"[intelligence] {len(events)} confirmed events detected")
        return events

    async def check_for_updates(self) -> List[ImpactReport]:
        """Check for critical events and run impact analysis."""
        critical_events = [e for e in self._events if e.confirmed and e.priority == "critical"]
        if not critical_events:
            logger.info("[intelligence] No critical confirmed events — skipping impact analysis")
            return []

        reports = []
        for event in critical_events:
            report = await self.analyze_impact(event)
            reports.append(report)
            await self._send_alert(report)

        self._reports.extend(reports)
        return reports

    async def analyze_impact(self, event: AlgorithmEvent) -> ImpactReport:
        """Analyze the impact of an event on all projects."""
        projects = []
        if self.project_manager:
            projects = self.project_manager.get_enabled_projects()

        impacts = []
        api_calls = 0

        for project in projects[:5]:  # max 5 Claude API calls
            impact = await self._assess_project_impact(event, project)
            impacts.append(impact)
            api_calls += 1

        report = ImpactReport(
            event=event,
            impacts=impacts,
            analyzed_at=datetime.now(timezone.utc),
        )
        logger.info(f"[intelligence] Impact report for '{event.title}': {api_calls} projects analyzed")
        return report

    async def _assess_project_impact(self, event: AlgorithmEvent, project) -> ProjectImpact:
        """Use Claude API to assess impact on a single project."""
        domain = project.domain
        impact = ProjectImpact(project_id=project.id, domain=domain)

        api_key = settings.CLAUDE_API_KEY
        if not api_key:
            logger.warning("[intelligence] No CLAUDE_API_KEY — using heuristic assessment")
            return self._heuristic_assessment(event, project)

        prompt = (
            f"An SEO algorithm event was detected: '{event.title}'.\n"
            f"Keywords: {', '.join(event.keywords)}\n"
            f"Sources: {', '.join(event.sources)}\n\n"
            f"Assess the impact on the website '{domain}'.\n"
            f"Respond with exactly:\n"
            f"RISK: LOW|MEDIUM|HIGH\n"
            f"ACTION1: [concrete action]\n"
            f"ACTION2: [concrete action]\n"
            f"ACTION3: [concrete action]"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": settings.CLAUDE_MODEL,
                        "max_tokens": 256,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )

            if resp.status_code == 200:
                text = resp.json()["content"][0]["text"]
                impact = self._parse_impact_response(text, project.id, domain)
            else:
                logger.warning(f"[intelligence] Claude API error {resp.status_code}")
                impact = self._heuristic_assessment(event, project)

        except Exception as exc:
            logger.warning(f"[intelligence] Claude API call failed: {exc}")
            impact = self._heuristic_assessment(event, project)

        return impact

    def _parse_impact_response(self, text: str, project_id: str, domain: str) -> ProjectImpact:
        """Parse the delimiter-based Claude response."""
        impact = ProjectImpact(project_id=project_id, domain=domain)
        for line in text.strip().splitlines():
            line = line.strip()
            if line.startswith("RISK:"):
                risk = line.split(":", 1)[1].strip().upper()
                if risk in ("LOW", "MEDIUM", "HIGH"):
                    impact.risk_level = risk
            elif line.startswith("ACTION"):
                action = line.split(":", 1)[1].strip() if ":" in line else ""
                if action:
                    impact.actions.append(action)
        return impact

    def _heuristic_assessment(self, event: AlgorithmEvent, project) -> ProjectImpact:
        """Fallback when Claude API is not available."""
        risk = "MEDIUM" if event.priority == "critical" else "LOW"
        return ProjectImpact(
            project_id=project.id,
            domain=project.domain,
            risk_level=risk,
            actions=[
                "Check Google Search Console for ranking changes",
                "Review Core Web Vitals scores",
                "Monitor organic traffic for anomalies",
            ],
        )

    async def _send_alert(self, report: ImpactReport) -> bool:
        """Send Telegram alert for a critical impact report."""
        token = settings.TELEGRAM_BOT_TOKEN
        chat_id = settings.TELEGRAM_CHAT_ID
        if not token or not chat_id:
            logger.info("[intelligence] Telegram not configured — skipping alert")
            return False

        text = self._format_alert(report)
        url = TELEGRAM_API.format(token=token, method="sendMessage")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, data={
                    "chat_id": chat_id,
                    "text": text[:4096],
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": "true",
                })
            if resp.status_code != 200:
                logger.warning(f"[intelligence] Telegram API error {resp.status_code}")
                return False
            logger.info("[intelligence] Telegram alert sent")
            return True
        except Exception as exc:
            logger.warning(f"[intelligence] Telegram send failed: {exc}")
            return False

    def _format_alert(self, report: ImpactReport) -> str:
        """Format the Telegram alert message."""
        event = report.event
        lines = [
            f"*ALGORITHM ALERT — seo-autopilot*",
            f"`{event.title}` detected",
            f"Confirmed by: {' + '.join(event.sources)}",
            "",
            "*Risk per project:*",
        ]

        highest_risk = None
        highest_risk_impact = None
        risk_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

        for imp in report.impacts:
            lines.append(f"- `{imp.domain}` (Score {imp.score or '–'}) \u2192 *{imp.risk_level}*")
            if highest_risk is None or risk_order.get(imp.risk_level, 3) < risk_order.get(highest_risk, 3):
                highest_risk = imp.risk_level
                highest_risk_impact = imp

        if highest_risk_impact and highest_risk_impact.actions:
            lines.append("")
            lines.append(f"*Immediate actions for `{highest_risk_impact.domain}`:*")
            for i, action in enumerate(highest_risk_impact.actions[:3], 1):
                lines.append(f"{i}. {action}")

        lines.append("")
        lines.append("Details: http://localhost:8002/api/intelligence/events")

        return "\n".join(lines)

    def get_events(self) -> List[Dict[str, Any]]:
        """Return all detected events as dicts."""
        return [
            {
                "event_id": e.event_id,
                "title": e.title,
                "priority": e.priority,
                "sources": e.sources,
                "confirmed": e.confirmed,
                "keywords": e.keywords,
                "first_seen": e.first_seen.isoformat() if e.first_seen else None,
                "items_count": len(e.items),
            }
            for e in self._events
        ]

    def get_impact_report(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest impact report for a project."""
        for report in reversed(self._reports):
            for imp in report.impacts:
                if imp.project_id == project_id:
                    return report.to_dict()
        return None
