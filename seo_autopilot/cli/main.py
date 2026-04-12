"""
CLI für seo-autopilot

Commands:
- seo-autopilot config add
- seo-autopilot config list
- seo-autopilot config remove
- seo-autopilot run
- seo-autopilot api
"""

import click
from ..core.project_manager import ProjectManager
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
    """Verwalte Projekte"""
    pass


@config.command()
@click.option("--id", prompt="Project ID", help="z.B. tentacl-ai")
@click.option("--domain", prompt="Domain", help="z.B. https://tentacl.ai")
@click.option("--name", prompt="Project Name", help="Display name")
@click.option("--adapter-type", default="static", help="static | wordpress | fastapi")
@click.option("--root-path", default=None, help="Für static adapter")
@click.option("--cron", default="0 7 * * 1", help="Cron expression (default: Montag 7 Uhr)")
def add(id, domain, name, adapter_type, root_path, cron):
    """Füge ein Projekt hinzu"""
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

        click.echo(f"✅ Projekt hinzugefügt: {project.id}")
        click.echo(f"   Domain: {project.domain}")
        click.echo(f"   Schedule: {project.schedule_cron}")

    except Exception as e:
        click.echo(f"❌ Fehler: {e}", err=True)


@config.command()
def list():
    """Liste alle Projekte"""
    pm = ProjectManager(settings.PROJECT_CONFIG_PATH)
    projects = pm.list_projects()

    if not projects:
        click.echo("Keine Projekte konfiguriert.")
        return

    click.echo(f"\n📊 {len(projects)} Projekt(e):\n")
    for p in projects:
        status = "✅ " if p.enabled else "⏸  "
        click.echo(f"{status} {p.id}")
        click.echo(f"   Domain: {p.domain}")
        click.echo(f"   Type: {p.adapter_type}")
        click.echo(f"   Schedule: {p.schedule_cron}")
        click.echo(f"   Last run: {p.last_run_at or 'never'}\n")


@config.command()
@click.option("--id", prompt="Project ID", help="z.B. tentacl-ai")
def remove(id):
    """Entferne ein Projekt"""
    pm = ProjectManager(settings.PROJECT_CONFIG_PATH)

    if not click.confirm(f"Wirklich {id} löschen?"):
        return

    try:
        pm.delete_project(id)
        click.echo(f"✅ Projekt gelöscht: {id}")
    except Exception as e:
        click.echo(f"❌ Fehler: {e}", err=True)


@cli.command()
@click.option("--project-id", default=None, help="Nur ein Projekt auditen")
def run(project_id):
    """Starte Audits manuell (synchron, mit vollem Report)."""
    import asyncio
    from ..api.main import run_audit_for_project
    from ..db.database import db

    pm = ProjectManager(settings.PROJECT_CONFIG_PATH)
    projects = [pm.get_project(project_id)] if project_id else pm.get_enabled_projects()
    projects = [p for p in projects if p]

    if not projects:
        click.echo("Keine Projekte gefunden. Erstelle zuerst eine projects.yaml:")
        click.echo("")
        click.echo("  projects:")
        click.echo("    my-site:")
        click.echo("      domain: https://example.com")
        click.echo("      name: My Site")
        click.echo("      enabled: true")
        click.echo("")
        click.echo("Dann: seo-autopilot run --project-id my-site")
        return

    # Zeige welche Features aktiv sind
    click.echo("")
    click.echo("🕷️  Crawler        ✅ aktiv (immer)")
    click.echo(f"📊  GSC            {'✅ aktiv' if settings.GSC_CREDENTIALS_PATH else '⬜ kein Service Account (optional)'}")
    click.echo(f"⚡  PageSpeed      {'✅ aktiv' if settings.PAGESPEED_API_KEY else '⬜ kein API Key (optional)'}")
    click.echo(f"🤖  AI-Fixes       {'✅ aktiv' if settings.CLAUDE_API_KEY else '⬜ kein Claude API Key (optional)'}")
    click.echo(f"📱  Telegram       {'✅ aktiv' if settings.TELEGRAM_BOT_TOKEN else '⬜ kein Bot Token (optional)'}")
    click.echo("")
    click.echo(f"Starte {len(projects)} Audit(s)...")

    async def _run_all():
        await db.initialize()
        try:
            for project in projects:
                click.echo(f"  Auditing {project.id}...")
                audit_id = await run_audit_for_project(project.id)
                click.echo(f"  -> done: {audit_id}")
        finally:
            await db.close()

    asyncio.run(_run_all())
    click.echo("Alle Audits abgeschlossen.")


@cli.command()
@click.option("--host", default=settings.API_HOST)
@click.option("--port", default=settings.API_PORT)
def api(host, port):
    """Starte REST API"""
    import uvicorn
    from ..api.main import app

    click.echo(f"🚀 Starting SEO Autopilot API on {host}:{port}")
    click.echo(f"📖 Docs: http://{host}:{port}/docs")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=settings.LOG_LEVEL.lower(),
    )


@cli.command()
def version():
    """Zeige Version"""
    click.echo("SEO Autopilot v0.3.0")


if __name__ == "__main__":
    cli()
