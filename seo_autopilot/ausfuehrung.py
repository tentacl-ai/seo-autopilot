"""
Ausführung — was der Autopilot selbst tun darf, und was nie.

Bis hierher kann das Werkzeug beobachten, messen und priorisieren. Ausführen
konnte es auch schon, aber mit einem einzigen Schalter: `auto_fix_enabled`
an oder aus. Das ist für ein System, das unbeaufsichtigt an Kundenwebsites
arbeitet, zu grob — und die Sicherheitsgrenzen, die dafür gelten sollen,
standen bisher nur in der Dokumentation, nicht im Code.

Drei Betriebsarten
------------------

* **Beobachter** — schaut zu, ändert nichts. Der sichere Standard für jedes
  neue Projekt.
* **Copilot** — bereitet jede Änderung vor und legt sie zur Freigabe. Nichts
  geht live, bevor ein Mensch zugestimmt hat.
* **Autopilot** — führt aus, was unbedenklich ist, und legt alles andere
  trotzdem zur Freigabe.

Die harte Sperrliste
--------------------

`GESPERRT` enthält Eingriffe, die **niemals** automatisch laufen — auch nicht
im Autopilot-Modus, auch nicht, wenn jemand sie in die Whitelist einträgt.
Sie sind nicht deshalb gesperrt, weil sie schwierig wären, sondern weil ein
Fehler dort nicht auffällt und teuer ist:

* Eine falsch gesetzte Kanonisierung oder ein `noindex` nimmt eine Seite
  wochenlang aus dem Index, bevor es jemand merkt.
* Eine überschriebene `robots.txt` kann eine ganze Website unsichtbar machen.
* Gelöschte Seiten und geänderte Adressen sind ohne Weiterleitung dauerhaft
  verloren.

Solche Vorschläge werden weiterhin erzeugt — sie landen nur immer in der
Freigabe, mit Begründung, statt still ausgeführt zu werden.

Warum das die Voraussetzung für autonomen Betrieb ist
-----------------------------------------------------

Ein System darf genau so viel selbst tun, wie man im Fehlerfall zurücknehmen
kann. Alles in diesem Modul ist darauf ausgelegt: Jede ausgeführte Änderung
steht im Änderungsbuch, jede Freigabe ist nachvollziehbar, und was nicht
sicher rücknehmbar ist, führt der Autopilot nicht selbst aus.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .learning import standard_db_pfad  # noqa: F401  (bewusst re-exportiert)

logger = logging.getLogger(__name__)

TABELLE = "freigaben"

# --- Betriebsarten ---------------------------------------------------------

BETRIEBSART_BEOBACHTER = "beobachter"
BETRIEBSART_COPILOT = "copilot"
BETRIEBSART_AUTOPILOT = "autopilot"
BETRIEBSARTEN = (
    BETRIEBSART_BEOBACHTER,
    BETRIEBSART_COPILOT,
    BETRIEBSART_AUTOPILOT,
)

# Neue Projekte starten immer hier. Wer ausführen lassen will, sagt es
# ausdrücklich — nicht umgekehrt.
BETRIEBSART_STANDARD = BETRIEBSART_BEOBACHTER

_BETRIEBSART_KLARTEXT = {
    BETRIEBSART_BEOBACHTER: "Beobachter (ändert nichts)",
    BETRIEBSART_COPILOT: "Copilot (alles zur Freigabe)",
    BETRIEBSART_AUTOPILOT: "Autopilot (führt Unbedenkliches aus)",
}

# --- Harte Sperrliste ------------------------------------------------------

# Diese Eingriffe laufen NIE automatisch. Die Liste ist bewusst hier im Code
# und nicht in der Konfiguration: Was hier steht, soll sich nicht durch einen
# Eintrag in projects.yaml aushebeln lassen.
GESPERRT: Dict[str, str] = {
    "noindex": "Nimmt die Seite aus dem Suchindex — Wirkung erst nach Wochen sichtbar.",
    "page_noindex": "Nimmt die Seite aus dem Suchindex — Wirkung erst nach Wochen sichtbar.",
    "missing_canonical": "Eine falsche Kanonisierung lässt Seiten aus dem Index fallen.",
    "canonical_missing": "Eine falsche Kanonisierung lässt Seiten aus dem Index fallen.",
    "wrong_canonical": "Eine falsche Kanonisierung lässt Seiten aus dem Index fallen.",
    "canonical_chain": "Eine falsche Kanonisierung lässt Seiten aus dem Index fallen.",
    "missing_robots_txt": "Eine fehlerhafte robots.txt kann die ganze Website unsichtbar machen.",
    "robots_txt_blocks": "Eine fehlerhafte robots.txt kann die ganze Website unsichtbar machen.",
    "delete_page": "Gelöschte Seiten sind ohne Weiterleitung dauerhaft verloren.",
    "thin_content": "Würde bedeuten, Inhalte zu entfernen oder neu zu schreiben.",
    "near_duplicate": "Zusammenlegen von Seiten ändert Adressen — braucht Weiterleitungen.",
    "url_migration": "Adressänderungen ohne Weiterleitung kosten alle Platzierungen.",
    "redirect_chain": "Weiterleitungsketten falsch aufzulösen kann Seiten unerreichbar machen.",
    "redirect_loop": "Weiterleitungsketten falsch aufzulösen kann Seiten unerreichbar machen.",
}

# --- Entscheidungen --------------------------------------------------------

WEG_AUSFUEHREN = "ausfuehren"
WEG_FREIGABE = "freigabe"
WEG_NICHTS = "nichts"

# --- Freigabe-Status -------------------------------------------------------

STATUS_OFFEN = "offen"
STATUS_FREIGEGEBEN = "freigegeben"
STATUS_ABGELEHNT = "abgelehnt"
STATUS_AUSGEFUEHRT = "ausgefuehrt"

# Nach so vielen Tagen gilt ein offener Vorschlag als veraltet: Die Website
# hat sich seitdem verändert, der Vorschlag passt womöglich nicht mehr.
VERFALL_TAGE = 30

_SCHEMA = f"""
create table if not exists {TABELLE} (
    id text primary key,
    project_id text not null,
    audit_id text,
    erstellt_am text not null,
    issue_type text not null,
    titel text,
    ziel_url text,
    vorschlag text,
    begruendung text,
    gesperrt_grund text,
    status text not null default '{STATUS_OFFEN}',
    entschieden_am text,
    entschieden_von text,
    notiz text
);
create index if not exists idx_{TABELLE}_projekt on {TABELLE} (project_id, status);
create index if not exists idx_{TABELLE}_status on {TABELLE} (status);
"""

_SPALTEN = (
    "id",
    "project_id",
    "audit_id",
    "erstellt_am",
    "issue_type",
    "titel",
    "ziel_url",
    "vorschlag",
    "begruendung",
    "gesperrt_grund",
    "status",
    "entschieden_am",
    "entschieden_von",
    "notiz",
)


@dataclass
class Freigabe:
    """Ein Vorschlag, der auf eine menschliche Entscheidung wartet."""

    id: str
    project_id: str
    erstellt_am: str
    issue_type: str
    audit_id: Optional[str] = None
    titel: str = ""
    ziel_url: Optional[str] = None
    vorschlag: str = ""
    begruendung: str = ""
    gesperrt_grund: Optional[str] = None
    status: str = STATUS_OFFEN
    entschieden_am: Optional[str] = None
    entschieden_von: Optional[str] = None
    notiz: str = ""

    @property
    def ist_gesperrt(self) -> bool:
        """Steht dieser Eingriff auf der harten Sperrliste?"""
        return bool(self.gesperrt_grund)

    @property
    def offen(self) -> bool:
        return self.status == STATUS_OFFEN


def betriebsart_von(projekt: Dict[str, Any]) -> str:
    """Die Betriebsart eines Projekts — im Zweifel die sicherste.

    Gelesen wird `betriebsart` aus der Projektkonfiguration. Ein unbekannter
    Wert führt NICHT zu einem Fehler, sondern zum Beobachter-Modus: Ein
    Tippfehler in der Konfiguration darf niemals dazu führen, dass mehr
    passiert als gewollt.
    """
    roh = str((projekt or {}).get("betriebsart") or "").strip().lower()
    if roh in BETRIEBSARTEN:
        return roh

    if roh:
        logger.warning(
            f"[ausfuehrung] Unbekannte Betriebsart {roh!r} — "
            f"es gilt {BETRIEBSART_STANDARD}."
        )
        return BETRIEBSART_STANDARD

    # Rückwärtskompatibel: Wer bisher auto_fix_enabled gesetzt hatte, bekommt
    # Autopilot — sonst würde ein Update stillschweigend die Ausführung
    # abschalten, die vorher lief.
    if (projekt or {}).get("auto_fix_enabled"):
        return BETRIEBSART_AUTOPILOT
    return BETRIEBSART_STANDARD


def betriebsart_klartext(art: str) -> str:
    return _BETRIEBSART_KLARTEXT.get(art, art)


def ist_gesperrt(issue_type: str) -> Optional[str]:
    """Grund, warum dieser Eingriff nie automatisch läuft — oder None."""
    return GESPERRT.get(str(issue_type or ""))


def entscheide(
    issue_type: str,
    betriebsart: str,
    in_whitelist: bool = True,
) -> Tuple[str, str]:
    """Welchen Weg nimmt dieser Vorschlag.

    Rückgabe: (Weg, Begründung im Klartext).

    Die Reihenfolge der Prüfungen ist die Sicherheitsgarantie des Moduls:
    Die harte Sperre wird VOR der Betriebsart geprüft. Damit gibt es keine
    Konfiguration und keinen Modus, der sie aushebelt.
    """
    grund = ist_gesperrt(issue_type)
    if grund:
        return (
            WEG_FREIGABE,
            f"Braucht immer eine menschliche Freigabe: {grund}",
        )

    if betriebsart == BETRIEBSART_BEOBACHTER:
        return (WEG_NICHTS, "Betriebsart Beobachter — es wird nichts geändert.")

    if not in_whitelist:
        return (
            WEG_FREIGABE,
            "Nicht in der Liste der unbeaufsichtigt erlaubten Eingriffe.",
        )

    if betriebsart == BETRIEBSART_COPILOT:
        return (WEG_FREIGABE, "Betriebsart Copilot — jede Änderung wird vorgelegt.")

    return (WEG_AUSFUEHREN, "Unbedenklicher Eingriff, Betriebsart Autopilot.")


# ---------------------------------------------------------------------------
# Freigabe-Schlange
# ---------------------------------------------------------------------------


def tabelle_anlegen(db_pfad: str) -> bool:
    """Legt `freigaben` an, falls sie fehlt. Mehrfach aufrufbar."""
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[ausfuehrung] Datenbank nicht erreichbar ({db_pfad}): {exc}")
        return False
    try:
        con.executescript(_SCHEMA)
        con.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning(f"[ausfuehrung] Tabelle {TABELLE} nicht anlegbar: {exc}")
        return False
    finally:
        con.close()


def _jetzt(wert: Optional[datetime] = None) -> datetime:
    return wert or datetime.now(timezone.utc)


def _zeile_zu_freigabe(row: sqlite3.Row) -> Freigabe:
    return Freigabe(
        id=row["id"],
        project_id=row["project_id"],
        audit_id=row["audit_id"],
        erstellt_am=row["erstellt_am"],
        issue_type=row["issue_type"],
        titel=row["titel"] or "",
        ziel_url=row["ziel_url"],
        vorschlag=row["vorschlag"] or "",
        begruendung=row["begruendung"] or "",
        gesperrt_grund=row["gesperrt_grund"],
        status=row["status"],
        entschieden_am=row["entschieden_am"],
        entschieden_von=row["entschieden_von"],
        notiz=row["notiz"] or "",
    )


def _kennung(project_id: str, issue_type: str, ziel_url: Optional[str]) -> str:
    """Stabile Kennung für einen Vorschlag.

    Damit derselbe Befund nicht bei jedem täglichen Lauf erneut in der
    Freigabe landet — sonst hätte man nach einer Woche siebenmal dieselbe
    Zeile und würde die Liste nicht mehr ansehen.
    """
    roh = f"{project_id}|{issue_type}|{ziel_url or ''}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, roh))


def zur_freigabe(
    db_pfad: str,
    project_id: str,
    fix: Dict[str, Any],
    begruendung: str,
    audit_id: Optional[str] = None,
    jetzt: Optional[datetime] = None,
) -> Optional[str]:
    """Legt einen Vorschlag in die Freigabe-Schlange.

    Ein bereits entschiedener Vorschlag (freigegeben, abgelehnt, ausgeführt)
    wird NICHT wieder geöffnet: Wer einmal abgelehnt hat, soll nicht jeden Tag
    erneut gefragt werden.
    """
    if not tabelle_anlegen(db_pfad):
        return None

    issue_type = str(fix.get("type") or "")
    ziel_url = fix.get("url")
    kennung = _kennung(project_id, issue_type, ziel_url)

    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[ausfuehrung] Datenbank nicht erreichbar: {exc}")
        return None
    con.row_factory = sqlite3.Row
    try:
        vorhanden = con.execute(
            f"select status from {TABELLE} where id = ?", (kennung,)
        ).fetchone()
        if vorhanden:
            return None  # schon bekannt — egal in welchem Status

        vorschlag = fix.get("suggestion") or fix.get("fix_suggestion") or ""
        con.execute(
            f"insert into {TABELLE} ({', '.join(_SPALTEN)}) "
            f"values ({', '.join('?' * len(_SPALTEN))})",
            (
                kennung,
                project_id,
                audit_id,
                _jetzt(jetzt).isoformat(),
                issue_type,
                str(fix.get("title") or issue_type),
                ziel_url,
                str(vorschlag)[:2000],
                begruendung,
                ist_gesperrt(issue_type),
                STATUS_OFFEN,
                None,
                None,
                "",
            ),
        )
        con.commit()
        return kennung
    except sqlite3.Error as exc:
        logger.warning(f"[ausfuehrung] Freigabe nicht speicherbar: {exc}")
        return None
    finally:
        con.close()


def freigaben(
    db_pfad: str,
    project_id: Optional[str] = None,
    status: Optional[str] = STATUS_OFFEN,
    nur_gesperrte: bool = False,
) -> List[Freigabe]:
    """Liest die Freigabe-Schlange, älteste zuerst."""
    bedingungen: List[str] = []
    werte: List[Any] = []
    if project_id:
        bedingungen.append("project_id = ?")
        werte.append(project_id)
    if status:
        bedingungen.append("status = ?")
        werte.append(status)
    if nur_gesperrte:
        bedingungen.append("gesperrt_grund is not null")

    wo = f"where {' and '.join(bedingungen)}" if bedingungen else ""

    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[ausfuehrung] Datenbank nicht lesbar: {exc}")
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"select {', '.join(_SPALTEN)} from {TABELLE} {wo} "
            "order by erstellt_am asc, id asc",
            werte,
        ).fetchall()
    except sqlite3.Error as exc:
        if "no such table" in str(exc).lower():
            logger.debug("[ausfuehrung] noch keine Freigaben angelegt")
        else:
            logger.warning(f"[ausfuehrung] Freigaben nicht lesbar: {exc}")
        return []
    finally:
        con.close()
    return [_zeile_zu_freigabe(r) for r in rows]


def entscheiden(
    db_pfad: str,
    freigabe_id: str,
    neuer_status: str,
    von: str = "mensch",
    notiz: str = "",
    jetzt: Optional[datetime] = None,
) -> bool:
    """Trägt eine Entscheidung ein (freigegeben / abgelehnt / ausgeführt)."""
    if neuer_status not in (STATUS_FREIGEGEBEN, STATUS_ABGELEHNT, STATUS_AUSGEFUEHRT):
        logger.warning(f"[ausfuehrung] Unbekannter Status {neuer_status!r}")
        return False
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[ausfuehrung] Datenbank nicht erreichbar: {exc}")
        return False
    try:
        cur = con.execute(
            f"update {TABELLE} set status = ?, entschieden_am = ?, "
            "entschieden_von = ?, notiz = ? where id = ?",
            (neuer_status, _jetzt(jetzt).isoformat(), von, notiz, freigabe_id),
        )
        con.commit()
        return cur.rowcount > 0
    except sqlite3.Error as exc:
        logger.warning(f"[ausfuehrung] Entscheidung nicht speicherbar: {exc}")
        return False
    finally:
        con.close()


def veraltete(
    db_pfad: str,
    tage: int = VERFALL_TAGE,
    jetzt: Optional[datetime] = None,
) -> List[Freigabe]:
    """Offene Vorschläge, die zu alt geworden sind.

    Sie werden nicht gelöscht — nur gekennzeichnet. Ein Vorschlag von vor zwei
    Monaten kann sich auf einen Seitenstand beziehen, den es nicht mehr gibt.
    """
    grenze = (_jetzt(jetzt) - timedelta(days=tage)).isoformat()
    return [f for f in freigaben(db_pfad) if f.erstellt_am < grenze]


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


def als_text(liste: Iterable[Freigabe], mit_vorschlag: bool = False) -> str:
    """Die Freigabe-Schlange als lesbare Liste."""
    liste = list(liste)
    if not liste:
        return "Keine offenen Freigaben."

    gesperrte = [f for f in liste if f.ist_gesperrt]
    zeilen = [
        f"{len(liste)} Vorschlag/Vorschläge warten auf Entscheidung "
        f"({len(gesperrte)} davon brauchen immer eine Freigabe).",
        "",
    ]

    nach_projekt: Dict[str, List[Freigabe]] = {}
    for f in liste:
        nach_projekt.setdefault(f.project_id, []).append(f)

    for projekt, gruppe in nach_projekt.items():
        zeilen.append(f"[{projekt}]")
        for f in gruppe:
            marke = "🔒" if f.ist_gesperrt else "·"
            zeilen.append(f"  {marke} {f.titel}")
            if f.ziel_url:
                zeilen.append(f"      {f.ziel_url}")
            zeilen.append(f"      {f.begruendung}")
            if mit_vorschlag and f.vorschlag:
                zeilen.append(f"      Vorschlag: {f.vorschlag[:200]}")
            zeilen.append(f"      Freigeben mit: freigabe --ja {f.id[:8]}")
        zeilen.append("")

    return "\n".join(zeilen).rstrip()
