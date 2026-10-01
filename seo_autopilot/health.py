"""
Selbstüberwachung des SEO-Autopilot ("Wächter").

Der Autopilot lief monatelang scheinbar normal, während einzelne Projekte
still ausfielen: `beratung-beispiel` war nie gelaufen (Domain zeigte auf eine tote
Adresse), `handel-beispiel` hatte gar keinen Cron, und die DB-Persistenz war wochenlang
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


def _globaler_pagespeed_schluessel() -> Optional[str]:
    """Der projektübergreifende Schlüssel — genau wie ihn der Analyzer sucht.

    Wichtig: ``os.environ`` allein genügt NICHT. Der Schlüssel darf auch in
    der ``.env`` stehen, die von den Settings gelesen wird; ``os.getenv``
    sieht ihn dort nicht. Genau diese Lücke hat den Fehler am 2026-08-17
    verschleiert. Der Wert wird nur auf Vorhandensein geprüft, nie ausgegeben.
    """
    aus_umgebung = os.environ.get("PAGESPEED_API_KEY")
    if aus_umgebung:
        return aus_umgebung
    try:
        from .core.config import settings

        return settings.PAGESPEED_API_KEY
    except Exception:  # pragma: no cover - defensiv
        return None


def _pagespeed_schluessel_da(quell_cfg: Dict[str, Any]) -> bool:
    """Ist für die Geschwindigkeitsmessung überhaupt ein Schlüssel hinterlegt?

    Zwei erlaubte Wege: projektweise ``source_config.pagespeed.api_key`` oder
    global über Umgebung bzw. ``.env``.
    """
    if (quell_cfg or {}).get("api_key"):
        return True
    return bool(_globaler_pagespeed_schluessel())


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
        _pruefe_wirkungsmessung(con, crontab, jetzt, report, db_pfad, projekte)
        _pruefe_historie(con, aktive, crontab, jetzt, report)
        _pruefe_freigaben(con, projekte, jetzt, report)
        _pruefe_paket(aktive, report)
        _pruefe_externe_berichte(aktive, report)
        _pruefe_lernschleife(crontab, jetzt, report)
    finally:
        con.close()
    if UMGEBUNG_PRUEFEN:
        _pruefe_werkzeug(report)
    return report


# Bausteine, deren Fehlen den Autopiloten STILL entwertet (18.09.2026: Pillow und
# Playwright fehlten seit Monaten, Reparaturen und JS-Rendering liefen ins Leere).
WERKZEUGE = {
    "PIL": ("Pillow", "Bildmasse und Bild-KI (Alt-Texte) koennen nicht arbeiten."),
    "playwright": (
        "Playwright",
        "JavaScript-Seiten (React/Vite) werden nur als leeres HTML bewertet.",
    ),
    "feedparser": (
        "feedparser",
        "Richtlinien-Radar und Marktbeobachter lesen keine Fachquellen.",
    ),
}
FREIGABE_ALARM_TAGE = 14
# Tests schalten das ab (conftest), sonst haengt ihr Ergebnis davon ab, ob auf der
# Maschine Pillow/Playwright/der PageSpeed-Schluessel da sind.
UMGEBUNG_PRUEFEN = True


def _modul_da(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _pruefe_werkzeug(
    report: HealthReport, modul_da=_modul_da, browser_ordner=None
) -> None:
    """Sind die Bausteine installiert, ohne die Pruefungen still ausfallen?"""
    for modul, (paket, folge) in WERKZEUGE.items():
        if not modul_da(modul):
            report.befunde.append(
                Befund(
                    "kritisch",
                    "werkzeug",
                    f"{paket} fehlt",
                    folge,
                    f"venv/bin/pip install -r requirements.txt",
                )
            )
    if modul_da("playwright"):
        if browser_ordner is None:
            from .sources.renderer import BROWSER_ORDNER as browser_ordner
        if not any(Path(browser_ordner).glob("chromium*")):
            report.befunde.append(
                Befund(
                    "kritisch",
                    "werkzeug",
                    "Browser fuer JavaScript-Seiten fehlt",
                    f"Kein Chromium unter {browser_ordner}.",
                    "PLAYWRIGHT_BROWSERS_PATH=<ordner> venv/bin/python -m playwright "
                    "install chromium-headless-shell",
                )
            )
    if not _globaler_pagespeed_schluessel():
        report.befunde.append(
            Befund(
                "warnung",
                "werkzeug",
                "PageSpeed-Schluessel fehlt",
                "Ohne Schluessel gibt es keine Ladezeit-Messung (Core Web Vitals).",
                "PAGESPEED_API_KEY in .env des Autopiloten eintragen.",
            )
        )


def _pruefe_paket(projekte: Dict[str, Dict[str, Any]], report: HealthReport) -> None:
    """Große Packung: jedes aktive Projekt hat Search Console, GA4 und Bericht.

    Robert (18.09.2026): Der Autopilot soll die Daten auch auswerten — ohne
    Search Console und Analytics bleibt er blind, ohne Bericht sieht es niemand.
    Projekte mit `paket: klein` in projects.yaml sind bewusst ausgenommen.
    """
    for name, cfg in projekte.items():
        cfg = cfg or {}
        if not cfg.get("enabled", True):
            continue
        if str(cfg.get("paket") or "").strip().lower() == "klein":
            continue
        quellen = cfg.get("enabled_sources") or []
        quell_cfg = cfg.get("source_config") or {}
        fehlt = []
        if "gsc" not in quellen or not (quell_cfg.get("gsc") or {}).get("property_url"):
            fehlt.append("Search Console")
        if "ga4" not in quellen or not (quell_cfg.get("ga4") or {}).get("property_id"):
            fehlt.append("Google Analytics 4")
        bericht = cfg.get("bericht") or {}
        standard = bericht.get("aktiv") and bericht.get("empfaenger")
        extern = bericht.get("extern_aktiv") and bericht.get("extern_timer")
        if not standard and not extern:
            fehlt.append("Wochenbericht")
        if fehlt:
            report.befunde.append(
                Befund(
                    "warnung",
                    name,
                    "Paket unvollständig: " + ", ".join(fehlt),
                    "Standard ist die große Packung (Search Console + Analytics + "
                    "Wochenbericht) — ohne sie wertet der Autopilot die Daten "
                    "dieser Website nicht aus.",
                    f"seo-autopilot einrichten --projekt {name} (zeigt, was fehlt, "
                    "und wer es freigeben muss). Bewusst klein? `paket: klein` "
                    "in projects.yaml eintragen.",
                )
            )


def _pruefe_externe_berichte(
    projekte: Dict[str, Dict[str, Any]], report: HealthReport
) -> None:
    """Externe Berichtstimer nicht nur konfigurieren, sondern ueberwachen."""
    for name, cfg in projekte.items():
        bericht = (cfg or {}).get("bericht") or {}
        if not bericht.get("extern_aktiv"):
            continue
        timer = str(bericht.get("extern_timer") or "").strip()
        if not timer:
            report.befunde.append(
                Befund(
                    "warnung",
                    name,
                    "Externer Wochenbericht ohne Timer",
                    "extern_aktiv ist gesetzt, aber extern_timer fehlt.",
                    "Systemd-Timer in bericht.extern_timer eintragen.",
                )
            )
            continue
        try:
            aktiviert = (
                subprocess.run(
                    ["systemctl", "is-enabled", "--quiet", timer], timeout=10
                ).returncode
                == 0
            )
            aktiv = (
                subprocess.run(
                    ["systemctl", "is-active", "--quiet", timer], timeout=10
                ).returncode
                == 0
            )
        except Exception as exc:
            report.befunde.append(
                Befund(
                    "warnung",
                    name,
                    "Externer Wochenbericht nicht prüfbar",
                    f"{timer}: {exc}",
                    "Timer auf dem Berichtsserver mit systemctl prüfen.",
                )
            )
            continue
        if not aktiviert or not aktiv:
            report.befunde.append(
                Befund(
                    "warnung",
                    name,
                    "Externer Wochenbericht läuft nicht",
                    f"{timer}: enabled={aktiviert}, active={aktiv}",
                    f"systemctl enable --now {timer}",
                )
            )


def _pruefe_lernschleife(
    crontab: str,
    jetzt: datetime,
    report: HealthReport,
    stand_pfad: Optional[Path] = None,
) -> None:
    """Laeuft die woechentliche Lernschleife, und bleibt ein Go liegen?"""
    if "lernschleife" not in crontab:
        return  # nicht eingerichtet - dann gibt es nichts zu ueberwachen
    from . import lernschleife as ls

    stand = ls.stand_laden(stand_pfad or ls.STAND_DATEI)
    zuletzt = _als_datum(stand.get("letzter_lauf"))
    if not zuletzt or (jetzt - zuletzt).days > ls.LAUF_ALARM_TAGE:
        report.befunde.append(
            Befund(
                "warnung",
                "-",
                "Lernschleife laeuft nicht",
                f"Letzter Lauf: {stand.get('letzter_lauf') or 'nie'}.",
                "logs/lernschleife.log und den Cron-Eintrag pruefen.",
            )
        )
    elif stand.get("fehler"):
        report.befunde.append(
            Befund(
                "warnung",
                "-",
                "Lernschleife konnte nicht bewerten",
                "; ".join(stand["fehler"])[:300],
                "Abo-Zugang (abo_ki) und logs/lernschleife.log pruefen.",
            )
        )
    for v in ls.go_ueberfaellig(stand, heute=jetzt.date()):
        report.befunde.append(
            Befund(
                "warnung",
                "-",
                f"Go fuer Anpassung liegt seit {v.get('entschieden_am')}: {v.get('titel')}",
                "Freigegeben, aber noch nicht umgesetzt.",
                f"Umsetzen, dann seo-autopilot lernschleife --erledigt {v.get('id')}",
            )
        )


def _pruefe_freigaben(
    con: sqlite3.Connection,
    projekte: Dict[str, Dict[str, Any]],
    jetzt: datetime,
    report: HealthReport,
) -> None:
    """Liegen Vorschlaege zu lange oder fuer abgeschaltete Projekte herum?"""
    try:
        zeilen = con.execute(
            "select project_id, count(*), min(erstellt_am) from freigaben "
            "where status = 'offen' group by project_id"
        ).fetchall()
    except sqlite3.Error:
        return  # Tabelle gibt es erst nach der ersten Freigabe
    for projekt, anzahl, aeltester in zeilen:
        cfg = projekte.get(projekt) or {}
        if not cfg.get("enabled", True):
            report.befunde.append(
                Befund(
                    "warnung",
                    projekt,
                    f"{anzahl} offene Vorschlaege fuer ein abgeschaltetes Projekt",
                    "Das Projekt laeuft nicht mehr, die Vorschlaege verstopfen die Liste.",
                    f"seo-autopilot freigabe --projekt {projekt} --alle-ablehnen",
                )
            )
            continue
        alt = _als_datum(aeltester)
        if alt and (jetzt - alt).days > FREIGABE_ALARM_TAGE:
            report.befunde.append(
                Befund(
                    "warnung",
                    projekt,
                    f"{anzahl} Vorschlaege warten, der aelteste seit {(jetzt - alt).days} Tagen",
                    "Niemand entscheidet — die Vorschlaege veralten.",
                    "Im Kundenbericht entscheiden oder seo-autopilot freigabe --projekt "
                    f"{projekt} ansehen.",
                )
            )


def _pruefe_wirkungsmessung(
    con: sqlite3.Connection,
    crontab: str,
    jetzt: datetime,
    report: HealthReport,
    db_pfad: str,
    projekte: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Läuft die Wirkungsmessung überhaupt noch?

    Die Wirkungsmessung ist besonders anfällig für einen stillen Ausfall: Sie
    meldet auch im Normalbetrieb wochenlang "nichts fällig" — genau wie eine
    kaputte. Ohne diese Prüfung fiele ein Ausfall erst auf, wenn jemand nach
    Ergebnissen fragt, die es dann nicht gibt.

    Drei Fragen, in der Reihenfolge ihrer Schwere:

    1. Steht der tägliche Lauf überhaupt im Cron?
    2. Steht er mit `cd` davor? Ohne das findet er `projects.yaml` nicht und
       misst nie etwas — dieser Fehler ist beim Einrichten tatsächlich passiert.
    3. Sind Messungen fällig, aber seit Tagen keine dazugekommen?
    """
    from .wirkung import GSC_VERZUG_TAGE

    # Ohne eine einzige protokollierte Änderung gibt es nichts zu messen. Eine
    # frische Installation braucht den Lauf noch nicht, und ein Wächter, der
    # dort schon meckert, erzieht zum Wegsehen.
    try:
        from .changelog_book import aenderungen

        if not aenderungen(db_pfad, tage=0):
            return
    except Exception as exc:  # pragma: no cover - defensiv
        logger.debug(f"[health] Änderungsbuch nicht lesbar: {exc}")
        return

    zeilen = [
        z
        for z in crontab.splitlines()
        if "seo_autopilot.cli.main wirkung" in z and not z.strip().startswith("#")
    ]

    if not zeilen:
        report.befunde.append(
            Befund(
                "warnung",
                "-",
                "Wirkungsmessung läuft nicht automatisch",
                "Kein Cron-Eintrag für 'wirkung --messen' gefunden. Änderungen "
                "werden protokolliert, aber nie ausgewertet.",
                "Täglichen Lauf eintragen (nach dem Wächter, z. B. 11:45).",
            )
        )
        return

    # Ohne `cd` wird projects.yaml relativ zum Home-Verzeichnis gesucht und nie
    # gefunden. Der Lauf endet dann zwar mit Fehler, aber im Cron sieht das
    # niemand — deshalb meldet der Wächter es.
    ohne_cd = [z for z in zeilen if "cd " not in z]
    if ohne_cd:
        report.befunde.append(
            Befund(
                "warnung",
                "-",
                "Wirkungsmessung im Cron ohne Verzeichniswechsel",
                "Der Eintrag enthält kein 'cd'. Seit 1.9.0 wird die "
                "Projektliste absolut aufgelöst, der Lauf funktioniert also "
                "trotzdem — bei relativen Angaben (--projects, --db) läuft er "
                "aber ins Leere.",
                "'cd <Installationsordner> &&' vor den Befehl setzen.",
            )
        )

    # Gibt es fällige Messungen, die liegen bleiben?
    try:
        from .wirkung import faellige_messungen

        offen = faellige_messungen(db_pfad, heute=jetzt.date())
    except Exception as exc:  # pragma: no cover - defensiv
        logger.debug(f"[health] Fälligkeit nicht prüfbar: {exc}")
        return

    # Nie messbare Änderungen dürfen keinen Dauer-Alarm auslösen (16.09.2026):
    # Projekte, die es nicht mehr gibt (Testreste wie 't'), und Projekte mit
    # eingeschalteter, aber nicht eingerichteter Search Console (handel-beispiel) - Letzteres
    # meldet _pruefe_projekt bereits als eigene Warnung mit der richtigen Abhilfe.
    if projekte is not None:
        offen = [(a, f) for a, f in offen if _messbar(projekte.get(a.project_id))]

    if not offen:
        return

    try:
        row = con.execute(
            "select max(gemessen_am) as letzte from wirkung_messungen"
        ).fetchone()
    except sqlite3.Error:
        row = None

    letzte = _als_datum(row["letzte"]) if row and row["letzte"] else None
    # Kulanz: Der Lauf ist täglich, die Search Console hinkt ohnehin hinterher.
    # Erst wenn beides zusammen überschritten ist, stimmt etwas nicht.
    grenze_tage = 1 + GSC_VERZUG_TAGE

    if letzte is None:
        report.befunde.append(
            Befund(
                "warnung",
                "-",
                f"{len(offen)} Wirkungsmessung(en) fällig, aber noch nie gemessen",
                "Es liegen auswertbare Änderungen vor, die Tabelle "
                "wirkung_messungen ist aber leer.",
                "Einmal 'wirkung --messen' von Hand laufen lassen und Log prüfen.",
            )
        )
        return

    alter = (jetzt.date() - letzte.date()).days
    if alter > grenze_tage:
        report.befunde.append(
            Befund(
                "warnung",
                "-",
                f"Wirkungsmessung seit {alter} Tagen ohne Ergebnis",
                f"{len(offen)} Messung(en) sind fällig, die letzte gespeicherte "
                f"stammt vom {letzte.date().isoformat()}.",
                "logs/wirkung.log prüfen — Search-Console-Zugang oder Cron defekt.",
            )
        )


