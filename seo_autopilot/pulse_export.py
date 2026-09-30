"""
Schnappschuss fuer die Steuerzentrale (Tentacl Pulse) — Seite "Sichtbarkeit".

Die Steuerzentrale liest nur, sie rechnet nichts nach und haelt keine eigene
Kopie in ihrer Datenbank: die Wahrheit bleibt hier. Je Projekt eine Datei
``reports/pulse/<projekt>.json``, die der Container nur lesend einbindet
(wie den Redaktionsplan des social-agent).

Inhalt, alles aus vorhandenen Auswertungen, keine neuen Abrufe:
- ``website``: Note mit Verlauf, offene/umgesetzte Empfehlungen (Kundenbericht)
- ``google``: Rangliste je Suchbegriff + Verlauf je Woche (rangliste.py)
- ``ki``: KI-Sichtbarkeit je Lauf, je Frage, Quellen, Wettbewerber (ki_verlauf.py)

Aufruf: ``python -m seo_autopilot.pulse_export`` (alle aktiven Berichtsprojekte)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from . import kundenbericht as kb

logger = logging.getLogger(__name__)

VERSION = 1
ZIEL = kb.WURZEL / "reports" / "pulse"


def _teil(fn: Callable[[], Any]) -> Any:
    """Ein fehlender Teil darf den Schnappschuss nicht verhindern."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[pulse] Teil nicht lesbar: {exc}")
        return {"fehler": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _website(db: str, schluessel: str) -> Dict[str, Any]:
    w = kb.wo_wir_stehen(db, schluessel, tage=7)
    return {
        k: w.get(k)
        for k in (
            "note",
            "note_vor_woche",
            "umgestellt",
            "seiten",
            "selbst_erledigt",
            "offen",
            "umgesetzt",
            "von_hand",
            "naechste",
        )
        if k in w
    }


def _google(db: str, schluessel: str) -> Dict[str, Any]:
    from .rangliste import wochenverlauf

    aus = kb.rangliste_fuer_bericht(db, schluessel, top=30)
    if aus:
        aus["verlauf"] = wochenverlauf(db, schluessel)
    return aus


def _ki(db: str, schluessel: str, host: str) -> Dict[str, Any]:
    from . import ki_verlauf as kv

    aus = kv.auswertung(db, schluessel, host)
    if not aus["verlauf"]:
        return {}
    aus["fragen"] = kv.letzter_lauf(db, schluessel)
    return aus


def schnappschuss(db: str, projects: str, schluessel: str) -> Dict[str, Any]:
    cfg = kb.lade_projekte(projects)[schluessel]
    host = kb.host_von(cfg)
    return {
        "version": VERSION,
        "projekt": schluessel,
        "name": cfg.get("name") or schluessel,
        "host": host,
        "stand": datetime.now().astimezone().isoformat(timespec="minutes"),
        "website": _teil(lambda: _website(db, schluessel)),
        "google": _teil(lambda: _google(db, schluessel)),
        "ki": _teil(lambda: _ki(db, schluessel, host)),
    }


def schreiben(daten: Dict[str, Any], ziel: Path = ZIEL) -> Path:
    """Atomar: die Steuerzentrale liest nie eine halb geschriebene Datei."""
    ziel.mkdir(parents=True, exist_ok=True)
    datei = ziel / f"{daten['projekt']}.json"
    fd, tmp = tempfile.mkstemp(dir=ziel, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, datei)
    return datei


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .historie import standard_db_pfad

    ap = argparse.ArgumentParser(prog="python -m seo_autopilot.pulse_export")
    ap.add_argument("--projekt", help="nur dieses Projekt (sonst alle mit Bericht)")
    ap.add_argument("--projects", default=str(kb.WURZEL / "projects.yaml"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--ziel", default=str(ZIEL))
    args = ap.parse_args(argv)

    db = args.db or standard_db_pfad()
    projekte = kb.lade_projekte(args.projects)
    ziele = [args.projekt] if args.projekt else kb.berichtsprojekte(projekte)
    for schluessel in ziele:
        if schluessel not in projekte:
            print(f"{schluessel}: steht nicht in der Projektliste")
            return 2
        datei = schreiben(schnappschuss(db, args.projects, schluessel), Path(args.ziel))
        print(f"{schluessel}: {datei}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
