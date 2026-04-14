"""
LLMs.txt + AI.txt + IndexNow Audit — Phase 11.

Detects:
- llms.txt: missing, invalid syntax, missing sections
- llms-full.txt: missing (optional recommendation)
- ai.txt: missing (emerging standard)
- IndexNow: missing key file (Bing/Yandex instant indexing)

Spec: https://llmstxt.org/
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Markdown link pattern: - [Label](URL)
LINK_PATTERN = re.compile(r"^-\s+\[([^\]]+)\]\(([^)]+)\)\s*$")

# H1/H2 header patterns
H1_PATTERN = re.compile(r"^#\s+(.+)$", re.MULTILINE)
H2_PATTERN = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@dataclass
class LlmsTxtResult:
    """Parsed llms.txt data."""

    url: str = ""
    exists: bool = False
    status_code: int = 0
    raw: str = ""
    has_title: bool = False
    title: str = ""
    description: str = ""
    sections: List[str] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)


@dataclass
class AiTxtResult:
    """Parsed ai.txt data."""

    url: str = ""
    exists: bool = False
    status_code: int = 0
    raw: str = ""


@dataclass
class IndexNowResult:
    """IndexNow key detection."""

    exists: bool = False
    key_url: str = ""
    status_code: int = 0


class LlmsAiTxtAuditor:
    """Audits llms.txt, ai.txt and IndexNow for AI search visibility."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    async def fetch_llms_txt(
        self, domain: str, client: Optional[httpx.AsyncClient] = None
    ) -> LlmsTxtResult:
        """Fetch and parse /llms.txt."""
        url = f"{domain.rstrip('/')}/llms.txt"
        result = LlmsTxtResult(url=url)
        own_client = client is None

        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)

        try:
            resp = await client.get(url)
            result.status_code = resp.status_code
            if resp.status_code == 200:
                result.exists = True
                result.raw = resp.text
                self._parse_llms_txt(result)
        except Exception as exc:
            logger.warning(f"[llms_txt] fetch failed: {exc}")
        finally:
            if own_client:
                await client.aclose()

        return result

    async def fetch_llms_full_txt(
        self, domain: str, client: Optional[httpx.AsyncClient] = None
    ) -> LlmsTxtResult:
        """Fetch /llms-full.txt (optional extended version)."""
        url = f"{domain.rstrip('/')}/llms-full.txt"
        result = LlmsTxtResult(url=url)
        own_client = client is None

        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)

        try:
            resp = await client.get(url)
            result.status_code = resp.status_code
            if resp.status_code == 200:
                result.exists = True
                result.raw = resp.text
        except Exception as exc:
            logger.warning(f"[llms_full_txt] fetch failed: {exc}")
        finally:
            if own_client:
                await client.aclose()

        return result

    async def fetch_ai_txt(
        self, domain: str, client: Optional[httpx.AsyncClient] = None
    ) -> AiTxtResult:
        """Fetch /ai.txt."""
        url = f"{domain.rstrip('/')}/ai.txt"
        result = AiTxtResult(url=url)
        own_client = client is None

        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)

        try:
            resp = await client.get(url)
            result.status_code = resp.status_code
            if resp.status_code == 200:
                result.exists = True
                result.raw = resp.text
        except Exception as exc:
            logger.warning(f"[ai_txt] fetch failed: {exc}")
        finally:
            if own_client:
                await client.aclose()

        return result

    async def check_indexnow(
        self, domain: str, client: Optional[httpx.AsyncClient] = None
    ) -> IndexNowResult:
        """Check for IndexNow key at common locations."""
        result = IndexNowResult()
        own_client = client is None

        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)

        try:
            # Check /.well-known/indexnow
            url = f"{domain.rstrip('/')}/.well-known/indexnow"
            resp = await client.get(url)
            if resp.status_code == 200 and resp.text.strip():
                result.exists = True
                result.key_url = url
                result.status_code = 200
                return result

            # Check for key file referenced in HTML meta or common patterns
            # IndexNow keys are typically 32-char hex at /{key}.txt
            # We can't guess the key, but we can check the well-known path
            result.status_code = resp.status_code
        except Exception as exc:
            logger.warning(f"[indexnow] check failed: {exc}")
        finally:
            if own_client:
                await client.aclose()

        return result

    def _parse_llms_txt(self, result: LlmsTxtResult) -> None:
        """Parse llms.txt content and validate against spec.

        Spec (https://llmstxt.org/):
        - Must start with # Title (H1)
        - Optional description paragraph after title
        - Optional ## Sections with markdown links
        - Links in format: - [Label](URL)
        """
        lines = result.raw.strip().splitlines()
        if not lines:
            result.parse_errors.append("File is empty")
            return

        # Check for H1 title
        h1_match = H1_PATTERN.match(lines[0].strip())
        if h1_match:
            result.has_title = True
            result.title = h1_match.group(1).strip()
        else:
            result.parse_errors.append(
                "llms.txt must start with '# Title' (H1 heading)"
            )

        # Extract sections (H2 headers)
        for line in lines:
            h2_match = H2_PATTERN.match(line.strip())
            if h2_match:
                result.sections.append(h2_match.group(1).strip())

        # Extract description (text between H1 and first H2 or link)
        desc_lines = []
        in_desc = False
        for line in lines[1:]:
            stripped = line.strip()
            if H2_PATTERN.match(stripped) or LINK_PATTERN.match(stripped):
                break
            if stripped:
                in_desc = True
                desc_lines.append(stripped)
            elif in_desc:
                break
        result.description = " ".join(desc_lines)

        # Extract links
        for line in lines:
            link_match = LINK_PATTERN.match(line.strip())
            if link_match:
                result.links.append(
                    {"label": link_match.group(1), "url": link_match.group(2)}
                )

    def detect_issues(
        self,
        llms: LlmsTxtResult,
        llms_full: LlmsTxtResult,
        ai: AiTxtResult,
        indexnow: IndexNowResult,
    ) -> List[Dict[str, Any]]:
        """Detect all LLMs.txt, AI.txt and IndexNow issues."""
        issues: List[Dict[str, Any]] = []

        # --- llms.txt ---
        if not llms.exists:
            issues.append(
                _issue(
                    "llms_ai",
                    "missing_llms_txt",
                    "medium",
                    "Missing llms.txt",
                    "No /llms.txt found. This file helps AI systems (ChatGPT, Claude, "
                    "Perplexity) understand your site's purpose, content and structure.",
                    "Create a /llms.txt following the spec at llmstxt.org. "
                    "Start with '# Site Name', add a description, and link to key pages.",
                )
            )
        else:
            # Validate syntax
            if llms.parse_errors:
                issues.append(
                    _issue(
                        "llms_ai",
                        "invalid_llms_syntax",
                        "high",
                        f"llms.txt syntax errors: {'; '.join(llms.parse_errors)}",
                        "llms.txt exists but has formatting issues that may prevent "
                        "AI systems from parsing it correctly.",
                        "Fix the syntax: file must start with '# Title', "
                        "followed by description and ## sections with markdown links.",
                    )
                )

            # No links = limited value
            if llms.exists and not llms.parse_errors and not llms.links:
                issues.append(
                    _issue(
                        "llms_ai",
                        "llms_no_links",
                        "medium",
                        "llms.txt has no resource links",
                        "llms.txt exists and is valid but contains no links to docs, "
                        "API references, or key pages. AI systems won't discover your content.",
                        "Add links in '- [Label](URL)' format under ## sections.",
                    )
                )

        # --- llms-full.txt ---
        if not llms_full.exists:
            issues.append(
                _issue(
                    "llms_ai",
                    "missing_llms_full_txt",
                    "low",
                    "Missing llms-full.txt (optional)",
                    "/llms-full.txt provides extended context for AI systems. "
                    "Not required but recommended for complex sites.",
                    "Create /llms-full.txt with detailed documentation "
                    "that AI systems can use for deeper understanding.",
                )
            )

        # --- ai.txt ---
        if not ai.exists:
            issues.append(
                _issue(
                    "llms_ai",
                    "missing_ai_txt",
                    "low",
                    "Missing ai.txt (emerging standard)",
                    "/ai.txt is an emerging standard for AI crawler permissions "
                    "and preferences. Not yet widely adopted.",
                    "Consider creating /ai.txt to explicitly declare AI usage preferences.",
                )
            )

        # --- IndexNow ---
        if not indexnow.exists:
            issues.append(
                _issue(
                    "llms_ai",
                    "missing_indexnow",
                    "low",
                    "IndexNow not configured",
                    "IndexNow enables instant indexing by Bing, Yandex, DuckDuckGo and others. "
                    "Without it, new content may take days to appear in these search engines.",
                    "Set up IndexNow: generate a key, place it at /.well-known/indexnow, "
                    "and submit URLs via the IndexNow API after publishing.",
                )
            )

        return issues


def _issue(
    category: str, type_: str, severity: str, title: str, description: str, fix: str
) -> Dict[str, Any]:
    return {
        "category": category,
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": "",
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": "",
    }
