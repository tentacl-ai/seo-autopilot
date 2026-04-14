"""
Public SEO Scan API – oeffentlicher Endpoint fuer tentacl.ai/seo-check/

Kein Login, kein Projekt noetig. URL eingeben, Report bekommen.
Rate Limited: 3 Scans/Stunde, 10/Tag pro IP.
"""

import asyncio
import logging
import time
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..agents.analyzer import AnalyzerAgent
from ..agents.base import AgentResult
from ..core.audit_context import AuditContext
from ..core.project_manager import ProjectConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public", tags=["public"])

# ============================================================
# In-Memory Storage (Scans + Rate Limits)
# ============================================================

# scan_id -> ScanResult
_scans: Dict[str, dict] = {}

# IP -> list of timestamps (for rate limiting)
_rate_limits: Dict[str, list] = defaultdict(list)

# Cleanup alte Scans nach 1h
_SCAN_TTL = 3600
_RATE_HOUR = 3
_RATE_DAY = 10
_MAX_PAGES = 10


# ============================================================
# Pydantic Models
# ============================================================


class ScanRequest(BaseModel):
    url: str


class ScanResponse(BaseModel):
    scan_id: str
    status: str
    message: str = ""


class ScanResultResponse(BaseModel):
    scan_id: str
    status: str  # queued | processing | completed | failed
    url: str = ""
    score: Optional[float] = None
    issues_total: int = 0
    issues_by_severity: Dict[str, int] = {}
    issues_by_category: Dict[str, int] = {}
    issues: List[Dict[str, Any]] = []
    pages_crawled: int = 0
    duration_seconds: float = 0
    error: Optional[str] = None


# ============================================================
# Rate Limiting
# ============================================================


def _check_rate_limit(ip: str) -> None:
    """Pruefe Rate Limit. Wirft HTTPException bei Ueberschreitung."""
    now = time.time()

    # Alte Eintraege entfernen (aelter als 24h)
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < 86400]

    timestamps = _rate_limits[ip]

    # Pro Stunde
    last_hour = [t for t in timestamps if now - t < 3600]
    if len(last_hour) >= _RATE_HOUR:
        raise HTTPException(
            status_code=429,
            detail=f"Maximal {_RATE_HOUR} Scans pro Stunde. Bitte warte etwas.",
        )

    # Pro Tag
    if len(timestamps) >= _RATE_DAY:
        raise HTTPException(
            status_code=429,
            detail=f"Maximal {_RATE_DAY} Scans pro Tag.",
        )


def _cleanup_old_scans() -> None:
    """Entferne Scans aelter als TTL."""
    now = time.time()
    expired = [
        sid for sid, s in _scans.items() if now - s.get("created_at", 0) > _SCAN_TTL
    ]
    for sid in expired:
        del _scans[sid]


# ============================================================
# URL Validation
# ============================================================


def _validate_url(url: str) -> str:
    """Validiere und normalisiere URL. Gibt bereinigte URL zurueck."""
    url = url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL darf nicht leer sein.")

    # Prefix hinzufuegen wenn noetig
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url

    parsed = urlparse(url)

    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="Ungueltige URL.")

    # Keine Private IPs / localhost
    hostname = parsed.hostname.lower()
    blocked = ["localhost", "127.0.0.1", "0.0.0.0", "::1", "10.", "192.168.", "172."]
    for b in blocked:
        if hostname.startswith(b):
            raise HTTPException(
                status_code=400, detail="Lokale/private Adressen sind nicht erlaubt."
            )

    # Domain muss mindestens einen Punkt haben
    if "." not in hostname:
        raise HTTPException(status_code=400, detail="Bitte eine vollstaendige Domain eingeben.")

    return f"{parsed.scheme}://{parsed.netloc}"


# ============================================================
# Routes
# ============================================================


