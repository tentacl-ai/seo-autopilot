"""Fehlalarme aus dem Rundumschlag 18.09.2026 — jeder zuerst am alten Code rot.

6. image_oversized/image_page_weight: gemessen wurde die groesste
   srcset-Variante statt der, die ein Handy laedt (beratung-beispiel, camping-beispiel).
7. image_lcp_lazy_loaded (high) obwohl das LCP-Element Text ist (natur-beispiel).
+ Zusatz: llms.txt-Links mit Notiz, sameAs Instagram/Google-Profil,
  Sitemap-Adressen fremder Hosts.
"""

from types import SimpleNamespace

import httpx
import pytest

from seo_autopilot.agents.analyzer import AnalyzerAgent
from seo_autopilot.analyzers.bild_variante import (
    slot_breite,
    srcset_kandidaten,
    waehle_kandidat,
)
from seo_autopilot.analyzers.eeat import EEATAnalyzer
from seo_autopilot.analyzers.image_audit import ImageAuditor
from seo_autopilot.analyzers.llms_ai_txt import (
    AiTxtResult,
    IndexNowResult,
    LlmsAiTxtAuditor,
    LlmsTxtResult,
)
from seo_autopilot.sources.pagespeed import _extract_lcp_element, PageSpeedResult

URL = "https://example.com/seite"

# Next.js-Markup wie auf beratung-beispiel.de (gekuerzt)
NEXT_SRCSET = ", ".join(
    f"/_next/image?url=%2Fhero.png&w={w}&q=75 {w}w"
    for w in (640, 750, 828, 1080, 1200, 1920, 2048, 3840)
)
NEXT_IMG = (
    '<img alt="Held" width="1600" height="900" '
    f'sizes="(max-width: 768px) 100vw, 448px" srcset="{NEXT_SRCSET}" '
    'src="/_next/image?url=%2Fhero.png&w=3840&q=75">'
)


