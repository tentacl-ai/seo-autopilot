"""
Gegenprobe für schwere Befunde ("Auto-Verify").

Am 2026-08-17 waren bei joseph-hehenwarter.de **fünf von fünf** schweren
Befunden falsch — Impressum, Datenschutz, Firmen-Schema, Sitemap-Adressen,
Bildbeschreibungen. Alle fünf ließen sich in Sekunden am Live-HTML widerlegen.
Genau das macht dieses Modul jetzt automatisch, BEVOR ein Befund gemeldet wird.

Grundregel: **Im Zweifel bleibt der Befund stehen.** Ein Prüfer entfernt nur,
was er positiv widerlegen kann. Ein Netzwerkfehler oder ein unbekannter
Befundtyp führt nie zum Verwerfen — sonst tauschen wir falsche Alarme gegen
übersehene Probleme, und das wäre die schlechtere Hälfte.

Die widerlegten Befunde werden zurückgegeben, nicht nur verworfen: Sie sind
das Rohmaterial für die Lernschleife (jeder wiederkehrende Fehlalarm gehört
als Regressionstest festgenagelt).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

BESTAETIGT = "confirmed"
WIDERLEGT = "refuted"
UNKLAR = "unknown"

# Pfade, unter denen Rechtsseiten üblicherweise liegen.
IMPRESSUM_PFADE = (
    "/impressum",
    "/imprint",
    "/legal-notice",
    "/legal",
    "/impressum.html",
)
DATENSCHUTZ_PFADE = (
    "/datenschutz",
    "/privacy",
    "/privacy-policy",
    "/datenschutzerklaerung",
    "/data-protection",
    "/datenschutz.html",
)

# Organization und seine gängigen Untertypen (siehe analyzers/eeat.py)
ORG_MARKER = re.compile(
    r'"@type"\s*:\s*(\[[^\]]*?)?"?(Organization|Corporation|LocalBusiness|'
    r"FinancialService|ProfessionalService|LegalService|InsuranceAgency|Store|"
    r'Restaurant|MedicalOrganization|NGO|EducationalOrganization)"?',
    re.IGNORECASE,
)


@dataclass
class Pruefergebnis:
    status: str
    begruendung: str


async def _hole(client: httpx.AsyncClient, url: str) -> Optional[httpx.Response]:
    try:
        r = await client.get(url, follow_redirects=True, timeout=15.0)
        return r
    except Exception as exc:
        logger.debug(f"[verify] Abruf fehlgeschlagen {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Einzelne Prüfer
# ---------------------------------------------------------------------------


async def _pruefe_rechtsseite(
    client: httpx.AsyncClient, domain: str, pfade: Tuple[str, ...], name: str
) -> Pruefergebnis:
    """Existiert die Seite wirklich nicht? Direkt abrufen statt dem Crawl glauben.

    Der Crawl kann sie schlicht nicht erreicht haben (Seitenlimit, kein Link im
    statischen HTML) — das ist kein fehlendes Impressum.
    """
    basis = domain.rstrip("/")
    for pfad in pfade:
        r = await _hole(client, f"{basis}{pfad}")
        if r is not None and r.status_code == 200 and len(r.text) > 500:
            return Pruefergebnis(
                WIDERLEGT, f"{name} ist unter {pfad} erreichbar (HTTP 200)"
            )

    # Zweiter Versuch: steht ein Link auf der Startseite?
    start = await _hole(client, basis)
    if start is not None and start.status_code == 200:
        for pfad in pfade:
            if f'href="{pfad}' in start.text or f'href="{basis}{pfad}' in start.text:
                return Pruefergebnis(
                    WIDERLEGT, f"{name} ist auf der Startseite verlinkt ({pfad})"
                )
        return Pruefergebnis(BESTAETIGT, f"Kein {name} gefunden — weder Pfad noch Link")
    return Pruefergebnis(UNKLAR, "Startseite nicht abrufbar — Befund bleibt bestehen")


async def _pruefe_org_schema(
    client: httpx.AsyncClient, domain: str, issue: Dict[str, Any]
) -> Pruefergebnis:
    r = await _hole(client, domain.rstrip("/"))
    if r is None or r.status_code != 200:
        return Pruefergebnis(UNKLAR, "Startseite nicht abrufbar")
    if ORG_MARKER.search(r.text):
        treffer = ORG_MARKER.search(r.text)
        return Pruefergebnis(
            WIDERLEGT, f"Organisations-Schema vorhanden ({treffer.group(2)})"
        )
    return Pruefergebnis(BESTAETIGT, "Kein Organisations-Schema im HTML gefunden")


async def _pruefe_nicht_erreichbar(
    client: httpx.AsyncClient, domain: str, issue: Dict[str, Any]
) -> Pruefergebnis:
    """'Von der Startseite nicht erreichbar' — steht der Link doch dort?"""
    ziel = _betroffene_url(issue)
    if not ziel:
        return Pruefergebnis(UNKLAR, "Keine betroffene Adresse im Befund")
    r = await _hole(client, domain.rstrip("/"))
    if r is None or r.status_code != 200:
        return Pruefergebnis(UNKLAR, "Startseite nicht abrufbar")
    pfad = ziel.replace(domain.rstrip("/"), "") or "/"
    if f'href="{pfad}"' in r.text or f'href="{ziel}"' in r.text:
        return Pruefergebnis(WIDERLEGT, f"Startseite verlinkt {pfad} sehr wohl")
    return Pruefergebnis(BESTAETIGT, f"Kein Link auf {pfad} im Start-HTML")


async def _pruefe_noindex(
    client: httpx.AsyncClient, domain: str, issue: Dict[str, Any]
) -> Pruefergebnis:
    ziel = _betroffene_url(issue) or domain
    r = await _hole(client, ziel)
    if r is None or r.status_code != 200:
        return Pruefergebnis(UNKLAR, "Seite nicht abrufbar")
    kopf = r.text[:20000].lower()
    if "noindex" in kopf or "noindex" in r.headers.get("x-robots-tag", "").lower():
        return Pruefergebnis(BESTAETIGT, "noindex im HTML bzw. X-Robots-Tag bestätigt")
    return Pruefergebnis(WIDERLEGT, "Kein noindex auffindbar")


async def _pruefe_bilder_ohne_alt(
    client: httpx.AsyncClient, domain: str, issue: Dict[str, Any]
) -> Pruefergebnis:
    """Nur ein FEHLENDES alt zählt; `alt=""` ist bei Deko-Bildern korrekt."""
    ziel = _betroffene_url(issue) or domain
    r = await _hole(client, ziel)
    if r is None or r.status_code != 200:
        return Pruefergebnis(UNKLAR, "Seite nicht abrufbar")
    bilder = re.findall(r"<img\b[^>]*>", r.text, re.IGNORECASE)
    ohne_alt = [b for b in bilder if not re.search(r"\balt\s*=", b, re.IGNORECASE)]
    if not ohne_alt:
        return Pruefergebnis(
            WIDERLEGT,
            f"Alle {len(bilder)} Bilder haben ein alt-Attribut "
            "(leeres alt ist bei dekorativen Bildern korrekt)",
        )
    return Pruefergebnis(BESTAETIGT, f"{len(ohne_alt)} Bilder ohne alt-Attribut")


def _betroffene_url(issue: Dict[str, Any]) -> Optional[str]:
    direkt = issue.get("affected_url")
    if direkt:
        return direkt
    roh = issue.get("affected_items")
    if not roh:
        return None
    try:
        daten = json.loads(roh) if isinstance(roh, str) else roh
    except (ValueError, TypeError):
        return None
    if isinstance(daten, dict):
        return daten.get("url")
    if isinstance(daten, list) and daten:
        erst = daten[0]
        return erst.get("url") if isinstance(erst, dict) else str(erst)
    return None


# Befundtyp -> Prüfer. Alles, was hier nicht steht, bleibt unangetastet.
PRUEFER = {
    "missing_impressum": lambda c, d, i: _pruefe_rechtsseite(
        c, d, IMPRESSUM_PFADE, "Impressum"
    ),
    "missing_datenschutz": lambda c, d, i: _pruefe_rechtsseite(
        c, d, DATENSCHUTZ_PFADE, "Datenschutzerklärung"
    ),
    "missing_privacy": lambda c, d, i: _pruefe_rechtsseite(
        c, d, DATENSCHUTZ_PFADE, "Datenschutzerklärung"
    ),
    "missing_org_schema": _pruefe_org_schema,
    "orphan_page": _pruefe_nicht_erreichbar,
    "unreachable_page": _pruefe_nicht_erreichbar,
    "noindex": _pruefe_noindex,
    "page_noindex": _pruefe_noindex,
    "images_without_alt": _pruefe_bilder_ohne_alt,
}


async def verify_issues(
    issues: List[Dict[str, Any]],
    domain: str,
    nur_schweregrade: Tuple[str, ...] = ("high",),
    client: Optional[httpx.AsyncClient] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Prüft Befunde gegen die Realität.

    Rückgabe: (verbleibende Befunde, widerlegte Befunde).
    Bestätigte Befunde bekommen `verified: True` plus Begründung — damit im
    Bericht sichtbar ist, was nachgeprüft wurde und was nicht.
    """
    eigener_client = client is None
    if eigener_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": "SEOAutopilotBot/verify"}, timeout=15.0
        )

    verbleibend: List[Dict[str, Any]] = []
    widerlegt: List[Dict[str, Any]] = []
    try:
        for issue in issues:
            typ = issue.get("type", "")
            pruefer = PRUEFER.get(typ)
            if pruefer is None or issue.get("severity") not in nur_schweregrade:
                verbleibend.append(issue)
                continue
            try:
                ergebnis = await pruefer(client, domain, issue)
            except Exception as exc:  # Prüfer darf niemals den Lauf killen
                logger.warning(f"[verify] Prüfer für {typ} fehlgeschlagen: {exc}")
                verbleibend.append(issue)
                continue

            if ergebnis.status == WIDERLEGT:
                issue = dict(issue)
                issue["refuted_reason"] = ergebnis.begruendung
                widerlegt.append(issue)
                logger.info(
                    f"[verify] Befund '{typ}' verworfen: {ergebnis.begruendung}"
                )
            else:
                issue = dict(issue)
                issue["verified"] = ergebnis.status == BESTAETIGT
                issue["verify_note"] = ergebnis.begruendung
                verbleibend.append(issue)
    finally:
        if eigener_client:
            await client.aclose()

    if widerlegt:
        logger.info(
            f"[verify] {len(widerlegt)} von {len(issues)} Befunden widerlegt "
            "und aus dem Bericht entfernt"
        )
    return verbleibend, widerlegt
