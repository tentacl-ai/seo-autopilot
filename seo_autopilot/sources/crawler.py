"""
HTTP Crawler + HTML Parser

Fetches pages via httpx, parses with BeautifulSoup and extracts
all SEO-relevant data (title, meta, h1-h6, schema.org JSON-LD,
canonical, hreflang, internal/external links, images with alt,
security headers, response time).

Also discovers pages from sitemap.xml or via internal-link crawl.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "SEOAutopilotBot/0.3 (+https://tentacl.ai/seo-autopilot)"
DEFAULT_TIMEOUT = 20.0
MAX_HTML_BYTES = 2_000_000  # 2 MB


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PageData:
    """Parsed data for a single page."""

    url: str
    status_code: int = 0
    fetch_ms: int = 0
    final_url: str = ""
    content_type: str = ""
    response_bytes: int = 0

    title: Optional[str] = None
    meta_description: Optional[str] = None
    canonical: Optional[str] = None
    robots_meta: Optional[str] = None
    lang: Optional[str] = None
    viewport: Optional[str] = None

    h1: List[str] = field(default_factory=list)
    h2: List[str] = field(default_factory=list)

    word_count: int = 0
    internal_links: int = 0
    external_links: int = 0

    images_total: int = 0
    images_without_alt: int = 0

    og_tags: Dict[str, str] = field(default_factory=dict)
    twitter_tags: Dict[str, str] = field(default_factory=dict)
    hreflang: List[Dict[str, str]] = field(default_factory=list)
    schema_types: List[str] = field(default_factory=list)
    schema_data: List[Dict] = field(default_factory=list)

    security_headers: Dict[str, str] = field(default_factory=dict)
    https: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------


class SEOCrawler:
    """
    Async SEO crawler.

    Usage:
        async with SEOCrawler() as crawler:
            urls = await crawler.discover_pages("https://example.com", limit=10)
            results = await crawler.crawl(urls)
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, concurrency: int = 5):
        self.timeout = timeout
        self.concurrency = concurrency
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(concurrency)

    async def __aenter__(self) -> "SEOCrawler":
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            await self._client.aclose()

    # -- discovery ----------------------------------------------------------

    async def discover_pages(self, domain: str, limit: int = 20) -> List[str]:
        """
        Discover pages for a domain. Tries sitemap.xml first,
        falls back to crawling internal links from the homepage.
        """
        domain = domain.rstrip("/")
        urls: List[str] = []

        # 1. sitemap.xml
        for sitemap_url in (f"{domain}/sitemap.xml", f"{domain}/sitemap_index.xml"):
            try:
                sitemap_urls = await self._parse_sitemap(sitemap_url, max_depth=2)
                if sitemap_urls:
                    logger.info(f"Discovered {len(sitemap_urls)} URLs via {sitemap_url}")
                    urls = sitemap_urls
                    break
            except Exception as exc:
                logger.debug(f"Sitemap {sitemap_url} failed: {exc}")

        # 2. fallback: parse homepage and collect internal links
        if not urls:
            urls = await self._discover_via_homepage(domain)

        # always include the root
        if domain not in urls and f"{domain}/" not in urls:
            urls.insert(0, domain)

        # de-duplicate while preserving order
        seen: Set[str] = set()
        deduped: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        return deduped[:limit]

    async def _parse_sitemap(self, url: str, max_depth: int = 2) -> List[str]:
        """Recursively parse sitemap.xml / sitemap index files."""
        if max_depth <= 0 or self._client is None:
            return []

        try:
            resp = await self._client.get(url)
        except Exception as exc:
            logger.debug(f"Sitemap fetch failed {url}: {exc}")
            return []

        if resp.status_code != 200 or "xml" not in resp.headers.get("content-type", ""):
            return []

        urls: List[str] = []
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return []

        # strip namespace for easier tag matching
        def tag(el):
            return el.tag.split("}", 1)[-1]

        if tag(root) == "sitemapindex":
            for sm in root:
                loc_el = next((c for c in sm if tag(c) == "loc"), None)
                if loc_el is not None and loc_el.text:
                    urls.extend(await self._parse_sitemap(loc_el.text.strip(), max_depth - 1))
        elif tag(root) == "urlset":
            for u in root:
                loc_el = next((c for c in u if tag(c) == "loc"), None)
                if loc_el is not None and loc_el.text:
                    urls.append(loc_el.text.strip())
        return urls

    async def _discover_via_homepage(self, domain: str) -> List[str]:
        """Fallback: crawl homepage and extract internal links."""
        if self._client is None:
            return [domain]
        try:
            resp = await self._client.get(domain)
        except Exception as exc:
            logger.warning(f"Homepage fetch failed {domain}: {exc}")
            return [domain]

        if resp.status_code != 200:
            return [domain]

        soup = BeautifulSoup(resp.text, "html.parser")
        base = urlparse(domain)
        urls: List[str] = [domain]
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(domain, href)
            p = urlparse(full)
            if p.netloc == base.netloc and p.scheme in ("http", "https"):
                urls.append(full.split("#")[0])
        return urls

    # -- crawling -----------------------------------------------------------

    async def crawl(self, urls: List[str]) -> List[PageData]:
        """Crawl a list of URLs concurrently and return PageData objects."""
        tasks = [self._fetch_one(url) for url in urls]
        return await asyncio.gather(*tasks)

    async def _fetch_one(self, url: str) -> PageData:
        async with self._semaphore:
            page = PageData(url=url)
            if self._client is None:
                page.error = "client not initialized"
                return page

            start = time.monotonic()
            try:
                resp = await self._client.get(url)
            except Exception as exc:
                page.error = f"{type(exc).__name__}: {exc}"
                logger.warning(f"Fetch failed {url}: {exc}")
                return page

            page.fetch_ms = int((time.monotonic() - start) * 1000)
            page.status_code = resp.status_code
            page.final_url = str(resp.url)
            page.content_type = resp.headers.get("content-type", "")
            page.response_bytes = len(resp.content)
            page.https = page.final_url.startswith("https://")
            page.security_headers = _extract_security_headers(resp.headers)

            if resp.status_code != 200 or "html" not in page.content_type.lower():
                return page

            html = resp.text[:MAX_HTML_BYTES]
            _parse_html_into(page, html)
            return page


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


