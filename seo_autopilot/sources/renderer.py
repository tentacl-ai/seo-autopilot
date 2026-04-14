"""
JavaScript Renderer — Playwright-Fallback fuer SPA-Seiten.

Wird nur aufgerufen wenn der httpx-Crawler zu wenig Content findet
(word_count < MIN_WORDS) und SPA-Indikatoren im HTML erkennt.

Playwright ist optional — wenn nicht installiert, wird der Fallback
uebersprungen und eine Warnung geloggt.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum Woerter die der statische Crawler liefern muss,
# bevor der Fallback ausgeloest wird
MIN_WORDS_THRESHOLD = 50

# SPA-Indikatoren im Raw-HTML
SPA_INDICATORS = [
    'id="root"',
    'id="app"',
    'id="__next"',
    'id="__nuxt"',
    "script type=\"module\"",
    "script type='module'",
    "__NEXT_DATA__",
    "__NUXT__",
]

# Timeout fuer Playwright-Rendering (ms)
RENDER_TIMEOUT_MS = 15_000

# Playwright wird lazy importiert — nicht jede Installation hat es
_playwright_available: Optional[bool] = None


def is_spa_likely(raw_html: str, word_count: int) -> bool:
    """Prueft ob die Seite wahrscheinlich eine SPA ist.

    True wenn: wenig sichtbarer Text UND SPA-Framework-Indikatoren im HTML.
    """
    if word_count >= MIN_WORDS_THRESHOLD:
        return False

    html_lower = raw_html.lower()
    return any(indicator.lower() in html_lower for indicator in SPA_INDICATORS)


async def render_page(url: str, timeout_ms: int = RENDER_TIMEOUT_MS) -> Optional[str]:
    """Rendert eine Seite mit Playwright und gibt den gerenderten HTML zurueck.

    Returns:
        Gerenderter HTML-String oder None bei Fehler/Nicht-Verfuegbarkeit.
    """
    global _playwright_available

    # Lazy-Check ob Playwright installiert ist
    if _playwright_available is False:
        return None

    try:
        from playwright.async_api import async_playwright
        _playwright_available = True
    except ImportError:
        _playwright_available = False
        logger.info(
            "[renderer] Playwright nicht installiert — JS-Rendering nicht verfuegbar. "
            "Installiere mit: pip install playwright && playwright install chromium"
        )
        return None

    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
            ],
        )

        page = await browser.new_page(
            user_agent="SEOAutopilotBot/1.1 (+https://github.com/tentacl-ai/seo-autopilot)",
            viewport={"width": 1280, "height": 720},
        )

        # Seite laden und auf Netzwerk-Idle warten
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

        # Extra-Warten fuer lazy-geladene Inhalte (200ms)
        await page.wait_for_timeout(500)

        # Gerenderten HTML holen
        rendered_html = await page.content()

        await page.close()
        await browser.close()
        await pw.stop()

        logger.info(f"[renderer] JS-rendered {url} ({len(rendered_html)} bytes)")
        return rendered_html

    except Exception as exc:
        logger.warning(f"[renderer] Rendering failed for {url}: {exc}")
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        return None
