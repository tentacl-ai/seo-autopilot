"""
Intent + GEO Content Analysis — Phase 12.

Uses Claude API to analyze top GSC keywords:
- Intent Match (0-100): does the page content match search intent?
- GEO Readiness (0-100): will AI systems cite this content?
- Content Gaps: what do top SERP results have that this page lacks?

Hard cap: max 10 Claude API calls per audit.
Selection: only keywords at position 4-30 with impressions >= 100.
Graceful skip when no CLAUDE_API_KEY is set.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_API_CALLS = 10
MIN_POSITION = 4
MAX_POSITION = 30
MIN_IMPRESSIONS = 100

# Claude API prompt for intent + GEO analysis
ANALYSIS_PROMPT = """Analyze this page for the search keyword "{keyword}".

Page URL: {url}
Page Title: {title}
Page H1: {h1}
Word Count: {word_count}
First 500 words of content (approximated from meta + headings):
{content_preview}

Respond in this exact JSON format (no markdown, no explanation):
{{
  "intent_match": <0-100>,
  "intent_type": "<informational|navigational|transactional|commercial>",
  "intent_explanation": "<1 sentence why the score>",
  "geo_readiness": <0-100>,
  "geo_explanation": "<1 sentence: would AI cite this?>",
  "content_gaps": ["<gap 1>", "<gap 2>", "<gap 3>"],
  "suggested_improvements": ["<improvement 1>", "<improvement 2>"]
}}

Scoring guide:
- intent_match: Does this page answer what someone searching "{keyword}" wants?
  90-100 = perfect match, 50-70 = partial, <30 = wrong intent
- geo_readiness: Would Google AI Overview or ChatGPT cite this page?
  90-100 = structured, factual, citable. 50-70 = decent but unstructured. <30 = no chance
"""


@dataclass
class KeywordAnalysis:
    """Result of analyzing one keyword+page pair."""
    keyword: str
    url: str
    position: float
    impressions: int
    clicks: int
    intent_match: int = 0
    intent_type: str = ""
    intent_explanation: str = ""
    geo_readiness: int = 0
    geo_explanation: str = ""
    content_gaps: List[str] = field(default_factory=list)
    suggested_improvements: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class IntentGEOResult:
    """Full result of intent + GEO analysis."""
    analyses: List[KeywordAnalysis] = field(default_factory=list)
    avg_intent_match: float = 0.0
    avg_geo_readiness: float = 0.0
    api_calls_used: int = 0
    skipped_reason: Optional[str] = None
    issues: List[Dict[str, Any]] = field(default_factory=list)


def select_keywords(
    gsc_keywords: List[Dict[str, Any]],
    max_count: int = MAX_API_CALLS,
) -> List[Dict[str, Any]]:
    """Select keywords worth analyzing: position 4-30, impressions >= 100.

    Sorts by impressions descending (highest opportunity first).
    """
    candidates = [
        kw for kw in gsc_keywords
        if MIN_POSITION <= kw.get("position", 0) <= MAX_POSITION
        and kw.get("impressions", 0) >= MIN_IMPRESSIONS
    ]
    candidates.sort(key=lambda k: k.get("impressions", 0), reverse=True)
    return candidates[:max_count]


def build_prompt(keyword: str, page: Dict[str, Any]) -> str:
    """Build the Claude API prompt for a keyword+page pair."""
    h1_list = page.get("h1", [])
    h1 = h1_list[0] if h1_list else ""
    content_preview = _build_content_preview(page)

    return ANALYSIS_PROMPT.format(
        keyword=keyword,
        url=page.get("url", ""),
        title=page.get("title", "") or "",
        h1=h1,
        word_count=page.get("word_count", 0),
        content_preview=content_preview,
    )


def _build_content_preview(page: Dict[str, Any]) -> str:
    """Build a content preview from available page data."""
    parts = []
    if page.get("title"):
        parts.append(f"Title: {page['title']}")
    if page.get("meta_description"):
        parts.append(f"Description: {page['meta_description']}")
    for h in page.get("h1", []):
        parts.append(f"H1: {h}")
    for h in page.get("h2", [])[:10]:
        parts.append(f"H2: {h}")
    return "\n".join(parts) if parts else "(no content available)"


def parse_response(raw: str) -> Dict[str, Any]:
    """Parse Claude's JSON response, tolerant of markdown fences."""
    import json

    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


async def call_claude(prompt: str, api_key: str) -> str:
    """Call Claude API with a prompt. Returns raw response text."""
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 512,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        # Extract text from content blocks
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block["text"]
    return ""


