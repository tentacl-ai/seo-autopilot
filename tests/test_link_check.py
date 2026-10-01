"""Tests fuer kaputte interne Links und Catch-all (analyzers/link_check.py).

Anlass: tentacl.ai (href="home_url", /projekte/artofwork/) und coaching-beispiel.de
(jede falsche Adresse = Startseite) — beides wurde nicht gemeldet.
Alle Abrufe laufen ueber httpx.MockTransport, kein echtes Netz.
"""

import httpx
import pytest

from seo_autopilot.analyzers import link_check
from seo_autopilot.analyzers.link_check import (
    Fingerabdruck,
    fingerabdruck_trennscharf,
    platzhalter_in,
    pruefe_catchall,
    pruefe_interne_links,
    sammle_interne_links,
)

DOMAIN = "https://example.com"
START_HTML = (
    "<html><head><title>Start</title>"
    '<link rel="canonical" href="https://example.com/"></head>'
    "<body><h1>Willkommen</h1>" + "Text " * 200 + "</body></html>"
)


def _seite(url, links, html_extra=""):
    anker = "".join(f'<a href="{h}">x</a>' for h in links)
    return {
        "url": url,
        "final_url": url,
        "html": f"<html><body>{anker}{html_extra}</body></html>",
    }


def _client(routen: dict, fehler_pfade=(), head_status=None, catchall=False):
    """routen: Pfad -> Status (GET). Unbekannte Pfade: 404 bzw. Startseite bei catchall."""

    def handler(request: httpx.Request) -> httpx.Response:
        pfad = request.url.path
        if pfad in fehler_pfade:
            raise httpx.ConnectError("weg", request=request)
        if request.method == "HEAD" and head_status and pfad in head_status:
            return httpx.Response(head_status[pfad])
        status = routen.get(pfad)
        if status is None:
            if catchall:
                return httpx.Response(
                    200, text=START_HTML, headers={"content-type": "text/html"}
                )
            status = 404
        if status == 200 and pfad in ("", "/"):
            return httpx.Response(
                200, text=START_HTML, headers={"content-type": "text/html"}
            )
        body = f"<html><head><title>Seite {pfad}</title></head><body>ok</body></html>"
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


class TestPlatzhalter:
    @pytest.mark.parametrize(
        "href",
        [
            "home_url",
            "{{ url }}",
            "/x/undefined",
            "null",
            "${link}",
            "/a/%7B%7Bslug%7D%7D",
        ],
    )
    def test_platzhalter_erkannt(self, href):
        assert platzhalter_in(href)

    @pytest.mark.parametrize(
        "href", ["/kontakt", "/blog/null-fehler-strategie", "/preise?x=1"]
    )
    def test_normale_links_sind_keine_platzhalter(self, href):
        assert platzhalter_in(href) is None


class TestSammeln:
    def test_ignoriert_mailto_tel_js_anker_und_fremde(self):
        seiten = [
            _seite(
                f"{DOMAIN}/a",
                [
                    "mailto:x@y.de",
                    "tel:+49",
                    "javascript:void(0)",
                    "#top",
                    "https://fremd.de/x",
                    "/b#abschnitt",
                ],
            )
        ]
        ziele = sammle_interne_links(seiten, DOMAIN)
        assert [z.url for z in ziele] == [f"{DOMAIN}/b"]

    def test_www_gilt_als_intern(self):
        ziele = sammle_interne_links(
            [_seite(f"{DOMAIN}/a", ["https://www.example.com/c"])], DOMAIN
        )
        assert len(ziele) == 1

    def test_kauflinks_werden_nie_aufgerufen(self):
        """Kauflinks werden nie aufgerufen, jeder Aufruf zaehlte beim Shop als Klick."""
        html = (
            '<a href="/api/go/sku/creme" rel="sponsored nofollow noopener">Kaufen</a>'
            '<a href="/go/123">Kaufen</a>'
            '<a href="/ratgeber/" rel="nofollow">Ratgeber</a>'
        )
        seite = {"url": f"{DOMAIN}/a", "final_url": f"{DOMAIN}/a", "html": html}
        ziele = sammle_interne_links([seite], DOMAIN)
        assert [z.url for z in ziele] == [f"{DOMAIN}/ratgeber/"]

    def test_quellseiten_werden_gesammelt(self):
        seiten = [_seite(f"{DOMAIN}/a", ["/x"]), _seite(f"{DOMAIN}/b", ["/x"])]
        (ziel,) = sammle_interne_links(seiten, DOMAIN)
        assert ziel.quellen == [f"{DOMAIN}/a", f"{DOMAIN}/b"]


