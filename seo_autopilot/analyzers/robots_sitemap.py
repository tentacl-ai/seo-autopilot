"""
Robots.txt + Sitemap Audit — Phase 9.

Detects:
- robots.txt: AI crawler blocking (GPTBot, ClaudeBot, PerplexityBot etc.),
  CSS/JS blocking, missing sitemap directive, overly broad disallow rules
- sitemap.xml: 3xx/4xx URLs, missing pages, stale lastmod,
  non-canonical URLs in sitemap, oversized sitemaps
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

# AI crawlers — blocking these hurts GEO/AIO visibility
AI_CRAWLERS = [
    "GPTBot", "ChatGPT-User", "ClaudeBot", "anthropic-ai",
    "PerplexityBot", "Bytespider", "CCBot", "Google-Extended",
    "FacebookBot", "cohere-ai",
]

# CSS/JS blocking prevents rendering — critical for JS-heavy sites
ASSET_PATTERNS = [
    r"/\.css", r"/\.js", r"/static/", r"/assets/", r"/_next/",
    r"/dist/", r"/bundle", r"/webpack",
]

# Max URLs per sitemap before search engines may ignore entries
SITEMAP_MAX_URLS = 50_000
SITEMAP_MAX_BYTES = 50 * 1024 * 1024  # 50 MB uncompressed

# Lastmod older than this is considered stale
LASTMOD_STALE_DAYS = 365

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@dataclass
class RobotsResult:
    """Parsed robots.txt data."""
    raw: str = ""
    exists: bool = False
    status_code: int = 0
    sitemap_directives: List[str] = field(default_factory=list)
    blocked_ai_crawlers: List[str] = field(default_factory=list)
    blocks_css_js: bool = False
    has_wildcard_disallow: bool = False
    disallow_rules: List[Tuple[str, str]] = field(default_factory=list)  # (user-agent, path)


@dataclass
class SitemapUrl:
    """Single URL entry from a sitemap."""
    loc: str
    lastmod: Optional[str] = None
    changefreq: Optional[str] = None
    priority: Optional[str] = None


@dataclass
class SitemapResult:
    """Parsed sitemap data."""
    url: str = ""
    exists: bool = False
    status_code: int = 0
    urls: List[SitemapUrl] = field(default_factory=list)
    is_index: bool = False
    child_sitemaps: List[str] = field(default_factory=list)
    size_bytes: int = 0
    parse_error: Optional[str] = None


class RobotsSitemapAuditor:
    """Audits robots.txt and sitemap.xml for SEO issues."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    async def fetch_robots(self, domain: str, client: Optional[httpx.AsyncClient] = None) -> RobotsResult:
        """Fetch and parse robots.txt."""
        result = RobotsResult()
        url = f"{domain.rstrip('/')}/robots.txt"
        own_client = client is None

        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)

        try:
            resp = await client.get(url)
            result.status_code = resp.status_code
            if resp.status_code == 200:
                result.exists = True
                result.raw = resp.text
                self._parse_robots(result)
        except Exception as exc:
            logger.warning(f"[robots] fetch failed: {exc}")
        finally:
            if own_client:
                await client.aclose()

        return result

    async def fetch_sitemap(self, url: str, client: Optional[httpx.AsyncClient] = None) -> SitemapResult:
        """Fetch and parse a sitemap XML."""
        result = SitemapResult(url=url)
        own_client = client is None

        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)

        try:
            resp = await client.get(url)
            result.status_code = resp.status_code
            result.size_bytes = len(resp.content)
            if resp.status_code == 200:
                result.exists = True
                self._parse_sitemap(result, resp.text)
        except Exception as exc:
            logger.warning(f"[sitemap] fetch failed for {url}: {exc}")
        finally:
            if own_client:
                await client.aclose()

        return result

    def _parse_robots(self, result: RobotsResult) -> None:
        """Parse robots.txt content into structured data."""
        current_agent = "*"
        for raw_line in result.raw.splitlines():
            line = raw_line.split("#")[0].strip()
            if not line:
                continue

            lower = line.lower()
            if lower.startswith("user-agent:"):
                current_agent = line.split(":", 1)[1].strip()
            elif lower.startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    result.disallow_rules.append((current_agent, path))
            elif lower.startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()
                # "Sitemap:" value includes the scheme, rejoin after first split
                if not sitemap_url.startswith("http"):
                    sitemap_url = ":" .join(line.split(":")[1:]).strip()
                result.sitemap_directives.append(sitemap_url)

        # Detect AI crawler blocking
        for crawler in AI_CRAWLERS:
            if self._is_blocked(result.raw, crawler):
                result.blocked_ai_crawlers.append(crawler)

        # Detect CSS/JS blocking
        for agent, path in result.disallow_rules:
            for pattern in ASSET_PATTERNS:
                if re.search(pattern, path, re.IGNORECASE):
                    result.blocks_css_js = True
                    break

        # Detect wildcard disallow (Disallow: / for *)
        for agent, path in result.disallow_rules:
            if agent == "*" and path == "/":
                result.has_wildcard_disallow = True

    def _is_blocked(self, robots_txt: str, crawler: str) -> bool:
        """Check if a specific crawler is blocked via Disallow: /."""
        current_agent = ""
        for raw_line in robots_txt.splitlines():
            line = raw_line.split("#")[0].strip()
            if not line:
                continue
            lower = line.lower()
            if lower.startswith("user-agent:"):
                current_agent = line.split(":", 1)[1].strip()
            elif lower.startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if current_agent.lower() == crawler.lower() and path == "/":
                    return True
        return False

    def _parse_sitemap(self, result: SitemapResult, content: str) -> None:
        """Parse sitemap XML into structured data."""
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            result.parse_error = str(exc)
            return

        tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

        if tag == "sitemapindex":
            result.is_index = True
            for sitemap_el in root.findall("sm:sitemap", NS):
                loc = sitemap_el.find("sm:loc", NS)
                if loc is not None and loc.text:
                    result.child_sitemaps.append(loc.text.strip())
        elif tag == "urlset":
            for url_el in root.findall("sm:url", NS):
                loc = url_el.find("sm:loc", NS)
                if loc is None or not loc.text:
                    continue
                lastmod_el = url_el.find("sm:lastmod", NS)
                changefreq_el = url_el.find("sm:changefreq", NS)
                priority_el = url_el.find("sm:priority", NS)
                result.urls.append(SitemapUrl(
                    loc=loc.text.strip(),
                    lastmod=lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else None,
                    changefreq=changefreq_el.text.strip() if changefreq_el is not None and changefreq_el.text else None,
                    priority=priority_el.text.strip() if priority_el is not None and priority_el.text else None,
                ))

    def detect_robots_issues(self, robots: RobotsResult) -> List[Dict[str, Any]]:
        """Detect SEO issues in robots.txt."""
        issues: List[Dict[str, Any]] = []

        if not robots.exists:
            issues.append(_issue(
                "robots", "missing_robots_txt", "medium",
                "Missing robots.txt",
                "No robots.txt found. Search engines use defaults, but explicit rules improve control.",
                "Create a robots.txt with sitemap directive and reasonable access rules.",
            ))
            return issues

        # AI crawler blocking
        if robots.blocked_ai_crawlers:
            crawlers = ", ".join(robots.blocked_ai_crawlers)
            issues.append(_issue(
                "robots", "ai_crawler_blocked", "high",
                f"AI crawlers blocked: {crawlers}",
                f"robots.txt blocks these AI crawlers: {crawlers}. "
                "This hurts visibility in AI search (ChatGPT, Perplexity, Claude).",
                "Remove Disallow: / for AI crawlers unless you have a specific reason to block them.",
            ))

        # CSS/JS blocking
        if robots.blocks_css_js:
            issues.append(_issue(
                "robots", "css_js_blocked", "high",
                "CSS/JS resources blocked in robots.txt",
                "Blocking CSS/JS prevents Googlebot from rendering the page correctly. "
                "This can lead to missing content in the index.",
                "Remove Disallow rules for static assets (CSS, JS, images).",
            ))

        # Missing sitemap directive
        if not robots.sitemap_directives:
            issues.append(_issue(
                "robots", "missing_sitemap_directive", "medium",
                "No Sitemap directive in robots.txt",
                "robots.txt has no Sitemap: line. Search engines may not find the sitemap automatically.",
                "Add 'Sitemap: https://example.com/sitemap.xml' to robots.txt.",
            ))

        # Wildcard disallow (blocks everything)
        if robots.has_wildcard_disallow:
            issues.append(_issue(
                "robots", "wildcard_disallow", "critical",
                "robots.txt blocks all crawlers (Disallow: /)",
                "User-agent: * with Disallow: / blocks all search engine crawlers from the entire site.",
                "Remove or restrict the Disallow: / rule to specific paths.",
            ))

        return issues

    def detect_sitemap_issues(
        self,
        sitemap: SitemapResult,
        canonical_urls: Optional[Set[str]] = None,
        crawled_urls: Optional[Set[str]] = None,
        url_status: Optional[Dict[str, int]] = None,
    ) -> List[Dict[str, Any]]:
        """Detect SEO issues in sitemap.xml."""
        issues: List[Dict[str, Any]] = []

        if not sitemap.exists:
            issues.append(_issue(
                "sitemap", "missing_sitemap", "high",
                "Missing sitemap.xml",
                "No sitemap.xml found. Search engines rely on sitemaps for efficient crawling.",
                "Create a sitemap.xml listing all indexable pages with lastmod dates.",
            ))
            return issues

        if sitemap.parse_error:
            issues.append(_issue(
                "sitemap", "sitemap_parse_error", "critical",
                "Sitemap XML parse error",
                f"Sitemap could not be parsed: {sitemap.parse_error}",
                "Fix the XML syntax in sitemap.xml. Validate with xmllint or Google Search Console.",
            ))
            return issues

        # Sitemap index — skip URL-level checks
        if sitemap.is_index:
            if not sitemap.child_sitemaps:
                issues.append(_issue(
                    "sitemap", "empty_sitemap_index", "high",
                    "Sitemap index has no child sitemaps",
                    "The sitemap index file contains no <sitemap> entries.",
                    "Add child sitemap references or switch to a flat sitemap.",
                ))
            return issues

        # Empty sitemap
        if not sitemap.urls:
            issues.append(_issue(
                "sitemap", "empty_sitemap", "high",
                "Sitemap is empty (0 URLs)",
                "sitemap.xml exists but contains no <url> entries.",
                "Add all indexable pages to the sitemap.",
            ))
            return issues

        # Too many URLs
        if len(sitemap.urls) > SITEMAP_MAX_URLS:
            issues.append(_issue(
                "sitemap", "sitemap_too_large", "medium",
                f"Sitemap has {len(sitemap.urls)} URLs (max {SITEMAP_MAX_URLS})",
                "Sitemaps with more than 50,000 URLs may be ignored by search engines.",
                "Split into multiple sitemaps and create a sitemap index.",
            ))

        # Oversized file
        if sitemap.size_bytes > SITEMAP_MAX_BYTES:
            mb = sitemap.size_bytes / (1024 * 1024)
            issues.append(_issue(
                "sitemap", "sitemap_file_too_large", "medium",
                f"Sitemap file too large ({mb:.1f} MB, max 50 MB)",
                "Uncompressed sitemap exceeds the 50 MB limit.",
                "Split into smaller sitemaps or use gzip compression.",
            ))

        # Check individual URLs
        stale_count = 0
        no_lastmod_count = 0
        non_canonical_count = 0
        broken_count = 0

        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(days=LASTMOD_STALE_DAYS)

        for entry in sitemap.urls:
            # Broken URLs (3xx/4xx/5xx)
            if url_status and entry.loc in url_status:
                status = url_status[entry.loc]
                if status >= 300:
                    broken_count += 1
                    if broken_count <= 5:  # Limit individual issues
                        issues.append(_issue(
                            "sitemap", "sitemap_broken_url", "high",
                            f"Sitemap contains HTTP {status} URL: {entry.loc}",
                            f"URL in sitemap returns status {status}. Only 200 URLs belong in sitemaps.",
                            "Remove non-200 URLs from sitemap or fix the page.",
                        ))

            # Non-canonical URLs
            if canonical_urls is not None and entry.loc not in canonical_urls:
                non_canonical_count += 1

            # Stale lastmod
            if entry.lastmod:
                try:
                    # Support both date and datetime formats
                    lastmod_str = entry.lastmod[:10]  # YYYY-MM-DD
                    lastmod_date = datetime.strptime(lastmod_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if lastmod_date < stale_threshold:
                        stale_count += 1
                except (ValueError, IndexError):
                    pass
            else:
                no_lastmod_count += 1

        # Missing pages (crawled but not in sitemap)
        if crawled_urls:
            sitemap_locs = {e.loc for e in sitemap.urls}
            missing = crawled_urls - sitemap_locs
            if missing:
                sample = list(missing)[:5]
                issues.append(_issue(
                    "sitemap", "sitemap_missing_pages", "medium",
                    f"{len(missing)} crawled pages not in sitemap",
                    f"Pages found during crawl but missing from sitemap: {', '.join(sample)}",
                    "Add all indexable pages to the sitemap for faster discovery.",
                ))

        # Aggregate stale lastmod
        if stale_count > 0:
            issues.append(_issue(
                "sitemap", "sitemap_stale_lastmod", "low",
                f"{stale_count} sitemap URLs have lastmod older than {LASTMOD_STALE_DAYS} days",
                "Stale lastmod dates reduce crawl priority. Search engines may skip these URLs.",
                "Update lastmod to reflect actual content changes.",
            ))

        # No lastmod at all
        if no_lastmod_count == len(sitemap.urls):
            issues.append(_issue(
                "sitemap", "sitemap_no_lastmod", "medium",
                "No lastmod dates in sitemap",
                "None of the sitemap URLs have lastmod. Search engines can't prioritize fresh content.",
                "Add accurate lastmod dates to all sitemap entries.",
            ))

        # Non-canonical URLs in sitemap
        if non_canonical_count > 0:
            issues.append(_issue(
                "sitemap", "sitemap_non_canonical_urls", "high",
                f"{non_canonical_count} non-canonical URLs in sitemap",
                "Sitemap contains URLs that are not the canonical version. "
                "This wastes crawl budget and sends conflicting signals.",
                "Only include canonical URLs in the sitemap.",
            ))

        # Broken URL summary (if more than 5)
        if broken_count > 5:
            issues.append(_issue(
                "sitemap", "sitemap_many_broken_urls", "critical",
                f"{broken_count} broken URLs in sitemap",
                f"Sitemap contains {broken_count} URLs with non-200 status codes.",
                "Audit and clean up the sitemap. Remove redirected, 404, and 5xx pages.",
            ))

        return issues


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
