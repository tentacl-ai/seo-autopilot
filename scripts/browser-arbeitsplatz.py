"""Desktop/mobile acceptance against a disposable local demo, no customer data."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("SEO_BROWSER_ARTIFACTS", "/tmp/seo-workspace-browser"))
OUT.mkdir(parents=True, exist_ok=True)

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
errors = []
with tempfile.TemporaryFile() as log:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "seo_autopilot.arbeitsplatz",
            "--demo",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError("Demo start failed")
            try:
                with urlopen(base + "/mode", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Demo did not start")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1050})
            context.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if route.request.url.startswith(base + "/")
                    else route.abort()
                ),
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/")
            page.get_by_label("Zugangscode").fill("demo")
            page.get_by_role("button", name="Anmelden", exact=True).click()
            page.get_by_role("heading", name="Überblick", exact=True).wait_for()
            page.get_by_text(
                "Fragen vor der Anfrage beantworten", exact=True
            ).wait_for()
            assert page.locator("#token").input_value() == ""
            page.screenshot(path=str(OUT / "desktop.png"), full_page=True)

            def navigate(name):
                page.get_by_role("navigation").get_by_role(
                    "button", name=name, exact=True
                ).click()
                page.get_by_role("heading", name=name, exact=True).wait_for()
                expect(page.locator("#project")).to_be_enabled()

            navigate("Projektakte")
            goal = page.locator(".panel").filter(
                has=page.get_by_role("heading", name="Aktuelles Ziel", exact=True)
            )
            goal.get_by_label("Inhalt", exact=True).fill(
                "Mehr passende Anfragen statt bloß mehr Klicks."
            )
            goal.get_by_role("button", name="Speichern", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("Gespeichert")
            navigate("Überblick")
            page.get_by_text(
                "Mehr passende Anfragen statt bloß mehr Klicks.", exact=True
            ).wait_for()

            navigate("Keywords & Seiten")
            page.get_by_label("Suchbegriff", exact=True).fill("Kosten für Coaching")
            page.get_by_label("Suchabsicht", exact=True).select_option("commercial")
            page.get_by_label("Vorhandene Zielseite (optional)").fill(
                "https://beispiel.invalid/coaching"
            )
            page.get_by_label("Themen, mit Komma getrennt").fill("Coaching")
            page.get_by_role("button", name="Speichern", exact=True).click()
            page.get_by_role("cell", name="Kosten für Coaching", exact=True).wait_for()

            for name in [
                "SEO-Agent",
                "Änderungen",
                "Google",
                "KI-Sichtbarkeit",
                "Local SEO",
                "Backlinks",
                "Maßnahmen",
                "Wirkung",
                "Recherche",
            ]:
                navigate(name)
                assert not page.locator("#notice").inner_text()

            navigate("Messaufträge")
            page.get_by_label("Suchbegriffe oder Fragen, eine pro Zeile").fill(
                "coaching am see"
            )
            page.get_by_role("button", name="Speichern", exact=True).click()
            page.get_by_role("button", name="Kosten & Start", exact=True).wait_for()
            page.get_by_role("button", name="Kosten & Start", exact=True).click()
            page.get_by_role("status").get_by_text(
                "Datenabrufe sind ausgeschaltet.", exact=False
            ).wait_for()

            navigate("Berichte")
            page.get_by_role("button", name="Bericht aus Messdaten erstellen").click()
            page.get_by_role("button", name="Öffnen", exact=True).wait_for()
            page.get_by_role("button", name="Öffnen", exact=True).click()
            page.locator(".report").wait_for()
            page.get_by_text("Bericht bearbeiten", exact=True).click()
            page.get_by_label("Berichtstitel").fill("Geprüfter Beispielbericht")
            page.get_by_label("Berichtstext").fill(
                "Aktueller Stand aus Beispieldaten. Keine Aussage über echte Kunden."
            )
            page.locator(".report").get_by_role(
                "button", name="Speichern", exact=True
            ).click()
            page.get_by_role(
                "cell", name="Geprüfter Beispielbericht", exact=True
            ).wait_for()
            page.get_by_role("button", name="Öffnen", exact=True).click()
            page.get_by_label("Berichtsfassung").select_option("1")
            page.locator(".report h2").filter(has_text="SEO-Stand").wait_for()

            navigate("Überblick")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(OUT / "mobile.png"), full_page=True)
            assert page.evaluate(
                "() => document.documentElement.scrollWidth <= innerWidth"
            ), "Mobile page overflows"
            page.get_by_role("button", name="Abmelden", exact=True).click()
            page.locator("#login").wait_for()
            assert not page.locator("#content").inner_text()
            assert page.evaluate("() => Object.keys(localStorage).length") == 0
            assert page.evaluate("() => Object.keys(sessionStorage).length") == 0
            assert not errors, errors
            browser.close()
        print(
            json.dumps(
                {
                    "result": "passed",
                    "desktop": "1440x1050",
                    "mobile": "390x844",
                    "errors": errors,
                    "artifacts": str(OUT),
                }
            )
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
