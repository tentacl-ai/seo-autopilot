"""
Internal Link Graph Analyzer.

Builds the internal link graph and detects:
- Orphan pages (no incoming internal link)
- Deep pages (click depth > 3 from homepage)
- Broken internal links (4xx)
- Link equity sinks (many incoming, no outgoing)
- PageRank distribution

No external dependency (no networkx) — custom PageRank.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any, Dict, List, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DAMPING_FACTOR = 0.85
PAGERANK_ITERATIONS = 20
MAX_CLICK_DEPTH = 3


class LinkGraph:
    """Internal link graph with PageRank and depth calculation."""

    def __init__(self):
        self.outlinks: Dict[str, Set[str]] = defaultdict(set)  # url -> {linked_urls}
        self.inlinks: Dict[str, Set[str]] = defaultdict(set)   # url -> {linking_urls}
        self.all_urls: Set[str] = set()
        self.broken_links: Dict[str, int] = {}  # url -> status_code (4xx/5xx)

    def build(self, pages: List[Dict[str, Any]], homepage: str) -> None:
        """Builds the graph from page data.

        Args:
            pages: List of dicts (url, final_url, status_code, outlink_urls).
                   outlink_urls: List of internal link targets.
            homepage: URL of the homepage (for depth calculation).
        """
        self.homepage = _normalize(homepage)

        for page in pages:
            url = _normalize(page.get("url", ""))
            status = page.get("status_code", 200)
            self.all_urls.add(url)

            if status >= 400:
                self.broken_links[url] = status

            for target in page.get("outlink_urls", []):
                target_norm = _normalize(target)
                if target_norm and target_norm != url:
                    self.outlinks[url].add(target_norm)
                    self.inlinks[target_norm].add(url)

    def pagerank(self) -> Dict[str, float]:
        """Calculates PageRank for all URLs in the graph."""
        n = len(self.all_urls)
        if n == 0:
            return {}

        urls = list(self.all_urls)
        pr = {url: 1.0 / n for url in urls}

        for _ in range(PAGERANK_ITERATIONS):
            new_pr = {}
            for url in urls:
                rank_sum = 0.0
                for linker in self.inlinks.get(url, set()):
                    out_count = len(self.outlinks.get(linker, set()))
                    if out_count > 0:
                        rank_sum += pr.get(linker, 0) / out_count
                new_pr[url] = (1 - DAMPING_FACTOR) / n + DAMPING_FACTOR * rank_sum
            pr = new_pr

        return pr

    def click_depth(self) -> Dict[str, int]:
        """BFS from homepage — click depth per URL."""
        depths: Dict[str, int] = {}
        if not self.homepage:
            return depths

        queue = deque([(self.homepage, 0)])
        visited: Set[str] = {self.homepage}

        while queue:
            url, depth = queue.popleft()
            depths[url] = depth

            for target in self.outlinks.get(url, set()):
                if target not in visited:
                    visited.add(target)
                    queue.append((target, depth + 1))

        return depths

    def orphan_pages(self) -> List[str]:
        """URLs without incoming internal links (except homepage)."""
        orphans = []
        for url in self.all_urls:
            if url == self.homepage:
                continue
            if not self.inlinks.get(url):
                orphans.append(url)
        return orphans

    def detect_issues(self, pages: List[Dict[str, Any]], homepage: str) -> List[Dict[str, Any]]:
        """Builds graph and detects all link issues."""
        self.build(pages, homepage)
        issues: List[Dict[str, Any]] = []

        self.pagerank()
        depths = self.click_depth()

        # 1. Orphan pages
        for url in self.orphan_pages():
            if url in self.broken_links:
                continue  # Broken pages are not an orphan issue
            issues.append(_link_issue(
                "orphan_page", "medium", url,
                f"Orphan page: no internal link to {url}",
                "Page has no incoming internal links and is only reachable via sitemap/direct link.",
                "Link the page from thematically relevant pages.",
            ))

        # 2. Deep pages (click depth > 3)
        for url, depth in depths.items():
            if depth > MAX_CLICK_DEPTH:
                issues.append(_link_issue(
                    "deep_page", "low", url,
                    f"Deep page: click depth {depth} (max {MAX_CLICK_DEPTH})",
                    f"Page is {depth} clicks away from the homepage.",
                    "Link from a page with lower depth.",
                ))

        # 3. Unreachable pages (not in depth map)
        for url in self.all_urls:
            if url not in depths and url not in self.broken_links:
                issues.append(_link_issue(
                    "unreachable_page", "high", url,
                    f"Not reachable from homepage: {url}",
                    "Page is not reachable via internal links from the homepage.",
                    "Ensure the page is linked from the main navigation or content pages.",
                ))

        # 4. Broken internal links
        for page in pages:
            url = _normalize(page.get("url", ""))
            for target in page.get("outlink_urls", []):
                target_norm = _normalize(target)
                if target_norm in self.broken_links:
                    status = self.broken_links[target_norm]
                    issues.append(_link_issue(
                        "broken_internal_link", "high" if status == 404 else "medium", url,
                        f"Broken link: {url} -> {target_norm} (HTTP {status})",
                        f"Internal link points to page with HTTP {status}.",
                        "Remove the link or change it to a working URL.",
                    ))

        # 5. Link equity sinks
        for url in self.all_urls:
            incount = len(self.inlinks.get(url, set()))
            outcount = len(self.outlinks.get(url, set()))
            if incount >= 5 and outcount == 0 and url not in self.broken_links:
                issues.append(_link_issue(
                    "link_equity_sink", "low", url,
                    f"Link equity sink: {incount} incoming, 0 outgoing",
                    "Page receives a lot of link equity but passes none on.",
                    "Add internal links to relevant pages.",
                ))

        return issues


def _normalize(url: str) -> str:
    """Normalizes URL for graph comparisons."""
    if not url:
        return ""
    url = url.split("#")[0].split("?")[0]
    if url.endswith("/") and len(urlparse(url).path) > 1:
        url = url.rstrip("/")
    return url.lower()


def _link_issue(type_: str, severity: str, url: str,
                title: str, description: str, fix: str) -> Dict[str, Any]:
    return {
        "category": "link_graph",
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": url,
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": "",
    }
