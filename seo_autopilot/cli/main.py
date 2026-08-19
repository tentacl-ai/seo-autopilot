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


def _projektliste_pfad(angabe=None):
    """Pfad der Projektliste — absolut, nicht vom Arbeitsverzeichnis abhängig.

    `projects.yaml` relativ zu laden hat schon zweimal dazu geführt, dass ein
    Cron-Eintrag ohne vorangestelltes `cd` gar nichts tat. Die Settings kennen
    den absoluten Pfad ohnehin; eine ausdrückliche Angabe schlägt ihn.
    """
    if angabe:
        return angabe
    return settings.PROJECT_CONFIG_PATH


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
@click.option("--projects", default=None, help="Pfad zur Projektliste")
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

        projekt_liste = _lade_projekte(Path(_projektliste_pfad(projects)))
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
@click.option("--projects", default=None, help="Pfad zur Projektliste")
@click.option("--projekt", default=None, help="Nur dieses Projekt")
@click.option(
    "--tage", default=28, type=int, help="Auswertungszeitraum in Tagen (Standard: 28)"
)
def wert(projects, projekt, tage):
    """Geschaeftswert: was die Seiten dem Kunden tatsaechlich einbringen.

    Rechnet Besucher gegen Anfragen und den hinterlegten Wert je Anfrage.
    Fehlt die Angabe, wird NICHT geschaetzt — dann zeigt der Befehl, welche
    Zahlen beim Kunden noch fehlen.

    Beispiel:
      seo-autopilot wert --projekt joseph
    """
    import asyncio
    from pathlib import Path

    from ..geschaeftswert import (
        als_text,
        bewerte_seiten,
        fehlende_angaben,
        lies_ziele,
    )
    from ..health import _lade_projekte

    alle = _lade_projekte(Path(_projektliste_pfad(projects)))
    if not alle:
        raise click.ClickException(
            f"Keine Projekte gefunden ({_projektliste_pfad(projects)})."
        )

    ausgewaehlt = {projekt: alle[projekt]} if projekt and projekt in alle else alle
    offen = fehlende_angaben(ausgewaehlt)

    alle_bewertet = []
    for pid, cfg in ausgewaehlt.items():
        ziele, waehrung = lies_ziele(cfg or {})
        if not [z for z in ziele if z.vollstaendig]:
            continue
        seiten = asyncio.run(_seiten_kennzahlen(pid, cfg or {}, tage))
        if not seiten:
            click.echo(f"[{pid}] keine Besucherdaten abrufbar — uebersprungen.")
            continue
        alle_bewertet.extend(bewerte_seiten(seiten, ziele, waehrung))

    click.echo(als_text(alle_bewertet, offen))


async def _seiten_kennzahlen(pid, cfg, tage):
    """Besucher je Seite aus GA4 holen.

    Anfragen bleiben vorerst 0: Welches Ereignis eine Anfrage ist, unterscheidet
    sich je Kunde und muss mit dem Geschaeftswert zusammen erfasst werden. Bis
    dahin zeigt der Bericht ehrlich Besucher ohne Anfragen statt erfundener
    Abschluesse.
    """
    quellen = cfg.get("enabled_sources") or []
    if "ga4" not in quellen:
        return []
    konfig = (cfg.get("source_config") or {}).get("ga4") or {}
    property_id = konfig.get("property_id")
    credentials = konfig.get("credentials_path")
    if not property_id or not credentials:
        return []
    try:
        from ..sources.ga4 import GA4DataSource

        quelle = GA4DataSource(str(credentials), str(property_id))
        daten = await quelle.pull_analytics(cfg.get("domain", ""), days=tage)
    except Exception as exc:
        logger.warning(f"[wert] {pid}: GA4 nicht abrufbar: {exc}")
        return []
    if not daten:
        return []
    return [
        {
            "url": eintrag.get("page") or eintrag.get("url") or "",
            "besucher": int(eintrag.get("users") or eintrag.get("sessions") or 0),
            "anfragen": 0,
        }
        for eintrag in (daten.top_pages or [])
    ]


