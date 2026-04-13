"""
Google PageSpeed Insights API Data Source.

Fetches Lighthouse scores and Core Web Vitals for each page.
Free tier: 400 requests/day with API key, very limited without.

Returns both lab data (Lighthouse) and field data (CrUX / Chrome UX Report)
when available. Field data = real user metrics, lab data = synthetic.

CWV thresholds (April 2026):
- LCP:  good < 2.5s | needs improvement < 4s | poor >= 4s
- CLS:  good < 0.1  | needs improvement < 0.25 | poor >= 0.25
- INP:  good < 200ms | needs improvement < 500ms | poor >= 500ms
- FID:  DEPRECATED since March 2024 — not collected

Usage in projects.yaml:
    source_config:
      pagespeed:
        api_key: "AIza..."       # optional, higher quota
        strategy: "mobile"       # mobile | desktop (default: mobile)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
DEFAULT_CATEGORIES = ["performance", "seo", "accessibility", "best-practices"]
TIMEOUT = 60.0

# CWV-Schwellenwerte (verifiziert April 2026)
CWV_THRESHOLDS = {
    "lcp": {"good": 2500, "poor": 4000, "unit": "ms"},
    "cls": {"good": 0.1, "poor": 0.25, "unit": "score"},
    "inp": {"good": 200, "poor": 500, "unit": "ms"},
    "fcp": {"good": 1800, "poor": 3000, "unit": "ms"},
    "ttfb": {"good": 800, "poor": 1800, "unit": "ms"},
}


@dataclass
class PageSpeedResult:
    """Lighthouse + CrUX result for a single URL."""

    url: str
    strategy: str = "mobile"

    # Lighthouse category scores (0-100)
    performance_score: Optional[int] = None
    seo_score: Optional[int] = None
    accessibility_score: Optional[int] = None
    best_practices_score: Optional[int] = None

    # Lab data (Lighthouse synthetic)
    lcp_ms: Optional[float] = None
    lcp_display: Optional[str] = None
    cls: Optional[float] = None
    cls_display: Optional[str] = None
    tbt_ms: Optional[float] = None
    tbt_display: Optional[str] = None
    fcp_ms: Optional[float] = None
    fcp_display: Optional[str] = None
    si_ms: Optional[float] = None
    si_display: Optional[str] = None
    tti_ms: Optional[float] = None
    tti_display: Optional[str] = None

    # Field data (CrUX — real user metrics, only available for popular pages)
    crux_lcp_ms: Optional[float] = None
    crux_lcp_rating: Optional[str] = None
    crux_cls: Optional[float] = None
    crux_cls_rating: Optional[str] = None
    crux_inp_ms: Optional[float] = None
    crux_inp_rating: Optional[str] = None
    crux_fcp_ms: Optional[float] = None
    crux_fcp_rating: Optional[str] = None
    crux_ttfb_ms: Optional[float] = None
    crux_ttfb_rating: Optional[str] = None
    has_field_data: bool = False

    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    def get_cwv_summary(self) -> Dict[str, Any]:
        """Zusammenfassung der Core Web Vitals mit Rating."""
        summary = {}
        # Bevorzuge Field-Daten (echte User), Fallback auf Lab-Daten
        if self.crux_lcp_ms is not None:
            summary["lcp"] = {
                "value": self.crux_lcp_ms,
                "rating": self.crux_lcp_rating,
                "source": "field",
            }
        elif self.lcp_ms is not None:
            summary["lcp"] = {
                "value": self.lcp_ms,
                "rating": rate_metric("lcp", self.lcp_ms),
                "source": "lab",
            }

        if self.crux_cls is not None:
            summary["cls"] = {
                "value": self.crux_cls,
                "rating": self.crux_cls_rating,
                "source": "field",
            }
        elif self.cls is not None:
            summary["cls"] = {
                "value": self.cls,
                "rating": rate_metric("cls", self.cls),
                "source": "lab",
            }

        if self.crux_inp_ms is not None:
            summary["inp"] = {
                "value": self.crux_inp_ms,
                "rating": self.crux_inp_rating,
                "source": "field",
            }
        # INP hat kein Lab-Equivalent (TBT ist nur Proxy)

        return summary


def rate_metric(metric: str, value: float) -> str:
    """Bewerte einen CWV-Wert als good/needs-improvement/poor."""
    thresholds = CWV_THRESHOLDS.get(metric)
    if not thresholds:
        return "unknown"
    if value <= thresholds["good"]:
        return "good"
    elif value < thresholds["poor"]:
        return "needs-improvement"
    else:
        return "poor"


async def fetch_pagespeed(
    url: str,
    api_key: Optional[str] = None,
    strategy: str = "mobile",
    categories: Optional[List[str]] = None,
) -> PageSpeedResult:
    """
    Fetch PageSpeed Insights for a single URL.

    Returns PageSpeedResult with Lighthouse scores, lab CWV, and CrUX field data.
    On error, returns a result with just the error field set.
    """
    result = PageSpeedResult(url=url, strategy=strategy)
    cats = categories or DEFAULT_CATEGORIES

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
            result.performance_score = int(
                categories_data["performance"]["score"] * 100
            )
        if "seo" in categories_data:
            result.seo_score = int(categories_data["seo"]["score"] * 100)
        if "accessibility" in categories_data:
            result.accessibility_score = int(
                categories_data["accessibility"]["score"] * 100
            )
        if "best-practices" in categories_data:
            result.best_practices_score = int(
                categories_data["best-practices"]["score"] * 100
            )

        # Lab data from Lighthouse audits
        audits = lr.get("audits", {})
        _extract_metric(result, audits, "largest-contentful-paint", "lcp")
        _extract_metric(result, audits, "cumulative-layout-shift", "cls")
        _extract_metric(result, audits, "total-blocking-time", "tbt")
        _extract_metric(result, audits, "first-contentful-paint", "fcp")
        _extract_metric(result, audits, "speed-index", "si")
        _extract_metric(result, audits, "interactive", "tti")

        # CrUX Field Data (loadingExperience — real user metrics)
        _extract_crux_data(result, data)

        logger.info(
            f"PageSpeed {strategy} {url}: perf={result.performance_score} "
            f"LCP={result.lcp_display} CLS={result.cls_display} "
            f"INP={'%sms' % int(result.crux_inp_ms) if result.crux_inp_ms else 'n/a'} "
            f"field_data={result.has_field_data}"
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


# CrUX metric keys in PSI response
_CRUX_METRIC_MAP = {
    "LARGEST_CONTENTFUL_PAINT_MS": ("crux_lcp_ms", "crux_lcp_rating", "lcp"),
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": ("crux_cls", "crux_cls_rating", "cls"),
    "INTERACTION_TO_NEXT_PAINT": ("crux_inp_ms", "crux_inp_rating", "inp"),
    "FIRST_CONTENTFUL_PAINT_MS": ("crux_fcp_ms", "crux_fcp_rating", "fcp"),
    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": ("crux_ttfb_ms", "crux_ttfb_rating", "ttfb"),
}


def _extract_crux_data(result: PageSpeedResult, data: Dict) -> None:
    """Extract CrUX field data from loadingExperience."""
    le = data.get("loadingExperience", {})
    metrics = le.get("metrics", {})

    if not metrics:
        return

    for psi_key, (value_attr, rating_attr, metric_name) in _CRUX_METRIC_MAP.items():
        metric_data = metrics.get(psi_key, {})
        percentile = metric_data.get("percentile")
        category = metric_data.get("category", "").lower().replace("_", "-")

        if percentile is not None:
            # CLS percentile kommt als integer * 100 (z.B. 10 = 0.10)
            if metric_name == "cls":
                setattr(result, value_attr, round(percentile / 100, 3))
            else:
                setattr(result, value_attr, float(percentile))
            result.has_field_data = True

        if category:
            # PSI gibt "FAST", "AVERAGE", "SLOW" — wir normalisieren
            rating_map = {
                "fast": "good",
                "average": "needs-improvement",
                "slow": "poor",
            }
            setattr(result, rating_attr, rating_map.get(category, category))
