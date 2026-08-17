"""
Änderungsbuch — was hat wer wann an der Website geändert.

Der Autopilot konnte bisher sagen, WAS er vorgeschlagen hat, aber nicht mehr,
was davon tatsächlich in der Website gelandet ist. Angewendete Fixes lebten nur
im Audit-Ergebnis des jeweiligen Laufs; wer eine Woche später wissen wollte,
warum ein Titel anders lautet, fand bestenfalls einen Git-Commit — und bei
Projekten ohne Repo gar nichts.

Das rächt sich in dem Moment, in dem die Wirkung gemessen werden soll: Steigt
eine Seite in den Rankings, muss belegbar sein, dass wir es waren. Ändert
gleichzeitig der Kunde selbst den Titel, ist jede Zurechnung wertlos. Deshalb
protokolliert dieses Modul zwei Dinge in dieselbe Tabelle:

  1. **Eigene Änderungen** — jeder Fix, den der ApplyAgent wirklich in eine
     Datei geschrieben hat, mit Vorher, Nachher, Begründung und Commit.
  2. **Fremde Änderungen** — weicht der beim Crawl gefundene Titel oder die
     Meta-Description vom zuletzt protokollierten Stand ab, obwohl wir dort
     nichts angefasst haben, war ein Mensch (oder ein anderes System) am Werk.
     Solche Einträge bekommen `urheber="mensch"`.

Benutzung:

    from seo_autopilot.changelog_book import notiere_aenderung, aenderungen, als_text

    notiere_aenderung(
        "seo_autopilot.db", "joseph", AKTION_META_TITLE,
        ziel_url="https://example.com/", vorher="Alt", nachher="Neu",
    )
    print(als_text(aenderungen("seo_autopilot.db", tage=30)))

Zwei bewusste Entscheidungen, gleich wie in `learning.py`:

1. **Kein alembic.** Dessen `env.py` ist auf async verdrahtet und hinterlässt
   bei `alembic upgrade` halb migrierte Zustände. Die Tabelle wird per
   `CREATE TABLE IF NOT EXISTS` bei jedem Schreibzugriff idempotent angelegt.
2. **Protokollieren darf nie die Änderung kosten.** Eine gesperrte, kaputte
   oder gar nicht vorhandene Datenbank kostet uns einen Buchungssatz, aber
   niemals den Fix selbst oder einen Audit-Lauf. Alle öffentlichen Funktionen
   fangen ihre Fehler selbst ab und liefern einen leeren Ersatzwert
   (leerer String, 0, leere Liste).
"""

from __future__ import annotations

import difflib
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Dieselbe Audit-Datenbank wie die Lernschleife — ein Buch, nicht zwei.
from .learning import standard_db_pfad  # noqa: F401  (bewusst re-exportiert)

logger = logging.getLogger(__name__)

TABELLE = "change_log"

# Standard-Beobachtungsfenster in Tagen (CLI und Berichte).
FENSTER_TAGE = 30

# Wer hat geändert.
URHEBER_AUTOPILOT = "autopilot"
URHEBER_MENSCH = "mensch"
URHEBER_UNBEKANNT = "unbekannt"
URHEBER = (URHEBER_AUTOPILOT, URHEBER_MENSCH, URHEBER_UNBEKANNT)

# Was ist mit der Änderung passiert.
STATUS_ANGEWENDET = "angewendet"
STATUS_ZURUECKGENOMMEN = "zurueckgenommen"
STATUS_FEHLGESCHLAGEN = "fehlgeschlagen"
STATUS = (STATUS_ANGEWENDET, STATUS_ZURUECKGENOMMEN, STATUS_FEHLGESCHLAGEN)

# Art der Änderung. Bewusst grob: Die Wirkungsmessung will später nach
# "Titel geändert" gruppieren, nicht nach dem Befundtyp, der dazu geführt hat.
AKTION_META_TITLE = "meta_title"
AKTION_META_DESCRIPTION = "meta_description"
AKTION_CANONICAL = "canonical"
AKTION_OG_IMAGE = "og_image"
AKTION_SCHEMA = "schema"
AKTION_ROBOTS_TXT = "robots_txt"
AKTION_SITEMAP_XML = "sitemap_xml"
AKTION_REDIRECT = "redirect"
AKTION_UNBEKANNT = "sonstiges"

