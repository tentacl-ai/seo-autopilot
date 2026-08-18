"""
Tests des Wettbewerbsvergleichs.

Zwei Dinge sind hier wichtiger als die Rechnung: dass fremde robots.txt
befolgt wird (wir crawlen fremde Server), und dass das Modul ehrlich sagt,
was es NICHT kann — Platzierungen und Verlinkungen des Wettbewerbs.
"""

from types import SimpleNamespace

import httpx
import pytest

from seo_autopilot.wettbewerb import (
    DEUTLICH,
    MAX_SEITEN,
    WORTE_AUSGEARBEITET,
    Profil,
    Vergleich,
    _bewerte,
    als_text,
    profil_aus_seiten,
    robots_erlaubt,
    wettbewerber_von,
)


def _seite(worte=100, schema=None, meta="Eine Beschreibung", titel="Ein Titel"):
    return SimpleNamespace(
        status_code=200,
        text_content=" ".join(["wort"] * worte),
        word_count=worte,
        schema_types=schema or [],
        meta_description=meta,
        title=titel,
    )


def _profil(domain, worte=500, seiten=5, schema_anteil=0.0, meta_anteil=1.0,
            typen=None):
    return Profil(
        domain=domain,
        seiten=seiten,
        worte_schnitt=worte,
        ausgearbeitete_seiten=seiten,
        mit_schema=int(seiten * schema_anteil),
        schema_typen=typen or [],
        mit_meta_description=int(seiten * meta_anteil),
        titel_laenge_schnitt=50,
    )


class TestFremdeRobotsTxt:
    """Auf fremden Servern ist robots.txt keine Empfehlung."""

    @pytest.mark.asyncio
    async def test_verbotene_adressen_werden_ausgelassen(self, monkeypatch):
        robots = "User-agent: *\nDisallow: /intern/\n"

        async def fake_get(self, url, **kw):
            return httpx.Response(200, text=robots)

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

        erlaubt, raus = await robots_erlaubt(
            "https://fremd.de",
            ["https://fremd.de/", "https://fremd.de/intern/geheim"],
        )

        assert erlaubt == ["https://fremd.de/"]
        assert raus == 1

    @pytest.mark.asyncio
    async def test_komplettes_verbot_wird_befolgt(self, monkeypatch):
        async def fake_get(self, url, **kw):
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

        erlaubt, raus = await robots_erlaubt(
            "https://fremd.de", ["https://fremd.de/a", "https://fremd.de/b"]
        )

        assert erlaubt == []
        assert raus == 2

    @pytest.mark.asyncio
    async def test_fehlende_robots_txt_verbietet_nichts(self, monkeypatch):
        """So verhalten sich Suchmaschinen auch."""

        async def fake_get(self, url, **kw):
            return httpx.Response(404)

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

        erlaubt, raus = await robots_erlaubt("https://fremd.de", ["https://fremd.de/a"])

        assert erlaubt == ["https://fremd.de/a"]
        assert raus == 0

    @pytest.mark.asyncio
    async def test_serverfehler_blockiert_den_vergleich_nicht(self, monkeypatch):
        async def fake_get(self, url, **kw):
            raise httpx.ConnectError("weg")

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

        erlaubt, _ = await robots_erlaubt("https://fremd.de", ["https://fremd.de/a"])

        assert erlaubt == ["https://fremd.de/a"]


class TestProfil:
    def test_verdichtet_gecrawlte_seiten(self):
        p = profil_aus_seiten(
            "https://x.de",
            [
                _seite(worte=800, schema=["Organization"]),
                _seite(worte=200, schema=[], meta=""),
            ],
        )
        assert p.seiten == 2
        assert p.worte_schnitt == 500
        assert p.ausgearbeitete_seiten == 1
        assert p.mit_schema == 1
        assert p.mit_meta_description == 1

    def test_nicht_abrufbare_domain_ist_kein_leeres_profil(self):
        """Sonst sähe eine gesperrte Seite aus wie eine inhaltsleere."""
        p = profil_aus_seiten("https://x.de", [])
        assert not p.erreichbar
        assert p.hinweis

    def test_schema_typen_aus_listen_und_dicts(self):
        p = profil_aus_seiten(
            "https://x.de",
            [_seite(schema=[{"@type": "LocalBusiness"}, "FAQPage"])],
        )
        assert "LocalBusiness" in p.schema_typen
        assert "FAQPage" in p.schema_typen


class TestBewertung:
    def test_vergleich_gegen_den_staerksten_nicht_den_schnitt(self):
        """Man konkurriert um Plaetze mit dem Besten, nicht mit dem Mittelmass."""
        eigenes = _profil("eigen", worte=400)
        fremde = [_profil("schwach", worte=200), _profil("stark", worte=1200)]

        rueck, _ = _bewerte(eigenes, fremde)

        assert any("stark" in r for r in rueck)

    def test_kleiner_unterschied_ist_kein_rueckstand(self):
        eigenes = _profil("eigen", worte=1000)
        fremde = [_profil("andere", worte=1050)]
        rueck, _ = _bewerte(eigenes, fremde)
        assert rueck == []

    def test_fehlende_auszeichnungen_werden_benannt(self):
        eigenes = _profil("eigen", typen=["Organization"])
        fremde = [_profil("andere", typen=["Organization", "FAQPage", "Service"])]

        rueck, _ = _bewerte(eigenes, fremde)

        assert any("FAQPage" in r for r in rueck)

    def test_vorsprung_wird_auch_gezeigt(self):
        eigenes = _profil("eigen", worte=1500)
        fremde = [_profil("andere", worte=400)]
        _, vor = _bewerte(eigenes, fremde)
        assert vor

    def test_ohne_erfassbare_wettbewerber_kein_urteil(self):
        eigenes = _profil("eigen")
        fremde = [Profil(domain="tot", erreichbar=False)]
        assert _bewerte(eigenes, fremde) == ([], [])


class TestEhrlichkeit:
    def test_bericht_nennt_die_grenzen_des_verfahrens(self):
        """Platzierungen und Backlinks sind so NICHT messbar."""
        v = Vergleich(eigenes=_profil("eigen"), fremde=[_profil("andere")])
        text = als_text(v)
        assert "NICHT messbar" in text
        assert "Datenanbieter" in text

    def test_ohne_wettbewerber_sagt_der_bericht_was_zu_tun_ist(self):
        text = als_text(Vergleich(eigenes=_profil("eigen")))
        assert "wettbewerber:" in text
        assert "raten wäre wertlos" in text

    def test_wettbewerber_werden_nicht_geraten(self):
        assert wettbewerber_von({}) == []
        assert wettbewerber_von({"wettbewerber": ["https://a.de"]}) == ["https://a.de"]
