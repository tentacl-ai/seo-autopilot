"""
FastAPI REST API – seo-autopilot

Multi-Tenant ready REST API für:
- Project Management
- Audit Scheduling
- Real-time Event Monitoring (WebSocket)
- Agent Orchestration
- Report Downloads

Endpoints:
- GET /api/projects
- POST /api/projects
- GET /api/projects/{project_id}
- POST /api/audits/run/{project_id}
- GET /api/audits/{audit_id}
- WS /api/ws/events/{project_id}
"""

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import asyncio
from datetime import datetime
from functools import partial

from ..core.config import settings
from ..core.project_manager import ProjectManager
from ..core.scheduler import scheduler
from ..core.event_bus import event_bus, EventType, Event
from ..core.audit_context import AuditContext
from ..agents.analyzer import AnalyzerAgent
from ..agents.keyword import KeywordAgent
from ..agents.strategy import StrategyAgent
from ..agents.content import ContentAgent
from ..agents.apply import ApplyAgent
from ..db.database import db
from ..db.models import SEOAudit, SEOIssue
from ..db.persistence import persist_audit
from ..reports.html import render_html_report
from ..notifications.telegram import send_audit_notification
from ..agents.intelligence_agent import IntelligenceAgent
from ..sources.intelligence import IntelligenceFeed
from .public_scan import router as public_scan_router

logger = logging.getLogger(__name__)

