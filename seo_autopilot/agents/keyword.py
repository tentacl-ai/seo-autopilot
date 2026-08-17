"""
KeywordAgent: Real Google Search Console keyword analysis.

Pulls last-28-days Search Analytics from GSC and detects:
- Low-CTR opportunities (rank good, CTR bad)
- High-impression / low-click queries (snippet tuning)
- Near-first-page queries (position 11-20 that could jump to page 1)
- Branded vs. non-branded breakdown
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.event_bus import EventType
from ..sources.base import DataSourceError, SearchAnalytics
from ..sources.ga4 import GA4Analytics, erstelle_quelle as erstelle_ga4_quelle
from ..sources.gsc import GSCDataSource
from .base import Agent, AgentResult, AgentStatus

logger = logging.getLogger(__name__)

LOW_CTR_THRESHOLD = 0.03  # 3%
STRIKING_DISTANCE_MIN = 11  # Positions 11-20 = "Page 2"
STRIKING_DISTANCE_MAX = 20
MIN_IMPRESSIONS = 30  # ignore noise

# GA4: ab wann eine Seite als "Besucher springen sofort ab" gilt.
# Erst ab einer Mindestzahl Sitzungen, sonst meldet eine Seite mit drei
# Besuchern einen Alarm.
HIGH_BOUNCE_THRESHOLD = 70.0  # Prozent
MIN_SESSIONS = 30


class KeywordAgent(Agent):
    """Real GSC-backed keyword research agent."""

    @property
    def name(self) -> str:
        return "keyword"

    @property
    def event_type(self) -> EventType:
        return EventType.KEYWORD_RESEARCH_COMPLETED

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

            analytics = await self._pull_gsc_analytics()
            # GA4 laeuft unabhaengig von GSC — es liefert nie eine Ausnahme,
            # sondern im Zweifel None.
            visitors = await self._pull_ga4_analytics()

            if analytics is None:
                result.status = AgentStatus.SKIPPED
                result.log_output = "GSC data source unavailable or not configured - skipping keyword analysis"
                result.metrics.update(
                    {
                        "gsc_available": False,
                        "total_keywords": 0,
                        "opportunities_found": 0,
                    }
                )
                result.metrics.update(self._ga4_metrics(visitors))
                result.issues = self._find_high_bounce_pages(visitors)
                return result

            keywords = analytics.top_queries
            opportunities = self._find_opportunities(keywords)
            striking_distance = self._find_striking_distance(keywords)
            high_bounce = self._find_high_bounce_pages(visitors)

            result.issues = opportunities + striking_distance + high_bounce

            # Persist raw GSC data in metrics so downstream agents/report use it
            result.metrics.update(
                {
                    "gsc_available": True,
                    "total_keywords": len(keywords),
                    "total_clicks": analytics.total_clicks,
                    "total_impressions": analytics.total_impressions,
                    "avg_ctr": analytics.avg_ctr,
                    "avg_position": analytics.avg_position,
                    "top_queries": keywords[:20],
                    "top_pages": analytics.top_pages[:20],
                    "by_device": analytics.by_device,
                    "opportunities_found": len(opportunities),
                    "striking_distance_count": len(striking_distance),
                }
            )
            result.metrics.update(self._ga4_metrics(visitors))

            result.status = AgentStatus.COMPLETED
            result.log_output = (
                f"Analyzed {len(keywords)} GSC keywords, "
                f"{len(opportunities)} CTR opportunities, "
                f"{len(striking_distance)} striking-distance queries"
            )
            logger.info(result.log_output)

        except DataSourceError as exc:
            # GSC-specific errors are expected (missing permission etc.)
            result.status = AgentStatus.SKIPPED
            result.errors.append(str(exc))
            result.log_output = f"GSC not available: {exc}"
            logger.warning(result.log_output)

        except Exception as exc:  # pragma: no cover
            result.status = AgentStatus.FAILED
            result.errors.append(str(exc))
            result.log_output = f"Keyword agent failed: {exc}"
            logger.exception("Keyword agent error")

        finally:
            result.duration_seconds = (datetime.utcnow() - start_time).total_seconds()
            await self.emit_result(result)

        return result

    # ------------------------------------------------------------------

    async def _pull_gsc_analytics(self) -> Optional[SearchAnalytics]:
        """Load GSC credentials and pull 28-day analytics."""
        source_cfg = (self.project_config.source_config or {}).get("gsc", {})
        creds_path = source_cfg.get("credentials_path")
        property_url = source_cfg.get("property_url") or self.project_config.domain

        if not creds_path:
            logger.info("No GSC credentials configured for project; skipping.")
            return None

        if not Path(creds_path).exists():
            logger.warning(f"GSC credentials file missing: {creds_path}")
            return None

        gsc = GSCDataSource(creds_path)
        await gsc.authenticate()
        return await gsc.pull_analytics(property_url, days=28)

    async def _pull_ga4_analytics(self) -> Optional[GA4Analytics]:
        """Besucherdaten aus GA4 holen — still, wenn nicht konfiguriert.

        Die Quelle darf den Lauf nie abbrechen: Fehlt die Bibliothek, die
        property_id oder die Schlüsseldatei, gibt es hier einfach None.
        """
        if "ga4" not in (self.project_config.enabled_sources or []):
            return None

        quelle = erstelle_ga4_quelle(self.project_config.source_config or {})
        if quelle is None:
            logger.info("[GA4] 'ga4' aktiviert, aber source_config.ga4 fehlt.")
            return None

        if not quelle.available:
            logger.warning(f"[GA4] {quelle.status_text()}")
            return None

        try:
            return await quelle.pull_analytics(self.project_config.domain, days=28)
        except Exception as exc:  # doppelter Boden — GA4 kippt nie den Audit
            logger.warning(f"[GA4] Besucherdaten nicht abrufbar: {exc}")
            return None

    def _ga4_metrics(self, visitors: Optional[GA4Analytics]) -> Dict[str, Any]:
        """GA4-Zahlen für Bericht und Persistenz aufbereiten."""
        if visitors is None:
            return {"ga4_available": False}
        return {
            "ga4_available": True,
            "ga4_users": visitors.total_users,
            "ga4_sessions": visitors.total_sessions,
            "ga4_pageviews": visitors.total_pageviews,
            "ga4_bounce_rate": visitors.bounce_rate,
            "ga4_engagement_rate": visitors.engagement_rate,
            "ga4_organic_sessions": visitors.organic_sessions,
            "ga4_organic_share": visitors.organic_share,
            "ga4_by_channel": visitors.by_channel,
            "ga4_top_pages": visitors.top_pages[:20],
        }

    def _find_high_bounce_pages(
        self, visitors: Optional[GA4Analytics]
    ) -> List[Dict[str, Any]]:
        """Seiten, die zwar Besucher bekommen, sie aber sofort verlieren."""
        if visitors is None:
            return []

        issues: List[Dict[str, Any]] = []
        for seite in visitors.top_pages:
            sitzungen = seite.get("sessions", 0)
            absprung = seite.get("bounce_rate", 0.0)
            if sitzungen < MIN_SESSIONS or absprung < HIGH_BOUNCE_THRESHOLD:
                continue
            issues.append(
                {
                    "category": "keyword",
                    "type": "high_bounce_page",
                    "severity": "medium",
                    "title": f"Hohe Absprungrate auf {seite['page']} ({absprung:.0f}%)",
                    "affected_url": seite["page"],
                    "sessions": sitzungen,
                    "bounce_rate": absprung,
                    "engagement_rate": seite.get("engagement_rate", 0.0),
                    "description": (
                        f"{seite['page']} hatte {sitzungen} Sitzungen, "
                        f"aber {absprung:.0f}% der Besucher springen sofort wieder ab."
                    ),
                    "fix_suggestion": (
                        "Prüfen, ob die Seite hält, was das Suchergebnis verspricht: "
                        "Einstieg, Ladezeit und ein klarer nächster Schritt oben."
                    ),
                }
            )
        return issues

    def _find_opportunities(
        self, keywords: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Low-CTR opportunities: ranking decent but clicks underperforming."""
        issues = []
        for kw in keywords:
            impressions = kw.get("impressions", 0)
            clicks = kw.get("clicks", 0)
            position = kw.get("position", 100)
            if impressions < MIN_IMPRESSIONS:
                continue
            ctr = clicks / impressions if impressions else 0
            if position <= 10 and ctr < LOW_CTR_THRESHOLD:
                issues.append(
                    {
                        "category": "keyword",
                        "type": "low_ctr_opportunity",
                        "severity": "high",
                        "title": f"Low CTR for '{kw['query']}'",
                        "keyword": kw["query"],
                        "position": round(position, 1),
                        "clicks": clicks,
                        "impressions": impressions,
                        "ctr": round(ctr * 100, 2),
                        "description": (
                            f"'{kw['query']}' ranks #{position:.1f} but only gets "
                            f"{clicks} clicks on {impressions} impressions "
                            f"(CTR {ctr * 100:.1f}%)."
                        ),
                        "fix_suggestion": (
                            "Rewrite the page title and meta description. "
                            "Include the exact keyword, add numbers/year, make it enticing."
                        ),
                        "estimated_impact": (
                            f"Target CTR 5% would yield ~{int(impressions * 0.05)} clicks/month"
                        ),
                    }
                )
        return issues

    def _find_striking_distance(
        self, keywords: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Queries on page 2 (pos 11-20) that could be pushed to page 1."""
        issues = []
        for kw in keywords:
            position = kw.get("position", 100)
            impressions = kw.get("impressions", 0)
            if (
                STRIKING_DISTANCE_MIN <= position <= STRIKING_DISTANCE_MAX
                and impressions >= MIN_IMPRESSIONS
            ):
                issues.append(
                    {
                        "category": "keyword",
                        "type": "striking_distance",
                        "severity": "medium",
                        "title": f"Striking distance: '{kw['query']}' (pos {position:.1f})",
                        "keyword": kw["query"],
                        "position": round(position, 1),
                        "impressions": impressions,
                        "description": (
                            f"'{kw['query']}' is on page 2 ({position:.1f}). "
                            f"Moving to top 10 could unlock {impressions} impressions/month."
                        ),
                        "fix_suggestion": (
                            "Improve on-page optimization, add internal links to the ranking page, "
                            "and extend content depth on the primary keyword."
                        ),
                    }
                )
        return issues
