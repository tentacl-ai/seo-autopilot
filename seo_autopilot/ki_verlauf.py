"""
KI-Sichtbarkeit mit Gedaechtnis — Erweiterung von `scripts/ki_sichtbarkeit.py`.

Der woechentliche Test (Kundenbericht, seit v1.14.0) fragt ChatGPT, Gemini und
Claude mit Websuche und verglich bisher nur mit der Vorwoche. Dieses Modul
speichert jedes Ergebnis und wertet ueber Wochen aus:

- Verlauf je KI ("genannt bei x von y Fragen", je Lauf)
- meistzitierte Quellen: wo sich die KIs ihre Antworten holen — dort muss man
  vorkommen (Verzeichnisse, Bewertungsportale, Wettbewerber)
- Wettbewerber: wer bei welchen Fragen genannt wird, wenn wir es nicht sind
  (optional ``wettbewerber`` in der Fragen-Datei)

Nach Vorbild von OpenSEO (share of voice, cited sources) und den Regeln aus
Anthropics Skill `seo-ai-visibility` (knowledge-work-plugins): keine
erfundene Sichtbarkeits-Punktzahl, nur was bei echten Fragen passiert ist; ein
einzelner Lauf beweist wenig, der Verlauf zaehlt.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABELLE = "ki_sichtbarkeit"
GENANNT = ("verlinkt", "erwaehnt")

_SCHEMA = f"""
create table if not exists {TABELLE} (
    project_id text not null,
    datum text not null,
    ki text not null,
    frage text not null,
    genannt text not null,
    quellen text not null default '[]',
    wettbewerber text not null default '[]',
    modell text not null default '',
    unique (project_id, datum, ki, frage)
);
create index if not exists idx_{TABELLE}_projekt on {TABELLE} (project_id, datum);
"""


def _verbinde(db_pfad: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_pfad)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def speichere(
    db_pfad: str, project_id: str, ergebnis: Dict[str, Any], datum: Optional[str] = None
) -> int:
    """Legt ein Ergebnis von `ki_sichtbarkeit.pruefen()` ab. Fehler-Antworten nicht.

    Ein ausgefallener Abruf ist kein "nicht genannt" — er wuerde den Verlauf
    nach unten ziehen, ohne dass sich etwas geaendert hat.
    """
    tag = datum or datetime.now(timezone.utc).date().isoformat()
    modelle = ergebnis.get("modelle") or {}
    saetze = []
    for frage, je_ki in (ergebnis.get("ergebnisse") or {}).items():
        for ki, e in je_ki.items():
            if e.get("genannt") not in (*GENANNT, "nein"):
                continue
            saetze.append(
                (
                    project_id,
                    tag,
                    ki,
                    frage,
                    e["genannt"],
                    json.dumps(e.get("quellen") or [], ensure_ascii=False),
                    json.dumps(e.get("wettbewerber") or [], ensure_ascii=False),
                    str(modelle.get(ki, "")),
                )
            )
    with _verbinde(db_pfad) as conn:
        conn.executemany(
            f"insert or replace into {TABELLE} (project_id, datum, ki, frage, genannt, "
            "quellen, wettbewerber, modell) values (?, ?, ?, ?, ?, ?, ?, ?)",
            saetze,
        )
    return len(saetze)


def nachtragen(db_pfad: str, project_id: str, ablage: Path) -> int:
    """Liest den Verlauf aus alten Kundenberichten (`bericht-JJJJ-MM-TT.json`) ein."""
    anzahl = 0
    for datei in sorted(Path(ablage).glob("bericht-*.json")):
        try:
            ks = json.loads(datei.read_text(encoding="utf-8")).get("ki_sichtbarkeit")
        except (OSError, ValueError):
            continue
        if isinstance(ks, dict) and ks.get("ergebnisse"):
            datum = datei.stem.replace("bericht-", "")[:10]
            anzahl += speichere(db_pfad, project_id, ks, datum)
    return anzahl


def _laeufe(db_pfad: str, project_id: str, anzahl: int) -> List[str]:
    with _verbinde(db_pfad) as conn:
        rows = conn.execute(
            f"select distinct datum from {TABELLE} where project_id = ? "
            "order by datum desc limit ?",
            (project_id, anzahl),
        ).fetchall()
    return sorted(r["datum"] for r in rows)


def _zeilen(db_pfad: str, project_id: str, daten: List[str]) -> List[sqlite3.Row]:
    if not daten:
        return []
    with _verbinde(db_pfad) as conn:
        return conn.execute(
            f"select * from {TABELLE} where project_id = ? "
            f"and datum in ({','.join('?' * len(daten))})",
            (project_id, *daten),
        ).fetchall()


def verlauf(db_pfad: str, project_id: str, laeufe: int = 8) -> List[Dict[str, Any]]:
    """Je Lauf und KI: genannt bei x von y Fragen (nur erfolgreiche Abrufe)."""
    daten = _laeufe(db_pfad, project_id, laeufe)
    zaehler: Dict[str, Dict[str, List[int]]] = {d: {} for d in daten}
    for r in _zeilen(db_pfad, project_id, daten):
        z = zaehler[r["datum"]].setdefault(r["ki"], [0, 0])
        z[0] += r["genannt"] in GENANNT
        z[1] += 1
    return [
        {
            "datum": d,
            "je_ki": {ki: {"genannt": g, "von": n} for ki, (g, n) in je.items()},
        }
        for d, je in zaehler.items()
    ]


def quellen(
    db_pfad: str, project_id: str, eigener_host: str, laeufe: int = 4, top: int = 10
) -> List[Dict[str, Any]]:
    """Meistzitierte Domains der letzten Laeufe, ohne die eigene."""
    zaehl: Counter = Counter()
    for r in _zeilen(db_pfad, project_id, _laeufe(db_pfad, project_id, laeufe)):
        for d in set(json.loads(r["quellen"] or "[]")):
            if d and eigener_host not in d:
                zaehl[d] += 1
    return [{"domain": d, "antworten": n} for d, n in zaehl.most_common(top)]


def wettbewerber(db_pfad: str, project_id: str) -> Dict[str, Any]:
    """Letzter Lauf: wer wie oft genannt wurde, und Fragen, bei denen nur andere vorkamen."""
    daten = _laeufe(db_pfad, project_id, 1)
    zeilen = _zeilen(db_pfad, project_id, daten)
    namen: Counter = Counter()
    luecken: Dict[str, set] = {}
    wir = sum(1 for r in zeilen if r["genannt"] in GENANNT)
    for r in zeilen:
        andere = json.loads(r["wettbewerber"] or "[]")
        namen.update(set(andere))
        if andere and r["genannt"] not in GENANNT:
            luecken.setdefault(r["frage"], set()).update(andere)
    return {
        "datum": daten[0] if daten else None,
        "antworten": len(zeilen),
        "wir": wir,
        "andere": dict(namen.most_common()),
        "luecken": {f: sorted(n) for f, n in luecken.items()},
    }


def letzter_lauf(db_pfad: str, project_id: str) -> List[Dict[str, Any]]:
    """Letzter Lauf je Frage: welche KI hat uns genannt, wer wurde stattdessen genannt."""
    daten = _laeufe(db_pfad, project_id, 1)
    fragen: Dict[str, Dict[str, Any]] = {}
    for r in _zeilen(db_pfad, project_id, daten):
        f = fragen.setdefault(
            r["frage"], {"frage": r["frage"], "je_ki": {}, "andere": set()}
        )
        f["je_ki"][r["ki"]] = r["genannt"]
        f["andere"].update(json.loads(r["wettbewerber"] or "[]"))
    return [{**f, "andere": sorted(f["andere"])} for f in fragen.values()]


def auswertung(db_pfad: str, project_id: str, eigener_host: str) -> Dict[str, Any]:
    return {
        "verlauf": verlauf(db_pfad, project_id),
        "quellen": quellen(db_pfad, project_id, eigener_host),
        "wettbewerb": wettbewerber(db_pfad, project_id),
    }