def _pruefe_historie(
    con: sqlite3.Connection,
    projekte: Dict[str, Dict[str, Any]],
    crontab: str,
    jetzt: datetime,
    report: HealthReport,
) -> None:
    """Wacht über die Search-Console-Langzeithistorie.

    Der stillste Dienst im Autopiloten: Im Normalbetrieb überspringt der Import
    alle archivierten Monate und meldet nichts — exakt wie ein Import, dessen
    Zugang abgelaufen ist. Der Unterschied fällt erst auf, wenn jemand nach der
    Historie fragt. Dann ist der fehlende Monat bei Google aber unwiederbringlich
    weg: Die Search Console gibt nur 16 Monate heraus.

    Zwei Prüfungen, bewusst zurückhaltend:
    1. Läuft der Import automatisch?
    2. Steht der zuletzt abgeschlossene Monat im Archiv?

    Ein Projekt ohne Search Console wird nicht bemängelt (das ist eine bekannte
    Tatsache, kein Ausfall), und ein noch leeres Archiv auch nicht — sonst
    meldet jede frische Installation sofort rot.
    """
    from .historie import NACHZIEHFRIST_TAGE, TABELLE, gsc_konfiguration

    mit_gsc = {n: c for n, c in projekte.items() if gsc_konfiguration(c)}
    if not mit_gsc:
        return

    try:
        vorhanden = {
            (z["project_id"], z["monat"])
            for z in con.execute(
                f"select project_id, monat from {TABELLE} where dimension = 'gesamt'"
            ).fetchall()
        }
    except sqlite3.Error:
        # Tabelle gibt es noch nicht — nie importiert. Kein Ausfall, sondern neu.
        return

    if not vorhanden:
        return

    if "cli.main historie" not in crontab:
        report.befunde.append(
            Befund(
                "warnung",
                "-",
                "Historie läuft nicht automatisch",
                "Kein Cron-Eintrag für den Historien-Import gefunden.",
                "Eintragen: 15 11 * * * "
                "./venv/bin/python3 -m seo_autopilot.cli.main historie --importieren "
                "— sonst gehen Monate verloren, die Google nach 16 Monaten "
                "nicht mehr herausgibt.",
            )
        )

    # Zuletzt abgeschlossener Monat — innerhalb der Nachziehfrist noch nicht
    # einfordern, da liefert Google selbst noch nach.
    erster_des_monats = jetzt.date().replace(day=1)
    letzter_abgeschlossener = erster_des_monats - timedelta(days=1)
    if (jetzt.date() - letzter_abgeschlossener).days <= NACHZIEHFRIST_TAGE:
        return
    monat = f"{letzter_abgeschlossener.year:04d}-{letzter_abgeschlossener.month:02d}"

    for name in mit_gsc:
        # Projekte, die noch nie importiert wurden, hier nicht anmahnen —
        # sonst meldet ein frisch aufgenommener Kunde sofort einen Ausfall.
        if not any(p == name for p, _ in vorhanden):
            continue
        if (name, monat) in vorhanden:
            continue
        report.befunde.append(
            Befund(
                "warnung",
                name,
                "Lücke in der Historie",
                f"Der abgeschlossene Monat {monat} fehlt im Archiv.",
                f"Nachholen: seo-autopilot historie --importieren --projekt {name} "
                f"— nach 16 Monaten gibt Google ihn nicht mehr heraus.",
            )
        )


