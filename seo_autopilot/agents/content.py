"""
ContentAgent: generates concrete fix snippets for each high-priority issue.

Uses Claude API when CLAUDE_API_KEY is set; falls back to deterministic
templates when the API is unavailable. Produces ready-to-paste fixes:
- Optimized <title>
- Meta description
- JSON-LD Organization snippet
- Alt text suggestions
- Security header nginx block
"""

from __future__ import annotations

import hashlib
import logging
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.config import settings
from .. import handwerker as hw
from ..core.event_bus import EventType
from .base import Agent, AgentResult, AgentStatus

logger = logging.getLogger(__name__)

MAX_CLAUDE_CALLS = 40  # Deckel je Audit (Kosten); kurze Prompts, kleine Antworten
CLAUDE_MODEL = settings.CLAUDE_MODEL
CLAUDE_TIMEOUT = 180.0  # CLI-Start + Antwort


class ContentAgent(Agent):
    @property
    def name(self) -> str:
        return "content"

    @property
    def event_type(self) -> EventType:
        return EventType.CONTENT_GENERATION_COMPLETED

    async def run(self) -> AgentResult:
        start_time = datetime.utcnow()
        result = AgentResult(
            status=AgentStatus.RUNNING,
            agent_name=self.name,
            project_id=self.project_id,
            audit_id=self.audit_id,
        )

        try:
            await self.emit_started()

            issues = list(self.context.all_issues) if self.context else []
            domain = self.project_config.domain
            name = self.project_config.name

            # Shortlist: take top-priority issues where a concrete fix helps
            fixable = [i for i in issues if i.get("type") in _FIXABLE_TYPES]

            adapter_cfg = getattr(self.project_config, "adapter_config", None) or {}
            root = _adapter_root(adapter_cfg)
            regeln = adapter_cfg.get("seo_regeln") or {}
            standard_og = adapter_cfg.get("standard_og_bild")

            claude_client = _get_claude_client()
            fixes: List[Dict[str, Any]] = []
            ki_aufrufe = 0
            verworfen = 0
            kontexte: Dict[str, Optional[Dict[str, Any]]] = {}

            for issue in fixable:
                typ = issue.get("type", "")
                seite = issue.get("affected_url") or ""
                if seite and seite not in kontexte:
                    kontexte[seite] = hw.seiten_kontext(seite, root)
                kontext = kontexte.get(seite)

                # 1) Regel-Fixes: deterministisch aus vorhandenen Daten, keine KI noetig
                fix = _regel_fix(issue, kontext, name, domain, standard_og)

                # 2) KI-Fixes mit Seitenkontext (Alt-Texte per Bild)
                if (
                    fix is None
                    and claude_client is not None
                    and ki_aufrufe < MAX_CLAUDE_CALLS
                ):
                    try:
                        if typ == "images_without_alt":
                            fix, n = await _alt_texte_fix(
                                claude_client,
                                issue,
                                kontext,
                                root,
                                name,
                                domain,
                                regeln,
                            )
                            ki_aufrufe += n
                        elif kontext is not None or typ in (
                            "low_ctr_opportunity",
                            "striking_distance",
                        ):
                            fix = await _claude_fix(
                                claude_client,
                                issue,
                                name,
                                domain,
                                kontext or {},
                                regeln,
                            )
                            ki_aufrufe += 1
                    except Exception as exc:
                        logger.warning(f"Claude call failed, using template: {exc}")
                        fix = None

                # 3) Vorlage — nur als Vorschlag, laeuft nie automatisch
                if fix is None:
                    fix = _template_fix(issue, name, domain)
                if fix is None:
                    continue
                fix.setdefault("seite", seite)

                # Plausibilitaet: im Zweifel NICHT schreiben
                if fix.get("source") == hw.QUELLE_KI and typ in _TEXT_TYPEN:
                    ok, grund = hw.plausibel(typ, fix.get("suggestion", ""), regeln)
                    if not ok:
                        verworfen += 1
                        logger.info(
                            f"[content] Vorschlag verworfen ({typ} {seite}): {grund}"
                        )
                        continue
                    fix["suggestion"] = hw.saeubern(fix["suggestion"])
                fixes.append(fix)
            result.metrics["fixes_verworfen"] = verworfen
            result.metrics["ki_aufrufe"] = ki_aufrufe
            if isinstance(claude_client, _GemerkterClient):
                gemerkt = claude_client.messages
                result.metrics["ki_gemerkt"] = gemerkt.treffer
                logger.info(
                    f"[content] KI: {gemerkt.neu} neu, {gemerkt.treffer} aus dem Gedaechtnis"
                )

            # Always produce a generic Organization schema snippet + security headers block
            fixes.append(_generic_organization_schema(name, domain))
            fixes.append(_generic_security_headers_nginx())

            result.fixes = fixes
            result.metrics.update(
                {
                    "claude_enabled": claude_client is not None,
                    "fixes_generated": len(fixes),
                    "fixes_from_ai": sum(
                        1 for f in fixes if f.get("source") == "claude"
                    ),
                    "fixes_from_template": sum(
                        1 for f in fixes if f.get("source") == "template"
                    ),
                    "fixes_from_rules": sum(
                        1 for f in fixes if f.get("source") == hw.QUELLE_REGEL
                    ),
                }
            )
            result.status = AgentStatus.COMPLETED
            result.log_output = (
                f"Generated {len(fixes)} fixes "
                f"({'Claude' if claude_client else 'template'} mode)"
            )
            logger.info(result.log_output)

        except Exception as exc:  # pragma: no cover
            result.status = AgentStatus.FAILED
            result.errors.append(str(exc))
            result.log_output = f"Content agent failed: {exc}"
            logger.exception("Content agent error")

        finally:
            result.duration_seconds = (datetime.utcnow() - start_time).total_seconds()
            await self.emit_result(result)

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FIXABLE_TYPES = {
    # Klassische Meta-Tags (aus dem Crawler-Analyzer)
    "missing_title",
    "short_title",
    "long_title",
    "missing_meta_description",
    "short_meta_description",
    "long_meta_description",
    "missing_h1",
    "missing_canonical",
    "canonical_missing",
    "missing_og_image",
    "missing_organization_schema",
    "low_ctr_opportunity",
    "striking_distance",
    # Welle 2.5: realistische Issue-Types vom modernen Analyzer
    "org_schema_no_sameas",  # Org-Schema vorhanden, sameAs fehlt
    "missing_robots_txt",
    "missing_sitemap_xml",
    "sitemap_no_lastmod",
    "missing_security_headers",  # Nginx-Snippet
    "missing_contact_page",  # Generates suggestion-Text fuer page
    "missing_about_page",
    # Handwerker (v1.13): Meta-Ebene und Auszeichnung je Seite
    "missing_og_title",
    "missing_twitter_card",
    "images_without_alt",
    "image_missing_dimensions",
    "no_jsonld",
}