# Befundtyp -> Aktion. Deckt die Whitelist des ApplyAgent ab; alles andere
# landet unter `AKTION_UNBEKANNT`, statt still verloren zu gehen.
AKTION_JE_ISSUE_TYPE = {
    "missing_title": AKTION_META_TITLE,
    "short_title": AKTION_META_TITLE,
    "long_title": AKTION_META_TITLE,
    "missing_meta_description": AKTION_META_DESCRIPTION,
    "short_meta_description": AKTION_META_DESCRIPTION,
    "long_meta_description": AKTION_META_DESCRIPTION,
    "missing_canonical": AKTION_CANONICAL,
    "canonical_missing": AKTION_CANONICAL,
    "missing_og_image": AKTION_OG_IMAGE,
    "missing_organization_schema": AKTION_SCHEMA,
    "org_schema_no_sameas": AKTION_SCHEMA,
    "missing_robots_txt": AKTION_ROBOTS_TXT,
    "missing_sitemap_xml": AKTION_SITEMAP_XML,
    "redirect_chain": AKTION_REDIRECT,
    "redirect_loop": AKTION_REDIRECT,
}

# Welche Felder einer gecrawlten Seite die Fremderkennung vergleicht.
# (Feld im Seiten-Schnappschuss, Aktion, Klartextname für die Begründung)
UEBERWACHTE_FELDER: Tuple[Tuple[str, str, str], ...] = (
    ("title", AKTION_META_TITLE, "Seitentitel"),
    ("meta_description", AKTION_META_DESCRIPTION, "Meta-Description"),
)

# Ab dieser Länge wird eine Diff-Zeile gekürzt — sonst sprengt eine einzige
# JSON-LD-Zeile jede Telegram-Nachricht.
MAX_DIFF_ZEILEN = 24
MAX_ZEILEN_LAENGE = 300

_SCHEMA = f"""
create table if not exists {TABELLE} (
    id text primary key,
    project_id text not null,
    audit_id text,
    zeitpunkt text not null,
    urheber text not null,
    aktion text not null,
    ziel_url text,
    datei_pfad text,
    vorher text,
    nachher text,
    begruendung text,
    issue_type text,
    git_commit text,
    rueckgaengig_moeglich integer not null default 0,
    rueckgaengig_am text,
    status text not null default '{STATUS_ANGEWENDET}'
);
create index if not exists idx_{TABELLE}_projekt on {TABELLE} (project_id);
create index if not exists idx_{TABELLE}_zeit on {TABELLE} (zeitpunkt);
create index if not exists idx_{TABELLE}_ziel on {TABELLE} (project_id, aktion, ziel_url);
"""

_SPALTEN = (
    "id",
    "project_id",
    "audit_id",
    "zeitpunkt",
    "urheber",
    "aktion",
    "ziel_url",
    "datei_pfad",
    "vorher",
    "nachher",
    "begruendung",
    "issue_type",
    "git_commit",
    "rueckgaengig_moeglich",
    "rueckgaengig_am",
    "status",
)


