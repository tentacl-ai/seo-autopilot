"""
FastAPI REST API – seo-autopilot

Multi-Tenant ready REST API für:
- Project Management
- Audit Scheduling
- Real-time Event Monitoring (WebSocket)
- Agent Orchestration
- Report Downloads
- Setup & Auth (single-site mode)

Endpoints:
- GET  /api/setup-status
- POST /api/setup
- POST /api/login
- POST /api/logout
- GET  /api/me
- GET  /api/projects
- POST /api/projects
- GET  /api/projects/{project_id}
- POST /api/audits/run/{project_id}
- GET  /api/audits/{audit_id}
- WS   /api/ws/events/{project_id}
"""

from fastapi import FastAPI, HTTPException, WebSocket, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from pathlib import Path
import logging
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from functools import partial

from ..core.config import settings
from ..core.project_manager import ProjectManager, ProjectConfig
from ..core.scheduler import scheduler
from ..core.event_bus import event_bus, EventType, Event
from ..core.audit_context import AuditContext
from ..agents.analyzer import AnalyzerAgent
from ..agents.keyword import KeywordAgent
from ..agents.strategy import StrategyAgent
from ..agents.content import ContentAgent
from ..db.database import db
from ..db.persistence import persist_audit
from ..reports.html import render_html_report
from ..notifications.telegram import send_audit_notification

logger = logging.getLogger(__name__)

# Initialize
app = FastAPI(
    title="SEO Autopilot API",
    description="Multi-tenant SEO automation platform",
    version="0.1.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
project_manager = ProjectManager(settings.PROJECT_CONFIG_PATH)

# ============================================================
# App Config + Session Storage
# ============================================================

APP_CONFIG_PATH = Path("/app/data/app_config.json")

# In-memory session store: {token: expires_at}
_sessions: Dict[str, datetime] = {}

# Öffentliche Routen (kein Auth nötig)
PUBLIC_ROUTES = {"/api/setup-status", "/api/setup", "/api/login", "/api/health", "/dashboard"}


def _load_app_config() -> Optional[Dict]:
    """Lade App-Config aus Datei"""
    if APP_CONFIG_PATH.exists():
        try:
            return json.loads(APP_CONFIG_PATH.read_text())
        except Exception:
            return None
    return None


def _save_app_config(config: Dict):
    """Speichere App-Config in Datei"""
    APP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    APP_CONFIG_PATH.write_text(json.dumps(config, indent=2))


def _hash_password(password: str) -> str:
    return "sha256:" + hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, stored_hash: str) -> bool:
    expected = _hash_password(password)
    return expected == stored_hash


def _get_token_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _is_valid_token(token: str) -> bool:
    if token not in _sessions:
        return False
    if datetime.utcnow() > _sessions[token]:
        del _sessions[token]
        return False
    return True


async def require_auth(request: Request):
    """Dependency: Auth prüfen"""
    token = _get_token_from_request(request)
    if not token or not _is_valid_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token


# ============================================================
# Pydantic Models
# ============================================================


class SetupRequest(BaseModel):
    site_url: str
    password: str


class LoginRequest(BaseModel):
    password: str


class CreateProjectRequest(BaseModel):
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
    id: str
    domain: str
    name: str
    adapter_type: str
    enabled: bool
    schedule_cron: str
    last_run_at: Optional[str]
    next_run_at: Optional[str]


class AuditRunRequest(BaseModel):
    project_id: str
    run_async: bool = True


class AuditRunResponse(BaseModel):
    audit_id: str
    project_id: str
    status: str
    message: str


# ============================================================
# Lifecycle
# ============================================================


@app.on_event("startup")
async def startup_event():
    logger.info("Starting SEO Autopilot API...")

    try:
        await db.initialize()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    await scheduler.start()

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

    logger.info("SEO Autopilot API started")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down SEO Autopilot API...")
    await scheduler.stop()
    await db.close()


