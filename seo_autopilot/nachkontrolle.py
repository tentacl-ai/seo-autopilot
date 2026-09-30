"""
Nachkontrolle nach einer automatischen Reparatur — hat der Fix etwas kaputt gemacht?

Der Adapter schreibt HTML-Dateien per Textersetzung. Ein falscher Treffer kann
dabei mehr treffen als gewollt: Titel weg, Hauptueberschrift weg, JSON-LD weg,
ein noindex taucht auf. Diese Pruefung vergleicht die Datei VOR und NACH dem
Fix, bevor irgendetwas committet wird. Findet sie einen Verlust, wird die
Aenderung zurueckgerollt und der Befund bleibt offen (``nicht_behebbar``).

Regeln nach claude-seo `skills/seo-drift/references/comparison-rules.md`
(v2.4.1, MIT, Copyright (c) 2026 agricidaniel), Stufe CRITICAL plus
Meta-Description, OG-Tags und hreflang. Absichtliche Aenderungen (Titeltext,
H1-Text, Canonical beim Canonical-Fix) sind erlaubt — geprueft werden nur
Verluste und neu auftauchendes noindex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from bs4 import BeautifulSoup

# Fix-Typen, die den Canonical absichtlich setzen oder aendern
CANONICAL_FIXES = {"missing_canonical", "canonical_missing", "canonical_wrong"}

# Mehr als so viel sichtbarer Text verloren -> verdaechtig. Fixes fuegen fast
# nur hinzu; einzig `erster_absatz` ersetzt Text, daher grosszuegig.
MAX_TEXTVERLUST = 0.25


@dataclass
class Stand:
    title: str = ""
    h1: int = 0
    meta_description: bool = False
    canonical: Optional[str] = None
    noindex: bool = False
    jsonld: int = 0
    og: int = 0
    hreflang: int = 0
    textlaenge: int = 0


@dataclass
class Ergebnis:
    verluste: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.verluste


def stand_lesen(html: str) -> Stand:
    soup = BeautifulSoup(html or "", "html.parser")
    st = Stand()
    if soup.title and soup.title.string:
        st.title = soup.title.string.strip()
    st.h1 = sum(1 for h in soup.find_all("h1") if h.get_text(strip=True))
    st.meta_description = any(
        (m.get("content") or "").strip()
        for m in soup.find_all(
            "meta", attrs={"name": lambda v: v and v.lower() == "description"}
        )
    )
    for link in soup.find_all("link", href=True):
        rel = [r.lower() for r in (link.get("rel") or [])]
        if "canonical" in rel and st.canonical is None:
            st.canonical = link["href"].strip()
        if "alternate" in rel and link.get("hreflang"):
            st.hreflang += 1
    st.noindex = any(
        "noindex" in (m.get("content") or "").lower()
        for m in soup.find_all(
            "meta", attrs={"name": lambda v: v and v.lower() in ("robots", "googlebot")}
        )
    )
    st.jsonld = len(soup.find_all("script", attrs={"type": "application/ld+json"}))
    st.og = len(
        soup.find_all("meta", attrs={"property": lambda v: v and v.startswith("og:")})
    )
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    st.textlaenge = len(" ".join(soup.get_text(" ").split()))
    return st


def pruefe(vorher_html: str, nachher_html: str, fix_typ: str = "") -> Ergebnis:
    """Vergleicht zwei Fassungen einer Seite. ``ok`` = nichts verloren gegangen."""
    v, n = stand_lesen(vorher_html), stand_lesen(nachher_html)
    e = Ergebnis()
    if v.title and not n.title:
        e.verluste.append("Seitentitel würde verschwinden")
    if v.h1 and not n.h1:
        e.verluste.append("Hauptüberschrift (H1) würde verschwinden")
    if v.meta_description and not n.meta_description:
        e.verluste.append("Kurzbeschreibung (meta description) würde verschwinden")
    if v.canonical and not n.canonical:
        e.verluste.append("Canonical würde verschwinden")
    elif v.canonical and n.canonical != v.canonical and fix_typ not in CANONICAL_FIXES:
        e.verluste.append(
            f"Canonical würde sich ändern ({v.canonical} → {n.canonical})"
        )
    if n.noindex and not v.noindex:
        e.verluste.append("noindex käme neu dazu – Seite würde aus Google fallen")
    if n.jsonld < v.jsonld:
        e.verluste.append(
            f"Strukturierte Daten: {v.jsonld - n.jsonld} JSON-LD-Block/Blöcke würden verschwinden"
        )
    if n.og < v.og:
        e.verluste.append("Social-Media-Vorschau (og:-Angaben) würde schrumpfen")
    if n.hreflang < v.hreflang:
        e.verluste.append("Sprachverweise (hreflang) würden verschwinden")
    if v.textlaenge and (v.textlaenge - n.textlaenge) / v.textlaenge > MAX_TEXTVERLUST:
        e.verluste.append(
            f"Sichtbarer Text würde um {1 - n.textlaenge / v.textlaenge:.0%} schrumpfen"
        )
    return e