async def analyze_keywords(
    gsc_keywords: List[Dict[str, Any]],
    pages: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    call_fn=None,
) -> IntentGEOResult:
    """Run intent + GEO analysis on selected keywords.

    Args:
        gsc_keywords: Keywords from GSC with position, impressions, clicks, url.
        pages: Crawled page snapshots (from AnalyzerAgent).
        api_key: Claude API key. If None, reads from CLAUDE_API_KEY env.
        call_fn: Optional override for the API call function (for testing).
    """
    result = IntentGEOResult()

    # Resolve API key
    key = api_key or os.environ.get("CLAUDE_API_KEY", "")
    if not key:
        result.skipped_reason = "No CLAUDE_API_KEY set — intent/GEO analysis skipped"
        logger.info(f"[intent_geo] {result.skipped_reason}")
        return result

    # Select keywords
    selected = select_keywords(gsc_keywords)
    if not selected:
        result.skipped_reason = "No keywords matching criteria (pos 4-30, impressions >= 100)"
        logger.info(f"[intent_geo] {result.skipped_reason}")
        return result

    # Build page lookup
    page_map = {p.get("url", ""): p for p in pages}

    # Analyze each keyword (respecting hard cap)
    _call = call_fn or call_claude
    for kw in selected[:MAX_API_CALLS]:
        url = kw.get("url", "")
        page = page_map.get(url, {"url": url})

        analysis = KeywordAnalysis(
            keyword=kw.get("keyword", ""),
            url=url,
            position=kw.get("position", 0),
            impressions=kw.get("impressions", 0),
            clicks=kw.get("clicks", 0),
        )

        try:
            prompt = build_prompt(kw["keyword"], page)
            raw_response = await _call(prompt, key)
            result.api_calls_used += 1

            parsed = parse_response(raw_response)
            if parsed:
                analysis.intent_match = int(parsed.get("intent_match", 0))
                analysis.intent_type = parsed.get("intent_type", "")
                analysis.intent_explanation = parsed.get("intent_explanation", "")
                analysis.geo_readiness = int(parsed.get("geo_readiness", 0))
                analysis.geo_explanation = parsed.get("geo_explanation", "")
                analysis.content_gaps = parsed.get("content_gaps", [])
                analysis.suggested_improvements = parsed.get("suggested_improvements", [])
            else:
                analysis.error = "Failed to parse API response"
        except Exception as exc:
            analysis.error = str(exc)
            logger.warning(f"[intent_geo] API call failed for '{kw.get('keyword', '')}': {exc}")

        result.analyses.append(analysis)

    # Compute averages
    valid = [a for a in result.analyses if not a.error]
    if valid:
        result.avg_intent_match = sum(a.intent_match for a in valid) / len(valid)
        result.avg_geo_readiness = sum(a.geo_readiness for a in valid) / len(valid)

    # Generate issues
    result.issues = _generate_issues(result)

    logger.info(
        f"[intent_geo] Analyzed {len(result.analyses)} keywords, "
        f"{result.api_calls_used} API calls, "
        f"avg intent={result.avg_intent_match:.0f}, "
        f"avg GEO={result.avg_geo_readiness:.0f}"
    )

    return result


def _generate_issues(result: IntentGEOResult) -> List[Dict[str, Any]]:
    """Generate issues from analysis results."""
    issues: List[Dict[str, Any]] = []

    for a in result.analyses:
        if a.error:
            continue

        if a.intent_match < 40:
            issues.append({
                "category": "content",
                "type": "poor_intent_match",
                "severity": "high",
                "title": f"Poor intent match for '{a.keyword}' ({a.intent_match}/100)",
                "affected_url": a.url,
                "description": (
                    f"Page at position {a.position:.0f} does not match search intent "
                    f"for '{a.keyword}'. {a.intent_explanation}"
                ),
                "fix_suggestion": "; ".join(a.suggested_improvements[:2]) if a.suggested_improvements else
                    "Rewrite content to match the search intent.",
                "estimated_impact": f"Keyword has {a.impressions} impressions/week",
            })
        elif a.intent_match < 70:
            issues.append({
                "category": "content",
                "type": "moderate_intent_match",
                "severity": "medium",
                "title": f"Moderate intent match for '{a.keyword}' ({a.intent_match}/100)",
                "affected_url": a.url,
                "description": (
                    f"Page partially matches intent for '{a.keyword}' (pos {a.position:.0f}). "
                    f"{a.intent_explanation}"
                ),
                "fix_suggestion": "; ".join(a.suggested_improvements[:2]) if a.suggested_improvements else
                    "Strengthen content alignment with search intent.",
                "estimated_impact": f"Keyword has {a.impressions} impressions/week",
            })

        if a.geo_readiness < 40:
            issues.append({
                "category": "geo",
                "type": "poor_geo_readiness",
                "severity": "high",
                "title": f"Low GEO readiness for '{a.keyword}' ({a.geo_readiness}/100)",
                "affected_url": a.url,
                "description": (
                    f"AI systems unlikely to cite this page for '{a.keyword}'. "
                    f"{a.geo_explanation}"
                ),
                "fix_suggestion": "Add structured answers, statistics, and clear H2 sections.",
                "estimated_impact": "",
            })

        if a.content_gaps:
            issues.append({
                "category": "content",
                "type": "content_gaps_detected",
                "severity": "medium",
                "title": f"Content gaps for '{a.keyword}'",
                "affected_url": a.url,
                "description": f"Missing vs competitors: {'; '.join(a.content_gaps[:3])}",
                "fix_suggestion": f"Add sections covering: {'; '.join(a.content_gaps[:3])}",
                "estimated_impact": "",
            })

    return issues
