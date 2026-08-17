"""Tests der GA4-Quelle — komplett ohne Netz.

Bibliothek und API-Antwort werden nachgebaut. Wichtigster Punkt: Die Quelle
darf einen Audit unter keinen Umständen abbrechen — fehlende Bibliothek,
fehlende Schlüsseldatei oder eine kaputte API führen zu "nicht verfügbar",
nie zu einer Ausnahme.
"""

import json
from types import SimpleNamespace

import pytest

from seo_autopilot.sources import ga4
from seo_autopilot.sources.ga4 import (
    GA4Analytics,
    GA4DataSource,
    baue_analytics,
    erstelle_quelle,
    zeilen_zu_dicts,
)

# ---------------------------------------------------------------------------
# Hilfen: eine GA4-Antwort nachbauen
# ---------------------------------------------------------------------------


def _antwort(dimensions, metrics, rows):
    """Baut eine Antwort wie die Data API sie liefert (Werte als Strings)."""
    return SimpleNamespace(
        dimension_headers=[SimpleNamespace(name=d) for d in dimensions],
        metric_headers=[SimpleNamespace(name=m) for m in metrics],
        rows=[
            SimpleNamespace(
                dimension_values=[SimpleNamespace(value=str(v)) for v in dims],
                metric_values=[SimpleNamespace(value=str(v)) for v in mets],
            )
            for dims, mets in rows
        ],
    )


SEITEN_ANTWORT = _antwort(
    ["pagePath"],
    ["activeUsers", "sessions", "screenPageViews", "bounceRate", "engagementRate"],
    [
        (["/"], [120, 150, 300, 0.42, 0.58]),
        (["/preise"], [40, 50, 60, 0.80, 0.20]),
    ],
)

KANAL_ANTWORT = _antwort(
    ["sessionDefaultChannelGroup"],
    ["activeUsers", "sessions"],
    [
        (["Organic Search"], [100, 120]),
        (["Direct"], [60, 80]),
    ],
)

LEERE_ANTWORT = _antwort(["pagePath"], ["sessions"], [])


class _FakeClient:
    """Ersetzt BetaAnalyticsDataClient: liefert Seiten- dann Kanal-Report."""

    def __init__(self, antworten):
        self._antworten = list(antworten)
        self.aufrufe = []

    def run_report(self, request):
        self.aufrufe.append(request)
        if not self._antworten:
            raise AssertionError("run_report öfter aufgerufen als erwartet")
        antwort = self._antworten.pop(0)
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


# Ob die echte Bibliothek installiert ist (beim Import festgehalten, bevor
# einzelne Tests das Flag umbiegen). Ohne sie lassen sich keine echten
# Report-Anfragen bauen — die reinen Auswertungstests laufen trotzdem.
BIBLIOTHEK_DA = ga4.HAS_GA4_API


def _quelle(tmp_path, monkeypatch, antworten=None, property_id="123456789"):
    """Baut eine GA4-Quelle mit echter Schlüsseldatei-Attrappe."""
    if not BIBLIOTHEK_DA:
        pytest.skip("google-analytics-data nicht installiert")
    creds = tmp_path / "service-account.json"
    creds.write_text(json.dumps({"type": "service_account"}), encoding="utf-8")

    monkeypatch.setattr(ga4, "HAS_GA4_API", True)
    quelle = GA4DataSource(credentials_path=str(creds), property_id=property_id)
    if antworten is not None:
        quelle.client = _FakeClient(antworten)
        quelle.authenticated = True
    return quelle


# ---------------------------------------------------------------------------
# 1. Fehlende Bibliothek
# ---------------------------------------------------------------------------


