"""
Rangliste — woechentliche Google-Position je Suchbegriff, selbst gebaut.

Nachbau der "Rank Tracking"-Funktion von OpenSEO (every-app/open-seo), aber
ohne DataForSEO: Grundlage ist die Search Console des Kunden. Das kostet
nichts und hat einen Vorteil, den bezahlte Ranglisten nicht haben: Die Search
Console reicht 16 Monate zurueck, der Verlauf steht also vom ersten Tag an
(`nachholen`), statt erst ab Einrichtung zu wachsen.

Grenzen (bewusst so, nicht vergessen):
- Es ist die DURCHSCHNITTLICHE Position ueber alle Einblendungen der Woche,
  keine Momentaufnahme einer einzelnen Suche.
- Ein Suchbegriff ohne Einblendung in der Woche steht als "nicht gefunden" da.
  Das heisst: nicht unter den Treffern, die Nutzer gesehen haben — nicht
  zwingend "nicht in den Top 100".
- Positionen von Wettbewerbern gibt es so nicht (dafuer braucht es eine
  Google-Abfrage von aussen, siehe docs).

Welche Begriffe verfolgt werden:
- `rangliste: {begriffe: [...]}` in projects.yaml (hoechstens 50), sonst
- automatisch die 20 Begriffe mit den meisten Einblendungen der letzten drei
  abgeschlossenen Monate aus `gsc_historie` (ohne Suchbefehle wie "site:").

Aufruf:
    python -m seo_autopilot.rangliste --projekt tentacl-ai --nachholen 12
    python -m seo_autopilot.rangliste --projekt tentacl-ai --bericht
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

TABELLE = "rangliste"
GSC_VERZUG_TAGE = 3
MAX_BEGRIFFE = 50
AUTO_BEGRIFFE = 20
AUTO_MONATE = 3  # automatische Auswahl nur aus den juengsten abgeschlossenen Monaten
# Eingaben mit Suchbefehlen sind Pruefabfragen (meist von uns selbst), keine Nachfrage
SUCHBEFEHLE = ("site:", "inurl:", "intitle:", "cache:", "related:", "filetype:")
ALLE = "alle"
GERAETE = {"MOBILE": "Handy", "DESKTOP": "Computer", "TABLET": "Tablet"}

_SCHEMA = f"""
create table if not exists {TABELLE} (
    project_id text not null,
    woche text not null,
    begriff text not null,
    geraet text not null,
    position real,
    klicks integer not null default 0,
    impressionen integer not null default 0,
    abgerufen_am text not null,
    unique (project_id, woche, begriff, geraet)
);
create index if not exists idx_{TABELLE}_projekt on {TABELLE} (project_id, woche);
"""


@dataclass
class Zeile:
    begriff: str
    position: Optional[float]
    vorwoche: Optional[float]
    vor_4_wochen: Optional[float]
    impressionen: int
    handy: Optional[float]
    computer: Optional[float]


def _verbinde(db_pfad: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_pfad)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------
# Wochen
# --------------------------------------------------------------------------


def letzte_volle_woche(heute: Optional[date] = None) -> Tuple[date, date]:
    """Montag..Sonntag der juengsten Woche, fuer die die Search Console fertig ist."""
    tag = (heute or datetime.now(timezone.utc).date()) - timedelta(days=GSC_VERZUG_TAGE)
    sonntag = tag - timedelta(days=(tag.weekday() + 1) % 7)
    return sonntag - timedelta(days=6), sonntag


def wochenschluessel(montag: date) -> str:
    jahr, woche, _ = montag.isocalendar()
    return f"{jahr}-W{woche:02d}"


def wochen(anzahl: int, heute: Optional[date] = None) -> List[Tuple[date, date]]:
    """Die letzten `anzahl` vollen Wochen, aelteste zuerst."""
    montag, sonntag = letzte_volle_woche(heute)
    return [
        (montag - timedelta(weeks=i), sonntag - timedelta(weeks=i))
        for i in reversed(range(anzahl))
    ]


# --------------------------------------------------------------------------
# Begriffe
# --------------------------------------------------------------------------


def _norm(begriff: str) -> str:
    return " ".join((begriff or "").lower().split())


def begriffe_fuer(db_pfad: str, project_id: str, projekt: Dict[str, Any]) -> List[str]:
    """Konfigurierte Begriffe, sonst die staerksten aus der Monatshistorie."""
    konfig = (projekt.get("rangliste") or {}).get("begriffe") or []
    if konfig:
        return list(dict.fromkeys(_norm(b) for b in konfig if _norm(b)))[:MAX_BEGRIFFE]
    from .historie import _verbinde as historie_verbinde

    with historie_verbinde(db_pfad) as conn:
        monate = [
            r["monat"]
            for r in conn.execute(
                "select distinct monat from gsc_historie where project_id = ? "
                "and vollstaendig = 1 order by monat desc limit ?",
                (project_id, AUTO_MONATE),
            )
        ]
        if not monate:
            return []
        rows = conn.execute(
            "select wert, sum(impressionen) as impr from gsc_historie "
            "where project_id = ? and dimension = 'begriff' and vollstaendig = 1 "
            f"and monat in ({','.join('?' * len(monate))}) "
            "group by wert order by impr desc, wert",
            (project_id, *monate),
        ).fetchall()
    begriffe = [
        _norm(r["wert"])
        for r in rows
        if not any(op in r["wert"].lower() for op in SUCHBEFEHLE)
    ]
    return begriffe[:AUTO_BEGRIFFE]


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def _zeilen_je_begriff(
    zeilen: Sequence[Dict[str, Any]], begriffe: Sequence[str], mit_geraet: bool
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    gesucht = set(begriffe)
    treffer: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for z in zeilen:
        keys = z.get("keys") or []
        begriff = _norm(keys[0]) if keys else ""
        if begriff not in gesucht:
            continue
        geraet = str(keys[1]).upper() if mit_geraet and len(keys) > 1 else ALLE
        treffer[(begriff, geraet)] = z
    return treffer


def speichere_woche(
    db_pfad: str,
    project_id: str,
    woche: str,
    begriffe: Sequence[str],
    gesamt: Sequence[Dict[str, Any]],
    je_geraet: Sequence[Dict[str, Any]],
) -> int:
    """Schreibt eine Woche. Begriff ohne Zeile = nicht gefunden (position NULL)."""
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    saetze = []
    for (begriff, geraet), z in {
        **{(b, ALLE): None for b in begriffe},
        **_zeilen_je_begriff(gesamt, begriffe, False),
        **_zeilen_je_begriff(je_geraet, begriffe, True),
    }.items():
        pos = round(float(z["position"]), 1) if z else None
        saetze.append(
            (project_id, woche, begriff, geraet, pos,
             int(z.get("clicks", 0)) if z else 0,
             int(z.get("impressions", 0)) if z else 0, jetzt)
        )  # fmt: skip
    with _verbinde(db_pfad) as conn:
        conn.execute(
            f"delete from {TABELLE} where project_id = ? and woche = ?",
            (project_id, woche),
        )
        conn.executemany(
            f"insert into {TABELLE} (project_id, woche, begriff, geraet, position, "
            "klicks, impressionen, abgerufen_am) values (?, ?, ?, ?, ?, ?, ?, ?)",
            saetze,
        )
    return len(saetze)


def vorhandene_wochen(db_pfad: str, project_id: str) -> set:
    with _verbinde(db_pfad) as conn:
        rows = conn.execute(
            f"select distinct woche from {TABELLE} where project_id = ?",
            (project_id,),
        ).fetchall()
    return {r["woche"] for r in rows}


async def importiere(
    db_pfad: str,
    project_id: str,
    projekt: Dict[str, Any],
    nachholen: int = 1,
    heute: Optional[date] = None,
    quelle: Any = None,
) -> Dict[str, Any]:
    """Holt fehlende Wochen (hoechstens `nachholen`). Zwei Abfragen je Woche.

    Abfragefehler -> Woche wird NICHT gespeichert (bleibt offen), statt alle
    Begriffe faelschlich als "nicht gefunden" zu buchen.
    """
    from .historie import gsc_konfiguration

    ergebnis = {"geholt": 0, "vorhanden": 0, "fehler": None}
    konfig = gsc_konfiguration(projekt)
    if not konfig:
        ergebnis["fehler"] = "keine Search Console konfiguriert"
        return ergebnis
    property_url, credentials = konfig
    if quelle is None:
        quelle = await _gsc_quelle(credentials)
        if quelle is None:
            ergebnis["fehler"] = "Search-Console-Anmeldung fehlgeschlagen"
            return ergebnis
    begriffe = begriffe_fuer(db_pfad, project_id, projekt) or await _begriffe_direkt(
        quelle, property_url, heute
    )
    if not begriffe:
        ergebnis["fehler"] = "keine Suchbegriffe (keine Suchdaten, nichts konfiguriert)"
        return ergebnis
    bekannt = vorhandene_wochen(db_pfad, project_id)
    for montag, sonntag in wochen(nachholen, heute):
        schluessel = wochenschluessel(montag)
        if schluessel in bekannt:
            ergebnis["vorhanden"] += 1
            continue
        gesamt = await quelle.pull_range(
            property_url, montag, sonntag, ["query"], 25000
        )
        je_geraet = await quelle.pull_range(
            property_url, montag, sonntag, ["query", "device"], 25000
        )
        if gesamt is None or je_geraet is None:
            logger.warning(
                f"[rangliste] {project_id}/{schluessel}: Abfrage fehlgeschlagen"
            )
            continue
        speichere_woche(db_pfad, project_id, schluessel, begriffe, gesamt, je_geraet)
        ergebnis["geholt"] += 1
    return ergebnis


async def _begriffe_direkt(
    quelle: Any, property_url: str, heute: Optional[date]
) -> List[str]:
    """Ohne Monatsarchiv: die staerksten Begriffe der letzten 90 Tage direkt holen."""
    _, bis = letzte_volle_woche(heute)
    zeilen = await quelle.pull_range(
        property_url, bis - timedelta(days=89), bis, ["query"], 500
    )
    zeilen = sorted(zeilen or [], key=lambda z: -int(z.get("impressions", 0)))
    begriffe = [
        _norm(z["keys"][0])
        for z in zeilen
        if z.get("keys") and not any(op in z["keys"][0].lower() for op in SUCHBEFEHLE)
    ]
    return list(dict.fromkeys(begriffe))[:AUTO_BEGRIFFE]


async def _gsc_quelle(credentials: str) -> Any:
    from .sources.gsc import GSCDataSource

    try:
        quelle = GSCDataSource(credentials)
        return quelle if await quelle.authenticate() else None
    except Exception as exc:
        logger.warning(f"[rangliste] Search Console nicht nutzbar: {exc}")
        return None


# --------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------


def tabelle(db_pfad: str, project_id: str) -> Tuple[Optional[str], List[Zeile]]:
    """Juengste Woche mit Vergleich zu Vorwoche und vor vier Wochen."""
    with _verbinde(db_pfad) as conn:
        rows = conn.execute(
            f"select woche, begriff, geraet, position, impressionen from {TABELLE} "
            "where project_id = ? order by woche",
            (project_id,),
        ).fetchall()
    if not rows:
        return None, []
    wochen_sortiert = sorted({r["woche"] for r in rows})
    aktuell = wochen_sortiert[-1]
    vor = {1: None, 4: None}
    for abstand in vor:
        if len(wochen_sortiert) > abstand:
            vor[abstand] = wochen_sortiert[-1 - abstand]
    wert = {(r["woche"], r["begriff"], r["geraet"]): r for r in rows}

    def pos(woche, begriff, geraet=ALLE):
        r = wert.get((woche, begriff, geraet))
        return r["position"] if r else None

    begriffe = sorted({r["begriff"] for r in rows if r["woche"] == aktuell})
    zeilen = [
        Zeile(
            begriff=b,
            position=pos(aktuell, b),
            vorwoche=pos(vor[1], b) if vor[1] else None,
            vor_4_wochen=pos(vor[4], b) if vor[4] else None,
            impressionen=(wert.get((aktuell, b, ALLE)) or {"impressionen": 0})[
                "impressionen"
            ],
            handy=pos(aktuell, b, "MOBILE"),
            computer=pos(aktuell, b, "DESKTOP"),
        )
        for b in begriffe
    ]
    zeilen.sort(key=lambda z: (z.position is None, z.position or 0))
    return aktuell, zeilen


def _p(wert: Optional[float]) -> str:
    return f"{wert:.1f}" if wert is not None else "–"


def _trend(jetzt: Optional[float], vorher: Optional[float]) -> str:
    """Positiv = nach vorn. Position 1 ist die beste."""
    if jetzt is None and vorher is None:
        return ""
    if vorher is None:
        return "neu"
    if jetzt is None:
        return "weg"
    diff = round(vorher - jetzt, 1)
    if abs(diff) < 1.0:
        return "="
    return f"{'▲' if diff > 0 else '▼'}{abs(diff):.1f}"


def bericht_text(db_pfad: str, project_id: str) -> str:
    woche, zeilen = tabelle(db_pfad, project_id)
    if not zeilen:
        return f"{project_id}: noch keine Rangliste (erst --nachholen laufen lassen)."
    kopf = (
        f"Rangliste {project_id}, Woche {woche} (Ø Google-Position laut Search Console)\n"
        f"{'Suchbegriff':<40} {'Pos':>5} {'Vorw.':>6} {'4 Wo.':>6} "
        f"{'Handy':>6} {'PC':>6} {'Einbl.':>6}"
    )
    zeilen_text = [
        f"{z.begriff[:40]:<40} {_p(z.position):>5} {_trend(z.position, z.vorwoche):>6} "
        f"{_trend(z.position, z.vor_4_wochen):>6} {_p(z.handy):>6} {_p(z.computer):>6} "
        f"{z.impressionen:>6}"
        for z in zeilen
    ]
    gefunden = sum(1 for z in zeilen if z.position is not None)
    fuss = (
        f"\n{gefunden} von {len(zeilen)} Begriffen diese Woche bei Google gefunden, "
        f"{sum(1 for z in zeilen if z.position is not None and z.position <= 10)} "
        "davon auf Seite 1."
    )
    return "\n".join([kopf, *zeilen_text]) + fuss


# --------------------------------------------------------------------------
# Aufruf
# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .health import _lade_projekte
    from .historie import standard_db_pfad

    ap = argparse.ArgumentParser(prog="python -m seo_autopilot.rangliste")
    ap.add_argument("--projekt", help="nur dieses Projekt (sonst alle)")
    ap.add_argument("--projects", default="projects.yaml")
    ap.add_argument("--db", default=None)
    ap.add_argument("--nachholen", type=int, default=1, help="so viele Wochen holen")
    ap.add_argument("--bericht", action="store_true")
    args = ap.parse_args(argv)

    from pathlib import Path

    db_pfad = args.db or standard_db_pfad()
    projekte = _lade_projekte(Path(args.projects))
    if not projekte:
        print(f"Keine Projekte gefunden ({args.projects}).")
        return 2
    ziele = {args.projekt: projekte.get(args.projekt)} if args.projekt else projekte
    fehler = 0
    for pid, konfig in ziele.items():
        if konfig is None:
            print(f"{pid}: steht nicht in der Projektliste")
            return 2
        if not args.bericht or args.nachholen > 1:
            e = asyncio.run(importiere(db_pfad, pid, konfig, nachholen=args.nachholen))
            print(f"{pid}: {e['geholt']} Woche(n) geholt, {e['vorhanden']} schon da"
                  + (f" — {e['fehler']}" if e["fehler"] else ""))  # fmt: skip
            if e["fehler"] and "keine Search Console" not in e["fehler"]:
                fehler += 1
        if args.bericht:
            print(bericht_text(db_pfad, pid) + "\n")
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(main())
