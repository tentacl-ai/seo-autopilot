"""
Schema Validator — Structured Data (JSON-LD) validation.

Checks JSON-LD schemas for:
- Syntactic correctness
- Required fields per schema type (Product, Article, FAQ, etc.)
- Rich result opportunities (which pages could unlock which rich results)

Uses the already extracted schema_data from the crawler (PageData.schema_data),
therefore does not need extruct as a dependency.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Required fields per schema type (Google Rich Results requirements, April 2026)
REQUIRED_FIELDS: Dict[str, List[str]] = {
    "Product": ["name", "offers"],
    "Article": ["headline", "author", "datePublished"],
    "NewsArticle": ["headline", "author", "datePublished"],
    "BlogPosting": ["headline", "author", "datePublished"],
    "FAQPage": ["mainEntity"],
    "HowTo": ["step"],
    "BreadcrumbList": ["itemListElement"],
    "LocalBusiness": ["name", "address", "telephone"],
    "Organization": ["name"],
    "Event": ["name", "startDate", "location"],
    "Recipe": ["name", "recipeIngredient", "recipeInstructions"],
    "VideoObject": ["name", "uploadDate", "thumbnailUrl"],
    "JobPosting": ["title", "datePosted", "description", "hiringOrganization"],
}

# Recommended fields (not required, but helpful for rich results)
RECOMMENDED_FIELDS: Dict[str, List[str]] = {
    "Product": ["image", "description", "brand", "sku"],
    "Article": ["image", "dateModified", "publisher"],
    "Organization": ["url", "logo", "sameAs"],
    "LocalBusiness": ["openingHoursSpecification", "geo", "url"],
    "Event": ["description", "image", "offers"],
    "FAQPage": [],  # mainEntity is sufficient
}

# Which schema types are expected on which page type (heuristic)
PAGE_TYPE_SCHEMA_MAP: Dict[str, List[str]] = {
    "homepage": ["Organization", "WebSite"],
    "blog": ["Article", "BlogPosting"],
    "product": ["Product"],
    "faq": ["FAQPage"],
    "contact": ["LocalBusiness", "ContactPage"],
    "event": ["Event"],
}


class SchemaValidator:
    """Validates JSON-LD structured data."""

    def validate_schema_block(self, schema: Dict[str, Any], url: str) -> Dict[str, Any]:
        """Validates a single JSON-LD block."""
        result = {
            "url": url,
            "schema_type": "",
            "is_valid": True,
            "missing_required": [],
            "missing_recommended": [],
            "errors": [],
        }

        # Check @type
        schema_type = schema.get("@type")
        if not schema_type:
            result["is_valid"] = False
            result["errors"].append("No @type defined")
            return result

        # Normalize @type (can be string or list)
        if isinstance(schema_type, list):
            schema_type = schema_type[0] if schema_type else ""
        result["schema_type"] = schema_type

        # Check required fields
        required = REQUIRED_FIELDS.get(schema_type, [])
        for field_name in required:
            if not _has_field(schema, field_name):
                result["missing_required"].append(field_name)
                result["is_valid"] = False

        # Check recommended fields
        recommended = RECOMMENDED_FIELDS.get(schema_type, [])
        for field_name in recommended:
            if not _has_field(schema, field_name):
                result["missing_recommended"].append(field_name)

        # Special checks
        if schema_type == "FAQPage":
            self._validate_faq(schema, result)
        elif schema_type in ("Product",):
            self._validate_product(schema, result)
        elif schema_type == "BreadcrumbList":
            self._validate_breadcrumb(schema, result)

        return result

    def validate_page(
        self,
        url: str,
        schema_data: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Validates all JSON-LD blocks of a page."""
        results = []
        for schema in schema_data:
            results.append(self.validate_schema_block(schema, url))
        return results

    def detect_issues(
        self,
        pages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Detects schema issues for a list of pages.

        Args:
            pages: List of dicts containing at least 'url' and 'schema_data'.
        """
        issues: List[Dict[str, Any]] = []

        for page in pages:
            url = page.get("url", "")
            schema_data = page.get("schema_data", [])

            # No schemas present (already checked by the analyzer, skip here)

            for schema in schema_data:
                # Syntax check (if schema_data comes from the crawler it is already parsed)
                schema_type = schema.get("@type", "")
                if isinstance(schema_type, list):
                    schema_type = schema_type[0] if schema_type else ""

                if not schema_type:
                    issues.append(_schema_issue(
                        "schema_syntax_error", "high", url,
                        "JSON-LD block without @type",
                        "A JSON-LD block has no @type — will be ignored by Google.",
                        "Add @type (e.g. Organization, Article, Product).",
                    ))
                    continue

                # Required fields
                result = self.validate_schema_block(schema, url)
                if result["missing_required"]:
                    missing = ", ".join(result["missing_required"])
                    issues.append(_schema_issue(
                        "schema_missing_required_field", "medium", url,
                        f"{schema_type}: required fields missing ({missing})",
                        f"Schema @type={schema_type} is missing: {missing}. "
                        f"Without these fields no rich result is possible.",
                        f"Add the missing fields: {missing}.",
                    ))

                # Errors from special checks
                for error in result.get("errors", []):
                    issues.append(_schema_issue(
                        "schema_syntax_error", "high", url,
                        f"{schema_type}: {error}",
                        error,
                        "Fix the JSON-LD structure.",
                    ))

            # Rich result opportunities
            schema_types_on_page = set()
            for s in schema_data:
                t = s.get("@type")
                if isinstance(t, list):
                    schema_types_on_page.update(t)
                elif t:
                    schema_types_on_page.add(t)

            opportunities = self._find_opportunities(url, schema_types_on_page, page)
            issues.extend(opportunities)

        return issues

    def _validate_faq(self, schema: Dict, result: Dict) -> None:
        """FAQPage needs mainEntity with Question/Answer pairs."""
        main_entity = schema.get("mainEntity")
        if not main_entity:
            return
        if not isinstance(main_entity, list):
            main_entity = [main_entity]
        for i, item in enumerate(main_entity):
            if not isinstance(item, dict):
                result["errors"].append(f"mainEntity[{i}] is not an object")
                continue
            if item.get("@type") != "Question":
                result["errors"].append(f"mainEntity[{i}] is not @type=Question")
            if not item.get("name") and not item.get("text"):
                result["errors"].append(f"mainEntity[{i}] has no question (name/text)")
            accepted = item.get("acceptedAnswer", {})
            if not isinstance(accepted, dict) or not accepted.get("text"):
                result["errors"].append(f"mainEntity[{i}] has no acceptedAnswer.text")

    def _validate_product(self, schema: Dict, result: Dict) -> None:
        """Product.offers needs price or priceRange."""
        offers = schema.get("offers")
        if not offers:
            return
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            if not offers.get("price") and not offers.get("priceRange"):
                result["errors"].append("offers has neither price nor priceRange")

    def _validate_breadcrumb(self, schema: Dict, result: Dict) -> None:
        """BreadcrumbList.itemListElement needs position."""
        items = schema.get("itemListElement", [])
        if not isinstance(items, list):
            result["errors"].append("itemListElement is not a list")
            return
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if "position" not in item:
                result["errors"].append(f"itemListElement[{i}] has no position")

    def _find_opportunities(
        self, url: str, existing_types: set, page: Dict
    ) -> List[Dict[str, Any]]:
        """Finds rich result opportunities based on page content."""
        issues = []

        # BreadcrumbList missing (almost always useful)
        if "BreadcrumbList" not in existing_types and "/" in url.replace("://", ""):
            path_depth = url.rstrip("/").count("/") - 2  # minus scheme://domain
            if path_depth > 0:
                issues.append(_schema_issue(
                    "schema_rich_result_opportunity", "info", url,
                    "BreadcrumbList schema missing",
                    "Page has URL depth > 1 but no BreadcrumbList schema.",
                    "Add BreadcrumbList JSON-LD for breadcrumb rich results in Google.",
                ))

        return issues


def _has_field(schema: Dict, field_name: str) -> bool:
    """Checks whether a field exists in the schema and is not empty."""
    value = schema.get(field_name)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, list) and not value:
        return False
    return True


def _schema_issue(type_: str, severity: str, url: str,
                  title: str, description: str, fix: str) -> Dict[str, Any]:
    return {
        "category": "schema",
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": url,
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": "",
    }