# Initialize
app = FastAPI(
    title="SEO Autopilot API",
    description="Multi-tenant SEO automation platform",
    version="1.1.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[*settings.CORS_ORIGINS, "https://tentacl.ai"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
project_manager = ProjectManager(settings.PROJECT_CONFIG_PATH)
intelligence_agent = IntelligenceAgent(
    feed=IntelligenceFeed(),
    project_manager=project_manager,
)


# ============================================================
# Pydantic Models
# ============================================================


class CreateProjectRequest(BaseModel):
    """Request zum Erstellen eines Projekts"""

    id: str
    domain: str
    name: str
    adapter_type: str = "static"
    adapter_config: Optional[Dict[str, Any]] = {}
    enabled_sources: Optional[List[str]] = ["gsc"]
    enabled: bool = True
    schedule_cron: str = "0 7 * * 1"
    notify_channels: Optional[List[str]] = ["telegram"]


class ProjectResponse(BaseModel):
    """Projekt-Response"""

    id: str
    domain: str
    name: str
    adapter_type: str
    enabled: bool
    schedule_cron: str
    last_run_at: Optional[str]
    next_run_at: Optional[str]


class AuditRunRequest(BaseModel):
    """Request zum Starten eines Audits"""

    project_id: str
    run_async: bool = True
    auto_fix: bool = False  # Welle 2: force ApplyAgent to apply fixes


class AuditRunResponse(BaseModel):
    """Audit Run Response"""

    audit_id: str
    project_id: str
    status: str
    message: str


# ============================================================
# Lifecycle
# ============================================================


@app.on_event("startup")
async def startup_event():
    """Starte Scheduler + Database beim App-Start"""
    logger.info("Starting SEO Autopilot API...")

    # Initialize database
    try:
        await db.initialize()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    # Starte Scheduler
    await scheduler.start()

    # Lade Projekte und schedule sie
    for project in project_manager.get_enabled_projects():
        try:
            job_id = scheduler.schedule_project(
                project_id=project.id,
                cron_expression=project.schedule_cron,
                callback=partial(run_audit_for_project, project.id),
            )
            logger.info(f"Scheduled: {project.id} -> {job_id}")
        except Exception as e:
            logger.error(f"Failed to schedule {project.id}: {e}")

    # Schedule intelligence feed jobs
    try:
        scheduler.schedule_intelligence_jobs(
            poll_callback=intelligence_agent.poll_feeds,
            check_callback=intelligence_agent.check_for_updates,
        )
        logger.info("Scheduled intelligence jobs")
    except Exception as e:
        logger.error(f"Failed to schedule intelligence jobs: {e}")

    logger.info("✅ SEO Autopilot API started")


@app.on_event("shutdown")
async def shutdown_event():
    """Stoppe Scheduler + Database beim App-Shutdown"""
    logger.info("Shutting down SEO Autopilot API...")
    await scheduler.stop()
    await db.close()


# ============================================================
# Routes: Projects
# ============================================================


@app.get("/api/health")
async def health():
    """Health Check"""
    return {"status": "ok", "version": "1.1.0"}


@app.get("/api/projects", response_model=List[ProjectResponse])
async def list_projects():
    """Liste alle Projekte"""
    projects = project_manager.list_projects()
    result = []

    for p in projects:
        next_run = scheduler.get_next_run(p.id)
        result.append(
            ProjectResponse(
                id=p.id,
                domain=p.domain,
                name=p.name,
                adapter_type=p.adapter_type,
                enabled=p.enabled,
                schedule_cron=p.schedule_cron,
                last_run_at=p.last_run_at.isoformat() if p.last_run_at else None,
                next_run_at=next_run.isoformat() if next_run else None,
            )
        )

    return result


@app.post("/api/projects", response_model=ProjectResponse)
async def create_project(req: CreateProjectRequest):
    """Erstelle ein neues Projekt"""
    try:
        project = project_manager.add_project(
            project_id=req.id,
            domain=req.domain,
            name=req.name,
            adapter_type=req.adapter_type,
            adapter_config=req.adapter_config,
            enabled_sources=req.enabled_sources,
            enabled=req.enabled,
            schedule_cron=req.schedule_cron,
            notify_channels=req.notify_channels,
        )

        # Schedule das Projekt
        if project.enabled:
            scheduler.schedule_project(
                project_id=project.id,
                cron_expression=project.schedule_cron,
                callback=partial(run_audit_for_project, project.id),
            )

        return ProjectResponse(
            id=project.id,
            domain=project.domain,
            name=project.name,
            adapter_type=project.adapter_type,
            enabled=project.enabled,
            schedule_cron=project.schedule_cron,
            last_run_at=None,
            next_run_at=scheduler.get_next_run(project.id).isoformat(),
        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    """Hole ein Projekt"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    next_run = scheduler.get_next_run(project.id)
    return ProjectResponse(
        id=project.id,
        domain=project.domain,
        name=project.name,
        adapter_type=project.adapter_type,
        enabled=project.enabled,
        schedule_cron=project.schedule_cron,
        last_run_at=project.last_run_at.isoformat() if project.last_run_at else None,
        next_run_at=next_run.isoformat() if next_run else None,
    )


# ============================================================
# Routes: Audits
# ============================================================


@app.post("/api/audits/run/{project_id}", response_model=AuditRunResponse)
async def trigger_audit(project_id: str, req: AuditRunRequest):
    """Triggere einen Audit manuell"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        # Starte Audit (async oder sync)
        if req.run_async:
            asyncio.create_task(
                run_audit_for_project(project_id, force_apply=req.auto_fix)
            )
            return AuditRunResponse(
                audit_id="pending",
                project_id=project_id,
                status="queued",
                message=f"Audit queued for {project_id}{' (auto-fix)' if req.auto_fix else ''}",
            )
        else:
            audit_id = await run_audit_for_project(project_id, force_apply=req.auto_fix)
            return AuditRunResponse(
                audit_id=audit_id,
                project_id=project_id,
                status="completed",
                message=f"Audit completed for {project_id}{' (auto-fix applied)' if req.auto_fix else ''}",
            )

    except Exception as e:
        logger.error(f"Audit trigger failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Auto-Fix-Loop (Welle 2): Apply / List / Revert
# ============================================================


@app.post("/api/fixes/apply/{audit_id}")
async def apply_fixes_for_audit(audit_id: str):
    """Re-runt einen abgeschlossenen Audit mit force_apply=True."""
    from sqlalchemy import select

    async with db.get_session() as session:
        row = await session.scalar(select(SEOAudit).where(SEOAudit.id == audit_id))
        if not row:
            raise HTTPException(404, "Audit not found")
        project_id = row.project_id
    new_audit_id = await run_audit_for_project(project_id, force_apply=True)
    return {
        "old_audit_id": audit_id,
        "new_audit_id": new_audit_id,
        "project_id": project_id,
        "message": "Re-audit started with auto-fix enabled",
    }


@app.get("/api/fixes/applied")
async def list_applied_fixes(project_id: Optional[str] = None, limit: int = 50):
    """Listet alle Fixes auf, die je auto-applied wurden."""
    from sqlalchemy import select, desc

    async with db.get_session() as session:
        q = select(SEOIssue).where(SEOIssue.fix_applied_at.isnot(None))
        if project_id:
            q = q.where(SEOIssue.project_id == project_id)
        q = q.order_by(desc(SEOIssue.fix_applied_at)).limit(limit)
        result = await session.scalars(q)
        rows = result.all()
    return [
        {
            "issue_id": r.id,
            "project_id": r.project_id,
            "audit_id": r.audit_id,
            "type": r.type,
            "title": r.title,
            "applied_by": r.applied_by,
            "git_commit_hash": r.git_commit_hash,
            "fix_applied_at": (
                r.fix_applied_at.isoformat() if r.fix_applied_at else None
            ),
            "status": r.status,
        }
        for r in rows
    ]


@app.post("/api/fixes/revert/{commit_hash}")
async def revert_fix(commit_hash: str):
    """Markiert einen Fix als rolled_back. (Git-Revert ist manuell durchzufuehren.)"""
    from sqlalchemy import select, update

    async with db.get_session() as session:
        q = select(SEOIssue).where(SEOIssue.git_commit_hash == commit_hash)
        result = await session.scalars(q)
        rows = result.all()
        if not rows:
            raise HTTPException(404, "No issue found with this commit_hash")
        for r in rows:
            r.applied_by = "rolled_back"
            r.status = "open"
        await session.commit()
    return {
        "commit_hash": commit_hash,
        "issues_marked_rolled_back": len(rows),
        "note": "DB-Status updated. Run 'git revert <hash>' in the project root to revert files.",
    }


# ============================================================
# WebSocket: Real-time Events
# ============================================================


@app.websocket("/api/ws/events/{project_id}")
async def websocket_events(websocket: WebSocket, project_id: str):
    """WebSocket für Audit-Events"""
    await websocket.accept()
    logger.info(f"Client connected: {project_id}")

    # Sende bereits fertige Events
    history = event_bus.get_history(project_id=project_id, limit=10)
    for event in history:
        await websocket.send_json(event.to_dict())

    # Subscribe zu neuen Events
    async def on_event(event: Event):
        if event.project_id == project_id:
            try:
                await websocket.send_json(event.to_dict())
            except Exception as e:
                logger.error(f"WebSocket send failed: {e}")

    event_bus.subscribe(EventType.AUDIT_COMPLETED, on_event)
    event_bus.subscribe(EventType.ISSUES_FOUND, on_event)

    try:
        while True:
            # Keep connection alive
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        logger.info(f"Client disconnected: {project_id}")


# ============================================================
# Background Tasks
# ============================================================


async def run_audit_for_project(
    project_id: str, force_apply: bool = False
) -> Optional[str]:
    """
    Run the full SEO audit pipeline for a single project.

    Pipeline (each agent reads shared AuditContext):
        Analyzer -> Keyword -> Strategy -> Content
    After the pipeline the context is persisted to the database,
    an HTML report is written to disk and notifications are sent.
    """
    project = project_manager.get_project(project_id)
    if not project:
        logger.error(f"Project not found: {project_id}")
        return None

    audit_id = f"audit_{project_id}_{int(datetime.utcnow().timestamp())}"
    logger.info(f"Starting audit: {audit_id}")

    ctx = AuditContext(audit_id=audit_id, project_id=project_id, project_config=project)
    ctx.force_apply = force_apply  # gelesen vom ApplyAgent

    await event_bus.emit(
        Event(
            type=EventType.AUDIT_STARTED,
            project_id=project_id,
            timestamp=datetime.utcnow(),
            data={"audit_id": audit_id, "project_name": project.name},
        )
    )

    agent_classes = [
        AnalyzerAgent,
        KeywordAgent,
        StrategyAgent,
        ContentAgent,
        ApplyAgent,
    ]

    try:
        for AgentCls in agent_classes:
            agent = AgentCls(project_id, audit_id, project, context=ctx)
            result = await agent.run()
            ctx.add_result(agent.name, result)

        ctx.completed_at = datetime.utcnow()
        ctx.status = "completed"
        ctx.calculate_score()

        # Persist to DB (best-effort, non-fatal)
        try:
            await persist_audit(ctx)
        except Exception as exc:
            logger.warning(f"Audit persistence failed: {exc}")

        # Generate HTML report
        try:
            report_path = render_html_report(ctx)
            logger.info(f"Report written: {report_path}")
        except Exception as exc:
            logger.warning(f"Report generation failed: {exc}")
            report_path = None

        # Notify via Telegram (non-fatal)
        try:
            await send_audit_notification(ctx, report_path=report_path)
        except Exception as exc:
            logger.warning(f"Telegram notification failed: {exc}")

        await event_bus.emit(
            Event(
                type=EventType.AUDIT_COMPLETED,
                project_id=project_id,
                timestamp=datetime.utcnow(),
                data=ctx.summary(),
            )
        )

        project_manager.update_project(project_id, last_run_at=datetime.utcnow())

        sev = ctx.issues_by_severity()
        logger.info(
            f"Audit completed: {audit_id} score={ctx.score} "
            f"high={sev['high']} med={sev['medium']} low={sev['low']}"
        )
        return audit_id

    except Exception as exc:
        ctx.status = "failed"
        ctx.error = str(exc)
        ctx.completed_at = datetime.utcnow()
        logger.exception("Audit failed")
        await event_bus.emit(
            Event(
                type=EventType.AUDIT_FAILED,
                project_id=project_id,
                timestamp=datetime.utcnow(),
                data={"audit_id": audit_id, "error": str(exc)},
            )
        )
        return audit_id


# ============================================================
# Routes: Intelligence Feed
# ============================================================


@app.get("/api/intelligence/events")
async def get_intelligence_events():
    """Return all detected algorithm events."""
    return {"events": intelligence_agent.get_events()}


@app.get("/api/intelligence/impact/{project_id}")
async def get_intelligence_impact(project_id: str):
    """Return the latest impact report for a project."""
    report = intelligence_agent.get_impact_report(project_id)
    if not report:
        raise HTTPException(
            status_code=404, detail="No impact report found for this project"
        )
    return report


@app.post("/api/intelligence/poll")
async def manual_poll_intelligence():
    """Manuell Intelligence Feed polling triggern."""
    events = await intelligence_agent.poll_feeds()
    return {
        "status": "ok",
        "events_detected": len(events),
        "events": [
            {
                "event_id": e.event_id,
                "title": e.title,
                "priority": e.priority,
                "confirmed": e.confirmed,
            }
            for e in events
        ],
    }


# Public Scan Router (tentacl.ai/seo-check/)
app.include_router(public_scan_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
