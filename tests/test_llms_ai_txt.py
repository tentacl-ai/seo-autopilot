"""Tests for LLMs.txt + AI.txt + IndexNow Audit (Phase 11)."""

import pytest
from seo_autopilot.analyzers.llms_ai_txt import (
    LlmsAiTxtAuditor,
    LlmsTxtResult,
    AiTxtResult,
    IndexNowResult,
)


@pytest.fixture
def auditor():
    return LlmsAiTxtAuditor()


# ---------------------------------------------------------------------------
# llms.txt tests
# ---------------------------------------------------------------------------


class TestLlmsTxtIssues:
    def test_missing_llms_txt(self, auditor):
        llms = LlmsTxtResult(exists=False, status_code=404)
        llms_full = LlmsTxtResult(exists=False, status_code=404)
        ai = AiTxtResult(exists=False, status_code=404)
        indexnow = IndexNowResult(exists=False)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "missing_llms_txt" in types

    def test_valid_llms_txt_no_syntax_issues(self, auditor):
        llms = LlmsTxtResult(
            exists=True,
            status_code=200,
            raw=(
                "# My Website\n\n"
                "A great website about cool stuff.\n\n"
                "## Docs\n\n"
                "- [API Reference](https://example.com/api)\n"
                "- [Guide](https://example.com/guide)\n"
            ),
        )
        auditor._parse_llms_txt(llms)
        llms_full = LlmsTxtResult(exists=True, status_code=200)
        ai = AiTxtResult(exists=True, status_code=200)
        indexnow = IndexNowResult(exists=True)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "missing_llms_txt" not in types
        assert "invalid_llms_syntax" not in types
        assert "llms_no_links" not in types

    def test_invalid_llms_syntax_no_h1(self, auditor):
        llms = LlmsTxtResult(
            exists=True,
            status_code=200,
            raw="Just some text without a heading\n",
        )
        auditor._parse_llms_txt(llms)
        llms_full = LlmsTxtResult(exists=False, status_code=404)
        ai = AiTxtResult(exists=False, status_code=404)
        indexnow = IndexNowResult(exists=False)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "invalid_llms_syntax" in types

    def test_llms_no_links(self, auditor):
        llms = LlmsTxtResult(
            exists=True,
            status_code=200,
            raw="# My Website\n\nJust a description, no links.\n",
        )
        auditor._parse_llms_txt(llms)
        llms_full = LlmsTxtResult(exists=False, status_code=404)
        ai = AiTxtResult(exists=False, status_code=404)
        indexnow = IndexNowResult(exists=False)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "llms_no_links" in types

    def test_llms_empty_file(self, auditor):
        llms = LlmsTxtResult(exists=True, status_code=200, raw="")
        auditor._parse_llms_txt(llms)
        assert len(llms.parse_errors) > 0


# ---------------------------------------------------------------------------
# llms-full.txt tests
# ---------------------------------------------------------------------------


class TestLlmsFullTxtIssues:
    def test_missing_llms_full_txt(self, auditor):
        llms = LlmsTxtResult(
            exists=True, status_code=200, raw="# Site\n", has_title=True
        )
        llms_full = LlmsTxtResult(exists=False, status_code=404)
        ai = AiTxtResult(exists=True, status_code=200)
        indexnow = IndexNowResult(exists=True)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "missing_llms_full_txt" in types


# ---------------------------------------------------------------------------
# ai.txt tests
# ---------------------------------------------------------------------------


class TestAiTxtIssues:
    def test_missing_ai_txt(self, auditor):
        llms = LlmsTxtResult(
            exists=True, status_code=200, raw="# Site\n", has_title=True
        )
        llms_full = LlmsTxtResult(exists=True, status_code=200)
        ai = AiTxtResult(exists=False, status_code=404)
        indexnow = IndexNowResult(exists=True)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "missing_ai_txt" in types

    def test_ai_txt_present_no_issue(self, auditor):
        llms = LlmsTxtResult(
            exists=True, status_code=200, raw="# Site\n", has_title=True
        )
        llms_full = LlmsTxtResult(exists=True, status_code=200)
        ai = AiTxtResult(exists=True, status_code=200)
        indexnow = IndexNowResult(exists=True)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "missing_ai_txt" not in types


# ---------------------------------------------------------------------------
# IndexNow tests
# ---------------------------------------------------------------------------


class TestIndexNowIssues:
    def test_missing_indexnow(self, auditor):
        llms = LlmsTxtResult(
            exists=True, status_code=200, raw="# Site\n", has_title=True
        )
        llms_full = LlmsTxtResult(exists=True, status_code=200)
        ai = AiTxtResult(exists=True, status_code=200)
        indexnow = IndexNowResult(exists=False)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "missing_indexnow" in types

    def test_indexnow_present_no_issue(self, auditor):
        llms = LlmsTxtResult(
            exists=True, status_code=200, raw="# Site\n", has_title=True
        )
        llms_full = LlmsTxtResult(exists=True, status_code=200)
        ai = AiTxtResult(exists=True, status_code=200)
        indexnow = IndexNowResult(exists=True)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        types = [i["type"] for i in issues]
        assert "missing_indexnow" not in types


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


class TestLlmsTxtParsing:
    def test_parse_title(self, auditor):
        result = LlmsTxtResult(
            exists=True,
            status_code=200,
            raw="# My Cool Website\n\nSome description.\n",
        )
        auditor._parse_llms_txt(result)
        assert result.has_title is True
        assert result.title == "My Cool Website"

    def test_parse_description(self, auditor):
        result = LlmsTxtResult(
            exists=True,
            status_code=200,
            raw="# Site\n\nThis is the description.\n\n## Links\n",
        )
        auditor._parse_llms_txt(result)
        assert "description" in result.description.lower() or result.description

    def test_parse_sections(self, auditor):
        result = LlmsTxtResult(
            exists=True,
            status_code=200,
            raw="# Site\n\n## Docs\n\n## API\n\n## Optional\n",
        )
        auditor._parse_llms_txt(result)
        assert "Docs" in result.sections
        assert "API" in result.sections
        assert "Optional" in result.sections

    def test_parse_links(self, auditor):
        result = LlmsTxtResult(
            exists=True,
            status_code=200,
            raw=(
                "# Site\n\n"
                "## Docs\n\n"
                "- [API Docs](https://example.com/api)\n"
                "- [Guide](https://example.com/guide)\n"
            ),
        )
        auditor._parse_llms_txt(result)
        assert len(result.links) == 2
        assert result.links[0]["label"] == "API Docs"
        assert result.links[0]["url"] == "https://example.com/api"

    def test_all_present_no_issues(self, auditor):
        """False-positive test: everything configured produces no issues."""
        llms = LlmsTxtResult(
            exists=True,
            status_code=200,
            raw="# Site\n\n## Docs\n\n- [Link](https://example.com)\n",
        )
        auditor._parse_llms_txt(llms)
        llms_full = LlmsTxtResult(exists=True, status_code=200)
        ai = AiTxtResult(exists=True, status_code=200)
        indexnow = IndexNowResult(exists=True)
        issues = auditor.detect_issues(llms, llms_full, ai, indexnow)
        assert issues == []