@router.post("/scan", response_model=ScanResponse)
async def start_scan(req: ScanRequest, request: Request):
    """Starte einen oeffentlichen SEO-Scan fuer eine URL."""
    # Rate Limiting
    client_ip = request.headers.get("x-real-ip", request.client.host)
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    _check_rate_limit(client_ip)

    # URL validieren
    domain = _validate_url(req.url)

    # Cleanup
    _cleanup_old_scans()

    # Scan-ID
    scan_id = f"ps_{int(time.time())}_{hash(domain) % 10000:04d}"

    # In-Memory speichern
    _scans[scan_id] = {
        "scan_id": scan_id,
        "status": "processing",
        "url": domain,
        "created_at": time.time(),
        "score": None,
        "issues": [],
        "issues_total": 0,
        "issues_by_severity": {},
        "issues_by_category": {},
        "pages_crawled": 0,
        "duration_seconds": 0,
        "error": None,
    }

    # Rate Limit Timestamp
    _rate_limits[client_ip].append(time.time())

    # Scan als Background-Task starten
    asyncio.create_task(_run_scan(scan_id, domain))

    logger.info(f"[public-scan] Started {scan_id} for {domain} (IP: {client_ip})")

    return ScanResponse(
        scan_id=scan_id,
        status="processing",
        message=f"Scan gestartet fuer {domain}",
    )