@cli.command()
@click.option("--db", default=None, help="Pfad zur Audit-Datenbank")
@click.option("--projects", default="projects.yaml", help="Pfad zur Projektliste")
@click.option("--projekt", default=None, help="Nur dieses Projekt")
@click.option(
    "--anzahl", default=10, type=int, help="Wie viele Massnahmen (Standard 10)"
)
def chancen(db, projects, projekt, anzahl):
    """Chancen-Motor: womit fange ich an.

    Bewertet die Befunde des letzten Audits nach Geschaeftswert mal Potenzial
    mal Sicherheit geteilt durch Aufwand. Fehlt der Geschaeftswert, wird nach
    Besuchern gewichtet — und das im Bericht ausdruecklich gesagt.

    Beispiel:
      seo-autopilot chancen --projekt joseph --anzahl 5
    """
    import sqlite3
    from pathlib import Path

    from ..chancen import als_text, bewerte_chancen
    from ..health import _lade_projekte
    from ..wirkung import standard_db_pfad

    db_pfad = db or standard_db_pfad()
    alle = _lade_projekte(Path(_projektliste_pfad(projects)))
    if not alle:
        raise click.ClickException(
            f"Keine Projekte gefunden ({_projektliste_pfad(projects)})."
        )

    ausgewaehlt = (
        [projekt]
        if projekt
        else [p for p, c in alle.items() if (c or {}).get("enabled", True)]
    )

    gesamt = []
    con = sqlite3.connect(db_pfad)
    con.row_factory = sqlite3.Row
    try:
        for pid in ausgewaehlt:
            befunde = _letzte_befunde(con, pid)
            if not befunde:
                click.echo(f"[{pid}] kein Audit mit Befunden gefunden.")
                continue
            # Sichtbarkeit je Seite dazuholen: Ohne Position und Besucher
            # unterscheidet der Motor die Befunde praktisch nur nach Aufwand.
            seitenwerte = _sichtbarkeit_je_seite(alle.get(pid) or {}, pid)
            for b in befunde:
                treffer = seitenwerte.get(b["url"]) or seitenwerte.get(
                    b["url"].rstrip("/")
                )
                if treffer:
                    b["position"] = treffer.get("position")
                    b["besucher"] = treffer.get("besucher", 0)
            gesamt.extend(
                bewerte_chancen(
                    befunde, projekt=pid, db_pfad=db_pfad, seitenwerte=seitenwerte
                )
            )
    finally:
        con.close()

    gesamt.sort(key=lambda c: c.punkte, reverse=True)
    click.echo(als_text(gesamt, anzahl=anzahl))


def _sichtbarkeit_je_seite(cfg, pid, tage=28):
    """Klicks und Durchschnittsposition je Adresse aus der Search Console.

    Liefert `{url: {"besucher": int, "position": float}}`. Ohne Search Console
    kommt ein leeres Verzeichnis zurueck — der Chancen-Motor faellt dann
    sichtbar auf reine Aufwandssortierung zurueck, statt eine Sichtbarkeit zu
    erfinden, die niemand gemessen hat.
    """
    import asyncio
    from datetime import date, timedelta

    quellen = cfg.get("enabled_sources") or []
    if "gsc" not in quellen:
        return {}
    konfig = (cfg.get("source_config") or {}).get("gsc") or {}
    property_url = konfig.get("property_url")
    credentials = konfig.get("credentials_path")
    if not property_url or not credentials:
        return {}

    async def _hole():
        from ..sources.gsc import GSCDataSource

        quelle = GSCDataSource(str(credentials))
        if not await quelle.authenticate():
            return {}
        # Die Search Console hinkt ein paar Tage hinterher.
        ende = date.today() - timedelta(days=3)
        start = ende - timedelta(days=tage)
        antwort = (
            quelle.service.searchanalytics()
            .query(
                siteUrl=str(property_url),
                body={
                    "startDate": start.isoformat(),
                    "endDate": ende.isoformat(),
                    "dimensions": ["page"],
                    "rowLimit": 500,
                },
            )
            .execute()
        )
        return {
            # Einblendungen statt Klicks als Sichtbarkeitsmassstab: Klicks
            # sind bei kleinen Websites zweistellig und damit zu grob, um
            # Seiten zu unterscheiden. Einblendungen messen ausserdem die
            # Nachfrage, nicht nur den bisherigen Erfolg.
            r["keys"][0]: {
                "besucher": int(r.get("impressions", 0)),
                "position": round(float(r.get("position", 0.0)), 2),
                "klicks": int(r.get("clicks", 0)),
            }
            for r in antwort.get("rows", [])
        }

    try:
        return asyncio.run(_hole())
    except Exception as exc:
        logger.warning(f"[chancen] {pid}: Sichtbarkeit nicht abrufbar: {exc}")
        return {}


