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

import logging
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.config import settings
from ..core.event_bus import EventType
from .base import Agent, AgentResult, AgentStatus

logger = logging.getLogger(__name__)

MAX_CLAUDE_CALLS = 8  # cap per audit to control cost
CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_TIMEOUT = 30.0


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
            fixable = fixable[:MAX_CLAUDE_CALLS]

            claude_client = _get_claude_client()
            fixes: List[Dict[str, Any]] = []

            for issue in fixable:
                fix = None
                if claude_client is not None:
                    try:
                        fix = await _claude_fix(claude_client, issue, name, domain)
                    except Exception as exc:
                        logger.warning(f"Claude call failed, using template: {exc}")
                        fix = None
                if fix is None:
                    fix = _template_fix(issue, name, domain)
                if fix:
                    fixes.append(fix)

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
    "missing_title",
    "short_title",
    "long_title",
    "missing_meta_description",
    "short_meta_description",
    "long_meta_description",
    "missing_h1",
    "low_ctr_opportunity",
    "striking_distance",
    "missing_organization_schema",
    "missing_og_image",
}


def _get_claude_client():
    if not settings.CLAUDE_API_KEY:
        return None
    try:
        import anthropic  # noqa

        return anthropic.Anthropic(
            api_key=settings.CLAUDE_API_KEY, timeout=CLAUDE_TIMEOUT
        )
    except ImportError:
        logger.warning("anthropic package not installed")
        return None


async def _claude_fix(
    client, issue: Dict[str, Any], name: str, domain: str
) -> Optional[Dict[str, Any]]:
    """Call Claude synchronously (client is sync). Wrap in run_in_executor for true async."""
    import asyncio

    prompt = _build_prompt(issue, name, domain)

    def _call():
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text if msg.content else ""

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

    if t in ("missing_title", "short_title"):
        suggestion = f"{name} | {url.rstrip('/').split('/')[-1].replace('-', ' ').title() or 'Home'}"
    elif t == "long_title":
        suggestion = f"Shorten to: {name} | {issue.get('affected_url', '').rstrip('/').split('/')[-1]}"
    elif t in ("missing_meta_description", "short_meta_description"):
        suggestion = (
            f"{name} bietet KI-Business-Systeme für moderne Unternehmen. "
            f"ERP, CRM, Automation aus einer Hand. Jetzt kostenlos informieren."
        )[:160]
    elif t == "long_meta_description":
        suggestion = "Shorten current description to 140-160 characters."
    elif t == "missing_h1":
        suggestion = f"{name} — KI-Business-Systeme"
    elif t == "missing_organization_schema":
        return _generic_organization_schema(name, domain)
    elif t == "missing_og_image":
        suggestion = (
            f'Add <meta property="og:image" content="{domain}/og-default.png">. '
            f"Create a 1200x630 image with the {name} logo and a one-line value prop."
        )
    elif t == "low_ctr_opportunity":
        kw = issue.get("keyword", "")
        suggestion = (
            f"Title: {kw.title()} | {name}\n"
            f"Description: Alles über {kw} bei {name}. Kostenlose Erstberatung, 20+ Jahre Erfahrung."
        )
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
