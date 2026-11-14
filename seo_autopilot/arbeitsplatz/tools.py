"""MCP tools call the same authorized service as the web interface."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, ValidationError, Field

from .store import WorkspaceError


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project: str = Field(min_length=1, max_length=80)


class SourceArgs(Args):
    section: str
    limit: int = Field(default=200, ge=1, le=2000)


class ListArgs(Args):
    kind: str


class ReadArgs(ListArgs):
    key: str
    revision: int | None = None


class SaveArgs(ListArgs):
    key: str
    payload: dict
    expected_revision: int = Field(default=0, ge=0)


class EstimateArgs(Args):
    key: str


class RunArgs(EstimateArgs):
    request_id: str
    confirmed: bool = False


class ResearchArgs(Args):
    feature: str
    parameters: dict
    request_id: str
    confirmed: bool = False


class ResultArgs(Args):
    request_id: str


class ReportArgs(Args):
    template_key: str | None = None


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


TOOLS = {
    "ranking_comparison": (
        Args,
        "Aktuelle verfügbare Woche und Vorwoche nebeneinander lesen.",
        False,
    ),
    "import_keywords": (
        Args,
        "Vorhandene GSC-Begriffe übernehmen, ohne externe Abrufe.",
        True,
    ),
    "research_log": (Args, "Gespeicherte Recherche und Auftragsstatus lesen.", False),
    "review_result": (
        ResultArgs,
        "Ergebnis eines eigenen bestehenden Bewertungsauftrags abrufen; kein neuer Auftrag.",
        False,
    ),
    "list_projects": (Empty, "Zugängliche Projekte lesen.", False),
    "get_overview": (
        Args,
        "Gespeicherte SEO-Messungen und Quellen lesen; keine Abrufe starten.",
        False,
    ),
    "read_source": (
        SourceArgs,
        "Audit, Rangliste, GSC, KI, Maps, Backlinks, Änderungen oder Wirkung lesen.",
        False,
    ),
    "list_documents": (
        ListArgs,
        "Projektakte, Keywords, Berichte, Vorlagen, Tracker oder Beobachtungen lesen.",
        False,
    ),
    "get_document": (
        ReadArgs,
        "Bestimmte unveränderliche Fassung eines Projekteintrags lesen.",
        False,
    ),
    "save_document": (
        SaveArgs,
        "Projektwissen oder einen manuellen Tracker versioniert speichern. Keine Automatikrechte.",
        True,
    ),
    "content_plan": (
        Args,
        "Gespeicherte Keyword-Zuordnungen nach Absicht und Zielseite gruppieren.",
        False,
    ),
    "estimate_tracker": (
        EstimateArgs,
        "Aufrufe und reservierte Kosten vor einer Messung berechnen.",
        False,
    ),
    "run_tracker": (
        RunArgs,
        "Explizit bestätigte Messung im Betreiberbudget ausführen. Standardmäßig gesperrt.",
        True,
    ),
    "research": (
        ResearchArgs,
        "Bezahlte Recherche mit Cache, Auftragskennung und Betreiberfreigabe.",
        True,
    ),
    "create_report": (
        ReportArgs,
        "Privaten Bericht aus gespeicherten Messungen erstellen; kein Versand.",
        True,
    ),
}


def list_tools():
    return [
        {
            "name": name,
            "description": desc,
            "inputSchema": model.model_json_schema(),
            "annotations": {
                "readOnlyHint": not write,
                "destructiveHint": False,
                "openWorldHint": name in {"run_tracker", "research", "review_result"},
            },
        }
        for name, (model, desc, write) in TOOLS.items()
    ]


async def call(workspace, user, name, arguments):
    if name not in TOOLS:
        raise WorkspaceError("Unbekanntes Werkzeug.")
    model, _, _ = TOOLS[name]
    try:
        args = model.model_validate(arguments).model_dump()
    except ValidationError:
        raise WorkspaceError("Ungültige Werkzeugparameter.")
    methods = {
        "ranking_comparison": workspace.ranking_comparison,
        "import_keywords": workspace.import_keywords,
        "research_log": workspace.research_log,
        "review_result": workspace.review_result,
        "list_projects": workspace.list_projects,
        "get_overview": workspace.overview,
        "read_source": workspace.source,
        "list_documents": workspace.list_documents,
        "get_document": workspace.get_document,
        "save_document": workspace.save_document,
        "content_plan": workspace.content_plan,
        "estimate_tracker": workspace.estimate,
        "run_tracker": workspace.run_tracker,
        "research": workspace.research,
        "create_report": workspace.create_report,
    }
    value = methods[name](user, **args)
    if name in {"run_tracker", "research", "review_result"}:
        value = await value
    return value


async def rpc(workspace, user, message):
    if (
        not isinstance(message, dict)
        or message.get("jsonrpc") != "2.0"
        or not isinstance(message.get("method"), str)
    ):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    method = message["method"]
    if "id" not in message:
        return None
    response = {"jsonrpc": "2.0", "id": message["id"]}
    if method == "initialize":
        return {
            **response,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tentacl-seo", "version": "1.0.0"},
            },
        }
    if method == "ping":
        return {**response, "result": {}}
    if method == "tools/list":
        return {**response, "result": {"tools": list_tools()}}
    if method != "tools/call":
        return {**response, "error": {"code": -32601, "message": "Method not found"}}
    params = message.get("params", {})
    if not isinstance(params, dict):
        return {**response, "error": {"code": -32602, "message": "Invalid params"}}
    try:
        value = await call(
            workspace, user, params.get("name"), params.get("arguments", {})
        )
        return {
            **response,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(value, ensure_ascii=False)}
                ],
                "structuredContent": {"data": value},
                "isError": False,
            },
        }
    except WorkspaceError as exc:
        return {
            **response,
            "result": {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            },
        }
    except Exception:
        return {
            **response,
            "result": {
                "content": [
                    {"type": "text", "text": "Werkzeug vorübergehend nicht verfügbar."}
                ],
                "isError": True,
            },
        }
