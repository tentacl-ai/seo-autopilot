"""Explicit local demo, authenticated runtime, and newline-delimited MCP stdio."""

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from .api import create_app
from .service import Access, Workspace
from .store import Store, WorkspaceError, now


def from_config(path, initialize=False):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise WorkspaceError("Zugangskonfiguration benötigt Dateirechte 600.")
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise WorkspaceError("Ungültiges Konfigurationsformat.")
    for project, policy in config.get("policies", {}).items():
        if not isinstance(policy, dict):
            raise WorkspaceError("Ungültige Datenpolicy.")
        monthly = policy.get("monthly_limit_microusd", 0)
        if type(monthly) is not int or monthly < 0:
            raise WorkspaceError("Budget muss eine nichtnegative ganze Zahl sein.")
        for ceiling in policy.get("ceilings", {}).values():
            if type(ceiling) is not int or ceiling <= 0:
                raise WorkspaceError("Planreserven müssen positive ganze Zahlen sein.")
        count = policy.get("max_calls_per_run", 50)
        if type(count) is not int or not 1 <= count <= 50:
            raise WorkspaceError("Aufrufdeckel muss zwischen 1 und 50 liegen.")
    hosts = config.get("hosts", ["127.0.0.1", "localhost"])
    if (
        not isinstance(hosts, list)
        or not hosts
        or not all(isinstance(h, str) and h and "*" not in h for h in hosts)
    ):
        raise WorkspaceError("Konkrete Hostfreigaben sind erforderlich.")
    from ..core.project_manager import ProjectManager

    manager = ProjectManager(config["projects_path"])
    projects = {
        p.id: {"name": p.name or p.id, "domain": p.domain}
        for p in manager.list_projects()
    }
    store = Store(config["state_db"])
    if initialize:
        store.initialize()
    from .providers import Provider, existing_assistant

    workspace = Workspace(
        store,
        projects,
        config.get("source_db"),
        Provider(config.get("provider")),
        config.get("policies"),
        existing_assistant if config.get("assistant_enabled", False) else None,
    )
    return (
        workspace,
        Access(config.get("access", [])),
        config.get("hosts", ["127.0.0.1", "localhost"]),
    )


def configured_app(path):
    workspace, access, hosts = from_config(path)
    return create_app(workspace, access, hosts)


