class TestAbgeschalteteRichResults:
    """Google zeigt fuer FAQPage/HowTo & Co. nichts mehr an (claude-seo Liste, Stand 23.09.2026)."""

    def test_faqpage_ohne_mainentity_ist_nur_hinweis(self):
        from seo_autopilot.analyzers.schema_validation import SchemaValidator

        pages = [{"url": "https://x.de/faq", "schema_data": [{"@type": "FAQPage"}]}]
        issues = SchemaValidator().detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "schema_missing_required_field" not in types
        assert "schema_rich_result_retired" in types
        assert all(
            i["severity"] == "info"
            for i in issues
            if i["type"] == "schema_rich_result_retired"
        )

    def test_kaputtes_howto_ist_kein_fehler(self):
        from seo_autopilot.analyzers.schema_validation import SchemaValidator

        pages = [
            {"url": "https://x.de/", "schema_data": [{"@type": "HowTo", "name": "x"}]}
        ]
        types = [i["type"] for i in SchemaValidator().detect_issues(pages)]
        assert "schema_missing_required_field" not in types
        assert "schema_syntax_error" not in types

    def test_hinweis_zaehlt_nicht_in_die_note(self):
        from seo_autopilot.befund_arten import HINWEIS, art_von

        assert art_von("schema_rich_result_retired") == HINWEIS
        assert art_von("ai_training_blocked") == HINWEIS

    def test_lebende_typen_weiter_geprueft(self):
        from seo_autopilot.analyzers.schema_validation import SchemaValidator

        pages = [
            {
                "url": "https://x.de/p",
                "schema_data": [{"@type": "Product", "name": "x"}],
            }
        ]
        types = [i["type"] for i in SchemaValidator().detect_issues(pages)]
        assert "schema_missing_required_field" in types
        assert "schema_rich_result_retired" not in types


class TestVideoObject:
    """Google-Doku 24.09.2026: creator/author empfohlen, vier erlaubte interactionType-Werte."""

    BASIS = {
        "@type": "VideoObject",
        "name": "v",
        "uploadDate": "2026-09-01",
        "thumbnailUrl": "https://x.de/t.jpg",
    }

    def _typen(self, **felder):
        from seo_autopilot.analyzers.schema_validation import SchemaValidator

        pages = [{"url": "https://x.de/v", "schema_data": [{**self.BASIS, **felder}]}]
        return {i["type"]: i for i in SchemaValidator().detect_issues(pages)}

    def test_ohne_urheber_empfehlung(self):
        befund = self._typen()["schema_video_empfohlen"]
        assert "creator/author fehlt" in befund["title"] and befund["severity"] == "low"

    def test_urheber_ohne_namen(self):
        assert (
            "ohne name"
            in self._typen(creator={"@type": "Person"})["schema_video_empfohlen"][
                "title"
            ]
        )

    def test_author_mit_alternatename_reicht(self):
        assert "schema_video_empfohlen" not in self._typen(
            author={"@type": "Organization", "alternateName": "x"}
        )

    def test_erlaubte_interaktionen_auch_als_url(self):
        stat = [
            {
                "@type": "InteractionCounter",
                "interactionType": "https://schema.org/WatchAction",
                "userInteractionCount": 5,
            },
            {
                "@type": "InteractionCounter",
                "interactionType": {"@type": "LikeAction"},
                "userInteractionCount": 1,
            },
        ]
        assert "schema_video_empfohlen" not in self._typen(
            creator={"name": "A"}, interactionStatistic=stat
        )

    def test_nicht_unterstuetzte_interaktion(self):
        stat = {
            "@type": "InteractionCounter",
            "interactionType": "https://schema.org/BookmarkAction",
        }
        titel = self._typen(creator={"name": "A"}, interactionStatistic=stat)[
            "schema_video_empfohlen"
        ]["title"]
        assert "BookmarkAction" in titel

    def test_ist_empfehlung_mit_klartext(self):
        from seo_autopilot.befund_arten import EMPFEHLUNG, art_von
        from seo_autopilot.weekly_report import BEFUND_TEXTE

        assert art_von("schema_video_empfohlen") == EMPFEHLUNG
        assert "schema_video_empfohlen" in BEFUND_TEXTE
