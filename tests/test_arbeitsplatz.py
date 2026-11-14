"""Behavioral checks for isolation, history, spending and provider transports."""

import hashlib
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from seo_autopilot.arbeitsplatz.api import create_app
from seo_autopilot.arbeitsplatz.service import Access, Principal, Workspace, grid
from seo_autopilot.arbeitsplatz.store import Store, WorkspaceError
from seo_autopilot.arbeitsplatz.tools import rpc
from seo_autopilot.arbeitsplatz.providers import Provider


@pytest.fixture
def workspace(tmp_path):
    store = Store(str(tmp_path / "workspace.db"))
    store.initialize()
    source = str(tmp_path / "measurements.db")
    with sqlite3.connect(source) as con:
        con.execute(
            "CREATE TABLE rangliste (project_id TEXT,woche TEXT,begriff TEXT,geraet TEXT,position REAL,klicks INTEGER)"
        )
        con.execute(
            "INSERT INTO rangliste VALUES ('a','2026-W46','alpha','MOBILE',4,12)"
        )
        con.execute(
            "INSERT INTO rangliste VALUES ('b','2026-W46','private-beta','MOBILE',1,99)"
        )
    return Workspace(
        store,
        {
            "a": {"name": "Alpha", "domain": "https://a.invalid"},
            "b": {"name": "Beta", "domain": "https://b.invalid"},
        },
        source,
    )


@pytest.fixture
def owner():
    return Principal("Owner A", frozenset({"a"}), "owner")


def test_source_is_read_only_and_project_scoped(workspace, owner):
    before = Path(workspace.source_db).read_bytes()
    assert [r["begriff"] for r in workspace.source(owner, "a", "rankings")["rows"]] == [
        "alpha"
    ]
    assert Path(workspace.source_db).read_bytes() == before
    with pytest.raises(WorkspaceError):
        workspace.source(owner, "b", "rankings")
    assert workspace.source(owner, "a", "effects")["status"] == "missing"
    with sqlite3.connect(workspace.source_db) as con:
        assert con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("rangliste",)]


def test_missing_source_is_not_created(workspace, owner, tmp_path):
    workspace.source_db = str(tmp_path / "absent.db")
    assert workspace.source(owner, "a", "audits")["status"] == "missing"
    assert not Path(workspace.source_db).exists()


def test_confirmed_facts_are_protected(workspace, owner):
    first = workspace.save_document(
        owner, "a", "context", "goal", {"text": "Anfragen", "status": "confirmed"}
    )
    editor = Principal("Agent", frozenset({"a"}), "editor")
    with pytest.raises(WorkspaceError):
        workspace.save_document(
            editor, "a", "context", "goal", {"text": "Traffic"}, first["revision"]
        )
    assert (
        workspace.get_document(owner, "a", "context", "goal")["payload"]["text"]
        == "Anfragen"
    )


def test_versions_conflicts_and_private_reports(workspace, owner):
    workspace.save_document(
        owner,
        "a",
        "report",
        "r1",
        {"title": "Alt", "text": "Alte Zahlen", "public": True},
    )
    workspace.save_document(
        owner, "a", "report", "r1", {"title": "Neu", "text": "Neue Zahlen"}, 1
    )
    assert (
        workspace.get_document(owner, "a", "report", "r1", 1)["payload"]["text"]
        == "Alte Zahlen"
    )
    assert (
        workspace.get_document(owner, "a", "report", "r1")["payload"]["visibility"]
        == "private"
    )
    with pytest.raises(WorkspaceError) as err:
        workspace.save_document(
            owner, "a", "report", "r1", {"title": "Alt", "text": ""}, 1
        )
    assert err.value.status == 409
    assert len(workspace.list_documents(owner, "a", "report")) == 1


def test_cross_project_document_ids_are_hidden(workspace, owner):
    beta = Principal("Beta", frozenset({"b"}), "owner")
    workspace.save_document(
        beta, "b", "report", "shared", {"title": "Beta only", "text": "private"}
    )
    for project in ["a", "b"]:
        with pytest.raises(WorkspaceError):
            workspace.get_document(owner, project, "report", "shared")


def test_viewer_cannot_save(workspace):
    with pytest.raises(WorkspaceError):
        workspace.save_document(
            Principal("Reader", frozenset({"a"})),
            "a",
            "context",
            "goal",
            {"text": "changed"},
        )


