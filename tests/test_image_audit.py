"""Tests für die Bild- und Ladezeit-Prüfung.

Zwei Anforderungen wiegen gleich schwer:
  1. Ein echter Bildmangel MUSS gefunden werden.
  2. Korrektes Markup darf NIE einen Befund erzeugen — insbesondere ein
     leeres ``alt=""`` bei dekorativen Bildern und ein bereits modernes
     Bildformat. Und wenn die Messung selbst scheitert (Netzwerk), gilt:
     im Zweifel nichts melden.

Alle Tests laufen ohne Netz — die HEAD-Abrufe gehen über httpx.MockTransport.
"""

import httpx
import pytest

from seo_autopilot.analyzers.image_audit import (
    GROESSE_BEFUND,
    ImageAuditor,
    extract_images,
    ist_generischer_dateiname,
)

URL = "https://example.com/seite"


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _client(groessen: dict = None, fehler: bool = False, status: int = 200):
    """Client, der HEAD-Abrufe mit definierten Grössen beantwortet.

    ``groessen`` bildet Pfad -> Bytes ab. Unbekannte Pfade liefern eine kleine
    Datei. ``fehler=True`` lässt jeden Abruf scheitern.
    """
    groessen = groessen or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if fehler:
            raise httpx.ConnectError("Netzwerk weg", request=request)
        pfad = request.url.path
        if status >= 400:
            return httpx.Response(status)
        bytes_ = groessen.get(pfad, 10_000)
        return httpx.Response(
            200,
            headers={
                "content-length": str(bytes_),
                "content-type": "image/jpeg",
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _befunde(html: str, og_tags: dict = None, client=None, **kwargs):
    auditor = ImageAuditor(**kwargs)
    if client is None:
        client = _client()
    async with client:
        return await auditor.audit_pages(
            [{"url": URL, "html": html, "og_tags": og_tags or {}}], client=client
        )


def _typen(befunde):
    return {b["type"] for b in befunde}


# ---------------------------------------------------------------------------
# 1. Bild-Metadaten
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBildMetadaten:
    async def test_leeres_alt_bei_dekobild_ist_kein_befund(self):
        """alt="" ist die KORREKTE Auszeichnung für dekorative Bilder.

        Genau diesen Fehler hat der Autopilot am 2026-08-17 schon einmal
        gemacht — er darf sich nicht wiederholen.
        """
        html = (
            '<img src="/deko.webp" alt="" width="10" height="10">'
            '<img src="/muster.webp" alt="" role="presentation" width="10" height="10">'
        )
        assert "image_missing_alt" not in _typen(await _befunde(html))

    async def test_fehlendes_alt_ist_ein_befund(self):
        html = '<img src="/produkt.webp" width="800" height="600">'
        befunde = await _befunde(html)
        assert "image_missing_alt" in _typen(befunde)

    async def test_bild_mit_bildunterschrift_braucht_kein_alt(self):
        """Eine <figcaption> trägt die Bedeutung bereits für alle Leser."""
        html = (
            "<figure><img src='/diagramm.webp' width='800' height='600'>"
            "<figcaption>Umsatz 2025 nach Quartal</figcaption></figure>"
        )
        assert "image_missing_alt" not in _typen(await _befunde(html))

    async def test_generischer_dateiname_wird_erkannt(self):
        html = '<img src="/IMG_1234.webp" alt="Foto" width="800" height="600">'
        assert "image_generic_filename" in _typen(await _befunde(html))

    async def test_sprechender_dateiname_ist_kein_befund(self):
        html = (
            '<img src="/roter-traktor-baujahr-1968-1024x768.webp" alt="Traktor" '
            'width="800" height="600">'
        )
        assert "image_generic_filename" not in _typen(await _befunde(html))

    async def test_figure_ohne_bildunterschrift(self):
        html = "<figure><img src='/foto-hafen.webp' alt='Hafen' width='800' height='600'></figure>"
        assert "image_figure_without_caption" in _typen(await _befunde(html))


class TestDateinamen:
    """Reine Namenslogik — ohne Netz und ohne HTML."""

    @pytest.mark.parametrize(
        "name",
        ["IMG_1234.jpg", "DSC00042.JPG", "unnamed.png", "20240513.webp", "image.png"],
    )
    def test_generisch(self, name):
        assert ist_generischer_dateiname(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "roter-traktor.jpg",
            "team-foto-2025-1024x768.jpg",
            "hafen-bei-nacht-scaled.jpg",
            "",
        ],
    )
    def test_aussagekraeftig(self, name):
        assert ist_generischer_dateiname(name) is False


# ---------------------------------------------------------------------------
# 2. Layout-Stabilität (CLS)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLayoutStabilitaet:
    async def test_fehlende_masse_werden_erkannt(self):
        html = (
            '<img src="/a.webp" alt="a"><img src="/b.webp" alt="b">'
            '<img src="/c.webp" alt="c">'
        )
        befunde = await _befunde(html)
        assert "image_missing_dimensions" in _typen(befunde)
        treffer = [b for b in befunde if b["type"] == "image_missing_dimensions"][0]
        assert treffer["severity"] == "medium"

    async def test_width_und_height_gesetzt_ist_sauber(self):
        html = '<img src="/a.webp" alt="a" width="800" height="600">'
        assert "image_missing_dimensions" not in _typen(await _befunde(html))

    async def test_aspect_ratio_im_style_genuegt(self):
        """Wer aspect-ratio setzt, reserviert den Platz ebenfalls korrekt."""
        html = '<img src="/a.webp" alt="a" style="aspect-ratio: 16 / 9; width:100%">'
        assert "image_missing_dimensions" not in _typen(await _befunde(html))


# ---------------------------------------------------------------------------
# 3. Ladeverhalten (LCP / lazy loading)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLadeverhalten:
    async def test_erstes_grosses_bild_darf_nicht_lazy_sein(self):
        html = (
            '<img src="/held.webp" alt="Held" width="1600" height="900" loading="lazy">'
        )
        befunde = await _befunde(html)
        assert "image_lcp_lazy_loaded" in _typen(befunde)
        treffer = [b for b in befunde if b["type"] == "image_lcp_lazy_loaded"][0]
        assert treffer["severity"] == "high"

    async def test_kleines_logo_vor_dem_heldenbild_zaehlt_nicht(self):
        """Ein 40x40-Logo ist nie das LCP-Element — das grosse Bild dahinter schon."""
        html = (
            '<img src="/logo.webp" alt="Logo" width="40" height="40">'
            '<img src="/held.webp" alt="Held" width="1600" height="900" loading="lazy">'
        )
        befunde = await _befunde(html)
        treffer = [b for b in befunde if b["type"] == "image_lcp_lazy_loaded"]
        assert treffer, "Das grosse Bild dahinter muss gemeldet werden"
        assert "held.webp" in treffer[0]["description"]

    async def test_eager_erstes_bild_mit_prioritaet_ist_sauber(self):
        html = (
            '<img src="/held.webp" alt="Held" width="1600" height="900" '
            'fetchpriority="high">'
        )
        typen = _typen(await _befunde(html))
        assert "image_lcp_lazy_loaded" not in typen
        assert "image_lcp_no_priority" not in typen

    async def test_bilder_weiter_unten_sollten_lazy_laden(self):
        html = (
            '<img src="/held.webp" alt="h" width="1600" height="900" fetchpriority="high">'
            + "".join(
                f'<img src="/g{i}.webp" alt="g{i}" width="600" height="400">'
                for i in range(6)
            )
        )
        assert "image_no_lazy_loading" in _typen(await _befunde(html))

    async def test_korrektes_lazy_loading_erzeugt_keinen_befund(self):
        html = (
            '<img src="/held.webp" alt="h" width="1600" height="900" fetchpriority="high">'
            + "".join(
                f'<img src="/g{i}.webp" alt="g{i}" width="600" height="400" loading="lazy">'
                for i in range(6)
            )
        )
        assert "image_no_lazy_loading" not in _typen(await _befunde(html))


# ---------------------------------------------------------------------------
# 4. Format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFormat:
    async def test_moderne_formate_erzeugen_keinen_befund(self):
        html = "".join(
            f'<img src="/bild{i}.webp" alt="b{i}" width="800" height="600">'
            for i in range(5)
        )
        assert "image_legacy_format" not in _typen(await _befunde(html))

    async def test_viele_alte_formate_werden_gemeldet(self):
        html = "".join(
            f'<img src="/bild{i}.jpg" alt="b{i}" width="800" height="600">'
            for i in range(5)
        )
        assert "image_legacy_format" in _typen(await _befunde(html))

    async def test_picture_mit_webp_quelle_ist_kein_altformat(self):
        """Das JPEG ist hier nur der Rückfall — eine Meldung wäre falsch."""
        html = "".join(
            "<picture><source type='image/webp' srcset='/b%d.webp'>"
            "<img src='/b%d.jpg' alt='b%d' width='800' height='600'></picture>"
            % (i, i, i)
            for i in range(5)
        )
        assert "image_legacy_format" not in _typen(await _befunde(html))


# ---------------------------------------------------------------------------
# 5. Dateigrösse (echte HEAD-Abrufe, hier gemockt)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDateigroesse:
    async def test_zu_grosses_bild_wird_erkannt(self):
        html = '<img src="/schwer.jpg" alt="schwer" width="1600" height="900" fetchpriority="high">'
        client = _client({"/schwer.jpg": 800_000})
        befunde = await _befunde(html, client=client)
        treffer = [b for b in befunde if b["type"] == "image_oversized"]
        assert treffer, _typen(befunde)
        assert treffer[0]["severity"] == "medium"
        assert "781 KB" in treffer[0]["title"]

    async def test_sehr_grosses_bild_ist_schwerwiegend(self):
        html = '<img src="/riese.jpg" alt="r" width="1600" height="900" fetchpriority="high">'
        client = _client({"/riese.jpg": 2_500_000})
        befunde = await _befunde(html, client=client)
        treffer = [b for b in befunde if b["type"] == "image_oversized"]
        assert treffer[0]["severity"] == "high"

    async def test_kleine_bilder_erzeugen_keinen_befund(self):
        html = '<img src="/klein.webp" alt="k" width="800" height="600" fetchpriority="high">'
        client = _client({"/klein.webp": 40_000})
        assert "image_oversized" not in _typen(await _befunde(html, client=client))

    async def test_netzwerkfehler_erzeugt_keinen_groessenbefund(self):
        """Im Zweifel nichts melden: ein Abrufproblem ist kein Website-Mangel."""
        html = '<img src="/unbekannt.jpg" alt="u" width="1600" height="900" fetchpriority="high">'
        befunde = await _befunde(html, client=_client(fehler=True))
        typen = _typen(befunde)
        assert "image_oversized" not in typen
        assert "image_page_weight" not in typen

    async def test_fehlerstatus_erzeugt_keinen_groessenbefund(self):
        html = '<img src="/weg.jpg" alt="w" width="1600" height="900" fetchpriority="high">'
        befunde = await _befunde(html, client=_client(status=404))
        assert "image_oversized" not in _typen(befunde)

    async def test_gesamtgewicht_der_seite(self):
        html = "".join(
            f'<img src="/b{i}.jpg" alt="b{i}" width="1600" height="900" loading="lazy">'
            for i in range(5)
        )
        client = _client({f"/b{i}.jpg": 600_000 for i in range(5)})
        assert "image_page_weight" in _typen(await _befunde(html, client=client))

    async def test_head_abrufe_sind_gedeckelt(self):
        """Die Prüfung darf einen Audit nicht ausbremsen."""
        aufrufe = []

        def handler(request: httpx.Request) -> httpx.Response:
            aufrufe.append(str(request.url))
            return httpx.Response(200, headers={"content-length": "1000"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        html = "".join(
            f'<img src="/b{i}.jpg" alt="b{i}" width="800" height="600" loading="lazy">'
            for i in range(50)
        )
        await _befunde(html, client=client, max_head_pruefungen=5)
        assert len(aufrufe) == 5


# ---------------------------------------------------------------------------
# 6. Responsive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResponsive:
    async def test_grosses_bild_ohne_srcset(self):
        html = '<img src="/panorama.webp" alt="p" width="1600" height="900" fetchpriority="high">'
        assert "image_missing_srcset" in _typen(await _befunde(html))

    async def test_srcset_vorhanden_ist_sauber(self):
        html = (
            '<img src="/panorama.webp" alt="p" width="1600" height="900" '
            'fetchpriority="high" srcset="/p-400.webp 400w, /p-1600.webp 1600w" '
            'sizes="100vw">'
        )
        assert "image_missing_srcset" not in _typen(await _befunde(html))


# ---------------------------------------------------------------------------
# 7. Soziale Vorschau
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSozialeVorschau:
    async def test_fehlendes_og_image(self):
        assert "og_image_missing" in _typen(await _befunde("<p>ohne Bild</p>"))

    async def test_og_image_nicht_erreichbar(self):
        befunde = await _befunde(
            "<p>x</p>",
            og_tags={"og:image": "https://example.com/weg.png"},
            client=_client(status=404),
        )
        assert "og_image_unreachable" in _typen(befunde)

    async def test_erreichbares_og_image_ist_sauber(self):
        befunde = await _befunde(
            "<p>x</p>",
            og_tags={
                "og:image": "https://example.com/vorschau.png",
                "og:image:width": "1200",
                "og:image:height": "630",
            },
        )
        typen = _typen(befunde)
        assert "og_image_unreachable" not in typen
        assert "og_image_too_small" not in typen
        assert "og_image_missing" not in typen

    async def test_og_image_zu_klein(self):
        befunde = await _befunde(
            "<p>x</p>",
            og_tags={
                "og:image": "https://example.com/vorschau.png",
                "og:image:width": "600",
                "og:image:height": "315",
            },
        )
        assert "og_image_too_small" in _typen(befunde)

    async def test_netzwerkfehler_meldet_og_image_nicht_als_kaputt(self):
        befunde = await _befunde(
            "<p>x</p>",
            og_tags={"og:image": "https://example.com/vorschau.png"},
            client=_client(fehler=True),
        )
        assert "og_image_unreachable" not in _typen(befunde)


# ---------------------------------------------------------------------------
# 8. Zusammenspiel mit dem AnalyzerAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDoppelteBefunde:
    async def test_schalter_unterdrueckt_die_bereits_gemeldeten_typen(self):
        """Der AnalyzerAgent meldet alt-Texte und og:image schon selbst."""
        html = '<img src="/produkt.webp" width="800" height="600">'
        typen = _typen(await _befunde(html, doppelte_befunde_vermeiden=True))
        assert "image_missing_alt" not in typen
        assert "og_image_missing" not in typen

    async def test_die_neuen_pruefungen_laufen_trotzdem(self):
        html = '<img src="/IMG_9999.jpg" width="1600" height="900" loading="lazy">'
        typen = _typen(await _befunde(html, doppelte_befunde_vermeiden=True))
        assert "image_lcp_lazy_loaded" in typen
        assert "image_generic_filename" in typen


# ---------------------------------------------------------------------------
# 9. Auslesen des HTML
# ---------------------------------------------------------------------------


class TestExtraktion:
    def test_relative_adressen_werden_aufgeloest(self):
        bilder = extract_images(
            '<img src="bilder/a.webp" alt="a">', base_url="https://example.com/blog/"
        )
        assert bilder[0].src == "https://example.com/blog/bilder/a.webp"

    def test_attribute_werden_vollstaendig_erfasst(self):
        bilder = extract_images(
            '<img src="/a.webp" alt="Alt" title="Titel" width="800px" height="600" '
            'loading="lazy" fetchpriority="low" srcset="/a-2x.webp 2x" sizes="50vw">',
            base_url="https://example.com/",
        )
        b = bilder[0]
        assert (b.alt, b.title, b.breite, b.hoehe) == ("Alt", "Titel", 800, 600)
        assert (b.loading, b.fetchpriority) == ("lazy", "low")
        assert b.srcset and b.sizes
        assert b.hat_masse is True

    def test_prozentbreite_zaehlt_nicht_als_mass(self):
        """width="100%" reserviert keinen Platz — der Browser kennt die Höhe nicht."""
        bilder = extract_images('<img src="/a.webp" alt="a" width="100%">')
        assert bilder[0].breite is None
        assert bilder[0].hat_masse is False

    def test_endung_faellt_auf_content_type_zurueck(self):
        bilder = extract_images('<img src="/media/12345" alt="a">')
        bilder[0].content_type = "image/webp; charset=binary"
        assert bilder[0].endung == "webp"

    def test_leeres_html_ergibt_keine_bilder(self):
        assert extract_images("") == []


# ---------------------------------------------------------------------------
# 10. Regressionen aus dem echten Lauf vom 2026-08-17
#     (joseph-hehenwarter.de, Next.js — beide Fehlalarme sind hier festgenagelt)
# ---------------------------------------------------------------------------


class TestBilddienstFehlalarme:
    def test_dateiname_hinter_dem_bilddienst_wird_aufgeloest(self):
        """Next.js liefert jedes Bild über /_next/image aus.

        Ohne Auflösung heisst JEDE Datei "image" und die komplette Website
        würde als "nichtssagende Dateinamen" gemeldet.
        """
        bilder = extract_images(
            '<img src="/_next/image?url=%2Fimages%2Flogos%2Fgenerali.png&w=256&q=75" '
            'alt="Generali">',
            base_url="https://example.com/",
        )
        assert bilder[0].dateiname == "generali.png"
        assert ist_generischer_dateiname(bilder[0].dateiname) is False

    def test_endung_folgt_dem_tatsaechlich_gelieferten_format(self):
        """Der Bilddienst wandelt PNG in WebP — gemeldet wird, was ankommt."""
        bilder = extract_images(
            '<img src="/_next/image?url=%2Fheld.png&w=3840" alt="Held">'
        )
        assert bilder[0].endung == "png"  # vor dem Abruf: nur der Pfad bekannt
        assert bilder[0].ist_altformat is True
        bilder[0].content_type = "image/webp"
        assert bilder[0].endung == "webp"
        assert bilder[0].ist_altformat is False

    def test_widerspruechliche_signale_gehen_zugunsten_der_website(self):
        """bild.webp mit Content-Type image/jpeg ist eine Serverfehlkonfiguration.

        Der Browser zeigt trotzdem WebP an. Ein Formatbefund wäre falsch.
        """
        bilder = extract_images('<img src="/held.webp" alt="h">')
        bilder[0].content_type = "image/jpeg"
        assert bilder[0].ist_altformat is False
        assert bilder[0].ist_modernes_format is True

    def test_pfad_ohne_query_bleibt_unveraendert(self):
        bilder = extract_images('<img src="https://example.com/a/held.jpg" alt="h">')
        assert bilder[0].quell_url == "https://example.com/a/held.jpg"
        assert bilder[0].dateiname == "held.jpg"


@pytest.mark.asyncio
async def test_head_abruf_gibt_sich_als_browser_aus():
    """Ohne Browser-Accept liefern Bilddienste das alte Format.

    Bei joseph-hehenwarter.de waren das 3062 KB PNG statt 912 KB WebP — die
    Messung hätte eine Datei bewertet, die kein Besucher je bekommt.
    """
    gesehen = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request.headers.get("accept", ""))
        return httpx.Response(200, headers={"content-length": "1000"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await _befunde('<img src="/a.jpg" alt="a" width="800" height="600">', client=client)
    assert gesehen, "Es muss ein HEAD-Abruf stattgefunden haben"
    assert all("image/webp" in a for a in gesehen), gesehen


@pytest.mark.asyncio
async def test_seite_ohne_html_wird_uebersprungen():
    """Ohne HTML lässt sich nichts ableiten — und nichts behaupten."""
    auditor = ImageAuditor()
    async with _client() as client:
        befunde = await auditor.audit_pages(
            [{"url": URL, "html": "", "og_tags": {"og:image": "/a.png"}}], client=client
        )
    assert not [b for b in befunde if b["type"].startswith("image_")]


def test_schwellenwert_ist_dokumentiert():
    """500 KB ist die Grenze zum echten Befund — als Regressionsanker."""
    assert GROESSE_BEFUND == 500 * 1024
