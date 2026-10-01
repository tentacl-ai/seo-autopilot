"""Nicht-monetäre Website-Ziele aus externen Berichtssnapshots.

Manche Ziele entstehen serverseitig und dürfen nicht von Cookie-Einwilligung
oder einem Browser-Ereignis abhängen. Ein externer Wochenbericht kann z. B.
erfolgreich gespeicherte Bewerbungen direkt aus dem Bewerbungsbestand zählen. Dieses
Modul macht solche belastbaren Zielzahlen für die SEO-Priorisierung nutzbar,
ohne daraus einen erfundenen Euro-Wert abzuleiten.
"""

from __future__ import annotations

import glob
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _datum(wert: Any) -> Optional[date]:
    try:
        return datetime.fromisoformat(str(wert)).date()
    except (TypeError, ValueError):
        return None


def _zahl(wert: Any) -> int:
    try:
        return max(0, int(wert or 0))
    except (TypeError, ValueError):
        return 0


def lade_zielsignale(
    projekt: Dict[str, Any],
    tage: int = 28,
    heute: Optional[date] = None,
) -> Dict[str, Any]:
    """Liest und dedupliziert abgeschlossene Wochen aus JSON-Berichten.

    Konfiguration unter ``source_config.conversions``::

        weekly_report_glob: /var/lib/kunde/bericht-*.json
        goal_name: Bewerbung abgesendet
        goal_page: /retreats

    Jeder Zeitraum wird nur einmal gezählt, auch wenn der neuere Bericht die
    aktuelle Woche und zum Vergleich noch einmal die Vorwoche enthält.
    """
    cfg = ((projekt or {}).get("source_config") or {}).get("conversions") or {}
    muster = str(cfg.get("weekly_report_glob") or "").strip()
    zielseite = str(cfg.get("goal_page") or "").strip()
    if not muster or not zielseite:
        return {}

    stichtag = heute or date.today()
    grenze = stichtag - timedelta(days=max(1, int(tage)))
    wochen: Dict[date, Dict[str, Any]] = {}

    for dateiname in sorted(glob.glob(muster)):
        try:
            pfad = Path(dateiname)
            inhalt = json.loads(pfad.read_text(encoding="utf-8"))
            start = _datum((inhalt.get("zeitraum") or {}).get("start"))
            if start is None:
                continue
            ziele = inhalt.get("ziele") or {}
            bewerbungen = inhalt.get("bewerbungen") or {}
            kandidaten = [
                (
                    start,
                    (ziele.get("woche") or {}).get(
                        "bewerbungen", bewerbungen.get("woche", 0)
                    ),
                    (ziele.get("woche") or {}).get("nach_kanal", {}),
                ),
                (
                    start - timedelta(days=7),
                    (ziele.get("vorwoche") or {}).get(
                        "bewerbungen", bewerbungen.get("vorwoche", 0)
                    ),
                    (ziele.get("vorwoche") or {}).get("nach_kanal", {}),
                ),
            ]
            for beginn, anzahl, kanaele in kandidaten:
                if beginn + timedelta(days=7) <= grenze:
                    continue
                bisher = wochen.get(beginn) or {}
                kanalwerte = {str(k): _zahl(v) for k, v in (kanaele or {}).items()}
                if not kanalwerte and bisher.get("nach_kanal"):
                    kanalwerte = bisher["nach_kanal"]
                wochen[beginn] = {
                    "start": beginn.isoformat(),
                    "anzahl": _zahl(anzahl),
                    "nach_kanal": kanalwerte,
                }
        except Exception as exc:
            logger.warning(f"[zielsignale] {dateiname} nicht lesbar: {exc}")

    if not wochen:
        return {
            "ziel_name": str(cfg.get("goal_name") or "Ziel"),
            "zielseite": zielseite,
            "gesamt": 0,
            "nach_kanal": {},
            "wochen": [],
        }

    nach_kanal: Dict[str, int] = {}
    for woche in wochen.values():
        for kanal, anzahl in woche["nach_kanal"].items():
            nach_kanal[kanal] = nach_kanal.get(kanal, 0) + anzahl

    sortiert = [wochen[k] for k in sorted(wochen)]
    return {
        "ziel_name": str(cfg.get("goal_name") or "Ziel"),
        "zielseite": zielseite,
        "gesamt": sum(w["anzahl"] for w in sortiert),
        "nach_kanal": dict(sorted(nach_kanal.items(), key=lambda x: (-x[1], x[0]))),
        "wochen": sortiert,
    }
