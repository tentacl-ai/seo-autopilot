"""A configured inspection must never become a writing/notification pipeline."""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from seo_autopilot.core.project_manager import ProjectConfig, ProjectManager


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_inspection_persists_findings_without_paid_agents_or_side_effects(
    monkeypatch, configured
):
    from seo_autopilot.api import main as api
    from seo_autopilot import ausfuehrung, identitaet
    from seo_autopilot.agents.base import AgentStatus

    project = ProjectConfig(
        id="inspection",
        name="Inspection",
        domain="https://example.invalid",
        inspection_only=configured,
        notifications_enabled=True,
        notify_channels=["telegram"],
    )
    runs = []
    saved = []

    def agent_class(label):
        class TestAgent:
            name = label

            def __init__(self, *args, **kwargs):
                assert kwargs["context"].inspection_only

            async def run(self):
                runs.append(label)
                return SimpleNamespace(
                    status=AgentStatus.COMPLETED,
                    errors=[],
                    issues=[],
                    fixes=[],
                    metrics={},
                    log_output="",
                )

        return TestAgent

    def forbidden(*args, **kwargs):
        raise AssertionError("inspection attempted a prohibited side effect")

    async def noop(*args, **kwargs):
        pass

    async def persist(ctx):
        saved.append((ctx.inspection_only, ctx.status))

    for label in ["Analyzer", "Keyword", "Strategy"]:
        monkeypatch.setattr(api, label + "Agent", agent_class(label))
    for label in ["Content", "Apply", "Trends"]:
        monkeypatch.setattr(api, label + "Agent", forbidden)
    monkeypatch.setattr(api.project_manager, "get_project", lambda _: project)
    monkeypatch.setattr(api.project_manager, "update_project", lambda *a, **k: None)
    monkeypatch.setattr(identitaet, "sicherstellen", noop)
    monkeypatch.setattr(api.event_bus, "emit", noop)
    monkeypatch.setattr(api, "persist_audit", persist)
    monkeypatch.setattr(api, "render_html_report", lambda _: None)
    monkeypatch.setattr(api, "send_audit_notification", forbidden)
    monkeypatch.setattr(ausfuehrung, "erledigte_schliessen", forbidden)
    audit = await api.run_audit_for_project(
        "inspection", inspection_only=not configured
    )
    assert runs == ["Analyzer", "Keyword", "Strategy"]
    assert saved == [(True, "completed")]
    assert api.LAUF_STATUS[audit] == "completed"
    with pytest.raises(ValueError):
        await api.run_audit_for_project(
            "inspection", force_apply=True, inspection_only=not configured
        )


def test_configured_inspection_survives_normal_project_save(tmp_path):
    path = tmp_path / "projects.yaml"
    manager = ProjectManager(str(path))
    manager.add_project(
        "readonly", "https://example.invalid", "Test", inspection_only=True
    )
    manager.update_project("readonly", name="Updated")
    assert ProjectManager(str(path)).get_project("readonly").inspection_only is True


def test_cli_refuses_inspection_with_force_apply():
    from seo_autopilot.cli.main import cli

    result = CliRunner().invoke(cli, ["run", "--nur-pruefen", "--auto-fix"])
    assert result.exit_code == 2
    assert "schließen sich aus" in result.output


def test_inspection_wrapper_passes_explicit_mode(monkeypatch, tmp_path):
    from seo_autopilot import betriebsjob, betriebssicherheit

    monkeypatch.setattr(
        ProjectManager,
        "get_project",
        lambda *args: SimpleNamespace(id="test", enabled=True),
    )
    captured = []
    monkeypatch.setattr(betriebsjob, "ausfuehren", lambda *a: captured.append(a) or 0)
    assert (
        betriebsjob.main(
            ["audit-lesend", "--projekt", "test", "--state-dir", str(tmp_path)]
        )
        == 0
    )
    command, name, state = captured[0]
    assert command[-1] == "--nur-pruefen" and name == "audit-lesend-test"
    normalized = betriebssicherheit.cron_fuer_pruefung(
        "35 4 * * * python -m seo_autopilot.betriebsjob audit-lesend --projekt test\n"
    )
    assert "cli.main run --project-id test --nur-pruefen" in normalized
