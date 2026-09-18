"""
JavaScript Renderer — Playwright-Fallback fuer SPA-Seiten.

Wird nur aufgerufen wenn der httpx-Crawler zu wenig Content findet
(word_count < MIN_WORDS) und SPA-Indikatoren im HTML erkennt.

Playwright ist optional — wenn nicht installiert, wird der Fallback
uebersprungen und eine Warnung geloggt.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Browser liegt im Projektordner, damit der root-Cron denselben Chromium findet
# wie ein Handlauf als claude-user (Standard waere ~/.cache je Benutzer).
BROWSER_ORDNER = Path(__file__).resolve().parents[2] / ".browsers"
if BROWSER_ORDNER.is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_ORDNER))

# Google indexiert mobil zuerst -> gerendert wird in Handy-Groesse
MOBIL_VIEWPORT = {"width": 412, "height": 915}

# Minimum Woerter die der statische Crawler liefern muss,
# bevor der Fallback ausgeloest wird
MIN_WORDS_THRESHOLD = 50

# SPA-Indikatoren im Raw-HTML
SPA_INDICATORS = [
    'id="root"',
    'id="app"',
    'id="__next"',
    'id="__nuxt"',
    'script type="module"',
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

    from .crawler import USER_AGENT

    pw = None
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
            user_agent=USER_AGENT,
            viewport=MOBIL_VIEWPORT,
            is_mobile=True,
        )

        # Seite laden und auf Netzwerk-Idle warten
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

        # Extra-Warten fuer lazy-geladene Inhalte (200ms)
        await page.wait_for_timeout(500)

        # Gerenderten HTML holen
        rendered_html = await page.content()

        await page.close()
        await browser.close()
        browser = None
        await pw.stop()
        pw = None

        logger.info(f"[renderer] JS-rendered {url} ({len(rendered_html)} bytes)")
        return rendered_html

    except Exception as exc:
        logger.warning(f"[renderer] Rendering failed for {url}: {exc}")
        for aufraeumen in (browser.close if browser else None, pw.stop if pw else None):
            if aufraeumen:
                try:
                    await aufraeumen()
                except Exception:
                    pass
        return None


# Liest das LCP-Element so, wie Chrome es selbst bestimmt (PerformanceObserver).
_LCP_SKRIPT = """
() => new Promise((fertig) => {
  let letzter = null;
  try {
    new PerformanceObserver((liste) => {
      const e = liste.getEntries();
      if (e.length) letzter = e[e.length - 1];
    }).observe({ type: 'largest-contentful-paint', buffered: true });
  } catch (err) { fertig(null); return; }
  setTimeout(() => {
    if (!letzter || !letzter.element) { fertig(null); return; }
    const html = letzter.element.outerHTML || '';
    const ende = html.indexOf('>');
    fertig(ende > 0 ? html.slice(0, ende + 1) : html.slice(0, 300));
  }, 1500);
})
"""


async def lcp_element_messen(
    url: str, timeout_ms: int = RENDER_TIMEOUT_MS
) -> Optional[str]:
    """Oeffnen-Tag des LCP-Elements am Handy (412x915, DPR 2,625) — oder None.

    Ersatz, wenn PageSpeed fuer eine Seite kein LCP-Element liefert (kein
    Schluessel, Kontingent, Fehler). Gemessen wird NUR, welches Element es
    ist, keine Zeit: Ein ungedrosselter Browser auf dem Server sagt ueber die
    Ladezeit echter Handys nichts aus.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    pw = browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        from .crawler import USER_AGENT

        seite = await browser.new_page(
            user_agent=USER_AGENT,
            viewport=MOBIL_VIEWPORT,
            is_mobile=True,
            device_scale_factor=2.625,
        )
        await seite.goto(url, wait_until="load", timeout=timeout_ms)
        return await seite.evaluate(_LCP_SKRIPT)
    except Exception as exc:
        logger.warning(f"[renderer] LCP-Element nicht messbar fuer {url}: {exc}")
        return None
    finally:
        for aufraeumen in (browser.close if browser else None, pw.stop if pw else None):
            if aufraeumen:
                try:
                    await aufraeumen()
                except Exception:
                    pass
