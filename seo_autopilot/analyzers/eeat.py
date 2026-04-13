"""
E-E-A-T Signal Analyzer — Phase 10.

Google's Experience-Expertise-Authoritativeness-Trustworthiness signals
determine whether a site is considered a reliable source. This module
checks structural trust signals that are machine-verifiable:

- Legal pages (Impressum, Datenschutz) — DSGVO/DE/AT/CH + trust signal
- Organization schema with sameAs (LinkedIn, Wikidata, Wikipedia)
- Author schema on articles (byline, datePublished, dateModified)
- ContactPage / contact info reachable
- Domain-level E-E-A-T score (0-100)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# --- Legal pages (DE/AT/CH focus + international) ---
LEGAL_PATTERNS = {
    "impressum": [
        "impressum", "imprint", "legal-notice", "legal_notice",
    ],
    "datenschutz": [
        "datenschutz", "privacy", "privacy-policy", "data-protection",
        "datenschutzerklaerung", "datenschutzerklärung",
    ],
}

# --- Contact page patterns ---
CONTACT_PATTERNS = [
    "kontakt", "contact", "contact-us", "kontaktformular",
    "get-in-touch", "anfrage",
]

# --- sameAs domains that signal authority ---
AUTHORITY_SAMEAS = {
    "linkedin.com": "LinkedIn",
    "wikidata.org": "Wikidata",
    "wikipedia.org": "Wikipedia",
    "github.com": "GitHub",
    "crunchbase.com": "Crunchbase",
    "twitter.com": "Twitter/X",
    "x.com": "Twitter/X",
    "facebook.com": "Facebook",
    "youtube.com": "YouTube",
}

# --- Score weights ---
WEIGHTS = {
    "impressum": 15,
    "datenschutz": 15,
    "contact_page": 10,
    "org_schema": 10,
    "org_sameas": 10,
    "author_schema": 15,
    "date_published": 10,
    "date_modified": 5,
    "about_page": 5,
    "https": 5,
}


def _url_matches(url: str, patterns: List[str]) -> bool:
    """Check if a URL path contains any of the given patterns."""
    path = url.lower().rstrip("/").split("?")[0]
    return any(p in path for p in patterns)


class EEATAnalyzer:
    """Analyzes domain-level E-E-A-T signals."""

    def analyze(self, pages: List[Dict[str, Any]], domain: str) -> Dict[str, Any]:
        """Run full E-E-A-T analysis across all pages.

        Returns dict with 'score', 'signals', 'issues'.
        """
        signals: Dict[str, Any] = {}
        issues: List[Dict[str, Any]] = []

        urls = [p.get("url", "") for p in pages]
        schema_all = self._collect_schemas(pages)

        # --- Legal pages ---
        has_impressum = any(_url_matches(u, LEGAL_PATTERNS["impressum"]) for u in urls)
        has_datenschutz = any(_url_matches(u, LEGAL_PATTERNS["datenschutz"]) for u in urls)

        signals["impressum"] = has_impressum
        signals["datenschutz"] = has_datenschutz

        if not has_impressum:
            issues.append(_issue(
                "eeat", "missing_impressum", "high",
                "No Impressum / Imprint page found",
                "German law (TMG §5) requires an Impressum. It is also a strong trust "
                "signal for Google — sites without legal identity disclosure rank lower.",
                "Add an /impressum or /imprint page with company name, address, contact, "
                "and registration details.",
            ))

        if not has_datenschutz:
            issues.append(_issue(
                "eeat", "missing_datenschutz", "high",
                "No Privacy Policy / Datenschutz page found",
                "DSGVO (GDPR) requires a privacy policy. Missing it signals low trust "
                "to both users and search engines.",
                "Add a /datenschutz or /privacy page covering data collection, cookies, "
                "and user rights.",
            ))

        # --- Contact page ---
        has_contact = any(_url_matches(u, CONTACT_PATTERNS) for u in urls)
        signals["contact_page"] = has_contact

        if not has_contact:
            issues.append(_issue(
                "eeat", "missing_contact_page", "medium",
                "No contact page found",
                "A dedicated contact page (or contact section) increases trust. "
                "Google's Quality Rater Guidelines emphasize 'who is behind this site'.",
                "Add a /kontakt or /contact page with email, phone, or contact form.",
            ))

        # --- About page ---
        about_patterns = ["about", "ueber-uns", "über-uns", "about-us", "team"]
        has_about = any(_url_matches(u, about_patterns) for u in urls)
        signals["about_page"] = has_about

        if not has_about:
            issues.append(_issue(
                "eeat", "missing_about_page", "low",
                "No About / Über uns page found",
                "An About page helps establish expertise and authority. "
                "Quality raters look for 'who created this content'.",
                "Add an /about or /ueber-uns page with team bios, credentials, "
                "and company background.",
            ))

        # --- Organization schema ---
        org_schemas = [s for s in schema_all if s.get("@type") in ("Organization", "Corporation")]
        has_org_schema = len(org_schemas) > 0
        signals["org_schema"] = has_org_schema

        # sameAs links in Organization
        sameas_found: Dict[str, str] = {}
        for org in org_schemas:
            for link in org.get("sameAs", []):
                if isinstance(link, str):
                    for domain_pattern, label in AUTHORITY_SAMEAS.items():
                        if domain_pattern in link.lower():
                            sameas_found[label] = link

        signals["org_sameas"] = list(sameas_found.keys())
        has_sameas = len(sameas_found) > 0

        if not has_org_schema:
            issues.append(_issue(
                "eeat", "missing_org_schema", "high",
                "No Organization schema found",
                "Organization JSON-LD on the homepage establishes entity identity. "
                "Google Knowledge Graph uses this to connect your site to known entities.",
                "Add Organization schema with name, url, logo, and sameAs.",
            ))
        elif not has_sameas:
            issues.append(_issue(
                "eeat", "org_schema_no_sameas", "medium",
                "Organization schema missing sameAs links",
                "sameAs links (LinkedIn, Wikipedia, Wikidata) help Google connect your "
                "organization to verified external profiles — crucial for E-E-A-T.",
                "Add sameAs array with links to LinkedIn company page, Wikidata entity, "
                "Wikipedia article, and social profiles.",
            ))

        # --- Author schema on articles ---
        article_types = {"Article", "NewsArticle", "BlogPosting", "TechArticle", "Report"}
        article_pages = []
        for p in pages:
            page_types = set(p.get("schema_types", []))
            if page_types & article_types:
                article_pages.append(p)

        articles_with_author = 0
        articles_with_date_published = 0
        articles_with_date_modified = 0

        for p in article_pages:
            for schema in p.get("schema_data", []):
                stype = schema.get("@type", "")
                if stype not in article_types:
                    continue
                if schema.get("author"):
                    articles_with_author += 1
                if schema.get("datePublished"):
                    articles_with_date_published += 1
                if schema.get("dateModified"):
                    articles_with_date_modified += 1

        total_articles = len(article_pages)
        signals["total_articles"] = total_articles
        signals["articles_with_author"] = articles_with_author
        signals["articles_with_date_published"] = articles_with_date_published
        signals["articles_with_date_modified"] = articles_with_date_modified

        has_author = total_articles == 0 or articles_with_author > 0
        has_date_published = total_articles == 0 or articles_with_date_published > 0
        has_date_modified = total_articles == 0 or articles_with_date_modified > 0

        signals["author_schema"] = has_author
        signals["date_published"] = has_date_published
        signals["date_modified"] = has_date_modified

        if total_articles > 0 and articles_with_author == 0:
            issues.append(_issue(
                "eeat", "articles_missing_author", "high",
                "Article pages have no author schema",
                f"{total_articles} article(s) found but none have an author in schema markup. "
                "Google's E-E-A-T guidelines heavily weight author expertise and transparency.",
                "Add 'author' with name (and ideally url) to Article/BlogPosting JSON-LD.",
            ))

        if total_articles > 0 and articles_with_date_published == 0:
            issues.append(_issue(
                "eeat", "articles_missing_date_published", "medium",
                "Articles missing datePublished",
                "No article has datePublished in schema. Freshness is a ranking signal, "
                "and missing dates reduce content credibility.",
                "Add datePublished (ISO 8601) to every Article schema.",
            ))

        if total_articles > 0 and articles_with_date_modified == 0:
            issues.append(_issue(
                "eeat", "articles_missing_date_modified", "low",
                "Articles missing dateModified",
                "dateModified signals content freshness. Updated articles rank better.",
                "Add dateModified to Article schema whenever content is updated.",
            ))

        # --- HTTPS ---
        all_https = all(p.get("https", False) for p in pages) if pages else False
        signals["https"] = all_https

        # --- Compute score ---
        score = self._compute_score(signals)
        signals["eeat_score"] = score

        if score < 30:
            issues.append(_issue(
                "eeat", "eeat_score_critical", "critical",
                f"E-E-A-T score critically low: {score}/100",
                "The site lacks fundamental trust signals. Google's Quality Rater "
                "Guidelines would likely classify this as low-quality.",
                "Priority: add Impressum, Privacy Policy, Organization schema with sameAs.",
            ))
        elif score < 60:
            issues.append(_issue(
                "eeat", "eeat_score_low", "high",
                f"E-E-A-T score below threshold: {score}/100",
                "Several trust signals are missing. This can affect rankings, "
                "especially in YMYL (Your Money, Your Life) niches.",
                "Review the E-E-A-T checklist and address missing signals.",
            ))

        return {
            "score": score,
            "signals": signals,
            "issues": issues,
        }

    def _collect_schemas(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Flatten all schema_data from all pages."""
        result = []
        for p in pages:
            for schema in p.get("schema_data", []):
                if isinstance(schema, dict):
                    result.append(schema)
        return result

    def _compute_score(self, signals: Dict[str, Any]) -> int:
        """Compute E-E-A-T score 0-100 based on weighted signals."""
        score = 0
        for key, weight in WEIGHTS.items():
            val = signals.get(key)
            if isinstance(val, bool) and val:
                score += weight
            elif isinstance(val, list) and len(val) > 0:
                # sameAs: partial credit per platform, capped at weight
                per_item = weight / 3  # 3+ platforms = full score
                score += min(weight, int(len(val) * per_item))
        return min(100, score)


def _issue(category: str, type_: str, severity: str,
           title: str, description: str, fix: str) -> Dict[str, Any]:
    return {
        "category": category,
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": "",
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": "",
    }