@dataclass
class Aenderung:
    """Ein Eintrag im Änderungsbuch."""

    id: str
    project_id: str
    zeitpunkt: str
    urheber: str
    aktion: str
    audit_id: Optional[str] = None
    ziel_url: Optional[str] = None
    datei_pfad: Optional[str] = None
    vorher: str = ""
    nachher: str = ""
    begruendung: str = ""
    issue_type: Optional[str] = None
    git_commit: Optional[str] = None
    rueckgaengig_moeglich: bool = False
    rueckgaengig_am: Optional[str] = None
    status: str = STATUS_ANGEWENDET

    @property
    def zurueckgenommen(self) -> bool:
        """Wurde die Änderung wieder entfernt?

        Zwei Wege führen dahin: ein gesetzter Status oder ein Datum in
        `rueckgaengig_am`. Beides zählt, damit ein halb gesetzter Eintrag die
        Wirkungsmessung nicht mit einer längst entfernten Änderung füttert.
        """
        return self.status == STATUS_ZURUECKGENOMMEN or bool(self.rueckgaengig_am)

    @property
    def wirksam(self) -> bool:
        """Steht die Änderung noch in der Website?"""
        return self.status == STATUS_ANGEWENDET and not self.rueckgaengig_am

    @property
    def urheber_klartext(self) -> str:
        return {
            URHEBER_AUTOPILOT: "Autopilot",
            URHEBER_MENSCH: "Mensch",
            URHEBER_UNBEKANNT: "unbekannt",
        }.get(self.urheber, self.urheber)

    @property
    def ziel(self) -> str:
        """Worauf sich die Änderung bezieht — Adresse, sonst Datei."""
        return self.ziel_url or self.datei_pfad or "(ohne Ziel)"


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _jetzt(wert: Optional[datetime] = None) -> datetime:
    if wert is None:
        return datetime.now(timezone.utc)
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def _text(wert: Any) -> str:
    """Alles wird als Text protokolliert — None wird zum leeren String."""
    if wert is None:
        return ""
    if isinstance(wert, str):
        return wert
    return str(wert)


def aktion_fuer(issue_type: Optional[str]) -> str:
    """Befundtyp in eine Aktion übersetzen.

    Unbekannte Typen landen bewusst unter `sonstiges` statt zu verschwinden:
    Ein Eintrag mit grober Aktion ist immer noch besser als eine Lücke im Buch.
    """
    if not issue_type:
        return AKTION_UNBEKANNT
    return AKTION_JE_ISSUE_TYPE.get(issue_type, AKTION_UNBEKANNT)


def tabelle_anlegen(db_pfad: str) -> bool:
    """Legt `change_log` an, falls sie fehlt. Mehrfach aufrufbar.

    Rückgabe: True, wenn die Tabelle danach existiert. Kein alembic — siehe
    Modul-Docstring.
    """
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[buch] Datenbank nicht erreichbar ({db_pfad}): {exc}")
        return False
    try:
        con.executescript(_SCHEMA)
        con.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning(f"[buch] Tabelle {TABELLE} nicht anlegbar: {exc}")
        return False
    finally:
        con.close()


def _zeile_zu_aenderung(row: sqlite3.Row) -> Aenderung:
    return Aenderung(
        id=row["id"],
        project_id=row["project_id"],
        audit_id=row["audit_id"],
        zeitpunkt=row["zeitpunkt"],
        urheber=row["urheber"],
        aktion=row["aktion"],
        ziel_url=row["ziel_url"],
        datei_pfad=row["datei_pfad"],
        vorher=row["vorher"] or "",
        nachher=row["nachher"] or "",
        begruendung=row["begruendung"] or "",
        issue_type=row["issue_type"],
        git_commit=row["git_commit"],
        rueckgaengig_moeglich=bool(row["rueckgaengig_moeglich"]),
        rueckgaengig_am=row["rueckgaengig_am"],
        status=row["status"],
    )


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


