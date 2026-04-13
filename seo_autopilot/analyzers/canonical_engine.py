"""
Canonical Engine — Canonical URL resolution and conflict detection.

Signal hierarchy (descending by strength):
1. HTTP Header Link: rel=canonical (strongest signal)
2. HTML <link rel="canonical"> in head
3. Sitemap entry (weaker signal)
4. Internal linking majority (heuristic)

Must run BEFORE duplicate content detection, since two pages with
similar content can be legitimate if one has a canonical pointing
to the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class CanonicalResolution:
    """Result of canonical resolution for a URL."""

    url: str
    declared_canonical: Optional[str] = None  # from HTML <link rel="canonical">
    http_header_canonical: Optional[str] = None  # from HTTP Link Header
    sitemap_listed: bool = False
    resolved_canonical: Optional[str] = None  # final result
    signal_source: str = "none"  # http_header | html | sitemap | self
    is_self_referencing: bool = False
    conflicts: List[str] = field(default_factory=list)


@dataclass
class PageCanonicalData:
    """Minimal data the CanonicalEngine needs per page."""

    url: str
    final_url: str = ""
    status_code: int = 200
    canonical: Optional[str] = None  # HTML <link rel="canonical">
    http_link_canonical: Optional[str] = None  # HTTP Link header
    robots_meta: Optional[str] = None
    hreflang: List[Dict[str, str]] = field(default_factory=list)


class CanonicalEngine:
    """Canonical URL resolution and conflict detection."""

    def __init__(self, sitemap_urls: Optional[Set[str]] = None):
        """
        Args:
            sitemap_urls: URLs found in the sitemap.
        """
        self.sitemap_urls: Set[str] = sitemap_urls or set()

    def resolve(self, page: PageCanonicalData) -> CanonicalResolution:
        """Evaluates all canonical signals and returns resolved_canonical."""
        url = _normalize_url(page.final_url or page.url)
        resolution = CanonicalResolution(url=url)
        resolution.declared_canonical = page.canonical
        resolution.http_header_canonical = page.http_link_canonical
        resolution.sitemap_listed = (
            url in self.sitemap_urls or page.url in self.sitemap_urls
        )

        # Signal hierarchy: HTTP Header > HTML > Sitemap > Self
        if page.http_link_canonical:
            canonical = _normalize_url(page.http_link_canonical)
            resolution.resolved_canonical = canonical
            resolution.signal_source = "http_header"
        elif page.canonical:
            canonical = _normalize_url(page.canonical)
            resolution.resolved_canonical = canonical
            resolution.signal_source = "html"
        elif resolution.sitemap_listed:
            resolution.resolved_canonical = url
            resolution.signal_source = "sitemap"
        else:
            resolution.resolved_canonical = url
            resolution.signal_source = "self"

        resolution.is_self_referencing = (
            _normalize_url(resolution.resolved_canonical or "") == url
        )

        return resolution

    def resolve_all(
        self, pages: List[PageCanonicalData]
    ) -> Dict[str, CanonicalResolution]:
        """Resolve all pages, returns Dict url -> CanonicalResolution."""
        return {_normalize_url(p.final_url or p.url): self.resolve(p) for p in pages}

    def detect_conflicts(self, pages: List[PageCanonicalData]) -> List[Dict[str, Any]]:
        """Detects canonical conflicts and returns issues."""
        issues: List[Dict[str, Any]] = []
        resolutions = self.resolve_all(pages)

        # Index for fast lookups
        url_status: Dict[str, int] = {}
        url_robots: Dict[str, Optional[str]] = {}
        url_hreflang: Dict[str, List[Dict]] = {}
        for p in pages:
            norm = _normalize_url(p.final_url or p.url)
            url_status[norm] = p.status_code
            url_robots[norm] = p.robots_meta
            url_hreflang[norm] = p.hreflang

        for url, res in resolutions.items():
            canonical = res.resolved_canonical
            if not canonical:
                continue

            # 1. Missing self-canonical
            if not res.declared_canonical and not res.http_header_canonical:
                issues.append(
                    _canonical_issue(
                        "canonical_missing",
                        "medium",
                        url,
                        "No self-canonical set",
                        "Page has neither HTML <link rel=canonical> nor HTTP Link Header.",
                        'Add <link rel="canonical" href="..."> with the page\'s own URL.',
                    )
                )

            # 2. Canonical points to redirect (3xx)
            if canonical != url and canonical in url_status:
                target_status = url_status[canonical]
                if 300 <= target_status < 400:
                    issues.append(
                        _canonical_issue(
                            "canonical_points_to_redirect",
                            "high",
                            url,
                            f"Canonical points to redirect ({target_status})",
                            f"Canonical {canonical} returns HTTP {target_status}.",
                            "Canonical must point to the final URL, not a redirect URL.",
                        )
                    )

            # 3. Canonical points to 404/5xx
            if canonical != url and canonical in url_status:
                target_status = url_status[canonical]
                if target_status >= 400:
                    sev = "critical" if target_status == 404 else "high"
                    issues.append(
                        _canonical_issue(
                            "canonical_points_to_error",
                            sev,
                            url,
                            f"Canonical points to HTTP {target_status}",
                            f"Canonical {canonical} returns HTTP {target_status}.",
                            "Change canonical to a reachable URL or remove it.",
                        )
                    )

            # 4. Canonical conflicts with sitemap
            if (
                canonical != url
                and url in self.sitemap_urls
                and canonical not in self.sitemap_urls
            ):
                issues.append(
                    _canonical_issue(
                        "canonical_conflicts_sitemap",
                        "medium",
                        url,
                        "Canonical conflicts with sitemap",
                        f"URL is in sitemap, canonical points to {canonical} (not in sitemap).",
                        "Correct either the canonical or the sitemap entry.",
                    )
                )

            # 5. Canonical points to noindex URL
            if canonical != url and canonical in url_robots:
                target_robots = url_robots.get(canonical) or ""
                if "noindex" in target_robots.lower():
                    issues.append(
                        _canonical_issue(
                            "canonical_points_to_noindex",
                            "high",
                            url,
                            "Canonical points to noindex page",
                            f"Canonical {canonical} has robots: {target_robots}.",
                            "Canonical must not point to noindex pages.",
                        )
                    )

            # 6. Canonical points outside hreflang cluster
            page_hreflangs = url_hreflang.get(url, [])
            if canonical != url and page_hreflangs:
                cluster_urls = {
                    _normalize_url(h["href"]) for h in page_hreflangs if h.get("href")
                }
                if canonical not in cluster_urls and url in cluster_urls:
                    issues.append(
                        _canonical_issue(
                            "canonical_conflicts_hreflang",
                            "high",
                            url,
                            "Canonical points outside hreflang cluster",
                            f"Canonical {canonical} is not part of the hreflang cluster.",
                            "Canonical must stay within the hreflang cluster.",
                        )
                    )

        # 7. Detect canonical chains (A -> B -> C)
        issues.extend(self._detect_chains(resolutions))

        return issues

    def _detect_chains(
        self, resolutions: Dict[str, CanonicalResolution]
    ) -> List[Dict[str, Any]]:
        """Detects canonical chains (A canonical B canonical C)."""
        issues = []
        for url, res in resolutions.items():
            canonical = res.resolved_canonical
            if not canonical or canonical == url:
                continue
            # Check if the target itself has a canonical pointing to a third URL
            if canonical in resolutions:
                target_res = resolutions[canonical]
                target_canonical = target_res.resolved_canonical
                if target_canonical and target_canonical != canonical:
                    issues.append(
                        _canonical_issue(
                            "canonical_chain",
                            "medium",
                            url,
                            f"Canonical chain: {url} -> {canonical} -> {target_canonical}",
                            "Canonical chains waste crawl budget and weaken signals.",
                            f"Set canonical directly to {target_canonical}.",
                        )
                    )
        return issues

    def is_canonical_pair(
        self, url_a: str, url_b: str, resolutions: Dict[str, CanonicalResolution]
    ) -> bool:
        """Checks whether a canonical relationship exists between two URLs.

        Used by duplicate content detection to avoid false positives.
        """
        a = _normalize_url(url_a)
        b = _normalize_url(url_b)
        res_a = resolutions.get(a)
        res_b = resolutions.get(b)

        if res_a and res_a.resolved_canonical == b:
            return True
        if res_b and res_b.resolved_canonical == a:
            return True
        return False


def _normalize_url(url: str) -> str:
    """Normalizes URL for comparisons (trailing slash, lowercase host)."""
    if not url:
        return ""
    parsed = urlparse(url)
    # Lowercase scheme + host
    normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
    # Normalize trailing slash (root keeps slash)
    if normalized.endswith("/") and len(parsed.path) > 1:
        normalized = normalized.rstrip("/")
    return normalized


def _canonical_issue(
    type_: str, severity: str, url: str, title: str, description: str, fix: str
) -> Dict[str, Any]:
    return {
        "category": "canonical",
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": url,
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": "",
    }
