

class TestAbgeschalteteRichResults:
    """Google zeigt fuer FAQPage/HowTo & Co. nichts mehr an (claude-seo Liste, Stand 23.09.2026)."""

    def test_faqpage_ohne_mainentity_ist_nur_hinweis(self):
        from seo_autopilot.analyzers.schema_validation import SchemaValidator

        pages = [{"url": "https://x.de/faq", "schema_data": [{"@type": "FAQPage"}]}]
        issues = SchemaValidator().detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "schema_missing_required_field" not in types
        assert "schema_rich_result_retired" in types
        assert all(i["severity"] == "info" for i in issues if i["type"] == "schema_rich_result_retired")

    def test_kaputtes_howto_ist_kein_fehler(self):
        from seo_autopilot.analyzers.schema_validation import SchemaValidator

        pages = [{"url": "https://x.de/", "schema_data": [{"@type": "HowTo", "name": "x"}]}]
        types = [i["type"] for i in SchemaValidator().detect_issues(pages)]
        assert "schema_missing_required_field" not in types
        assert "schema_syntax_error" not in types

    def test_hinweis_zaehlt_nicht_in_die_note(self):
        from seo_autopilot.befund_arten import HINWEIS, art_von

        assert art_von("schema_rich_result_retired") == HINWEIS
        assert art_von("ai_training_blocked") == HINWEIS

    def test_lebende_typen_weiter_geprueft(self):
        from seo_autopilot.analyzers.schema_validation import SchemaValidator

        pages = [{"url": "https://x.de/p", "schema_data": [{"@type": "Product", "name": "x"}]}]
        types = [i["type"] for i in SchemaValidator().detect_issues(pages)]
        assert "schema_missing_required_field" in types
        assert "schema_rich_result_retired" not in types