def notiere_aenderung(
    db_pfad: str,
    project_id: str,
    aktion: str,
    *,
    audit_id: Optional[str] = None,
    urheber: str = URHEBER_AUTOPILOT,
    ziel_url: Optional[str] = None,
    datei_pfad: Optional[str] = None,
    vorher: Any = None,
    nachher: Any = None,
    begruendung: Any = None,
    issue_type: Optional[str] = None,
    git_commit: Optional[str] = None,
    rueckgaengig_moeglich: bool = False,
    rueckgaengig_am: Optional[str] = None,
    status: str = STATUS_ANGEWENDET,
    zeitpunkt: Optional[datetime] = None,
) -> str:
    """Schreibt einen Eintrag ins Änderungsbuch und gibt dessen ID zurück.

    Rückgabe: die vergebene ID — oder ein **leerer String**, wenn nichts
    geschrieben werden konnte. Es fliegt bewusst keine Ausnahme nach oben:
    Ein Protokollfehler darf die eigentliche Änderung niemals abbrechen. Wer
    den Rückgabewert prüfen will, kann das tun; wer nicht, verliert nichts.

    `zeitpunkt` ist injizierbar, damit Tests Zeitfenster prüfen können, ohne
    die Systemuhr zu verstellen.
    """
    try:
        if not tabelle_anlegen(db_pfad):
            return ""

        kennung = uuid.uuid4().hex[:16]
        werte = (
            kennung,
            _text(project_id),
            audit_id,
            _jetzt(zeitpunkt).isoformat(),
            urheber if urheber in URHEBER else URHEBER_UNBEKANNT,
            _text(aktion) or AKTION_UNBEKANNT,
            ziel_url,
            datei_pfad,
            _text(vorher),
            _text(nachher),
            _text(begruendung),
            issue_type,
            git_commit,
            1 if rueckgaengig_moeglich else 0,
            rueckgaengig_am,
            status if status in STATUS else STATUS_ANGEWENDET,
        )

        con = sqlite3.connect(db_pfad)
        try:
            con.execute(
                f"insert into {TABELLE} ({', '.join(_SPALTEN)}) "
                f"values ({', '.join('?' * len(_SPALTEN))})",
                werte,
            )
            con.commit()
        finally:
            con.close()

        logger.info(
            f"[buch] {project_id}: {aktion} von {urheber} protokolliert "
            f"({ziel_url or datei_pfad or '-'})"
        )
        return kennung
    except Exception as exc:  # bewusst breit: Protokoll darf nie der Grund sein
        logger.warning(f"[buch] Änderung nicht protokollierbar ({db_pfad}): {exc}")
        return ""


def markiere_zurueckgenommen(
    db_pfad: str,
    aenderung_id: str,
    zeitpunkt: Optional[datetime] = None,
) -> bool:
    """Vermerkt, dass eine Änderung wieder entfernt wurde.

    Der Eintrag bleibt stehen — gelöscht wird im Buch nie, sonst fehlt später
    genau die Zeile, die erklärt, warum eine Wirkung wieder verschwunden ist.
    """
    stempel = _jetzt(zeitpunkt).isoformat()
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[buch] Datenbank nicht erreichbar ({db_pfad}): {exc}")
        return False
    try:
        cur = con.execute(
            f"update {TABELLE} set status = ?, rueckgaengig_am = ? where id = ?",
            (STATUS_ZURUECKGENOMMEN, stempel, aenderung_id),
        )
        con.commit()
        return cur.rowcount > 0
    except sqlite3.Error as exc:
        logger.warning(f"[buch] Rücknahme nicht speicherbar: {exc}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


def aenderungen(
    db_pfad: str,
    project_id: Optional[str] = None,
    tage: int = FENSTER_TAGE,
    nur_offene: bool = False,
    jetzt: Optional[datetime] = None,
) -> List[Aenderung]:
    """Liest das Änderungsbuch — chronologisch, älteste zuerst.

    `tage` begrenzt das Fenster (0 oder negativ = alles), `project_id` filtert
    auf ein Projekt. `nur_offene=True` liefert nur Änderungen, die noch in der
    Website stehen: Status `angewendet` und ohne Rücknahmedatum. Genau die sind
    für die Wirkungsmessung relevant — eine zurückgenommene Änderung erklärt
    keine heutigen Rankings mehr.

    Bei nicht lesbarer Datenbank kommt eine leere Liste zurück, keine Ausnahme.
    """
    bedingungen: List[str] = []
    werte: List[Any] = []

    if tage and tage > 0:
        bedingungen.append("zeitpunkt >= ?")
        werte.append((_jetzt(jetzt) - timedelta(days=int(tage))).isoformat())
    if project_id:
        bedingungen.append("project_id = ?")
        werte.append(project_id)
    if nur_offene:
        bedingungen.append("status = ?")
        werte.append(STATUS_ANGEWENDET)
        bedingungen.append("(rueckgaengig_am is null or rueckgaengig_am = '')")

    wo = f"where {' and '.join(bedingungen)}" if bedingungen else ""

    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[buch] Datenbank nicht lesbar ({db_pfad}): {exc}")
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"select {', '.join(_SPALTEN)} from {TABELLE} {wo} "
            "order by zeitpunkt asc, id asc",
            werte,
        ).fetchall()
    except sqlite3.Error as exc:
        # Zwei sehr verschiedene Fälle, die nicht gleich klingen dürfen:
        # Eine fehlende Tabelle heißt schlicht "es wurde noch nie etwas
        # geändert" — das ist der Normalzustand einer frischen Installation
        # und keine Warnung wert. Alles andere ist ein echtes Problem.
        if "no such table" in str(exc).lower():
            logger.debug(f"[buch] noch kein Änderungsbuch angelegt: {exc}")
        else:
            logger.warning(f"[buch] Änderungen nicht lesbar: {exc}")
        return []
    finally:
        con.close()

    return [_zeile_zu_aenderung(r) for r in rows]


