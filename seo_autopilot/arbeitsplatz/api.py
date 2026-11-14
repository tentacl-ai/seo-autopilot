"""Authenticated API factory; starting it never starts audit jobs or sends mail."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .service import Access, Workspace
from .store import WorkspaceError


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict
    expected_revision: int = Field(default=0, ge=0)


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=80)
    confirmed: bool = False


class ResearchInput(RunInput):
    parameters: dict = Field(default_factory=dict)


class QuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1000)


def create_app(workspace: Workspace, access: Access, hosts=None):
    app = FastAPI(
        title="SEO-Arbeitsplatz", docs_url=None, redoc_url=None, openapi_url=None
    )
    hosts = hosts or ["127.0.0.1", "localhost", "testserver"]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def security(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Fremder Ursprung nicht zugelassen."}, 403)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Ungültige Anfragegröße."}, 400)
        if length > 300_000:
            return JSONResponse({"detail": "Anfrage zu groß."}, 413)
        if request.method in {"POST", "PUT"}:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 300_000:
                    return JSONResponse({"detail": "Anfrage zu groß."}, 413)
            request._body = bytes(data)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(WorkspaceError)
    async def known_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def unknown_error(request, exc):
        return JSONResponse(
            {"detail": "Arbeitsplatz vorübergehend nicht verfügbar."}, status_code=503
        )

    def principal(request: Request):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise WorkspaceError("Zugang erforderlich.", 401)
        return access.authenticate(auth[7:])

    assets = Path(__file__).parent / "web"

    @app.get("/")
    def index():
        return FileResponse(assets / "index.html")

    @app.get("/mode")
    def mode():
        return {"demo": bool(getattr(app.state, "demo", False))}

    @app.get("/app.js")
    def js():
        return FileResponse(assets / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    def css():
        return FileResponse(assets / "style.css", media_type="text/css")

    @app.get("/fonts/{name}")
    def font(name: str):
        if name not in {"dm-sans.woff2", "OFL.txt"}:
            raise WorkspaceError("Datei nicht gefunden.", 404)
        return FileResponse(assets / "fonts" / name)

    @app.get("/api/projects")
    def projects(user=Depends(principal)):
        return {"projects": workspace.list_projects(user), "role": user.role}

    @app.get("/api/{project}/overview")
    def overview(project: str, user=Depends(principal)):
        return workspace.overview(user, project)

    @app.get("/api/{project}/source/{section}")
    def source(project: str, section: str, limit: int = 200, user=Depends(principal)):
        return workspace.source(user, project, section, limit)

    @app.get("/api/{project}/documents/{kind}")
    def documents(project: str, kind: str, user=Depends(principal)):
        return workspace.list_documents(user, project, kind)

    @app.get("/api/{project}/documents/{kind}/{key}")
    def document(
        project: str,
        kind: str,
        key: str,
        revision: int | None = None,
        user=Depends(principal),
    ):
        return workspace.get_document(user, project, kind, key, revision)

    @app.put("/api/{project}/documents/{kind}/{key}")
    def save(
        project: str, kind: str, key: str, data: DocumentInput, user=Depends(principal)
    ):
        return workspace.save_document(
            user, project, kind, key, data.payload, data.expected_revision
        )

    @app.get("/api/{project}/content-plan")
    def plan(project: str, user=Depends(principal)):
        return workspace.content_plan(user, project)

    @app.get("/api/{project}/ranking-comparison")
    def ranking_comparison(project: str, user=Depends(principal)):
        return workspace.ranking_comparison(user, project)

    @app.post("/api/{project}/keywords/import")
    def import_keywords(project: str, user=Depends(principal)):
        return workspace.import_keywords(user, project)

    @app.get("/api/{project}/research")
    def research_log(project: str, user=Depends(principal)):
        return workspace.research_log(user, project)

    @app.get("/api/{project}/research/{feature}/estimate")
    def research_estimate(project: str, feature: str, user=Depends(principal)):
        return workspace.research_estimate(user, project, feature)

    @app.post("/api/{project}/reviews/{request_id}/result")
    async def reviews_result(project: str, request_id: str, user=Depends(principal)):
        return await workspace.review_result(user, project, request_id)

    @app.get("/api/{project}/trackers/{key}/estimate")
    def estimate(project: str, key: str, user=Depends(principal)):
        return workspace.estimate(user, project, key)

    @app.post("/api/{project}/trackers/{key}/run")
    async def run(project: str, key: str, data: RunInput, user=Depends(principal)):
        return await workspace.run_tracker(
            user, project, key, data.request_id, data.confirmed
        )

    @app.post("/api/{project}/research/{feature}")
    async def research(
        project: str, feature: str, data: ResearchInput, user=Depends(principal)
    ):
        return await workspace.research(
            user, project, feature, data.parameters, data.request_id, data.confirmed
        )

    @app.post("/api/{project}/reports")
    def report(project: str, template_key: str | None = None, user=Depends(principal)):
        return workspace.create_report(user, project, template_key=template_key)

    @app.post("/api/{project}/ask")
    async def ask(project: str, data: QuestionInput, user=Depends(principal)):
        return await workspace.ask(user, project, data.question)

    @app.post("/mcp")
    async def mcp(request: Request, user=Depends(principal)):
        from .tools import rpc

        result = await rpc(workspace, user, await request.json())
        if result is None:
            from starlette.responses import Response

            return Response(status_code=202)
        return JSONResponse(result)

    return app
