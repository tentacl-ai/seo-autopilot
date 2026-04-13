"""Tests for Intelligence Agent — Impact Analysis + Alerts.

No real API calls. All external services are mocked.
"""

import sys
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Stub apscheduler if not installed (CI / minimal env)
if "apscheduler" not in sys.modules:
    _ap = MagicMock()
    sys.modules["apscheduler"] = _ap
    sys.modules["apscheduler.schedulers"] = _ap
    sys.modules["apscheduler.schedulers.asyncio"] = _ap
    sys.modules["apscheduler.triggers"] = _ap
    sys.modules["apscheduler.triggers.cron"] = _ap

from seo_autopilot.sources.intelligence import (
    AlgorithmEvent,
    FeedItem,
    IntelligenceFeed,
    SEO_FEEDS,
)
from seo_autopilot.agents.intelligence_agent import (
    IntelligenceAgent,
    ImpactReport,
    ProjectImpact,
)
from seo_autopilot.core.scheduler import AuditScheduler


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_project():
    p = MagicMock()
    p.id = "test-project"
    p.domain = "example.com"
    p.name = "Test Project"
    p.enabled = True
    return p


@pytest.fixture
def mock_project_manager(mock_project):
    pm = MagicMock()
    pm.get_enabled_projects.return_value = [mock_project]
    return pm


@pytest.fixture
def confirmed_event():
    return AlgorithmEvent(
        event_id="evt001",
        title="Algorithm Event: Core Update",
        priority="critical",
        sources=["google_search_central", "search_engine_journal"],
        items=[],
        first_seen=datetime(2026, 4, 10, tzinfo=timezone.utc),
        confirmed=True,
        keywords=["core update"],
    )


@pytest.fixture
def agent(mock_project_manager):
    feed = IntelligenceFeed()
    return IntelligenceAgent(feed=feed, project_manager=mock_project_manager)


# ============================================================
# AUFGABE 1 — Scheduler Intelligence Jobs
# ============================================================


class TestSchedulerIntelligenceJobs:
    def test_schedule_intelligence_jobs(self):
        sched = AuditScheduler()
        sched.scheduler = MagicMock()

        poll_cb = AsyncMock()
        check_cb = AsyncMock()

        sched.schedule_intelligence_jobs(poll_cb, check_cb)

        assert sched.scheduler.add_job.call_count == 2
        calls = sched.scheduler.add_job.call_args_list

        # Erster Call: poll_feeds
        assert calls[0].kwargs["id"] == "intelligence_poll_feeds"
        assert calls[0].kwargs["replace_existing"] is True

        # Zweiter Call: check_for_updates
        assert calls[1].kwargs["id"] == "intelligence_check_for_updates"
        assert calls[1].kwargs["replace_existing"] is True

    def test_schedule_intelligence_jobs_replace_existing(self):
        """Jobs koennen mehrfach registriert werden ohne Fehler."""
        sched = AuditScheduler()
        sched.scheduler = MagicMock()

        poll_cb = AsyncMock()
        check_cb = AsyncMock()

        sched.schedule_intelligence_jobs(poll_cb, check_cb)
        sched.schedule_intelligence_jobs(poll_cb, check_cb)

        assert sched.scheduler.add_job.call_count == 4


# ============================================================
# AUFGABE 2 — Google News Feeds
# ============================================================


class TestGoogleNewsFeeds:
    def test_google_news_feeds_present(self):
        assert "google_news_algo" in SEO_FEEDS
        assert "google_news_cwv" in SEO_FEEDS
        assert "google_news_geo" in SEO_FEEDS
        assert "google_news_ai_crawlers" in SEO_FEEDS

    def test_google_news_feeds_urls(self):
        for key in ("google_news_algo", "google_news_cwv", "google_news_geo", "google_news_ai_crawlers"):
            url = SEO_FEEDS[key]
            assert "news.google.com/rss/search" in url
            assert "hl=en-US" in url
            assert "gl=US" in url
            assert "ceid=US:en" in url

    def test_total_feed_count(self):
        # 8 original + 4 Google News = 12
        assert len(SEO_FEEDS) == 12

    def test_feed_instance_includes_google_news(self):
        feed = IntelligenceFeed()
        assert "google_news_algo" in feed.feeds


