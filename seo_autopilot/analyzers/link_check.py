"""
Kaputte interne Links und "jede falsche Adresse zeigt die Startseite".

Anlass (Rundumschlag 18.09.2026) — beides wurde nicht gemeldet:

* tentacl.ai/help/ verlinkte ``href="home_url"`` (unersetzter Platzhalter,
  landet auf /help/home_url = 404); /loesungen/aussendienst/ verlinkte
  /projekte/artofwork/ (404). Die bisherige Pruefung (link_graph) kannte nur
  den Status der GECRAWLTEN Seiten — Ziele ausserhalb des Crawl-Limits
  blieben ungeprueft.
* coaching-beispiel.de liefert fuer JEDE nicht existente Adresse HTTP 200 mit der
  Startseite (Catch-all). Google wertet das als Soft-404; ein Link auf
  /support sieht dann fuer jede Statuspruefung gesund aus.

Grundregel: **im Zweifel nicht melden.** Netzwerkfehler, Zeitueberschreitung,
401/403/429 sind kein kaputter Link. Ein 5xx zaehlt nur, wenn HEAD UND GET
ihn liefern. Der Catch-all-Vergleich wird abgeschaltet, sobald eine echte,
gecrawlte Unterseite genauso aussieht wie die Zufallsadresse (reine
JavaScript-Huelle ohne Vorrendern — dann taugt der Fingerabdruck nichts).
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..sources.crawler import USER_AGENT, host_ohne_www
from .seiten_checks import UTILITY_SEGMENTE

APP_SEGMENTE = UTILITY_SEGMENTE | {"app", "portal", "members", "mitglieder"}

logger = logging.getLogger(__name__)

MAX_ZIELE = 150
PARALLEL = 5
TIMEOUT = 10.0
# Sitemap-Seiten jenseits des Crawl-Limits, die nur als Linkquelle gelesen werden
MAX_ZUSATZQUELLEN = 20

# Schemata, die kein Seitenlink sind
_KEIN_SEITENLINK = ("mailto:", "tel:", "javascript:", "data:", "sms:", "fax:")

# Kauf-/Affiliate-Links nie aufrufen (01.10.2026): Jeder Aufruf zählt beim Shop als Klick,
# verfälscht die Klickstatistik und kann bei Amazon & Co. als Bot-Klick auffallen.
# Erkannt an rel="sponsored" (Google-Standard für bezahlte Links) oder einem typischen Weiterleitungspfad.
_KAUFLINK_PFAD = re.compile(r"^/(?:api/)?(?:go|goto|out|recommends|affiliate)/", re.I)


def ist_kauflink(a, voll: str) -> bool:
    rel = a.get("rel") or []
    rel = rel.split() if isinstance(rel, str) else rel
    return "sponsored" in [r.lower() for r in rel] or bool(
        _KAUFLINK_PFAD.match(urlparse(voll).path)
    )


# Offensichtliche, nicht ersetzte Vorlagen-Platzhalter
_PLATZHALTER = re.compile(
    r"(\{\{.*?\}\}|%7b%7b.*?%7d%7d|\$\{.*?\}|<%.*?%>|\[object\s*object\]"
    r"|(?:^|/)(?:undefined|null|nan)(?:/|$|\?)"
    r"|(?:^|/)[a-z]+_(?:url|link|href|path)(?:/|$|\?))",
    re.IGNORECASE,
)

_SOFT_404_TEXT = re.compile(
    r"\b(404|nicht gefunden|not found|existiert nicht|does not exist)\b", re.I
)


def platzhalter_in(href: str) -> Optional[str]:
    """Liefert den Platzhalter-Text, wenn ``href`` offensichtlich eine Vorlagenluecke ist."""
    treffer = _PLATZHALTER.search(href or "")
    return treffer.group(0).strip("/?") if treffer else None


def _norm(url: str) -> str:
    return (url or "").split("#")[0].rstrip("/").lower()


@dataclass
class LinkZiel:
    url: str
    quellen: List[str] = field(default_factory=list)
    platzhalter: Optional[str] = None
    roh_href: str = ""


@dataclass
class Fingerabdruck:
    """So sieht die Antwort auf eine garantiert nicht existente Adresse aus."""

    titel: str
    canonical: str
    laenge: int

    def passt(self, titel: str, canonical: str, laenge: int) -> bool:
        if (titel or "") != self.titel:
            return False
        if canonical and self.canonical:
            return _norm(canonical) == _norm(self.canonical)
        return self.laenge > 0 and abs(laenge - self.laenge) <= self.laenge * 0.01


def _titel_und_canonical(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    titel = soup.title.get_text(strip=True) if soup.title else ""
    canonical = ""
    for link in soup.find_all("link", href=True):
        rel = [r.lower() for r in (link.get("rel") or [])]
        if "canonical" in rel:
            canonical = link["href"].strip()
            break
    return titel, canonical


# --------------------------------------------------------------------------
# Links sammeln
# --------------------------------------------------------------------------


def sammle_interne_links(seiten: List[Dict[str, Any]], domain: str) -> List[LinkZiel]:
    """Alle internen Linkziele aller Seiten, je Ziel mit den Quellseiten."""
    eigen = host_ohne_www(domain)
    ziele: Dict[str, LinkZiel] = {}
    for seite in seiten:
        basis = seite.get("final_url") or seite.get("url") or ""
        soup = BeautifulSoup(seite.get("html") or "", "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if (
                not href
                or href.startswith("#")
                or href.lower().startswith(_KEIN_SEITENLINK)
            ):
                continue
            voll = urljoin(basis, href).split("#")[0]
            teile = urlparse(voll)
            if teile.scheme not in ("http", "https") or host_ohne_www(voll) != eigen:
                continue
            if ist_kauflink(a, voll):
                continue
            ziel = ziele.setdefault(_norm(voll), LinkZiel(url=voll, roh_href=href))
            if seite.get("url") not in ziel.quellen:
                ziel.quellen.append(seite.get("url"))
            ziel.platzhalter = ziel.platzhalter or platzhalter_in(href)
    return list(ziele.values())


def _auswahl(ziele: List[LinkZiel], bekannt: Dict[str, int], max_ziele: int):
    """Unbekannte Ziele, Platzhalter zuerst, dann die meistverlinkten."""
    offen = [z for z in ziele if _norm(z.url) not in bekannt]
    offen.sort(key=lambda z: (z.platzhalter is None, -len(z.quellen)))
    return offen[:max_ziele], max(0, len(offen) - max_ziele)


# --------------------------------------------------------------------------
# Abrufen
# --------------------------------------------------------------------------


async def _abruf(client: httpx.AsyncClient, methode: str, url: str):
    try:
        return await client.request(methode, url, timeout=TIMEOUT)
    except Exception as exc:
        logger.debug(f"[links] {methode} {url} fehlgeschlagen: {exc}")
        return None


async def _status_ziel(
    client: httpx.AsyncClient, url: str, fingerabdruck: Optional[Fingerabdruck]
) -> Tuple[str, int]:
    """('kaputt'|'catchall'|'ok'|'unklar', HTTP-Status)."""
    kopf = await _abruf(client, "HEAD", url)
    if kopf is not None and kopf.status_code < 400 and fingerabdruck is None:
        return "ok", kopf.status_code
    # GET bestaetigt jeden Verdacht (manche Server koennen kein HEAD)
    antwort = await _abruf(client, "GET", url)
    if antwort is None:
        return "unklar", 0
    s = antwort.status_code
    if s in (404, 410):
        return "kaputt", s
    if s >= 500:
        return (
            "kaputt" if kopf is not None and kopf.status_code >= 500 else "unklar"
        ), s
    if s == 200 and fingerabdruck is not None:
        titel, canonical = _titel_und_canonical(antwort.text)
        if fingerabdruck.passt(titel, canonical, len(antwort.content)):
            # Nur die Pfade vergleichen: "/?ref=x" mit Canonical "/" ist gesund.
            if _norm((canonical or url).split("?")[0]) != _norm(url.split("?")[0]):
                return "catchall", s
    return ("ok" if s < 400 else "unklar"), s


async def pruefe_ziele(
    ziele: List[LinkZiel],
    client: httpx.AsyncClient,
    fingerabdruck: Optional[Fingerabdruck] = None,
    parallel: int = PARALLEL,
) -> Dict[str, Tuple[str, int]]:
    sem = asyncio.Semaphore(parallel)

    async def _eins(z: LinkZiel):
        async with sem:
            return _norm(z.url), await _status_ziel(client, z.url, fingerabdruck)

    return dict(await asyncio.gather(*[_eins(z) for z in ziele]))


# --------------------------------------------------------------------------
# Befunde
# --------------------------------------------------------------------------


def _pfad(url: str) -> str:
    teile = urlparse(url)
    return teile.path + (f"?{teile.query}" if teile.query else "") or "/"


def _link_befund(ziel: LinkZiel, ergebnis: str, status: int) -> Dict[str, Any]:
    quellen = ziel.quellen[:5]
    mehr = len(ziel.quellen) - len(quellen)
    quelltext = ", ".join(_pfad(q) for q in quellen) + (
        f" (+{mehr} weitere)" if mehr else ""
    )
    if ergebnis == "catchall":
        grund = (
            "Die Adresse existiert nicht; der Server zeigt stattdessen die "
            "Startseite (HTTP 200, Catch-all)"
        )
    else:
        grund = f"Das Ziel antwortet mit HTTP {status}"
    if ziel.platzhalter:
        grund += (
            f". Ursache: nicht ersetzter Platzhalter '{ziel.platzhalter}' "
            f'im Link (href="{ziel.roh_href}")'
        )
    return {
        "category": "links",
        "type": "broken_internal_link",
        "severity": "high",
        "title": f"Kaputter interner Link: {_pfad(ziel.url)}"
        + (" (Platzhalter)" if ziel.platzhalter else ""),
        "affected_url": ziel.quellen[0] if ziel.quellen else ziel.url,
        "description": f"{grund}. Verlinkt von: {quelltext}.",
        "fix_suggestion": (
            "Platzhalter in der Vorlage durch die echte Adresse ersetzen."
            if ziel.platzhalter
            else "Link auf eine existierende Seite aendern oder entfernen "
            "(ggf. 301-Weiterleitung einrichten)."
        ),
        "estimated_impact": "Besucher und Google laufen ins Leere.",
        "ziel_url": ziel.url,
        "quellseiten": list(ziel.quellen),
    }


def ist_app_route(url: str) -> bool:
    """Pfad einer typischen App-/Kontoseite (/login, /dashboard, /app ...).

    Bei einer Single-Page-App liefert der Server fuer solche Routen dieselbe
    Huelle wie fuer unbekannte Adressen — per HTTP nicht unterscheidbar.
    """
    segmente = {t.lower() for t in urlparse(url).path.split("/") if t}
    return bool(segmente & APP_SEGMENTE)


async def hole_linkquellen(
    urls: List[str], client: httpx.AsyncClient, max_seiten: int = MAX_ZUSATZQUELLEN
) -> List[Dict[str, Any]]:
    """Sitemap-Seiten jenseits des Crawl-Limits NUR fuer die Linksuche abrufen.

    Anlass: tentacl.ai/help/ (mit href="home_url") lag hinter Seite 40 der
    Sitemap und wurde nie gelesen.
    """
    sem = asyncio.Semaphore(PARALLEL)

    async def _eins(url: str):
        async with sem:
            r = await _abruf(client, "GET", url)
            if r is None or r.status_code != 200:
                return None
            if "html" not in r.headers.get("content-type", "").lower():
                return None
            return {"url": url, "final_url": str(r.url), "html": r.text}

    geholt = await asyncio.gather(*[_eins(u) for u in urls[:max_seiten]])
    return [g for g in geholt if g]


def _bekannter_status(
    ziel: LinkZiel, bekannt: Dict[str, int]
) -> Optional[Tuple[str, int]]:
    s = bekannt.get(_norm(ziel.url))
    if s is None:
        return None
    return ("kaputt" if s in (404, 410) or s >= 500 else "ok"), s


async def pruefe_interne_links(
    seiten: List[Dict[str, Any]],
    domain: str,
    bekannter_status: Optional[Dict[str, int]] = None,
    fingerabdruck: Optional[Fingerabdruck] = None,
    client: Optional[httpx.AsyncClient] = None,
    max_ziele: int = MAX_ZIELE,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Prueft alle internen Linkziele. Rueckgabe: (Befunde, Statistik).

    Nebenbei: verlinkte, NICHT gecrawlte Werkzeugseiten (/dashboard, /login
    ...) mit HTTP 200 werden auf noindex geprueft (tentacl.ai
    /seo-autopilot/dashboard stand nicht in der Sitemap, war aber verlinkt).
    """
    bekannt = {_norm(u): s for u, s in (bekannter_status or {}).items() if s}
    ziele = sammle_interne_links(seiten, domain)
    auswahl, ausgelassen = _auswahl(ziele, bekannt, max_ziele)
    eigener = client is None
    if eigener:
        client = httpx.AsyncClient(
            follow_redirects=True, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
    try:
        ergebnisse = await pruefe_ziele(auswahl, client, fingerabdruck)
        werkzeug = [
            z
            for z in auswahl
            if ergebnisse[_norm(z.url)][0] == "ok" and ist_app_route(z.url)
        ]
        werkzeug_befunde = await verlinkte_werkzeugseiten(werkzeug, client)
    finally:
        if eigener:
            await client.aclose()

    befunde, app_routen = _auswerten(ziele, ergebnisse, bekannt)
    statistik = {
        "link_ziele": len(ziele),
        "link_ziele_geprueft": len(auswahl),
        "link_ziele_ausgelassen": ausgelassen,
        "link_ziele_unklar": sum(1 for e in ergebnisse.values() if e[0] == "unklar"),
        "link_ziele_app_routen": app_routen,
    }
    return befunde + werkzeug_befunde, statistik


def _auswerten(ziele, ergebnisse, bekannt) -> Tuple[List[Dict[str, Any]], int]:
    befunde, app_routen = [], 0
    for ziel in ziele:
        ergebnis = ergebnisse.get(_norm(ziel.url)) or _bekannter_status(ziel, bekannt)
        if not ergebnis or ergebnis[0] not in ("kaputt", "catchall"):
            continue
        if (
            ergebnis[0] == "catchall"
            and not ziel.platzhalter
            and ist_app_route(ziel.url)
        ):
            # /login, /dashboard einer SPA: meist echte App-Route -> im Zweifel still
            app_routen += 1
            continue
        befunde.append(_link_befund(ziel, *ergebnis))
    return befunde, app_routen


async def verlinkte_werkzeugseiten(
    ziele: List[LinkZiel], client: httpx.AsyncClient, max_seiten: int = 10
) -> List[Dict[str, Any]]:
    """Verlinkte Werkzeugseiten mit HTTP 200 auf noindex pruefen (low)."""
    from .seiten_checks import pruefe_werkzeugseiten

    seiten = []
    for ziel in ziele[:max_seiten]:
        r = await _abruf(client, "GET", ziel.url)
        if r is None or r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text or "", "html.parser")
        robots = soup.find("meta", attrs={"name": "robots"})
        _, canonical = _titel_und_canonical(r.text)
        seiten.append(
            {
                "url": ziel.url,
                "final_url": str(r.url),
                "robots_meta": (robots.get("content") or "") if robots else "",
                "x_robots_tag": r.headers.get("x-robots-tag", ""),
                "canonical": canonical,
            }
        )
    befunde = pruefe_werkzeugseiten(seiten)
    for b in befunde:
        ziel = next(z for z in ziele if z.url == b["affected_url"])
        b[
            "description"
        ] += f" Nicht in der Sitemap, aber verlinkt von: {', '.join(_pfad(q) for q in ziel.quellen[:3])}."
    return befunde