def demo(directory):
    """Invented examples only. No production settings or audit startup."""
    state = Store(str(Path(directory) / "workspace.db"))
    state.initialize()
    source = str(Path(directory) / "measurements.db")
    stamp = now()
    with sqlite3.connect(source) as con:
        con.executescript("""
            CREATE TABLE seo_audits (id TEXT,project_id TEXT,started_at TEXT,completed_at TEXT,status TEXT,total_pages INTEGER,score REAL,gsc_clicks INTEGER,gsc_impressions INTEGER);
            CREATE TABLE rangliste (project_id TEXT,woche TEXT,begriff TEXT,geraet TEXT,position REAL,klicks INTEGER,impressionen INTEGER,abgerufen_am TEXT);
            CREATE TABLE ki_sichtbarkeit (project_id TEXT,datum TEXT,ki TEXT,frage TEXT,genannt TEXT,quellen TEXT,wettbewerber TEXT,modell TEXT);
            CREATE TABLE maps_platz (project_id TEXT,woche TEXT,begriff TEXT,ort TEXT,platz INTEGER);
            CREATE TABLE backlinks (project_id TEXT,graph TEXT,domain TEXT,rang INTEGER);
            CREATE TABLE empfehlungen (id TEXT,project_id TEXT,seite TEXT,art TEXT,erstellt_am TEXT,prioritaet REAL,titel TEXT,text TEXT,vorschlag TEXT,status TEXT);
            CREATE TABLE wirkung_messungen (id TEXT,project_id TEXT,ziel_url TEXT,aktion TEXT,fenster_tage INTEGER,gemessen_am TEXT,vorher_klicks INTEGER,nachher_klicks INTEGER,vorher_position REAL,nachher_position REAL,urteil TEXT,notiz TEXT);
        """)
        for i, clicks in enumerate([120, 120]):
            con.execute(
                "INSERT INTO seo_audits VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    str(i),
                    "beispiel",
                    stamp,
                    stamp,
                    "completed",
                    24,
                    81 + i * 3,
                    clicks,
                    6400,
                ),
            )
        for week, offset in [("2026-W45", 2), ("2026-W46", 0)]:
            for query, position, clicks in [
                ("coaching am see", 7, 42),
                ("retreat wochenende", 11, 27),
                ("coaching kosten", 5, 51),
            ]:
                con.execute(
                    "INSERT INTO rangliste VALUES (?,?,?,?,?,?,?,?)",
                    (
                        "beispiel",
                        week,
                        query,
                        "MOBILE",
                        position + offset,
                        clicks,
                        500,
                        stamp,
                    ),
                )
        con.execute(
            "INSERT INTO ki_sichtbarkeit VALUES (?,?,?,?,?,?,?,?)",
            (
                "beispiel",
                stamp[:10],
                "ChatGPT",
                "Wo finde ich Coaching am See?",
                "verlinkt",
                '["https://beispiel.invalid/coaching"]',
                "[]",
                "Demo",
            ),
        )
        con.execute(
            "INSERT INTO maps_platz VALUES (?,?,?,?,?)",
            ("beispiel", "2026-W46", "Coaching", "Beispielort", 4),
        )
        con.execute(
            "INSERT INTO backlinks VALUES (?,?,?,?)",
            ("beispiel", "Demo-Graph", "beispiel-verzeichnis.invalid", 420),
        )
        con.execute(
            "INSERT INTO empfehlungen VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "emp1",
                "beispiel",
                "https://beispiel.invalid/coaching",
                "content",
                stamp,
                0.8,
                "Fragen vor der Anfrage beantworten",
                "Suchdaten zeigen Fragen zu Ablauf und Kosten.",
                "Vorhandene Leistungsseite gezielt ergänzen.",
                "offen",
            ),
        )
        con.execute(
            "INSERT INTO wirkung_messungen VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "wirkung1",
                "beispiel",
                "https://beispiel.invalid/coaching",
                "meta_title",
                28,
                stamp,
                40,
                45,
                8,
                7,
                "zu_wenig_daten",
                "Beispieldaten; keine Aussage über echte Wirkung.",
            ),
        )
    workspace = Workspace(
        state,
        {
            "beispiel": {
                "name": "Coaching am See · Beispiel",
                "domain": "https://beispiel.invalid",
            }
        },
        source,
    )
    access = Access(
        [
            {
                "name": "Lokale Demo",
                "sha256": hashlib.sha256(b"demo").hexdigest(),
                "projects": ["beispiel"],
                "role": "owner",
            }
        ],
        allow_demo=True,
    )
    owner = access.authenticate("demo")
    workspace.save_document(
        owner,
        "beispiel",
        "context",
        "business",
        {
            "text": "Erfundenes Coaching-Angebot am See. Alle Zahlen sind Beispieldaten.",
            "status": "confirmed",
            "source": "Demo",
        },
    )
    workspace.save_document(
        owner,
        "beispiel",
        "context",
        "goal",
        {
            "text": "Passende Anfragen für Wochenend-Retreats.",
            "status": "confirmed",
            "source": "Demo",
        },
    )
    workspace.save_document(
        owner,
        "beispiel",
        "keyword",
        "coaching",
        {
            "query": "coaching am see",
            "intent": "commercial",
            "url": "https://beispiel.invalid/coaching",
            "tags": ["Coaching"],
        },
    )
    return workspace, access


async def stdio(workspace, user):
    from .tools import rpc

    for line in sys.stdin:
        if len(line.encode()) > 300_000:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Request too large"},
            }
        else:
            try:
                response = await rpc(workspace, user, json.loads(line))
            except (ValueError, TypeError):
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="SEO-Arbeitsplatz; kein Scheduler, kein Versand"
    )
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--run-due", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="seo-workspace-") as directory:
        if args.demo:
            workspace, access = demo(directory)
            hosts = None
        elif args.config:
            workspace, access, hosts = from_config(args.config, args.initialize)
            if args.initialize:
                return
        else:
            parser.error("--demo oder --config erforderlich")
        if args.run_due:
            if args.demo:
                parser.error("Geplante Abrufe sind im Demomodus nicht verfügbar")
            user = access.authenticate(os.environ.get("SEO_WORKSPACE_TOKEN", ""))

            async def due():
                return {
                    project["id"]: await workspace.run_due(user, project["id"])
                    for project in workspace.list_projects(user)
                }

            print(json.dumps(asyncio.run(due()), ensure_ascii=False))
        elif args.stdio:
            user = access.authenticate(
                "demo" if args.demo else os.environ.get("SEO_WORKSPACE_TOKEN", "")
            )
            asyncio.run(stdio(workspace, user))
        else:
            import uvicorn

            app = create_app(workspace, access, hosts)
            app.state.demo = args.demo
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
