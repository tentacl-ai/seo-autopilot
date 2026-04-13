"""Tests for Redirect Audit + Soft-404 Detection."""

import pytest
from seo_autopilot.analyzers.redirect_audit import (
    RedirectAuditor,
    RedirectChain,
    RedirectHop,
    PageForRedirectAudit,
    is_soft_404,
)


@pytest.fixture
def auditor():
    return RedirectAuditor()


class TestRedirectChainDetection:
    def test_detects_redirect_chain(self, auditor):
        chains = [
            RedirectChain(
                start_url="https://example.com/old",
                hops=[
                    RedirectHop("https://example.com/old", 301, "https://example.com/mid"),
                    RedirectHop("https://example.com/mid", 301, "https://example.com/new"),
                ],
                final_url="https://example.com/new",
                final_status=200,
                chain_length=2,
            )
        ]
        issues = auditor.detect_issues([], chains=chains)
        types = [i["type"] for i in issues]
        assert "redirect_chain" in types

    def test_detects_redirect_loop(self, auditor):
        chains = [
            RedirectChain(
                start_url="https://example.com/a",
                hops=[
                    RedirectHop("https://example.com/a", 301, "https://example.com/b"),
                    RedirectHop("https://example.com/b", 301, "https://example.com/a"),
                ],
                final_url="https://example.com/a",
                final_status=0,
                is_loop=True,
                chain_length=2,
            )
        ]
        issues = auditor.detect_issues([], chains=chains)
        types = [i["type"] for i in issues]
        assert "redirect_loop" in types
        loop_issue = [i for i in issues if i["type"] == "redirect_loop"][0]
        assert loop_issue["severity"] == "critical"

    def test_detects_302_should_be_301(self, auditor):
        chains = [
            RedirectChain(
                start_url="https://example.com/page",
                hops=[
                    RedirectHop("https://example.com/page", 302, "https://example.com/new"),
                ],
                final_url="https://example.com/new",
                final_status=200,
                chain_length=1,
            )
        ]
        issues = auditor.detect_issues([], chains=chains)
        types = [i["type"] for i in issues]
        assert "redirect_302_should_be_301" in types

    def test_detects_redirect_to_different_domain(self, auditor):
        chains = [
            RedirectChain(
                start_url="https://example.com/page",
                hops=[
                    RedirectHop("https://example.com/page", 301, "https://other.com/page"),
                ],
                final_url="https://other.com/page",
                final_status=200,
                chain_length=1,
            )
        ]
        issues = auditor.detect_issues([], chains=chains)
        types = [i["type"] for i in issues]
        assert "redirect_to_different_domain" in types

    def test_detects_internal_links_to_redirects(self, auditor):
        chains = [
            RedirectChain(
                start_url="https://example.com/old",
                hops=[RedirectHop("https://example.com/old", 301, "https://example.com/new")],
                final_url="https://example.com/new",
                final_status=200,
                chain_length=1,
            )
        ]
        pages = [
            PageForRedirectAudit(
                url="https://example.com/home",
                internal_link_targets=["https://example.com/old"],
            )
        ]
        issues = auditor.detect_issues(pages, chains=chains)
        types = [i["type"] for i in issues]
        assert "internal_link_to_redirect" in types


class TestSoft404:
    def test_detects_soft_404(self):
        page = PageForRedirectAudit(
            url="https://example.com/missing",
            status_code=200,
            title="Page Not Found - Example",
            h1="",
            word_count=30,
        )
        assert is_soft_404(page) is True

    def test_no_false_positive_on_real_page(self):
        page = PageForRedirectAudit(
            url="https://example.com/about",
            status_code=200,
            title="About Us - Example Company",
            h1="About Us",
            word_count=500,
        )
        assert is_soft_404(page) is False

    def test_soft_404_german(self):
        page = PageForRedirectAudit(
            url="https://example.com/alt",
            status_code=200,
            title="Seite nicht gefunden",
            h1="",
            word_count=20,
        )
        assert is_soft_404(page) is True

    def test_soft_404_in_issues(self, auditor):
        pages = [
            PageForRedirectAudit(
                url="https://example.com/gone",
                status_code=200,
                title="404 - Not Found",
                h1="",
                word_count=25,
            )
        ]
        issues = auditor.detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "soft_404" in types


class Test5xxCluster:
    def test_detects_5xx_cluster(self, auditor):
        pages = [
            PageForRedirectAudit(url=f"https://example.com/error-{i}", status_code=500)
            for i in range(5)
        ]
        issues = auditor.detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "5xx_cluster" in types