def _letzte_befunde(con, project_id):
    """Befunde des juengsten abgeschlossenen Audits eines Projekts."""
    audit = con.execute(
        "select id from seo_audits where project_id=? "
        "order by started_at desc limit 1",
        (project_id,),
    ).fetchone()
    if not audit:
        return []
    # Die betroffene Adresse steht nicht in einer eigenen Spalte, sondern als
    # JSON in `affected_items` — ein leerer Wert ist normal (Befunde, die die
    # ganze Website betreffen, etwa robots.txt).
    import json

    rows = con.execute(
        "select type as typ, title as titel, affected_items, severity "
        "from seo_issues where audit_id=?",
        (audit["id"],),
    ).fetchall()

    befunde = []
    for r in rows:
        url = ""
        roh = r["affected_items"]
        if roh:
            try:
                daten = json.loads(roh)
                if isinstance(daten, dict):
                    url = str(daten.get("url") or "")
                elif isinstance(daten, list) and daten:
                    erstes = daten[0]
                    url = str(erstes.get("url") if isinstance(erstes, dict) else erstes)
            except (ValueError, TypeError):
                url = ""
        befunde.append(
            {
                "type": r["typ"],
                "title": r["titel"],
                "url": url,
                "position": None,
                "severity": r["severity"],
            }
        )
    return befunde


@cli.command()
@click.option("--db", default=None, help="Pfad zur Audit-Datenbank")
@click.option("--projekt", default=None, help="Nur dieses Projekt")
@click.option("--ja", "freigeben", default=None, help="Diese Kennung freigeben")
@click.option("--nein", "ablehnen", default=None, help="Diese Kennung ablehnen")
@click.option("--notiz", default="", help="Begruendung zur Entscheidung")
@click.option(
    "--alle-gesperrten/--nur-offene",
    default=False,
    help="Auch bereits entschiedene gesperrte Vorschlaege zeigen",
)
@click.option("--notify/--no-notify", default=False, help="Offene per Telegram melden")
def freigabe(db, projekt, freigeben, ablehnen, notiz, alle_gesperrten, notify):
    """Freigaben: was der Autopilot vorgelegt hat und noch nicht tun darf.

    Ohne Argumente zeigt der Befehl die offenen Vorschlaege. Mit --ja/--nein
    wird entschieden; es genuegen die ersten Zeichen der Kennung.

    Beispiel:
      seo-autopilot freigabe
      seo-autopilot freigabe --ja 3f2a91c4 --notiz "geprueft, passt"
    """
    from ..ausfuehrung import (
        STATUS_ABGELEHNT,
        STATUS_FREIGEGEBEN,
        als_text,
        entscheiden,
        freigaben,
        standard_db_pfad,
        veraltete,
    )

    db_pfad = db or standard_db_pfad()

    if freigeben or ablehnen:
        kennung = freigeben or ablehnen
        ziel = STATUS_FREIGEGEBEN if freigeben else STATUS_ABGELEHNT
        offen = freigaben(db_pfad, project_id=projekt)
        treffer = [f for f in offen if f.id.startswith(kennung)]
        if not treffer:
            raise click.ClickException(
                f"Keine offene Freigabe mit Kennung {kennung!r}."
            )
        if len(treffer) > 1:
            raise click.ClickException(
                f"{len(treffer)} Freigaben passen auf {kennung!r} — mehr Zeichen angeben."
            )
        eintrag = treffer[0]
        entscheiden(db_pfad, eintrag.id, ziel, von="robert", notiz=notiz)
        wort = "freigegeben" if freigeben else "abgelehnt"
        click.echo(f"{wort}: {eintrag.titel}")
        if freigeben:
            click.echo("Hinweis: Die Aenderung wird beim naechsten Lauf ausgefuehrt.")
        return

    offen = freigaben(db_pfad, project_id=projekt, nur_gesperrte=alle_gesperrten)
    text = als_text(offen, mit_vorschlag=True)
    click.echo(text)

    alt = veraltete(db_pfad)
    if alt:
        click.echo(
            f"\n{len(alt)} Vorschlag/Vorschlaege sind aelter als 30 Tage — "
            "die Seite kann sich seitdem geaendert haben."
        )

    if notify and offen:
        try:
            from ..notifications.telegram import MAX_MESSAGE_LENGTH, send_plain_message

            meldung = f"SEO-Autopilot: {len(offen)} Freigabe(n) offen\n\n{text}"
            send_plain_message(meldung[:MAX_MESSAGE_LENGTH])
        except Exception as exc:  # pragma: no cover - Netzwerk
            logger.warning(f"Telegram-Meldung fehlgeschlagen: {exc}")


