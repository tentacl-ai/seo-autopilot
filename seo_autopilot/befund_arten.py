"""
Befund-Arten: technischer Fehler oder Empfehlung?

Anlass (Rundumschlag 18.09.2026): Faustregeln wie "Antwort zuerst"
(geo_answer_first) oder "Titel zu kurz" wogen in der Note genauso viel wie ein
kaputter Link oder eine fehlende Meta-Description. Eine Website, die technisch
sauber ist, aber nicht jedem Schreibratgeber folgt, bekam dadurch eine Note,
die nach Baustelle aussah.

Deshalb bekommt jeder Befundtyp genau eine Art:

* ``fehler``     — technisch eindeutig, am Live-System nachpruefbar
                   (404, fehlender Titel, noindex, Messwert schlecht ...).
* ``empfehlung`` — Faustregel, Geschmack oder Chance (Textlaenge, GEO-Stil,
                   fehlendes Social-Tag, Suchbegriff mit Potenzial ...).

Die Tabelle ist die EINZIGE Stelle, an der das entschieden wird. Unbekannte
Typen gelten als ``fehler`` — im Zweifel darf ein echter Fehler nicht in der
Empfehlungs-Ecke verschwinden.

Zusaetzlich stehen hier die Ursachen-Familien: Befunde, die an derselben
Adresse dieselbe Ursache beschreiben (z. B. "nicht erreichbar" UND "verwaist"),
zaehlen fuer die Note nur einmal. Sichtbar bleiben sie trotzdem alle.
"""

from __future__ import annotations

from typing import Dict, Optional

FEHLER = "fehler"
EMPFEHLUNG = "empfehlung"
# Laut Google ohne Wirkung auf die (KI-)Suche — "Optimizing your website for
# generative AI features on Google Search", Search Central, Stand 10.07.2026:
# keine llms.txt/KI-Dateien noetig, kein "Chunking", kein Schreiben speziell fuer
# KI, Structured Data kein KI-Hebel. Solche Befunde zaehlen NICHT in die Note,
# stehen nicht auf der Arbeitsliste und werden nicht automatisch "repariert".
# Sichtbar bleiben sie als Hinweis. Quelle:
# https://developers.google.com/search/docs/fundamentals/ai-optimization-guide
HINWEIS = "hinweis"

_HINWEIS_TYPEN = {
    # llms.txt / KI-Dateien
    "missing_llms_txt",
    "missing_llms_full_txt",
    "llms_no_links",
    "missing_ai_txt",
    # Nur KI-Training gesperrt: bewusste Entscheidung, KI-Suche laeuft ueber eigene Crawler
    "ai_training_blocked",
    # "Chunking" / Schreiben fuer KI (GEO-Faustregeln)
    "geo_paragraph_length",
    "geo_structured_format",
    "geo_answer_first",
    "geo_fact_density",
    "geo_entity_clarity",
    "geo_freshness_signals",
    "poor_geo_readiness",
    # Structured Data ohne Rich-Result-Nutzen
    "no_jsonld",
    "schema_rich_result_opportunity",
    "schema_rich_result_retired",
    "articles_missing_date_modified",
}

_FEHLER_TYPEN = {
    # Abruf / Erreichbarkeit
    "fetch_error",
    "broken_internal_link",
    "soft_404",
    "soft_404_catchall",
    "unreachable_page",
    "orphan_page",
    # Meta / Indexierung
    "missing_title",
    "missing_meta_description",
    "missing_h1",
    "missing_viewport",
    "missing_html_lang",
    "noindex_detected",
    "utility_page_indexable",
    "duplicate_title",
    # Canonical / Weiterleitungen / hreflang
    "canonical_chain",
    "canonical_conflicts_hreflang",
    "canonical_conflicts_sitemap",
    "canonical_points_to_noindex",
    "canonical_points_to_redirect",
    "redirect_chain",
    "redirect_loop",
    "redirect_302_should_be_301",
    "redirect_to_different_domain",
    "hreflang_broken_target",
    "hreflang_missing_return_link",
    "hreflang_language_mismatch",
    # robots.txt / Sitemap
    "wildcard_disallow",
    "css_js_blocked",
    "ai_crawler_blocked",
    "geo_ai_crawler_blocked",
    "missing_sitemap",
    "empty_sitemap",
    "empty_sitemap_index",
    "sitemap_parse_error",
    "sitemap_broken_url",
    "sitemap_many_broken_urls",
    "sitemap_non_canonical_urls",
    "sitemap_too_large",
    "sitemap_file_too_large",
    "sitemap_foreign_host",
    # Schema
    "schema_syntax_error",
    "schema_missing_required_field",
    # Rechtliches
    "missing_impressum",
    "missing_datenschutz",
    # Inhalt (echte Doppelung, nicht nur gleiche Ueberschrift)
    "near_duplicate_content",
    # Leistung (gemessen)
    "poor_lcp",
    "poor_cls",
    "poor_inp",
    "poor_tbt",
    "poor_ttfb",
    "poor_lighthouse_performance",
    "low_lighthouse_seo",
    "low_accessibility",
    "slow_response",
    "image_oversized",
    "image_page_weight",
    "image_lcp_lazy_loaded",
    # Barrierefreiheit / Sicherheit
    "images_without_alt",
    "image_missing_alt",
    "no_https",
    "mixed_content",
    "og_image_unreachable",
}

