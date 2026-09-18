"""
Welche Bilddatei laedt ein Handy wirklich? (srcset / sizes / <picture>)

Fehlalarm 18.09.2026 (beratung-beispiel.de): ``image_oversized`` hat die
Datei aus ``src`` gemessen — bei Next.js ist das die groesste Variante
(w=3840, 912 KB). Der Browser laedt wegen ``sizes`` und ``srcset`` aber eine
Variante um 1080 px Breite (97 KB). Dasselbe bei camping-beispiel fuer das
Seitengewicht: gemeldet 4,3 MB, am 3x-Handy gemessen 1,8 MB.

Feste Referenz: ein typisches Handy (Pixel/Galaxy-Klasse) mit 412 CSS-Pixeln
Breite und Pixeldichte 2,625 — wie Lighthouse "mobile". Ausgewertet werden
``<picture><source media type srcset sizes>`` (erste passende Quelle gewinnt,
wie im Browser; WebP/AVIF-Quellen stehen dort ueblicherweise zuerst), dann
``srcset``/``sizes`` am ``<img>``. Was nicht sicher auswertbar ist (calc(),
unbekannte Media-Bedingungen), faellt auf 100vw zurueck.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin

HANDY_BREITE = 412  # CSS-Pixel
HANDY_DPR = 2.625
UNTERSTUETZTE_TYPEN = {
    "", "image/avif", "image/webp", "image/jpeg", "image/png", "image/gif",
    "image/svg+xml", "image/apng",
}  # fmt: skip

_BEDINGUNG = re.compile(r"\(\s*(min|max)-width\s*:\s*([\d.]+)\s*(px|em|rem)\s*\)", re.I)
_LAENGE = re.compile(r"^([\d.]+)\s*(px|vw|em|rem)$", re.I)


def srcset_kandidaten(srcset: str) -> List[Tuple[str, str, float]]:
    """``"a.jpg 640w, b.jpg 1080w"`` -> [(url, 'w', 640.0), ...]; ohne Angabe 1x.

    Einfacher Parser nach HTML-Spezifikation: Die Adresse endet am ersten
    Leerzeichen; ein Komma direkt am Ende der Adresse trennt Kandidaten.
    Kommas IN Adressen (Cloudinary "w_300,h_200") bleiben so erhalten.
    """
    kandidaten: List[Tuple[str, str, float]] = []
    rest = re.sub(r"\s+", " ", srcset or "")
    while rest:
        rest = rest.lstrip(" \t\n\r,")
        if not rest:
            break
        url, _, rest = rest.partition(" ") if " " in rest else (rest, "", "")
        beschreibung = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            beschreibung, _, rest = rest.partition(",")
        treffer = re.match(r"\s*([\d.]+)([wx])", beschreibung or "")
        art, wert = (
            (treffer.group(2), float(treffer.group(1))) if treffer else ("x", 1.0)
        )
        if url:
            kandidaten.append((url, art, wert))
    return kandidaten


def _px(wert: float, einheit: str) -> float:
    return wert * 16 if einheit.lower() in ("em", "rem") else wert


def _bedingung_erfuellt(bedingung: str, breite: int) -> Optional[bool]:
    teile = _BEDINGUNG.findall(bedingung)
    rest = _BEDINGUNG.sub("", bedingung).replace("and", "").strip()
    if not teile or rest:
        return None  # nicht auswertbar
    for art, zahl, einheit in teile:
        grenze = _px(float(zahl), einheit)
        if (art == "max" and breite > grenze) or (art == "min" and breite < grenze):
            return False
    return True


def slot_breite(sizes: str, breite: int = HANDY_BREITE) -> float:
    """Angezeigte Breite in CSS-Pixeln laut ``sizes`` (Standard 100vw)."""
    for eintrag in (sizes or "").split(","):
        eintrag = eintrag.strip()
        if not eintrag:
            continue
        if eintrag.startswith("("):
            ende = eintrag.rfind(")")
            erfuellt = _bedingung_erfuellt(eintrag[: ende + 1], breite)
            if not erfuellt:
                continue
            eintrag = eintrag[ende + 1 :].strip()
        treffer = _LAENGE.match(eintrag)
        if not treffer:
            return float(breite)  # auto, calc() ...: ganze Breite annehmen
        wert, einheit = float(treffer.group(1)), treffer.group(2).lower()
        return wert * breite / 100 if einheit == "vw" else _px(wert, einheit)
    return float(breite)


def waehle_kandidat(srcset: str, sizes: str = "") -> Optional[str]:
    """Die Adresse, die ein Browser am Referenz-Handy aus ``srcset`` nimmt."""
    kandidaten = srcset_kandidaten(srcset)
    if not kandidaten:
        return None
    if any(art == "w" for _, art, _ in kandidaten):
        noetig = slot_breite(sizes) * HANDY_DPR
        breiten = sorted((w, u) for u, art, w in kandidaten if art == "w")
    else:
        noetig = HANDY_DPR
        breiten = sorted((x, u) for u, art, x in kandidaten)
    passend = [u for w, u in breiten if w >= noetig]
    return passend[0] if passend else breiten[-1][1]


def ladeadresse(img, base_url: str = "") -> Optional[str]:
    """Adresse der Datei, die das Referenz-Handy fuer dieses ``<img>`` laedt.

    ``None``, wenn es nichts zu waehlen gibt (dann gilt ``src``).
    """
    picture = img.find_parent("picture")
    for source in picture.find_all("source") if picture is not None else []:
        typ = (source.get("type") or "").strip().lower()
        if typ not in UNTERSTUETZTE_TYPEN:
            continue
        media = (source.get("media") or "").strip()
        if media and not _bedingung_erfuellt(media, HANDY_BREITE):
            continue
        wahl = waehle_kandidat(source.get("srcset") or "", source.get("sizes") or "")
        if wahl:
            return urljoin(base_url, wahl) if base_url else wahl
    wahl = waehle_kandidat(img.get("srcset") or "", img.get("sizes") or "")
    if wahl:
        return urljoin(base_url, wahl) if base_url else wahl
    return None