# Typen, deren Vorschlag freier Text ist und deshalb durch die
# Plausibilitaetspruefung muss, bevor er in eine Datei darf.
_TEXT_TYPEN = {
    "missing_title",
    "short_title",
    "long_title",
    "missing_meta_description",
    "short_meta_description",
    "long_meta_description",
    "missing_og_title",
    "missing_h1",
}


def _adapter_root(adapter_cfg: Dict[str, Any]):
    from pathlib import Path

    root = adapter_cfg.get("root_path")
    if not root:
        return None
    p = Path(root)
    return p if p.exists() else None


def _regel_fix(issue, kontext, name, domain, standard_og):
    """Deterministische Fixes ohne KI — nur aus Daten, die schon da sind."""
    typ = issue.get("type", "")
    seite = issue.get("affected_url") or domain
    basis = {
        "source": hw.QUELLE_REGEL,
        "type": typ,
        "seite": seite,
        "url": seite,
        "issue_title": issue.get("title"),
        "priority": issue.get("severity", "low"),
    }
    if typ == "missing_twitter_card":
        return {
            **basis,
            "suggestion": "twitter:card summary_large_image + Titel/Beschreibung/Bild aus og:*",
        }
    if typ == "image_missing_dimensions":
        return {**basis, "suggestion": "width/height aus den lokalen Bilddateien"}
    if typ == "no_jsonld":
        return {
            **basis,
            "suggestion": "WebPage-JSON-LD mit datePublished/dateModified",
            "publisher": name,
            "domain": domain,
        }
    if typ == "missing_og_image":
        # Kein geratener Pfad mehr: nur ein konfiguriertes Standardbild oder nichts
        if not standard_og:
            return None
        return {**basis, "url": standard_og, "suggestion": standard_og}
    return None


