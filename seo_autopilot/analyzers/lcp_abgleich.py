"""
LCP-Bildbefunde gegen die echte Messung abgleichen.

Der Bildpruefer arbeitet auf dem Quelltext und RAET, welches Bild das
LCP-Element ist. Fehlalarm 18.09.2026 (natur-beispiel.at/einblicke):
``image_lcp_lazy_loaded`` (high) — das Bild liegt aber unter dem ersten
Bildschirm, das LCP-Element ist Text.

Regeln (Messung schlaegt Vermutung, im Zweifel Schwere senken):

1. Gemessener LCP im gruenen Bereich            -> Befund verworfen (seit 2026-08-18).
2. Die Messung nennt das LCP-Element (PageSpeed oder, ohne PageSpeed,
   Chrome selbst per Playwright — renderer.lcp_element_messen):
   a) kein <img> (Text, Hintergrundbild ...)    -> verworfen.
   b) ein ANDERES Bild                          -> verworfen.
   c) genau dieses Bild                         -> bleibt; "lazy" wird high (bestaetigt).
3. Keine Messung fuer die Seite                 -> "lazy" nur medium (Vermutung).
"""

from __future__ import annotations

import html as html_lib
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

LCP_ZIELWERT_MS = 2500
LCP_BILDBEFUNDE = {"image_lcp_lazy_loaded", "image_lcp_no_priority"}
LAZY = "image_lcp_lazy_loaded"


def _norm(url: str) -> str:
    return (url or "").split("#")[0].rstrip("/")


def _messungen(psi_results: Optional[List[Any]]) -> Dict[str, Any]:
    return {
        _norm(getattr(r, "url", "")): r
        for r in (psi_results or [])
        if not getattr(r, "error", None)
    }


def _ist_bild(snippet: str) -> bool:
    kopf = (snippet or "").lstrip().lower()
    return kopf.startswith(("<img", "<image", "<video"))


def bild_passt(bild_src: str, snippet: str) -> bool:
    """Steht die gemeldete Bilddatei im Snippet des LCP-Elements?"""
    if not bild_src:
        return True  # nichts zum Vergleichen -> nicht widerlegbar
    text = unquote(html_lib.unescape(snippet or ""))
    pfad = unquote(urlparse(bild_src).path)
    name = pfad.rsplit("/", 1)[-1]
    parameter = unquote(urlparse(bild_src).query)
    if (
        "url=" in parameter
    ):  # Bilddienst (/_next/image?url=/bild.png): nur die Datei zaehlt
        kandidaten = [parameter.split("url=", 1)[1].split("&", 1)[0]]
    else:
        kandidaten = [k for k in (pfad, name) if k and k != "/"]
    return any(k and k in text for k in kandidaten)


def _urteil(issue: Dict[str, Any], messung: Any) -> Optional[str]:
    """None = behalten, sonst Begruendung fuer das Verwerfen."""
    lcp = getattr(messung, "lcp_ms", None)
    if lcp is not None and lcp <= LCP_ZIELWERT_MS:
        return f"gemessener LCP {lcp:.0f} ms liegt im gruenen Bereich"
    snippet = getattr(messung, "lcp_element_snippet", None)
    if not snippet:
        return None
    if not _ist_bild(snippet):
        return f"LCP-Element ist kein Bild ({snippet[:60]})"
    if not bild_passt(issue.get("bild_src") or "", snippet):
        return "LCP-Element ist ein anderes Bild"
    return None


def lcp_befunde_abgleichen(
    bild_issues: List[Dict[str, Any]], psi_results: Optional[List[Any]]
) -> List[Dict[str, Any]]:
    messungen = _messungen(psi_results)
    behalten = []
    for issue in bild_issues:
        if issue.get("type") not in LCP_BILDBEFUNDE:
            behalten.append(issue)
            continue
        messung = messungen.get(_norm(issue.get("affected_url") or ""))
        if messung is None:
            behalten.append(_als_vermutung(issue))
            continue
        grund = _urteil(issue, messung)
        if grund:
            logger.info(
                f"[analyzer] LCP-Bildbefund verworfen ({grund}): {issue.get('affected_url')}"
            )
            continue
        behalten.append(_bestaetigt(issue, messung))
    return behalten


def _als_vermutung(issue: Dict[str, Any]) -> Dict[str, Any]:
    if issue.get("type") != LAZY or issue.get("severity") != "high":
        return issue
    neu = dict(issue)
    neu["severity"] = "medium"
    neu["description"] = (
        issue.get("description", "")
        + " (Vermutung aus dem Quelltext — keine Messung fuer diese Seite.)"
    )
    return neu


def _bestaetigt(issue: Dict[str, Any], messung: Any) -> Dict[str, Any]:
    if issue.get("type") != LAZY or not getattr(messung, "lcp_element_snippet", None):
        return _als_vermutung(issue)
    neu = dict(issue)
    neu["severity"] = "high"
    neu["description"] = (
        issue.get("description", "")
        + " Messung bestaetigt: genau dieses Bild ist das LCP-Element."
    )
    return neu
