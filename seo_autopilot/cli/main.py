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
@click.option("--db", default=None, help="Pfad zur Audit-Datenbank")
@click.option(
    "--min-treffer",
    default=None,
    type=int,
    help="Ab wie vielen Widerlegungen ein Befundtyp als Muster gilt (Standard: 3)",
)
@click.option(
    "--tage", default=None, type=int, help="Beobachtungsfenster in Tagen (Standard: 30)"
)
def learnings(db, min_treffer, tage):
    """Wiederkehrende Fehlalarme zeigen (Lernschleife).

    Listet Befundtypen, die die Gegenprobe wiederholt widerlegt hat. Taucht ein
    Typ bei mehreren Projekten auf, ist die Pruefregel im Analyzer kaputt.
    """
    from ..learning import (
        FENSTER_TAGE,
        MIN_TREFFER,
        bericht_als_text,
        muster_bericht,
        standard_db_pfad,
    )

    db_pfad = db or standard_db_pfad()
    schwelle = MIN_TREFFER if min_treffer is None else min_treffer
    fenster = FENSTER_TAGE if tage is None else tage

    muster = muster_bericht(db_pfad, min_treffer=schwelle, tage=fenster)
    click.echo(bericht_als_text(muster, tage=fenster, min_treffer=schwelle))


@cli.command()
@click.option(
    "--tage", default=14, help="Nur Meldungen der letzten N Tage (Standard: 14)"
)
def radar(tage):
    """Richtlinien-Radar: neue Google-/KI-Suchrichtlinien und was daran haengt.

    Holt die ueberwachten RSS-Feeds, erkennt Richtlinien-Themen und nennt die
    eigenen Pruefbereiche, die dadurch angepasst werden muessen.
    Bricht nie ab: sind die Feeds nicht erreichbar, gibt es eine Meldung und
    Exit-Code 0.
    """
    from ..policy_radar import analysiere_meldungen, radar_zusammenfassung
    from ..sources.intelligence import IntelligenceFeed

    feed = IntelligenceFeed()
    if not feed.available:
        click.echo(
            "Richtlinien-Radar: feedparser ist nicht installiert — "
            "keine Feeds abrufbar (pip install feedparser)."
        )
        return

    try:
        eintraege = feed.poll_feeds()
    except Exception as exc:  # pragma: no cover - Netzwerk
        logger.warning(f"[radar] Feed-Abruf fehlgeschlagen: {exc}")
        click.echo(f"Richtlinien-Radar: Feeds nicht erreichbar ({exc}).")
        return

    if not eintraege:
        click.echo(
            "Richtlinien-Radar: keine Feed-Meldungen abrufbar "
            "(Feeds leer oder nicht erreichbar)."
        )
        return

    treffer = analysiere_meldungen(eintraege, max_alter_tage=tage)
    click.echo(
        f"Ausgewertet: {len(eintraege)} Feed-Meldungen "
        f"(Fenster: letzte {tage} Tage).\n"
    )
    click.echo(radar_zusammenfassung(treffer))


@cli.command()
@click.option("--db", default="seo_autopilot.db", help="Pfad zur Audit-Datenbank")
@click.option("--projects", default="projects.yaml", help="Pfad zur Projektliste")
@click.option("--tage", default=7, type=int, help="Zeitraum in Tagen (Standard 7)")
@click.option(
    "--html", "html_pfad", default=None, help="HTML-Bericht in diese Datei schreiben"
)
@click.option("--notify/--no-notify", default=False, help="Bericht per Telegram senden")
def weekly(db, projects, tage, html_pfad, notify):
    """Wochenbericht ueber alle Projekte (verstaendliches Deutsch).

    Beispiel:
      seo-autopilot weekly --tage 7 --html reports/woche.html --notify
    """
    from ..weekly_report import als_text, baue_wochenbericht, schreibe_html

    bericht = baue_wochenbericht(db_pfad=db, projects_pfad=projects, tage=tage)
    text = als_text(bericht)
    click.echo(text)

    if html_pfad:
        ziel = schreibe_html(bericht, html_pfad)
        click.echo(f"\nHTML-Bericht geschrieben: {ziel}")

    if notify:
        try:
            from ..notifications.telegram import MAX_MESSAGE_LENGTH, send_plain_message

            # Telegram schneidet lange Nachrichten stillschweigend ab — dann
            # lieber die Empfehlungen weglassen als das letzte Projekt.
            meldung = text
            if len(meldung) > MAX_MESSAGE_LENGTH:
                meldung = als_text(bericht, kompakt=True)
            send_plain_message(meldung)
        except Exception as exc:  # pragma: no cover - Netzwerk
            logger.warning(f"Telegram-Meldung fehlgeschlagen: {exc}")


