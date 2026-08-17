"""Tests für das Richtlinien-Radar.

Der Wert des Radars steht und fällt mit zwei Eigenschaften:

1. Es ordnet die richtigen Prüfbereiche zu (sonst ist der Hinweis nutzlos).
2. Es stirbt nie an einem kaputten Feed-Eintrag (sonst reißt es den ganzen
   Bericht mit, und wir tauschen eine verpasste Meldung gegen einen Ausfall).

Deshalb prüft jeder Zuordnungstest zuerst, dass ein Thema wirklich ERKANNT
wird — und die Robustheitstests werfen bewusst Müll hinein.
"""

from datetime import datetime, timedelta, timezone

import pytest

from seo_autopilot.policy_radar import (
    RELEVANZ_HOCH,
    RELEVANZ_MITTEL,
    RELEVANZ_NIEDRIG,
    RadarTreffer,
    analysiere_meldung,
    analysiere_meldungen,
    betroffene_pruefbereiche,
    erkenne_themen,
    radar_zusammenfassung,
)

JETZT = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def meldung(titel, quelle="search_engine_land", summary="", url="", published=JETZT):
    """Baut eine Feed-Meldung als dict (wie sie aus dem Feed kommt)."""
    return {
        "title": titel,
        "source": quelle,
        "summary": summary,
        "url": url,
        "published": published,
    }


class FeedItemAttrappe:
    """Nachbau eines FeedItem-Objekts — das Radar muss beides verdauen."""

    def __init__(self, title, source, summary="", url="", published=None):
        self.title = title
        self.source = source
        self.summary = summary
        self.url = url
        self.published = published


# ---------------------------------------------------------------------------
# Themen-Zuordnung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "titel,erwarteter_bereich",
    [
        ("Google explains the new INP threshold", "pagespeed"),
        ("Core Web Vitals report gets a new metric", "core_web_vitals"),
        ("How AI Overviews change click-through rates", "geo_audit"),
        ("AI Mode rollout: what GEO means for publishers", "llms_ai_txt"),
        ("GPTBot and ClaudeBot: should you block AI crawlers?", "robots_sitemap"),
        ("New structured data requirements for rich results", "schema_validation"),
        ("Helpful content update targets scaled content abuse", "eeat"),
        ("Canonical tags and duplicate content explained", "canonical_engine"),
        ("Duplicate content myths", "duplicate_content"),
        ("Sitemap indexing errors in Search Console", "robots_sitemap"),
    ],
)
def test_themen_werden_dem_richtigen_pruefbereich_zugeordnet(titel, erwarteter_bereich):
    treffer = analysiere_meldung(meldung(titel))
    assert treffer is not None, f"kein Treffer für {titel!r}"
    assert erwarteter_bereich in treffer.pruefbereiche


def test_ki_crawler_meldung_nennt_thema_und_begriff():
    treffer = analysiere_meldung(
        meldung("OpenAI updates GPTBot documentation", summary="robots.txt rules")
    )
    assert treffer is not None
    assert "ki_crawler" in treffer.themen
    assert "gptbot" in treffer.begriffe


def test_wortgrenzen_verhindern_fehlzuordnung():
    """'INP' darf nicht in 'input' anschlagen, 'GEO' nicht in 'geography'."""
    assert erkenne_themen("New input fields in the geography module")[0] == []


# ---------------------------------------------------------------------------
# Relevanz
# ---------------------------------------------------------------------------


def test_google_eigene_meldung_bekommt_hohe_relevanz():
    treffer = analysiere_meldung(
        meldung(
            "Sitemap best practices",
            quelle="google_search_central",
            url="https://developers.google.com/search/blog/2026/08/sitemaps",
        )
    )
    assert treffer is not None
    assert treffer.von_google is True
    assert treffer.relevanz == RELEVANZ_HOCH


def test_google_news_zaehlt_nicht_als_google_quelle():
    """news.google.com aggregiert nur Fremdartikel — keine Richtlinienquelle."""
    treffer = analysiere_meldung(
        meldung(
            "Blogger discusses sitemaps",
            quelle="google_news_algo",
            url="https://news.google.com/rss/articles/abc",
        )
    )
    assert treffer is not None
    assert treffer.von_google is False
    assert treffer.relevanz == RELEVANZ_NIEDRIG


def test_mehrere_themen_ergeben_hohe_relevanz():
    treffer = analysiere_meldung(
        meldung(
            "Core Web Vitals and structured data both affect AI Overviews",
            quelle="moz_blog",
        )
    )
    assert treffer is not None
    assert len(treffer.themen) >= 2
    assert treffer.relevanz == RELEVANZ_HOCH


def test_einzelnes_fremdthema_bleibt_mittel_oder_niedrig():
    treffer = analysiere_meldung(
        meldung("Why canonical tags still matter", quelle="ahrefs_blog")
    )
    assert treffer is not None
    assert treffer.themen == ["doppelte_inhalte"]
    assert treffer.relevanz == RELEVANZ_MITTEL


# ---------------------------------------------------------------------------
# Nicht-Treffer
# ---------------------------------------------------------------------------


