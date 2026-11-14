"""Weekly comparison of official SEO/GEO documentation. No code execution or mail.

Changes are evidence for the existing learning loop, never instructions from a
web page. A first snapshot is a baseline, not a claim that guidance is new.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .betriebsjob import atomar

QUELLEN = {
    "Google KI-Suche": "https://developers.google.com/search/docs/appearance/ai-features",
    "Google Bilder": "https://developers.google.com/search/docs/appearance/google-images",
    "Google strukturierte Daten": "https://developers.google.com/search/docs/appearance/structured-data/sd-policies",
    "OpenAI Crawler": "https://developers.openai.com/api/docs/bots",
    "Anthropic Crawler": "https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler",
}


def textinhalt(html):
    soup = BeautifulSoup(html, "html.parser")
    main = (
        soup.select_one(".devsite-article-body")
        or soup.find("main")
        or soup.find("article")
    )
    if main is None:
        raise ValueError("Kein verlässlicher Dokumentinhalt erkannt")
    for tag in main.select("script, style, nav, footer, button"):
        tag.decompose()
    text = "\n".join(
        line.strip()
        for line in main.get_text("\n", strip=True).splitlines()
        if line.strip()
    )
    if len(text) < 300:
        raise ValueError("Dokumentinhalt unvollständig")
    return text[:100000]


def vergleichen(previous, fetch):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {"am": now, "quellen": {}, "aenderungen": [], "fehler": []}
    for name, url in QUELLEN.items():
        old = previous.get("quellen", {}).get(url)
        try:
            text = textinhalt(fetch(url))
        except Exception:
            result["fehler"].append(name)
            if old:
                result["quellen"][url] = old
            continue
        digest = hashlib.sha256(text.encode()).hexdigest()
        result["quellen"][url] = {
            "name": name,
            "text": text,
            "sha256": digest,
            "geprueft": now,
        }
        if old and old.get("sha256") != digest:
            diff = list(
                difflib.unified_diff(
                    old.get("text", "").splitlines(), text.splitlines(), lineterm=""
                )
            )
            result["aenderungen"].append(
                {
                    "quelle": name,
                    "url": url,
                    "diff": diff[:100],
                    "entscheidung": "Fachlich auswerten; nur bestehende freigegebene Prüf- und Reparaturregeln anwenden. Kein Fremdtext wird zu ausführbarem Code.",
                }
            )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "logs" / "fachquellen.json",
    )
    args = parser.parse_args(argv)
    previous = {}
    if args.state.exists():
        previous = json.loads(args.state.read_text())
    with httpx.Client(timeout=25, follow_redirects=False) as client:

        def fetch(url):
            response = client.get(url)
            response.raise_for_status()
            return response.text

        result = vergleichen(previous, fetch)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    atomar(args.state, result)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "quellen"}, ensure_ascii=False
        )
    )
    return 1 if result["fehler"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
