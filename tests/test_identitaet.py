"""Tests fuer den Identitaetsschutz (identitaet.py, projects.yaml-Feld ``erwartet``).

Anlass 18.09.2026: Unter einer Port-Adresse wurde eine ganz andere
Kundenseite als "camping-beispiel" geprueft — 38 Befunde, 9 Freigaben, 14 Eintraege
im Aenderungsbuch. Kein Netz: httpx.MockTransport bzw. monkeypatch.
"""

import asyncio

import httpx
import pytest

from seo_autopilot import identitaet
from seo_autopilot.core.project_manager import ProjectConfig, ProjectManager
from seo_autopilot.identitaet import pruefe_html, pruefe_identitaet

CAMPING = (
    "<html><head><title>Camping am See – camping-beispiel</title>"
    '<script type="application/ld+json">{"@type":"Campground","name":"Campingplatz camping-beispiel"}</script>'
    "</head><body><h1>Willkommen am Bodensee</h1></body></html>"
)
FREMD = "<html><head><title>Friseur Schmidt</title></head><body><h1>Haarschnitt</h1></body></html>"


def _client(status=200, html=CAMPING, fehler=False):
    def handler(request):
        if fehler:
            raise httpx.ConnectError("weg", request=request)
        return httpx.Response(status, text=html, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestPruefeHtml:
    def test_schema_name_reicht(self):
        assert pruefe_html(CAMPING, "Campingplatz camping-beispiel")[0]

    def test_gross_klein_und_umlaute_egal(self):
        html = "<title>Café Müller am Hafen</title>"
        assert pruefe_html(html, "cafe mueller")[0]
        assert pruefe_html(html, "CAFÉ  MÜLLER")[0]

    def test_liste_eine_reicht(self):
        assert pruefe_html(CAMPING, ["Gibt es nicht", "Camping am See"])[0]

    def test_falsche_website(self):
        ok, grund = pruefe_html(FREMD, "Campingplatz camping-beispiel")
        assert not ok
        assert "falsche Website" in grund

    def test_ohne_erwartung_immer_ok(self):
        assert pruefe_html(FREMD, None)[0]
        assert pruefe_html(FREMD, "")[0]


@pytest.mark.asyncio
class TestAbruf:
    async def test_nicht_erreichbar_ist_unbestaetigt(self):
        async with _client(fehler=True) as c:
            ok, grund = await pruefe_identitaet("https://x.de", "Camping", client=c)
        assert not ok and "nicht abrufbar" in grund

    async def test_http_fehler_ist_unbestaetigt(self):
        async with _client(status=502) as c:
            assert not (await pruefe_identitaet("https://x.de", "Camping", client=c))[0]

    async def test_ohne_erwartung_kein_abruf(self):
        def handler(request):
            raise AssertionError("darf nicht abrufen")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            assert (await pruefe_identitaet("https://x.de", None, client=c))[0]


class TestKonfiguration:
    def test_feld_ueberlebt_das_speichern(self, tmp_path):
        """Unbekannte Felder verwirft _save_config still (CHANGELOG 1.14.0)."""
        pfad = tmp_path / "projects.yaml"
        pfad.write_text(
            "projects:\n  camp:\n    domain: https://x.de\n    name: Camp\n"
            "    erwartet: Campingplatz camping-beispiel\n"
        )
        pm = ProjectManager(str(pfad))
        assert pm.get_project("camp").erwartet == "Campingplatz camping-beispiel"
        pm.update_project("camp", name="Camp 2")
        assert (
            ProjectManager(str(pfad)).get_project("camp").erwartet
            == "Campingplatz camping-beispiel"
        )


class TestAuditBrichtAb:
    def test_falsche_website_stoppt_vor_jeder_analyse(self, monkeypatch):
        import seo_autopilot.api.main as api_main

        projekt = ProjectConfig(
            id="camp",
            domain="https://x.de",
            name="Camp",
            erwartet="Campingplatz camping-beispiel",
        )
        gestartet = []
        gespeichert = []

        class KeinAgent:
            def __init__(self, *a, **k):
                gestartet.append(self)

        async def falsch(domain, erwartet, client=None):
            return False, "Startseite enthaelt keinen der erwarteten Texte"

        async def speichern(ctx):
            gespeichert.append((ctx.status, ctx.error))

        async def still(*a, **k):
            return None

        monkeypatch.setattr(api_main.project_manager, "get_project", lambda i: projekt)
        monkeypatch.setattr(identitaet, "pruefe_identitaet", falsch)
        for name in (
            "AnalyzerAgent",
            "KeywordAgent",
            "TrendsAgent",
            "StrategyAgent",
            "ContentAgent",
            "ApplyAgent",
        ):
            monkeypatch.setattr(api_main, name, KeinAgent)
        monkeypatch.setattr(api_main, "persist_audit", speichern)
        monkeypatch.setattr(api_main.event_bus, "emit", still)

        audit_id = asyncio.run(api_main.run_audit_for_project("camp", force_apply=True))
        assert gestartet == []  # weder Analyse noch Auto-Fix
        assert api_main.LAUF_STATUS[audit_id] == "failed"
        assert gespeichert and gespeichert[0][0] == "failed"
        assert "Identitaet" in gespeichert[0][1]

    def test_ohne_feld_laeuft_alles_wie_bisher(self, monkeypatch):
        import seo_autopilot.api.main as api_main

        projekt = ProjectConfig(id="p", domain="https://x.de", name="P")

        async def nie(*a, **k):
            raise AssertionError("ohne erwartet darf nicht geprueft werden")

        monkeypatch.setattr(identitaet, "pruefe_identitaet", nie)
        asyncio.run(identitaet.sicherstellen(projekt))