def test_zero_budget_and_concurrent_reservations(workspace):
    with pytest.raises(WorkspaceError):
        workspace.store.reserve("a", "zero", "x", 1, 0)

    def reserve(i):
        try:
            workspace.store.reserve("a", "r" + str(i), "x" + str(i), 100, 500)
            return True
        except WorkspaceError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(reserve, range(20))) == 5
    assert workspace.store.spending("a")[0]["reserved"] == 500


def test_failed_request_cannot_be_charged_again(workspace):
    workspace.store.reserve("a", "same", "x", 100, 1000)
    workspace.store.finish("a", "same", {"status": "unknown"}, "unknown")
    with pytest.raises(WorkspaceError):
        workspace.store.reserve("a", "same", "x", 100, 1000)
    assert workspace.store.spending("a")[0]["calls"] == 1


def test_keyword_plan_reuses_pages_without_fake_volume(workspace, owner):
    workspace.save_document(
        owner,
        "a",
        "keyword",
        "one",
        {
            "query": "buy alpha",
            "intent": "transactional",
            "url": "https://a.invalid/product",
            "volume": 99999,
        },
    )
    workspace.save_document(
        owner,
        "a",
        "keyword",
        "two",
        {"query": "alpha definition", "intent": "informational", "tags": ["Alpha"]},
    )
    assert {r["action"] for r in workspace.content_plan(owner, "a")} == {
        "improve",
        "review_gap",
    }
    assert all(
        r["payload"]["volume"] is None
        for r in workspace.list_documents(owner, "a", "keyword")
    )
    with pytest.raises(WorkspaceError):
        workspace.save_document(
            owner, "a", "keyword", "third", {"query": "x", "url": "https://b.invalid/"}
        )


def test_grid_coordinates_and_validation():
    raster = grid(47, 9, 3, 1)
    assert len(raster["points"]) == 9
    assert (raster["points"][4]["lat"], raster["points"][4]["lng"]) == (47, 9)
    for lat, lng, size in [(90, 0, 3), (0, 181, 3), (0, 0, 100), (float("nan"), 0, 3)]:
        with pytest.raises(WorkspaceError):
            grid(lat, lng, size)


@pytest.mark.asyncio
async def test_disabled_collection_never_touches_provider(workspace, owner):
    workspace.provider = AsyncMock()
    workspace.save_document(
        owner, "a", "tracker", "search", {"terms": ["alpha"], "type": "serp"}
    )
    with pytest.raises(WorkspaceError):
        await workspace.run_tracker(owner, "a", "search", "no-budget", True)
    workspace.provider.collect.assert_not_called()
    assert workspace.store.spending("a") == []


@pytest.mark.asyncio
async def test_partial_grid_and_no_duplicate_execution(workspace, owner):
    workspace.provider = AsyncMock()
    workspace.provider.collect.side_effect = (
        [{"position": 2}] * 4
        + [RuntimeError("must not leak")]
        + [{"position": None}] * 4
    )
    workspace.policies = {
        "a": {
            "enabled": True,
            "ceilings": {"local": 100},
            "monthly_limit_microusd": 2000,
        }
    }
    workspace.save_document(
        owner,
        "a",
        "tracker",
        "local",
        {"type": "local", "terms": ["alpha"], "lat": 47, "lng": 9, "place_id": "place"},
    )
    result = await workspace.run_tracker(owner, "a", "local", "run1", True)
    assert result["status"] == "partial" and result["successful"] == 8
    assert result["observations"][4]["value"] is None
    with pytest.raises(WorkspaceError):
        await workspace.run_tracker(owner, "a", "local", "run1", True)
    assert workspace.provider.collect.await_count == 9
    assert workspace.store.spending("a")[0]["reserved"] == 900


@pytest.mark.asyncio
async def test_research_cache_avoids_duplicate_spend(workspace, owner):
    workspace.provider = AsyncMock()
    workspace.provider.research.return_value = [
        {"items": [{"keyword": "alpha", "search_volume": 5}]}
    ]
    workspace.policies = {
        "a": {
            "enabled": True,
            "ceilings": {"keyword_ideas": 100},
            "monthly_limit_microusd": 1000,
        }
    }
    first = await workspace.research(
        owner, "a", "keyword_ideas", {"keyword": "alpha"}, "first", True
    )
    second = await workspace.research(
        owner, "a", "keyword_ideas", {"keyword": "alpha"}, "second", False
    )
    assert first["data"] == second["data"] and second["cached"]
    assert workspace.provider.research.await_count == 1


