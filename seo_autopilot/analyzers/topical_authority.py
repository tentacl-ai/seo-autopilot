"""
Topical Authority Analyzer — Topic cluster detection.

Detects whether a site has built topic clusters (pillar + cluster pages)
and whether they are complete, consistently linked, and non-cannibalizing.

Approach (without scikit-learn, for sites up to ~200 pages):
1. URL path clustering: /blog/seo-*, /blog/content-* etc.
2. Title keyword overlap: Pages with similar title keywords
3. Link graph validation: Cluster pages must be internally linked
4. GSC-based coverage gaps: Keywords with impressions without ranking URL
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Stop words ignored during keyword extraction
STOP_WORDS = {
    "der",
    "die",
    "das",
    "ein",
    "eine",
    "und",
    "oder",
    "ist",
    "sind",
    "the",
    "a",
    "an",
    "and",
    "or",
    "is",
    "are",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "was",
    "were",
    "been",
    "be",
    "fuer",
    "mit",
    "von",
    "aus",
    "nach",
    "bei",
    "wie",
    "was",
    "wir",
    "ihr",
    "sie",
    "ich",
    "du",
    "er",
    "es",
    "nicht",
    "auch",
    "noch",
}

MIN_CLUSTER_SIZE = 2  # Minimum 2 pages for a cluster


@dataclass
class TopicCluster:
    """A detected topic cluster."""

    cluster_id: str
    topic_label: str
    pillar_url: Optional[str] = None
    cluster_urls: List[str] = field(default_factory=list)
    coverage_score: float = 0.0  # 0-100
    authority_score: float = 0.0  # based on internal links
    gap_keywords: List[str] = field(default_factory=list)
    cannibalization_risk: bool = False
    internal_link_coverage: float = 0.0  # % of cluster pages that are internally linked


class TopicalAuthorityAnalyzer:
    """Detects topic clusters and structural authority problems."""

    def detect_clusters(
        self,
        pages: List[Dict[str, Any]],
    ) -> List[TopicCluster]:
        """Detects topic clusters via URL paths and title keywords.

        Args:
            pages: List of dicts (url, title, h1, h2, word_count,
                   internal_links, schema_types).
        """
        # Phase 1: URL path clustering
        path_clusters = self._cluster_by_url_path(pages)

        # Phase 2: Title keyword overlap (merged into existing clusters)
        keyword_clusters = self._cluster_by_title_keywords(pages)

        # Merge: Path clusters have priority, keyword clusters supplement
        all_clusters = self._merge_clusters(path_clusters, keyword_clusters)

        # Phase 3: Pillar detection and scoring
        for cluster in all_clusters:
            self._identify_pillar(cluster, pages)
            self._calculate_authority(cluster, pages)

        return [c for c in all_clusters if len(c.cluster_urls) >= MIN_CLUSTER_SIZE]

    def detect_issues(
        self,
        clusters: List[TopicCluster],
        pages: List[Dict[str, Any]],
        gsc_keywords: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Detects issues based on cluster analysis."""
        issues: List[Dict[str, Any]] = []

        if not clusters:
            # No clusters detected — with enough pages this is an issue
            if len(pages) >= 5:
                issues.append(
                    _authority_issue(
                        "no_topic_clusters_detected",
                        "high",
                        pages[0].get("url", ""),
                        "No topic clusters detected",
                        f"Site has {len(pages)} pages but no recognizable cluster structure. "
                        f"Thematically related pages should be grouped into pillar clusters.",
                        "Create pillar pages for main topics and link cluster pages to them.",
                    )
                )
            return issues

        all_cluster_urls: Set[str] = set()
        for cluster in clusters:
            all_cluster_urls.update(cluster.cluster_urls)
            if cluster.pillar_url:
                all_cluster_urls.add(cluster.pillar_url)

        for cluster in clusters:
            # Missing pillar
            if not cluster.pillar_url:
                issues.append(
                    _authority_issue(
                        "missing_pillar_page",
                        "high",
                        cluster.cluster_urls[0] if cluster.cluster_urls else "",
                        f"Cluster '{cluster.topic_label}' without pillar page",
                        f"Cluster has {len(cluster.cluster_urls)} pages but no recognizable pillar article.",
                        "Create a comprehensive pillar page that summarizes all aspects of the topic.",
                    )
                )

            # Weak internal linking
            if cluster.internal_link_coverage < 0.5:
                issues.append(
                    _authority_issue(
                        "weak_cluster_linking",
                        "medium",
                        cluster.pillar_url or cluster.cluster_urls[0],
                        f"Cluster '{cluster.topic_label}': weak internal linking",
                        f"Only {cluster.internal_link_coverage:.0%} of cluster pages are internally linked.",
                        "Link all cluster pages to each other and to/from the pillar.",
                    )
                )

            # Cannibalization
            if cluster.cannibalization_risk:
                issues.append(
                    _authority_issue(
                        "cluster_cannibalization",
                        "high",
                        cluster.pillar_url or cluster.cluster_urls[0],
                        f"Cluster '{cluster.topic_label}': keyword cannibalization",
                        "Multiple pages in the cluster have identical H1/title keywords.",
                        "Differentiate the pages thematically or set canonicals.",
                    )
                )

        # Orphan pages (belong to no cluster)
        for page in pages:
            url = page.get("url", "")
            if url not in all_cluster_urls and page.get("word_count", 0) > 200:
                # Only flag pages with real content
                issues.append(
                    _authority_issue(
                        "orphan_cluster_page",
                        "low",
                        url,
                        "Page belongs to no topic cluster",
                        "Page is thematically isolated and does not benefit from cluster authority.",
                        "Assign the page to an existing cluster or create a new one.",
                    )
                )

        # Coverage gaps via GSC
        if gsc_keywords:
            for cluster in clusters:
                gaps = self._find_coverage_gaps(cluster, gsc_keywords, pages)
                cluster.gap_keywords = gaps
                if gaps:
                    issues.append(
                        _authority_issue(
                            "cluster_coverage_gap",
                            "medium",
                            cluster.pillar_url or cluster.cluster_urls[0],
                            f"Cluster '{cluster.topic_label}': {len(gaps)} missing subtopics",
                            f"GSC shows impressions for keywords without ranking URL: {', '.join(gaps[:5])}",
                            "Create content for the missing subtopics in the cluster.",
                        )
                    )

        return issues

    # ---------------------------------------------------------------
    # Private: Clustering
    # ---------------------------------------------------------------

    def _cluster_by_url_path(self, pages: List[Dict]) -> List[TopicCluster]:
        """Groups pages by URL path prefix."""
        path_groups: Dict[str, List[str]] = defaultdict(list)

        for page in pages:
            url = page.get("url", "")
            parsed = urlparse(url)
            parts = [p for p in parsed.path.strip("/").split("/") if p]

            if len(parts) >= 2:
                prefix = parts[0]  # e.g. "blog", "products", "services"
                path_groups[prefix].append(url)

        clusters = []
        for prefix, urls in path_groups.items():
            if len(urls) >= MIN_CLUSTER_SIZE:
                clusters.append(
                    TopicCluster(
                        cluster_id=f"path_{prefix}",
                        topic_label=prefix.replace("-", " ").title(),
                        cluster_urls=urls,
                    )
                )
        return clusters

    def _cluster_by_title_keywords(self, pages: List[Dict]) -> List[TopicCluster]:
        """Groups pages by shared title keywords."""
        # Extract keywords per page
        page_keywords: Dict[str, Set[str]] = {}
        for page in pages:
            url = page.get("url", "")
            title = page.get("title", "") or ""
            h1_list = page.get("h1", [])
            h1 = h1_list[0] if isinstance(h1_list, list) and h1_list else ""

            keywords = _extract_keywords(f"{title} {h1}")
            if keywords:
                page_keywords[url] = keywords

        # Find keyword groups (pages sharing >= 2 keywords)
        keyword_groups: Dict[str, List[str]] = defaultdict(list)
        urls = list(page_keywords.keys())

        for i, url_a in enumerate(urls):
            for url_b in urls[i + 1 :]:
                shared = page_keywords[url_a] & page_keywords[url_b]
                if len(shared) >= 2:
                    group_key = "_".join(sorted(shared)[:3])
                    if url_a not in keyword_groups[group_key]:
                        keyword_groups[group_key].append(url_a)
                    if url_b not in keyword_groups[group_key]:
                        keyword_groups[group_key].append(url_b)

        clusters = []
        for key, urls in keyword_groups.items():
            if len(urls) >= MIN_CLUSTER_SIZE:
                label = key.replace("_", " ").title()
                clusters.append(
                    TopicCluster(
                        cluster_id=f"keyword_{key}",
                        topic_label=label,
                        cluster_urls=urls,
                    )
                )
        return clusters

    def _merge_clusters(
        self, path_clusters: List[TopicCluster], keyword_clusters: List[TopicCluster]
    ) -> List[TopicCluster]:
        """Merges path and keyword clusters. Path has priority."""
        assigned: Set[str] = set()
        result = []

        for c in path_clusters:
            result.append(c)
            assigned.update(c.cluster_urls)

        for c in keyword_clusters:
            remaining = [u for u in c.cluster_urls if u not in assigned]
            if len(remaining) >= MIN_CLUSTER_SIZE:
                c.cluster_urls = remaining
                result.append(c)
                assigned.update(remaining)

        return result

    def _identify_pillar(self, cluster: TopicCluster, pages: List[Dict]) -> None:
        """Identifies the pillar page in the cluster (shortest path or most links)."""
        best_url = None
        best_score = -1

        for page in pages:
            url = page.get("url", "")
            if url not in cluster.cluster_urls:
                continue

            # Score: shorter path + more word count + more internal links
            parsed = urlparse(url)
            path_depth = len([p for p in parsed.path.strip("/").split("/") if p])
            word_count = page.get("word_count", 0)
            internal_links = page.get("internal_links", 0)

            score = (1000 - path_depth * 100) + word_count + internal_links * 50
            if score > best_score:
                best_score = score
                best_url = url

        cluster.pillar_url = best_url

    def _calculate_authority(self, cluster: TopicCluster, pages: List[Dict]) -> None:
        """Calculates authority score and internal link coverage."""
        cluster_urls_set = set(cluster.cluster_urls)

        total_internal_links = 0
        linked_pages = 0

        for page in pages:
            url = page.get("url", "")
            if url in cluster_urls_set:
                il = page.get("internal_links", 0)
                total_internal_links += il
                if il > 0:
                    linked_pages += 1

        cluster_size = len(cluster.cluster_urls)
        cluster.internal_link_coverage = linked_pages / max(cluster_size, 1)
        cluster.authority_score = min(100.0, total_internal_links * 5.0)

        # Cannibalization: Check if H1/titles overlap too much
        titles = []
        for page in pages:
            if page.get("url") in cluster_urls_set:
                title = (page.get("title") or "").lower().strip()
                if title:
                    titles.append(title)

        if len(titles) >= 2:
            # Simple check: identical first 3 words
            prefixes = [" ".join(t.split()[:3]) for t in titles]
            if len(prefixes) != len(set(prefixes)):
                cluster.cannibalization_risk = True

    def _find_coverage_gaps(
        self,
        cluster: TopicCluster,
        gsc_keywords: List[Dict],
        pages: List[Dict],
    ) -> List[str]:
        """Finds keywords with impressions without ranking URL in the cluster."""
        cluster_urls_set = set(cluster.cluster_urls)
        # Keywords ranking on cluster URLs
        cluster_keywords: Set[str] = set()
        for kw in gsc_keywords:
            if kw.get("page", "") in cluster_urls_set:
                cluster_keywords.add(kw.get("query", "").lower())

        # Keywords with impressions NOT ranking on cluster URLs
        # but thematically related (share at least 1 word with cluster label)
        label_words = set(cluster.topic_label.lower().split())
        gaps = []
        for kw in gsc_keywords:
            query = kw.get("query", "").lower()
            if query in cluster_keywords:
                continue
            impressions = kw.get("impressions", 0)
            if impressions < 30:
                continue
            query_words = set(query.split())
            if query_words & label_words:
                gaps.append(query)

        return gaps[:10]


def _extract_keywords(text: str) -> Set[str]:
    """Extracts keywords from text (words > 3 characters, without stop words)."""
    words = re.findall(r"\w+", text.lower())
    return {w for w in words if len(w) > 3 and w not in STOP_WORDS}


def _authority_issue(
    type_: str, severity: str, url: str, title: str, description: str, fix: str
) -> Dict[str, Any]:
    return {
        "category": "topical_authority",
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": url,
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": "",
    }
