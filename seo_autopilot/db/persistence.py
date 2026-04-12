"""
Persist an AuditContext into the SQLAlchemy database.

Upserts the project row, inserts a new audit row, and writes one
SEOIssue row per issue plus one SEOKeyword row per top query.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from sqlalchemy import select

from ..core.audit_context import AuditContext
from .database import db
from .models import SEOAudit, SEOIssue, SEOKeyword, SEOProject

logger = logging.getLogger(__name__)


async def persist_audit(ctx: AuditContext) -> str:
    """Write an audit + all its issues/keywords to the DB. Returns audit UUID."""
    await db.initialize()

    audit_uuid = str(uuid.uuid4())

    async with db.get_session() as session:
        # 1. upsert SEOProject row
        existing = await session.scalar(
            select(SEOProject).where(SEOProject.id == ctx.project_id)
        )
        if existing is None:
            existing = SEOProject(
                id=ctx.project_id,
                tenant_id=ctx.project_config.tenant_id,
                domain=ctx.project_config.domain,
                name=ctx.project_config.name,
                adapter_type=ctx.project_config.adapter_type,
                adapter_config=_jsonify(ctx.project_config.adapter_config),
                enabled=ctx.project_config.enabled,
                schedule_cron=ctx.project_config.schedule_cron,
                enabled_sources=list(ctx.project_config.enabled_sources or []),
                source_config=_jsonify(ctx.project_config.source_config),
                notify_channels=list(ctx.project_config.notify_channels or []),
                notify_config=_jsonify(ctx.project_config.notify_config),
            )
            session.add(existing)
        existing.last_run_at = ctx.completed_at
        existing.seo_score = ctx.score

        # 2. insert SEOAudit row
        kw_result = ctx.agent_results.get("keyword")
        kw_metrics = getattr(kw_result, "metrics", {}) if kw_result else {}

        analyzer_result = ctx.agent_results.get("analyzer")
        analyzer_metrics = getattr(analyzer_result, "metrics", {}) if analyzer_result else {}

        audit_row = SEOAudit(
            id=audit_uuid,
            project_id=ctx.project_id,
            tenant_id=ctx.project_config.tenant_id,
            started_at=ctx.started_at,
            completed_at=ctx.completed_at,
            duration_seconds=(
                int((ctx.completed_at - ctx.started_at).total_seconds())
                if ctx.completed_at else None
            ),
            status=ctx.status,
            total_pages=analyzer_metrics.get("pages_crawled"),
            total_keywords=kw_metrics.get("total_keywords"),
            issues_found=len(ctx.all_issues),
            score=ctx.score,
            gsc_clicks=kw_metrics.get("total_clicks"),
            gsc_impressions=kw_metrics.get("total_impressions"),
            gsc_ctr=kw_metrics.get("avg_ctr"),
            gsc_avg_position=kw_metrics.get("avg_position"),
            analytics_data=_jsonify({
                "top_queries": kw_metrics.get("top_queries", []),
                "top_pages": kw_metrics.get("top_pages", []),
            }),
            log_output="\n".join(
                getattr(r, "log_output", "") for r in ctx.agent_results.values()
            ),
            errors=_jsonify([
                {"agent": name, "errors": getattr(r, "errors", [])}
                for name, r in ctx.agent_results.items() if getattr(r, "errors", [])
            ]),
        )
        session.add(audit_row)

        # 3. insert issues
        for issue in ctx.all_issues:
            session.add(SEOIssue(
                id=str(uuid.uuid4()),
                project_id=ctx.project_id,
                audit_id=audit_uuid,
                tenant_id=ctx.project_config.tenant_id,
                category=issue.get("category", "other"),
                type=issue.get("type", "unknown"),
                severity=issue.get("severity", "low"),
                priority=issue.get("priority", "low"),
                title=issue.get("title", "")[:255],
                description=issue.get("description", ""),
                affected_items=_jsonify({
                    "url": issue.get("affected_url"),
                    "keyword": issue.get("keyword"),
                }),
                count=1,
                fix_suggestion=issue.get("fix_suggestion", ""),
                estimated_impact=str(issue.get("estimated_impact", ""))[:255],
            ))

        # 4. insert keywords
        for kw in kw_metrics.get("top_queries", [])[:50]:
            session.add(SEOKeyword(
                id=str(uuid.uuid4()),
                project_id=ctx.project_id,
                audit_id=audit_uuid,
                tenant_id=ctx.project_config.tenant_id,
                query=(kw.get("query") or "")[:255],
                clicks=kw.get("clicks"),
                impressions=kw.get("impressions"),
                position=kw.get("position"),
                ctr=(kw.get("clicks", 0) / max(kw.get("impressions", 1), 1)),
                status="active",
            ))

    logger.info(f"Persisted audit {audit_uuid} with {len(ctx.all_issues)} issues")
    return audit_uuid


def _jsonify(value: Any) -> Any:
    """Coerce values into JSON-serialisable structures for the JSON column."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