def _groessen_client(groessen_nach_w):
    """HEAD-Antwort je nach w-Parameter: gross fuer 3840, klein sonst."""

    def handler(request):
        w = request.url.params.get("w")
        return httpx.Response(
            200,
            headers={
                "content-length": str(groessen_nach_w.get(w, 50_000)),
                "content-type": "image/webp",
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _bild_befunde(html):
    async with _groessen_client({"3840": 912 * 1024, "1200": 97 * 1024}) as c:
        return await ImageAuditor().audit_pages([{"url": URL, "html": html}], client=c)


# ---------------------------------------------------------------------------
# 6. Geladene Variante statt groesster
# ---------------------------------------------------------------------------


class TestGeladeneVariante:
    def test_srcset_parser(self):
        k = srcset_kandidaten("a.jpg 640w,\n b.jpg 1080w")
        assert k == [("a.jpg", "w", 640.0), ("b.jpg", "w", 1080.0)]
        assert srcset_kandidaten("x.jpg 1x, y.jpg 2x")[1] == ("y.jpg", "x", 2.0)

    def test_komma_in_adresse(self):
        k = srcset_kandidaten(
            "https://c.de/w_300,h_200/a.jpg 300w, https://c.de/w_900,h_600/a.jpg 900w"
        )
        assert [u for u, _, _ in k][0] == "https://c.de/w_300,h_200/a.jpg"

    def test_sizes_am_handy(self):
        assert slot_breite("(max-width: 768px) 100vw, 448px") == 412
        assert slot_breite("(min-width: 1024px) 50vw, 90vw") == pytest.approx(370.8)
        assert slot_breite("") == 412
        assert slot_breite("calc(100vw - 2rem)") == 412

    def test_handy_waehlt_passende_breite(self):
        # 412 CSS-px * 2,625 = 1081,5 px -> kleinste Variante darueber: 1200w
        assert "w=1200" in waehle_kandidat(
            NEXT_SRCSET, "(max-width: 768px) 100vw, 448px"
        )

    def test_x_deskriptor(self):
        assert waehle_kandidat("a.jpg 1x, b.jpg 2x, c.jpg 3x") == "c.jpg"
        assert waehle_kandidat("a.jpg 1x, b.jpg 2x") == "b.jpg"

    @pytest.mark.asyncio
    async def test_beratung_beispiel_fall_kein_oversized_mehr(self):
        """Alt: src (w=3840, 912 KB) gemessen -> image_oversized. Neu: w=1200 (97 KB)."""
        befunde = await _bild_befunde(NEXT_IMG)
        assert "image_oversized" not in {b["type"] for b in befunde}

    @pytest.mark.asyncio
    async def test_echt_zu_grosse_variante_bleibt_gemeldet(self):
        async with _groessen_client({"1200": 900 * 1024}) as c:
            befunde = await ImageAuditor().audit_pages(
                [{"url": URL, "html": NEXT_IMG}], client=c
            )
        assert "image_oversized" in {b["type"] for b in befunde}

    @pytest.mark.asyncio
    async def test_seitengewicht_zaehlt_geladene_varianten(self):
        """camping-beispiel: gemeldet 4,3 MB (groesste Varianten), am Handy 1,8 MB."""
        html = "".join(NEXT_IMG.replace("hero", f"bild{i}") for i in range(5))
        async with _groessen_client({"3840": 900 * 1024, "1200": 150 * 1024}) as c:
            befunde = await ImageAuditor().audit_pages(
                [{"url": URL, "html": html}], client=c
            )
        assert "image_page_weight" not in {b["type"] for b in befunde}

    @pytest.mark.asyncio
    async def test_picture_source_webp_wird_gemessen(self):
        html = (
            '<picture><source type="image/avif" srcset="/a.avif 800w, /a-gross.avif 2000w" sizes="100vw">'
            '<img src="/a.jpg" alt="x" width="2000" height="1000"></picture>'
        )
        gemessen = []

        def handler(request):
            gemessen.append(request.url.path)
            return httpx.Response(
                200, headers={"content-length": "1000", "content-type": "image/avif"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await ImageAuditor().audit_pages([{"url": URL, "html": html}], client=c)
        assert gemessen == ["/a-gross.avif"]  # 1081 px noetig -> 2000w


# ---------------------------------------------------------------------------
# 7. LCP-Element aus PageSpeed
# ---------------------------------------------------------------------------


LAZY_BEFUND = {
    "type": "image_lcp_lazy_loaded",
    "severity": "high",
    "affected_url": "https://natur-beispiel.at/einblicke",
    "description": "x",
    "bild_src": "https://natur-beispiel.at/img/wald.jpg",
}


def _psi(lcp_ms, snippet=None):
    return SimpleNamespace(
        url="https://natur-beispiel.at/einblicke",
        error=None,
        lcp_ms=lcp_ms,
        lcp_element_snippet=snippet,
    )


class TestLcpElement:
    def test_lcp_element_ist_text_widerlegt(self):
        """natur-beispiel /einblicke: LCP 3,1 s, aber das LCP-Element ist ein Absatz."""
        psi = [_psi(3100, '<p class="lead">Einblicke in unsere Arbeit</p>')]
        assert AnalyzerAgent._ohne_widerlegte_lcp_befunde([LAZY_BEFUND], psi) == []

    def test_anderes_bild_widerlegt(self):
        psi = [_psi(3100, '<img src="/img/logo-gross.webp">')]
        assert AnalyzerAgent._ohne_widerlegte_lcp_befunde([LAZY_BEFUND], psi) == []

    def test_dasselbe_bild_bestaetigt_high(self):
        psi = [_psi(3100, '<img src="/img/wald.jpg" loading="lazy">')]
        (b,) = AnalyzerAgent._ohne_widerlegte_lcp_befunde([LAZY_BEFUND], psi)
        assert b["severity"] == "high"
        assert "bestaetigt" in b["description"]

    def test_next_image_wird_ueber_url_parameter_erkannt(self):
        befund = dict(
            LAZY_BEFUND, bild_src="https://x.de/_next/image?url=%2Fwald.jpg&w=3840"
        )
        psi = [_psi(3100, '<img src="/_next/image?url=%2Fwald.jpg&amp;w=1200">')]
        assert len(AnalyzerAgent._ohne_widerlegte_lcp_befunde([befund], psi)) == 1

    def test_anderes_next_bild_widerlegt(self):
        befund = dict(
            LAZY_BEFUND, bild_src="https://x.de/_next/image?url=%2Fwald.jpg&w=3840"
        )
        psi = [_psi(3100, '<img src="/_next/image?url=%2Fberg.jpg&amp;w=1200">')]
        assert AnalyzerAgent._ohne_widerlegte_lcp_befunde([befund], psi) == []

    def test_ohne_messung_nur_medium(self):
        """Im Zweifel Schwere senken statt high."""
        (b,) = AnalyzerAgent._ohne_widerlegte_lcp_befunde([LAZY_BEFUND], [])
        assert b["severity"] == "medium"
        assert "Vermutung" in b["description"]

    def test_guter_lcp_widerlegt_weiterhin(self):
        assert (
            AnalyzerAgent._ohne_widerlegte_lcp_befunde([LAZY_BEFUND], [_psi(1800)])
            == []
        )

    def test_andere_befunde_unberuehrt(self):
        anderer = {"type": "image_oversized", "severity": "high", "affected_url": "x"}
        assert AnalyzerAgent._ohne_widerlegte_lcp_befunde([anderer], []) == [anderer]

    def test_pagespeed_liest_lcp_element_neues_format(self):
        audits = {
            "largest-contentful-paint-element": {
                "details": {
                    "type": "list",
                    "items": [
                        {
                            "type": "table",
                            "items": [
                                {
                                    "node": {
                                        "type": "node",
                                        "snippet": "<p>Hallo</p>",
                                        "selector": "main > p",
                                        "nodeLabel": "Hallo",
                                    }
                                }
                            ],
                        },
                        {"type": "table", "items": [{"phase": "TTFB"}]},
                    ],
                }
            }
        }
        r = PageSpeedResult(url="u")
        _extract_lcp_element(r, audits)
        assert r.lcp_element_snippet == "<p>Hallo</p>"
        assert r.lcp_element_selector == "main > p"

    def test_pagespeed_ohne_audit_bleibt_leer(self):
        r = PageSpeedResult(url="u")
        _extract_lcp_element(r, {})
        assert r.lcp_element_snippet is None


# ---------------------------------------------------------------------------
# Zusatz: llms.txt, sameAs, fremde Hosts
# ---------------------------------------------------------------------------


class TestLlmsLinksMitNotiz:
    def test_link_mit_notiz_zaehlt(self):
        """llmstxt.org erlaubt '- [Name](url): Notizen' — tentacl.ai hat 28 solcher Zeilen."""
        roh = "# Firma\n\n> Kurz.\n\n## Seiten\n\n- [Preise](https://x.de/preise): Alle Tarife\n- [Kontakt](https://x.de/kontakt)\n"
        result = LlmsTxtResult(exists=True, raw=roh)
        LlmsAiTxtAuditor()._parse_llms_txt(result)
        assert len(result.links) == 2
        assert result.links[0]["url"] == "https://x.de/preise"

    def test_kein_befund_llms_no_links(self):
        roh = "# Firma\n\n> Kurz.\n\n## Seiten\n\n- [Preise](https://x.de/preise): Alle Tarife\n"
        result = LlmsTxtResult(exists=True, raw=roh)
        auditor = LlmsAiTxtAuditor()
        auditor._parse_llms_txt(result)
        typen = {
            i["type"]
            for i in auditor.detect_issues(
                result,
                LlmsTxtResult(exists=True),
                AiTxtResult(exists=True),
                IndexNowResult(exists=True),
            )
        }
        assert "llms_no_links" not in typen


class TestSameAs:
    def _analyse(self, same_as):
        seiten = [
            {
                "url": "https://x.de/",
                "schema_data": [
                    {"@type": "LocalBusiness", "name": "X", "sameAs": same_as}
                ],
            },
            {"url": "https://x.de/impressum"},
            {"url": "https://x.de/datenschutz"},
        ]
        return {
            i["type"] for i in EEATAnalyzer().analyze(seiten, "https://x.de")["issues"]
        }

    @pytest.mark.parametrize(
        "profil",
        [
            "https://www.instagram.com/camping-beispiel/",
            "https://maps.google.com/?cid=123456",
            "https://www.google.com/maps/place/Camping",
            "https://g.page/camping-beispiel",
            "https://www.tripadvisor.de/Hotel_Review-x",
            "https://www.tiktok.com/@x",
            "https://www.xing.com/pages/x",
            "https://www.provenexpert.com/x/",
        ],
    )
    def test_lokale_profile_zaehlen(self, profil):
        assert "org_schema_no_sameas" not in self._analyse([profil])

    def test_sameas_als_einzelner_text(self):
        assert "org_schema_no_sameas" not in self._analyse(
            "https://www.instagram.com/x/"
        )

    def test_ohne_profil_bleibt_der_befund(self):
        assert "org_schema_no_sameas" in self._analyse(["https://beispiel.de/"])


class TestFremdeHosts:
    @pytest.mark.asyncio
    async def test_sitemap_mit_fremdem_host_wird_nicht_gecrawlt(self):
        from seo_autopilot.sources.crawler import SEOCrawler

        sitemap = (
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.com/a</loc></url>"
            "<url><loc>https://www.example.com/b</loc></url>"
            "<url><loc>https://fremd-kunde.de/x</loc></url></urlset>"
        )

        def handler(request):
            if request.url.path == "/sitemap.xml":
                return httpx.Response(
                    200, text=sitemap, headers={"content-type": "application/xml"}
                )
            return httpx.Response(404)

        crawler = SEOCrawler()
        async with crawler:
            await crawler._client.aclose()
            crawler._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            urls = await crawler.discover_pages("https://example.com", limit=10)
        assert "https://fremd-kunde.de/x" not in urls
        assert "https://www.example.com/b" in urls
        assert crawler.fremde_urls == ["https://fremd-kunde.de/x"]

    def test_befund_fuer_fremde_hosts(self):
        from seo_autopilot.agents.analyzer import _fremde_hosts_befund
        from seo_autopilot.befund_arten import art_von

        b = _fremde_hosts_befund(["https://fremd-kunde.de/x"], "https://example.com")
        assert (b["type"], b["severity"]) == ("sitemap_foreign_host", "medium")
        assert art_von(b["type"]) == "fehler"
        assert "fremd-kunde.de" in b["description"]


class TestLcpElementOhnePageSpeed:
    """Ohne PageSpeed-Element misst Chrome selbst (Playwright), welches Element LCP ist."""

    def _messen(self, monkeypatch, snippet, psi=()):
        import asyncio

        import seo_autopilot.sources.renderer as renderer

        aufrufe = []

        async def fake(url, timeout_ms=0):
            aufrufe.append(url)
            return snippet

        monkeypatch.setattr(renderer, "lcp_element_messen", fake)
        messungen = asyncio.run(
            AnalyzerAgent._lcp_elemente_messen([LAZY_BEFUND], list(psi))
        )
        return messungen, aufrufe

    def test_text_element_widerlegt_ohne_pagespeed(self, monkeypatch):
        messungen, aufrufe = self._messen(monkeypatch, '<p class="text-sm">')
        assert aufrufe == ["https://natur-beispiel.at/einblicke"]
        assert (
            AnalyzerAgent._ohne_widerlegte_lcp_befunde([LAZY_BEFUND], messungen) == []
        )

    def test_pagespeed_zeit_bleibt_erhalten(self, monkeypatch):
        messungen, _ = self._messen(
            monkeypatch, '<img src="/img/wald.jpg">', psi=[_psi(3900)]
        )
        assert messungen[0].lcp_ms == 3900
        (b,) = AnalyzerAgent._ohne_widerlegte_lcp_befunde(
            [LAZY_BEFUND], [_psi(3900)] + messungen
        )
        assert b["severity"] == "high"

    def test_kein_browser_messung_bleibt_vermutung(self, monkeypatch):
        messungen, _ = self._messen(monkeypatch, None)
        assert messungen == []
        (b,) = AnalyzerAgent._ohne_widerlegte_lcp_befunde([LAZY_BEFUND], messungen)
        assert b["severity"] == "medium"

    def test_pagespeed_element_vorhanden_keine_browsermessung(self, monkeypatch):
        _, aufrufe = self._messen(monkeypatch, "<p>", psi=[_psi(3900, "<p>x</p>")])
        assert aufrufe == []
