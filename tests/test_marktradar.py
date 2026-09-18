"""Marktbeobachter: nur Meldungen mit echter Quelle, keine Doppelten, nie ein Absturz."""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from seo_autopilot import marktradar as mr
from seo_autopilot.policy_radar import erkenne_themen
from seo_autopilot.sources.intelligence import FeedItem


def _antwort(status=200, url=None):
    return lambda u: SimpleNamespace(status_code=status, url=url or u)


class TestQuellePruefen:
    def test_erreichbare_artikeladresse_bleibt(self):
        assert (
            mr.quelle_pruefen("https://blog.google/ads/neu/", _antwort())
            == "https://blog.google/ads/neu/"
        )

    def test_404_wird_verworfen(self):
        assert mr.quelle_pruefen("https://example.com/erfunden", _antwort(404)) is None

    def test_startseite_ist_keine_quelle(self):
        assert mr.quelle_pruefen("https://searchengineland.com/", _antwort()) is None

    def test_weiterleitungsdienst_ist_keine_quelle(self):
        assert (
            mr.quelle_pruefen(
                "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                _antwort(),
            )
            is None
        )

    def test_netzfehler_heisst_nicht_belegt(self):
        def kaputt(u):
            raise OSError("keine Verbindung")

        assert mr.quelle_pruefen("https://example.com/artikel", kaputt) is None

    def test_tracking_parameter_fliegen_raus(self):
        assert (
            mr.quelle_pruefen("https://x.de/a?utm_source=feed", _antwort())
            == "https://x.de/a"
        )

    def test_endadresse_nach_weiterleitung(self):
        assert (
            mr.quelle_pruefen("https://x.de/kurz", _antwort(url="https://x.de/lang"))
            == "https://x.de/lang"
        )


class TestKiSpaeher:
    def test_json_aus_text_mit_zaun(self):
        text = 'Hier:\n```json\n[{"titel": "A", "url": "https://x.de/a"}]\n```'
        assert mr.json_liste_aus_text(text) == [{"titel": "A", "url": "https://x.de/a"}]

    def test_kaputtes_json_ergibt_leere_liste(self):
        assert mr.json_liste_aus_text("[{kein json") == []
        assert mr.json_liste_aus_text("") == []

    def test_erfundene_quelle_wird_verworfen(self):
        text = '[{"titel": "LCP muss unter 2,0 s", "url": "https://erfunden.de/x"}, {"titel": "Google Ads AI Max", "url": "https://blog.google/ai-max"}]'
        ergebnis = mr.Sammelergebnis()
        meldungen = mr.aus_ki(
            {"ChatGPT": lambda auftrag, format: {"text": text}},
            pruefer=lambda u: u if "blog.google" in u else None,
            ergebnis=ergebnis,
        )
        assert [m["titel"] for m in meldungen] == ["Google Ads AI Max"]
        assert ergebnis.ki_vorschlaege == 2 and ergebnis.ki_verworfen == 1

    def test_zwei_kis_mit_derselben_quelle_ergeben_eine_meldung(self):
        text = '[{"titel": "Core Update", "url": "https://developers.google.com/search/update"}]'
        spaeher = {
            "ChatGPT": lambda a, f: {"text": text},
            "Gemini": lambda a, f: {"text": text},
        }
        meldungen = mr.aus_ki(spaeher, pruefer=lambda u: u)
        assert len(meldungen) == 1
        assert meldungen[0]["via"] == "ChatGPT+Gemini"

    def test_ausfall_einer_ki_stoppt_nicht(self):
        def kaputt(a, f):
            raise TimeoutError()

        text = '[{"titel": "GA4 Neuerung", "url": "https://blog.google/ga4"}]'
        ergebnis = mr.Sammelergebnis()
        meldungen = mr.aus_ki(
            {"Gemini": kaputt, "ChatGPT": lambda a, f: {"text": text}},
            pruefer=lambda u: u,
            ergebnis=ergebnis,
        )
        assert len(meldungen) == 1
        assert ergebnis.fehler == ["Gemini: TimeoutError"]


class TestFeedsUndAblage:
    def test_nur_themenrelevante_juengere_feed_meldungen(self):
        jetzt = datetime.now(timezone.utc)
        eintraege = [
            FeedItem(
                title="Google Ads launches AI Max for Search",
                url="https://a.de/1",
                source="ppc_land",
                published=jetzt,
            ),
            FeedItem(
                title="Office party photos",
                url="https://a.de/2",
                source="ppc_land",
                published=jetzt,
            ),
            FeedItem(
                title="Core update rolled out",
                url="https://a.de/3",
                source="x",
                published=jetzt - timedelta(days=40),
            ),
        ]
        assert [m["url"] for m in mr.aus_feeds(eintraege)] == ["https://a.de/1"]

    def test_sea_und_messung_sind_themen(self):
        assert [
            t.schluessel
            for t in erkenne_themen("Performance Max changes in Google Ads")[0]
        ] == ["sea_werbung"]
        assert "messung" in [
            t.schluessel for t in erkenne_themen("Consent Mode update for GA4")[0]
        ]

    def test_speichern_zaehlt_dieselbe_url_nur_einmal(self, tmp_path):
        con = sqlite3.connect(tmp_path / "m.db")
        m = {"url": "https://x.de/a", "titel": "A", "relevanz": "hoch"}
        assert mr.speichern(con, [m]) == 1
        assert mr.speichern(con, [m, {"url": "", "titel": "ohne Quelle"}]) == 0

    def test_neueste_filtert_relevanz_und_sortiert(self, tmp_path):
        db = str(tmp_path / "m.db")
        con = sqlite3.connect(db)
        mr.speichern(
            con,
            [
                {"url": "https://x.de/n", "titel": "niedrig", "relevanz": "niedrig"},
                {"url": "https://x.de/m", "titel": "mittel", "relevanz": "mittel"},
                {"url": "https://x.de/h", "titel": "hoch", "relevanz": "hoch"},
            ],
        )
        con.close()
        assert [m["titel"] for m in mr.neueste(db)] == ["hoch", "mittel"]
