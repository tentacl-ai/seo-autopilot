"""Tests fuer die hreflang-Pruefung (analyzers/hreflang_audit.py).

Anlass: coaching-beispiel.de weist /en/help-center als hreflang="en" aus, Inhalt und
<html lang> sind deutsch. Kein Netz: httpx.MockTransport.
"""

import httpx
import pytest

from seo_autopilot.analyzers.hreflang_audit import (
    erkenne_sprache,
    pruefe_hreflang,
    pruefe_sprache,
)

D = "https://example.com"
DEUTSCH = (
    "Hier findest du alle Antworten auf deine Fragen. Wir erklären dir, wie du "
    "das Programm nutzt und was du bei der Anmeldung beachten musst. Die Kurse "
    "sind für alle, die mehr Zeit für sich und ihre Familie haben wollen. "
) * 3
ENGLISCH = (
    "Here you will find all the answers to your questions. We explain how you "
    "can use the program and what you should know about the sign up. These "
    "courses are for all of those who want more time with their family. "
) * 3


def _html(lang, text, links=()):
    kopf = "".join(
        f'<link rel="alternate" hreflang="{c}" href="{h}">' for c, h in links
    )
    return f'<html lang="{lang}"><head>{kopf}</head><body><p>{text}</p></body></html>'


PAAR = [
    ("de", f"{D}/hilfe"),
    ("en", f"{D}/en/help-center"),
    ("x-default", f"{D}/hilfe"),
]


def _seite(url, html, hreflang=PAAR):
    return {
        "url": url,
        "final_url": url,
        "status_code": 200,
        "html": html,
        "hreflang": [{"hreflang": c, "href": h} for c, h in hreflang],
    }


def _client(routen):
    def handler(request):
        treffer = routen.get(str(request.url))
        if treffer is None:
            return httpx.Response(
                404, text="nein", headers={"content-type": "text/html"}
            )
        status, html = treffer
        return httpx.Response(status, text=html, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestSprache:
    def test_deutsch(self):
        assert erkenne_sprache(DEUTSCH) == "de"

    def test_englisch(self):
        assert erkenne_sprache(ENGLISCH) == "en"

    def test_zu_wenig_text_ist_unbekannt(self):
        assert erkenne_sprache("Hallo Welt") is None

    def test_deutscher_text_unter_hreflang_en(self):
        """Der coaching-beispiel-Fall: hreflang="en", Text und lang deutsch."""
        b = pruefe_sprache(f"{D}/en/help-center", "en", _html("de", DEUTSCH))
        assert b["type"] == "hreflang_language_mismatch"
        assert b["severity"] == "medium"
        assert "deutsch" in b["description"]

    def test_passende_sprache_ist_kein_befund(self):
        assert pruefe_sprache(f"{D}/en/", "en", _html("en", ENGLISCH)) is None
        assert pruefe_sprache(f"{D}/de/", "de-AT", _html("de-AT", DEUTSCH)) is None

    def test_nur_lang_attribut_falsch_ist_low(self):
        b = pruefe_sprache(f"{D}/en/", "en", _html("de", ENGLISCH))
        assert b["severity"] == "low"

    def test_x_default_wird_nicht_sprachgeprueft(self):
        assert pruefe_sprache(f"{D}/", "x-default", _html("de", DEUTSCH)) is None


@pytest.mark.asyncio
class TestVerbund:
    async def test_coaching_beispiel_fall_gefunden(self):
        de = _seite(f"{D}/hilfe", _html("de", DEUTSCH, PAAR))
        en_html = _html("de", DEUTSCH, PAAR)  # "englische" Fassung ist deutsch
        async with _client({f"{D}/en/help-center": (200, en_html)}) as c:
            befunde = await pruefe_hreflang([de], client=c)
        typen = [b["type"] for b in befunde]
        assert typen == ["hreflang_language_mismatch"]
        assert befunde[0]["affected_url"] == f"{D}/en/help-center"

    async def test_sauberer_verbund_ohne_befund(self):
        de = _seite(f"{D}/hilfe", _html("de", DEUTSCH, PAAR))
        en = _seite(f"{D}/en/help-center", _html("en", ENGLISCH, PAAR))
        async with _client({}) as c:
            assert await pruefe_hreflang([de, en], client=c) == []

    async def test_fehlender_rueckverweis(self):
        de = _seite(f"{D}/hilfe", _html("de", DEUTSCH, PAAR))
        en_ohne = _html("en", ENGLISCH, [("en", f"{D}/en/help-center")])
        async with _client({f"{D}/en/help-center": (200, en_ohne)}) as c:
            befunde = await pruefe_hreflang([de], client=c)
        assert [b["type"] for b in befunde] == ["hreflang_missing_return_link"]

    async def test_kaputtes_ziel(self):
        de = _seite(f"{D}/hilfe", _html("de", DEUTSCH, PAAR))
        async with _client({f"{D}/en/help-center": (404, "")}) as c:
            befunde = await pruefe_hreflang([de], client=c)
        assert [b["type"] for b in befunde] == ["hreflang_broken_target"]

    async def test_catchall_ziel_ist_nicht_existent_statt_falsche_sprache(self):
        """coaching-beispiel /en/imprint liefert nur die (deutsche) Startseite."""
        from seo_autopilot.analyzers.link_check import Fingerabdruck

        start = (
            '<html lang="de"><head><title>Start</title><link rel="canonical" href="https://example.com/"></head><body>'
            + DEUTSCH
            + "</body></html>"
        )
        fp = Fingerabdruck(
            titel="Start", canonical="https://example.com/", laenge=len(start)
        )
        paar = [
            ("de", f"{D}/impressum"),
            ("en", f"{D}/en/imprint"),
            ("x-default", f"{D}/impressum"),
        ]
        de = _seite(f"{D}/impressum", _html("de", DEUTSCH, paar), hreflang=paar)
        async with _client({f"{D}/en/imprint": (200, start)}) as c:
            befunde = await pruefe_hreflang([de], client=c, fingerabdruck=fp)
        assert [b["type"] for b in befunde] == ["hreflang_broken_target"]
        assert "Catch-all" in befunde[0]["description"]

    async def test_netzwerkfehler_ist_kein_befund(self):
        de = _seite(f"{D}/hilfe", _html("de", DEUTSCH, PAAR))

        def handler(request):
            raise httpx.ConnectError("weg", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            assert await pruefe_hreflang([de], client=c) == []

    async def test_x_default_fehlt_ist_low_und_einmalig(self):
        paar = PAAR[:2]
        de = _seite(f"{D}/hilfe", _html("de", DEUTSCH, paar), hreflang=paar)
        en = _seite(f"{D}/en/help-center", _html("en", ENGLISCH, paar), hreflang=paar)
        async with _client({}) as c:
            befunde = await pruefe_hreflang([de, en], client=c)
        assert [(b["type"], b["severity"]) for b in befunde] == [
            ("hreflang_missing_x_default", "low")
        ]

    async def test_ohne_hreflang_nichts_zu_tun(self):
        async with _client({}) as c:
            assert (
                await pruefe_hreflang([_seite(f"{D}/", "<html></html>", ())], client=c)
                == []
            )