@cli.command()
@click.option("--db", default=None, help="Pfad zur Audit-Datenbank")
@click.option("--projekt", default=None, help="Nur dieses Projekt anzeigen")
@click.option(
    "--tage", default=30, type=int, help="Zeitfenster in Tagen (Standard: 30)"
)
@click.option(
    "--diff",
    "mit_diff",
    is_flag=True,
    default=False,
    help="Vorher/Nachher-Vergleich je Aenderung anzeigen",
)
@click.option(
    "--nur-offene/--alle",
    default=False,
    help="Nur Aenderungen, die noch in der Website stehen",
)
def changes(db, projekt, tage, mit_diff, nur_offene):
    """Aenderungsbuch: wer hat wann was an der Website geaendert.

    Zeigt eigene (Autopilot) und fremde (Mensch) Aenderungen chronologisch.
    Fremde Eintraege sind die wichtigen: Deren Wirkung darf uns spaeter nicht
    zugerechnet werden.

    Beispiel:
      seo-autopilot changes --projekt joseph --tage 14 --diff
    """
    from ..changelog_book import aenderungen, als_text, standard_db_pfad

    db_pfad = db or standard_db_pfad()
    liste = aenderungen(db_pfad, project_id=projekt, tage=tage, nur_offene=nur_offene)
    click.echo(als_text(liste, tage=tage, mit_diff=mit_diff))


@cli.command()
@click.option("--db", default=None, help="Pfad zur Audit-Datenbank")
@click.option("--projects", default="projects.yaml", help="Pfad zur Projektliste")
@click.option("--projekt", default=None, help="Nur dieses Projekt")
@click.option(
    "--messen/--nur-anzeigen",
    default=False,
    help="Faellige Messungen jetzt durchfuehren (fragt die Search Console ab)",
)
@click.option(
    "--fenster",
    default=None,
    type=int,
    help="Nur dieses Messfenster anzeigen (7, 14, 28 oder 56 Tage)",
)
@click.option(
    "--bilanz",
    "zeige_bilanz",
    is_flag=True,
    default=False,
    help="Trefferquote je Art der Aenderung statt Einzelmessungen",
)
@click.option(
    "--nur-belastbar/--alle",
    default=False,
    help="Messungen ohne verwertbares Ergebnis ausblenden",
)
def wirkung(db, projects, projekt, messen, fenster, zeige_bilanz, nur_belastbar):
    """Wirkungsmessung: hat eine Aenderung tatsaechlich etwas gebracht.

    Vergleicht je Aenderung das Zeitfenster davor mit dem danach (7/14/28/56
    Tage, Search-Console-Daten fuer genau diese Adresse). Ohne ausreichende
    Datenlage wird bewusst KEIN Urteil gefaellt.

    Beispiele:
      seo-autopilot wirkung --messen          # faellige Messungen nachholen
      seo-autopilot wirkung --bilanz          # was wirkt ueberhaupt
      seo-autopilot wirkung --projekt joseph --fenster 28
    """
    import asyncio
    from pathlib import Path

    from ..wirkung import (
        als_text,
        bilanz,
        bilanz_als_text,
        faellige_messungen,
        messungen,
        miss_faellige,
        standard_db_pfad,
    )

    db_pfad = db or standard_db_pfad()

    if messen:
        from ..health import _lade_projekte

        projekt_liste = _lade_projekte(Path(projects))
        if not projekt_liste:
            # Exit-Code, kein stilles return: Im Cron laeuft dieser Befehl aus
            # einem beliebigen Verzeichnis, und `projects.yaml` wird relativ
            # aufgeloest. Ohne `cd` faende er nie eine Projektliste — und
            # wuerde jahrelang "erfolgreich" nichts messen. Genau dieser
            # Fehlertyp (stiller Ausfall) hat dieses Projekt schon zweimal
            # Monate gekostet.
            raise click.ClickException(
                f"Keine Projekte gefunden ({projects}). "
                "Im Cron 'cd /opt/odoo/docs/seo-autopilot &&' voranstellen."
            )
        neue = asyncio.run(miss_faellige(db_pfad, projekt_liste, project_id=projekt))
        click.echo(f"{len(neue)} Messung(en) durchgefuehrt.\n")

    if zeige_bilanz:
        click.echo(bilanz_als_text(bilanz(db_pfad, project_id=projekt)))
        return

    liste = messungen(
        db_pfad,
        project_id=projekt,
        fenster_tage=fenster,
        nur_belastbar=nur_belastbar,
    )
    click.echo(als_text(liste))

    if not messen:
        offen = faellige_messungen(db_pfad, project_id=projekt)
        if offen:
            click.echo(f"\n{len(offen)} Messung(en) faellig — mit '--messen' abrufen.")


@cli.command()
def version():
    """Show version"""
    from .. import __version__

    click.echo(f"SEO Autopilot v{__version__}")


if __name__ == "__main__":
    cli()
