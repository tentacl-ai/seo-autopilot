"""
Redirect Audit — Redirect chains, loops, 302->301, soft-404 detection.

Redirect chains waste crawl budget. 302 instead of 301 does not pass
link equity. Internal links to redirect URLs cause avoidable latency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 10
SOFT_404_PHRASES = [
    "not found", "404", "keine seite", "nicht gefunden",
    "page not found", "seite nicht gefunden", "does not exist",
]


@dataclass
class RedirectHop:
    """A single redirect step."""
    url: str
    status_code: int
    location: str  # target URL


@dataclass
class RedirectChain:
    """Complete redirect chain from start to final."""
    start_url: str
    hops: List[RedirectHop] = field(default_factory=list)
    final_url: str = ""
    final_status: int = 0
    is_loop: bool = False
    chain_length: int = 0


@dataclass
class PageForRedirectAudit:
    """Minimal data per page for the redirect audit."""
    url: str
    final_url: str = ""
    status_code: int = 200
    title: str = ""
    h1: str = ""
    word_count: int = 0
    internal_link_targets: List[str] = field(default_factory=list)


class RedirectAuditor:
    """Detects redirect problems and soft-404s."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    async def trace_chain(self, url: str, client: Optional[httpx.AsyncClient] = None) -> RedirectChain:
        """Follows redirects to the final URL, measures length and types."""
        chain = RedirectChain(start_url=url)
        seen: Set[str] = set()
        current = url
        own_client = client is None

        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)

        try:
            for _ in range(MAX_REDIRECTS):
                if current in seen:
                    chain.is_loop = True
                    chain.final_url = current
                    break
                seen.add(current)

                try:
                    resp = await client.get(current, follow_redirects=False)
                except Exception as exc:
                    chain.final_url = current
                    chain.final_status = 0
                    break

                if 300 <= resp.status_code < 400:
                    location = resp.headers.get("location", "")
                    if not location:
                        chain.final_url = current
                        chain.final_status = resp.status_code
                        break
                    chain.hops.append(RedirectHop(
                        url=current,
                        status_code=resp.status_code,
                        location=location,
                    ))
                    current = location
                else:
                    chain.final_url = current
                    chain.final_status = resp.status_code
                    break
            else:
                chain.final_url = current
                chain.final_status = 0

        finally:
            if own_client:
                await client.aclose()

        chain.chain_length = len(chain.hops)
        return chain

    def detect_issues(
        self,
        pages: List[PageForRedirectAudit],
        chains: Optional[List[RedirectChain]] = None,
    ) -> List[Dict[str, Any]]:
        """Detects all redirect and soft-404 issues."""
        issues: List[Dict[str, Any]] = []

        # Redirect chain issues
        if chains:
            for chain in chains:
                if chain.is_loop:
                    issues.append(_redirect_issue(
                        "redirect_loop", "critical", chain.start_url,
                        f"Redirect loop detected: {chain.start_url}",
                        f"URL redirects back to itself after {chain.chain_length} hops.",
                        "Resolve the redirect loop, point directly to the final URL.",
                    ))
                elif chain.chain_length > 1:
                    hop_urls = " -> ".join([h.url for h in chain.hops] + [chain.final_url])
                    issues.append(_redirect_issue(
                        "redirect_chain", "medium", chain.start_url,
                        f"Redirect chain ({chain.chain_length} hops)",
                        f"Chain: {hop_urls}",
                        f"Set redirect directly to {chain.final_url}.",
                    ))

                # 302 should be 301 (permanent content move)
                for hop in chain.hops:
                    if hop.status_code == 302:
                        issues.append(_redirect_issue(
                            "redirect_302_should_be_301", "medium", hop.url,
                            f"302 instead of 301: {hop.url}",
                            f"302 (temporary) to {hop.location}. If permanent, use 301.",
                            "Replace 302 with 301 if the redirect is permanent (link equity!).",
                        ))

                # Redirect to different domain
                if chain.hops:
                    start_domain = urlparse(chain.start_url).netloc
                    final_domain = urlparse(chain.final_url).netloc
                    if start_domain != final_domain:
                        issues.append(_redirect_issue(
                            "redirect_to_different_domain", "high", chain.start_url,
                            f"Redirect to different domain: {final_domain}",
                            f"{chain.start_url} -> {chain.final_url}",
                            "Check whether the domain change is intentional.",
                        ))

        # Internal links to redirects
        redirect_urls = set()
        if chains:
            for chain in chains:
                if chain.chain_length > 0:
                    redirect_urls.add(chain.start_url)

        for page in pages:
            for target in page.internal_link_targets:
                if target in redirect_urls:
                    issues.append(_redirect_issue(
                        "internal_link_to_redirect", "low", page.url,
                        f"Internal link to redirect: {target}",
                        f"Page {page.url} links to {target} (redirected).",
                        "Change internal link to point directly to the final URL.",
                    ))

        # 5xx cluster
        error_pages = [p for p in pages if p.status_code >= 500]
        if len(error_pages) >= 3:
            urls = [p.url for p in error_pages[:5]]
            issues.append(_redirect_issue(
                "5xx_cluster", "critical", error_pages[0].url,
                f"Server error cluster: {len(error_pages)} pages with 5xx",
                f"Affected URLs: {', '.join(urls)}",
                "Check server logs, fix the root cause.",
            ))

        # Soft-404 detection
        for page in pages:
            if page.status_code == 200 and is_soft_404(page):
                issues.append(_redirect_issue(
                    "soft_404", "medium", page.url,
                    f"Soft-404 detected: {page.url}",
                    "Page returns HTTP 200, but content indicates 'not found'.",
                    "Return a real 404 status or fill the page with content.",
                ))

        return issues


def is_soft_404(page: PageForRedirectAudit) -> bool:
    """Detects soft-404: HTTP 200 but content says 'not found'.

    At least 2 of 3 signals must match.
    """
    signals = [
        page.word_count < 50,
        any(phrase in (page.title or "").lower() for phrase in SOFT_404_PHRASES),
        not page.h1 and page.word_count < 100,
    ]
    return sum(signals) >= 2


def _redirect_issue(type_: str, severity: str, url: str,
                    title: str, description: str, fix: str) -> Dict[str, Any]:
    return {
        "category": "redirect",
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": url,
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": "",
    }