def _messbar(cfg: Optional[Dict[str, Any]]) -> bool:
    """Kann die Wirkungsmessung dieses Projekt überhaupt je auswerten?"""
    if not cfg or not cfg.get("enabled", True):
        return False
    if "gsc" in (cfg.get("enabled_sources") or []):
        gsc = (cfg.get("source_config") or {}).get("gsc") or {}
        return bool(gsc.get("property_url"))
    return True


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
                "Cron-Log prüfen: <Installationsordner>/logs/cron.log",
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

    # --- Geschwindigkeitsmessung wirklich möglich? ---
    # Anders als GSC/GA4/DataForSEO muss PageSpeed nicht in `enabled_sources`
    # stehen — der Analyzer ruft es bei JEDEM Lauf auf. Ohne Schlüssel läuft
    # die Anfrage über ein gemeinsames Google-Kontingent, das dauerhaft
    # erschöpft ist (HTTP 429). Im Log stand dann nur "PageSpeed unavailable",
    # und niemandem fiel auf, dass seit Monaten keine einzige
    # Core-Web-Vitals-Zahl im Bericht steht.
    if not _pagespeed_schluessel_da(quell_cfg.get("pagespeed") or {}):
        report.befunde.append(
            Befund(
                "warnung",
                name,
                "Geschwindigkeitsmessung ohne API-Schlüssel",
                "Weder source_config.pagespeed.api_key noch die Umgebungsvariable "
                "PAGESPEED_API_KEY sind gesetzt. Google beantwortet Anfragen ohne "
                "Schlüssel aus einem gemeinsamen Tageskontingent, das erschöpft "
                "ist — es kommen KEINE Core Web Vitals (LCP, CLS, INP) an. "
                "Die Bildprüfung läuft weiter, ersetzt die Messung aber nicht.",
                "Kostenlosen Schlüssel anlegen "
                "(developers.google.com/speed/docs/insights/v5/get-started) und "
                "als PAGESPEED_API_KEY in die .env eintragen.",
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