# ============================================================
# Routes: Dashboard HTML
# ============================================================


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the SEO Autopilot Web Dashboard"""
    html_path = Path(__file__).parent / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ============================================================
# Routes: Setup & Auth
# ============================================================


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/setup-status")
async def setup_status():
    """Prüfe ob App konfiguriert ist"""
    config = _load_app_config()
    configured = config is not None and "password_hash" in config
    site_url = config.get("site_url", "") if config else ""
    # Extrahiere nur die Domain für die Anzeige
    from urllib.parse import urlparse
    domain = ""
    if site_url:
        try:
            domain = urlparse(site_url).netloc or site_url
        except Exception:
            domain = site_url
    return {"configured": configured, "domain": domain}


@app.post("/api/setup")
async def setup(req: SetupRequest):
    """Einmalige Einrichtung: URL + Passwort speichern, Projekt anlegen"""
    config = _load_app_config()
    if config and "password_hash" in config:
        raise HTTPException(status_code=400, detail="Already configured")

    if not req.site_url.startswith("http"):
        raise HTTPException(status_code=400, detail="URL muss mit http:// oder https:// beginnen")
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Passwort zu kurz (min. 4 Zeichen)")

    from urllib.parse import urlparse
    domain = urlparse(req.site_url).netloc or req.site_url

    new_config = {
        "site_url": req.site_url,
        "domain": domain,
        "password_hash": _hash_password(req.password),
        "project_id": "main-site",
        "created_at": datetime.utcnow().isoformat(),
    }
    _save_app_config(new_config)

    # Projekt anlegen (falls nicht vorhanden)
    try:
        existing = project_manager.get_project("main-site")
        if not existing:
            project_manager.add_project(
                project_id="main-site",
                domain=req.site_url,
                name=domain,
                adapter_type="static",
                adapter_config={},
                enabled_sources=["gsc"],
                enabled=True,
                schedule_cron="0 7 * * 1",
                notify_channels=["telegram"],
            )
            logger.info(f"[Setup] Projekt 'main-site' angelegt für {domain}")
    except Exception as e:
        logger.warning(f"[Setup] Projekt anlegen fehlgeschlagen: {e}")

    return {"ok": True, "domain": domain}


@app.post("/api/login")
async def login(req: LoginRequest):
    """Login mit Passwort → gibt Session-Token zurück"""
    config = _load_app_config()
    if not config or "password_hash" not in config:
        raise HTTPException(status_code=400, detail="App nicht konfiguriert")

    if not _verify_password(req.password, config["password_hash"]):
        raise HTTPException(status_code=401, detail="Falsches Passwort")

    token = str(uuid.uuid4())
    _sessions[token] = datetime.utcnow() + timedelta(hours=24)
    logger.info("[Auth] Login erfolgreich")
    return {"token": token, "domain": config.get("domain", "")}


@app.post("/api/logout")
async def logout(request: Request):
    """Logout – Token aus Session entfernen"""
    token = _get_token_from_request(request)
    if token and token in _sessions:
        del _sessions[token]
    return {"ok": True}


@app.get("/api/me")
async def me(token: str = Depends(require_auth)):
    """Auth-Check: gibt authenticated: true zurück wenn Token gültig"""
    config = _load_app_config()
    domain = config.get("domain", "") if config else ""
    return {"authenticated": True, "domain": domain}


# ============================================================
# Routes: Projects
# ============================================================


@app.get("/api/projects", response_model=List[ProjectResponse])
async def list_projects(token: str = Depends(require_auth)):
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
async def create_project(req: CreateProjectRequest, token: str = Depends(require_auth)):
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
async def get_project(project_id: str, token: str = Depends(require_auth)):
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
async def trigger_audit(project_id: str, req: AuditRunRequest, token: str = Depends(require_auth)):
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        if req.run_async:
            asyncio.create_task(run_audit_for_project(project_id))
            return AuditRunResponse(
                audit_id="pending",
                project_id=project_id,
                status="queued",
                message=f"Audit queued for {project_id}",
            )
        else:
            audit_id = await run_audit_for_project(project_id)
            return AuditRunResponse(
                audit_id=audit_id,
                project_id=project_id,
                status="completed",
                message=f"Audit completed for {project_id}",
            )
    except Exception as e:
        logger.error(f"Audit trigger failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/audits")
async def list_audits(project_id: Optional[str] = None, limit: int = 50, token: str = Depends(require_auth)):
    try:
        from sqlalchemy import select, desc
        from ..db.models import SEOAudit

        async with db.get_session() as session:
            stmt = select(SEOAudit).order_by(desc(SEOAudit.started_at)).limit(limit)
            if project_id:
                stmt = stmt.where(SEOAudit.project_id == project_id)
            result = await session.execute(stmt)
            audits = result.scalars().all()
            return [
                {
                    "id": a.id,
                    "project_id": a.project_id,
                    "status": a.status,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                    "duration_seconds": a.duration_seconds,
                    "score": a.score,
                    "total_pages": a.total_pages,
                    "total_keywords": a.total_keywords,
                    "issues_found": a.issues_found,
                    "gsc_clicks": a.gsc_clicks,
                    "gsc_impressions": a.gsc_impressions,
                    "gsc_ctr": a.gsc_ctr,
                    "gsc_avg_position": a.gsc_avg_position,
                }
                for a in audits
            ]
    except Exception as e:
        logger.error(f"list_audits failed: {e}")
        return []


@app.get("/api/audits/{audit_id}")
async def get_audit(audit_id: str, token: str = Depends(require_auth)):
    try:
        from sqlalchemy import select
        from ..db.models import SEOAudit, SEOIssue, SEOProject

        async with db.get_session() as session:
            audit = await session.scalar(select(SEOAudit).where(SEOAudit.id == audit_id))
            if not audit:
                raise HTTPException(status_code=404, detail="Audit not found")

            project = await session.scalar(select(SEOProject).where(SEOProject.id == audit.project_id))

            result = await session.execute(
                select(SEOIssue).where(SEOIssue.audit_id == audit_id)
            )
            issues = result.scalars().all()

            return {
                "id": audit.id,
                "project_id": audit.project_id,
                "project_name": project.name if project else audit.project_id,
                "domain": project.domain if project else None,
                "status": audit.status,
                "started_at": audit.started_at.isoformat() if audit.started_at else None,
                "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
                "duration_seconds": audit.duration_seconds,
                "score": audit.score,
                "total_pages": audit.total_pages,
                "total_keywords": audit.total_keywords,
                "issues_found": audit.issues_found,
                "gsc_clicks": audit.gsc_clicks,
                "gsc_impressions": audit.gsc_impressions,
                "gsc_ctr": audit.gsc_ctr,
                "gsc_avg_position": audit.gsc_avg_position,
                "issues": [
                    {
                        "id": i.id,
                        "category": i.category,
                        "type": i.type,
                        "severity": i.severity,
                        "priority": i.priority,
                        "status": i.status,
                        "title": i.title,
                        "description": i.description,
                        "affected_items": i.affected_items,
                        "count": i.count,
                        "fix_suggestion": i.fix_suggestion,
                        "estimated_impact": i.estimated_impact,
                    }
                    for i in issues
                ],
                "fixes": [
                    {
                        "id": i.id,
                        "title": i.title,
                        "fix_suggestion": i.fix_suggestion,
                        "estimated_impact": i.estimated_impact,
                        "category": i.category,
                        "severity": i.severity,
                    }
                    for i in issues if i.fix_suggestion
                ],
                "analytics_data": audit.analytics_data,
                "log_output": audit.log_output,
                "errors": audit.errors,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_audit failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# WebSocket: Real-time Events
# ============================================================


@app.websocket("/api/ws/events/{project_id}")
async def websocket_events(websocket: WebSocket, project_id: str):
    await websocket.accept()
    logger.info(f"Client connected: {project_id}")

    history = event_bus.get_history(project_id=project_id, limit=10)
    for event in history:
        await websocket.send_json(event.to_dict())

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
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        logger.info(f"Client disconnected: {project_id}")


# ============================================================
# Background Tasks
# ============================================================


async def run_audit_for_project(project_id: str) -> Optional[str]:
    """
    Run the full SEO audit pipeline for a single project.
    Pipeline: Analyzer -> Keyword -> Strategy -> Content
    """
    project = project_manager.get_project(project_id)
    if not project:
        logger.error(f"Project not found: {project_id}")
        return None

    audit_id = f"audit_{project_id}_{int(datetime.utcnow().timestamp())}"
    logger.info(f"Starting audit: {audit_id}")

    ctx = AuditContext(audit_id=audit_id, project_id=project_id, project_config=project)

    await event_bus.emit(Event(
        type=EventType.AUDIT_STARTED,
        project_id=project_id,
        timestamp=datetime.utcnow(),
        data={"audit_id": audit_id, "project_name": project.name},
    ))

    agent_classes = [AnalyzerAgent, KeywordAgent, StrategyAgent, ContentAgent]

    try:
        for AgentCls in agent_classes:
            agent = AgentCls(project_id, audit_id, project, context=ctx)
            result = await agent.run()
            ctx.add_result(agent.name, result)

        ctx.completed_at = datetime.utcnow()
        ctx.status = "completed"
        ctx.calculate_score()

        try:
            await persist_audit(ctx)
        except Exception as exc:
            logger.warning(f"Audit persistence failed: {exc}")

        try:
            report_path = render_html_report(ctx)
            logger.info(f"Report written: {report_path}")
        except Exception as exc:
            logger.warning(f"Report generation failed: {exc}")
            report_path = None

        try:
            await send_audit_notification(ctx, report_path=report_path)
        except Exception as exc:
            logger.warning(f"Telegram notification failed: {exc}")

        await event_bus.emit(Event(
            type=EventType.AUDIT_COMPLETED,
            project_id=project_id,
            timestamp=datetime.utcnow(),
            data=ctx.summary(),
        ))

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
        await event_bus.emit(Event(
            type=EventType.AUDIT_FAILED,
            project_id=project_id,
            timestamp=datetime.utcnow(),
            data={"audit_id": audit_id, "error": str(exc)},
        ))
        return audit_id


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
