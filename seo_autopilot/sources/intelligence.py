"""
SEO Intelligence Feed — Algorithm Update Monitor.

Polls RSS feeds from Google Search Central, SEJ, Moz etc. and
detects algorithmic updates via keyword matching.

Requires: `pip install feedparser` (optional dependency).
Works without feedparser — returns empty results in that case.

Confirmed events: At least 2 independent sources must report.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

try:
    import feedparser

    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False
    logger.info(
        "[intelligence] feedparser not installed — feed polling disabled. Install with: pip install feedparser"
    )

# RSS feed sources
SEO_FEEDS: Dict[str, str] = {
    "google_search_central": "https://developers.google.com/search/blog/rss.xml",
    "google_status": "https://status.search.google.com/en/summary.rss",
    "search_engine_journal": "https://www.searchenginejournal.com/feed/",
    "search_engine_land": "https://searchengineland.com/feed",
    "moz_blog": "https://feedpress.me/mozblog",
    "ahrefs_blog": "https://ahrefs.com/blog/rss.xml",
    "web_dev_blog": "https://web.dev/feed.xml",
    "chrome_developers": "https://developer.chrome.com/static/blog/rss.xml",
    "google_news_algo": "https://news.google.com/rss/search?q=google+algorithm+update&hl=en-US&gl=US&ceid=US:en",
    "google_news_cwv": "https://news.google.com/rss/search?q=core+web+vitals+INP&hl=en-US&gl=US&ceid=US:en",
    "google_news_geo": "https://news.google.com/rss/search?q=google+AI+overviews+SEO&hl=en-US&gl=US&ceid=US:en",
    "google_news_ai_crawlers": "https://news.google.com/rss/search?q=GPTBot+ClaudeBot+SEO&hl=en-US&gl=US&ceid=US:en",
}

# Keywords by priority
PRIORITY_KEYWORDS: Dict[str, List[str]] = {
    "critical": [
        "core update",
        "broad core update",
        "spam update",
        "algorithm update",
        "ranking change",
        "search update",
    ],
    "high": [
        "core web vitals",
        "inp",
        "ai overview",
        "ai mode",
        "indexing",
        "crawling",
        "structured data",
        "rich results",
    ],
    "medium": [
        "e-e-a-t",
        "schema",
        "geo",
        "ai search",
        "chatgpt search",
        "gemini",
        "perplexity",
        "helpful content",
        "link spam",
        "review update",
    ],
}

# Minimum sources for event confirmation
MIN_SOURCES_FOR_CONFIRMED = 2


@dataclass
class FeedItem:
    """A single RSS feed entry."""

    title: str
    url: str
    source: str
    published: Optional[datetime] = None
    summary: str = ""
    priority: str = "low"
    matched_keywords: List[str] = field(default_factory=list)
    item_id: str = ""

    def __post_init__(self):
        if not self.item_id:
            self.item_id = hashlib.md5(
                f"{self.source}:{self.url}".encode()
            ).hexdigest()[:12]


@dataclass
class AlgorithmEvent:
    """A detected algorithmic event (confirmed by 2+ sources)."""

    event_id: str
    title: str
    priority: str
    sources: List[str] = field(default_factory=list)
    items: List[FeedItem] = field(default_factory=list)
    first_seen: Optional[datetime] = None
    confirmed: bool = False
    keywords: List[str] = field(default_factory=list)


class IntelligenceFeed:
    """SEO Intelligence Feed with algorithm update detection."""

    def __init__(self, feeds: Optional[Dict[str, str]] = None):
        self.feeds = feeds or SEO_FEEDS
        self._seen_ids: Set[str] = set()
        self._items: List[FeedItem] = []

    @property
    def available(self) -> bool:
        """Checks whether feedparser is installed."""
        return HAS_FEEDPARSER

    def poll_feeds(self, max_items_per_feed: int = 20) -> List[FeedItem]:
        """Polls all configured RSS feeds.

        Returns:
            List of new FeedItems (since last poll).
        """
        if not HAS_FEEDPARSER:
            logger.warning("[intelligence] feedparser not installed — skipping poll")
            return []

        new_items: List[FeedItem] = []

        for source_name, feed_url in self.feeds.items():
            try:
                items = self._parse_feed(source_name, feed_url, max_items_per_feed)
                for item in items:
                    if item.item_id not in self._seen_ids:
                        self._seen_ids.add(item.item_id)
                        new_items.append(item)
            except Exception as exc:
                logger.warning(f"[intelligence] Feed {source_name} failed: {exc}")

        self._items.extend(new_items)
        logger.info(
            f"[intelligence] Polled {len(self.feeds)} feeds, {len(new_items)} new items"
        )
        return new_items

    def detect_events(
        self, items: Optional[List[FeedItem]] = None
    ) -> List[AlgorithmEvent]:
        """Detects algorithmic events from feed items.

        An event is considered confirmed when at least 2 different sources
        report on the same topic (same priority keywords match).
        """
        items = items or self._items
        if not items:
            return []

        # Group by matched keywords
        keyword_groups: Dict[str, List[FeedItem]] = defaultdict(list)
        for item in items:
            if item.priority in ("critical", "high"):
                for kw in item.matched_keywords:
                    keyword_groups[kw.lower()].append(item)

        events: List[AlgorithmEvent] = []
        seen_keywords: Set[str] = set()

        for keyword, group_items in keyword_groups.items():
            if keyword in seen_keywords:
                continue

            # Count different sources
            sources = list({item.source for item in group_items})

            if len(sources) >= MIN_SOURCES_FOR_CONFIRMED:
                seen_keywords.add(keyword)
                event_id = hashlib.md5(keyword.encode()).hexdigest()[:10]

                # Earliest item = first_seen
                dates = [i.published for i in group_items if i.published]
                first_seen = min(dates) if dates else None

                events.append(
                    AlgorithmEvent(
                        event_id=event_id,
                        title=f"Algorithm Event: {keyword.title()}",
                        priority=group_items[0].priority,
                        sources=sources,
                        items=group_items[:5],
                        first_seen=first_seen,
                        confirmed=True,
                        keywords=[keyword],
                    )
                )

        return events

    def get_prioritized_items(self, limit: int = 50) -> List[FeedItem]:
        """Returns the newest items sorted by priority."""
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_items = sorted(
            self._items,
            key=lambda i: (
                priority_order.get(i.priority, 3),
                -(i.published or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            ),
        )
        return sorted_items[:limit]

    def _parse_feed(self, source: str, url: str, max_items: int) -> List[FeedItem]:
        """Parses a single RSS feed."""
        feed = feedparser.parse(url)
        items = []

        for entry in feed.entries[:max_items]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", "")[:500]

            # Parse publish date
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    )
                except (TypeError, ValueError):
                    pass

            # Determine priority
            priority, matched = _classify_priority(f"{title} {summary}")

            items.append(
                FeedItem(
                    title=title,
                    url=link,
                    source=source,
                    published=published,
                    summary=summary,
                    priority=priority,
                    matched_keywords=matched,
                )
            )

        return items


def _classify_priority(text: str) -> tuple:
    """Determines the priority of a text based on keywords.

    Returns:
        (priority_level, matched_keywords)
    """
    text_lower = text.lower()
    matched: List[str] = []

    for priority in ("critical", "high", "medium"):
        for keyword in PRIORITY_KEYWORDS[priority]:
            if keyword in text_lower:
                matched.append(keyword)
                if priority == "critical":
                    return priority, matched

    if matched:
        # Highest found priority
        for priority in ("critical", "high", "medium"):
            for kw in matched:
                if kw in PRIORITY_KEYWORDS.get(priority, []):
                    return priority, matched
    return "low", matched
