"""
CLI for seo-autopilot

Commands:
- seo-autopilot config add
- seo-autopilot config list
- seo-autopilot config remove
- seo-autopilot run
- seo-autopilot api
"""

import click
from ..core.config import settings
import logging

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@click.group()
def cli():
    """SEO Autopilot – Multi-Tenant SEO Automation"""
    pass


@cli.group()
def config():
    """Manage projects"""
    pass


@config.command()
@click.option("--id", prompt="Project ID", help="e.g. my-website")
@click.option("--domain", prompt="Domain", help="e.g. https://example.com")
@click.option("--name", prompt="Project Name", help="Display name")
@click.option("--adapter-type", default="static", help="static | wordpress | fastapi")
@click.option("--root-path", default=None, help="For static adapter")
@click.option(
    "--cron", default="0 7 * * 1", help="Cron expression (default: Monday 7am)"
)
def add(id, domain, name, adapter_type, root_path, cron):
    """Add a project"""
    from ..core.project_manager import ProjectManager

    pm = ProjectManager(settings.PROJECT_CONFIG_PATH)

    try:
        adapter_config = {}
        if root_path:
            adapter_config["root_path"] = root_path

        project = pm.add_project(
            project_id=id,
            domain=domain,
            name=name,
            adapter_type=adapter_type,
            adapter_config=adapter_config,
            schedule_cron=cron,
        )

        click.echo(f"Project added: {project.id}")
        click.echo(f"  Domain: {project.domain}")
        click.echo(f"  Schedule: {project.schedule_cron}")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@config.command()
def list():
    """List all projects"""
    from ..core.project_manager import ProjectManager

    pm = ProjectManager(settings.PROJECT_CONFIG_PATH)
    projects = pm.list_projects()

    if not projects:
        click.echo("No projects configured.")
        return

    click.echo(f"\n{len(projects)} project(s):\n")
    for p in projects:
        status = "[on] " if p.enabled else "[off]"
        click.echo(f"{status} {p.id}")
        click.echo(f"   Domain: {p.domain}")
        click.echo(f"   Type: {p.adapter_type}")
        click.echo(f"   Schedule: {p.schedule_cron}")
        click.echo(f"   Last run: {p.last_run_at or 'never'}\n")


@config.command()
@click.option("--id", prompt="Project ID", help="e.g. my-website")
def remove(id):
    """Remove a project"""
    from ..core.project_manager import ProjectManager

    pm = ProjectManager(settings.PROJECT_CONFIG_PATH)

    if not click.confirm(f"Delete {id}?"):
        return

    try:
        pm.delete_project(id)
        click.echo(f"Project deleted: {id}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@cli.command()
@click.option("--project-id", default=None, help="Audit a single project")
@click.option(
    "--auto-fix",
    is_flag=True,
    default=False,
    help="Force ApplyAgent to apply fixes even if project.auto_fix_enabled=False",
)
def run(project_id, auto_fix):
    """Run audits (synchronous, with full report)."""
    import asyncio
    from ..api.main import run_audit_for_project
    from ..db.database import db
    from ..core.project_manager import ProjectManager

    pm = ProjectManager(settings.PROJECT_CONFIG_PATH)
    projects = [pm.get_project(project_id)] if project_id else pm.get_enabled_projects()
    projects = [p for p in projects if p]

    if not projects:
        click.echo("No projects found.")
        return

    click.echo(
        f"Starting {len(projects)} audit(s){' WITH --auto-fix' if auto_fix else ''}..."
    )

    async def _run_all():
        await db.initialize()
        try:
            for project in projects:
                click.echo(f"  Auditing {project.id}...")
                audit_id = await run_audit_for_project(project.id, force_apply=auto_fix)
                click.echo(f"  -> done: {audit_id}")
        finally:
            await db.close()

    asyncio.run(_run_all())
    click.echo("All audits completed.")


@cli.command()
@click.option("--host", default=settings.API_HOST)
@click.option("--port", default=settings.API_PORT)
def api(host, port):
    """Start REST API"""
    import uvicorn
    from ..api.main import app

    click.echo(f"Starting SEO Autopilot API on {host}:{port}")
    click.echo(f"Docs: http://{host}:{port}/docs")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=settings.LOG_LEVEL.lower(),
    )


@cli.command()
@click.option("--db", default="seo_autopilot.db", help="Pfad zur Audit-Datenbank")
@click.option("--projects", default="projects.yaml", help="Pfad zur Projektliste")
@click.option("--notify/--no-notify", default=False, help="Befunde per Telegram melden")
def selfcheck(db, projects, notify):
    """Selbstpruefung: laeuft der Autopilot ueberall noch?

    Exit-Code 0 = gesund, 1 = Warnungen, 2 = kritisch (fuer Cron/Monitoring).
    """
    from ..health import run_selfcheck

    report = run_selfcheck(db_pfad=db, projects_pfad=projects)
    text = report.as_text()
    click.echo(text)

    if notify and report.befunde:
        try:
            from ..notifications.telegram import send_plain_message

            send_plain_message(f"SEO-Autopilot Selbstpruefung\n\n{text}")
        except Exception as exc:  # pragma: no cover - Netzwerk
            logger.warning(f"Telegram-Meldung fehlgeschlagen: {exc}")

    raise SystemExit(report.exit_code)


@cli.command()
def version():
    """Show version"""
    from .. import __version__

    click.echo(f"SEO Autopilot v{__version__}")


if __name__ == "__main__":
    cli()
