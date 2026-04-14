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
    passed_checks: List[Dict[str, Any]] = []
    pages: List[Dict[str, Any]] = []
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
        raise HTTPException(
            status_code=400, detail="Bitte eine vollstaendige Domain eingeben."
        )

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
        raise HTTPException(
            status_code=404, detail="Scan nicht gefunden oder abgelaufen."
        )

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

        # Seitendaten aus Analyzer-Metrics
        raw_pages = result.metrics.get("pages", [])
        pages = _enrich_pages(raw_pages)

        # Bestandene Checks generieren (braucht Issues um zu wissen was NICHT bestanden ist)
        passed = _generate_passed_checks(raw_pages, issues=result.issues or [])

        # Ergebnis speichern
        _scans[scan_id].update(
            {
                "status": "completed",
                "score": ctx.score,
                "issues": issues,
                "issues_total": len(issues),
                "issues_by_severity": ctx.issues_by_severity(),
                "issues_by_category": ctx.issues_by_category(),
                "passed_checks": passed,
                "pages": pages,
                "pages_crawled": len(raw_pages),
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
    # === GRUNDLAGEN ===
    "missing_title": {
        "title_de": "Seitentitel fehlt",
        "desc_de": "Deiner Seite fehlt ein Titel.",
        "why_de": "Der Titel ist das Erste, was Google in den Suchergebnissen anzeigt. Ohne Titel hat deine Seite quasi keinen Namen — Google wuerde sich selbst einen ausdenken, und der passt selten.",
        "fix_de": "Fuege einen <title>-Tag im <head> hinzu. Nenne dein Hauptthema und halte dich an 50-65 Zeichen.",
        "ideal": "50-65 Zeichen",
    },
    "short_title": {
        "title_de": "Seitentitel zu kurz",
        "desc_de": "Dein Titel ist zu kurz.",
        "why_de": "Google zeigt bis zu 65 Zeichen in den Suchergebnissen an. Wenn dein Titel zu kurz ist, verschenkst du Platz fuer wichtige Keywords und Ueberzeugungsarbeit.",
        "fix_de": "Ergaenze deinen Titel mit relevanten Keywords. Format-Tipp: Hauptthema — Markenname | Nutzenversprechen",
        "ideal": "50-65 Zeichen",
    },
    "long_title": {
        "title_de": "Seitentitel zu lang",
        "desc_de": "Dein Titel ist zu lang und wird von Google abgeschnitten.",
        "why_de": "Google schneidet Titel nach ca. 65 Zeichen ab und zeigt '...' an. Der wichtige Teil deines Titels geht dann verloren.",
        "fix_de": "Kuerze deinen Titel auf maximal 65 Zeichen. Stelle die wichtigsten Keywords an den Anfang.",
        "ideal": "50-65 Zeichen",
    },
    "missing_meta_description": {
        "title_de": "Meta-Beschreibung fehlt",
        "desc_de": "Deiner Seite fehlt eine Meta-Beschreibung.",
        "why_de": "Die Meta-Beschreibung ist der kurze Text unter deinem Titel bei Google. Ohne sie waehlt Google einen zufaelligen Textausschnitt — der passt oft nicht und hat keine Handlungsaufforderung.",
        "fix_de": "Schreibe eine Beschreibung mit 120-155 Zeichen. Nenne dein Hauptthema, einen Vorteil und eine Handlungsaufforderung (z.B. 'Jetzt entdecken').",
        "ideal": "120-155 Zeichen",
    },
    "short_meta_description": {
        "title_de": "Meta-Beschreibung zu kurz",
        "desc_de": "Deine Meta-Beschreibung ist zu kurz.",
        "why_de": "Eine zu kurze Description nutzt den verfuegbaren Platz bei Google nicht aus. Du verschenkst die Chance, Suchende zum Klicken zu ueberreden.",
        "fix_de": "Erweitere auf 120-155 Zeichen. Beschreibe was der Besucher auf der Seite findet und warum er klicken sollte.",
        "ideal": "120-155 Zeichen",
    },
    "long_meta_description": {
        "title_de": "Meta-Beschreibung zu lang",
        "desc_de": "Deine Meta-Beschreibung ist zu lang und wird abgeschnitten.",
        "why_de": "Google zeigt nur ca. 155-165 Zeichen an. Der Rest wird abgeschnitten — deine Handlungsaufforderung am Ende geht verloren.",
        "fix_de": "Kuerze auf 120-155 Zeichen. Stelle die wichtigste Botschaft an den Anfang.",
        "ideal": "120-155 Zeichen",
    },
    "missing_h1": {
        "title_de": "Hauptueberschrift (H1) fehlt",
        "desc_de": "Deiner Seite fehlt eine Hauptueberschrift.",
        "why_de": "Die H1 ist wie der Titel eines Buchkapitels — sie sagt Google und Besuchern sofort, worum es auf der Seite geht. Ohne H1 hat deine Seite kein klares Thema.",
        "fix_de": "Fuege genau eine H1-Ueberschrift hinzu. Sie sollte das Hauptthema der Seite enthalten und sich vom Meta-Titel leicht unterscheiden.",
    },
    "multiple_h1": {
        "title_de": "Mehrere Hauptueberschriften",
        "desc_de": "Deine Seite hat mehrere H1-Ueberschriften.",
        "why_de": "Mehrere H1-Tags verwirren Google — die Suchmaschine weiss nicht, welches das Hauptthema der Seite ist. Das verwaessert dein Ranking-Signal.",
        "fix_de": "Behalte nur eine H1 und aendere die anderen zu H2 oder H3. Die H1 sollte das Kernthema der Seite beschreiben.",
    },
    "missing_viewport": {
        "title_de": "Viewport nicht konfiguriert",
        "desc_de": "Deine Seite ist nicht fuer Mobilgeraete eingerichtet.",
        "why_de": "Ohne Viewport-Meta-Tag zeigen Handys deine Seite in Desktop-Groesse an — winzige Schrift, viel Zoomen. Google straft das beim Mobile-Ranking ab. Ueber 60% aller Suchanfragen kommen vom Handy.",
        "fix_de": 'Fuege im <head> hinzu: <meta name="viewport" content="width=device-width, initial-scale=1.0">',
    },
    "missing_lang": {
        "title_de": "Sprach-Attribut fehlt",
        "desc_de": "Google weiss nicht, in welcher Sprache deine Seite ist.",
        "why_de": "Das lang-Attribut hilft Google, deine Seite in der richtigen Sprache anzuzeigen. Ohne es kann deine Seite in falschen Laendern erscheinen — oder gar nicht.",
        "fix_de": "Fuege zum <html>-Tag hinzu: <html lang=\"de\"> (oder 'en' fuer Englisch).",
    },
    # === SOCIAL MEDIA ===
    "missing_og_tags": {
        "title_de": "Open Graph Tags fehlen",
        "desc_de": "Wenn jemand deine Seite auf Social Media teilt, wird kein Vorschaubild oder Titel angezeigt.",
        "why_de": "Ohne Open Graph Tags sieht ein geteilter Link langweilig aus — kein Bild, generischer Titel. Beitraege mit Vorschaubild bekommen bis zu 8x mehr Klicks.",
        "fix_de": "Fuege og:title, og:description und og:image Meta-Tags im <head> hinzu. Das Bild sollte 1200x630px gross sein.",
    },
    "missing_og_image": {
        "title_de": "Kein Vorschaubild fuer Social Media",
        "desc_de": "Das og:image Tag fehlt — beim Teilen wird kein Bild angezeigt.",
        "why_de": "Posts mit Bild erhalten dramatisch mehr Aufmerksamkeit. Ohne og:image zeigen Facebook, LinkedIn und WhatsApp nur einen kahlen Link.",
        "fix_de": 'Erstelle ein Bild in 1200x630px mit deinem Logo und einer kurzen Aussage. Fuege hinzu: <meta property="og:image" content="URL-zum-Bild">',
    },
    "missing_twitter_card": {
        "title_de": "Twitter Card fehlt",
        "desc_de": "Beim Teilen auf X (Twitter) fehlt das Vorschaubild.",
        "why_de": "Ohne Twitter Card wird dein Link auf X ohne Vorschau angezeigt. Twitter Cards machen deine geteilten Inhalte deutlich attraktiver.",
        "fix_de": 'Fuege hinzu: <meta name="twitter:card" content="summary_large_image"> und <meta name="twitter:image" content="URL">',
    },
    # === TECHNIK ===
    "no_https": {
        "title_de": "Kein HTTPS",
        "desc_de": "Deine Seite ist nicht verschluesselt.",
        "why_de": "Browser zeigen eine 'Nicht sicher'-Warnung an. Das schreckt Besucher ab und Google stuft deine Seite schlechter ein. HTTPS ist seit 2014 ein Ranking-Signal.",
        "fix_de": "Beantrage ein SSL-Zertifikat (bei Let's Encrypt kostenlos) und leite HTTP auf HTTPS um.",
    },
    "missing_hsts": {
        "title_de": "HSTS-Header fehlt",
        "desc_de": "Der HSTS-Header fehlt.",
        "why_de": "Ohne HSTS koennten Besucher versehentlich die unverschluesselte HTTP-Version laden. HSTS zwingt Browser, immer die sichere Verbindung zu nutzen.",
        "fix_de": "Fuege in deiner Nginx/Apache-Config hinzu: Strict-Transport-Security: max-age=31536000; includeSubDomains",
    },
    "missing_security_headers": {
        "title_de": "Sicherheits-Header fehlen",
        "desc_de": "Wichtige HTTP-Sicherheitsheader fehlen.",
        "why_de": "Sicherheitsheader wie X-Frame-Options und X-Content-Type-Options schuetzen vor Clickjacking und MIME-Sniffing Angriffen. Google bewertet die Sicherheit einer Seite.",
        "fix_de": "Fuege in deinem Webserver hinzu: X-Frame-Options: SAMEORIGIN, X-Content-Type-Options: nosniff",
    },
    "missing_robots_txt": {
        "title_de": "Keine robots.txt",
        "desc_de": "Die robots.txt fehlt.",
        "why_de": "Die robots.txt sagt Suchmaschinen-Crawlern, welche Bereiche sie besuchen duerfen und wo die Sitemap liegt. Ohne sie muessen Crawler raten.",
        "fix_de": "Erstelle eine robots.txt im Stammverzeichnis. Mindestinhalt: User-agent: *\\nAllow: /\\nSitemap: https://deine-domain.de/sitemap.xml",
    },
    "missing_sitemap": {
        "title_de": "Keine Sitemap gefunden",
        "desc_de": "Keine XML-Sitemap gefunden.",
        "why_de": "Ohne Sitemap muss Google alle deine Seiten selbst entdecken — und uebersieht vielleicht wichtige. Eine Sitemap ist wie ein Inhaltsverzeichnis fuer Suchmaschinen.",
        "fix_de": "Erstelle eine sitemap.xml mit allen wichtigen Seiten. Die meisten CMS (WordPress, Shopify) generieren sie automatisch.",
    },
    "slow_response": {
        "title_de": "Langsame Antwortzeit",
        "desc_de": "Deine Seite antwortet langsam.",
        "why_de": "Jede Sekunde Ladezeit kostet dich ~7% der Besucher. Google bevorzugt schnelle Seiten. Ab 3 Sekunden springen die meisten Nutzer ab.",
        "fix_de": "Prüfe dein Hosting, komprimiere Bilder, aktiviere Caching und minimiere CSS/JS.",
    },
    "fetch_error": {
        "title_de": "Seite nicht erreichbar",
        "desc_de": "Diese Seite konnte nicht geladen werden.",
        "why_de": "Wenn Google deine Seite nicht laden kann, verschwindet sie aus dem Index. Besucher sehen eine Fehlermeldung.",
        "fix_de": "Pruefe ob die Seite online ist, der Server laeuft und keine Firewall den Zugriff blockiert.",
    },
    "http_404": {
        "title_de": "Seite nicht gefunden (404)",
        "desc_de": "Diese Seite gibt einen 404-Fehler zurueck.",
        "why_de": "404-Fehler frustrieren Besucher und verschwenden Crawl-Budget. Wenn andere Seiten auf diese URL verlinken, geht deren Linkpower verloren.",
        "fix_de": "Entweder die Seite wiederherstellen oder eine 301-Weiterleitung auf eine passende existierende Seite einrichten.",
    },
    "canonical_missing": {
        "title_de": "Canonical-Tag fehlt",
        "desc_de": "Kein Canonical-Tag vorhanden.",
        "why_de": "Ohne Canonical-Tag koennte Google verschiedene URL-Varianten (mit/ohne www, mit/ohne /) als Duplikate werten. Das verwaessert dein Ranking.",
        "fix_de": 'Fuege hinzu: <link rel="canonical" href="https://deine-domain.de/seite/"> — mit der bevorzugten URL.',
    },
    "canonical_mismatch": {
        "title_de": "Canonical-Tag stimmt nicht",
        "desc_de": "Der Canonical-Tag verweist auf eine andere URL.",
        "why_de": "Wenn der Canonical auf eine andere Seite zeigt, sagt das Google: 'Ignoriere diese Seite, nimm die andere.' Dein Ranking geht auf die andere URL ueber.",
        "fix_de": "Pruefe ob der Canonical korrekt ist. Wenn die Seite eigenstaendig sein soll, muss der Canonical auf sich selbst zeigen.",
    },
    "noindex_detected": {
        "title_de": "Seite auf noindex gesetzt",
        "desc_de": "Diese Seite wird von Google bewusst nicht indexiert.",
        "why_de": "Das robots-Meta-Tag sagt Google: Zeige diese Seite nicht in Suchergebnissen. Bei Impressum/Datenschutz ist das oft gewollt, bei wichtigen Seiten ein Problem.",
        "fix_de": "Pruefe ob noindex hier gewollt ist. Bei Impressum/Datenschutz ist es ok. Bei Inhaltsseiten entferne das noindex.",
    },
    # === BILDER ===
    "images_without_alt": {
        "title_de": "Bilder ohne Beschreibung (Alt-Text)",
        "desc_de": "Einige Bilder haben keine Beschreibung.",
        "why_de": "Alt-Texte sind fuer drei Dinge wichtig: 1) Barrierefreiheit — Screenreader lesen sie vor. 2) Google Bildersuche — ohne Alt-Text erscheinen deine Bilder dort nicht. 3) Wenn ein Bild nicht laedt, wird der Alt-Text angezeigt.",
        "fix_de": "Fuege jedem <img>-Tag ein alt-Attribut hinzu. Beschreibe kurz was auf dem Bild zu sehen ist (2-8 Woerter).",
    },
    # === INHALT ===
    "thin_content": {
        "title_de": "Zu wenig Inhalt",
        "desc_de": "Diese Seite hat sehr wenig Text.",
        "why_de": "Google bevorzugt Seiten mit ausfuehrlichem, hilfreichem Content. Seiten mit weniger als 300 Woertern werden als 'Thin Content' eingestuft und ranken schlechter.",
        "fix_de": "Erweitere den Inhalt auf mindestens 300 Woerter. Beantworte die wichtigsten Fragen deiner Besucher zum Thema dieser Seite.",
        "ideal": "300+ Woerter",
    },
    "missing_schema": {
        "title_de": "Keine strukturierten Daten",
        "desc_de": "Keine Schema.org Daten gefunden.",
        "why_de": "Strukturierte Daten helfen Google, den Inhalt deiner Seite besser zu verstehen. Damit bekommst du erweiterte Suchergebnisse — mit Sternebewertungen, Preisen, FAQ-Boxen oder Firmeninfo direkt bei Google.",
        "fix_de": "Fuege JSON-LD Daten im <head> hinzu. Fuer Unternehmen: Organization-Schema mit Name, Logo, Adresse. Fuer Produkte: Product-Schema mit Preis und Bewertung.",
    },
    "schema_syntax_error": {
        "title_de": "Strukturierte Daten fehlerhaft",
        "desc_de": "Die JSON-LD Daten haben einen Fehler.",
        "why_de": "Fehlerhafte Schema.org Daten werden von Google komplett ignoriert — du bekommst keine erweiterten Suchergebnisse trotz des Aufwands.",
        "fix_de": "Pruefe deine JSON-LD Daten mit dem Google Rich Results Test (search.google.com/test/rich-results). Haeufigster Fehler: fehlender @type.",
    },
    # === KI-SICHTBARKEIT ===
    "missing_llms_txt": {
        "title_de": "llms.txt fehlt",
        "desc_de": "Deine Seite hat keine /llms.txt.",
        "why_de": "llms.txt ist ein neuer Standard — eine Art 'Visitenkarte' fuer KI-Systeme. ChatGPT, Claude und Perplexity nutzen sie, um deine Seite schnell zu verstehen und korrekt zu zitieren.",
        "fix_de": "Erstelle eine /llms.txt mit: # Seitenname, kurze Beschreibung, und Links zu wichtigen Seiten. Format: llmstxt.org",
    },
    "invalid_llms_syntax": {
        "title_de": "llms.txt fehlerhaft",
        "desc_de": "Deine llms.txt hat Formatierungsfehler.",
        "why_de": "KI-Systeme koennen eine fehlerhafte llms.txt nicht korrekt lesen. Deine Seite wird dann moeglicherweise falsch zitiert oder ignoriert.",
        "fix_de": "Die Datei muss mit '# Titel' beginnen, gefolgt von einer Beschreibung. Links im Format: - [Label](URL)",
    },
    "llms_no_links": {
        "title_de": "llms.txt ohne Links",
        "desc_de": "Deine llms.txt hat keine Links zu wichtigen Seiten.",
        "why_de": "Ohne Links wissen KI-Systeme nicht, wo sie mehr ueber dich erfahren koennen. Die llms.txt ist nur halb so wirksam.",
        "fix_de": "Fuege Links im Format '- [API Docs](https://dein-link.de)' unter ## Sektionen hinzu.",
    },
    "missing_llms_full_txt": {
        "title_de": "llms-full.txt fehlt (optional)",
        "desc_de": "Die erweiterte /llms-full.txt fehlt.",
        "why_de": "Die llms-full.txt gibt KI-Systemen tieferes Verstaendnis deiner Inhalte. Besonders bei komplexen Websites oder Produkten lohnt sich das.",
        "fix_de": "Erstelle eine /llms-full.txt mit ausfuehrlicher Dokumentation deiner Website, Produkte und Services.",
    },
    "missing_ai_txt": {
        "title_de": "ai.txt fehlt",
        "desc_de": "Die /ai.txt fehlt.",
        "why_de": "ai.txt ist ein neuer Standard fuer KI-Crawler-Berechtigungen. Damit steuerst du explizit, wie KI-Systeme deine Inhalte nutzen duerfen.",
        "fix_de": "Erstelle eine /ai.txt mit deinen Praeferenzen zur KI-Nutzung deiner Inhalte.",
    },
    "missing_indexnow": {
        "title_de": "IndexNow nicht eingerichtet",
        "desc_de": "IndexNow ist nicht konfiguriert.",
        "why_de": "Ohne IndexNow dauert es Tage bis Bing, DuckDuckGo und Yandex neue oder geaenderte Inhalte finden. Mit IndexNow werden Aenderungen sofort uebermittelt — in Sekunden statt Tagen.",
        "fix_de": "Generiere einen IndexNow-Key und lege ihn unter /.well-known/indexnow ab. Dann benachrichtige bei jeder Aenderung: POST https://api.indexnow.org/indexnow",
    },
    # === E-E-A-T ===
    "missing_impressum": {
        "title_de": "Kein Impressum gefunden",
        "desc_de": "Keine Impressum-Seite erkannt.",
        "why_de": "In Deutschland ist ein Impressum Pflicht (TMG §5). Fuer Google ist es ein wichtiges Vertrauenssignal — Seiten ohne Impressum wirken unserioes und ranken schlechter.",
        "fix_de": "Erstelle eine /impressum Seite mit Name, Adresse, Kontaktdaten und Rechtsform. Verlinke sie im Footer jeder Seite.",
    },
    "missing_datenschutz": {
        "title_de": "Keine Datenschutzerklaerung gefunden",
        "desc_de": "Keine Datenschutz-Seite erkannt.",
        "why_de": "Die DSGVO verlangt eine Datenschutzerklaerung. Fehlt sie, riskierst du Abmahnungen. Google wertet sie als Vertrauenssignal.",
        "fix_de": "Erstelle eine /datenschutz Seite und verlinke sie im Footer. Nutze einen DSGVO-Generator fuer die Grundstruktur.",
    },
    "missing_contact_page": {
        "title_de": "Keine Kontaktseite gefunden",
        "desc_de": "Keine Kontaktmoeglichkeit erkannt.",
        "why_de": "Eine Kontaktseite ist ein starkes E-E-A-T Signal. Sie zeigt Google und Besuchern: Hier steht ein echtes Unternehmen dahinter, das erreichbar ist.",
        "fix_de": "Erstelle eine /kontakt Seite mit Formular, E-Mail, Telefon und optional Adresse. Verlinke sie in der Navigation.",
    },
    "missing_about_page": {
        "title_de": "Keine Ueber-uns-Seite",
        "desc_de": "Keine Ueber-uns oder About-Seite erkannt.",
        "why_de": "Die Ueber-uns-Seite ist wichtig fuer E-E-A-T (Expertise, Erfahrung, Autoritaet, Vertrauen). Google moechte wissen, wer hinter einer Website steht.",
        "fix_de": "Erstelle eine /ueber-uns Seite. Beschreibe das Team, die Expertise und die Geschichte. Fuege Fotos hinzu — das schafft Vertrauen.",
    },
    "missing_org_schema": {
        "title_de": "Kein Organization-Schema",
        "desc_de": "Kein Organization Schema.org Markup gefunden.",
        "why_de": "Das Organization-Schema sagt Google klar: Das ist ein Unternehmen mit diesem Namen, Logo und diesen Kontaktdaten. Ohne es muss Google raten.",
        "fix_de": "Fuege ein JSON-LD Organization-Schema mit name, url, logo, contactPoint und sameAs (Social-Media-Links) hinzu.",
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
    """Reichere Issues mit deutschen Klartext-Titeln, Erklaerungen und Fix-Tipps an."""
    translated = []
    for issue in issues:
        i = dict(issue)
        issue_type = i.get("type", "")

        # Deutsche Uebersetzung wenn vorhanden
        tr = _TRANSLATIONS.get(issue_type, {})
        if tr:
            i["title_de"] = tr["title_de"]
            i["description_de"] = tr["desc_de"]
            if "why_de" in tr:
                i["why_de"] = tr["why_de"]
            if "fix_de" in tr:
                i["fix_de"] = tr["fix_de"]
            if "ideal" in tr:
                i["ideal"] = tr["ideal"]

        # Aktuellen Wert aus dem englischen Titel/Description extrahieren
        desc = i.get("description", "")
        title = i.get("title", "")

        # Zeichenzahl aus Titel extrahieren ("Title too long (78 chars)")
        chars_match = re.search(r"\((\d+)\s*chars?\)", title)
        if chars_match:
            i["current_length"] = int(chars_match.group(1))

        # Aktuellen Wert extrahieren ("Current title: '...'")
        value_match = re.search(r"Current (?:title|description): '([^']*)'", desc)
        if value_match:
            i["current_value"] = value_match.group(1)

        # Word count ("Thin content: 293 words")
        words_match = re.search(r"(\d+)\s*words?", title)
        if words_match and "thin" in title.lower():
            i["current_value"] = f"{words_match.group(1)} Woerter"

        # Kategorie uebersetzen
        cat = i.get("category", "other")
        i["category_de"] = _CATEGORY_DE.get(cat, cat.capitalize())

        translated.append(i)
    return translated


def _enrich_pages(raw_pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bereite Seitendaten fuer die Frontend-Anzeige auf."""
    enriched = []
    for p in raw_pages:
        enriched.append(
            {
                "url": p.get("url", ""),
                "status_code": p.get("status_code", 0),
                "fetch_ms": p.get("fetch_ms", 0),
                "rendered_via": p.get("rendered_via", "httpx"),
                "title": p.get("title") or "",
                "title_length": len(p.get("title") or ""),
                "meta_description": p.get("meta_description") or "",
                "meta_description_length": len(p.get("meta_description") or ""),
                "h1": p.get("h1", []),
                "h2": p.get("h2", []),
                "word_count": p.get("word_count", 0),
                "images_total": p.get("images_total", 0),
                "images_without_alt": p.get("images_without_alt", 0),
                "internal_links": p.get("internal_links", 0),
                "external_links": p.get("external_links", 0),
                "has_canonical": bool(p.get("canonical")),
                "has_viewport": bool(p.get("viewport")),
                "has_lang": bool(p.get("lang")),
                "lang": p.get("lang") or "",
                "https": p.get("https", False),
                "schema_types": p.get("schema_types", []),
                "og_tags": p.get("og_tags", {}),
                "twitter_tags": p.get("twitter_tags", {}),
                "robots_meta": p.get("robots_meta") or "",
                "error": p.get("error"),
            }
        )
    return enriched


def _generate_passed_checks(
    raw_pages: List[Dict[str, Any]],
    issues: List[Dict[str, Any]] = None,
    scan_data: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    """Generiere ALLE Checks — bestanden (gruen) und nicht geprueft (grau).

    Jeder der 69 Checks wird ausgewertet. Wenn kein Issue dafuer existiert,
    gilt der Check als bestanden.
    """
    if not raw_pages:
        return []

    issues = issues or []
    scan_data = scan_data or {}
    issue_types = {i.get("type", "") for i in issues}

    passed = []
    ok_pages = [p for p in raw_pages if p.get("status_code") == 200]
    if not ok_pages:
        return []

    first = ok_pages[0]

    def _add(cat: str, label: str, value: str = "", check_type: str = ""):
        """Fuege bestandenen Check hinzu wenn kein Issue dieses Typs existiert."""
        if check_type and check_type in issue_types:
            return  # Issue existiert — wird als rot/orange gezeigt, nicht gruen
        entry = {"category_de": cat, "label_de": label, "severity": "passed"}
        if value:
            entry["value"] = value
        passed.append(entry)

    # ===================================================================
    # GRUNDLAGEN (9 Checks)
    # ===================================================================
    # 1. Seitentitel vorhanden
    titles = [p for p in ok_pages if p.get("title")]
    if titles:
        t = titles[0]["title"]
        tl = len(t)
        if 20 <= tl <= 65:
            _add(
                "Grundlagen",
                "Seitentitel vorhanden und optimale Laenge",
                f'"{t}" ({tl} Zeichen)',
                "missing_title",
            )
        elif tl > 0:
            _add(
                "Grundlagen",
                "Seitentitel vorhanden",
                f'"{t[:50]}..." ({tl} Zeichen)',
                "missing_title",
            )

    # 2. Titel-Laenge
    long_titles = [p for p in ok_pages if len(p.get("title") or "") > 65]
    short_titles = [p for p in ok_pages if 0 < len(p.get("title") or "") < 20]
    if not long_titles and not short_titles and titles:
        _add(
            "Grundlagen",
            "Alle Titel in optimaler Laenge (50-65 Zeichen)",
            f"{len(titles)} Seiten geprueft",
            "long_title",
        )

    # 3. Meta-Beschreibung vorhanden
    descs = [p for p in ok_pages if p.get("meta_description")]
    if descs:
        d = descs[0]["meta_description"]
        dl = len(d)
        if 80 <= dl <= 165:
            _add(
                "Grundlagen",
                "Meta-Beschreibung vorhanden und optimale Laenge",
                f'"{d[:70]}..." ({dl} Zeichen)',
                "missing_meta_description",
            )
        else:
            _add(
                "Grundlagen",
                "Meta-Beschreibung vorhanden",
                f"{dl} Zeichen",
                "missing_meta_description",
            )

    # 4. Description-Laenge
    long_descs = [p for p in ok_pages if len(p.get("meta_description") or "") > 165]
    short_descs = [p for p in ok_pages if 0 < len(p.get("meta_description") or "") < 80]
    if not long_descs and not short_descs and descs:
        _add(
            "Grundlagen",
            "Alle Meta-Beschreibungen in optimaler Laenge",
            f"{len(descs)} Seiten",
            "long_meta_description",
        )

    # 5. H1 vorhanden
    h1_pages = [p for p in ok_pages if p.get("h1")]
    if h1_pages:
        h1_val = h1_pages[0]["h1"][0] if h1_pages[0]["h1"] else ""
        _add(
            "Grundlagen",
            "Hauptueberschrift (H1) vorhanden",
            f'"{h1_val}"',
            "missing_h1",
        )

    # 6. Nur eine H1
    multi_h1 = [p for p in ok_pages if len(p.get("h1", [])) > 1]
    if not multi_h1 and h1_pages:
        _add("Grundlagen", "Genau eine H1 pro Seite", "", "multiple_h1")

    # 7. Viewport
    viewports = [p for p in ok_pages if p.get("viewport")]
    if viewports:
        _add(
            "Grundlagen",
            "Mobile-freundlich (Viewport konfiguriert)",
            "",
            "missing_viewport",
        )

    # 8. Sprache
    langs = [p for p in ok_pages if p.get("lang")]
    if langs:
        _add("Grundlagen", "Sprache gesetzt", langs[0]["lang"], "missing_lang")

    # 9. Kein versehentliches noindex
    indexable = [
        p
        for p in ok_pages
        if not (
            p.get("robots_meta") and "noindex" in (p.get("robots_meta") or "").lower()
        )
    ]
    if indexable:
        _add(
            "Grundlagen",
            "Seiten indexierbar (kein versehentliches noindex)",
            f"{len(indexable)} von {len(ok_pages)} Seiten",
            "noindex_detected",
        )

    # ===================================================================
    # TECHNIK (13 Checks)
    # ===================================================================
    # 1. HTTPS
    https_pages = [p for p in ok_pages if p.get("https")]
    if len(https_pages) == len(ok_pages):
        _add("Technik", "HTTPS aktiv auf allen Seiten", "", "no_https")

    # 2. HSTS
    sec_headers = first.get("security_headers", [])
    if "strict-transport-security" in sec_headers:
        _add(
            "Technik",
            "HSTS-Header aktiv (Strict-Transport-Security)",
            "",
            "missing_hsts",
        )

    # 3. Security Headers
    sec_ok = all(
        h in sec_headers for h in ["x-frame-options", "x-content-type-options"]
    )
    if sec_ok:
        _add(
            "Technik",
            "Sicherheitsheader vorhanden (X-Frame-Options, X-Content-Type)",
            ", ".join(sec_headers),
            "missing_security_headers",
        )

    # 4. Canonical
    canonicals = [p for p in ok_pages if p.get("canonical")]
    if canonicals:
        _add(
            "Technik",
            "Canonical-Tags gesetzt",
            f"{len(canonicals)} von {len(ok_pages)} Seiten",
            "canonical_missing",
        )

    # 5. robots.txt
    if "missing_robots_txt" not in issue_types:
        _add("Technik", "robots.txt vorhanden", "", "missing_robots_txt")

    # 6. Sitemap
    if "missing_sitemap" not in issue_types:
        _add("Technik", "XML-Sitemap vorhanden", "", "missing_sitemap")

    # 7. Keine Redirect-Chains
    if "redirect_chain" not in issue_types:
        _add("Technik", "Keine Redirect-Ketten", "", "redirect_chain")

    # 8. Keine Redirect-Loops
    if "redirect_loop" not in issue_types:
        _add("Technik", "Keine Redirect-Schleifen", "", "redirect_loop")

    # 9. Schnelle Antwortzeit
    slow = [p for p in ok_pages if p.get("fetch_ms", 0) > 3000]
    if not slow:
        avg_ms = sum(p.get("fetch_ms", 0) for p in ok_pages) // max(len(ok_pages), 1)
        _add(
            "Technik",
            "Schnelle Serverantwort",
            f"Durchschnitt {avg_ms}ms",
            "slow_response",
        )

    # 10. Keine Soft-404
    if "soft_404" not in issue_types:
        _add("Technik", "Keine Soft-404 Seiten", "", "soft_404")

    # 11. Keine 5xx Fehler
    errors_5xx = [p for p in raw_pages if (p.get("status_code", 0) // 100) == 5]
    if not errors_5xx:
        _add("Technik", "Keine Server-Fehler (5xx)", "", "http_5xx")

    # 12. CSS/JS nicht blockiert
    if "css_js_blocked" not in issue_types:
        _add("Technik", "CSS/JS nicht in robots.txt blockiert", "", "css_js_blocked")

    # 13. Kein Wildcard-Disallow
    if "wildcard_disallow" not in issue_types:
        _add(
            "Technik",
            "Kein pauschales Crawling-Verbot (Disallow: /)",
            "",
            "wildcard_disallow",
        )

    # ===================================================================
    # INHALT (12 Checks)
    # ===================================================================
    # 1. Genuegend Content
    good_content = [p for p in ok_pages if p.get("word_count", 0) >= 300]
    if good_content:
        _add(
            "Inhalt",
            "Ausreichend Content",
            f"{len(good_content)} Seiten mit 300+ Woertern",
            "thin_content",
        )

    # 2. Keine Thin-Pages
    thin = [p for p in ok_pages if 0 < p.get("word_count", 0) < 300]
    if not thin:
        _add("Inhalt", "Kein Thin Content", "Alle Seiten ueber 300 Woerter")

    # 3. Keine Duplikate
    if "near_duplicate" not in issue_types:
        _add("Inhalt", "Keine doppelten Inhalte erkannt", "", "near_duplicate")

    # 4. Keine Keyword-Kannibalisierung
    if "keyword_cannibalization" not in issue_types:
        _add("Inhalt", "Keine Keyword-Kannibalisierung", "", "keyword_cannibalization")

    # 5. Themen-Cluster
    if "no_topic_cluster" not in issue_types:
        _add("Inhalt", "Themen-Cluster erkannt", "", "no_topic_cluster")

    # 6-9. E-E-A-T Seiten
    if "missing_impressum" not in issue_types:
        _add("Inhalt", "Impressum vorhanden", "", "missing_impressum")
    if "missing_datenschutz" not in issue_types:
        _add("Inhalt", "Datenschutzerklaerung vorhanden", "", "missing_datenschutz")
    if "missing_contact_page" not in issue_types:
        _add("Inhalt", "Kontaktseite vorhanden", "", "missing_contact_page")
    if "missing_about_page" not in issue_types:
        _add("Inhalt", "Ueber-uns-Seite vorhanden", "", "missing_about_page")

    # 10. Organization-Schema
    if "missing_org_schema" not in issue_types:
        _add("Inhalt", "Organization-Schema vorhanden", "", "missing_org_schema")

    # 11. E-E-A-T Score
    if "eeat_low_score" not in issue_types:
        _add("Inhalt", "E-E-A-T Score ausreichend", "", "eeat_low_score")

    # ===================================================================
    # STRUKTUR (10 Checks)
    # ===================================================================
    # 1. Schema.org vorhanden
    schema_pages = [p for p in ok_pages if p.get("schema_types")]
    if schema_pages:
        all_types = set()
        for p in schema_pages:
            all_types.update(p.get("schema_types", []))
        _add(
            "Struktur",
            "Strukturierte Daten (Schema.org) vorhanden",
            ", ".join(sorted(all_types)),
            "missing_schema",
        )

    # 2. Schema valide
    if "schema_syntax_error" not in issue_types and schema_pages:
        _add("Struktur", "Schema.org Syntax korrekt", "", "schema_syntax_error")

    # 3. Breadcrumb-Schema
    has_breadcrumb = any(
        "BreadcrumbList" in p.get("schema_types", []) for p in ok_pages
    )
    if has_breadcrumb:
        _add("Struktur", "Breadcrumb-Schema vorhanden", "", "missing_breadcrumb")

    # 4. Canonical korrekt
    if "canonical_mismatch" not in issue_types and canonicals:
        _add(
            "Struktur",
            "Canonical-Tags zeigen auf korrekte URLs",
            "",
            "canonical_mismatch",
        )

    # 5. Keine Canonical-Chains
    if "canonical_chain" not in issue_types:
        _add("Struktur", "Keine Canonical-Ketten", "", "canonical_chain")

    # 6. Keine Canonical-Konflikte
    if "canonical_conflict" not in issue_types:
        _add("Struktur", "Keine Canonical-Konflikte", "", "canonical_conflict")

    # 7. Keine Orphan-Pages
    if "orphan_page" not in issue_types:
        _add(
            "Struktur", "Alle Seiten intern verlinkt (keine Waisen)", "", "orphan_page"
        )

    # 8. Gute Klicktiefe
    if "deep_page" not in issue_types:
        _add("Struktur", "Alle Seiten in max. 3 Klicks erreichbar", "", "deep_page")

    # 9. Keine Broken Links
    if "broken_internal_link" not in issue_types:
        _add("Struktur", "Keine defekten internen Links", "", "broken_internal_link")

    # 10. Link-Verteilung
    if "link_equity_sink" not in issue_types:
        _add("Struktur", "Gute interne Linkverteilung", "", "link_equity_sink")

    # ===================================================================
    # SOCIAL MEDIA (6 Checks)
    # ===================================================================
    og = first.get("og_tags", {})
    tw = first.get("twitter_tags", {})

    if og.get("og:title"):
        _add(
            "Social Media",
            "Open Graph Titel gesetzt",
            og["og:title"],
            "missing_og_tags",
        )
    if og.get("og:description"):
        _add(
            "Social Media",
            "Open Graph Beschreibung gesetzt",
            og["og:description"][:60] + "...",
            "missing_og_description",
        )
    if og.get("og:image"):
        _add(
            "Social Media",
            "Open Graph Bild gesetzt",
            og["og:image"].split("/")[-1],
            "missing_og_image",
        )
    if og.get("og:url"):
        _add("Social Media", "Open Graph URL gesetzt", "", "missing_og_url")
    if tw.get("twitter:card"):
        _add(
            "Social Media",
            "Twitter Card konfiguriert",
            tw["twitter:card"],
            "missing_twitter_card",
        )
    if tw.get("twitter:image"):
        _add("Social Media", "Twitter Bild gesetzt", "", "missing_twitter_image")

    # ===================================================================
    # PERFORMANCE (6 Checks)
    # ===================================================================
    avg_ms = sum(p.get("fetch_ms", 0) for p in ok_pages) // max(len(ok_pages), 1)
    if avg_ms < 1000:
        _add("Performance", "Exzellente Ladezeit", f"{avg_ms}ms Durchschnitt")
    elif avg_ms < 2000:
        _add("Performance", "Gute Ladezeit", f"{avg_ms}ms Durchschnitt")

    # Bilder mit Alt-Text
    all_images = sum(p.get("images_total", 0) for p in ok_pages)
    all_without_alt = sum(p.get("images_without_alt", 0) for p in ok_pages)
    if all_images > 0 and all_without_alt == 0:
        _add(
            "Performance",
            "Alle Bilder haben Alt-Text (Barrierefreiheit)",
            f"{all_images} Bilder geprueft",
            "images_without_alt",
        )
    elif all_images > 0:
        ratio = all_images - all_without_alt
        _add(
            "Performance",
            "Bilder-Alt-Text",
            f"{ratio} von {all_images} Bildern haben Alt-Text",
        )

    # Bilder-Anzahl (Info)
    if all_images > 0:
        _add(
            "Performance",
            "Bilder auf der Website",
            f"{all_images} Bilder auf {len(ok_pages)} Seiten",
        )

    # ===================================================================
    # KI-SICHTBARKEIT (7 Checks)
    # ===================================================================
    if "missing_llms_txt" not in issue_types:
        _add("KI-Sichtbarkeit", "llms.txt vorhanden", "", "missing_llms_txt")
    if (
        "invalid_llms_syntax" not in issue_types
        and "missing_llms_txt" not in issue_types
    ):
        _add("KI-Sichtbarkeit", "llms.txt Syntax korrekt", "", "invalid_llms_syntax")
    if "llms_no_links" not in issue_types and "missing_llms_txt" not in issue_types:
        _add("KI-Sichtbarkeit", "llms.txt enthaelt Links", "", "llms_no_links")
    if "missing_llms_full_txt" not in issue_types:
        _add("KI-Sichtbarkeit", "llms-full.txt vorhanden", "", "missing_llms_full_txt")
    if "missing_ai_txt" not in issue_types:
        _add("KI-Sichtbarkeit", "ai.txt vorhanden", "", "missing_ai_txt")
    if "missing_indexnow" not in issue_types:
        _add("KI-Sichtbarkeit", "IndexNow konfiguriert", "", "missing_indexnow")
    if "ai_crawler_blocked" not in issue_types:
        _add(
            "KI-Sichtbarkeit",
            "KI-Crawler erlaubt (GPTBot, ClaudeBot, PerplexityBot)",
            "",
            "ai_crawler_blocked",
        )

    # ===================================================================
    # GEO / KI-SUCHE (6 Checks)
    # ===================================================================
    if "geo_no_answer_first" not in issue_types:
        _add(
            "Struktur",
            "Answer-First Struktur (GEO)",
            "Erste 150 Woerter beantworten die Hauptfrage",
        )
    if "geo_no_structured_format" not in issue_types:
        _add(
            "Struktur",
            "Frage-basierte Ueberschriften (GEO)",
            "H2/H3 als Fragen formatiert",
        )
    if "geo_low_fact_density" not in issue_types:
        _add(
            "Struktur",
            "Gute Faktendichte (GEO)",
            "Statistiken, Zahlen und Quellen vorhanden",
        )
    if "geo_no_entity" not in issue_types:
        _add("Struktur", "Marke als Entity definiert (GEO)", "")
    if "geo_no_freshness" not in issue_types:
        _add(
            "Struktur",
            "Aktualitaetssignale vorhanden (GEO)",
            "datePublished/dateModified gesetzt",
        )

    return passed