@cli.command()
@click.option("--projects", default=None, help="Pfad zur Projektliste")
def betrieb(projects):
    """Betriebsarten: was der Autopilot bei welchem Projekt tun darf.

    Zeigt je Projekt, ob er nur beobachtet, alles zur Freigabe legt oder
    Unbedenkliches selbst ausfuehrt.
    """
    from pathlib import Path

    from ..ausfuehrung import (
        BETRIEBSARTEN,
        GESPERRT,
        betriebsart_klartext,
        betriebsart_von,
    )
    from ..health import _lade_projekte

    alle = _lade_projekte(Path(_projektliste_pfad(projects)))
    if not alle:
        raise click.ClickException(
            f"Keine Projekte gefunden ({_projektliste_pfad(projects)})."
        )

    click.echo("Betriebsart je Projekt\n")
    for pid, cfg in alle.items():
        if not (cfg or {}).get("enabled", True):
            continue
        art = betriebsart_von(cfg or {})
        click.echo(f"  {pid:<16} {betriebsart_klartext(art)}")

    click.echo(
        "\nUmstellen in projects.yaml je Projekt:  betriebsart: "
        f"{' | '.join(BETRIEBSARTEN)}"
    )
    click.echo(
        f"\n{len(GESPERRT)} Eingriffe laufen NIE automatisch — auch nicht im "
        "Autopilot-Modus.\nSie werden immer zur Freigabe vorgelegt:"
    )
    for typ, grund in sorted(GESPERRT.items()):
        click.echo(f"  {typ:<28} {grund}")


@cli.command()
@click.option("--projects", default=None, help="Pfad zur Projektliste")
@click.option("--projekt", default=None, help="Nur dieses Projekt")
@click.option(
    "--gegen",
    default=None,
    help="Wettbewerber-Domains, per Komma getrennt (statt aus projects.yaml)",
)
@click.option(
    "--seiten", default=10, type=int, help="Wie viele Seiten je Domain (Standard 10)"
)
def wettbewerb(projects, projekt, gegen, seiten):
    """Wettbewerbsvergleich mit dem eigenen Crawler — ohne Datenanbieter.

    Vergleicht Inhaltstiefe, strukturierte Daten und Meta-Angaben gegen die
    hinterlegten Wettbewerber. Fremde robots.txt wird befolgt.

    NICHT messbar: fremde Platzierungen und Verlinkungen — dafuer braucht es
    einen Datenanbieter wie DataForSEO.

    Beispiel:
      seo-autopilot wettbewerb --projekt joseph
      seo-autopilot wettbewerb --projekt joseph --gegen https://a.de,https://b.de
    """
    import asyncio
    from pathlib import Path

    from ..health import _lade_projekte
    from ..wettbewerb import als_text, vergleiche, wettbewerber_von

    alle = _lade_projekte(Path(_projektliste_pfad(projects)))
    if not alle:
        raise click.ClickException(
            f"Keine Projekte gefunden ({_projektliste_pfad(projects)})."
        )

    ausgewaehlt = (
        {projekt: alle[projekt]}
        if projekt and projekt in alle
        else {p: c for p, c in alle.items() if (c or {}).get("enabled", True)}
    )

    for pid, cfg in ausgewaehlt.items():
        cfg = cfg or {}
        liste = (
            [w.strip() for w in gegen.split(",") if w.strip()]
            if gegen
            else wettbewerber_von(cfg)
        )
        domain = cfg.get("domain", "")
        if not domain:
            continue
        click.echo(f"\n=== {pid} ===")
        ergebnis = asyncio.run(vergleiche(domain, liste, max_seiten=seiten))
        click.echo(als_text(ergebnis))