def letzter_stand(
    db_pfad: str,
    project_id: str,
    aktion: str,
    ziel_url: str,
) -> Optional[Aenderung]:
    """Der zuletzt protokollierte Stand für eine Adresse und eine Aktion.

    Grundlage der Fremderkennung: Was hier als `nachher` steht, ist der Wert,
    den wir hinterlassen haben. Steht beim nächsten Crawl etwas anderes auf
    der Seite, hat jemand anderes angefasst.
    """
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[buch] Datenbank nicht lesbar ({db_pfad}): {exc}")
        return None
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            f"select {', '.join(_SPALTEN)} from {TABELLE} "
            "where project_id = ? and aktion = ? and ziel_url = ? and status = ? "
            "order by zeitpunkt desc, id desc limit 1",
            (project_id, aktion, ziel_url, STATUS_ANGEWENDET),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.warning(f"[buch] Letzter Stand nicht lesbar: {exc}")
        return None
    finally:
        con.close()

    return _zeile_zu_aenderung(row) if row else None


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


def _kuerze(zeile: str) -> str:
    if len(zeile) <= MAX_ZEILEN_LAENGE:
        return zeile
    return zeile[:MAX_ZEILEN_LAENGE] + " […]"


def diff_text(aenderung: Aenderung, max_zeilen: int = MAX_DIFF_ZEILEN) -> str:
    """Lesbarer Vorher/Nachher-Vergleich einer Änderung.

    Nutzt `difflib.unified_diff`, damit auch mehrzeilige Werte (JSON-LD,
    robots.txt) verständlich bleiben. Gekürzt auf `max_zeilen`, weil sonst ein
    einziger Schema-Block jede Telegram-Nachricht sprengt.
    """
    vorher = _text(aenderung.vorher)
    nachher = _text(aenderung.nachher)

    if not vorher and not nachher:
        return "(kein Vorher/Nachher protokolliert)"
    if vorher == nachher:
        return "(keine Textänderung protokolliert)"
    if not vorher:
        return "\n".join(
            ["(vorher nichts vorhanden)"]
            + [f"+ {_kuerze(z)}" for z in nachher.splitlines()[:max_zeilen]]
        )

    zeilen = list(
        difflib.unified_diff(
            vorher.splitlines(),
            nachher.splitlines(),
            fromfile="vorher",
            tofile="nachher",
            lineterm="",
            n=1,
        )
    )
    gekuerzt = [_kuerze(z) for z in zeilen[:max_zeilen]]
    if len(zeilen) > max_zeilen:
        gekuerzt.append(f"… ({len(zeilen) - max_zeilen} weitere Zeilen)")
    return "\n".join(gekuerzt)


