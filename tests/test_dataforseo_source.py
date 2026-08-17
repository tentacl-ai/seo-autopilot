"""Tests für die DataForSEO-Datenquelle.

Kein einziger Test darf ins Netz. Alles läuft über ``httpx.MockTransport``
(genau wie in ``test_verification.py``) — sonst würde die Testsuite bei jedem
Durchlauf echtes Geld kosten.

Drei Dinge sind hier wichtiger als das Parsen:
  1. **Ohne Zugangsdaten** meldet sich die Quelle als "nicht konfiguriert"
     und wirft nicht — ein Audit darf daran niemals scheitern.
  2. **Die Kostenbremse** greift nachweislich und bricht ab.
  3. **Zugangsdaten** tauchen in keiner Log-Zeile und in keiner Fehlermeldung
     auf — auch dann nicht, wenn die Gegenstelle sie zurückspiegelt.
"""

import base64
import json
import logging

import httpx
import pytest

from seo_autopilot.sources.dataforseo import (
    DataForSEODataSource,
    KostenbremseError,
    quelle_aus_projekt,
)

LOGIN = "kunde@example.com"
PASSWORT = "SuperGeheimesPasswort123!"
UMGEBUNG = {"DATAFORSEO_LOGIN": LOGIN, "DATAFORSEO_PASSWORD": PASSWORT}


