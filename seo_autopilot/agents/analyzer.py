"""
AnalyzerAgent: Real HTTP crawler + HTML analyzer.

Uses SEOCrawler to fetch pages, parses HTML with BeautifulSoup and
detects real SEO issues:
- Missing/short/long titles
- Missing meta descriptions
- Missing H1s, multiple H1s
- Missing canonical / noindex
- Missing Open Graph / Twitter cards
- Missing viewport / lang
- Images without alt
- Missing schema.org (Organization on homepage)
- HTTP (not HTTPS)
- Missing security headers
- Slow response time
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.event_bus import EventType
from ..sources.crawler import PageData, SEOCrawler
from ..sources.pagespeed import PageSpeedResult, fetch_pagespeed_batch
from .base import Agent, AgentResult, AgentStatus

logger = logging.getLogger(__name__)

# Thresholds (doc-only magic numbers are avoided: central here for easy tuning)
TITLE_MIN = 20
TITLE_MAX = 65
META_MIN = 80
META_MAX = 165
SLOW_RESPONSE_MS = 2500

REQUIRED_SECURITY_HEADERS = [
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
]


class AnalyzerAgent(Agent):
    """Real HTTP crawler + SEO analyzer."""

    @property
    def name(self) -> str:
        return "analyzer"

    @property
    def event_type(self) -> EventType:
        return EventType.ANALYZER_COMPLETED

    async def run(self) -> AgentResult:
        start_time = datetime.utcnow()
        result = AgentResult(
            status=AgentStatus.RUNNING,
            agent_name=self.name,
            project_id=self.project_id,
            audit_id=self.audit_id,
        )

        domain = self.project_config.domain.rstrip("/")
        max_pages = int(
            (self.project_config.adapter_config or {}).get("max_pages", 15)
        )

        try:
            await self.emit_started()

            async with SEOCrawler(concurrency=4) as crawler:
                urls = await crawler.discover_pages(domain, limit=max_pages)
                logger.info(f"[analyzer] discovered {len(urls)} URLs on {domain}")
                pages = await crawler.crawl(urls)

            good_pages = [p for p in pages if p.status_code == 200]

            issues: List[Dict[str, Any]] = []
            issues.extend(self._check_fetch_errors(pages))
            issues.extend(self._check_meta(good_pages))
            issues.extend(self._check_headings(good_pages))
            issues.extend(self._check_social(good_pages))
            issues.extend(self._check_images(good_pages))
            issues.extend(self._check_schema(good_pages, domain))
            issues.extend(self._check_security(good_pages))
            issues.extend(self._check_performance(good_pages))
            issues.extend(self._check_canonical(good_pages))

            # PageSpeed Insights (optional, runs only for homepage + top pages)
            psi_results = await self._run_pagespeed(domain, urls[:3])
            if psi_results:
                issues.extend(self._check_core_web_vitals(psi_results))

            # Snapshot metrics
            total_images = sum(p.images_total for p in good_pages)
            missing_alt = sum(p.images_without_alt for p in good_pages)
            avg_response_ms = int(
                sum(p.fetch_ms for p in good_pages) / max(len(good_pages), 1)
            )

            result.metrics.update({
                "pages_discovered": len(urls),
                "pages_crawled": len(pages),
                "pages_ok": len(good_pages),
                "pages_failed": len(pages) - len(good_pages),
                "avg_response_ms": avg_response_ms,
                "total_images": total_images,
                "images_without_alt": missing_alt,
                "total_issues": len(issues),
            })

            # PageSpeed metrics (if available)
            if psi_results:
                psi_ok = [r for r in psi_results if not r.error]
                if psi_ok:
                    result.metrics["pagespeed"] = [r.to_dict() for r in psi_ok]
                    result.metrics["lighthouse_performance"] = psi_ok[0].performance_score
                    result.metrics["lighthouse_seo"] = psi_ok[0].seo_score
                    result.metrics["lighthouse_accessibility"] = psi_ok[0].accessibility_score

            # Store raw page data in metrics so downstream agents can use it
            result.metrics["pages"] = [_page_snapshot(p) for p in pages]

            result.issues = issues
            result.status = AgentStatus.COMPLETED
            result.log_output = (
                f"Analyzed {len(good_pages)}/{len(pages)} pages on {domain}, "
                f"found {len(issues)} issues"
            )
            logger.info(result.log_output)

        except Exception as exc:  # pragma: no cover - defensive
            result.status = AgentStatus.FAILED
            result.errors.append(str(exc))
            result.log_output = f"Analyzer failed: {exc}"
            logger.exception("Analyzer error")

        finally:
            result.duration_seconds = (datetime.utcnow() - start_time).total_seconds()
            await self.emit_result(result)

        return result

    # ------------------------------------------------------------------
    # Issue detectors
    # ------------------------------------------------------------------

    def _check_fetch_errors(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if p.error:
                issues.append({
                    "category": "crawl",
                    "type": "fetch_error",
                    "severity": "high",
                    "title": f"Fetch failed: {p.url}",
                    "affected_url": p.url,
                    "description": p.error,
                    "fix_suggestion": "Check DNS, firewall, server availability.",
                })
                continue
            if p.status_code >= 400:
                issues.append({
                    "category": "crawl",
                    "type": f"http_{p.status_code}",
                    "severity": "high" if p.status_code >= 500 else "medium",
                    "title": f"HTTP {p.status_code} on {p.url}",
                    "affected_url": p.url,
                    "description": f"Server returned {p.status_code}",
                    "fix_suggestion": "Fix the broken page or return a proper redirect.",
                })
        return issues

    def _check_meta(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            # Title
            t = (p.title or "").strip()
            if not t:
                issues.append(_issue("meta", "missing_title", "high", p.url,
                    "Missing page title",
                    "Page has no <title> element",
                    "Add a unique, descriptive <title> (50-60 chars)."))
            elif len(t) < TITLE_MIN:
                issues.append(_issue("meta", "short_title", "medium", p.url,
                    f"Title too short ({len(t)} chars)",
                    f"Current title: {t!r}",
                    f"Expand title to at least {TITLE_MIN} chars, ideally 50-60."))
            elif len(t) > TITLE_MAX:
                issues.append(_issue("meta", "long_title", "low", p.url,
                    f"Title too long ({len(t)} chars)",
                    f"Current title: {t!r}",
                    f"Shorten title to below {TITLE_MAX} chars so it isn't truncated in SERP."))

            # Meta description
            d = (p.meta_description or "").strip()
            if not d:
                issues.append(_issue("meta", "missing_meta_description", "high", p.url,
                    "Missing meta description",
                    "Page has no meta description",
                    "Add a meta description of 120-160 chars."))
            elif len(d) < META_MIN:
                issues.append(_issue("meta", "short_meta_description", "low", p.url,
                    f"Meta description too short ({len(d)} chars)",
                    f"Current: {d!r}",
                    f"Expand to at least {META_MIN} chars."))
            elif len(d) > META_MAX:
                issues.append(_issue("meta", "long_meta_description", "low", p.url,
                    f"Meta description too long ({len(d)} chars)",
                    f"Current: {d!r}",
                    f"Shorten to below {META_MAX} chars."))

            # Viewport
            if not p.viewport:
                issues.append(_issue("meta", "missing_viewport", "medium", p.url,
                    "Missing viewport meta tag",
                    "Page is likely not mobile-friendly",
                    'Add <meta name="viewport" content="width=device-width, initial-scale=1">.'))

            # lang
            if not p.lang:
                issues.append(_issue("meta", "missing_html_lang", "low", p.url,
                    "Missing <html lang> attribute",
                    "Search engines can't determine language",
                    'Add lang attribute to <html>, e.g. lang="de".'))

            # robots noindex - downgrade severity since legal pages are intentionally noindex
            if p.robots_meta and "noindex" in p.robots_meta.lower():
                is_legal = any(k in p.url.lower() for k in ("impressum", "datenschutz", "privacy", "terms", "agb"))
                issues.append(_issue(
                    "meta", "noindex_detected",
                    "low" if is_legal else "high",
                    p.url,
                    "Page is set to noindex" + (" (likely intentional)" if is_legal else ""),
                    f"robots meta: {p.robots_meta}",
                    "Verify this is intentional. Legal pages (impressum, datenschutz) are fine; public content pages must be indexed.",
                ))

        return issues

    def _check_headings(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if len(p.h1) == 0:
                issues.append(_issue("content", "missing_h1", "medium", p.url,
                    "Missing H1 heading",
                    "Page has no H1 element",
                    "Add exactly one descriptive H1 tag."))
            elif len(p.h1) > 1:
                issues.append(_issue("content", "multiple_h1", "low", p.url,
                    f"Multiple H1 elements ({len(p.h1)})",
                    f"H1s: {p.h1[:3]}",
                    "Use exactly one H1 per page; use H2 for sub-sections."))
        return issues

    def _check_social(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if not p.og_tags.get("og:title"):
                issues.append(_issue("social", "missing_og_title", "low", p.url,
                    "Missing og:title",
                    "No Open Graph title for social shares",
                    'Add <meta property="og:title" content="...">.'))
            if not p.og_tags.get("og:image"):
                issues.append(_issue("social", "missing_og_image", "medium", p.url,
                    "Missing og:image",
                    "Social shares will have no preview image",
                    'Add <meta property="og:image" content="https://.../og.png">.'))
            if not p.twitter_tags.get("twitter:card"):
                issues.append(_issue("social", "missing_twitter_card", "low", p.url,
                    "Missing twitter:card",
                    "Twitter / X previews will be generic",
                    'Add <meta name="twitter:card" content="summary_large_image">.'))
        return issues

    def _check_images(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if p.images_without_alt > 0:
                issues.append(_issue("accessibility", "images_without_alt",
                    "medium" if p.images_without_alt > 3 else "low", p.url,
                    f"{p.images_without_alt} images without alt text",
                    f"Page has {p.images_total} images, {p.images_without_alt} missing alt",
                    "Add descriptive alt attributes to every meaningful image."))
        return issues

    def _check_schema(self, pages: List[PageData], domain: str) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            is_home = p.final_url.rstrip("/") == domain.rstrip("/") or p.url == domain
            if is_home and "Organization" not in p.schema_types and "WebSite" not in p.schema_types:
                issues.append(_issue("schema", "missing_organization_schema", "medium", p.url,
                    "Homepage missing Organization schema",
                    "Homepage has no Organization or WebSite JSON-LD",
                    "Add JSON-LD with @type Organization including name, url, logo, sameAs."))
            if not p.schema_types:
                issues.append(_issue("schema", "no_jsonld", "low", p.url,
                    "No JSON-LD structured data",
                    "Page has no schema.org markup",
                    "Add relevant schema.org JSON-LD (Article, Service, Product, etc.)."))
        return issues

    def _check_security(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        seen_domains: set = set()
        for p in pages:
            if not p.https:
                issues.append(_issue("security", "no_https", "high", p.url,
                    "Page not served via HTTPS",
                    f"URL: {p.url}",
                    "Configure HTTPS and redirect HTTP -> HTTPS."))

            # Only report security headers once per origin (first page wins)
            from urllib.parse import urlparse
            origin = urlparse(p.final_url or p.url).netloc
            if origin in seen_domains:
                continue
            seen_domains.add(origin)

            missing = [h for h in REQUIRED_SECURITY_HEADERS if h not in p.security_headers]
            if missing:
                issues.append(_issue("security", "missing_security_headers",
                    "medium", p.url,
                    f"Missing security headers: {', '.join(missing)}",
                    f"Present: {list(p.security_headers)}",
                    "Add headers via nginx: Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options."))
        return issues

    def _check_performance(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if p.fetch_ms > SLOW_RESPONSE_MS:
                issues.append(_issue("performance", "slow_response", "medium", p.url,
                    f"Slow response ({p.fetch_ms} ms)",
                    f"Page responded in {p.fetch_ms} ms (target < {SLOW_RESPONSE_MS} ms)",
                    "Reduce TTFB: cache aggressively, optimize backend, use CDN."))
        return issues

    async def _run_pagespeed(self, domain: str, urls: List[str]) -> List[PageSpeedResult]:
        """Run PageSpeed Insights on top pages. Non-fatal on failure."""
        source_cfg = (self.project_config.source_config or {}).get("pagespeed", {})
        api_key = source_cfg.get("api_key")
        strategy = source_cfg.get("strategy", "mobile")

        try:
            results = await fetch_pagespeed_batch(
                urls, api_key=api_key, strategy=strategy, concurrency=2
            )
            ok = [r for r in results if not r.error]
            if ok:
                logger.info(f"[analyzer] PageSpeed: {len(ok)}/{len(urls)} pages scored")
            else:
                errors = [r.error for r in results if r.error]
                logger.info(f"[analyzer] PageSpeed unavailable: {errors[0][:80] if errors else 'unknown'}")
            return results
        except Exception as exc:
            logger.warning(f"[analyzer] PageSpeed failed (non-fatal): {exc}")
            return []

    def _check_core_web_vitals(self, psi_results: List[PageSpeedResult]) -> List[Dict[str, Any]]:
        """Detect Core Web Vitals issues from PageSpeed results."""
        issues = []
        for r in psi_results:
            if r.error:
                continue

            # Performance score
            if r.performance_score is not None and r.performance_score < 50:
                issues.append(_issue("performance", "poor_lighthouse_performance", "high", r.url,
                    f"Poor Lighthouse performance score ({r.performance_score}/100, {r.strategy})",
                    f"Performance: {r.performance_score}, LCP: {r.lcp_display}, TBT: {r.tbt_display}, CLS: {r.cls_display}",
                    "Optimize images (WebP/AVIF), defer non-critical JS/CSS, reduce server response time.",
                    f"Target: 90+ performance score"))
            elif r.performance_score is not None and r.performance_score < 90:
                issues.append(_issue("performance", "moderate_lighthouse_performance", "medium", r.url,
                    f"Moderate Lighthouse performance ({r.performance_score}/100, {r.strategy})",
                    f"Performance: {r.performance_score}, LCP: {r.lcp_display}, TBT: {r.tbt_display}",
                    "Review render-blocking resources, optimize images, enable text compression."))

            # LCP > 2.5s = poor (Google threshold)
            if r.lcp_ms is not None and r.lcp_ms > 2500:
                sev = "high" if r.lcp_ms > 4000 else "medium"
                issues.append(_issue("performance", "poor_lcp", sev, r.url,
                    f"Poor LCP: {r.lcp_display} ({r.strategy})",
                    f"Largest Contentful Paint is {r.lcp_ms:.0f}ms (target: <2500ms)",
                    "Optimize hero image (compress, use WebP, preload), reduce TTFB, eliminate render-blocking CSS."))

            # CLS > 0.1 = needs improvement, > 0.25 = poor
            if r.cls is not None and r.cls > 0.1:
                sev = "high" if r.cls > 0.25 else "medium"
                issues.append(_issue("performance", "poor_cls", sev, r.url,
                    f"Poor CLS: {r.cls_display} ({r.strategy})",
                    f"Cumulative Layout Shift is {r.cls:.3f} (target: <0.1)",
                    "Set explicit width/height on images/videos, avoid injecting content above the fold."))

            # TBT > 200ms = needs improvement, > 600ms = poor
            if r.tbt_ms is not None and r.tbt_ms > 200:
                sev = "high" if r.tbt_ms > 600 else "medium"
                issues.append(_issue("performance", "poor_tbt", sev, r.url,
                    f"High TBT: {r.tbt_display} ({r.strategy})",
                    f"Total Blocking Time is {r.tbt_ms:.0f}ms (target: <200ms)",
                    "Split long tasks, defer non-critical JavaScript, use web workers."))

            # Lighthouse SEO score
            if r.seo_score is not None and r.seo_score < 90:
                issues.append(_issue("meta", "low_lighthouse_seo", "medium", r.url,
                    f"Lighthouse SEO score: {r.seo_score}/100 ({r.strategy})",
                    f"Google Lighthouse rates the SEO at {r.seo_score}/100",
                    "Check Lighthouse report for specific recommendations (crawlable links, valid robots.txt, etc)."))

            # Accessibility
            if r.accessibility_score is not None and r.accessibility_score < 80:
                issues.append(_issue("accessibility", "low_accessibility", "medium", r.url,
                    f"Low accessibility score: {r.accessibility_score}/100 ({r.strategy})",
                    f"Lighthouse accessibility: {r.accessibility_score}/100",
                    "Add ARIA labels, ensure sufficient color contrast, check keyboard navigation."))

        return issues

    def _check_canonical(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if not p.canonical:
                issues.append(_issue("meta", "missing_canonical", "low", p.url,
                    "Missing canonical link",
                    "Page has no rel=canonical",
                    'Add <link rel="canonical" href="..."> to prevent duplicate content.'))
        return issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _issue(category: str, type_: str, severity: str, url: str,
           title: str, description: str, fix: str,
           impact: Optional[str] = None) -> Dict[str, Any]:
    return {
        "category": category,
        "type": type_,
        "severity": severity,
        "title": title,
        "affected_url": url,
        "description": description,
        "fix_suggestion": fix,
        "estimated_impact": impact or "",
    }


def _page_snapshot(p: PageData) -> Dict[str, Any]:
    """Small snapshot used by downstream agents + reports."""
    return {
        "url": p.url,
        "final_url": p.final_url,
        "status_code": p.status_code,
        "fetch_ms": p.fetch_ms,
        "title": p.title,
        "meta_description": p.meta_description,
        "h1": p.h1,
        "word_count": p.word_count,
        "schema_types": p.schema_types,
        "images_total": p.images_total,
        "images_without_alt": p.images_without_alt,
        "og_tags": p.og_tags,
        "canonical": p.canonical,
        "https": p.https,
        "security_headers": list(p.security_headers),
        "error": p.error,
    }