@cli.command()
def version():
    """Show version"""
    from .. import __version__

    click.echo(f"SEO Autopilot v{__version__}")


@cli.command()
@click.option("--db", default=None, help="Pfad zur Audit-Datenbank")
@click.option("--projects", default=None, help="Pfad zur Projektliste")
@click.option("--projekt", default=None, help="Nur dieses Projekt")
@click.option(
    "--importieren/--nur-anzeigen",
    default=False,
    help="Historie jetzt aus der Search Console holen",
)
@click.option(
    "--monate",
    default=16,
    type=int,
    help="Wie weit zurueck (max. 16 — weiter gibt Google nicht her)",
)
@click.option(
    "--alles-neu",
    is_flag=True,
    default=False,
    help="Auch bereits archivierte Monate erneut holen",
)
@click.option(
    "--top", default=10, type=int, help="Wie viele Gewinner/Verlierer je Liste"
)
@click.option("--export", "export_pfad", default=None, help="Als CSV herausschreiben")
def historie(db, projects, projekt, importieren, monate, alles_neu, top, export_pfad):
    """Langzeit-Historie aus der Search Console (bis 16 Monate).

    Google gibt nur 16 Monate heraus — was heute nicht geholt wird, ist spaeter
    verloren. Einmal importiert, bleibt ein Monat dauerhaft im eigenen Archiv.

    Beispiele:
      seo-autopilot historie --importieren            # alle Projekte, 16 Monate
      seo-autopilot historie --projekt tentacl-ai     # Bericht ansehen
      seo-autopilot historie --export historie.csv    # fuer Excel
    """
    import asyncio
    from pathlib import Path

    from ..historie import (
        bericht_text,
        exportiere_csv,
        importiere,
        monatsreihe,
        standard_db_pfad,
    )
    from ..health import _lade_projekte

    db_pfad = db or standard_db_pfad()
    projekt_liste = _lade_projekte(Path(_projektliste_pfad(projects)))
    if not projekt_liste:
        # Exit-Code statt stillem return: Im Cron laeuft der Befehl aus einem
        # beliebigen Verzeichnis. Ohne Projektliste wuerde er sonst jahrelang
        # "erfolgreich" nichts tun.
        raise click.ClickException(
            f"Keine Projekte gefunden ({projects}). "
            "Im Cron 'cd /opt/odoo/docs/seo-autopilot &&' voranstellen."
        )

    ziele = {projekt: projekt_liste[projekt]} if projekt else projekt_liste
    if projekt and projekt not in projekt_liste:
        raise click.ClickException(
            f"Projekt '{projekt}' steht nicht in der Projektliste."
        )

    if importieren:
        for pid, konfig in ziele.items():
            ergebnis = asyncio.run(
                importiere(db_pfad, pid, konfig, monate=monate, alles_neu=alles_neu)
            )
            if not ergebnis.erfolgreich:
                click.echo(f"{pid}: {ergebnis.fehler}")
                continue
            click.echo(
                f"{pid}: {ergebnis.monate_geholt} Monat(e) geholt, "
                f"{ergebnis.monate_uebersprungen} schon im Archiv, "
                f"{ergebnis.zeilen} Zeilen"
            )
            if ergebnis.monate_fehlgeschlagen:
                # Sichtbar machen, nicht verschlucken: ein fehlgeschlagener
                # Monat ist eine Luecke in der Zeitreihe, kein Nulltraffic.
                click.echo(
                    f"  ACHTUNG: {ergebnis.monate_fehlgeschlagen} Monat(e) "
                    f"nicht abrufbar — bleiben offen, naechster Lauf versucht es erneut"
                )
        click.echo("")

    if export_pfad:
        anzahl = exportiere_csv(db_pfad, export_pfad, project_id=projekt)
        click.echo(f"{anzahl} Zeilen nach {export_pfad} geschrieben.")
        return

    for pid in ziele:
        if not monatsreihe(db_pfad, pid) and not importieren:
            click.echo(f"{pid}: noch keine Historie — '--importieren' holt sie.")
            continue
        click.echo(bericht_text(db_pfad, pid, top=top))
        click.echo("")


if __name__ == "__main__":
    cli()
