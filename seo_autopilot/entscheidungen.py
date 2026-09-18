"""
Entscheidungen mit Knopf — Anschluss an die Entscheidungsseiten auf tentacl.de/freigabe/e.

Es gibt EINE Stelle, an der Robert entscheidet: den Freigabe-Dienst im
social-agent (`<eigene Freigabe-App>`). Der liest drei Dateien
aus `<ENTSCHEIDUNGEN_ORDNER aus der .env>/`:

- `entscheidungen_dyn.json`      {id: {id, titel, text, kanal, seit, optionen, dyn, ...}}
- `entscheidungen_tokens.json`   {token: {id, zuletzt}}  - Link gilt 14 Tage nach `zuletzt`
- `entscheidungen_antworten.jsonl`  eine Zeile je Klick {zeit, id, titel, wahl, text}

Dasselbe Format schreibt die Marketing-Tagesmail (`tools/tagesmail.py`,
`ent_token_fuer`). Punkte aus dem SEO-Bericht tragen `quelle: seo-bericht`,
damit die Marketing-Mail sie nicht zusaetzlich auflistet — auf der
Entscheidungsseite erscheinen trotzdem alle offenen Punkte gemeinsam.

Hier wird nie etwas ausgefuehrt. Ein Klick wird nur festgehalten; was daraus
folgt, entscheidet der naechste Berichtslauf (`antworten_uebernehmen`).
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from .core.config import settings

logger = logging.getLogger(__name__)

ORDNER = Path(settings.ENTSCHEIDUNGEN_ORDNER or "")
BASIS_URL = "https://tentacl.de/freigabe/e"
QUELLE = "seo-bericht"
OPTIONEN_JA_NEIN = [["ja", "Ja, machen"], ["spaeter", "Später"], ["nein", "Nein"]]


def _laden(pfad: Path, leer: Any) -> Any:
    if not pfad.exists():
        return leer
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return leer


def _schreiben(pfad: Path, daten: Any) -> None:
    pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")


def token_fuer(eid: str, ordner: Path = ORDNER, heute: Optional[date] = None) -> str:
    """Wie tagesmail.ent_token_fuer: vorhandenen Token wiederverwenden, `zuletzt` auffrischen."""
    pfad = ordner / "entscheidungen_tokens.json"
    tokens = _laden(pfad, {})
    tag = (heute or date.today()).isoformat()
    tok = next(
        (
            t
            for t, w in tokens.items()
            if (w.get("id") if isinstance(w, dict) else w) == eid
        ),
        None,
    ) or secrets.token_urlsafe(18)
    if tokens.get(tok) != {"id": eid, "zuletzt": tag}:
        tokens[tok] = {"id": eid, "zuletzt": tag}
        _schreiben(pfad, tokens)
    return tok


def punkte_anlegen(
    punkte: Sequence[Dict[str, Any]],
    ordner: Path = ORDNER,
    heute: Optional[date] = None,
) -> Dict[str, str]:
    """Legt Punkte an (oder frischt sie auf) und gibt {id: url} zurueck.

    Jeder Punkt braucht `id` und `titel`; `text`, `kanal`, `optionen`, `link` optional.
    Bereits beantwortete Punkte werden NICHT erneut angelegt.
    """
    if not ordner.exists():
        logger.warning(f"[Entscheidungen] Ordner fehlt: {ordner} - keine Knoepfe")
        return {}
    tag = (heute or date.today()).isoformat()
    pfad = ordner / "entscheidungen_dyn.json"
    dyn = _laden(pfad, {})
    beantwortet = antworten(ordner=ordner)
    urls: Dict[str, str] = {}
    for p in punkte:
        eid = p["id"]
        if eid in beantwortet:
            continue
        alt = dyn.get(eid, {})
        dyn[eid] = {
            "id": eid,
            "titel": str(p["titel"])[:200],
            "text": p.get("text", ""),
            "kanal": p.get("kanal", "SEO"),
            # "seit" = letzter Bericht: die Tagesmail raeumt Punkte weg, deren "seit" aelter
            # als 14 Tage ist. Wann der Punkt zum ersten Mal kam, steht in "erstmals".
            "seit": tag,
            "erstmals": alt.get("erstmals", tag),
            "optionen": p.get("optionen") or OPTIONEN_JA_NEIN,
            "dyn": True,  # jede Antwort schliesst den Punkt
            "quelle": QUELLE,
            **(
                {"link": p["link"], "knopf": p.get("knopf", "Ansehen")}
                if p.get("link")
                else {}
            ),
        }
        urls[eid] = f"{BASIS_URL}/{token_fuer(eid, ordner, heute)}"
    _schreiben(pfad, dyn)
    return urls


def antworten(
    ordner: Path = ORDNER, ids: Optional[Sequence[str]] = None
) -> Dict[str, Dict[str, Any]]:
    """Letzte Antwort je Punkt, optional nur fuer bestimmte IDs."""
    pfad = ordner / "entscheidungen_antworten.jsonl"
    aus: Dict[str, Dict[str, Any]] = {}
    if not pfad.exists():
        return aus
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        try:
            a = json.loads(zeile)
        except json.JSONDecodeError:
            continue
        if isinstance(a, dict) and a.get("id") and (ids is None or a["id"] in ids):
            aus[a["id"]] = a
    return aus


def knopf_html(url: str, text: str = "Entscheiden", farbe: str = "#e8540a") -> str:
    """Mail-Knopf mit Inline-Styles (Gmail wirft <style> weg, E453)."""
    return (
        f'<a href="{url}" style="display:inline-block;background:{farbe};color:#fff;'
        f"text-decoration:none;font:600 14px -apple-system,Segoe UI,sans-serif;"
        f'padding:9px 18px;border-radius:999px;margin:8px 0 0">{text}</a>'
    )


def wahl_zu_status(wahl: str) -> Tuple[Optional[str], str]:
    """Knopf -> Status in der Freigabe-Schlange (None = nichts tun)."""
    if wahl == "ja":
        return "freigegeben", "per Knopf im Wochenbericht freigegeben"
    if wahl == "nein":
        return "abgelehnt", "per Knopf im Wochenbericht abgelehnt"
    return None, ""


def offene_ids(ids: List[str], ordner: Path = ORDNER) -> List[str]:
    beantwortet = antworten(ordner=ordner, ids=ids)
    return [i for i in ids if i not in beantwortet]
