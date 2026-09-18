"""Regression tests for the false-positive fixes of 2026-08-17.

All four findings below were reported as "high" on beratung-beispiel.de
although the site was correct. Each test pins the corrected behaviour.
"""

from bs4 import BeautifulSoup

from seo_autopilot.analyzers.eeat import EEATAnalyzer, _is_organization
from seo_autopilot.analyzers.robots_sitemap import (
    RobotsSitemapAuditor,
    SitemapResult,
    SitemapUrl,
)
from seo_autopilot.sources.crawler import PageData, SEOCrawler, _parse_html_into

# --- 1. Organization schema: subtypes and @type arrays --------------------


class TestOrganizationDetection:
    def test_plain_organization(self):
        assert _is_organization({"@type": "Organization"})

    def test_subtype_counts_as_organization(self):
        """FinancialService IS an Organization — was reported as missing."""
        assert _is_organization({"@type": "FinancialService"})
        assert _is_organization({"@type": "LocalBusiness"})

    def test_type_array(self):
        assert _is_organization({"@type": ["Organization", "FinancialService"]})

    def test_non_organization(self):
        assert not _is_organization({"@type": "Person"})
        assert not _is_organization({"@type": ["Article", "WebPage"]})
        assert not _is_organization({})

    def test_analyzer_accepts_financialservice(self):
        pages = [
            {
                "url": "https://example.com",
                "schema_data": [{"@type": ["Organization", "FinancialService"]}],
            }
        ]
        result = EEATAnalyzer().analyze(pages, "https://example.com")
        assert result["signals"]["org_schema"] is True
        assert not any(i["type"] == "missing_org_schema" for i in result["issues"])


# --- 2. Crawl order: trust pages must survive the page limit --------------


class TestTrustPagePriority:
    def test_legal_pages_pulled_in_front_of_limit(self):
        """Impressum/Datenschutz sit at the end of a sitemap and were cut off."""
        urls = ["https://s.de"] + [f"https://s.de/p{i}" for i in range(20)]
        urls += ["https://s.de/impressum", "https://s.de/datenschutz"]

        ordered = SEOCrawler._prioritize(urls)[:15]

        assert "https://s.de/impressum" in ordered
        assert "https://s.de/datenschutz" in ordered
        assert ordered[0] == "https://s.de", "root must stay first"

    def test_order_otherwise_stable(self):
        urls = ["https://s.de", "https://s.de/a", "https://s.de/b"]
        assert SEOCrawler._prioritize(urls) == urls


# --- 3. Sitemap: uncrawled pages are unknown, not non-canonical -----------


class TestSitemapCanonicalCheck:
    def _sitemap(self, locs):
        return SitemapResult(
            url="https://s.de/sitemap.xml",
            exists=True,
            status_code=200,
            urls=[SitemapUrl(loc=u, lastmod="2026-01-01") for u in locs],
        )

    def test_uncrawled_url_is_not_flagged(self):
        auditor = RobotsSitemapAuditor()
        sitemap = self._sitemap(["https://s.de/a", "https://s.de/impressum"])
        issues = auditor.detect_sitemap_issues(
            sitemap,
            canonical_urls={"https://s.de/a"},
            crawled_urls={"https://s.de/a"},  # impressum was never fetched
        )
        assert not any(i["type"] == "sitemap_non_canonical_urls" for i in issues)

    def test_trailing_slash_is_not_a_mismatch(self):
        auditor = RobotsSitemapAuditor()
        sitemap = self._sitemap(["https://s.de/"])
        issues = auditor.detect_sitemap_issues(
            sitemap,
            canonical_urls={"https://s.de"},
            crawled_urls={"https://s.de/"},
        )
        assert not any(i["type"] == "sitemap_non_canonical_urls" for i in issues)

    def test_real_mismatch_still_reported(self):
        auditor = RobotsSitemapAuditor()
        sitemap = self._sitemap(["https://s.de/page?ref=123"])
        issues = auditor.detect_sitemap_issues(
            sitemap,
            canonical_urls={"https://s.de/page"},
            crawled_urls={"https://s.de/page?ref=123"},
        )
        assert any(i["type"] == "sitemap_non_canonical_urls" for i in issues)