def _client(handler) -> httpx.AsyncClient:
    """Client, der ausschließlich über den übergebenen Handler antwortet."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _antwort(nutzlast: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=nutzlast)

    return handler


def _quelle(handler=None, config=None, umgebung=UMGEBUNG) -> DataForSEODataSource:
    return DataForSEODataSource(
        source_config=config or {},
        client=_client(handler) if handler else None,
        umgebung=umgebung,
    )


# --- Beispielantworten, gekürzt auf die Felder, die wir auswerten -----------

SERP_ANTWORT = {
    "status_code": 20000,
    "status_message": "Ok.",
    "tasks": [
        {
            "status_code": 20000,
            "result": [
                {
                    "keyword": "campingplatz bodensee",
                    "items": [
                        {"type": "paid", "domain": "anzeige.de", "title": "Werbung"},
                        {
                            "type": "organic",
                            "rank_group": 1,
                            "rank_absolute": 1,
                            "domain": "campingamsee.com",
                            "title": "Camping am See – Bodensee",
                            "url": "https://campingamsee.com/",
                        },
                        {
                            "type": "people_also_ask",
                            "title": "Wo ist es am schönsten?",
                        },
                        {
                            "type": "organic",
                            "rank_group": 2,
                            "rank_absolute": 3,
                            "domain": "konkurrenz.de",
                            "title": "Die 10 besten Campingplätze",
                            "url": "https://konkurrenz.de/bodensee",
                        },
                    ],
                }
            ],
        }
    ],
}

SUCHVOLUMEN_ANTWORT = {
    "status_code": 20000,
    "tasks": [
        {
            "status_code": 20000,
            "result": [
                {
                    "keyword": "campingplatz bodensee",
                    "search_volume": 8100,
                    "competition": "LOW",
                    "cpc": 0.42,
                },
                {
                    "keyword": "stellplatz allensbach",
                    "search_volume": 210,
                    "competition": "LOW",
                    "cpc": 0.11,
                },
            ],
        }
    ],
}

BACKLINK_ANTWORT = {
    "status_code": 20000,
    "tasks": [
        {
            "status_code": 20000,
            "result": [
                {
                    "target": "campingamsee.com",
                    "referring_domains": 143,
                    "backlinks": 2871,
                    "rank": 212,
                }
            ],
        }
    ],
}


# ===========================================================================
# 1. Ohne Zugangsdaten: still, sauber, ohne Ausnahme
# ===========================================================================


@pytest.mark.asyncio
class TestOhneZugangsdaten:
    async def test_serp_meldet_nicht_konfiguriert(self):
        quelle = _quelle(umgebung={})
        ergebnis = await quelle.serp_ergebnisse("campingplatz bodensee")
        assert ergebnis.konfiguriert is False
        assert ergebnis.treffer == []
        assert "nicht konfiguriert" in ergebnis.fehler

    async def test_suchvolumen_und_backlinks_ebenfalls(self):
        quelle = _quelle(umgebung={})
        volumen = await quelle.suchvolumen(["a", "b"])
        backlinks = await quelle.backlink_uebersicht("https://example.com/x")
        assert volumen.konfiguriert is False and volumen.eintraege == []
        assert backlinks.konfiguriert is False
        assert backlinks.verweisende_domains is None
        # Domain wird trotzdem sauber normalisiert zurückgemeldet
        assert backlinks.domain == "example.com"

    async def test_kein_netzwerkzugriff_ohne_zugangsdaten(self):
        """Ohne Zugangsdaten darf nicht einmal eine Anfrage rausgehen."""

        def handler(request):  # pragma: no cover — darf nie laufen
            raise AssertionError("Es wurde trotz fehlender Zugangsdaten gefragt!")

        quelle = _quelle(handler=handler, umgebung={})
        await quelle.serp_ergebnisse("egal")
        await quelle.suchvolumen(["egal"])
        await quelle.backlink_uebersicht("example.com")
        assert quelle.abfragen_verbraucht == 0

    async def test_authenticate_wirft_nicht(self):
        """Die Search Console wirft hier — DataForSEO darf das nicht."""
        quelle = _quelle(umgebung={})
        assert await quelle.authenticate() is False
        assert await quelle.test_connection() is False
        assert quelle.status()["konfiguriert"] is False


# ===========================================================================
# 2. Gemockte Antworten korrekt parsen
# ===========================================================================


@pytest.mark.asyncio
class TestAntwortenParsen:
    async def test_serp_wird_geparst(self):
        quelle = _quelle(_antwort(SERP_ANTWORT))
        ergebnis = await quelle.serp_ergebnisse("campingplatz bodensee")

        assert ergebnis.ok
        assert ergebnis.konfiguriert is True
        # Anzeige und "Ähnliche Fragen" fliegen raus — nur organische Treffer
        assert len(ergebnis.treffer) == 2
        erster = ergebnis.treffer[0]
        assert erster.position == 1
        assert erster.domain == "campingamsee.com"
        assert erster.titel == "Camping am See – Bodensee"
        assert erster.url == "https://campingamsee.com/"
        # rank_absolute hat Vorrang vor rank_group
        assert ergebnis.treffer[1].position == 3

    async def test_serp_limit_wird_eingehalten(self):
        quelle = _quelle(_antwort(SERP_ANTWORT))
        ergebnis = await quelle.serp_ergebnisse("campingplatz bodensee", limit=1)
        assert len(ergebnis.treffer) == 1

    async def test_serp_schickt_deutsche_standardwerte(self):
        """Land Deutschland (2276) und Sprache Deutsch ohne Zutun."""
        gesehen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            gesehen.update(json.loads(request.content)[0])
            gesehen["url"] = str(request.url)
            return httpx.Response(200, json=SERP_ANTWORT)

        quelle = _quelle(handler)
        await quelle.serp_ergebnisse("campingplatz bodensee")

        assert gesehen["location_code"] == 2276
        assert gesehen["language_code"] == "de"
        assert gesehen["keyword"] == "campingplatz bodensee"
        assert gesehen["url"].startswith("https://api.dataforseo.com/v3/")

    async def test_suchvolumen_wird_geparst(self):
        quelle = _quelle(_antwort(SUCHVOLUMEN_ANTWORT))
        ergebnis = await quelle.suchvolumen(
            ["campingplatz bodensee", "stellplatz allensbach"]
        )
        assert ergebnis.ok
        assert len(ergebnis.eintraege) == 2
        assert ergebnis.eintraege[0].keyword == "campingplatz bodensee"
        assert ergebnis.eintraege[0].suchvolumen == 8100
        assert ergebnis.eintraege[0].cpc == 0.42
        assert ergebnis.eintraege[1].suchvolumen == 210
        # Alle Begriffe in EINER Abfrage — das ist der günstige Weg
        assert quelle.abfragen_verbraucht == 1

    async def test_backlinks_werden_geparst(self):
        quelle = _quelle(_antwort(BACKLINK_ANTWORT))
        ergebnis = await quelle.backlink_uebersicht(
            "https://campingamsee.com/impressum"
        )
        assert ergebnis.ok
        assert ergebnis.domain == "campingamsee.com"
        assert ergebnis.verweisende_domains == 143
        assert ergebnis.backlinks_gesamt == 2871
        assert ergebnis.vertrauenswert == 212

    async def test_basic_auth_header_wird_gesetzt(self):
        """HTTP Basic — nicht als Query-Parameter, nicht im Rumpf."""
        gesehen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            gesehen["auth"] = request.headers.get("authorization", "")
            gesehen["query"] = request.url.query.decode()
            return httpx.Response(200, json=SERP_ANTWORT)

        quelle = _quelle(handler)
        await quelle.serp_ergebnisse("test")

        erwartet = base64.b64encode(f"{LOGIN}:{PASSWORT}".encode()).decode()
        assert gesehen["auth"] == f"Basic {erwartet}"
        assert gesehen["query"] == ""

    async def test_leeres_ergebnis_ist_kein_fehler(self):
        leer = {"status_code": 20000, "tasks": [{"status_code": 20000, "result": None}]}
        quelle = _quelle(_antwort(leer))
        ergebnis = await quelle.serp_ergebnisse("gibt es nicht")
        assert ergebnis.ok
        assert ergebnis.treffer == []


# ===========================================================================
# 3. Fehlerantworten sauber behandeln
# ===========================================================================


@pytest.mark.asyncio
class TestFehlerBehandlung:
    async def test_401_wird_erklaert(self):
        quelle = _quelle(_antwort({"status_message": "unauthorized"}, status=401))
        ergebnis = await quelle.serp_ergebnisse("test")
        assert not ergebnis.ok
        assert "401" in ergebnis.fehler
        assert "abgelehnt" in ergebnis.fehler
        assert ergebnis.treffer == []

    async def test_429_wird_erklaert(self):
        quelle = _quelle(_antwort({}, status=429))
        ergebnis = await quelle.suchvolumen(["test"])
        assert not ergebnis.ok
        assert "429" in ergebnis.fehler
        assert ergebnis.eintraege == []

    async def test_500_wird_erklaert(self):
        quelle = _quelle(_antwort({}, status=500))
        ergebnis = await quelle.backlink_uebersicht("example.com")
        assert not ergebnis.ok
        assert "500" in ergebnis.fehler
        assert ergebnis.verweisende_domains is None

    async def test_fehler_im_rumpf_trotz_http_200(self):
        """DataForSEO antwortet mit 200 und meldet den Fehler im JSON."""
        koerper = {"status_code": 40501, "status_message": "Invalid Field: 'keyword'"}
        quelle = _quelle(_antwort(koerper))
        ergebnis = await quelle.serp_ergebnisse("test")
        assert not ergebnis.ok
        assert "40501" in ergebnis.fehler

    async def test_netzwerkfehler_wirft_nicht(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Netzwerk weg", request=request)

        quelle = _quelle(handler)
        ergebnis = await quelle.serp_ergebnisse("test")
        assert not ergebnis.ok
        assert "Netzwerkfehler" in ergebnis.fehler

    async def test_kaputtes_json_wirft_nicht(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>kein JSON</html>")

        quelle = _quelle(handler)
        ergebnis = await quelle.serp_ergebnisse("test")
        assert not ergebnis.ok
        assert "JSON" in ergebnis.fehler


# ===========================================================================
# 4. Kostenbremse — Robert zahlt pro Abfrage
# ===========================================================================


@pytest.mark.asyncio
class TestKostenbremse:
    async def test_standardgrenze_ist_25(self):
        assert _quelle().max_abfragen == 25

    async def test_bremse_bricht_ab(self):
        quelle = _quelle(_antwort(SERP_ANTWORT), config={"max_abfragen_pro_lauf": 3})
        for _ in range(3):
            assert (await quelle.serp_ergebnisse("test")).ok

        with pytest.raises(KostenbremseError) as fehler:
            await quelle.serp_ergebnisse("eine zu viel")

        assert "Kostenbremse" in str(fehler.value)
        assert quelle.abfragen_verbraucht == 3  # NICHT hochgezählt

    async def test_bremse_verhindert_die_anfrage_wirklich(self):
        """Nach dem Abbruch darf keine weitere Anfrage rausgehen."""
        zaehler = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            zaehler["n"] += 1
            return httpx.Response(200, json=SERP_ANTWORT)

        quelle = _quelle(handler, config={"max_abfragen_pro_lauf": 1})
        await quelle.serp_ergebnisse("erste")
        for _ in range(5):
            with pytest.raises(KostenbremseError):
                await quelle.serp_ergebnisse("weitere")

        assert zaehler["n"] == 1

    async def test_bremse_gilt_fuer_alle_drei_methoden(self):
        quelle = _quelle(_antwort(SERP_ANTWORT), config={"max_abfragen_pro_lauf": 1})
        await quelle.serp_ergebnisse("erste")
        with pytest.raises(KostenbremseError):
            await quelle.suchvolumen(["zweite"])
        with pytest.raises(KostenbremseError):
            await quelle.backlink_uebersicht("example.com")

    async def test_zuruecksetzen_gibt_budget_frei(self):
        quelle = _quelle(_antwort(SERP_ANTWORT), config={"max_abfragen_pro_lauf": 1})
        await quelle.serp_ergebnisse("erste")
        quelle.budget_zuruecksetzen()
        assert (await quelle.serp_ergebnisse("zweite")).ok

    async def test_abbruch_wird_protokolliert(self, caplog):
        quelle = _quelle(_antwort(SERP_ANTWORT), config={"max_abfragen_pro_lauf": 0})
        with caplog.at_level(logging.ERROR, logger="seo_autopilot.sources.dataforseo"):
            with pytest.raises(KostenbremseError):
                await quelle.serp_ergebnisse("test")
        assert "Kostenbremse" in caplog.text


# ===========================================================================
# 5. Zugangsdaten dürfen NIRGENDS auftauchen
# ===========================================================================


@pytest.mark.asyncio
class TestGeheimhaltung:
    async def test_zugangsdaten_nicht_in_fehlermeldungen(self):
        """Selbst wenn die Gegenstelle den Auth-Header zurückspiegelt."""

        def handler(request: httpx.Request) -> httpx.Response:
            # Bösartigster Fall: der Server plappert alles zurück
            return httpx.Response(
                200,
                json={
                    "status_code": 40100,
                    "status_message": (
                        f"Auth failed for {LOGIN} with password {PASSWORT} "
                        f"(header: {request.headers.get('authorization')})"
                    ),
                },
            )

        quelle = _quelle(handler)
        ergebnis = await quelle.serp_ergebnisse("test")

        assert not ergebnis.ok
        for geheim in (
            LOGIN,
            PASSWORT,
            base64.b64encode(f"{LOGIN}:{PASSWORT}".encode()).decode(),
        ):
            assert geheim not in ergebnis.fehler
        assert "***" in ergebnis.fehler

    async def test_zugangsdaten_nicht_in_logs(self, caplog):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status_code": 40100,
                    "status_message": f"{LOGIN} / {PASSWORT} abgelehnt",
                },
            )

        with caplog.at_level(logging.DEBUG):
            quelle = _quelle(handler)
            await quelle.serp_ergebnisse("test")
            await quelle.suchvolumen(["a"])
            await quelle.backlink_uebersicht("example.com")

        assert caplog.text  # es wurde überhaupt geloggt
        assert LOGIN not in caplog.text
        assert PASSWORT not in caplog.text
        # auch nicht gekürzt: kein Anfangsstück des Passworts
        assert PASSWORT[:8] not in caplog.text

    async def test_status_enthaelt_keine_zugangsdaten(self):
        status = _quelle().status()
        gedruckt = json.dumps(status)
        assert LOGIN not in gedruckt
        assert PASSWORT not in gedruckt
        assert status["konfiguriert"] is True
        assert status["herkunft"] == "Umgebungsvariablen"

    async def test_netzwerkfehler_meldung_ist_sauber(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(
                f"connect to {LOGIN}:{PASSWORT}@api.dataforseo.com failed",
                request=request,
            )

        quelle = _quelle(handler)
        ergebnis = await quelle.backlink_uebersicht("example.com")
        assert PASSWORT not in ergebnis.fehler
        assert LOGIN not in ergebnis.fehler


# ===========================================================================
# 6. Zugangsdaten aus Datei + Registrierung als Quelle
# ===========================================================================


@pytest.mark.asyncio
class TestZugangsdatenUndRegistrierung:
    async def test_json_datei_wird_gelesen(self, tmp_path):
        datei = tmp_path / "dataforseo.json"
        datei.write_text(json.dumps({"login": LOGIN, "password": PASSWORT}))
        quelle = DataForSEODataSource(
            source_config={"credentials_path": str(datei)}, umgebung={}
        )
        assert quelle.ist_konfiguriert is True
        assert quelle.status()["herkunft"] == "Datei dataforseo.json"

    async def test_zeilenformat_wird_gelesen(self, tmp_path):
        datei = tmp_path / "creds.env"
        datei.write_text(
            f"# Kommentar\nDATAFORSEO_LOGIN={LOGIN}\nDATAFORSEO_PASSWORD={PASSWORT}\n"
        )
        quelle = DataForSEODataSource(
            source_config={"credentials_path": str(datei)}, umgebung={}
        )
        assert quelle.ist_konfiguriert is True

    async def test_fehlende_datei_ist_kein_absturz(self, tmp_path):
        quelle = DataForSEODataSource(
            source_config={"credentials_path": str(tmp_path / "gibtsnicht.json")},
            umgebung={},
        )
        assert quelle.ist_konfiguriert is False
        ergebnis = await quelle.serp_ergebnisse("test")
        assert ergebnis.konfiguriert is False

    async def test_umgebung_schlaegt_datei(self, tmp_path):
        datei = tmp_path / "alt.json"
        datei.write_text(json.dumps({"login": "alt", "password": "alt"}))
        quelle = DataForSEODataSource(
            source_config={"credentials_path": str(datei)}, umgebung=UMGEBUNG
        )
        assert quelle.status()["herkunft"] == "Umgebungsvariablen"

    async def test_registrierung_aus_projektkonfiguration(self):
        projekt = {
            "enabled_sources": ["gsc", "dataforseo"],
            "source_config": {"dataforseo": {"max_abfragen_pro_lauf": 7}},
        }
        quelle = quelle_aus_projekt(projekt)
        assert isinstance(quelle, DataForSEODataSource)
        assert quelle.max_abfragen == 7

    async def test_registrierung_ohne_aktivierung(self):
        projekt = {"enabled_sources": ["gsc"], "source_config": {}}
        assert quelle_aus_projekt(projekt) is None