async def _alt_texte_fix(client, issue, kontext, root, name, domain, regeln):
    """Alt-Texte per Bild-KI. Rueckgabe (fix|None, Anzahl KI-Aufrufe)."""
    import asyncio

    seite = issue.get("affected_url") or ""
    if kontext is None or root is None:
        return None, 0
    offen = [
        b for b in kontext.get("bilder", []) if b["alt"] is None and not b["dekorativ"]
    ]
    if not offen:
        return None, 0
    system = hw.system_prompt(name, domain, regeln, kontext.get("lang", "de"))
    bilder: Dict[str, str] = {}
    aufrufe = 0
    for b in offen[:MAX_ALT_JE_SEITE]:
        datei = hw.lokales_bild(root, seite, b["src"])
        if datei is None:
            continue
        payload = hw.bild_fuer_ki(datei)
        if payload is None:
            continue
        media_type, daten = payload

        def _call():
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=200,
                system=system,
                extra_body={"output_config": {"effort": "low"}},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": daten,
                                },
                            },
                            {
                                "type": "text",
                                "text": hw.alt_text_prompt(kontext, b["src"]),
                            },
                        ],
                    }
                ],
            )
            return _text_aus(msg)

        aufrufe += 1
        text = hw.saeubern(await asyncio.get_event_loop().run_in_executor(None, _call))
        ok, grund = hw.plausibel("images_without_alt", text, regeln)
        if ok:
            bilder[b["src"]] = text
        else:
            logger.info(f"[content] Alt-Text verworfen ({b['src']}): {grund}")
    if not bilder:
        return None, aufrufe
    return {
        "source": hw.QUELLE_KI,
        "type": "images_without_alt",
        "seite": seite,
        "url": seite,
        "issue_title": issue.get("title"),
        "suggestion": "; ".join(f"{k} → {v}" for k, v in bilder.items()),
        "bilder": bilder,
        "priority": issue.get("severity", "low"),
    }, aufrufe


MAX_ALT_JE_SEITE = 8


def _text_aus(msg) -> str:
    if getattr(msg, "stop_reason", None) == "refusal":
        return ""
    return "".join(
        getattr(b, "text", "")
        for b in (msg.content or [])
        if getattr(b, "type", "") == "text"
    )


def _get_claude_client():
    # Seit 16.09.2026 ueber Roberts Max-Abo (abo_ki), nie mehr ueber den bezahlten API-Key
    from .. import abo_ki

    if not abo_ki.verfuegbar():
        logger.warning("[content] Abo-Zugang fehlt - Vorlagen statt KI")
        return None
    return _GemerkterClient(abo_ki.AboClient(zeitlimit=CLAUDE_TIMEOUT))


# --- Gemerkte KI-Antworten (25.09.2026) ---------------------------------------
# Ohne Auto-Fix bleiben dieselben Befunde taeglich offen, und die KI schrieb jeden
# Tag dieselben Vorschlaege neu (ein Projekt: 31 Opus-Aufrufe am Tag). Identische
# Anfrage (Modell, Systemprompt, Seitentext, Befund, Bild) -> gemerkte Antwort.
# Aendert sich die Seite, aendert sich die Anfrage und die KI schreibt neu.
KI_CACHE_DATEI = Path(
    os.environ.get(
        "SEO_KI_CACHE", "/var/lib/tentacl/seo-autopilot/ki_cache_content.json"
    )
)
KI_CACHE_TAGE = 14  # danach wird auch bei unveraenderter Seite neu geschrieben


class _GemerkteAntwort:
    stop_reason = "end_turn"

    def __init__(self, text: str):
        block = type("Block", (), {"type": "text", "text": text})()
        self.content = [block]


