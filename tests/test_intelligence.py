"""Tests for Intelligence Feed — Algorithm Monitor.

Tests run even without feedparser (graceful degradation).
"""

import pytest
from datetime import datetime, timezone
from seo_autopilot.sources.intelligence import (
    IntelligenceFeed,
    FeedItem,
    AlgorithmEvent,
    _classify_priority,
    HAS_FEEDPARSER,
)


@pytest.fixture
def feed():
    return IntelligenceFeed()


class TestPriorityClassification:
    def test_critical_core_update(self):
        priority, keywords = _classify_priority("Google announces March 2026 broad core update")
        assert priority == "critical"
        assert any("core update" in kw for kw in keywords)

    def test_high_ai_overview(self):
        priority, keywords = _classify_priority("New AI Overview features rolling out")
        assert priority == "high"
        assert "ai overview" in keywords

    def test_medium_eeat(self):
        priority, keywords = _classify_priority("How E-E-A-T impacts your rankings")
        assert priority == "medium"

    def test_low_unrelated(self):
        priority, keywords = _classify_priority("Best restaurants in Berlin 2026")
        assert priority == "low"
        assert keywords == []


class TestFeedItemCreation:
    def test_feed_item_auto_id(self):
        item = FeedItem(title="Test", url="https://example.com", source="test")
        assert item.item_id != ""
        assert len(item.item_id) == 12

    def test_feed_item_unique_ids(self):
        a = FeedItem(title="A", url="https://example.com/a", source="src1")
        b = FeedItem(title="B", url="https://example.com/b", source="src2")
        assert a.item_id != b.item_id


class TestEventDetection:
    def test_two_source_confirmation_required(self, feed):
        items = [
            FeedItem(title="Google Core Update", url="u1", source="google_search_central",
                     priority="critical", matched_keywords=["core update"]),
            FeedItem(title="Google Core Update confirmed", url="u2", source="search_engine_journal",
                     priority="critical", matched_keywords=["core update"]),
        ]
        events = feed.detect_events(items)
        assert len(events) >= 1
        assert events[0].confirmed is True
        assert len(events[0].sources) >= 2

    def test_single_source_not_confirmed(self, feed):
        items = [
            FeedItem(title="Core Update rumor", url="u1", source="random_blog",
                     priority="critical", matched_keywords=["core update"]),
        ]
        events = feed.detect_events(items)
        assert len(events) == 0

    def test_low_priority_no_event(self, feed):
        items = [
            FeedItem(title="Cooking tips", url="u1", source="src1", priority="low", matched_keywords=[]),
            FeedItem(title="Travel guide", url="u2", source="src2", priority="low", matched_keywords=[]),
        ]
        events = feed.detect_events(items)
        assert len(events) == 0


class TestFeedPolling:
    def test_poll_without_feedparser_returns_empty(self, feed):
        if HAS_FEEDPARSER:
            pytest.skip("feedparser is installed — cannot test missing-feedparser path")
        result = feed.poll_feeds()
        assert result == []

    def test_available_property(self, feed):
        assert feed.available == HAS_FEEDPARSER


class TestPrioritizedItems:
    def test_returns_critical_first(self, feed):
        feed._items = [
            FeedItem(title="Low", url="u1", source="s1", priority="low",
                     published=datetime(2026, 4, 10, tzinfo=timezone.utc)),
            FeedItem(title="Critical", url="u2", source="s2", priority="critical",
                     published=datetime(2026, 4, 10, tzinfo=timezone.utc)),
            FeedItem(title="Medium", url="u3", source="s3", priority="medium",
                     published=datetime(2026, 4, 10, tzinfo=timezone.utc)),
        ]
        result = feed.get_prioritized_items(limit=3)
        assert result[0].priority == "critical"
        assert result[1].priority == "medium"
        assert result[2].priority == "low"
