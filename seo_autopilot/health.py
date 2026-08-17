"""
Selbstüberwachung des SEO-Autopilot ("Wächter").

Der Autopilot lief monatelang scheinbar normal, während einzelne Projekte
still ausfielen: `joseph` war nie gelaufen (Domain zeigte auf eine tote
Adresse), `topal` hatte gar keinen Cron, und die DB-Persistenz war wochenlang
kaputt, ohne dass es jemandem auffiel. Ein Audit-Tool, das seinen eigenen
Ausfall nicht bemerkt, ist wertlos.

Dieses Modul prüft den Betriebszustand — nicht die Websites, sondern das
Werkzeug selbst — und liefert eine Liste von Befunden mit Schweregrad.

    from seo_autopilot.health import run_selfcheck
    report = run_selfcheck()
    print(report.as_text())
    sys.exit(report.exit_code)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Ein Projekt gilt als "stumm", wenn der letzte Lauf länger her ist.
# Die Crons laufen täglich; 36 h lässt einen Ausfall zu, ohne bei jeder
# Verzögerung Alarm zu schlagen.
MAX_ALTER_STUNDEN = 36

# Score-Verlust gegenüber dem Vorlauf, ab dem wir das als Einbruch melden.
SCORE_EINBRUCH = 15.0

SCHWERE_REIHENFOLGE = {"kritisch": 0, "warnung": 1, "hinweis": 2}


@dataclass
class Befund:
    schwere: str  # kritisch | warnung | hinweis
    projekt: str
    titel: str
    detail: str
    abhilfe: str = ""


@dataclass
class HealthReport:
    befunde: List[Befund] = field(default_factory=list)
    geprueft: int = 0
    zeitpunkt: Optional[datetime] = None

    @property
    def kritisch(self) -> List[Befund]:
        return [b for b in self.befunde if b.schwere == "kritisch"]

    @property
    def warnungen(self) -> List[Befund]:
        return [b for b in self.befunde if b.schwere == "warnung"]

    @property
    def exit_code(self) -> int:
        """0 = gesund, 1 = Warnungen, 2 = kritisch (für Cron/Monitoring)."""
        if self.kritisch:
            return 2
        if self.warnungen:
            return 1
        return 0

    def as_text(self) -> str:
        if not self.befunde:
            return f"OK — {self.geprueft} Projekte geprüft, keine Auffälligkeiten."
        zeilen = [
            f"SEO-Autopilot Selbstprüfung — {self.geprueft} Projekte, "
            f"{len(self.kritisch)} kritisch, {len(self.warnungen)} Warnungen"
        ]
        symbole = {"kritisch": "[!]", "warnung": "[~]", "hinweis": "[i]"}
        for b in sorted(
            self.befunde, key=lambda x: SCHWERE_REIHENFOLGE.get(x.schwere, 9)
        ):
            zeilen.append(f"{symbole.get(b.schwere,'[?]')} {b.projekt}: {b.titel}")
            zeilen.append(f"      {b.detail}")
            if b.abhilfe:
                zeilen.append(f"      → {b.abhilfe}")
        return "\n".join(zeilen)


# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------


def _lade_projekte(pfad: Path) -> Dict[str, Dict[str, Any]]:
    if not pfad.exists():
        return {}
    daten = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    return daten.get("projects", daten)


def _crontab_text() -> str:
    """Root-Crontab lesen. Fehlt sudo, geben wir leer zurück statt zu knallen."""
    for befehl in (["sudo", "-n", "crontab", "-l"], ["crontab", "-l"]):
        try:
            r = subprocess.run(befehl, capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        except Exception:  # pragma: no cover - Umgebungsabhängig
            continue
    return ""


def _als_datum(wert: Any) -> Optional[datetime]:
    if wert is None:
        return None
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _dataforseo_zugangsdaten_da(quell_cfg: Dict[str, Any]) -> bool:
    """Sind für DataForSEO überhaupt Zugangsdaten erreichbar?

    Zwei erlaubte Wege: Umgebungsvariablen oder eine Datei, deren Pfad in
    ``source_config.dataforseo.credentials_path`` steht. Geprüft wird nur die
    Existenz — der Inhalt wird hier nie gelesen und nie ausgegeben.
    """
    if os.environ.get("DATAFORSEO_LOGIN") and os.environ.get("DATAFORSEO_PASSWORD"):
        return True
    pfad = (quell_cfg or {}).get("credentials_path")
    return bool(pfad) and Path(pfad).exists()


# --------------------------------------------------------------------------
# Prüfungen
# --------------------------------------------------------------------------


def run_selfcheck(
    db_pfad: str = "seo_autopilot.db",
    projects_pfad: str = "projects.yaml",
    jetzt: Optional[datetime] = None,
    crontab_text: Optional[str] = None,
) -> HealthReport:
    """Prüft den Betriebszustand und liefert alle Befunde.

    `crontab_text` ist injizierbar, damit Tests nicht von der echten
    Server-Crontab abhängen.
    """
    jetzt = jetzt or datetime.now(timezone.utc)
    report = HealthReport(zeitpunkt=jetzt)

    projekte = _lade_projekte(Path(projects_pfad))
    if not projekte:
        report.befunde.append(
            Befund(
                "kritisch",
                "-",
                "Keine Projektkonfiguration gefunden",
                f"{projects_pfad} fehlt oder ist leer.",
                "projects.yaml prüfen — ohne sie läuft kein einziges Audit.",
            )
        )
        return report

    db = Path(db_pfad)
    if not db.exists():
        report.befunde.append(
            Befund(
                "kritisch",
                "-",
                "Datenbank fehlt",
                f"{db_pfad} existiert nicht — Ergebnisse können nicht gespeichert werden.",
                "Migration prüfen (alembic_version).",
            )
        )
        return report

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        _pruefe_schema(con, report)
        crontab = _crontab_text() if crontab_text is None else crontab_text
        aktive = {k: v for k, v in projekte.items() if v.get("enabled", True)}
        report.geprueft = len(aktive)
        for name, cfg in aktive.items():
            _pruefe_projekt(con, name, cfg, crontab, jetzt, report)
    finally:
        con.close()
    return report


def _pruefe_schema(con: sqlite3.Connection, report: HealthReport) -> None:
    """Die Migrationsmarke muss stehen — fehlt sie, schlagen Speicherungen fehl."""
    tabellen = {
        r[0] for r in con.execute("select name from sqlite_master where type='table'")
    }
    if "alembic_version" not in tabellen:
        report.befunde.append(
            Befund(
                "kritisch",
                "-",
                "Datenbank steht nicht unter Migrationskontrolle",
                "Tabelle alembic_version fehlt. Genau dieser Zustand hat 2026-05-30 "
                "wochenlang jede Audit-Speicherung still scheitern lassen.",
                "Schema per SQL nachziehen und alembic_version stempeln.",
            )
        )
    for pflicht in ("seo_audits", "seo_issues", "seo_projects"):
        if pflicht not in tabellen:
            report.befunde.append(
                Befund(
                    "kritisch",
                    "-",
                    f"Tabelle {pflicht} fehlt",
                    "Ohne sie kann kein Audit gespeichert werden.",
                    "Migrationen prüfen.",
                )
            )


def _pruefe_projekt(
    con: sqlite3.Connection,
    name: str,
    cfg: Dict[str, Any],
    crontab: str,
    jetzt: datetime,
    report: HealthReport,
) -> None:
    # --- Zeitplan vorhanden? ---
    if crontab and f"project-id {name}" not in crontab:
        report.befunde.append(
            Befund(
                "warnung",
                name,
                "Kein Zeitplan eingetragen",
                "Das Projekt ist aktiv, wird aber von keinem Cron aufgerufen — "
                "es läuft nur, wenn jemand es von Hand startet.",
                f"Cron-Zeile analog der anderen Projekte anlegen (--project-id {name}).",
            )
        )

    laeufe = con.execute(
        "select id, score, status, issues_found, total_pages, started_at "
        "from seo_audits where project_id=? order by started_at desc limit 2",
        (name,),
    ).fetchall()

    # --- Lief das Projekt überhaupt jemals? ---
    if not laeufe:
        report.befunde.append(
            Befund(
                "kritisch",
                name,
                "Noch nie gelaufen",
                "Für dieses Projekt existiert kein einziges Audit.",
                "Einmal von Hand starten und die Ursache prüfen (Domain erreichbar?).",
            )
        )
        return

    letzter = laeufe[0]
    gestartet = _als_datum(letzter["started_at"])

    # --- Ist der letzte Lauf frisch genug? ---
    if gestartet is None:
        report.befunde.append(
            Befund(
                "warnung",
                name,
                "Zeitstempel des letzten Laufs unlesbar",
                f"Wert: {letzter['started_at']!r}",
                "Persistenz prüfen.",
            )
        )
    elif jetzt - gestartet > timedelta(hours=MAX_ALTER_STUNDEN):
        alter = int((jetzt - gestartet).total_seconds() // 3600)
        report.befunde.append(
            Befund(
                "kritisch",
                name,
                "Seit über %d Stunden kein Lauf" % MAX_ALTER_STUNDEN,
                f"Letzter Lauf vor {alter} Stunden ({gestartet:%Y-%m-%d %H:%M} UTC).",
                "Cron-Log prüfen: /opt/odoo/docs/seo-autopilot/logs/cron.log",
            )
        )

    # --- Hat der Lauf tatsächlich Daten hinterlassen? ---
    if (letzter["total_pages"] or 0) == 0:
        report.befunde.append(
            Befund(
                "kritisch",
                name,
                "Letzter Lauf hat keine Seite erfasst",
                "total_pages = 0 — Crawl fehlgeschlagen oder Domain nicht erreichbar.",
                "Domain in projects.yaml prüfen (zeigt sie noch auf die richtige Adresse?).",
            )
        )
    if letzter["status"] and str(letzter["status"]).lower() not in (
        "completed",
        "success",
        "ok",
    ):
        report.befunde.append(
            Befund(
                "warnung",
                name,
                f"Letzter Lauf endete mit Status '{letzter['status']}'",
                "Der Durchlauf wurde nicht sauber abgeschlossen.",
                "Log des Laufs ansehen.",
            )
        )

    # --- Datenquelle Search Console wirklich verbunden? ---
    quellen = cfg.get("enabled_sources") or []
    quell_cfg = cfg.get("source_config") or {}
    if "gsc" in quellen and not quell_cfg.get("gsc"):
        report.befunde.append(
            Befund(
                "warnung",
                name,
                "Search Console aktiviert, aber nicht konfiguriert",
                "enabled_sources enthält 'gsc', source_config aber keine Zugangsdaten — "
                "die Keyword-Auswertung wird bei jedem Lauf still übersprungen.",
                "property_url + credentials_path in source_config.gsc eintragen.",
            )
        )

    # --- Datenquelle Analytics wirklich verbunden? ---
    if "ga4" in quellen:
        ga4_cfg = quell_cfg.get("ga4") or {}
        fehlend = [
            feld
            for feld in ("property_id", "credentials_path")
            if not ga4_cfg.get(feld)
        ]
        if fehlend:
            report.befunde.append(
                Befund(
                    "warnung",
                    name,
                    "Analytics aktiviert, aber nicht vollständig konfiguriert",
                    "enabled_sources enthält 'ga4', in source_config.ga4 fehlt aber: "
                    f"{', '.join(fehlend)} — die Besucherdaten werden bei jedem "
                    "Lauf still übersprungen.",
                    "property_id (nur Ziffern, nicht G-XXXX) + credentials_path "
                    "in source_config.ga4 eintragen, siehe docs/ga4-setup.md.",
                )
            )

    # --- Datenquelle DataForSEO wirklich verbunden? ---
    # Diese Quelle bricht nie ab, wenn Zugangsdaten fehlen — sie liefert dann
    # still gar nichts. Genau das soll hier auffallen.
    if "dataforseo" in quellen and not _dataforseo_zugangsdaten_da(
        quell_cfg.get("dataforseo") or {}
    ):
        report.befunde.append(
            Befund(
                "warnung",
                name,
                "DataForSEO aktiviert, aber nicht konfiguriert",
                "enabled_sources enthält 'dataforseo', es gibt aber weder "
                "DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD in der Umgebung noch eine "
                "lesbare credentials_path — SERP-, Suchvolumen- und "
                "Backlink-Daten fehlen bei jedem Lauf stillschweigend.",
                "Zugangsdaten hinterlegen, siehe docs/dataforseo-setup.md.",
            )
        )

    # --- Score-Einbruch gegenüber dem Vorlauf ---
    if len(laeufe) > 1 and letzter["score"] is not None:
        vorher = laeufe[1]["score"]
        if vorher is not None and (vorher - letzter["score"]) >= SCORE_EINBRUCH:
            report.befunde.append(
                Befund(
                    "warnung",
                    name,
                    "Score eingebrochen",
                    f"{vorher:.1f} → {letzter['score']:.1f} "
                    f"({letzter['score'] - vorher:+.1f} Punkte).",
                    "Neue Befunde des letzten Laufs ansehen — echte Verschlechterung "
                    "oder Messfehler?",
                )
            )
