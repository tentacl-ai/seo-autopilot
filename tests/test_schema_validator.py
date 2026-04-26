"""Tests for Schema Validator — JSON-LD required field checks."""

import pytest
from seo_autopilot.analyzers.schema_validation import SchemaValidator


@pytest.fixture
def validator():
    return SchemaValidator()


class TestSchemaBlockValidation:
    def test_valid_article(self, validator):
        schema = {
            "@type": "Article",
            "headline": "Test Artikel",
            "author": {"@type": "Person", "name": "Max"},
            "datePublished": "2026-04-01",
        }
        result = validator.validate_schema_block(
            schema, "https://example.com/blog/test"
        )
        assert result["is_valid"] is True
        assert result["missing_required"] == []

    def test_detects_missing_author_in_article(self, validator):
        schema = {
            "@type": "Article",
            "headline": "Test",
            "datePublished": "2026-04-01",
        }
        result = validator.validate_schema_block(
            schema, "https://example.com/blog/test"
        )
        assert result["is_valid"] is False
        assert "author" in result["missing_required"]

    def test_detects_no_type(self, validator):
        schema = {"name": "Test"}
        result = validator.validate_schema_block(schema, "https://example.com")
        assert result["is_valid"] is False
        assert "No @type defined" in result["errors"]

    def test_validates_product_offers(self, validator):
        schema = {
            "@type": "Product",
            "name": "Widget",
            "offers": {"@type": "Offer"},  # no price
        }
        result = validator.validate_schema_block(schema, "https://example.com/product")
        assert any("price" in e for e in result["errors"])

    def test_valid_product(self, validator):
        schema = {
            "@type": "Product",
            "name": "Widget",
            "offers": {"@type": "Offer", "price": "19.99"},
        }
        result = validator.validate_schema_block(schema, "https://example.com/product")
        assert result["is_valid"] is True


class TestFaqValidation:
    def test_validates_faq_schema_structure(self, validator):
        schema = {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "Was ist SEO?",
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "SEO ist Suchmaschinenoptimierung.",
                    },
                }
            ],
        }
        result = validator.validate_schema_block(schema, "https://example.com/faq")
        assert result["is_valid"] is True
        assert result["errors"] == []

    def test_detects_faq_missing_answer(self, validator):
        schema = {
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": "Was ist SEO?"},
            ],
        }
        result = validator.validate_schema_block(schema, "https://example.com/faq")
        assert any("acceptedAnswer" in e for e in result["errors"])

    def test_detects_faq_wrong_type(self, validator):
        schema = {
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Answer", "text": "falsch"},
            ],
        }
        result = validator.validate_schema_block(schema, "https://example.com/faq")
        assert any(
            "not @type=Question" in e or "Question" in e for e in result["errors"]
        )


class TestBreadcrumbValidation:
    def test_detects_missing_position(self, validator):
        schema = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "name": "Home"},
            ],
        }
        result = validator.validate_schema_block(schema, "https://example.com/page")
        assert any("position" in e for e in result["errors"])


class TestIssueDetection:
    def test_detects_json_ld_without_type(self, validator):
        pages = [{"url": "https://example.com", "schema_data": [{"name": "Test"}]}]
        issues = validator.detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "schema_syntax_error" in types

    def test_detects_missing_required_fields(self, validator):
        pages = [
            {
                "url": "https://example.com/blog/test",
                "schema_data": [{"@type": "Article", "headline": "Test"}],
            }
        ]
        issues = validator.detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "schema_missing_required_field" in types

    def test_identifies_rich_result_opportunity(self, validator):
        pages = [
            {
                "url": "https://example.com/blog/post",
                "schema_data": [
                    {
                        "@type": "Article",
                        "headline": "X",
                        "author": "Y",
                        "datePublished": "2026-01-01",
                    }
                ],
            }
        ]
        issues = validator.detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "schema_rich_result_opportunity" in types

    def test_no_false_positive_valid_schema(self, validator):
        pages = [
            {
                "url": "https://example.com",
                "schema_data": [
                    {
                        "@type": "Organization",
                        "name": "Tentacl",
                        "url": "https://tentacl.ai",
                    }
                ],
            }
        ]
        issues = validator.detect_issues(pages)
        # Only BreadcrumbList opportunity expected (root has depth 0 -> no issue)
        error_issues = [
            i for i in issues if i["type"] not in ("schema_rich_result_opportunity",)
        ]
        assert len(error_issues) == 0