# --------------------------------------------------------------------------
# Catch-all / Soft-404 der ganzen Website
# --------------------------------------------------------------------------


def zufallsadresse(domain: str) -> str:
    return f"{domain.rstrip('/')}/seo-autopilot-gibt-es-nicht-{secrets.token_hex(6)}"


async def pruefe_catchall(
    domain: str, client: httpx.AsyncClient
) -> Tuple[Optional[Dict[str, Any]], Optional[Fingerabdruck]]:
    """Ruft zwei garantiert nicht existente Adressen ab.

    Ergaenzt den seitenweisen ``soft_404`` (gecrawlte Seite sagt "nicht
    gefunden", liefert aber 200) um die Website-Sicht: Antwortet der Server
    auf ALLES mit 200, ist jede falsche Adresse eine indexierbare Kopie.
    Nur wenn BEIDE Proben 200 mit HTML liefern, gibt es einen Befund.
    """
    proben = [zufallsadresse(domain), zufallsadresse(domain) + "/unterseite"]
    antworten = [await _abruf(client, "GET", u) for u in proben]
    if any(a is None or a.status_code != 200 for a in antworten):
        return None, None
    erste = antworten[0]
    if "html" not in erste.headers.get("content-type", "html").lower():
        return None, None
    titel, canonical = _titel_und_canonical(erste.text)
    fp = Fingerabdruck(titel=titel, canonical=canonical, laenge=len(erste.content))
    auf_start = urlparse(str(erste.url)).path in ("", "/")
    return _catchall_befund(domain, proben[0], erste, titel, canonical, auf_start), fp


