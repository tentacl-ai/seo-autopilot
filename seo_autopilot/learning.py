"""
Lernschleife für widerlegte Befunde.

Seit 2026-08-17 prüfen sich schwere Befunde in `verification.py` selbst gegen
die Realität. Was dabei widerlegt wird, verschwand bisher in einer Logzeile —
und damit auch die Information, WELCHE Prüfung im Analyzer eigentlich falsch
liegt. Genau das war der eigentliche Schaden bei joseph-hehenwarter.de: Nicht
der einzelne Fehlalarm, sondern dass niemand sehen konnte, dass derselbe
Fehlalarm seit Wochen bei mehreren Projekten auftrat.

Dieses Modul schreibt jeden widerlegten Befund in die Tabelle
`refuted_findings` und wertet ihn aus:

    from seo_autopilot.learning import record_refuted, muster_bericht

    record_refuted("seo_autopilot.db", "joseph", audit_id, widerlegt)
    for m in muster_bericht("seo_autopilot.db"):
        print(m.issue_type, m.treffer, m.projekte)

Die Leseregel dahinter: **Ein Typ, der bei mehreren Projekten immer wieder
widerlegt wird, ist ein Analyzer-Bug — kein Zufall.** Ein Einzelfall bei einem
Projekt kann eine Eigenheit der Website sein; dreimal quer über zwei Kunden
ist eine kaputte Prüfregel, die gefixt und mit einem Regressionstest
festgenagelt gehört.

Zwei bewusste Entscheidungen:

1. **Kein alembic.** Der Migrations-Hook des Projekts ist auf async verdrahtet
   und hinterlässt bei `alembic upgrade` halb migrierte Zustände. Die Tabelle
   wird deshalb per `CREATE TABLE IF NOT EXISTS` direkt angelegt — idempotent,
   bei jedem Schreibzugriff aufs Neue.
2. **Nichts hier darf ein Audit abbrechen.** Ein kaputter oder gesperrter
   Datenbankpfad kostet uns Statistik, aber niemals einen Audit-Lauf. Alle
   öffentlichen Funktionen fangen Datenbankfehler selbst ab und liefern einen
   leeren Ersatzwert (0 bzw. leere Liste).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

TABELLE = "refuted_findings"

# Ab wie vielen Treffern gilt ein Befundtyp als Muster und nicht als Ausrutscher.
MIN_TREFFER = 3

# Beobachtungsfenster in Tagen. Ein vor Monaten gefixter Fehlalarm soll den
# Bericht nicht ewig verstopfen.
FENSTER_TAGE = 30

_SCHEMA = f"""
create table if not exists {TABELLE} (
    id integer primary key autoincrement,
    project_id text not null,
    audit_id text,
    issue_type text not null,
    category text,
    title text,
    refuted_reason text,
    affected_url text,
    detected_at text not null
);
create index if not exists idx_{TABELLE}_typ on {TABELLE} (issue_type);
create index if not exists idx_{TABELLE}_zeit on {TABELLE} (detected_at);
"""


@dataclass
class Muster:
    """Ein Befundtyp, der wiederholt widerlegt wurde."""

    issue_type: str
    treffer: int  # Wie oft insgesamt widerlegt
    projekte: int  # Bei wie vielen VERSCHIEDENEN Projekten
    projekt_namen: List[str] = field(default_factory=list)
    beispiel_begruendung: str = ""
    letzter_treffer: str = ""

    @property
    def systematisch(self) -> bool:
        """Mehr als ein Projekt betroffen -> mit hoher Sicherheit ein Analyzer-Bug.

        Bei einem einzelnen Projekt kann es an der Website liegen (exotisches
        CMS, JS-Rendering). Tritt derselbe Fehlalarm bei zwei unabhängigen
        Kunden auf, liegt es an unserer Prüfregel.
        """
        return self.projekte >= 2

    @property
    def urteil(self) -> str:
        return "Analyzer-Bug" if self.systematisch else "beobachten"


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def standard_db_pfad() -> str:
    """Pfad der Audit-Datenbank aus den Settings ableiten.

    Die Settings liefern eine SQLAlchemy-URL (`sqlite+aiosqlite:///...`); hier
    wird aber roh per sqlite3 geschrieben, damit die Lernschleife auch dann
    funktioniert, wenn die async-Session des Audits schon zu ist.
    """
    try:
        from .core.config import settings

        url = settings.DATABASE_URL
        if url.startswith("sqlite"):
            return url.split("///", 1)[-1]
    except Exception as exc:  # pragma: no cover - Konfiguration fehlt
        logger.debug(f"[learning] Settings nicht lesbar: {exc}")
    return "seo_autopilot.db"


def _jetzt(wert: Optional[datetime] = None) -> datetime:
    if wert is None:
        return datetime.now(timezone.utc)
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def _affected_url(issue: Dict[str, Any]) -> Optional[str]:
    """Betroffene Adresse aus dem Befund ziehen.

    Bevorzugt der Extraktor aus `verification.py` (kennt auch `affected_items`
    als JSON-String); fällt der Import aus, reicht das direkte Feld.
    """
    try:
        from .verification import _betroffene_url

        return _betroffene_url(issue)
    except Exception:  # pragma: no cover - httpx fehlt o.ä.
        return issue.get("affected_url")


def tabelle_anlegen(db_pfad: str) -> bool:
    """Legt `refuted_findings` an, falls sie fehlt. Mehrfach aufrufbar.

    Rückgabe: True, wenn die Tabelle danach existiert. Kein alembic — siehe
    Modul-Docstring.
    """
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[learning] Datenbank nicht erreichbar ({db_pfad}): {exc}")
        return False
    try:
        con.executescript(_SCHEMA)
        con.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning(f"[learning] Tabelle {TABELLE} nicht anlegbar: {exc}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


def record_refuted(
    db_pfad: str,
    project_id: str,
    audit_id: Optional[str],
    issues: Iterable[Dict[str, Any]],
    zeitpunkt: Optional[datetime] = None,
) -> int:
    """Sichert widerlegte Befunde dauerhaft.

    `issues` sind die von `verify_issues()` zurückgegebenen widerlegten
    Befunde (Schlüssel `refuted_reason`). Rückgabe: Anzahl geschriebener
    Zeilen — 0, wenn nichts anzulegen war ODER die Datenbank nicht mitspielt.
    Es fliegt bewusst keine Ausnahme nach oben: Statistik darf niemals einen
    Audit-Lauf kosten.

    `zeitpunkt` ist injizierbar, damit Tests das Zeitfenster prüfen können,
    ohne die Systemuhr zu verstellen.
    """
    zeilen = []
    stempel = _jetzt(zeitpunkt).isoformat()
    for issue in issues or []:
        if not isinstance(issue, dict):
            logger.debug(f"[learning] Befund ignoriert (kein dict): {issue!r}")
            continue
        zeilen.append(
            (
                project_id,
                audit_id,
                str(issue.get("type") or "unknown"),
                issue.get("category"),
                issue.get("title"),
                issue.get("refuted_reason"),
                _affected_url(issue),
                stempel,
            )
        )

    if not tabelle_anlegen(db_pfad):
        return 0
    if not zeilen:
        return 0

    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:  # pragma: no cover - von tabelle_anlegen gedeckt
        logger.warning(f"[learning] Datenbank nicht erreichbar ({db_pfad}): {exc}")
        return 0
    try:
        con.executemany(
            f"insert into {TABELLE} "
            "(project_id, audit_id, issue_type, category, title, "
            " refuted_reason, affected_url, detected_at) "
            "values (?,?,?,?,?,?,?,?)",
            zeilen,
        )
        con.commit()
        logger.info(
            f"[learning] {len(zeilen)} widerlegte Befunde von {project_id} gesichert"
        )
        return len(zeilen)
    except sqlite3.Error as exc:
        logger.warning(f"[learning] Widerlegte Befunde nicht speicherbar: {exc}")
        return 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Auswerten
# ---------------------------------------------------------------------------


def muster_bericht(
    db_pfad: str,
    min_treffer: int = MIN_TREFFER,
    tage: int = FENSTER_TAGE,
    jetzt: Optional[datetime] = None,
) -> List[Muster]:
    """Wiederkehrende Fehlalarme sichtbar machen.

    Liefert je Befundtyp: wie oft widerlegt und bei wie vielen verschiedenen
    Projekten — absteigend nach Häufigkeit. Typen unterhalb von `min_treffer`
    fallen raus: Ein einzelner widerlegter Befund ist kein Muster, sondern
    genau das, wofür die Gegenprobe da ist.

    Bei nicht lesbarer Datenbank kommt eine leere Liste zurück, keine Ausnahme.
    """
    grenze = (_jetzt(jetzt) - timedelta(days=tage)).isoformat()

    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[learning] Datenbank nicht lesbar ({db_pfad}): {exc}")
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"""
            select issue_type,
                   count(*)                     as treffer,
                   count(distinct project_id)   as projekte,
                   group_concat(distinct project_id) as namen,
                   max(detected_at)             as letzter,
                   max(refuted_reason)          as beispiel
              from {TABELLE}
             where detected_at >= ?
          group by issue_type
            having count(*) >= ?
          order by treffer desc, projekte desc, issue_type asc
            """,
            (grenze, int(min_treffer)),
        ).fetchall()
    except sqlite3.Error as exc:
        # Tabelle fehlt (noch nie etwas widerlegt) oder DB kaputt — beides
        # ist kein Grund, den Aufrufer scheitern zu lassen.
        logger.warning(f"[learning] Musterbericht nicht erstellbar: {exc}")
        return []
    finally:
        con.close()

    muster = []
    for r in rows:
        namen = sorted(n for n in (r["namen"] or "").split(",") if n)
        muster.append(
            Muster(
                issue_type=r["issue_type"],
                treffer=int(r["treffer"]),
                projekte=int(r["projekte"]),
                projekt_namen=namen,
                beispiel_begruendung=r["beispiel"] or "",
                letzter_treffer=r["letzter"] or "",
            )
        )
    return muster


def bericht_als_text(
    muster: List[Muster], tage: int = FENSTER_TAGE, min_treffer: int = MIN_TREFFER
) -> str:
    """Musterbericht als Klartext-Tabelle (für CLI und Telegram)."""
    if not muster:
        return (
            f"Keine wiederkehrenden Fehlalarme in den letzten {tage} Tagen "
            f"(Schwelle: {min_treffer} Widerlegungen je Befundtyp)."
        )

    breite = max(len(m.issue_type) for m in muster)
    breite = max(breite, len("Befundtyp"))
    zeilen = [
        f"Wiederkehrende Fehlalarme — letzte {tage} Tage, "
        f"ab {min_treffer} Widerlegungen:",
        "",
        f"{'Befundtyp'.ljust(breite)}  {'Widerlegt':>9}  {'Projekte':>8}  Urteil",
        f"{'-' * breite}  {'-' * 9}  {'-' * 8}  {'-' * 12}",
    ]
    for m in muster:
        zeilen.append(
            f"{m.issue_type.ljust(breite)}  {m.treffer:>9}  "
            f"{m.projekte:>8}  {m.urteil}"
        )
    zeilen.append("")
    for m in muster:
        zeilen.append(f"* {m.issue_type} ({', '.join(m.projekt_namen)})")
        if m.beispiel_begruendung:
            zeilen.append(f"    Beispiel: {m.beispiel_begruendung}")
    zeilen.append("")
    zeilen.append(
        "Urteil 'Analyzer-Bug' = bei mehreren Projekten widerlegt. Diese Regel "
        "gehoert korrigiert und per Regressionstest festgenagelt."
    )
    return "\n".join(zeilen)
