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
from ..analyzers.canonical_engine import CanonicalEngine, PageCanonicalData
from ..analyzers.redirect_audit import PageForRedirectAudit, is_soft_404
from ..analyzers.schema_validation import SchemaValidator
from ..analyzers.geo_audit import GEOAuditor
from ..analyzers.topical_authority import TopicalAuthorityAnalyzer
from ..analyzers.duplicate_content import DuplicateContentDetector
from ..analyzers.link_graph import LinkGraph
from ..analyzers.robots_sitemap import RobotsSitemapAuditor
from ..analyzers.eeat import EEATAnalyzer
from .intent_geo_agent import analyze_keywords as intent_geo_analyze
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

            # --- v0.4 Analyzer: Canonical Engine ---
            try:
                sitemap_urls = set(urls)
                canonical_pages = [
                    PageCanonicalData(
                        url=p.url, final_url=p.final_url,
                        status_code=p.status_code, canonical=p.canonical,
                        robots_meta=p.robots_meta, hreflang=p.hreflang,
                    )
                    for p in pages
                ]
                canonical_engine = CanonicalEngine(sitemap_urls=sitemap_urls)
                canonical_issues = canonical_engine.detect_conflicts(canonical_pages)
                issues.extend(canonical_issues)
                logger.info(f"[analyzer] Canonical engine: {len(canonical_issues)} issues")
            except Exception as exc:
                logger.warning(f"[analyzer] Canonical engine failed (non-fatal): {exc}")

            # --- v0.4 Analyzer: Schema Validator ---
            try:
                schema_pages = [
                    {"url": p.url, "schema_data": p.schema_data}
                    for p in good_pages if p.schema_data
                ]
                schema_validator = SchemaValidator()
                schema_issues = schema_validator.detect_issues(schema_pages)
                issues.extend(schema_issues)
                logger.info(f"[analyzer] Schema validator: {len(schema_issues)} issues")
            except Exception as exc:
                logger.warning(f"[analyzer] Schema validator failed (non-fatal): {exc}")

            # --- v0.4 Analyzer: Soft-404 Detection ---
            try:
                soft_404_count = 0
                for p in good_pages:
                    page_for_audit = PageForRedirectAudit(
                        url=p.url, status_code=p.status_code,
                        title=p.title or "", h1=p.h1[0] if p.h1 else "",
                        word_count=p.word_count,
                    )
                    if is_soft_404(page_for_audit):
                        issues.append({
                            "category": "redirect",
                            "type": "soft_404",
                            "severity": "medium",
                            "title": f"Soft-404 detected: {p.url}",
                            "affected_url": p.url,
                            "description": "Page returns HTTP 200 but content indicates 'not found'.",
                            "fix_suggestion": "Return a real 404 status or fill the page with content.",
                            "estimated_impact": "",
                        })
                        soft_404_count += 1
                if soft_404_count:
                    logger.info(f"[analyzer] Soft-404: {soft_404_count} detected")
            except Exception as exc:
                logger.warning(f"[analyzer] Soft-404 detection failed (non-fatal): {exc}")

            # --- v0.5 Analyzer: GEO Audit ---
            geo_result = None
            try:
                # Fetch robots.txt for AI crawler check
                robots_txt = ""
                try:
                    import httpx as _httpx
                    async with _httpx.AsyncClient(timeout=10) as _client:
                        _resp = await _client.get(f"{domain}/robots.txt")
                        if _resp.status_code == 200:
                            robots_txt = _resp.text
                except Exception as exc:
                    logger.debug(f"[analyzer] robots.txt fetch failed (non-fatal): {exc}")

                geo_pages = [_page_snapshot(p) for p in good_pages]
                geo_auditor = GEOAuditor(robots_txt_content=robots_txt)
                geo_result = geo_auditor.analyze_site(geo_pages)
                issues.extend(geo_result["issues"])
                logger.info(
                    f"[analyzer] GEO audit: avg_score={geo_result['avg_geo_score']}, "
                    f"{len(geo_result['issues'])} issues"
                )
            except Exception as exc:
                logger.warning(f"[analyzer] GEO audit failed (non-fatal): {exc}")

            # --- v0.5 Analyzer: Topical Authority ---
            topical_clusters = []
            try:
                ta_pages = [_page_snapshot(p) for p in good_pages]
                ta_analyzer = TopicalAuthorityAnalyzer()
                topical_clusters = ta_analyzer.detect_clusters(ta_pages)
                ta_issues = ta_analyzer.detect_issues(topical_clusters, ta_pages)
                issues.extend(ta_issues)
                logger.info(
                    f"[analyzer] Topical authority: {len(topical_clusters)} clusters, "
                    f"{len(ta_issues)} issues"
                )
            except Exception as exc:
                logger.warning(f"[analyzer] Topical authority failed (non-fatal): {exc}")

            # --- v0.5 Analyzer: Duplicate Content ---
            try:
                # Canonical pairs from the canonical engine for dedup awareness
                canonical_pairs = set()
                if canonical_engine:
                    resolutions = canonical_engine.resolve_all(canonical_pages)
                    for url_key, res in resolutions.items():
                        if res.resolved_canonical and res.resolved_canonical != url_key:
                            canonical_pairs.add((url_key, res.resolved_canonical))

                # Cluster URLs for cluster awareness
                cluster_url_map = {}
                for cluster in topical_clusters:
                    cluster_url_map[cluster.cluster_id] = set(cluster.cluster_urls)

                dup_detector = DuplicateContentDetector(
                    canonical_pairs=canonical_pairs,
                    cluster_urls=cluster_url_map,
                )
                dup_pages = [_page_snapshot(p) for p in good_pages]
                dup_issues = dup_detector.detect_issues(dup_pages)
                issues.extend(dup_issues)
                logger.info(f"[analyzer] Duplicate content: {len(dup_issues)} issues")
            except Exception as exc:
                logger.warning(f"[analyzer] Duplicate content failed (non-fatal): {exc}")

            # --- v0.5 Analyzer: Internal Link Graph ---
            try:
                link_graph = LinkGraph()
                # Link graph needs outlink_urls — we use the crawled pages
                # Since the crawler only has counters, we build links from snapshots
                # For now: link graph is fed with available data
                # (real outlink URLs will come in a later version)
                link_issues = link_graph.detect_issues(
                    [{"url": p.url, "final_url": p.final_url, "status_code": p.status_code,
                      "outlink_urls": p.internal_link_urls}
                     for p in pages],
                    domain,
                )
                issues.extend(link_issues)
                logger.info(f"[analyzer] Link graph: {len(link_issues)} issues")
            except Exception as exc:
                logger.warning(f"[analyzer] Link graph failed (non-fatal): {exc}")

            # --- v0.9 Analyzer: Robots.txt + Sitemap Audit ---
            try:
                rs_auditor = RobotsSitemapAuditor()
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=15, follow_redirects=True) as _rs_client:
                    robots_result = await rs_auditor.fetch_robots(domain, client=_rs_client)
                    issues.extend(rs_auditor.detect_robots_issues(robots_result))

                    # Determine sitemap URL from robots.txt or default
                    sitemap_url = (
                        robots_result.sitemap_directives[0]
                        if robots_result.sitemap_directives
                        else f"{domain.rstrip('/')}/sitemap.xml"
                    )
                    sitemap_result = await rs_auditor.fetch_sitemap(sitemap_url, client=_rs_client)

                    # Build lookup data for sitemap cross-checks
                    crawled_200 = {p.url for p in good_pages}
                    canonical_set = {p.canonical for p in good_pages if p.canonical}
                    url_status_map = {p.url: p.status_code for p in pages}

                    sitemap_issues = rs_auditor.detect_sitemap_issues(
                        sitemap_result,
                        canonical_urls=canonical_set or None,
                        crawled_urls=crawled_200 or None,
                        url_status=url_status_map or None,
                    )
                    issues.extend(sitemap_issues)
                    logger.info(
                        f"[analyzer] Robots/Sitemap: robots={'ok' if robots_result.exists else 'missing'}, "
                        f"sitemap={len(sitemap_result.urls)} URLs, "
                        f"{len(sitemap_issues) + len([i for i in issues if i['category'] == 'robots'])} issues"
                    )
            except Exception as exc:
                logger.warning(f"[analyzer] Robots/Sitemap audit failed (non-fatal): {exc}")

            # --- v0.10 Analyzer: E-E-A-T Signals ---
            eeat_result = None
            try:
                eeat_analyzer = EEATAnalyzer()
                eeat_pages = [_page_snapshot(p) for p in good_pages]
                eeat_result = eeat_analyzer.analyze(eeat_pages, domain)
                issues.extend(eeat_result["issues"])
                logger.info(
                    f"[analyzer] E-E-A-T: score={eeat_result['score']}/100, "
                    f"{len(eeat_result['issues'])} issues"
                )
            except Exception as exc:
                logger.warning(f"[analyzer] E-E-A-T analysis failed (non-fatal): {exc}")

            # --- v0.12 Analyzer: Intent + GEO Content Analysis ---
            intent_geo_result = None
            try:
                # GSC keywords from project config or context
                gsc_keywords = []
                if self.context and hasattr(self.context, "gsc_keywords"):
                    gsc_keywords = self.context.gsc_keywords or []
                elif (self.project_config.source_config or {}).get("gsc_keywords"):
                    gsc_keywords = self.project_config.source_config["gsc_keywords"]

                if gsc_keywords:
                    page_snapshots = [_page_snapshot(p) for p in good_pages]
                    intent_geo_result = await intent_geo_analyze(
                        gsc_keywords=gsc_keywords,
                        pages=page_snapshots,
                    )
                    if not intent_geo_result.skipped_reason:
                        issues.extend(intent_geo_result.issues)
                    logger.info(
                        f"[analyzer] Intent/GEO: {intent_geo_result.api_calls_used} API calls, "
                        f"avg_intent={intent_geo_result.avg_intent_match:.0f}, "
                        f"avg_geo={intent_geo_result.avg_geo_readiness:.0f}"
                        + (f" (skipped: {intent_geo_result.skipped_reason})" if intent_geo_result.skipped_reason else "")
                    )
                else:
                    logger.info("[analyzer] Intent/GEO: no GSC keywords available, skipped")
            except Exception as exc:
                logger.warning(f"[analyzer] Intent/GEO analysis failed (non-fatal): {exc}")

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

            # GEO metrics
            if geo_result:
                result.metrics["geo_avg_score"] = geo_result["avg_geo_score"]
                result.metrics["geo_page_scores"] = geo_result["page_scores"]

            # Topical Authority metrics
            if topical_clusters:
                result.metrics["topic_clusters"] = [
                    {
                        "cluster_id": c.cluster_id,
                        "topic_label": c.topic_label,
                        "pillar_url": c.pillar_url,
                        "cluster_size": len(c.cluster_urls),
                        "authority_score": c.authority_score,
                        "cannibalization_risk": c.cannibalization_risk,
                    }
                    for c in topical_clusters
                ]

            # E-E-A-T metrics
            if eeat_result:
                result.metrics["eeat_score"] = eeat_result["score"]
                result.metrics["eeat_signals"] = eeat_result["signals"]

            # Intent/GEO metrics
            if intent_geo_result and not intent_geo_result.skipped_reason:
                result.metrics["intent_geo"] = {
                    "avg_intent_match": intent_geo_result.avg_intent_match,
                    "avg_geo_readiness": intent_geo_result.avg_geo_readiness,
                    "api_calls_used": intent_geo_result.api_calls_used,
                    "keywords_analyzed": len(intent_geo_result.analyses),
                }

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
                issues.append(_issue(
                    "meta", "missing_title", "high", p.url,
                    "Missing page title",
                    "Page has no <title> element",
                    "Add a unique, descriptive <title> (50-60 chars)."))
            elif len(t) < TITLE_MIN:
                issues.append(_issue(
                    "meta", "short_title", "medium", p.url,
                    f"Title too short ({len(t)} chars)",
                    f"Current title: {t!r}",
                    f"Expand title to at least {TITLE_MIN} chars, ideally 50-60."))
            elif len(t) > TITLE_MAX:
                issues.append(_issue(
                    "meta", "long_title", "low", p.url,
                    f"Title too long ({len(t)} chars)",
                    f"Current title: {t!r}",
                    f"Shorten title to below {TITLE_MAX} chars so it isn't truncated in SERP."))

            # Meta description
            d = (p.meta_description or "").strip()
            if not d:
                issues.append(_issue(
                    "meta", "missing_meta_description", "high", p.url,
                    "Missing meta description",
                    "Page has no meta description",
                    "Add a meta description of 120-160 chars."))
            elif len(d) < META_MIN:
                issues.append(_issue(
                    "meta", "short_meta_description", "low", p.url,
                    f"Meta description too short ({len(d)} chars)",
                    f"Current: {d!r}",
                    f"Expand to at least {META_MIN} chars."))
            elif len(d) > META_MAX:
                issues.append(_issue(
                    "meta", "long_meta_description", "low", p.url,
                    f"Meta description too long ({len(d)} chars)",
                    f"Current: {d!r}",
                    f"Shorten to below {META_MAX} chars."))

            # Viewport
            if not p.viewport:
                issues.append(_issue(
                    "meta", "missing_viewport", "medium", p.url,
                    "Missing viewport meta tag",
                    "Page is likely not mobile-friendly",
                    'Add <meta name="viewport" content="width=device-width, initial-scale=1">.'))

            # lang
            if not p.lang:
                issues.append(_issue(
                    "meta", "missing_html_lang", "low", p.url,
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
                issues.append(_issue(
                    "content", "missing_h1", "medium", p.url,
                    "Missing H1 heading",
                    "Page has no H1 element",
                    "Add exactly one descriptive H1 tag."))
            elif len(p.h1) > 1:
                issues.append(_issue(
                    "content", "multiple_h1", "low", p.url,
                    f"Multiple H1 elements ({len(p.h1)})",
                    f"H1s: {p.h1[:3]}",
                    "Use exactly one H1 per page; use H2 for sub-sections."))
        return issues

    def _check_social(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if not p.og_tags.get("og:title"):
                issues.append(_issue(
                    "social", "missing_og_title", "low", p.url,
                    "Missing og:title",
                    "No Open Graph title for social shares",
                    'Add <meta property="og:title" content="...">.'))
            if not p.og_tags.get("og:image"):
                issues.append(_issue(
                    "social", "missing_og_image", "medium", p.url,
                    "Missing og:image",
                    "Social shares will have no preview image",
                    'Add <meta property="og:image" content="https://.../og.png">.'))
            if not p.twitter_tags.get("twitter:card"):
                issues.append(_issue(
                    "social", "missing_twitter_card", "low", p.url,
                    "Missing twitter:card",
                    "Twitter / X previews will be generic",
                    'Add <meta name="twitter:card" content="summary_large_image">.'))
        return issues

    def _check_images(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if p.images_without_alt > 0:
                issues.append(_issue(
                    "accessibility", "images_without_alt",
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
                issues.append(_issue(
                    "schema", "missing_organization_schema", "medium", p.url,
                    "Homepage missing Organization schema",
                    "Homepage has no Organization or WebSite JSON-LD",
                    "Add JSON-LD with @type Organization including name, url, logo, sameAs."))
            if not p.schema_types:
                issues.append(_issue(
                    "schema", "no_jsonld", "low", p.url,
                    "No JSON-LD structured data",
                    "Page has no schema.org markup",
                    "Add relevant schema.org JSON-LD (Article, Service, Product, etc.)."))
        return issues

    def _check_security(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        seen_domains: set = set()
        for p in pages:
            if not p.https:
                issues.append(_issue(
                    "security", "no_https", "high", p.url,
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
                issues.append(_issue(
                    "security", "missing_security_headers",
                    "medium", p.url,
                    f"Missing security headers: {', '.join(missing)}",
                    f"Present: {list(p.security_headers)}",
                    "Add headers via nginx: Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options."))
        return issues

    def _check_performance(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if p.fetch_ms > SLOW_RESPONSE_MS:
                issues.append(_issue(
                    "performance", "slow_response", "medium", p.url,
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
        """Detect Core Web Vitals issues from PageSpeed results.

        Prefers CrUX field data (real users), falls back to lab data.
        INP (Interaction to Next Paint) replaced FID since March 2024.
        """
        issues = []
        for r in psi_results:
            if r.error:
                continue

            # Performance score
            if r.performance_score is not None and r.performance_score < 50:
                issues.append(_issue(
                    "performance", "poor_lighthouse_performance", "high", r.url,
                    f"Poor Lighthouse performance score ({r.performance_score}/100, {r.strategy})",
                    f"Performance: {r.performance_score}, LCP: {r.lcp_display}, "
                    f"TBT: {r.tbt_display}, CLS: {r.cls_display}",
                    "Optimize images (WebP/AVIF), defer non-critical JS/CSS, reduce server response time.",
                    "Target: 90+ performance score"))
            elif r.performance_score is not None and r.performance_score < 90:
                issues.append(_issue(
                    "performance", "moderate_lighthouse_performance", "medium", r.url,
                    f"Moderate Lighthouse performance ({r.performance_score}/100, {r.strategy})",
                    f"Performance: {r.performance_score}, LCP: {r.lcp_display}, TBT: {r.tbt_display}",
                    "Review render-blocking resources, optimize images, enable text compression."))

            # --- LCP (prefer CrUX, fallback to lab) ---
            lcp_val = r.crux_lcp_ms if r.crux_lcp_ms is not None else r.lcp_ms
            lcp_source = "field" if r.crux_lcp_ms is not None else "lab"
            if lcp_val is not None and lcp_val > 2500:
                sev = "high" if lcp_val > 4000 else "medium"
                issues.append(_issue(
                    "performance", "poor_lcp", sev, r.url,
                    f"Poor LCP: {lcp_val:.0f}ms ({r.strategy}, {lcp_source})",
                    f"Largest Contentful Paint is {lcp_val:.0f}ms (target: <2500ms)",
                    "Optimize hero image (compress, use WebP, preload), reduce TTFB, eliminate render-blocking CSS."))

            # --- CLS (prefer CrUX, fallback to lab) ---
            cls_val = r.crux_cls if r.crux_cls is not None else r.cls
            cls_source = "field" if r.crux_cls is not None else "lab"
            if cls_val is not None and cls_val > 0.1:
                sev = "high" if cls_val > 0.25 else "medium"
                issues.append(_issue(
                    "performance", "poor_cls", sev, r.url,
                    f"Poor CLS: {cls_val:.3f} ({r.strategy}, {cls_source})",
                    f"Cumulative Layout Shift is {cls_val:.3f} (target: <0.1)",
                    "Set explicit width/height on images/videos, avoid injecting content above the fold."))

            # --- INP (CrUX only — no lab equivalent, TBT is only a proxy) ---
            if r.crux_inp_ms is not None and r.crux_inp_ms > 200:
                sev = "high" if r.crux_inp_ms > 500 else "medium"
                issues.append(_issue(
                    "performance", "poor_inp", sev, r.url,
                    f"Poor INP: {r.crux_inp_ms:.0f}ms ({r.strategy}, field)",
                    f"Interaction to Next Paint is {r.crux_inp_ms:.0f}ms (target: <200ms). "
                    f"INP measures all interactions in a session, not just the first one.",
                    "Split long tasks, defer non-critical JS, use requestIdleCallback, optimize event handlers."))

            # --- TBT as INP proxy when no CrUX INP is available ---
            if r.crux_inp_ms is None and r.tbt_ms is not None and r.tbt_ms > 200:
                sev = "high" if r.tbt_ms > 600 else "medium"
                issues.append(_issue(
                    "performance", "poor_tbt", sev, r.url,
                    f"High TBT: {r.tbt_display} ({r.strategy}, lab — INP-Proxy)",
                    f"Total Blocking Time is {r.tbt_ms:.0f}ms (target: <200ms). "
                    f"TBT is a lab proxy for INP. Real INP data is available when CrUX data exists.",
                    "Split long tasks, defer non-critical JavaScript, use web workers."))

            # --- TTFB (CrUX) ---
            if r.crux_ttfb_ms is not None and r.crux_ttfb_ms > 800:
                sev = "high" if r.crux_ttfb_ms > 1800 else "medium"
                issues.append(_issue(
                    "performance", "poor_ttfb", sev, r.url,
                    f"Poor TTFB: {r.crux_ttfb_ms:.0f}ms ({r.strategy}, field)",
                    f"Time to First Byte is {r.crux_ttfb_ms:.0f}ms (target: <800ms)",
                    "Optimize server response: enable caching, use CDN, check database queries."))

            # Lighthouse SEO score
            if r.seo_score is not None and r.seo_score < 90:
                issues.append(_issue(
                    "meta", "low_lighthouse_seo", "medium", r.url,
                    f"Lighthouse SEO score: {r.seo_score}/100 ({r.strategy})",
                    f"Google Lighthouse rates the SEO at {r.seo_score}/100",
                    "Check Lighthouse report for specific recommendations (crawlable links, valid robots.txt, etc)."))

            # Accessibility
            if r.accessibility_score is not None and r.accessibility_score < 80:
                issues.append(_issue(
                    "accessibility", "low_accessibility", "medium", r.url,
                    f"Low accessibility score: {r.accessibility_score}/100 ({r.strategy})",
                    f"Lighthouse accessibility: {r.accessibility_score}/100",
                    "Add ARIA labels, ensure sufficient color contrast, check keyboard navigation."))

        return issues

    def _check_canonical(self, pages: List[PageData]) -> List[Dict[str, Any]]:
        issues = []
        for p in pages:
            if not p.canonical:
                issues.append(_issue(
                    "meta", "missing_canonical", "low", p.url,
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
        "h2": p.h2,
        "word_count": p.word_count,
        "internal_links": p.internal_links,
        "external_links": p.external_links,
        "schema_types": p.schema_types,
        "schema_data": p.schema_data,
        "images_total": p.images_total,
        "images_without_alt": p.images_without_alt,
        "og_tags": p.og_tags,
        "canonical": p.canonical,
        "hreflang": p.hreflang,
        "robots_meta": p.robots_meta,
        "lang": p.lang,
        "https": p.https,
        "internal_link_urls": p.internal_link_urls,
        "security_headers": list(p.security_headers),
        "error": p.error,
    }