@pytest.mark.asyncio
class TestKaputteLinks:
    async def test_404_ausserhalb_des_crawls_wird_gemeldet(self):
        """Wie tentacl.ai /loesungen/aussendienst/ -> /projekte/artofwork/ (404)."""
        seiten = [
            _seite(
                f"{DOMAIN}/loesungen/aussendienst/",
                ["/projekte/artofwork/", "/kontakt"],
            )
        ]
        async with _client({"/kontakt": 200}) as c:
            befunde, stat = await pruefe_interne_links(seiten, DOMAIN, client=c)
        assert [b["ziel_url"] for b in befunde] == [f"{DOMAIN}/projekte/artofwork/"]
        assert befunde[0]["severity"] == "high"
        assert "/loesungen/aussendienst/" in befunde[0]["description"]
        assert stat["link_ziele_geprueft"] == 2

    async def test_platzhalter_wird_benannt(self):
        """Wie tentacl.ai/help/: href="home_url" -> /help/home_url = 404."""
        seiten = [_seite(f"{DOMAIN}/help/", ["home_url"])]
        async with _client({}) as c:
            befunde, _ = await pruefe_interne_links(seiten, DOMAIN, client=c)
        assert len(befunde) == 1
        assert "home_url" in befunde[0]["description"]
        assert "Platzhalter" in befunde[0]["title"]

    async def test_netzwerkfehler_ist_kein_kaputter_link(self):
        seiten = [_seite(f"{DOMAIN}/a", ["/wackelt"])]
        async with _client({}, fehler_pfade=("/wackelt",)) as c:
            befunde, stat = await pruefe_interne_links(seiten, DOMAIN, client=c)
        assert befunde == []
        assert stat["link_ziele_unklar"] == 1

    @pytest.mark.parametrize("status", [401, 403, 429])
    async def test_geschuetzte_seiten_sind_nicht_kaputt(self, status):
        seiten = [_seite(f"{DOMAIN}/a", ["/intern"])]
        async with _client({"/intern": status}) as c:
            befunde, _ = await pruefe_interne_links(seiten, DOMAIN, client=c)
        assert befunde == []

    async def test_server_ohne_head_wird_per_get_bestaetigt(self):
        """HEAD 405, GET 200 -> gesund."""
        seiten = [_seite(f"{DOMAIN}/a", ["/ok"])]
        async with _client({"/ok": 200}, head_status={"/ok": 405}) as c:
            befunde, _ = await pruefe_interne_links(seiten, DOMAIN, client=c)
        assert befunde == []

    async def test_5xx_nur_wenn_head_und_get_scheitern(self):
        seiten = [_seite(f"{DOMAIN}/a", ["/einmal", "/immer"])]
        async with _client(
            {"/einmal": 500, "/immer": 503}, head_status={"/einmal": 200}
        ) as c:
            befunde, _ = await pruefe_interne_links(seiten, DOMAIN, client=c)
        assert [b["ziel_url"] for b in befunde] == [f"{DOMAIN}/immer"]

    async def test_bekannter_crawlstatus_wird_ohne_abruf_genutzt(self):
        seiten = [_seite(f"{DOMAIN}/a", ["/weg"])]

        def handler(request):
            raise AssertionError("darf nicht abgerufen werden")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            befunde, _ = await pruefe_interne_links(
                seiten, DOMAIN, bekannter_status={f"{DOMAIN}/weg": 410}, client=c
            )
        assert len(befunde) == 1

    async def test_deckel_begrenzt_abrufe(self):
        seiten = [_seite(f"{DOMAIN}/a", [f"/s{i}" for i in range(20)])]
        async with _client({f"/s{i}": 200 for i in range(20)}) as c:
            _, stat = await pruefe_interne_links(seiten, DOMAIN, client=c, max_ziele=5)
        assert stat["link_ziele_geprueft"] == 5
        assert stat["link_ziele_ausgelassen"] == 15

    async def test_ein_befund_je_ziel_nicht_je_quelle(self):
        seiten = [_seite(f"{DOMAIN}/{i}", ["/fehlt"]) for i in range(10)]
        async with _client({}) as c:
            befunde, _ = await pruefe_interne_links(seiten, DOMAIN, client=c)
        assert len(befunde) == 1
        assert len(befunde[0]["quellseiten"]) == 10


