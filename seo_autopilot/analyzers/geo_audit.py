"""
GEO Audit — Generative Engine Optimization.

Checks whether pages are structurally optimized for AI citations:
- Google AI Overviews
- ChatGPT Search
- Perplexity
- Gemini

AI systems (RAG-based) preferentially extract:
- Direct answers in the first 150 words
- Clearly structured content (H2/H3 as questions, lists, tables)
- Fact-rich passages with numbers/sources
- Short paragraphs (max 3 sentences per paragraph)
- Pages that do not block AI crawlers
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# AI crawler user-agents checked in robots.txt
AI_CRAWLERS = [
    "GPTBot",
    "ChatGPT-User",
    "ClaudeBot",
    "PerplexityBot",
    "Google-Extended",
    "anthropic-ai",
    "cohere-ai",
    "CCBot",
    "Bytespider",
]

# GEO check definitions with weighting
GEO_CHECKS = {
    "ai_crawler_access": {
        "weight": 25,
        "severity": "critical",
        "description": "AI crawlers blocked in robots.txt",
        "geo_impact": "Blocked = no citation possible",
    },
    "answer_first": {
        "weight": 20,
        "severity": "high",
        "description": "First 150 words contain no direct answer",
        "geo_impact": "AI RAG systems preferentially extract the first 200 words",
    },
    "structured_format": {
        "weight": 15,
        "severity": "medium",
        "description": "No question-based H2/H3, no lists/tables",
        "geo_impact": "AI parsers prefer clearly structured content",
    },
    "fact_density": {
        "weight": 15,
        "severity": "medium",
        "description": "Few statistics, numbers, or source references",
        "geo_impact": "Fact-rich content is cited 30-40% more often",
    },
    "paragraph_length": {
        "weight": 10,
        "severity": "low",
        "description": "Paragraphs too long for RAG extraction",
        "geo_impact": "Short passages (max 3 sentences) are extracted more directly",
    },
    "entity_clarity": {
        "weight": 10,
        "severity": "medium",
        "description": "Brand/organization not defined as entity",
        "geo_impact": "AI systems work entity-based",
    },
    "freshness_signals": {
        "weight": 5,
        "severity": "medium",
        "description": "datePublished/dateModified missing",
        "geo_impact": "AI systems have a strong recency bias",
    },
}


class GEOAuditor:
    """Checks pages for AI citability."""

    def __init__(self, robots_txt_content: Optional[str] = None):
        """
        Args:
            robots_txt_content: Content of robots.txt (if available).
        """
        self.robots_txt = robots_txt_content or ""

    def check_ai_crawler_access(self) -> List[str]:
        """Checks which AI crawlers are blocked in robots.txt."""
        blocked = []
        if not self.robots_txt:
            return blocked

        lines = self.robots_txt.lower().split("\n")
        current_agent = ""

        for line in lines:
            line = line.strip()
            if line.startswith("user-agent:"):
                current_agent = line.split(":", 1)[1].strip()
            elif line.startswith("disallow:") and line.split(":", 1)[1].strip() == "/":
                # Check if current agent is an AI crawler
                for crawler in AI_CRAWLERS:
                    if crawler.lower() == current_agent or current_agent == "*":
                        if current_agent == "*":
                            # Wildcard blocks everything, but check if
                            # specific allow rules for AI crawlers exist
                            pass  # Wildcard block handled separately
                        else:
                            blocked.append(crawler)

        return blocked

    def analyze_page(self, page: Dict[str, Any]) -> Dict[str, Any]:
        """Analyzes a single page for GEO readiness.

        Args:
            page: Dict with url, title, h1, h2, word_count, schema_types,
                  schema_data, meta_description, etc.

        Returns:
            Dict with geo_score (0-100), checks (passed/failed), issues.
        """
        checks_passed: Dict[str, bool] = {}
        issues: List[Dict[str, Any]] = []
        url = page.get("url", "")

        # 1. Answer-First: Check if page immediately provides an answer
        # Heuristic: H1 present + sufficient content
        h1_list = page.get("h1", [])
        h1 = (
            h1_list[0]
            if isinstance(h1_list, list) and h1_list
            else (h1_list if isinstance(h1_list, str) else "")
        )
        word_count = page.get("word_count", 0)
        checks_passed["answer_first"] = bool(h1) and word_count >= 100

        # 2. Structured Format: H2/H3 as questions, lists present
        h2_list = page.get("h2", [])
        has_question_headings = any(
            h.endswith("?")
            or h.lower().startswith(
                (
                    "was ",
                    "wie ",
                    "warum ",
                    "wann ",
                    "wo ",
                    "what ",
                    "how ",
                    "why ",
                    "when ",
                    "where ",
                )
            )
            for h in (h2_list if isinstance(h2_list, list) else [])
        )
        has_enough_structure = len(h2_list) >= 2 if isinstance(h2_list, list) else False
        checks_passed["structured_format"] = (
            has_question_headings or has_enough_structure
        )

        # 3. Fact Density: Numbers, percentages, source references in title/description
        title = page.get("title", "") or ""
        description = page.get("meta_description", "") or ""
        combined_text = f"{title} {description} {h1}"
        number_pattern = re.compile(r"\d+[%+]?|\d+\.\d+")
        fact_count = len(number_pattern.findall(combined_text))
        checks_passed["fact_density"] = fact_count >= 1 or word_count >= 500

        # 4. Paragraph Length (heuristic via word_count / h2 ratio)
        h2_count = len(h2_list) if isinstance(h2_list, list) else 0
        if h2_count > 0 and word_count > 0:
            avg_section_words = word_count / (h2_count + 1)
            checks_passed["paragraph_length"] = avg_section_words < 300
        else:
            checks_passed["paragraph_length"] = word_count < 500

        # 5. Entity Clarity: Organization/Person schema present
        schema_types = page.get("schema_types", [])
        has_entity_schema = any(
            t in schema_types
            for t in ("Organization", "Person", "LocalBusiness", "WebSite")
        )
        checks_passed["entity_clarity"] = has_entity_schema

        # 6. Freshness Signals: datePublished/dateModified in schema
        schema_data = page.get("schema_data", [])
        has_date = False
        for s in schema_data:
            if isinstance(s, dict) and (
                s.get("datePublished") or s.get("dateModified")
            ):
                has_date = True
                break
        checks_passed["freshness_signals"] = has_date

        # 7. AI Crawler Access (site-level, not page-level)
        blocked_crawlers = self.check_ai_crawler_access()
        checks_passed["ai_crawler_access"] = len(blocked_crawlers) == 0

        # Calculate GEO score (weighted)
        geo_score = 0.0
        for check_name, passed in checks_passed.items():
            weight = GEO_CHECKS[check_name]["weight"]
            if passed:
                geo_score += weight

        # Generate issues for failed checks
        for check_name, passed in checks_passed.items():
            if not passed:
                check_def = GEO_CHECKS[check_name]
                issue = {
                    "category": "geo",
                    "type": f"geo_{check_name}",
                    "severity": check_def["severity"],
                    "title": f"GEO: {check_def['description']}",
                    "affected_url": url,
                    "description": check_def["geo_impact"],
                    "fix_suggestion": _get_fix_suggestion(
                        check_name, page, blocked_crawlers
                    ),
                    "estimated_impact": "",
                }
                issues.append(issue)

        return {
            "url": url,
            "geo_score": round(geo_score, 1),
            "checks": checks_passed,
            "issues": issues,
        }

    def analyze_site(self, pages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyzes all pages of a site for GEO readiness.

        Returns:
            Dict with avg_geo_score, page_scores, all_issues.
        """
        results = []
        all_issues: List[Dict[str, Any]] = []

        for page in pages:
            result = self.analyze_page(page)
            results.append(result)
            all_issues.extend(result["issues"])

        scores = [r["geo_score"] for r in results]
        avg_score = sum(scores) / max(len(scores), 1)

        # AI crawler issues only once (site-level)
        blocked = self.check_ai_crawler_access()
        site_issues = []
        if blocked:
            site_issues.append(
                {
                    "category": "geo",
                    "type": "geo_ai_crawler_blocked",
                    "severity": "critical",
                    "title": f"AI crawlers blocked: {', '.join(blocked)}",
                    "affected_url": "robots.txt",
                    "description": f"robots.txt blocks {len(blocked)} AI crawlers. "
                    f"Blocked crawlers: {', '.join(blocked)}",
                    "fix_suggestion": "Update robots.txt: allow AI crawlers for GEO visibility.",
                    "estimated_impact": "",
                }
            )

        # Deduplicate AI crawler issues from page-level
        page_issues = [i for i in all_issues if i["type"] != "geo_ai_crawler_access"]

        return {
            "avg_geo_score": round(avg_score, 1),
            "page_scores": {r["url"]: r["geo_score"] for r in results},
            "issues": site_issues + page_issues,
            "pages_analyzed": len(pages),
        }


def _get_fix_suggestion(check_name: str, page: Dict, blocked: List[str]) -> str:
    """Concrete fix suggestions per check."""
    suggestions = {
        "ai_crawler_access": f"robots.txt: Allow {', '.join(blocked or ['AI crawlers'])}. "
        f"Example: User-agent: GPTBot\\nAllow: /",
        "answer_first": "Start the page with a direct answer to the main question "
        "of the title. First 150 words = core message.",
        "structured_format": "Use H2/H3 that formulate real questions (e.g. 'What is...?'). "
        "Add lists, tables, or FAQ sections.",
        "fact_density": "Add statistics, numbers, percentages, and source references. "
        "Fact-rich content is cited 30-40% more often.",
        "paragraph_length": "Shorten paragraphs to max 3 sentences. "
        "AI RAG systems extract short passages more precisely.",
        "entity_clarity": "Add Organization or Person JSON-LD schema "
        "with name, url, sameAs (LinkedIn, Wikipedia).",
        "freshness_signals": "Add datePublished and dateModified to Article/BlogPosting schema. "
        "AI systems prefer recent content.",
    }
    return suggestions.get(check_name, "")