class TestBibliothekFehlt:
    def test_konstruktor_wirft_nicht_und_meldet_nicht_verfuegbar(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(ga4, "HAS_GA4_API", False)
        quelle = GA4DataSource(str(tmp_path / "egal.json"), "123")

        assert quelle.available is False
        assert "google-analytics-data" in quelle.unavailable_reason
        assert "nicht verfügbar" in quelle.status_text()

    @pytest.mark.asyncio
    async def test_authenticate_gibt_false_statt_ausnahme(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ga4, "HAS_GA4_API", False)
        quelle = GA4DataSource(str(tmp_path / "egal.json"), "123")

        assert await quelle.authenticate() is False

    @pytest.mark.asyncio
    async def test_fetch_gibt_none_statt_ausnahme(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ga4, "HAS_GA4_API", False)
        quelle = GA4DataSource(str(tmp_path / "egal.json"), "123")

        assert await quelle.fetch("2026-08-01", "2026-08-17") is None


# ---------------------------------------------------------------------------
# 2. Fehlende / kaputte Zugangsdaten
# ---------------------------------------------------------------------------


class TestZugangsdaten:
    def test_fehlende_schluesseldatei_meldet_sich_ab(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ga4, "HAS_GA4_API", True)
        quelle = GA4DataSource(str(tmp_path / "gibt-es-nicht.json"), "123456789")

        assert quelle.available is False
        assert "nicht gefunden" in quelle.unavailable_reason

    def test_fehlende_property_id_meldet_sich_ab(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ga4, "HAS_GA4_API", True)
        creds = tmp_path / "sa.json"
        creds.write_text("{}", encoding="utf-8")
        quelle = GA4DataSource(str(creds), "")

        assert quelle.available is False
        assert "property_id" in quelle.unavailable_reason

    @pytest.mark.asyncio
    async def test_kaputte_schluesseldatei_fuehrt_zu_none(self, tmp_path, monkeypatch):
        creds = tmp_path / "kaputt.json"
        creds.write_text("kein JSON", encoding="utf-8")
        monkeypatch.setattr(ga4, "HAS_GA4_API", True)

        def _explodiert(*args, **kwargs):
            raise ValueError("Schlüsseldatei nicht lesbar")

        monkeypatch.setattr(
            ga4,
            "Credentials",
            SimpleNamespace(from_service_account_file=_explodiert),
            raising=False,
        )

        quelle = GA4DataSource(str(creds), "123456789")
        assert await quelle.authenticate() is False
        assert await quelle.fetch() is None
        assert "Anmeldung fehlgeschlagen" in quelle.unavailable_reason


# ---------------------------------------------------------------------------
# 3. Übersetzung der API-Antwort
# ---------------------------------------------------------------------------


class TestUebersetzung:
    def test_zeilen_zu_dicts_liest_ueber_kopfzeilen(self):
        zeilen = zeilen_zu_dicts(SEITEN_ANTWORT)

        assert len(zeilen) == 2
        assert zeilen[0]["pagePath"] == "/"
        assert zeilen[0]["sessions"] == 150.0
        assert zeilen[1]["bounceRate"] == 0.80

    def test_zeilen_zu_dicts_vertraegt_none_und_leer(self):
        assert zeilen_zu_dicts(None) == []
        assert zeilen_zu_dicts(LEERE_ANTWORT) == []

    @pytest.mark.asyncio
    async def test_gemockte_antwort_wird_korrekt_uebersetzt(
        self, tmp_path, monkeypatch
    ):
        quelle = _quelle(tmp_path, monkeypatch, [SEITEN_ANTWORT, KANAL_ANTWORT])
        daten = await quelle.fetch("2026-08-01", "2026-08-17")

        assert isinstance(daten, GA4Analytics)
        assert daten.total_users == 160
        assert daten.total_sessions == 200
        assert daten.total_pageviews == 360
        # Absprungrate nach Sitzungen gewichtet: (42*150 + 80*50) / 200
        assert daten.bounce_rate == 51.5
        assert daten.engagement_rate == 48.5
        # Top-Seite ist die mit den meisten Aufrufen
        assert daten.top_pages[0]["page"] == "/"
        assert daten.top_pages[1]["bounce_rate"] == 80.0
        assert daten.start_date == "2026-08-01"

    @pytest.mark.asyncio
    async def test_kanaele_und_organischer_anteil(self, tmp_path, monkeypatch):
        quelle = _quelle(tmp_path, monkeypatch, [SEITEN_ANTWORT, KANAL_ANTWORT])
        daten = await quelle.fetch()

        assert daten.by_channel["Organic Search"]["sessions"] == 120
        assert daten.by_channel["Direct"]["sessions"] == 80
        assert daten.organic_sessions == 120
        assert daten.organic_share == 60.0  # 120 von 200 Sitzungen

    def test_deutsche_kanalbezeichnung_zaehlt_als_organisch(self):
        daten = baue_analytics(
            seiten_zeilen=[],
            kanal_zeilen=[
                {"sessionDefaultChannelGroup": "Organische Suche", "sessions": 30},
                {"sessionDefaultChannelGroup": "Direkt", "sessions": 10},
            ],
            start_date="2026-08-01",
            end_date="2026-08-17",
        )

        assert daten.organic_sessions == 30
        assert daten.organic_share == 75.0

    def test_kaputte_werte_kippen_die_auswertung_nicht(self):
        daten = baue_analytics(
            seiten_zeilen=[
                {"pagePath": "/", "sessions": "keine Zahl", "bounceRate": None}
            ],
            kanal_zeilen=[],
            start_date="a",
            end_date="b",
        )

        assert daten.total_sessions == 0
        assert daten.bounce_rate == 0.0
        assert daten.top_pages[0]["page"] == "/"


# ---------------------------------------------------------------------------
# 4. Leere Antwort und API-Fehler
# ---------------------------------------------------------------------------


class TestLeerUndFehler:
    @pytest.mark.asyncio
    async def test_leere_antwort_ergibt_leeres_ergebnis(self, tmp_path, monkeypatch):
        quelle = _quelle(tmp_path, monkeypatch, [LEERE_ANTWORT, LEERE_ANTWORT])
        daten = await quelle.fetch()

        assert isinstance(daten, GA4Analytics)
        assert daten.is_empty is True
        assert daten.top_pages == []
        assert daten.by_channel == {}
        assert daten.organic_share == 0.0

    @pytest.mark.asyncio
    async def test_api_fehler_ergibt_none_statt_ausnahme(self, tmp_path, monkeypatch):
        quelle = _quelle(tmp_path, monkeypatch, [RuntimeError("403 permission denied")])

        assert await quelle.fetch() is None

    @pytest.mark.asyncio
    async def test_test_connection_meldet_fehler_als_false(self, tmp_path, monkeypatch):
        quelle = _quelle(tmp_path, monkeypatch, [RuntimeError("kein Zugriff")])
        assert await quelle.test_connection() is False

    @pytest.mark.asyncio
    async def test_backlinks_und_keywords_sind_none(self, tmp_path, monkeypatch):
        quelle = _quelle(tmp_path, monkeypatch, [])

        assert await quelle.pull_backlinks("https://example.com") is None
        assert await quelle.pull_keywords("https://example.com") is None


# ---------------------------------------------------------------------------
# 5. Verdrahtung über projects.yaml (source_config)
# ---------------------------------------------------------------------------


class TestQuellenRegistrierung:
    def test_ohne_ga4_abschnitt_keine_quelle(self):
        assert erstelle_quelle({"gsc": {"property_url": "sc-domain:x"}}) is None
        assert erstelle_quelle({}) is None
        assert erstelle_quelle(None) is None

    def test_vollstaendige_konfiguration_ergibt_quelle(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ga4, "HAS_GA4_API", True)
        creds = tmp_path / "sa.json"
        creds.write_text("{}", encoding="utf-8")

        quelle = erstelle_quelle(
            {
                "ga4": {
                    "property_id": "987654321",
                    "credentials_path": str(creds),
                }
            }
        )

        assert quelle is not None
        assert quelle.property_id == "987654321"
        assert quelle.available is True

    def test_unvollstaendige_konfiguration_meldet_sich_ab(self, monkeypatch):
        monkeypatch.setattr(ga4, "HAS_GA4_API", True)
        quelle = erstelle_quelle({"ga4": {"property_id": "1"}})

        assert quelle is not None
        assert quelle.available is False

    @pytest.mark.asyncio
    async def test_pull_analytics_nutzt_zeitraum_in_tagen(self, tmp_path, monkeypatch):
        quelle = _quelle(tmp_path, monkeypatch, [SEITEN_ANTWORT, KANAL_ANTWORT])
        daten = await quelle.pull_analytics("https://example.com", days=7)

        assert daten is not None
        # Zwei Reports: Seiten + Kanäle
        assert len(quelle.client.aufrufe) == 2
        assert daten.total_sessions == 200


# ---------------------------------------------------------------------------
# 6. Verwendung im KeywordAgent
# ---------------------------------------------------------------------------


def _agent(enabled_sources, source_config):
    """KeywordAgent ohne Event-Bus, nur mit Projektkonfiguration."""
    from seo_autopilot.agents.keyword import KeywordAgent
    from seo_autopilot.core.project_manager import ProjectConfig

    agent = KeywordAgent.__new__(KeywordAgent)
    agent.project_config = ProjectConfig(
        id="demo",
        domain="https://example.com",
        name="Demo",
        enabled_sources=enabled_sources,
        source_config=source_config,
    )
    return agent


class TestKeywordAgentAnbindung:
    @pytest.mark.asyncio
    async def test_ohne_ga4_in_enabled_sources_kein_abruf(self):
        agent = _agent(["gsc"], {"ga4": {"property_id": "1"}})

        assert await agent._pull_ga4_analytics() is None

    @pytest.mark.asyncio
    async def test_ga4_aktiviert_aber_unkonfiguriert_bricht_nicht_ab(self):
        agent = _agent(["gsc", "ga4"], {})

        assert await agent._pull_ga4_analytics() is None

    def test_hohe_absprungrate_wird_als_befund_gemeldet(self):
        agent = _agent(["ga4"], {})
        daten = GA4Analytics(
            top_pages=[
                {"page": "/preise", "sessions": 50, "bounce_rate": 82.0},
                {"page": "/", "sessions": 90, "bounce_rate": 40.0},  # unauffällig
                {"page": "/neu", "sessions": 3, "bounce_rate": 100.0},  # zu wenig Daten
            ]
        )

        befunde = agent._find_high_bounce_pages(daten)

        assert len(befunde) == 1
        assert befunde[0]["type"] == "high_bounce_page"
        assert befunde[0]["affected_url"] == "/preise"

    def test_metriken_ohne_ga4_melden_nicht_verfuegbar(self):
        agent = _agent(["gsc"], {})

        assert agent._ga4_metrics(None) == {"ga4_available": False}
        assert agent._find_high_bounce_pages(None) == []
