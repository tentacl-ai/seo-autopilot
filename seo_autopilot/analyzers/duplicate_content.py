"""
Duplicate Content Detector — SimHash-based, canonical-aware.

Detects:
- Near-duplicate content (SimHash Hamming distance < threshold)
- Thin content (word_count < 300)
- Keyword cannibalization (same H1 on 2+ pages)

IMPORTANT: Runs AFTER Canonical Engine and Topical Authority.
- Pages with canonical relationship -> no duplicate issue
- Pages in the same topical cluster -> cluster_cannibalization (not here)
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

HAMMING_THRESHOLD = 10  # bits — smaller = more similar
THIN_CONTENT_THRESHOLD = 300  # words
SIMHASH_BITS = 64


class DuplicateContentDetector:
    """Detects duplicate content, canonical-aware."""

    def __init__(
        self,
        canonical_pairs: Optional[Set[tuple]] = None,
        cluster_urls: Optional[Dict[str, Set[str]]] = None,
    ):
        """
        Args:
            canonical_pairs: Set of (url_a, url_b) tuples that have a
                             canonical relationship -> no duplicate issues.
            cluster_urls: Dict cluster_id -> Set[url] for topical authority clusters.
                          Cannibalization within clusters is NOT reported as
                          duplicate (the TopicalAuthorityAnalyzer handles that).
        """
        self.canonical_pairs = canonical_pairs or set()
        self.cluster_urls = cluster_urls or {}
        # Reverse lookup: url -> cluster_id
        self._url_cluster: Dict[str, str] = {}
        for cid, urls in self.cluster_urls.items():
            for url in urls:
                self._url_cluster[url] = cid

    def detect_issues(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detects duplicate content issues.

        Args:
            pages: List of dicts (url, title, h1, word_count, text_content).
                   text_content is optional — if present, SimHash is calculated.
        """
        issues: List[Dict[str, Any]] = []

        # 1. Thin content
        for page in pages:
            wc = page.get("word_count", 0)
            if 0 < wc < THIN_CONTENT_THRESHOLD:
                url = page.get("url", "")
                # Exclude legal pages
                if any(
                    k in url.lower()
                    for k in ("impressum", "datenschutz", "privacy", "terms", "agb")
                ):
                    continue
                issues.append(
                    _duplicate_issue(
                        "thin_content",
                        "medium",
                        url,
                        f"Thin content: {wc} words",
                        f"Page has only {wc} words (minimum: {THIN_CONTENT_THRESHOLD}).",
                        "Expand the content or merge with a similar page.",
                    )
                )

        # 2. SimHash near-duplicates
        simhashes: List[tuple] = []  # (url, simhash_value)
        for page in pages:
            text = page.get("text_content", "")
            if not text:
                # Fallback: title + H1 + meta
                title = page.get("title", "") or ""
                h1_list = page.get("h1", [])
                h1 = h1_list[0] if isinstance(h1_list, list) and h1_list else ""
                meta = page.get("meta_description", "") or ""
                text = f"{title} {h1} {meta}"

            if len(text.split()) < 20:
                continue

            sh = simhash(text)
            simhashes.append((page.get("url", ""), sh))

        # Pairwise comparison (O(n^2) but n is typically < 50)
        for i, (url_a, hash_a) in enumerate(simhashes):
            for url_b, hash_b in simhashes[i + 1 :]:
                dist = hamming_distance(hash_a, hash_b)
                if dist <= HAMMING_THRESHOLD:
                    # Canonical pair? -> Skip
                    if self._is_canonical_pair(url_a, url_b):
                        continue
                    # Same cluster? -> Skip (TopicalAuthority handles it)
                    if self._same_cluster(url_a, url_b):
                        continue

                    issues.append(
                        _duplicate_issue(
                            "near_duplicate_content",
                            "high",
                            url_a,
                            f"Near-duplicate: {url_a} and {url_b}",
                            f"SimHash Hamming distance: {dist} (threshold: {HAMMING_THRESHOLD}). "
                            f"Pages have very similar content.",
                            f"Set canonical or merge pages. "
                            f"Recommendation: Set canonical from {url_b} to {url_a}.",
                        )
                    )

        # 3. Keyword cannibalization (same H1 on 2+ pages)
        h1_pages: Dict[str, List[str]] = defaultdict(list)
        for page in pages:
            h1_list = page.get("h1", [])
            h1 = (
                h1_list[0].strip().lower()
                if isinstance(h1_list, list) and h1_list
                else ""
            )
            if h1 and len(h1) > 5:
                url = page.get("url", "")
                # Skip if in same cluster
                h1_pages[h1].append(url)

        for h1, urls in h1_pages.items():
            if len(urls) < 2:
                continue
            # Filter: Only URLs NOT in the same cluster
            non_cluster_groups = self._group_by_cluster(urls)
            if len(non_cluster_groups) >= 2:
                issues.append(
                    _duplicate_issue(
                        "keyword_cannibalization",
                        "high",
                        urls[0],
                        f"Keyword cannibalization: H1 '{h1}' on {len(urls)} pages",
                        f"URLs: {', '.join(urls[:5])}",
                        "Differentiate the H1 tags or set canonical to the strongest page.",
                    )
                )

        return issues

    def _is_canonical_pair(self, url_a: str, url_b: str) -> bool:
        return (url_a, url_b) in self.canonical_pairs or (
            url_b,
            url_a,
        ) in self.canonical_pairs

    def _same_cluster(self, url_a: str, url_b: str) -> bool:
        ca = self._url_cluster.get(url_a)
        cb = self._url_cluster.get(url_b)
        return ca is not None and ca == cb

    def _group_by_cluster(self, urls: List[str]) -> List[List[str]]:
        """Groups URLs by cluster. URLs without cluster = own group."""
        groups: Dict[str, List[str]] = defaultdict(list)
        for url in urls:
            cluster = self._url_cluster.get(url, f"_none_{url}")
            groups[cluster].append(url)
        return list(groups.values())


# ---------------------------------------------------------------
# SimHash implementation (64-bit, no external dependency)
# ---------------------------------------------------------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def simhash(text: str, hashbits: int = SIMHASH_BITS) -> int:
    """Calculates 64-bit SimHash of a text.

    SimHash is a locality-sensitive hash: similar texts have
    similar hashes (small Hamming distance).
    """
    tokens = _WORD_RE.findall(text.lower())
    if not tokens:
        return 0

    v = [0] * hashbits

    for token in tokens:
        token_hash = int(hashlib.md5(token.encode()).hexdigest(), 16)
        for i in range(hashbits):
            bitmask = 1 << i
            if token_hash & bitmask:
                v[i] += 1
            else:
                v[i] -= 1

    fingerprint = 0
    for i in range(hashbits):
        if v[i] >= 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Calculates Hamming distance between two integers."""
    return bin(a ^ b).count("1")


def _duplicate_issue(
    type_: str, severity: str, url: str, title: str, description: str, fix: str
) -> Dict[str, Any]:
    return {
        "category": "duplicate_content",
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": url,
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": "",
    }