def als_text(
    eintraege: List[Aenderung],
    tage: int = FENSTER_TAGE,
    mit_diff: bool = False,
) -> str:
    """Änderungsbuch als deutscher Klartext (CLI und Telegram).

    Chronologisch, älteste zuerst — das Buch liest sich von vorne nach hinten.
    Jede Zeile nennt Zeitpunkt, Urheber und Ziel, damit auf einen Blick klar
    ist, ob eine Veränderung von uns kam oder nicht.
    """
    if not eintraege:
        return f"Keine protokollierten Änderungen in den letzten {tage} Tagen."

    fremde = [a for a in eintraege if a.urheber == URHEBER_MENSCH]
    eigene = [a for a in eintraege if a.urheber == URHEBER_AUTOPILOT]
    offen = len(eintraege) - len(fremde) - len(eigene)

    kopf = (
        f"Änderungsbuch — letzte {tage} Tage: {len(eintraege)} Einträge "
        f"({len(eigene)} vom Autopilot, {len(fremde)} von Hand"
    )
    kopf += f", {offen} ohne Urheber)." if offen else ")."
    zeilen = [kopf, ""]

    letztes_projekt = None
    for a in eintraege:
        if a.project_id != letztes_projekt:
            if letztes_projekt is not None:
                zeilen.append("")
            zeilen.append(f"[{a.project_id}]")
            letztes_projekt = a.project_id

        marke = {
            STATUS_ZURUECKGENOMMEN: " (zurueckgenommen)",
            STATUS_FEHLGESCHLAGEN: " (fehlgeschlagen)",
        }.get(a.status, "")
        zeilen.append(
            f"  {a.zeitpunkt[:16].replace('T', ' ')}  "
            f"{a.urheber_klartext:<10} {a.aktion:<18} {a.ziel}{marke}"
        )
        if a.begruendung:
            zeilen.append(f"      Grund: {a.begruendung}")
        if a.git_commit:
            zeilen.append(f"      Commit: {a.git_commit}")
        if mit_diff:
            for d in diff_text(a).splitlines():
                zeilen.append(f"      {d}")

    if fremde:
        zeilen.append("")
        zeilen.append(
            f"Achtung: {len(fremde)} Änderung(en) stammen NICHT vom Autopilot. "
            "Deren Wirkung darf uns nicht zugerechnet werden."
        )
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Fremderkennung
# ---------------------------------------------------------------------------


def erkenne_fremde_aenderungen(
    db_pfad: str,
    project_id: str,
    aktuelle_seiten: Iterable[Dict[str, Any]],
    basis_erfassen: bool = True,
) -> List[Dict[str, Any]]:
    """Findet Änderungen, die nicht von uns stammen.

    Vergleicht Titel und Meta-Description der frisch gecrawlten Seiten mit dem
    zuletzt protokollierten Stand. Weicht etwas ab, obwohl wir dort seither
    nichts geschrieben haben, war ein Mensch (oder ein anderes System) am Werk.
    Ohne diesen Abgleich rechnet die spätere Wirkungsmessung fremde Effekte uns
    zu — und ein Ranking-Sprung, den der Kunde selbst ausgelöst hat, landet in
    unserem Erfolgsbericht.

    `aktuelle_seiten` sind Schnappschüsse mit den Schlüsseln `url` (bzw.
    `final_url`), `title` und `meta_description` — genau das, was
    `_page_snapshot()` im AnalyzerAgent liefert.

    Rückgabe: Liste fertiger Buchungssätze (Schlüssel wie die Parameter von
    `notiere_aenderung`). Geschrieben wird hier nichts — dafür ist
    `protokolliere_fremde_aenderungen()` zuständig. Einträge mit
    `urheber="mensch"` sind echte Abweichungen; ist `basis_erfassen=True`,
    kommen für bisher unbekannte Seiten zusätzlich Einträge mit
    `urheber="unbekannt"` dazu. Die sind kein Vorwurf, sondern der
    Vergleichspunkt, ohne den beim nächsten Crawl nichts erkennbar wäre.

    Bei nicht lesbarer Datenbank kommt eine leere Liste zurück, keine Ausnahme.
    """
    funde: List[Dict[str, Any]] = []
    # Erst prüfen, ob das Buch überhaupt beschreibbar ist. Sonst würden wir bei
    # kaputter Datenbank lauter "Erststand"-Sätze erzeugen, die niemand
    # speichern kann — und beim nächsten Lauf wieder dieselben.
    if not tabelle_anlegen(db_pfad):
        return []
    try:
        for seite in aktuelle_seiten or []:
            if not isinstance(seite, dict):
                logger.debug(f"[buch] Seite ignoriert (kein dict): {seite!r}")
                continue
            url = seite.get("final_url") or seite.get("url")
            if not url:
                continue

            for feld, aktion, klartext in UEBERWACHTE_FELDER:
                aktuell = _text(seite.get(feld)).strip()
                stand = letzter_stand(db_pfad, project_id, aktion, url)

                if stand is None:
                    # Noch nie protokolliert: Es gibt nichts zu vergleichen.
                    # Ein Vorwurf wäre hier falsch — wir legen nur den
                    # Vergleichspunkt an, damit die NÄCHSTE Abweichung auffällt.
                    if basis_erfassen and aktuell:
                        funde.append(
                            {
                                "urheber": URHEBER_UNBEKANNT,
                                "aktion": aktion,
                                "ziel_url": url,
                                "vorher": "",
                                "nachher": aktuell,
                                "begruendung": (
                                    f"{klartext} erstmals erfasst — Vergleichspunkt "
                                    "für die Fremderkennung"
                                ),
                                "status": STATUS_ANGEWENDET,
                                "rueckgaengig_moeglich": False,
                            }
                        )
                    continue

                if _text(stand.nachher).strip() == aktuell:
                    continue

                funde.append(
                    {
                        "urheber": URHEBER_MENSCH,
                        "aktion": aktion,
                        "ziel_url": url,
                        "vorher": stand.nachher,
                        "nachher": aktuell,
                        "begruendung": (
                            f"{klartext} weicht vom protokollierten Stand ab — "
                            "vom Autopilot stammt diese Änderung nicht"
                        ),
                        "status": STATUS_ANGEWENDET,
                        "rueckgaengig_moeglich": False,
                    }
                )
    except Exception as exc:  # Erkennung darf keinen Audit-Lauf kosten
        logger.warning(f"[buch] Fremderkennung fehlgeschlagen: {exc}")
        return funde

    fremde = [f for f in funde if f["urheber"] == URHEBER_MENSCH]
    if fremde:
        logger.info(
            f"[buch] {project_id}: {len(fremde)} fremde Änderung(en) erkannt "
            "(nicht vom Autopilot)"
        )
    return funde