@pytest.mark.asyncio
async def test_http_auth_origin_and_validation(workspace):
    token = secrets.token_hex(32)
    access = Access(
        [
            {
                "sha256": hashlib.sha256(token.encode()).hexdigest(),
                "name": "A",
                "projects": ["a"],
                "role": "owner",
            }
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(workspace, access)),
        base_url="http://testserver",
    ) as client:
        assert (await client.get("/api/projects")).status_code == 401
        headers = {"Authorization": "Bearer " + token}
        assert (await client.get("/api/projects", headers=headers)).json()["projects"][
            0
        ]["id"] == "a"
        assert (await client.get("/api/b/overview", headers=headers)).status_code == 404
        assert (
            await client.get(
                "/api/a/overview", headers={**headers, "Origin": "https://evil.invalid"}
            )
        ).status_code == 403
        bad = await client.put(
            "/api/a/documents/context/goal",
            headers=headers,
            json={"payload": {}, "publish": True},
        )
        assert bad.status_code == 422
        response = await client.get("/api/a/overview", headers=headers)
        assert (
            response.status_code == 200
            and response.headers["cache-control"] == "no-store"
        )
        assert "private-beta" not in response.text


@pytest.mark.asyncio
async def test_mcp_uses_same_service(workspace, owner):
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "read_source",
            "arguments": {"project": "a", "section": "rankings"},
        },
    }
    response = await rpc(workspace, owner, message)
    assert (
        response["result"]["structuredContent"]["data"]["rows"]
        == workspace.source(owner, "a", "rankings")["rows"]
    )
    message["params"]["arguments"]["project"] = "b"
    assert (await rpc(workspace, owner, message))["result"]["isError"]
    assert (
        await rpc(
            workspace, owner, {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        is None
    )
    assert (await rpc(workspace, owner, []))["error"]["code"] == -32600


@pytest.fixture
def provider_identity(monkeypatch):
    for name in ["DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"]:
        monkeypatch.setenv(name, secrets.token_hex(16))


@pytest.mark.asyncio
async def test_provider_endpoint_and_device(provider_identity):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status_code": 20000,
                "tasks": [
                    {
                        "status_code": 20000,
                        "result": [
                            {
                                "items": [
                                    {
                                        "type": "organic",
                                        "rank_group": 4,
                                        "url": "https://a.invalid/page",
                                    }
                                ]
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        row = await Provider(client=client).collect(
            "serp",
            {
                "term": "alpha",
                "language_code": "de",
                "location_code": 2276,
                "device": "mobile",
                "domain": "https://a.invalid",
            },
        )
    assert row["position"] == 4
    assert (
        str(calls[0].url)
        == "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
    )
    import json

    assert json.loads(calls[0].content)[0]["device"] == "mobile"


@pytest.mark.asyncio
async def test_provider_timeout_not_retried(provider_identity):
    calls = []

    async def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WorkspaceError) as error:
            await Provider(client=client).post(
                "backlinks/backlinks/live", {"target": "a.invalid"}
            )
    assert len(calls) == 1 and "private detail" not in str(error.value)


def test_weekly_comparison_preserves_zero_and_missing(workspace, owner):
    with sqlite3.connect(workspace.source_db) as con:
        con.execute(
            "INSERT INTO rangliste VALUES ('a','2026-W45','alpha','MOBILE',5,12)"
        )
    result = workspace.ranking_comparison(owner, "a")
    assert result["week"] == "2026-W46" and result["previous_week"] == "2026-W45"
    row = result["rows"][0]
    assert (row["position_current"], row["position_previous"]) == (4, 5)
    assert row["clicks_current"] == row["clicks_previous"] == 12
    assert row["change"] == "gleich wie Vorwoche"
    with sqlite3.connect(workspace.source_db) as con:
        con.execute("DELETE FROM rangliste WHERE woche='2026-W45'")
    assert (
        workspace.ranking_comparison(owner, "a")["rows"][0]["clicks_previous"] is None
    )


def test_gsc_import_preserves_existing_manual_decisions(workspace, owner):
    workspace.save_document(
        owner,
        "a",
        "keyword",
        "manual",
        {"query": "alpha", "url": "https://a.invalid/service", "intent": "commercial"},
    )
    with sqlite3.connect(workspace.source_db) as con:
        con.execute(
            "CREATE TABLE gsc_historie (project_id TEXT,dimension TEXT,wert TEXT,monat TEXT,impressionen INTEGER,position REAL)"
        )
        con.execute(
            "INSERT INTO gsc_historie VALUES ('a','begriff','alpha','2026-10',100,3)"
        )
        con.execute(
            "INSERT INTO gsc_historie VALUES ('a','begriff','new','2026-10',300,8)"
        )
        con.execute(
            "INSERT INTO gsc_historie VALUES ('b','begriff','private','2026-10',1000,1)"
        )
    assert workspace.import_keywords(owner, "a")["imported"] == 1
    records = workspace.list_documents(owner, "a", "keyword")
    assert {r["payload"]["query"] for r in records} == {"alpha", "new"}
    assert (
        workspace.get_document(owner, "a", "keyword", "manual")["payload"]["intent"]
        == "commercial"
    )
    assert workspace.import_keywords(owner, "a")["imported"] == 0


@pytest.mark.asyncio
async def test_queued_reviews_are_scoped_and_not_reposted(workspace, owner):
    workspace.provider = AsyncMock()
    workspace.provider.research.return_value = {"status": "queued", "task_id": "task-1"}
    workspace.provider.review_result.return_value = {
        "status": "completed",
        "result": [{"items": []}],
    }
    workspace.policies = {
        "a": {
            "enabled": True,
            "ceilings": {"reviews": 100},
            "monthly_limit_microusd": 1000,
        }
    }
    await workspace.research(
        owner, "a", "reviews", {"target": "place_id:example"}, "reviews1", True
    )
    with pytest.raises(WorkspaceError):
        await workspace.research(
            owner, "a", "reviews", {"target": "place_id:example"}, "reviews2", True
        )
    beta = Principal("B", frozenset({"b"}), "owner")
    with pytest.raises(WorkspaceError):
        await workspace.review_result(beta, "b", "reviews1")
    result = await workspace.review_result(owner, "a", "reviews1")
    assert result["data"]["status"] == "completed"
    await workspace.review_result(owner, "a", "reviews1")
    assert workspace.provider.review_result.await_count == 1
    assert workspace.provider.research.await_count == 1


@pytest.mark.asyncio
async def test_review_adapter_posts_once_then_reads_task(provider_identity):
    seen = []

    async def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            data = {
                "status_code": 20000,
                "tasks": [{"status_code": 20100, "id": "task-1"}],
            }
        else:
            data = {
                "status_code": 20000,
                "tasks": [{"status_code": 20000, "result": []}],
            }
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = Provider(client=client)
        task = await provider.research(
            "reviews",
            {"target": "example", "location_code": 2276, "language_code": "de"},
        )
        assert task["task_id"] == "task-1"
        assert (await provider.review_result(task["task_id"]))["status"] == "completed"
    assert [method for method, _ in seen] == ["POST", "GET"]


@pytest.mark.asyncio
async def test_assistant_cached_and_scoped_without_tools(workspace, owner):
    workspace.assistant = AsyncMock(
        return_value="Position 4 aus gespeicherten GSC-Daten."
    )
    first = await workspace.ask(owner, "a", "Wie stehen wir?")
    cached = await workspace.ask(owner, "a", "Wie stehen wir?")
    assert first["answer"] == cached["answer"] and cached["cached"]
    assert workspace.assistant.await_count == 1
    evidence = workspace.assistant.await_args.args[1]
    assert "private-beta" not in str(evidence)
    with pytest.raises(WorkspaceError):
        await workspace.ask(owner, "b", "Wie stehen wir?")


@pytest.mark.asyncio
async def test_scheduled_runs_require_release_and_are_idempotent(workspace, owner):
    from datetime import date

    workspace.provider = AsyncMock()
    workspace.provider.collect.return_value = {"position": 4}
    workspace.policies = {
        "a": {
            "enabled": True,
            "ceilings": {"serp": 100},
            "monthly_limit_microusd": 1000,
        }
    }
    workspace.save_document(
        owner,
        "a",
        "tracker",
        "weekly",
        {"terms": ["alpha"], "type": "serp", "schedule": "weekly"},
    )
    with pytest.raises(WorkspaceError):
        await workspace.run_due(owner, "a", date(2026, 11, 14))
    workspace.policies["a"]["scheduled_allowed"] = True
    first = await workspace.run_due(owner, "a", date(2026, 11, 14))
    second = await workspace.run_due(owner, "a", date(2026, 11, 14))
    assert first[0]["status"] == "completed" and second[0]["status"] == "not_started"
    assert workspace.provider.collect.await_count == 1


def test_short_production_access_and_operators_rejected(workspace, owner):
    access = Access(
        [
            {
                "sha256": hashlib.sha256(b"").hexdigest(),
                "name": "A",
                "projects": ["a"],
                "role": "owner",
            }
        ]
    )
    with pytest.raises(WorkspaceError):
        access.authenticate("")
    with pytest.raises(WorkspaceError):
        workspace.save_document(
            owner,
            "a",
            "tracker",
            "costly",
            {"terms": ["site:a.invalid"], "type": "serp"},
        )


def test_report_template_is_applied_without_changing_source(workspace, owner):
    workspace.save_document(
        owner,
        "a",
        "template",
        "month",
        {"title": "Monatsblick", "text": "Für die Geschäftsführung."},
    )
    report = workspace.create_report(owner, "a", template_key="month")
    assert report["payload"]["title"] == "Monatsblick"
    assert "Für die Geschäftsführung." in report["payload"]["text"]
    assert "alpha" in report["payload"]["text"]


def test_sqlite_backup_restores_document_versions(workspace, owner, tmp_path):
    workspace.save_document(
        owner, "a", "report", "saved", {"title": "Gesichert", "text": "Fassung eins"}
    )
    backup = str(tmp_path / "backup.db")
    with sqlite3.connect(workspace.store.path) as source, sqlite3.connect(
        backup
    ) as target:
        source.backup(target)
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    workspace.save_document(
        owner, "a", "report", "saved", {"title": "Neu", "text": "Fassung zwei"}, 1
    )
    restored = Store(backup)
    assert restored.get("a", "report", "saved")["revision"] == 1
    assert workspace.store.get("a", "report", "saved")["revision"] == 2


def test_private_configuration_and_explicit_initialization(tmp_path):
    import json
    from seo_autopilot.arbeitsplatz.__main__ import from_config

    projects = tmp_path / "projects.yaml"
    projects.write_text(
        "projects:\n  a:\n    name: Alpha\n    domain: https://a.invalid\n    enabled: false\n"
    )
    config = tmp_path / "workspace.conf"
    state = tmp_path / "state.db"
    config.write_text(
        json.dumps(
            {"projects_path": str(projects), "state_db": str(state), "access": []}
        )
    )
    config.chmod(0o644)
    with pytest.raises(WorkspaceError):
        from_config(config)
    config.chmod(0o600)
    workspace, access, _ = from_config(config, initialize=True)
    assert state.exists() and "a" in workspace.projects
    with pytest.raises(WorkspaceError):
        access.authenticate(secrets.token_hex(32))


@pytest.mark.asyncio
async def test_assistant_daily_cap_stops_before_model(workspace, owner):
    workspace.assistant = AsyncMock(return_value="should not run")
    for i in range(20):
        workspace.store.claim_assistant("a", "previous-" + str(i), owner.name)
    with pytest.raises(WorkspaceError) as error:
        await workspace.ask(owner, "a", "Eine neue Frage")
    assert error.value.status == 429
    workspace.assistant.assert_not_called()


def test_large_measurement_keeps_rank_and_marks_excerpt():
    from seo_autopilot.arbeitsplatz.service import bounded_evidence

    result = bounded_evidence(
        {"position": 3, "status": "ranked", "answer": "x" * 50000}
    )
    assert result["position"] == 3 and result["truncated"]
    assert len(result["excerpt"]) <= 1000


def test_stdio_handshake_works_as_separate_process():
    import json
    import subprocess
    import sys

    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        },
    ]
    result = subprocess.run(
        [sys.executable, "-m", "seo_autopilot.arbeitsplatz", "--demo", "--stdio"],
        input="\n".join(json.dumps(m) for m in messages) + "\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3]
    assert len(responses[1]["result"]["tools"]) == 15
    assert responses[2]["result"]["structuredContent"]["data"][0]["id"] == "beispiel"
