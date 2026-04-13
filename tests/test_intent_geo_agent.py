"""Tests for Intent + GEO Content Analysis (Phase 12)."""

import json
import pytest
from unittest.mock import AsyncMock

from seo_autopilot.agents.intent_geo_agent import (
    analyze_keywords,
    select_keywords,
    build_prompt,
    parse_response,
    KeywordAnalysis,
    MAX_API_CALLS,
)


def _kw(keyword, position, impressions, clicks=10, url="https://example.com/page"):
    return {
        "keyword": keyword,
        "position": position,
        "impressions": impressions,
        "clicks": clicks,
        "url": url,
    }


def _page(url="https://example.com/page"):
    return {
        "url": url,
        "title": "Example Page",
        "meta_description": "A great page about things",
        "h1": ["Example Page"],
        "h2": ["Section 1", "Section 2"],
        "word_count": 800,
    }


MOCK_RESPONSE = json.dumps({
    "intent_match": 75,
    "intent_type": "informational",
    "intent_explanation": "Good coverage of the topic",
    "geo_readiness": 60,
    "geo_explanation": "Structured but lacks statistics",
    "content_gaps": ["pricing comparison", "case studies"],
    "suggested_improvements": ["Add pricing table", "Include testimonials"],
})


# ---------------------------------------------------------------------------
# Keyword selection
# ---------------------------------------------------------------------------


class TestKeywordSelection:
    def test_filters_by_position_and_impressions(self):
        keywords = [
            _kw("seo tool", 2, 500),      # pos too high (top 3)
            _kw("seo audit", 8, 200),      # good
            _kw("seo check", 15, 50),      # impressions too low
            _kw("seo report", 35, 300),    # pos too low (>30)
            _kw("seo score", 12, 150),     # good
        ]
        selected = select_keywords(keywords)
        kw_names = [k["keyword"] for k in selected]
        assert "seo audit" in kw_names
        assert "seo score" in kw_names
        assert "seo tool" not in kw_names
        assert "seo check" not in kw_names
        assert "seo report" not in kw_names

    def test_sorts_by_impressions(self):
        keywords = [
            _kw("low", 10, 100),
            _kw("high", 10, 500),
            _kw("mid", 10, 250),
        ]
        selected = select_keywords(keywords)
        assert selected[0]["keyword"] == "high"
        assert selected[-1]["keyword"] == "low"

    def test_respects_max_count(self):
        keywords = [_kw(f"kw-{i}", 10, 200) for i in range(20)]
        selected = select_keywords(keywords, max_count=5)
        assert len(selected) == 5


# ---------------------------------------------------------------------------
# API call cap + graceful skip
# ---------------------------------------------------------------------------


class TestAPICapAndSkip:
    @pytest.mark.asyncio
    async def test_graceful_skip_no_api_key(self):
        """No CLAUDE_API_KEY → skip gracefully, no error."""
        result = await analyze_keywords(
            gsc_keywords=[_kw("test", 10, 200)],
            pages=[_page()],
            api_key="",
        )
        assert result.skipped_reason is not None
        assert "CLAUDE_API_KEY" in result.skipped_reason
        assert result.api_calls_used == 0
        assert result.analyses == []

    @pytest.mark.asyncio
    async def test_hard_cap_10_calls(self):
        """Even with 15 qualifying keywords, max 10 API calls."""
        keywords = [_kw(f"kw-{i}", 10, 200 + i) for i in range(15)]
        mock_call = AsyncMock(return_value=MOCK_RESPONSE)

        result = await analyze_keywords(
            gsc_keywords=keywords,
            pages=[_page()],
            api_key="test-key",
            call_fn=mock_call,
        )
        assert result.api_calls_used == MAX_API_CALLS
        assert mock_call.call_count == MAX_API_CALLS

    @pytest.mark.asyncio
    async def test_skip_no_qualifying_keywords(self):
        """No keywords in range → skip."""
        keywords = [_kw("top3", 2, 500)]  # position too high
        result = await analyze_keywords(
            gsc_keywords=keywords,
            pages=[_page()],
            api_key="test-key",
        )
        assert result.skipped_reason is not None
        assert result.api_calls_used == 0


# ---------------------------------------------------------------------------
# Analysis logic
# ---------------------------------------------------------------------------


class TestAnalysis:
    @pytest.mark.asyncio
    async def test_successful_analysis(self):
        mock_call = AsyncMock(return_value=MOCK_RESPONSE)
        keywords = [_kw("seo audit", 8, 300)]

        result = await analyze_keywords(
            gsc_keywords=keywords,
            pages=[_page()],
            api_key="test-key",
            call_fn=mock_call,
        )
        assert len(result.analyses) == 1
        a = result.analyses[0]
        assert a.intent_match == 75
        assert a.geo_readiness == 60
        assert a.intent_type == "informational"
        assert len(a.content_gaps) == 2
        assert result.avg_intent_match == 75.0
        assert result.avg_geo_readiness == 60.0

    @pytest.mark.asyncio
    async def test_api_error_handled(self):
        """API failure on one keyword doesn't crash the whole run."""
        mock_call = AsyncMock(side_effect=Exception("API timeout"))
        keywords = [_kw("seo test", 10, 200)]

        result = await analyze_keywords(
            gsc_keywords=keywords,
            pages=[_page()],
            api_key="test-key",
            call_fn=mock_call,
        )
        assert len(result.analyses) == 1
        assert result.analyses[0].error is not None

    @pytest.mark.asyncio
    async def test_issues_generated_for_low_scores(self):
        low_response = json.dumps({
            "intent_match": 25,
            "intent_type": "informational",
            "intent_explanation": "Totally wrong topic",
            "geo_readiness": 20,
            "geo_explanation": "No structure at all",
            "content_gaps": ["pricing", "FAQ"],
            "suggested_improvements": ["Rewrite entirely"],
        })
        mock_call = AsyncMock(return_value=low_response)
        keywords = [_kw("seo pricing", 12, 400)]

        result = await analyze_keywords(
            gsc_keywords=keywords,
            pages=[_page()],
            api_key="test-key",
            call_fn=mock_call,
        )
        types = [i["type"] for i in result.issues]
        assert "poor_intent_match" in types
        assert "poor_geo_readiness" in types
        assert "content_gaps_detected" in types


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParsing:
    def test_parse_clean_json(self):
        parsed = parse_response(MOCK_RESPONSE)
        assert parsed["intent_match"] == 75

    def test_parse_markdown_fenced(self):
        fenced = f"```json\n{MOCK_RESPONSE}\n```"
        parsed = parse_response(fenced)
        assert parsed["intent_match"] == 75

    def test_parse_invalid_returns_empty(self):
        parsed = parse_response("not json at all")
        assert parsed == {}

    def test_build_prompt_contains_keyword(self):
        prompt = build_prompt("seo audit", _page())
        assert "seo audit" in prompt
        assert "Example Page" in prompt