_EMPFEHLUNG_TYPEN = {
    # VideoObject: empfohlene Angaben (Google-Doku 24.09.2026)
    "schema_video_empfohlen",
    # Meta-Laengen und Social-Vorschau
    "short_title",
    "long_title",
    "short_meta_description",
    "long_meta_description",
    "multiple_h1",
    "heading_level_skipped",
    "duplicate_meta_description",
    "missing_canonical",
    "canonical_missing",
    "missing_og_title",
    "missing_og_image",
    "og_image_missing",
    "og_image_too_small",
    "missing_twitter_card",
    "missing_security_headers",
    # Verlinkung / Struktur
    "deep_page",
    "link_equity_sink",
    "internal_link_to_redirect",
    "hreflang_missing_x_default",
    # robots / Sitemap / KI-Dateien
    "missing_robots_txt",
    "missing_sitemap_directive",
    "sitemap_missing_pages",
    "sitemap_no_lastmod",
    "sitemap_stale_lastmod",
    "missing_llms_txt",
    "missing_llms_full_txt",
    "missing_ai_txt",
    "missing_indexnow",
    "llms_no_links",
    "invalid_llms_syntax",
    # Schema
    "no_jsonld",
    "missing_org_schema",
    "missing_organization_schema",
    "org_schema_no_sameas",
    "missing_local_business_schema",
    "schema_rich_result_opportunity",
    # Vertrauenssignale
    "missing_contact_page",
    "missing_about_page",
    "eeat_score_low",
    "eeat_score_critical",
    "articles_missing_author",
    "articles_missing_date_published",
    "articles_missing_date_modified",
    # Inhalt / Themen
    "thin_content",
    "keyword_cannibalization",
    "cluster_cannibalization",
    "weak_cluster_linking",
    "orphan_cluster_page",
    "missing_pillar_page",
    "cluster_coverage_gap",
    "no_topic_clusters_detected",
    # GEO-Faustregeln
    "geo_answer_first",
    "geo_fact_density",
    "geo_freshness_signals",
    "geo_entity_clarity",
    "geo_structured_format",
    "geo_paragraph_length",
    "poor_geo_readiness",
    # Bilder (Optimierung statt Fehler)
    "image_lcp_no_priority",
    "image_no_lazy_loading",
    "image_legacy_format",
    "image_missing_srcset",
    "image_missing_dimensions",
    "image_generic_filename",
    "image_figure_without_caption",
    "moderate_lighthouse_performance",
    # Chancen aus Such-/Nutzungsdaten
    "low_ctr_opportunity",
    "striking_distance",
    "high_bounce_page",
    "content_gaps_detected",
    "poor_intent_match",
    "moderate_intent_match",
    "rising_query",
    "top_query",
}

BEFUND_ARTEN: Dict[str, str] = {
    **{t: FEHLER for t in _FEHLER_TYPEN},
    **{t: EMPFEHLUNG for t in _EMPFEHLUNG_TYPEN},
    **{t: HINWEIS for t in _HINWEIS_TYPEN},  # zuletzt: gewinnt bei Doppelnennung
}


def art_von(typ: Optional[str]) -> str:
    """Art eines Befundtyps. Unbekannt oder leer -> ``fehler`` (im Zweifel ernst nehmen).

    ``http_404``, ``http_500`` usw. entstehen dynamisch und sind immer Fehler.
    """
    if not typ:
        return FEHLER
    return BEFUND_ARTEN.get(typ, FEHLER)


# Ursachen-Familien: Befunde derselben Familie an derselben Adresse beschreiben
# dieselbe Ursache. Fuer die Note zaehlt je Adresse nur der schwerste davon.
URSACHEN_FAMILIEN: Dict[str, str] = {
    # "Seite ist schlecht verlinkt" — eine fehlende Navigation erzeugt alle vier
    "unreachable_page": "verlinkung",
    "orphan_page": "verlinkung",
    "deep_page": "verlinkung",
    "orphan_cluster_page": "verlinkung",
    # "Zwei Seiten sind sich zu aehnlich" (coaching-beispiel /hilfe vs /en/help-center)
    "near_duplicate_content": "doppelung",
    "keyword_cannibalization": "doppelung",
    "cluster_cannibalization": "doppelung",
    # Dieselbe Sache aus zwei Analyzern
    "ai_crawler_blocked": "ki_crawler",
    "geo_ai_crawler_blocked": "ki_crawler",
    "missing_org_schema": "org_schema",
    "missing_organization_schema": "org_schema",
    "missing_canonical": "canonical_fehlt",
    "canonical_missing": "canonical_fehlt",
    "images_without_alt": "alt_text",
    "image_missing_alt": "alt_text",
}

# Familien, die sich auf die ganze Website beziehen: die Adresse spielt keine Rolle.
DOMAINWEITE_FAMILIEN = {"ki_crawler", "org_schema"}


def familie_von(typ: Optional[str]) -> Optional[str]:
    return URSACHEN_FAMILIEN.get(typ or "")