@pytest.mark.asyncio
class TestCatchall:
    async def test_catchall_wird_erkannt(self):
        """Wie coaching-beispiel.de: jede falsche Adresse -> 200 + Startseite."""
        async with _client({"/": 200}, catchall=True) as c:
            befund, fp = await pruefe_catchall(DOMAIN, c)
        assert befund["type"] == "soft_404_catchall"
        assert befund["severity"] == "high"
        assert "Startseite" in befund["description"]
        assert fp is not None and fp.titel == "Start"

    async def test_echter_404_ist_kein_befund(self):
        async with _client({"/": 200}) as c:
            befund, fp = await pruefe_catchall(DOMAIN, c)
        assert befund is None and fp is None

    async def test_netzwerkfehler_ist_kein_befund(self):
        def handler(request):
            raise httpx.ConnectError("weg", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            assert await pruefe_catchall(DOMAIN, c) == (None, None)

    async def test_fehlerseite_mit_200_ist_medium(self):
        def handler(request):
            return httpx.Response(
                200,
                text="<html><head><title>Seite nicht gefunden</title></head><body>404</body></html>",
                headers={"content-type": "text/html"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            befund, _ = await pruefe_catchall(DOMAIN, c)
        assert befund["severity"] == "medium"

    async def test_link_auf_nicht_existente_seite_bei_catchall(self):
        """coaching-beispiel /hilfe verlinkt /support — liefert 200, ist aber nur die Startseite."""
        seiten = [_seite(f"{DOMAIN}/hilfe", ["/support", "/"])]
        async with _client({"/": 200}, catchall=True) as c:
            _, fp = await pruefe_catchall(DOMAIN, c)
            befunde, _ = await pruefe_interne_links(
                seiten, DOMAIN, fingerabdruck=fp, client=c
            )
        assert [b["ziel_url"] for b in befunde] == [f"{DOMAIN}/support"]
        assert "Catch-all" in befunde[0]["description"]

    async def test_app_routen_werden_bei_catchall_nicht_gemeldet(self):
        """/login, /dashboard einer SPA liefern dieselbe Huelle wie jede falsche Adresse."""
        seiten = [_seite(f"{DOMAIN}/hilfe", ["/login", "/dashboard", "/support"])]
        async with _client({"/": 200}, catchall=True) as c:
            _, fp = await pruefe_catchall(DOMAIN, c)
            befunde, stat = await pruefe_interne_links(
                seiten, DOMAIN, fingerabdruck=fp, client=c
            )
        assert [b["ziel_url"] for b in befunde] == [f"{DOMAIN}/support"]
        assert stat["link_ziele_app_routen"] == 2

    async def test_echter_404_auf_app_route_wird_gemeldet(self):
        seiten = [_seite(f"{DOMAIN}/a", ["/login"])]
        async with _client({}) as c:
            befunde, _ = await pruefe_interne_links(seiten, DOMAIN, client=c)
        assert len(befunde) == 1

    async def test_query_auf_startseite_ist_kein_catchall(self):
        seiten = [_seite(f"{DOMAIN}/a", ["/?ref=nav"])]
        async with _client({"/": 200}, catchall=True) as c:
            _, fp = await pruefe_catchall(DOMAIN, c)
            befunde, _ = await pruefe_interne_links(
                seiten, DOMAIN, fingerabdruck=fp, client=c
            )
        assert befunde == []

    def test_fingerabdruck_wird_bei_js_huelle_verworfen(self):
        """Sieht eine ECHTE Unterseite aus wie die Zufallsadresse, taugt der Vergleich nicht."""
        fp = Fingerabdruck(titel="App", canonical="https://example.com/", laenge=500)
        huelle = '<html><head><title>App</title><link rel="canonical" href="https://example.com/"></head></html>'
        seiten = [
            {"url": f"{DOMAIN}/", "html": huelle},
            {"url": f"{DOMAIN}/preise", "html": huelle},
        ]
        assert fingerabdruck_trennscharf(fp, seiten, DOMAIN) is None

    def test_fingerabdruck_bleibt_bei_echten_unterseiten(self):
        fp = Fingerabdruck(titel="Start", canonical="https://example.com/", laenge=500)
        seiten = [{"url": f"{DOMAIN}/preise", "html": "<title>Preise</title>"}]
        assert fingerabdruck_trennscharf(fp, seiten, DOMAIN) is fp


@pytest.mark.asyncio
async def test_seiten_hinter_dem_crawl_limit_als_linkquelle():
    """tentacl.ai/help/ lag hinter Seite 40 der Sitemap — href="home_url" blieb unentdeckt."""
    html = '<html><body><a href="home_url">Start</a></body></html>'

    def handler(request):
        if request.url.path == "/help/":
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        quellen = await link_check.hole_linkquellen(
            [f"{DOMAIN}/help/", f"{DOMAIN}/weg/"], c
        )
        befunde, _ = await pruefe_interne_links(quellen, DOMAIN, client=c)
    assert [q["url"] for q in quellen] == [f"{DOMAIN}/help/"]
    assert befunde[0]["ziel_url"] == f"{DOMAIN}/help/home_url"
    assert "home_url" in befunde[0]["description"]


def test_konstanten_laut_auftrag():
    assert link_check.MAX_ZIELE == 150
    assert link_check.PARALLEL == 5
    assert link_check.TIMEOUT == 10.0


@pytest.mark.asyncio
async def test_verlinkte_werkzeugseite_ohne_noindex():
    """tentacl.ai/seo-autopilot/dashboard: nicht in der Sitemap, aber verlinkt und indexierbar."""
    seiten = [
        _seite(f"{DOMAIN}/seo-autopilot/", ["/seo-autopilot/dashboard", "/login"])
    ]

    def handler(request):
        if request.url.path == "/login":
            html = '<html><head><meta name="robots" content="noindex"></head></html>'
        else:
            html = "<html><head><title>Dashboard</title></head><body>x</body></html>"
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        befunde, _ = await pruefe_interne_links(seiten, DOMAIN, client=c)
    assert [(b["type"], b["severity"], b["affected_url"]) for b in befunde] == [
        ("utility_page_indexable", "low", f"{DOMAIN}/seo-autopilot/dashboard")
    ]
    assert "/seo-autopilot/" in befunde[0]["description"]