# ============================================================
# AUFGABE 3 — Intelligence Agent
# ============================================================


class TestIntelligenceAgentPollFeeds:
    @pytest.mark.asyncio
    async def test_poll_feeds_detects_events(self, agent):
        """poll_feeds delegiert an IntelligenceFeed."""
        mock_items = [
            FeedItem(title="Core Update", url="u1", source="src1",
                     priority="critical", matched_keywords=["core update"]),
            FeedItem(title="Core Update confirmed", url="u2", source="src2",
                     priority="critical", matched_keywords=["core update"]),
        ]
        agent.feed.poll_feeds = MagicMock(return_value=mock_items)
        agent.feed.detect_events = MagicMock(return_value=[
            AlgorithmEvent(
                event_id="e1", title="Core Update", priority="critical",
                sources=["src1", "src2"], confirmed=True, keywords=["core update"],
            )
        ])

        events = await agent.poll_feeds()
        assert len(events) == 1
        assert events[0].confirmed is True

    @pytest.mark.asyncio
    async def test_poll_feeds_stores_events(self, agent):
        agent.feed.poll_feeds = MagicMock(return_value=[])
        agent.feed.detect_events = MagicMock(return_value=[
            AlgorithmEvent(event_id="e1", title="Test", priority="high",
                           sources=["a", "b"], confirmed=True, keywords=["test"])
        ])

        await agent.poll_feeds()
        assert len(agent._events) == 1


class TestIntelligenceAgentCheckForUpdates:
    @pytest.mark.asyncio
    async def test_no_critical_events_skips(self, agent):
        agent._events = [
            AlgorithmEvent(event_id="e1", title="Minor", priority="high",
                           sources=["a", "b"], confirmed=True, keywords=["test"])
        ]
        reports = await agent.check_for_updates()
        assert reports == []

    @pytest.mark.asyncio
    async def test_critical_event_triggers_analysis(self, agent, confirmed_event):
        agent._events = [confirmed_event]
        agent._send_alert = AsyncMock(return_value=False)

        reports = await agent.check_for_updates()
        assert len(reports) == 1
        assert reports[0].event.event_id == "evt001"
        agent._send_alert.assert_called_once()


class TestImpactAnalysis:
    @pytest.mark.asyncio
    async def test_heuristic_fallback_without_api_key(self, agent, confirmed_event):
        """Ohne CLAUDE_API_KEY wird heuristic assessment verwendet."""
        with patch.object(type(agent), '_assess_project_impact', wraps=agent._assess_project_impact):
            with patch("seo_autopilot.agents.intelligence_agent.settings") as mock_settings:
                mock_settings.CLAUDE_API_KEY = None
                report = await agent.analyze_impact(confirmed_event)

        assert len(report.impacts) == 1
        assert report.impacts[0].risk_level == "MEDIUM"
        assert len(report.impacts[0].actions) == 3

    @pytest.mark.asyncio
    async def test_max_5_projects(self, mock_project_manager, confirmed_event):
        """Maximal 5 Projekte werden analysiert."""
        projects = [MagicMock(id=f"p{i}", domain=f"d{i}.com") for i in range(10)]
        mock_project_manager.get_enabled_projects.return_value = projects
        agent = IntelligenceAgent(project_manager=mock_project_manager)
        agent._assess_project_impact = AsyncMock(
            return_value=ProjectImpact(project_id="p0", domain="d0.com")
        )

        report = await agent.analyze_impact(confirmed_event)
        assert agent._assess_project_impact.call_count == 5

    def test_parse_impact_response(self, agent):
        text = "RISK: HIGH\nACTION1: Fix meta tags\nACTION2: Update schema\nACTION3: Check CWV"
        impact = agent._parse_impact_response(text, "proj", "example.com")
        assert impact.risk_level == "HIGH"
        assert len(impact.actions) == 3
        assert "Fix meta tags" in impact.actions[0]

    def test_parse_impact_response_malformed(self, agent):
        text = "some random text"
        impact = agent._parse_impact_response(text, "proj", "example.com")
        assert impact.risk_level == "LOW"  # default
        assert impact.actions == []