def protokolliere_fremde_aenderungen(
    db_pfad: str,
    project_id: str,
    audit_id: Optional[str],
    funde: Iterable[Dict[str, Any]],
    zeitpunkt: Optional[datetime] = None,
) -> int:
    """Schreibt die Funde der Fremderkennung ins Buch. Rückgabe: Anzahl Zeilen."""
    geschrieben = 0
    for f in funde or []:
        if not isinstance(f, dict):
            continue
        kennung = notiere_aenderung(
            db_pfad,
            project_id,
            f.get("aktion") or AKTION_UNBEKANNT,
            audit_id=audit_id,
            urheber=f.get("urheber") or URHEBER_UNBEKANNT,
            ziel_url=f.get("ziel_url"),
            vorher=f.get("vorher"),
            nachher=f.get("nachher"),
            begruendung=f.get("begruendung"),
            status=f.get("status") or STATUS_ANGEWENDET,
            rueckgaengig_moeglich=bool(f.get("rueckgaengig_moeglich")),
            zeitpunkt=zeitpunkt,
        )
        if kennung:
            geschrieben += 1
    return geschrieben


# ---------------------------------------------------------------------------
# Vorher/Nachher aus einem Git-Diff
# ---------------------------------------------------------------------------


def vorher_nachher_aus_diff(diff: str) -> Tuple[str, str]:
    """Zieht Vorher und Nachher aus dem Diff eines ApplyResult.

    Der StaticFilesAdapter liefert als Beleg `git show`-Ausgabe. Für das Buch
    zählt nur, was tatsächlich ersetzt wurde: die `-`-Zeilen als Vorher, die
    `+`-Zeilen als Nachher. Dateiköpfe (`---`, `+++`) fliegen raus.

    Ohne Git-Repo steht dort ein Platzhaltertext; dann kommt zweimal ein leerer
    String zurück und der Aufrufer nimmt seine eigenen Werte.
    """
    if not diff or diff.startswith("("):
        return "", ""

    alt: List[str] = []
    neu: List[str] = []
    for zeile in diff.splitlines():
        if zeile.startswith("---") or zeile.startswith("+++"):
            continue
        if zeile.startswith("-"):
            alt.append(zeile[1:].strip())
        elif zeile.startswith("+"):
            neu.append(zeile[1:].strip())
    return "\n".join(alt), "\n".join(neu)