@router.get("/scan/{scan_id}", response_model=ScanResultResponse)
async def get_scan_result(scan_id: str):
    """Hole das Ergebnis eines Scans."""
    scan = _scans.get(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan nicht gefunden oder abgelaufen.")

    return ScanResultResponse(**{k: v for k, v in scan.items() if k != "created_at"})


# ============================================================
# Background Task: Der echte Scan
# ============================================================


async def _run_scan(scan_id: str, domain: str) -> None:
    """Fuehrt den echten SEO-Scan durch."""
    start = time.time()

    try:
        # Dummy ProjectConfig fuer den AnalyzerAgent
        project_config = ProjectConfig(
            id="public-scan",
            domain=domain,
            name="Public Scan",
            adapter_type="static",
            adapter_config={"max_pages": _MAX_PAGES},
            enabled_sources=[],
            source_config={},
        )

        audit_id = scan_id
        ctx = AuditContext(
            audit_id=audit_id,
            project_id="public-scan",
            project_config=project_config,
        )

        # Nur den AnalyzerAgent laufen lassen (kein Keyword/Strategy/Content)
        analyzer = AnalyzerAgent(
            project_id="public-scan",
            audit_id=audit_id,
            project_config=project_config,
            context=ctx,
        )

        result: AgentResult = await analyzer.run()
        ctx.add_result("analyzer", result)
        ctx.completed_at = datetime.utcnow()
        ctx.status = "completed"
        ctx.calculate_score()

        # Issues mit deutschen Titeln anreichern
        issues = _translate_issues(result.issues or [])

        # Ergebnis speichern
        _scans[scan_id].update(
            {
                "status": "completed",
                "score": ctx.score,
                "issues": issues,
                "issues_total": len(issues),
                "issues_by_severity": ctx.issues_by_severity(),
                "issues_by_category": ctx.issues_by_category(),
                "pages_crawled": int(
                    (result.log_output or "").split("/")[0].split()[-1]
                    if "/" in (result.log_output or "")
                    else 0
                ),
                "duration_seconds": round(time.time() - start, 1),
            }
        )

        logger.info(
            f"[public-scan] Completed {scan_id}: score={ctx.score}, "
            f"issues={len(issues)}, {round(time.time() - start, 1)}s"
        )

    except Exception as exc:
        logger.exception(f"[public-scan] Failed {scan_id}: {exc}")
        _scans[scan_id].update(
            {
                "status": "failed",
                "error": str(exc),
                "duration_seconds": round(time.time() - start, 1),
            }
        )


# ============================================================
# Issue Translation (Tech -> Klartext)
# ============================================================

_TRANSLATIONS = {
    "missing_title": {
        "title_de": "Seitentitel fehlt",
        "desc_de": "Deiner Seite fehlt ein Titel. Das ist die Ueberschrift die Google in den Suchergebnissen anzeigt.",
    },
    "short_title": {
        "title_de": "Seitentitel zu kurz",
        "desc_de": "Dein Titel ist zu kurz. Google zeigt bis zu 65 Zeichen an -- nutze den Platz fuer mehr Sichtbarkeit.",
    },
    "long_title": {
        "title_de": "Seitentitel zu lang",
        "desc_de": "Dein Titel ist zu lang und wird von Google abgeschnitten. Kuerze ihn auf maximal 65 Zeichen.",
    },
    "missing_meta_description": {
        "title_de": "Meta-Beschreibung fehlt",
        "desc_de": "Deiner Seite fehlt eine Meta-Beschreibung. Das ist der kurze Text unter dem Titel bei Google -- ohne ihn sucht sich Google selbst einen Text aus.",
    },
    "short_meta_description": {
        "title_de": "Meta-Beschreibung zu kurz",
        "desc_de": "Deine Meta-Beschreibung ist zu kurz. Nutze mindestens 80 Zeichen um Google und Besuchern zu zeigen, worum es auf deiner Seite geht.",
    },
    "missing_h1": {
        "title_de": "Hauptueberschrift (H1) fehlt",
        "desc_de": "Deiner Seite fehlt eine Hauptueberschrift. Das ist wie ein Buch ohne Titel -- Google weiss nicht genau, worum es geht.",
    },
    "multiple_h1": {
        "title_de": "Mehrere Hauptueberschriften",
        "desc_de": "Deine Seite hat mehrere H1-Ueberschriften. Es sollte nur eine geben, damit Google das Hauptthema klar erkennt.",
    },
    "missing_og_tags": {
        "title_de": "Open Graph Tags fehlen",
        "desc_de": "Wenn jemand deine Seite auf Social Media teilt, wird kein Vorschaubild oder Titel angezeigt. Open Graph Tags loesen das.",
    },
    "missing_twitter_card": {
        "title_de": "Twitter Card fehlt",
        "desc_de": "Beim Teilen auf X (Twitter) fehlt das Vorschaubild. Twitter Card Tags loesen das.",
    },
    "no_https": {
        "title_de": "Kein HTTPS",
        "desc_de": "Deine Seite ist nicht verschluesselt. Browser zeigen Besuchern eine Warnung -- das kostet Vertrauen und Ranking.",
    },
    "missing_hsts": {
        "title_de": "HSTS-Header fehlt",
        "desc_de": "Der HSTS-Header fehlt. Damit wuerden Browser immer die sichere HTTPS-Verbindung nutzen.",
    },
    "missing_viewport": {
        "title_de": "Viewport nicht konfiguriert",
        "desc_de": "Deine Seite ist nicht fuer Mobilgeraete eingerichtet. Die meisten Besucher kommen ueber das Handy.",
    },
    "missing_lang": {
        "title_de": "Sprach-Attribut fehlt",
        "desc_de": "Google weiss nicht, in welcher Sprache deine Seite geschrieben ist. Das lang-Attribut loest das.",
    },
    "images_without_alt": {
        "title_de": "Bilder ohne Beschreibung",
        "desc_de": "Einige Bilder haben keine Beschreibung (Alt-Text). Google kann nicht sehen -- es braucht Text um Bilder zu verstehen.",
    },
    "missing_schema": {
        "title_de": "Keine strukturierten Daten",
        "desc_de": "Keine Schema.org Daten gefunden. Damit kann Google erweiterte Suchergebnisse anzeigen (Sterne, Preise, FAQ).",
    },
    "missing_sitemap": {
        "title_de": "Keine Sitemap gefunden",
        "desc_de": "Ohne Sitemap muss Google selbst herausfinden welche Seiten es gibt -- und uebersieht vielleicht wichtige.",
    },
    "missing_robots_txt": {
        "title_de": "Keine robots.txt",
        "desc_de": "Die robots.txt sagt Suchmaschinen welche Bereiche deiner Seite sie besuchen duerfen. Sie fehlt.",
    },
    "slow_response": {
        "title_de": "Langsame Antwortzeit",
        "desc_de": "Deine Seite antwortet langsam. Jede Sekunde Ladezeit kostet dich Besucher und Ranking.",
    },
    "fetch_error": {
        "title_de": "Seite nicht erreichbar",
        "desc_de": "Diese Seite konnte nicht geladen werden. Prüfe ob sie erreichbar ist.",
    },
    "http_404": {
        "title_de": "Seite nicht gefunden (404)",
        "desc_de": "Diese Seite gibt einen 404-Fehler zurueck. Tote Links schaden deinem Ranking.",
    },
    "canonical_missing": {
        "title_de": "Canonical-Tag fehlt",
        "desc_de": "Google koennte diese Seite mehrfach finden und weiss nicht, welche Version die richtige ist.",
    },
    "canonical_mismatch": {
        "title_de": "Canonical-Tag stimmt nicht",
        "desc_de": "Der Canonical-Tag zeigt auf eine andere URL. Google ist verwirrt, welche Seite die richtige ist.",
    },
    "missing_llms_txt": {
        "title_de": "llms.txt fehlt",
        "desc_de": "Deine Seite hat keine /llms.txt. Diese Datei hilft KI-Systemen (ChatGPT, Claude, Perplexity) deine Seite zu verstehen.",
    },
    "invalid_llms_syntax": {
        "title_de": "llms.txt fehlerhaft",
        "desc_de": "Deine llms.txt hat Formatierungsfehler. KI-Systeme koennen sie nicht korrekt lesen.",
    },
    "llms_no_links": {
        "title_de": "llms.txt ohne Links",
        "desc_de": "Deine llms.txt hat keine Links zu wichtigen Seiten. KI-Systeme finden deine Inhalte nicht.",
    },
    "missing_llms_full_txt": {
        "title_de": "llms-full.txt fehlt (optional)",
        "desc_de": "Die erweiterte /llms-full.txt fehlt. Sie gibt KI-Systemen tieferes Verstaendnis deiner Inhalte.",
    },
    "missing_ai_txt": {
        "title_de": "ai.txt fehlt",
        "desc_de": "Die /ai.txt fehlt. Dieser neue Standard erlaubt dir, KI-Crawler-Berechtigungen explizit zu steuern.",
    },
    "missing_indexnow": {
        "title_de": "IndexNow nicht eingerichtet",
        "desc_de": "Ohne IndexNow dauert es Tage bis Bing, DuckDuckGo und Yandex neue Inhalte finden. Mit IndexNow geht es sofort.",
    },
}

_CATEGORY_DE = {
    "meta": "Grundlagen",
    "crawl": "Technik",
    "content": "Inhalt",
    "social": "Social Media",
    "accessibility": "Grundlagen",
    "schema": "Struktur",
    "security": "Technik",
    "performance": "Performance",
    "redirect": "Technik",
    "canonical": "Struktur",
    "geo": "Struktur",
    "topical_authority": "Inhalt",
    "duplicate_content": "Inhalt",
    "link_graph": "Struktur",
    "robots_sitemap": "Technik",
    "eeat": "Inhalt",
    "llms_ai": "KI-Sichtbarkeit",
}


def _translate_issues(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reichere Issues mit deutschen Klartext-Titeln an."""
    translated = []
    for issue in issues:
        i = dict(issue)
        issue_type = i.get("type", "")

        # Deutsche Uebersetzung wenn vorhanden
        tr = _TRANSLATIONS.get(issue_type, {})
        if tr:
            i["title_de"] = tr["title_de"]
            i["description_de"] = tr["desc_de"]

        # Kategorie uebersetzen
        cat = i.get("category", "other")
        i["category_de"] = _CATEGORY_DE.get(cat, cat.capitalize())

        translated.append(i)
    return translated