class TestTelegramAlert:
    def test_format_alert_message(self, agent, confirmed_event):
        report = ImpactReport(
            event=confirmed_event,
            impacts=[
                ProjectImpact(
                    project_id="test", domain="example.com",
                    score=72.0, risk_level="HIGH",
                    actions=["Fix CWV", "Update schema", "Check rankings"],
                ),
            ],
            analyzed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        )
        msg = agent._format_alert(report)
        assert "ALGORITHM ALERT" in msg
        assert "Core Update" in msg
        assert "google_search_central" in msg
        assert "example.com" in msg
        assert "HIGH" in msg
        assert "Fix CWV" in msg
        assert "localhost:8002/api/intelligence/events" in msg

    @pytest.mark.asyncio
    async def test_send_alert_no_config(self, agent, confirmed_event):
        """Ohne Telegram-Config wird kein Alert gesendet."""
        report = ImpactReport(event=confirmed_event, impacts=[])
        with patch("seo_autopilot.agents.intelligence_agent.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = None
            mock_settings.TELEGRAM_CHAT_ID = None
            result = await agent._send_alert(report)
        assert result is False


class TestAPIHelpers:
    def test_get_events_empty(self, agent):
        assert agent.get_events() == []

    def test_get_events_with_data(self, agent, confirmed_event):
        agent._events = [confirmed_event]
        events = agent.get_events()
        assert len(events) == 1
        assert events[0]["event_id"] == "evt001"
        assert events[0]["confirmed"] is True

    def test_get_impact_report_not_found(self, agent):
        assert agent.get_impact_report("nonexistent") is None

    def test_get_impact_report_found(self, agent, confirmed_event):
        report = ImpactReport(
            event=confirmed_event,
            impacts=[ProjectImpact(project_id="test-project", domain="example.com")],
        )
        agent._reports = [report]
        result = agent.get_impact_report("test-project")
        assert result is not None
        assert result["event_id"] == "evt001"


class TestManualPollEndpoint:
    """Test POST /api/intelligence/poll via TestClient."""

    def test_manual_poll_returns_200(self):
        with patch("seo_autopilot.api.main.intelligence_agent") as mock_agent:
            mock_agent.poll_feeds = AsyncMock(return_value=[])
            from starlette.testclient import TestClient
            from seo_autopilot.api.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/intelligence/poll")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["events_detected"] == 0

    def test_manual_poll_with_events(self):
        evt = AlgorithmEvent(
            event_id="e1", title="Core Update", priority="critical",
            sources=["a", "b"], confirmed=True, keywords=["core update"],
        )
        with patch("seo_autopilot.api.main.intelligence_agent") as mock_agent:
            mock_agent.poll_feeds = AsyncMock(return_value=[evt])
            from starlette.testclient import TestClient
            from seo_autopilot.api.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/intelligence/poll")
            assert resp.status_code == 200
            data = resp.json()
            assert data["events_detected"] == 1
            assert data["events"][0]["event_id"] == "e1"


class TestImpactReportSerialization:
    def test_to_dict(self, confirmed_event):
        report = ImpactReport(
            event=confirmed_event,
            impacts=[
                ProjectImpact(project_id="p1", domain="d1.com", risk_level="HIGH", actions=["a"]),
            ],
            analyzed_at=datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
        )
        d = report.to_dict()
        assert d["event_id"] == "evt001"
        assert len(d["impacts"]) == 1
        assert d["impacts"][0]["risk_level"] == "HIGH"
        assert d["analyzed_at"] is not None