# --- 4. Decorative images: empty alt is correct, missing alt is not -------


class TestImageAltCounting:
    def _count(self, html: str) -> int:
        page = PageData(url="https://s.de", status_code=200)
        _parse_html_into(page, f"<html><body>{html}</body></html>")
        return page.images_without_alt

    def test_empty_alt_is_valid_for_decorative_images(self):
        assert self._count('<img src="bg.jpg" alt="">') == 0

    def test_missing_alt_is_still_a_defect(self):
        assert self._count('<img src="photo.jpg">') == 1

    def test_role_presentation_is_decorative(self):
        assert self._count('<img src="bg.jpg" role="presentation">') == 0
        assert self._count('<img src="bg.jpg" aria-hidden="true">') == 0

    def test_mixed_page(self):
        html = (
            '<img src="a.jpg" alt="Ein Portrait">'
            '<img src="b.jpg" alt="">'
            '<img src="c.jpg">'
        )
        assert self._count(html) == 1


def test_bs4_available():
    """Guard: the alt-counting tests rely on the same parser as production."""
    assert BeautifulSoup("<img>", "html.parser").find("img") is not None


# --- 5. Fehlalarme vom 16.09.2026 (beratung-beispiel.de) ------------------


class TestUeberMichSeite:
    """„Über mich" ist bei Einzelberatern die Über-Seite."""

    def _befunde(self, pfade):
        pages = [
            {"url": f"https://example.com{p}", "html": "", "schema": []} for p in pfade
        ]
        return {
            i["type"]
            for i in EEATAnalyzer().analyze(pages, "https://example.com")["issues"]
        }

    def test_ueber_mich_zaehlt_als_ueber_seite(self):
        assert "missing_about_page" not in self._befunde(["/", "/ueber-mich"])

    def test_ohne_ueber_seite_bleibt_der_befund(self):
        assert "missing_about_page" in self._befunde(["/", "/leistungen"])


class TestDoppelseiteNurMitWortvergleich:
    """SimHash allein meldete zwei sehr unterschiedliche Seiten als Doppelung."""

    def _seiten(self, text_a, text_b):
        return [
            {
                "url": "https://example.com/a",
                "text_content": text_a,
                "word_count": len(text_a.split()),
                "h1": ["A"],
            },
            {
                "url": "https://example.com/b",
                "text_content": text_b,
                "word_count": len(text_b.split()),
                "h1": ["B"],
            },
        ]

    def _befunde(self, seiten):
        from seo_autopilot.analyzers.duplicate_content import DuplicateContentDetector

        return {i["type"] for i in DuplicateContentDetector().detect_issues(seiten)}

    def test_gleicher_text_bleibt_doppelseite(self):
        text = (
            "Factoring verschafft Unternehmen Liquiditaet aus offenen Rechnungen binnen 48 Stunden. "
            * 8
        )
        assert "near_duplicate_content" in self._befunde(
            self._seiten(text, text + " Kleiner Zusatz.")
        )

    def test_wenig_gemeinsame_woerter_ist_keine_doppelseite(self):
        from seo_autopilot.analyzers.duplicate_content import wortueberschneidung

        a = (
            "Factoring Forderungsverkauf Liquiditaet Rechnungen Debitoren Bonitaet Auszahlung Ausfallschutz "
            * 12
        )
        b = (
            "Vorsorgevollmacht Patientenverfuegung Notfallordner Geschaeftsfuehrer Handlungsfaehigkeit Notar Betreuung "
            * 12
        )
        assert wortueberschneidung(a, b) < 0.4
        assert "near_duplicate_content" not in self._befunde(self._seiten(a, b))

    def test_ohne_text_bleibt_der_simhash_befund_unangetastet(self):
        from seo_autopilot.analyzers.duplicate_content import wortueberschneidung

        assert wortueberschneidung("", "irgendwas") == 1.0