def _catchall_befund(
    domain, probe, antwort, titel, canonical, auf_start
) -> Dict[str, Any]:
    text = BeautifulSoup(antwort.text or "", "html.parser").get_text(" ", strip=True)
    fehlerseite = bool(_SOFT_404_TEXT.search(f"{titel} {text[:3000]}"))
    startseite = auf_start or _norm(canonical) == _norm(domain)
    if fehlerseite and not startseite:
        was = "eine Fehlerseite („nicht gefunden“), aber mit HTTP 200 statt 404"
        schwere = "medium"
    elif auf_start:
        was = "eine Weiterleitung auf die Startseite (HTTP 200 am Ende)"
        schwere = "high"
    else:
        was = "die Startseite" if startseite else "normalen Seiteninhalt"
        was += " mit HTTP 200"
        schwere = "high"
    return {
        "category": "redirect",
        "type": "soft_404_catchall",
        "severity": schwere,
        "title": "Jede falsche Adresse liefert HTTP 200 (Soft-404)",
        "affected_url": domain.rstrip("/") + "/",
        "description": (
            f"Probe: {_pfad(probe)} existiert garantiert nicht. Der Server "
            f"antwortet mit {was}. Google wertet das als Soft-404; kaputte "
            "Links fallen dadurch niemandem auf, und falsche Adressen koennen "
            "als Kopie im Index landen."
        ),
        "fix_suggestion": (
            "Unbekannte Adressen mit echtem HTTP 404 beantworten (bei SPAs: "
            "Server-/Hosting-Regel nur fuer bekannte Routen auf index.html, "
            "sonst 404-Seite mit Status 404)."
        ),
        "estimated_impact": "Index-Qualitaet, Crawl-Budget, unentdeckte Linkfehler",
    }


def fingerabdruck_trennscharf(
    fp: Optional[Fingerabdruck], seiten: List[Dict[str, Any]], domain: str
) -> Optional[Fingerabdruck]:
    """Verwirft den Fingerabdruck, wenn eine echte Unterseite genauso aussieht.

    Bei einer reinen JavaScript-Huelle haben ALLE Adressen denselben Titel und
    Canonical — dann wuerde jeder gesunde Link als Catch-all gelten.
    """
    if fp is None:
        return None
    for seite in seiten:
        url = seite.get("url") or ""
        if _norm(url) == _norm(domain) or not seite.get("html"):
            continue
        titel, canonical = _titel_und_canonical(seite["html"])
        if fp.passt(titel, canonical, len(seite["html"].encode("utf-8", "ignore"))):
            logger.info(
                "[links] Catch-all-Fingerabdruck nicht trennscharf "
                f"({url} sieht gleich aus) — Vergleich abgeschaltet"
            )
            return None
    return fp
