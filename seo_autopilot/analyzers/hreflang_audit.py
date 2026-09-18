"""
hreflang-Pruefung: Ziel erreichbar, Rueckverweis vorhanden, Sprache passt.

Anlass (Rundumschlag 18.09.2026): coaching-beispiel.de weist /en/help-center als
``hreflang="en"`` aus — Inhalt und ``<html lang="de">`` sind aber deutsch.
Google zeigt englischsprachigen Suchenden damit eine deutsche Seite (bzw.
verwirft den ganzen hreflang-Verbund). Bisher prueften wir hreflang nur im
Zusammenspiel mit dem Canonical.

Sprach-Heuristik (bewusst einfach): ``<html lang>`` plus Haeufigkeit
deutscher und englischer Funktionswoerter im sichtbaren Text. Erkannt werden
nur Deutsch und Englisch, und nur bei eindeutigem Verhaeltnis (2:1) und
genug Treffern — sonst gilt die Sprache als unbekannt und es gibt keinen
Befund aus dem Text.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..sources.crawler import USER_AGENT

logger = logging.getLogger(__name__)

MAX_ZIELE = 30
PARALLEL = 5
TIMEOUT = 10.0
MIN_TREFFER = 15

# Woerter, die in beiden Sprachen vorkommen (in, an, was, will, also, so),
# sind bewusst NICHT enthalten.
DEUTSCHE_WOERTER = set(
    "der die das und ist nicht mit für fuer auf sich ein eine einen einem dem den "
    "des zu von im bei auch oder wie wir sie ich du ihr uns sind wird werden kann "
    "können koennen noch nur schon über ueber unter aus nach vor zum zur als wenn "
    "dass weil aber mehr sehr hier jetzt ihre ihren dein deine unsere unser euch".split()
)
ENGLISCHE_WOERTER = set(
    "the and is are not with for on this that it be to of from you your we our "
    "can have has were by at as or but if more all which their they what when "
    "how about into there these those would should could been being".split()
)
_WORT = re.compile(r"[a-zäöüß]+")


def sichtbarer_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.extract()
    return soup.get_text(" ", strip=True)


def erkenne_sprache(text: str) -> Optional[str]:
    """'de', 'en' oder None (zu wenig Text oder nicht eindeutig)."""
    woerter = _WORT.findall((text or "").lower())
    de = sum(1 for w in woerter if w in DEUTSCHE_WOERTER)
    en = sum(1 for w in woerter if w in ENGLISCHE_WOERTER)
    if de + en < MIN_TREFFER:
        return None
    if de >= 2 * en:
        return "de"
    if en >= 2 * de:
        return "en"
    return None


def html_lang(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    tag = soup.find("html")
    return ((tag.get("lang") if tag else "") or "").strip().lower()


def hreflang_liste(html: str, basis: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    liste = []
    for link in soup.find_all("link", hreflang=True):
        rel = [r.lower() for r in (link.get("rel") or [])]
        if "alternate" in rel and link.get("href"):
            liste.append(
                {"hreflang": link["hreflang"], "href": urljoin(basis, link["href"])}
            )
    return liste


def _norm(url: str) -> str:
    return (url or "").split("#")[0].rstrip("/").lower()


def _haupt(code: str) -> str:
    return (code or "").strip().lower().split("-")[0].split("_")[0]


# --------------------------------------------------------------------------
# Daten zusammentragen
# --------------------------------------------------------------------------


def _seiten_index(seiten: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index = {}
    for s in seiten:
        if s.get("status_code", 200) != 200 or not s.get("html"):
            continue
        eintrag = {"status": 200, "html": s["html"], "url": s.get("url")}
        for u in (s.get("url"), s.get("final_url")):
            if u:
                index[_norm(u)] = eintrag
    return index


def _verweise(seiten: List[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    """(Quelle, hreflang-Code, Ziel) fuer alle gecrawlten Seiten."""
    paare = []
    for s in seiten:
        basis = s.get("final_url") or s.get("url") or ""
        for h in s.get("hreflang") or []:
            if h.get("href"):
                paare.append(
                    (s.get("url"), h.get("hreflang", ""), urljoin(basis, h["href"]))
                )
    return paare


async def _hole_ziele(urls: List[str], client: httpx.AsyncClient) -> Dict[str, Dict]:
    sem = asyncio.Semaphore(PARALLEL)

    async def _eins(u: str):
        async with sem:
            try:
                r = await client.get(u, timeout=TIMEOUT)
                html = r.text if "html" in r.headers.get("content-type", "") else ""
                return _norm(u), {"status": r.status_code, "html": html, "url": u}
            except Exception as exc:
                logger.debug(f"[hreflang] Abruf {u} fehlgeschlagen: {exc}")
                return _norm(u), None

    return {k: v for k, v in await asyncio.gather(*[_eins(u) for u in urls]) if v}


# --------------------------------------------------------------------------
# Pruefungen
# --------------------------------------------------------------------------


def _befund(typ, schwere, url, titel, beschreibung, abhilfe) -> Dict[str, Any]:
    return {
        "category": "hreflang",
        "type": typ,
        "severity": schwere,
        "title": titel,
        "affected_url": url,
        "description": beschreibung,
        "fix_suggestion": abhilfe,
        "estimated_impact": "",
    }


def pruefe_sprache(ziel: str, code: str, html: str) -> Optional[Dict[str, Any]]:
    """Passt die Sprache der Zielseite zur hreflang-Angabe?"""
    erwartet = _haupt(code)
    if not erwartet or erwartet == "x":
        return None
    text_sprache = erkenne_sprache(sichtbarer_text(html))
    attr = _haupt(html_lang(html))
    namen = {"de": "deutsch", "en": "englisch"}
    if text_sprache and erwartet in namen and text_sprache != erwartet:
        grund = f"Der Text ist {namen[text_sprache]}"
        if attr:
            grund += f', <html lang="{html_lang(html)}">'
        schwere = "medium"
    elif attr and attr != erwartet and text_sprache != erwartet:
        grund = f'<html lang="{html_lang(html)}"> (Text nicht eindeutig)'
        schwere = "low"
    elif attr and attr != erwartet:
        grund = f'Nur das Attribut stimmt nicht: <html lang="{html_lang(html)}">'
        schwere = "low"
    else:
        return None
    return _befund(
        "hreflang_language_mismatch",
        schwere,
        ziel,
        f'hreflang="{code}" zeigt auf eine Seite in anderer Sprache',
        f'{ziel} ist als hreflang="{code}" ausgewiesen. {grund}.',
        "Seite wirklich uebersetzen (und lang-Attribut anpassen) oder den "
        "hreflang-Eintrag entfernen, bis die Uebersetzung existiert.",
    )


def _rueckverweis_fehlt(quelle: str, ziel_html: str, ziel: str) -> bool:
    eintraege = hreflang_liste(ziel_html, ziel)
    return _norm(quelle) not in {_norm(e["href"]) for e in eintraege}


async def pruefe_hreflang(
    seiten: List[Dict[str, Any]],
    client: Optional[httpx.AsyncClient] = None,
    max_ziele: int = MAX_ZIELE,
    fingerabdruck: Any = None,
) -> List[Dict[str, Any]]:
    """Alle hreflang-Befunde der gecrawlten Seiten.

    ``fingerabdruck`` (link_check.Fingerabdruck): Liefert die Website fuer
    unbekannte Adressen die Startseite (Catch-all), gilt ein Ziel, das genau
    so aussieht, als nicht existent — sonst wuerde die Startseite als
    "deutsche Seite unter hreflang=en" gemeldet (coaching-beispiel /en/imprint).
    """
    paare = _verweise(seiten)
    if not paare:
        return []
    index = _seiten_index(seiten)
    offen = list(dict.fromkeys(z for _, _, z in paare if _norm(z) not in index))
    eigener = client is None
    if eigener:
        client = httpx.AsyncClient(
            follow_redirects=True, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
    try:
        geholt = await _hole_ziele(offen[:max_ziele], client)
        index.update(_catchall_als_404(geholt, fingerabdruck))
    finally:
        if eigener:
            await client.aclose()
    return _auswerten(paare, index)


def _catchall_als_404(geholt: Dict[str, Dict], fingerabdruck: Any) -> Dict[str, Dict]:
    if fingerabdruck is None:
        return geholt
    from .link_check import _titel_und_canonical

    for schluessel, daten in geholt.items():
        if daten.get("status") != 200 or not daten.get("html"):
            continue
        titel, canonical = _titel_und_canonical(daten["html"])
        laenge = len(daten["html"].encode("utf-8", "ignore"))
        if fingerabdruck.passt(titel, canonical, laenge) and _norm(
            canonical or ""
        ) != _norm(daten["url"]):
            daten["status"] = 404
            daten["catchall"] = True
    return geholt


def _auswerten(paare, index) -> List[Dict[str, Any]]:
    befunde: List[Dict[str, Any]] = []
    kaputt: Dict[str, List[str]] = {}
    ohne_rueckweg: Dict[str, List[Tuple[str, str]]] = {}
    sprache_geprueft = set()
    for quelle, code, ziel in paare:
        daten = index.get(_norm(ziel))
        if daten is None:  # nicht abrufbar oder ueber dem Deckel: im Zweifel still
            continue
        if daten["status"] != 200:
            kaputt.setdefault(ziel, []).append(quelle)
            continue
        if not daten["html"]:
            continue
        schluessel = (_norm(ziel), _haupt(code))
        if schluessel not in sprache_geprueft:
            sprache_geprueft.add(schluessel)
            b = pruefe_sprache(ziel, code, daten["html"])
            if b:
                befunde.append(b)
        if _norm(ziel) != _norm(quelle) and _rueckverweis_fehlt(
            quelle, daten["html"], ziel
        ):
            ohne_rueckweg.setdefault(ziel, []).append((quelle, code))
    for ziel, quellen in ohne_rueckweg.items():
        befunde.append(_rueckverweis_befund(quellen, ziel))
    for ziel, quellen in kaputt.items():
        daten = index[_norm(ziel)]
        befunde.append(
            _kaputt_befund(ziel, quellen, daten["status"], daten.get("catchall", False))
        )
    if not any(_haupt(c) == "x" for _, c, _ in paare):
        befunde.append(_x_default_befund(paare[0][0]))
    return befunde


def _rueckverweis_befund(quellen, ziel):
    quelle, code = quellen[0]
    mehr = f" (und {len(quellen) - 1} weitere Seite(n))" if len(quellen) > 1 else ""
    return _befund(
        "hreflang_missing_return_link",
        "medium",
        quelle,
        f"hreflang ohne Rueckverweis: {ziel}",
        f'{quelle}{mehr} verweist per hreflang="{code}" auf {ziel}, die Zielseite '
        "verweist aber nicht zurueck. Google ignoriert einseitige hreflang-Paare.",
        "Auf der Zielseite ebenfalls alle Sprachfassungen (inkl. dieser) auffuehren.",
    )


def _kaputt_befund(ziel, quellen, status, catchall=False):
    grund = (
        "existiert nicht (der Server zeigt dort nur die Startseite, Catch-all)"
        if catchall
        else f"antwortet mit HTTP {status}"
    )
    return _befund(
        "hreflang_broken_target",
        "medium",
        quellen[0],
        (
            "hreflang-Ziel existiert nicht"
            if catchall
            else f"hreflang-Ziel nicht erreichbar (HTTP {status})"
        ),
        f"{ziel} {grund}, ist aber als Sprachfassung "
        f"eingetragen (auf {len(set(quellen))} Seite(n)).",
        "hreflang-Eintrag korrigieren oder die Zielseite wiederherstellen.",
    )


def _x_default_befund(url):
    return _befund(
        "hreflang_missing_x_default",
        "low",
        url,
        "hreflang ohne x-default",
        "Die Website nutzt hreflang, nennt aber keine Rueckfall-Fassung "
        '(hreflang="x-default") fuer Besucher anderer Sprachen.',
        '<link rel="alternate" hreflang="x-default" href="..."> ergaenzen.',
    )
