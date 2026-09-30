"""
Google-Updates im Messzeitraum — damit die Wirkungsmessung Googles Schwankungen
nicht als unseren Erfolg (oder Misserfolg) verbucht.

Grundlage ist `daten/google_updates.json` (aus claude-seo, siehe
`daten/HERKUNFT.md`): nur Eintraege mit Google-eigener Quelle. Unbestaetigte
Meldungen (`unverified[]`) werden bewusst NICHT verwendet.

Die Liste nennt nur den Start. Die Ausrolldauer ist unsere Annahme: Core
Updates 21 Tage, Spam Updates 14 Tage ab Start (einzelne Core Updates liefen
deutlich laenger, z. B. Maerz 2024).

- Core Update (``core``, ``core+spam``) im Messzeitraum -> das Ergebnis ist
  nicht zurechenbar.
- Spam Update -> nur Vermerk; es trifft Spam-Seiten, nicht die kleinen
  Kundenseiten, die wir betreuen.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

DATEI = Path(__file__).parent / "daten" / "google_updates.json"

AUSROLLEN_TAGE = {"core": 21, "core+spam": 21, "spam": 14}
ZURECHNUNG_SPERRENDE_ARTEN = {"core", "core+spam"}

# Aelter als das -> Wächter-Hinweis, die Liste neu zu kopieren
VERALTET_NACH_TAGEN = 120


@dataclass(frozen=True)
class GoogleUpdate:
    start: date
    ende: date
    name: str
    art: str
    quelle: str

    @property
    def sperrt_zurechnung(self) -> bool:
        return self.art in ZURECHNUNG_SPERRENDE_ARTEN


@lru_cache(maxsize=1)
def _laden(pfad: str = str(DATEI)) -> tuple:
    try:
        roh = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"[google-updates] Liste nicht lesbar ({pfad}): {exc}")
        return (), None
    updates = []
    for eintrag in roh.get("updates") or []:
        art = eintrag.get("kind", "")
        if art not in AUSROLLEN_TAGE:
            continue
        try:
            start = date.fromisoformat(eintrag["date"])
        except (KeyError, ValueError):
            continue
        updates.append(
            GoogleUpdate(
                start=start,
                ende=start + timedelta(days=AUSROLLEN_TAGE[art] - 1),
                name=eintrag.get("name", ""),
                art=art,
                quelle=eintrag.get("source", ""),
            )
        )
    stand = roh.get("last_verified")
    return tuple(sorted(updates, key=lambda u: u.start)), stand


def ranking_updates(pfad: Optional[str] = None) -> List[GoogleUpdate]:
    """Alle bestaetigten Core-/Spam-Updates mit angenommenem Ausroll-Ende."""
    return list(_laden(pfad or str(DATEI))[0])


def stand(pfad: Optional[str] = None) -> Optional[date]:
    """Datum der letzten Pruefung der Liste (``last_verified``)."""
    wert = _laden(pfad or str(DATEI))[1]
    try:
        return date.fromisoformat(wert) if wert else None
    except ValueError:
        return None


def ist_veraltet(heute: date, pfad: Optional[str] = None) -> bool:
    geprueft = stand(pfad)
    return geprueft is None or (heute - geprueft).days > VERALTET_NACH_TAGEN


def im_zeitraum(von: date, bis: date, pfad: Optional[str] = None) -> List[GoogleUpdate]:
    """Updates, deren Ausrollzeit sich mit [von, bis] ueberschneidet."""
    return [u for u in ranking_updates(pfad) if u.start <= bis and u.ende >= von]
