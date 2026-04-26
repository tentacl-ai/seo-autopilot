"""Tests for Duplicate Content Detector."""

import pytest
from seo_autopilot.analyzers.duplicate_content import (
    DuplicateContentDetector,
    simhash,
    hamming_distance,
)


@pytest.fixture
def detector():
    return DuplicateContentDetector()


class TestSimHash:
    def test_identical_texts_zero_distance(self):
        text = "Dies ist ein Test Text mit genug Woertern fuer einen SimHash"
        h1 = simhash(text)
        h2 = simhash(text)
        assert hamming_distance(h1, h2) == 0

    def test_similar_texts_low_distance(self):
        text_a = "SEO Optimierung ist wichtig fuer die Sichtbarkeit einer Website in Suchmaschinen wie Google"
        text_b = "SEO Optimierung ist wichtig fuer die Sichtbarkeit einer Webseite in Suchmaschinen wie Google"
        h1 = simhash(text_a)
        h2 = simhash(text_b)
        assert hamming_distance(h1, h2) < 10

    def test_different_texts_high_distance(self):
        text_a = "Python ist eine Programmiersprache die fuer Data Science und Machine Learning verwendet wird"
        text_b = "Kochen mit frischen Zutaten aus dem Garten macht Spass und ist gesund fuer die ganze Familie"
        h1 = simhash(text_a)
        h2 = simhash(text_b)
        assert hamming_distance(h1, h2) > 10


class TestDuplicateDetection:
    def test_detects_true_duplicate_without_canonical(self, detector):
        text = "Dies ist ein ausfuehrlicher Artikel ueber Suchmaschinenoptimierung und alle wichtigen Aspekte die man beachten muss um in den Google Suchergebnissen gut zu ranken und Traffic zu generieren fuer die eigene Website"
        pages = [
            {
                "url": "https://example.com/page-1",
                "text_content": text,
                "word_count": 500,
                "h1": ["SEO Guide"],
                "title": "SEO",
            },
            {
                "url": "https://example.com/page-2",
                "text_content": text,
                "word_count": 500,
                "h1": ["SEO Guide Copy"],
                "title": "SEO",
            },
        ]
        issues = detector.detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "near_duplicate_content" in types

    def test_ignores_legitimate_duplicate_with_canonical(self):
        canonical_pairs = {("https://example.com/page-1", "https://example.com/page-2")}
        detector = DuplicateContentDetector(canonical_pairs=canonical_pairs)
        text = "Dies ist ein ausfuehrlicher Artikel ueber Suchmaschinenoptimierung und alle wichtigen Aspekte die man beachten muss um in den Google Suchergebnissen gut zu ranken und Traffic zu generieren fuer die eigene Website"
        pages = [
            {
                "url": "https://example.com/page-1",
                "text_content": text,
                "word_count": 500,
                "h1": ["SEO"],
                "title": "SEO",
            },
            {
                "url": "https://example.com/page-2",
                "text_content": text,
                "word_count": 500,
                "h1": ["SEO"],
                "title": "SEO",
            },
        ]
        issues = detector.detect_issues(pages)
        dup_issues = [i for i in issues if i["type"] == "near_duplicate_content"]
        assert len(dup_issues) == 0

    def test_distinguishes_from_cluster_cannibalization(self):
        cluster_urls = {
            "blog": {"https://example.com/blog/a", "https://example.com/blog/b"}
        }
        detector = DuplicateContentDetector(cluster_urls=cluster_urls)
        text = "Dies ist ein ausfuehrlicher Artikel ueber Suchmaschinenoptimierung und alle wichtigen Aspekte die man beachten muss um in den Google Suchergebnissen gut zu ranken und Traffic zu generieren fuer die eigene Website"
        pages = [
            {
                "url": "https://example.com/blog/a",
                "text_content": text,
                "word_count": 500,
                "h1": ["SEO"],
                "title": "SEO",
            },
            {
                "url": "https://example.com/blog/b",
                "text_content": text,
                "word_count": 500,
                "h1": ["SEO"],
                "title": "SEO",
            },
        ]
        issues = detector.detect_issues(pages)
        dup_issues = [i for i in issues if i["type"] == "near_duplicate_content"]
        assert len(dup_issues) == 0  # In same cluster -> TopicalAuthority handles it

    def test_detects_thin_content(self, detector):
        pages = [
            {
                "url": "https://example.com/thin",
                "word_count": 80,
                "h1": ["X"],
                "title": "X",
            },
        ]
        issues = detector.detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "thin_content" in types

    def test_no_thin_content_on_legal_pages(self, detector):
        pages = [
            {
                "url": "https://example.com/impressum",
                "word_count": 80,
                "h1": ["Impressum"],
                "title": "Impressum",
            },
            {
                "url": "https://example.com/datenschutz",
                "word_count": 80,
                "h1": ["Datenschutz"],
                "title": "Datenschutz",
            },
        ]
        issues = detector.detect_issues(pages)
        thin = [i for i in issues if i["type"] == "thin_content"]
        assert len(thin) == 0

    def test_recommends_canonical_for_duplicate_pair(self, detector):
        text = "Ein langer Text ueber SEO der auf zwei Seiten vorkommt und Duplicate Content verursacht weil beide Seiten den identischen Inhalt haben und Google nicht weiss welche Seite ranken soll"
        pages = [
            {
                "url": "https://example.com/original",
                "text_content": text,
                "word_count": 500,
                "h1": ["SEO"],
                "title": "SEO",
            },
            {
                "url": "https://example.com/copy",
                "text_content": text,
                "word_count": 500,
                "h1": ["SEO Copy"],
                "title": "SEO",
            },
        ]
        issues = detector.detect_issues(pages)
        dup = [i for i in issues if i["type"] == "near_duplicate_content"]
        assert len(dup) > 0
        assert "canonical" in dup[0]["fix_suggestion"].lower()


class TestKeywordCannibalization:
    def test_detects_same_h1_on_multiple_pages(self, detector):
        pages = [
            {
                "url": "https://example.com/page-1",
                "word_count": 500,
                "h1": ["SEO Optimierung"],
                "title": "SEO 1",
            },
            {
                "url": "https://example.com/page-2",
                "word_count": 500,
                "h1": ["SEO Optimierung"],
                "title": "SEO 2",
            },
        ]
        issues = detector.detect_issues(pages)
        types = [i["type"] for i in issues]
        assert "keyword_cannibalization" in types