def _cache_laden() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(KI_CACHE_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_speichern(speicher: Dict[str, Dict[str, Any]]) -> None:
    grenze = time.time() - KI_CACHE_TAGE * 86400
    frisch = {k: v for k, v in speicher.items() if v.get("zeit", 0) >= grenze}
    try:
        KI_CACHE_DATEI.parent.mkdir(parents=True, exist_ok=True)
        tmp = KI_CACHE_DATEI.with_suffix(".tmp")
        tmp.write_text(json.dumps(frisch, ensure_ascii=False), encoding="utf-8")
        tmp.replace(KI_CACHE_DATEI)
    except OSError as exc:
        logger.warning(f"[content] KI-Cache nicht gespeichert: {exc}")


class _GemerkteNachrichten:
    def __init__(self, client):
        self._client = client
        self._speicher = _cache_laden()
        self.treffer = 0
        self.neu = 0

    def create(self, **anfrage):
        roh = json.dumps(anfrage, sort_keys=True, ensure_ascii=False, default=str)
        schluessel = hashlib.sha256(roh.encode("utf-8")).hexdigest()
        eintrag = self._speicher.get(schluessel)
        if eintrag and time.time() - eintrag.get("zeit", 0) < KI_CACHE_TAGE * 86400:
            self.treffer += 1
            return _GemerkteAntwort(eintrag["text"])
        antwort = self._client.messages.create(**anfrage)
        self.neu += 1
        text = _text_aus(antwort)
        if text.strip():
            self._speicher[schluessel] = {"text": text, "zeit": time.time()}
            _cache_speichern(self._speicher)
        return antwort


class _GemerkterClient:
    """Wie AboClient (client.messages.create), merkt sich aber Antworten."""

    def __init__(self, client):
        self.messages = _GemerkteNachrichten(client)


async def _claude_fix(
    client,
    issue: Dict[str, Any],
    name: str,
    domain: str,
    kontext: Optional[Dict[str, Any]] = None,
    regeln: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Call Claude synchronously (client is sync). Wrap in run_in_executor for true async.

    Mit Seitenkontext (Titel, H1, Text) schreibt die KI passende Texte; ohne
    Kontext bleibt der alte, knappe Prompt.
    """
    import asyncio

    kontext = dict(kontext or {})
    kontext.setdefault("seite", issue.get("affected_url") or domain)
    system = hw.system_prompt(name, domain, regeln, kontext.get("lang", "de"))
    prompt = (
        hw.auftrag(issue.get("type", ""), kontext, issue)
        if kontext.get("text") or kontext.get("title")
        else _build_prompt(issue, name, domain)
    )

    def _call():
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            system=system,
            extra_body={"output_config": {"effort": "low"}},
            messages=[{"role": "user", "content": prompt}],
        )
        return _text_aus(msg)

    try:
        text = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception:
        raise

    return {
        "source": "claude",
        "type": issue.get("type"),
        "url": issue.get("affected_url") or issue.get("keyword") or domain,
        "issue_title": issue.get("title"),
        "suggestion": text.strip(),
        "priority": issue.get("severity", "medium"),
    }


def _build_prompt(issue: Dict[str, Any], name: str, domain: str) -> str:
    """Build a Claude API prompt for the given issue type.

    NOTE: All prompt strings are intentionally in German because they generate
    German SEO content for German-language websites.
    """
    itype = issue.get("type", "")
    if itype in ("missing_title", "short_title", "long_title"):
        # Prompt: generate an optimal HTML <title> in German
        return (
            f"Schreibe einen optimalen HTML <title> (50-60 Zeichen, deutsch) für die Seite "
            f"{issue.get('affected_url')} der Marke '{name}' ({domain}). "
            f"Kontext: {issue.get('description', '')}. "
            f"Gib NUR den Titel-Text aus, keine Erklärung, keine Anführungszeichen."
        )
    if itype in (
        "missing_meta_description",
        "short_meta_description",
        "long_meta_description",
    ):
        # Prompt: generate an optimal meta description in German
        return (
            f"Schreibe eine optimale Meta-Description (140-160 Zeichen, deutsch) für "
            f"{issue.get('affected_url')} von '{name}'. "
            f"Enthalte Call-to-Action. Gib NUR den reinen Text aus."
        )
    if itype == "missing_h1":
        # Prompt: suggest an H1 heading in German
        return (
            f"Schlage einen H1-Text (3-8 Wörter, deutsch) für {issue.get('affected_url')} "
            f"von '{name}' vor. Nur der H1-Text, nichts anderes."
        )
    if itype == "low_ctr_opportunity":
        # Prompt: suggest new title + meta description to improve CTR (German)
        return (
            f"Für den Suchbegriff '{issue.get('keyword')}' ranked '{name}' ({domain}) "
            f"auf Position {issue.get('position')} mit CTR {issue.get('ctr')}%. "
            f"Schlage einen neuen Page-Title UND eine neue Meta-Description vor die die CTR erhöhen. "
            f"Format:\nTITLE: ...\nDESC: ..."
        )
    if itype == "striking_distance":
        # Prompt: list 5 on-page SEO measures to reach page 1 (German)
        return (
            f"Die Seite rankt für '{issue.get('keyword')}' auf Position {issue.get('position')}. "
            f"Liste 5 konkrete on-page SEO-Maßnahmen für '{name}' auf um auf Seite 1 zu kommen. "
            f"Kurz und konkret, nummeriert."
        )
    if itype == "missing_organization_schema":
        # Prompt: generate a complete Organization JSON-LD block (German)
        return (
            f"Erzeuge einen vollständigen schema.org Organization JSON-LD Block für "
            f"'{name}' ({domain}). Gib nur den JSON-Block aus."
        )
    # Fallback prompt: short actionable recommendation in German
    return (
        f"SEO-Problem: {issue.get('title', '')} — {issue.get('description', '')}. "
        f"Marke: {name}, URL: {issue.get('affected_url', '')}. "
        f"Gib eine kurze, umsetzbare Empfehlung in maximal 3 S\u00e4tzen."
    )


def _template_fix(
    issue: Dict[str, Any], name: str, domain: str
) -> Optional[Dict[str, Any]]:
    """Deterministic fallback when Claude is not available."""
    t = issue.get("type", "")
    url = issue.get("affected_url") or domain
    suggestion: Optional[str] = None

    # Vorlagen fuer Text (Titel, Beschreibung, H1) gibt es seit v1.13 nicht mehr:
    # Ohne Seitenkontext ist jeder geratene Text schlechter als der alte.
    if t in (
        "missing_title",
        "short_title",
        "long_title",
        "missing_meta_description",
        "short_meta_description",
        "long_meta_description",
        "missing_h1",
        "missing_og_title",
        "images_without_alt",
        "image_missing_dimensions",
        "no_jsonld",
        "missing_twitter_card",
    ):
        return None
    if t in ("missing_canonical", "canonical_missing"):
        # Adapter erwartet die kanonische URL als 'url' Feld
        return {
            "source": "template",
            "type": "canonical_missing",
            "url": url.split("#")[0].split("?")[0],
            "issue_title": issue.get("title"),
            "suggestion": url,
            "priority": issue.get("severity", "medium"),
        }
    elif t == "missing_organization_schema":
        return _generic_organization_schema(name, domain)
    elif t == "org_schema_no_sameas":
        # Erweitertes Schema mit sameAs-Link auf About-Page
        schema = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": name,
            "url": domain,
            "logo": f"{domain.rstrip('/')}/icon-512.png",
            "sameAs": [f"{domain.rstrip('/')}/impressum"],
        }
        return {
            "source": "template",
            "type": "missing_organization_schema",  # adapter applies as schema_block
            "url": domain,
            "issue_title": issue.get("title"),
            "suggestion": json.dumps(schema, indent=2, ensure_ascii=False),
            "priority": issue.get("severity", "medium"),
        }
    elif t == "missing_og_image":
        return None  # geratene Bildpfade zeigen ins Leere; nur konfiguriertes Standardbild (Regel-Fix)
    elif t == "missing_robots_txt":
        suggestion = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "\n"
            f"Sitemap: {domain.rstrip('/')}/sitemap.xml\n"
        )
    elif t == "missing_sitemap_xml":
        # Minimal-Sitemap mit Homepage
        suggestion = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"  <url><loc>{domain}</loc><priority>1.0</priority></url>\n"
            "</urlset>\n"
        )
    elif t == "sitemap_no_lastmod":
        suggestion = (
            "Add <lastmod>YYYY-MM-DD</lastmod> to each <url> entry in sitemap.xml."
        )
    elif t == "missing_security_headers":
        return _generic_security_headers_nginx()
    elif t in ("missing_contact_page", "missing_about_page"):
        page = "Kontakt" if "contact" in t else "Ueber uns"
        suggestion = (
            f"Erstelle eine {page}-Page unter {domain.rstrip('/')}/{('kontakt' if 'contact' in t else 'about')} "
            f"mit Adresse, Email-Kontakt und 1-2 Absaetzen ueber {name}. "
            f"Verlinke sie aus der Hauptnavigation."
        )
    elif t == "low_ctr_opportunity":
        return None  # erfand frueher "20+ Jahre Erfahrung" — nie wieder ohne KI und Kontext
    else:
        return None

    return {
        "source": "template",
        "type": t,
        "url": url,
        "issue_title": issue.get("title"),
        "suggestion": suggestion,
        "priority": issue.get("severity", "medium"),
    }


def _generic_organization_schema(name: str, domain: str) -> Dict[str, Any]:
    schema = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": name,
        "url": domain,
        "logo": f"{domain.rstrip('/')}/logo.png",
        "sameAs": [
            f"{domain.rstrip('/')}/about",
        ],
    }
    return {
        "source": "template",
        "type": "missing_organization_schema",
        "url": domain,
        "issue_title": "Organization schema snippet",
        "suggestion": json.dumps(schema, indent=2, ensure_ascii=False),
        "priority": "medium",
    }


def _generic_security_headers_nginx() -> Dict[str, Any]:
    snippet = """# /etc/nginx/snippets/security-headers.conf
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
"""
    return {
        "source": "template",
        "type": "missing_security_headers",
        "url": "nginx config",
        "issue_title": "Security headers snippet",
        "suggestion": snippet,
        "priority": "medium",
    }
