"""TrendsAgent: zieht Google-Trends-Daten via PyTrends pro Audit (Welle 3).

Liest aus project.intel_config:
- intel_keywords: list[str]    # max 5
- geo: str                      # default "DE"
- timeframe: str                # default "now 7-d"

Schreibt Ergebnis in:
- ctx.intel_bundle              # TrendBundle (fuer Persistence + Telegram)
- result.metrics                # rising_count, top_count

Skipped wenn keine intel_keywords konfiguriert.
"""

from __future__ import annotations

import logging

from .base import Agent, AgentResult, AgentStatus
from ..core.event_bus import EventType
from ..sources.trends import TrendsSource, TrendBundle

logger = logging.getLogger(__name__)


class TrendsAgent(Agent):
    @property
    def name(self) -> str:
        return "trends"

    @property
    def event_type(self) -> EventType:
        return (
            EventType.APPLY_COMPLETED
        )  # reuse generic, kein eigener event-type noetig

    async def run(self) -> AgentResult:
        result = AgentResult(
            status=AgentStatus.RUNNING,
            agent_name=self.name,
            project_id=self.project_id,
            audit_id=self.audit_id,
        )

        intel_cfg = getattr(self.project_config, "intel_config", None) or {}
        keywords = intel_cfg.get("intel_keywords") or []
        geo = intel_cfg.get("geo", "DE")
        timeframe = intel_cfg.get("timeframe", "now 7-d")

        if not keywords:
            result.status = AgentStatus.SKIPPED
            result.log_output = "no intel_keywords configured — skipping trends fetch"
            return result

        source = TrendsSource(geo=geo, timeframe=timeframe)
        try:
            bundle: TrendBundle = source.fetch(self.project_id, keywords)
            logger.info(
                f"[trends_agent] fetched: rising={len(bundle.rising)} "
                f"interest_kws={len(bundle.interest)} error={bundle.error!r}"
            )
        except Exception as e:
            result.status = AgentStatus.FAILED
            result.errors.append(str(e))
            result.log_output = f"TrendsSource failed: {e}"
            logger.exception("[trends_agent] fetch raised")
            return result

        if self.context is not None:
            self.context.intel_bundle = bundle

        result.metrics = {
            "keywords_tracked": len(bundle.interest),
            "rising_count": len(bundle.rising),
            "trending_count": len(bundle.top),
            "geo": geo,
            "timeframe": timeframe,
            "had_error": bool(bundle.error),
        }
        result.status = AgentStatus.COMPLETED
        if bundle.error:
            result.log_output = f"Trends: {bundle.error}"
        else:
            result.log_output = (
                f"Trends: {len(bundle.interest)} kws, {len(bundle.rising)} rising, "
                f"{len(bundle.top)} trending"
            )
        logger.info(result.log_output)
        return result