SECURITY_HEADER_NAMES = {
    "strict-transport-security",
    "x-frame-options",
    "x-content-type-options",
    "content-security-policy",
    "referrer-policy",
    "permissions-policy",
}


def _extract_security_headers(headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in SECURITY_HEADER_NAMES:
            out[name.lower()] = value
    return out


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _parse_html_into(page: PageData, html: str) -> None:
    """Parse HTML string and fill in page attributes."""
    soup = BeautifulSoup(html, "html.parser")

    # <html lang>
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        page.lang = html_tag["lang"].strip()

    # title
    if soup.title and soup.title.string:
        page.title = soup.title.string.strip()

    # meta tags
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        prop = (meta.get("property") or "").lower()
        content = (meta.get("content") or "").strip()
        if not content:
            continue
        if name == "description":
            page.meta_description = content
        elif name == "viewport":
            page.viewport = content
        elif name == "robots":
            page.robots_meta = content
        elif prop.startswith("og:"):
            page.og_tags[prop] = content
        elif name.startswith("twitter:"):
            page.twitter_tags[name] = content

    # canonical + hreflang
    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        if isinstance(rel, list):
            rel = [r.lower() for r in rel]
        if "canonical" in rel and link.get("href"):
            page.canonical = link["href"].strip()
        if "alternate" in rel and link.get("hreflang"):
            page.hreflang.append({
                "hreflang": link["hreflang"],
                "href": link.get("href", ""),
            })

    # headings
    page.h1 = [h.get_text(strip=True) for h in soup.find_all("h1")]
    page.h2 = [h.get_text(strip=True) for h in soup.find_all("h2")]

    # schema.org JSON-LD (extract BEFORE stripping scripts for word-count)
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            page.schema_data.append(entry)
            t = entry.get("@type")
            if isinstance(t, list):
                page.schema_types.extend(t)
            elif t:
                page.schema_types.append(t)

    # links (before stripping scripts - nav links could be inside)
    base = urlparse(page.final_url or page.url)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(page.final_url or page.url, href)
        if urlparse(full).netloc == base.netloc:
            page.internal_links += 1
        else:
            page.external_links += 1

    # images
    images = soup.find_all("img")
    page.images_total = len(images)
    page.images_without_alt = sum(1 for img in images if not (img.get("alt") or "").strip())

    # word count (visible text minus script/style) - done LAST since it mutates soup
    for s in soup(["script", "style", "noscript"]):
        s.extract()
    text = soup.get_text(" ", strip=True)
    page.word_count = len(_WORD_RE.findall(text))