def test_unbekannte_meldung_erzeugt_keinen_treffer():
    assert (
        analysiere_meldung(meldung("Neue Kaffeemaschine im Buero vorgestellt")) is None
    )


def test_liste_ohne_relevante_meldungen_bleibt_leer():
    eintraege = [
        meldung("Firmenlauf 2026: Anmeldung gestartet"),
        meldung("Interview mit einem Grafiker"),
    ]
    assert analysiere_meldungen(eintraege) == []


# ---------------------------------------------------------------------------
# Robustheit — darf NIE eine Ausnahme werfen
# ---------------------------------------------------------------------------


def test_kaputte_und_leere_eintraege_werfen_keine_ausnahme():
    muell = [
        None,
        {},
        {"title": None, "source": None, "summary": None},
        {"title": "", "summary": ""},
        [],
        42,
        "nur ein String ohne Thema",
        {"title": "Core Web Vitals update", "published": "voellig kaputt"},
        FeedItemAttrappe(title=None, source=None),
    ]
    treffer = analysiere_meldungen(muell)
    # Genau eine verwertbare Meldung steckt drin (Core Web Vitals).
    assert len(treffer) == 1
    assert treffer[0].datum is None  # kaputtes Datum -> None, kein Absturz


def test_leere_oder_unbrauchbare_eingabe_liefert_leere_liste():
    assert analysiere_meldungen(None) == []
    assert analysiere_meldungen([]) == []
    assert analysiere_meldungen(123) == []


def test_feed_item_objekt_wird_genauso_ausgewertet_wie_dict():
    treffer = analysiere_meldung(
        FeedItemAttrappe(
            title="Google updates its spam policies",
            source="google_search_central",
            url="https://developers.google.com/search/blog/spam",
            published="2026-08-16T09:00:00Z",
        )
    )
    assert treffer is not None
    assert "eeat" in treffer.pruefbereiche
    assert treffer.datum.year == 2026


# ---------------------------------------------------------------------------
# Filter + Sortierung
# ---------------------------------------------------------------------------


def test_alte_meldungen_werden_herausgefiltert():
    alt = meldung("Core Web Vitals rollout", published=JETZT - timedelta(days=40))
    neu = meldung("AI Overviews expand", published=JETZT - timedelta(days=2))
    treffer = analysiere_meldungen([alt, neu], max_alter_tage=14, jetzt=JETZT)
    assert [t.titel for t in treffer] == ["AI Overviews expand"]


def test_meldung_ohne_datum_bleibt_im_zweifel_drin():
    ohne = meldung("Structured data changes", published=None)
    treffer = analysiere_meldungen([ohne], max_alter_tage=7, jetzt=JETZT)
    assert len(treffer) == 1


def test_treffer_sind_nach_relevanz_sortiert():
    niedrig = meldung("Sitemap tips for beginners", quelle="moz_blog")
    hoch = meldung(
        "Core update rolling out now",
        quelle="google_search_central",
        url="https://developers.google.com/search/blog/core-update",
    )
    treffer = analysiere_meldungen([niedrig, hoch])
    assert [t.relevanz for t in treffer] == [RELEVANZ_HOCH, RELEVANZ_NIEDRIG]


# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------


def test_zusammenfassung_bei_null_treffern_ist_sinnvoll():
    for leer in (None, []):
        text = radar_zusammenfassung(leer)
        assert "Richtlinien-Radar" in text
        assert "keine relevanten" in text
        assert "Kein Handlungsbedarf" in text


def test_zusammenfassung_nennt_pruefbereiche_und_relevanz():
    treffer = analysiere_meldungen(
        [
            meldung(
                "AI Overviews and GPTBot: new crawler guidance",
                quelle="google_search_central",
                url="https://developers.google.com/search/blog/ai",
            )
        ]
    )
    text = radar_zusammenfassung(treffer)
    assert "1 relevante Meldung(en)" in text
    assert "geo_audit" in text
    assert "robots_sitemap" in text
    assert "hoch" in text


def test_zusammenfassung_begrenzt_die_anzahl():
    eintraege = [meldung(f"Core Web Vitals Teil {i}") for i in range(15)]
    treffer = analysiere_meldungen(eintraege)
    assert len(treffer) == 15
    text = radar_zusammenfassung(treffer, max_eintraege=3)
    assert "12 weitere Meldung(en)" in text


def test_zusammenfassung_vertraegt_kaputte_treffer_objekte():
    kaputt = RadarTreffer(titel="X", quelle="y", themen=["gibt_es_nicht"])
    text = radar_zusammenfassung([kaputt])
    assert "Richtlinien-Radar" in text


def test_betroffene_pruefbereiche_zaehlt_haeufigkeit():
    treffer = analysiere_meldungen(
        [
            meldung("Sitemap indexing changes"),
            meldung("GPTBot crawler rules"),
            meldung("Rich results update"),
        ]
    )
    zaehlung = dict(betroffene_pruefbereiche(treffer))
    assert zaehlung["robots_sitemap"] == 2
    assert zaehlung["schema_validation"] == 1
    assert betroffene_pruefbereiche([]) == []
