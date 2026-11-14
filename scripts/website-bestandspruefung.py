"""Read-only public GET inventory, bounded to one approved site and its images."""

import asyncio
import json
import re
import sys
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

BASE = "https://tentacl.ai"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/tentacl-seo-bestand.json")


def internal(url):
    p = urlsplit(url)
    return (
        p.scheme == "https"
        and p.netloc == "tentacl.ai"
        and not p.query
        and not p.fragment
        and not re.search(
            r"/(api|admin|intern|login|logout|termin|kalender)(/|$)", p.path
        )
    )


async def main():
    pages, assets, checks, failures = [], {}, {}, []
    queue, seen = deque([BASE + "/"]), set()
    async with httpx.AsyncClient(
        timeout=25,
        follow_redirects=True,
        max_redirects=5,
        headers={"User-Agent": "Tentacl-SEO-Bestandspruefung/1.0"},
    ) as client:
        for path in ["/robots.txt", "/sitemap.xml", "/llms.txt"]:
            r = await client.get(BASE + path)
            checks[path] = {
                "status": r.status_code,
                "bytes": len(r.content),
                "content": r.text[:500000],
            }
        robots = RobotFileParser()
        robots.parse(checks["/robots.txt"]["content"].splitlines())
        sitemap_seen = set()

        async def sitemap(url, text=None):
            if url in sitemap_seen or len(sitemap_seen) >= 8 or not internal(url):
                return
            sitemap_seen.add(url)
            try:
                if text is None:
                    res = await client.get(url)
                    res.raise_for_status()
                    text = res.text
                root = ET.fromstring(text)
                urls = [
                    node.text.strip()
                    for node in root.iter()
                    if node.tag.endswith("}loc") and node.text
                ]
                if root.tag.endswith("sitemapindex"):
                    for child in urls:
                        await sitemap(child)
                else:
                    queue.extend(url for url in urls if internal(url))
            except Exception as exc:
                failures.append({"url": url, "reason": type(exc).__name__})

        if checks["/sitemap.xml"]["status"] == 200:
            await sitemap(BASE + "/sitemap.xml", checks["/sitemap.xml"]["content"])
        while queue and len(seen) < 100:
            url = urldefrag(queue.popleft())[0]
            if not internal(url) or url in seen:
                continue
            seen.add(url)
            if not robots.can_fetch("*", url):
                failures.append({"url": url, "reason": "robots disallows"})
                continue
            try:
                response = await client.get(url)
                if not internal(str(response.url)):
                    failures.append(
                        {"url": url, "reason": "redirect outside approved site"}
                    )
                    continue
                if "text/html" not in response.headers.get("content-type", ""):
                    continue
                soup = BeautifulSoup(response.text, "html.parser")
                meta = soup.find(
                    "meta", attrs={"name": re.compile("^description$", re.I)}
                )
                canonical = soup.find("link", rel="canonical")
                noindex = soup.find(
                    "meta", attrs={"name": re.compile("^robots$", re.I)}
                )
                images = []
                for img in soup.find_all("img"):
                    src = urljoin(str(response.url), img.get("src", ""))
                    item = {
                        key: img.get(key)
                        for key in [
                            "alt",
                            "width",
                            "height",
                            "loading",
                            "srcset",
                            "sizes",
                        ]
                    }
                    item["src"] = src
                    images.append(item)
                    if internal(src):
                        assets[src] = None
                schemas = []
                schema_errors = 0
                for tag in soup.find_all("script", type="application/ld+json"):
                    try:
                        obj = json.loads(tag.string or tag.get_text())
                        entries = (
                            obj if isinstance(obj, list) else obj.get("@graph", [obj])
                        )
                        schemas.extend(
                            x.get("@type") for x in entries if isinstance(x, dict)
                        )
                    except (ValueError, TypeError):
                        schema_errors += 1
                links = []
                for a in soup.find_all("a", href=True):
                    link = urldefrag(urljoin(str(response.url), a["href"]))[0]
                    if internal(link) and not re.search(
                        r"\.(png|jpe?g|webp|avif|svg|pdf|zip|mp[34]|woff2?)(/|$)",
                        urlsplit(link).path,
                        re.I,
                    ):
                        queue.append(link)
                        links.append(link)
                pages.append(
                    {
                        "url": url,
                        "final_url": str(response.url),
                        "status": response.status_code,
                        "title": (
                            soup.title.get_text(" ", strip=True) if soup.title else ""
                        ),
                        "description": meta.get("content", "") if meta else "",
                        "canonical": canonical.get("href") if canonical else None,
                        "h1": [
                            h.get_text(" ", strip=True) for h in soup.find_all("h1")
                        ],
                        "robots": noindex.get("content") if noindex else None,
                        "x_robots": response.headers.get("x-robots-tag"),
                        "lang": soup.html.get("lang") if soup.html else None,
                        "images": images,
                        "schema_types": schemas,
                        "schema_errors": schema_errors,
                        "links": links,
                        "words": len(soup.get_text(" ", strip=True).split()),
                    }
                )
            except Exception as exc:
                failures.append({"url": url, "reason": type(exc).__name__})
        for url in list(assets)[:200]:
            try:
                r = await client.head(url)
                assets[url] = {
                    "status": r.status_code,
                    "bytes": r.headers.get("content-length"),
                    "type": r.headers.get("content-type"),
                }
            except Exception as exc:
                assets[url] = {"error": type(exc).__name__}
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "site": BASE,
        "method": "public GET pages / HEAD images, no audit/apply/mail",
        "pages": pages,
        "assets": assets,
        "checks": checks,
        "failures": failures,
        "cap_reached": bool(queue),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    summary = {
        "pages": len(pages),
        "assets": len(assets),
        "status": dict(Counter(str(p["status"]) for p in pages)),
        "missing_description": sum(not p["description"] for p in pages),
        "missing_canonical": sum(not p["canonical"] for p in pages),
        "h1_not_one": sum(len(p["h1"]) != 1 for p in pages),
        "missing_alt": sum(i["alt"] is None for p in pages for i in p["images"]),
        "failures": len(failures),
        "out": str(OUT),
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
