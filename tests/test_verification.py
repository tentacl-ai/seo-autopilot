"""Tests für die Gegenprobe schwerer Befunde.

Zwei Anforderungen, die gleich wichtig sind:
  1. Ein widerlegbarer Fehlalarm MUSS verschwinden.
  2. Ein echter Befund MUSS bleiben — und bei Netzwerkproblemen ebenfalls,
     sonst tauschen wir falsche Alarme gegen übersehene Probleme.
"""

import httpx
import pytest

from seo_autopilot.verification import verify_issues

DOMAIN = "https://example.com"


def _client(routen: dict, fehler: bool = False) -> httpx.AsyncClient:
    """Baut einen Client, der nur die angegebenen Pfade kennt."""

    def handler(request: httpx.Request) -> httpx.Response:
        if fehler:
            raise httpx.ConnectError("Netzwerk weg", request=request)
        pfad = request.url.path or "/"
        if pfad in routen:
            return httpx.Response(200, text=routen[pfad])
        return httpx.Response(404, text="nicht da")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _issue(typ: str, **extra):
    basis = {
        "category": "eeat",
        "type": typ,
        "severity": "high",
        "title": f"Testbefund {typ}",
        "description": "",
    }
    basis.update(extra)
    return basis


LANGER_TEXT = "Impressum. " * 100


@pytest.mark.asyncio
class TestFehlalarmeVerschwinden:
    async def test_impressum_existiert_doch(self):
        client = _client({"/impressum": LANGER_TEXT})
        bleibt, weg = await verify_issues(
            [_issue("missing_impressum")], DOMAIN, client=client
        )
        assert bleibt == []
        assert len(weg) == 1
        assert "erreichbar" in weg[0]["refuted_reason"]

    async def test_datenschutz_unter_abweichendem_pfad(self):
        """Ein Link, der den Standardpfad als Präfix enthält, gilt als Nachweis.

        `/datenschutz-neu` ist mit hoher Wahrscheinlichkeit die
        Datenschutzseite — hier melden wir lieber nichts, als falsch zu warnen.
        """
        client = _client({"/": '<a href="/datenschutz-neu">Datenschutz</a>'})
        bleibt, weg = await verify_issues(
            [_issue("missing_datenschutz")], DOMAIN, client=client
        )
        assert bleibt == []
        assert "verlinkt" in weg[0]["refuted_reason"]

    async def test_voellig_anderer_link_widerlegt_nicht(self):
        """Gegenprobe: /kontakt ist kein Nachweis für eine Datenschutzseite."""
        client = _client({"/": '<a href="/kontakt">Kontakt</a>'})
        bleibt, weg = await verify_issues(
            [_issue("missing_datenschutz")], DOMAIN, client=client
        )
        assert len(bleibt) == 1
        assert weg == []

    async def test_org_schema_als_subtyp(self):
        html = (
            '<script type="application/ld+json">{"@type":"FinancialService"}</script>'
        )
        client = _client({"/": html})
        bleibt, weg = await verify_issues(
            [_issue("missing_org_schema")], DOMAIN, client=client
        )
        assert bleibt == []
        assert "FinancialService" in weg[0]["refuted_reason"]

    async def test_org_schema_als_typ_array(self):
        html = '<script>{"@type": ["Organization","FinancialService"]}</script>'
        client = _client({"/": html})
        bleibt, weg = await verify_issues(
            [_issue("missing_org_schema")], DOMAIN, client=client
        )
        assert bleibt == []

    async def test_seite_ist_doch_verlinkt(self):
        client = _client({"/": '<a href="/projekte">Projekte</a>'})
        bleibt, weg = await verify_issues(
            [_issue("orphan_page", affected_url=f"{DOMAIN}/projekte")],
            DOMAIN,
            client=client,
        )
        assert bleibt == []

    async def test_bilder_haben_leeres_alt(self):
        """Leeres alt ist korrekt für Deko-Bilder — kein Mangel."""
        client = _client({"/x": '<img src="a.jpg" alt=""><img src="b.jpg" alt="Foto">'})
        bleibt, weg = await verify_issues(
            [_issue("images_without_alt", affected_url=f"{DOMAIN}/x")],
            DOMAIN,
            client=client,
        )
        assert bleibt == []


@pytest.mark.asyncio
class TestEchteBefundeBleiben:
    async def test_impressum_fehlt_wirklich(self):
        client = _client({"/": "<html>ohne alles</html>"})
        bleibt, weg = await verify_issues(
            [_issue("missing_impressum")], DOMAIN, client=client
        )
        assert len(bleibt) == 1
        assert weg == []
        assert bleibt[0]["verified"] is True

    async def test_seite_wirklich_nicht_verlinkt(self):
        client = _client({"/": "<a href='/anderes'>x</a>"})
        bleibt, weg = await verify_issues(
            [_issue("unreachable_page", affected_url=f"{DOMAIN}/projekte")],
            DOMAIN,
            client=client,
        )
        assert len(bleibt) == 1

    async def test_bild_ohne_alt_bleibt(self):
        client = _client({"/x": '<img src="a.jpg">'})
        bleibt, weg = await verify_issues(
            [_issue("images_without_alt", affected_url=f"{DOMAIN}/x")],
            DOMAIN,
            client=client,
        )
        assert len(bleibt) == 1

    async def test_netzwerkfehler_behaelt_befund(self):
        """Im Zweifel NICHT verwerfen — sonst uebersehen wir echte Probleme."""
        client = _client({}, fehler=True)
        bleibt, weg = await verify_issues(
            [_issue("missing_impressum")], DOMAIN, client=client
        )
        assert len(bleibt) == 1
        assert weg == []

    async def test_unbekannter_typ_bleibt_unangetastet(self):
        client = _client({"/": "egal"})
        original = _issue("irgendein_neuer_typ")
        bleibt, weg = await verify_issues([original], DOMAIN, client=client)
        assert bleibt == [original]
        assert weg == []

    async def test_nur_schwere_befunde_werden_geprueft(self):
        client = _client({"/impressum": LANGER_TEXT})
        leicht = _issue("missing_impressum", severity="low")
        bleibt, weg = await verify_issues([leicht], DOMAIN, client=client)
        assert bleibt == [leicht]
        assert weg == []
