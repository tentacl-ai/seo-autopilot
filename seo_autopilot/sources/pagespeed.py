"""
Google PageSpeed Insights API Data Source.

Fetches Lighthouse scores and Core Web Vitals for each page.
Free tier: 400 requests/day with API key, very limited without.

Usage in projects.yaml:
    source_config:
      pagespeed:
        api_key: "AIza..."       # optional, higher quota
        strategy: "mobile"       # mobile | desktop (default: mobile)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
DEFAULT_CATEGORIES = ["performance", "seo", "accessibility", "best-practices"]
TIMEOUT = 60.0  # Lighthouse runs can be slow


@dataclass
class PageSpeedResult:
    """Lighthouse result for a single URL."""
    url: str
    strategy: str = "mobile"
    performance_score: Optional[int] = None
    seo_score: Optional[int] = None
    accessibility_score: Optional[int] = None
    best_practices_score: Optional[int] = None

    # Core Web Vitals
    lcp_ms: Optional[float] = None          # Largest Contentful Paint (ms)
    lcp_display: Optional[str] = None
    cls: Optional[float] = None             # Cumulative Layout Shift
    cls_display: Optional[str] = None
    tbt_ms: Optional[float] = None          # Total Blocking Time (ms, proxy for INP)
    tbt_display: Optional[str] = None
    fcp_ms: Optional[float] = None          # First Contentful Paint (ms)
    fcp_display: Optional[str] = None
    si_ms: Optional[float] = None           # Speed Index (ms)
    si_display: Optional[str] = None
    tti_ms: Optional[float] = None          # Time to Interactive (ms)
    tti_display: Optional[str] = None

    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


async def fetch_pagespeed(
    url: str,
    api_key: Optional[str] = None,
    strategy: str = "mobile",
    categories: Optional[List[str]] = None,
) -> PageSpeedResult:
    """
    Fetch PageSpeed Insights for a single URL.

    Returns PageSpeedResult with Lighthouse scores and Core Web Vitals.
    On error, returns a result with just the error field set.
    """
    result = PageSpeedResult(url=url, strategy=strategy)
    cats = categories or DEFAULT_CATEGORIES

    params: Dict[str, Any] = {
        "url": url,
        "strategy": strategy,
    }
    for c in cats:
        params.setdefault("category", [])
        # httpx handles list params correctly
    # Actually httpx needs repeated params as list of tuples
    param_list = [("url", url), ("strategy", strategy)]
    for c in cats:
        param_list.append(("category", c))
    if api_key:
        param_list.append(("key", api_key))

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(PSI_ENDPOINT, params=param_list)

        if resp.status_code == 429:
            result.error = "Rate limited (429). Set pagespeed.api_key in source_config for higher quota."
            logger.warning(f"PageSpeed rate limited for {url}")
            return result

        if resp.status_code != 200:
            result.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            logger.warning(f"PageSpeed error for {url}: {result.error}")
            return result

        data = resp.json()
        lr = data.get("lighthouseResult", {})

        # Category scores (0-100)
        categories_data = lr.get("categories", {})
        if "performance" in categories_data:
            result.performance_score = int(categories_data["performance"]["score"] * 100)
        if "seo" in categories_data:
            result.seo_score = int(categories_data["seo"]["score"] * 100)
        if "accessibility" in categories_data:
            result.accessibility_score = int(categories_data["accessibility"]["score"] * 100)
        if "best-practices" in categories_data:
            result.best_practices_score = int(categories_data["best-practices"]["score"] * 100)

        # Core Web Vitals from audits
        audits = lr.get("audits", {})
        _extract_metric(result, audits, "largest-contentful-paint", "lcp")
        _extract_metric(result, audits, "cumulative-layout-shift", "cls")
        _extract_metric(result, audits, "total-blocking-time", "tbt")
        _extract_metric(result, audits, "first-contentful-paint", "fcp")
        _extract_metric(result, audits, "speed-index", "si")
        _extract_metric(result, audits, "interactive", "tti")

        logger.info(
            f"PageSpeed {strategy} {url}: perf={result.performance_score} "
            f"seo={result.seo_score} LCP={result.lcp_display} CLS={result.cls_display}"
        )

    except httpx.TimeoutException:
        result.error = f"Timeout after {TIMEOUT}s"
        logger.warning(f"PageSpeed timeout for {url}")
    except Exception as exc:
        result.error = str(exc)
        logger.warning(f"PageSpeed error for {url}: {exc}")

    return result


async def fetch_pagespeed_batch(
    urls: List[str],
    api_key: Optional[str] = None,
    strategy: str = "mobile",
    concurrency: int = 2,
) -> List[PageSpeedResult]:
    """
    Fetch PageSpeed for multiple URLs with concurrency limit.
    PSI API is slow (~10-30s per request), so limit parallelism.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _limited(url: str) -> PageSpeedResult:
        async with semaphore:
            return await fetch_pagespeed(url, api_key=api_key, strategy=strategy)

    return await asyncio.gather(*[_limited(u) for u in urls])


def _extract_metric(result: PageSpeedResult, audits: Dict, audit_key: str, prefix: str):
    """Extract a Lighthouse metric into the result object."""
    audit = audits.get(audit_key)
    if not audit:
        return
    display = audit.get("displayValue")
    numeric = audit.get("numericValue")
    if display:
        setattr(result, f"{prefix}_display", display)
    if numeric is not None:
        setattr(result, f"{prefix}_ms", round(numeric, 2))
