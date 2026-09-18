"""Tests fuer die netzlosen Seitenpruefungen (analyzers/seiten_checks.py)."""

import pytest

from seo_autopilot.analyzers.seiten_checks import (
    erster_ebenensprung,
    h1_nur_optisch,
    pruefe_doppelte_titel,
    pruefe_lokales_schema,
    pruefe_mixed_content,
    pruefe_ueberschriften,
    pruefe_werkzeugseiten,
)

D = "https://example.com"


def _s(pfad, **kw):
    url = f"{D}{pfad}"
    basis = {
        "url": url,
        "final_url": url,
        "html": "",
        "schema_data": [],
        "schema_types": [],
    }
    basis.update(kw)
    return basis


class TestWerkzeugseiten:
    def test_dashboard_ohne_noindex(self):
        """tentacl.ai/seo-autopilot/dashboard stand im Index."""
        befunde = pruefe_werkzeugseiten(
            [_s("/seo-autopilot/dashboard")], [f"{D}/seo-autopilot/dashboard"]
        )
        assert [(b["type"], b["severity"]) for b in befunde] == [
            ("utility_page_indexable", "medium")
        ]

    def test_ohne_sitemap_nur_low(self):
        assert pruefe_werkzeugseiten([_s("/login")])[0]["severity"] == "low"

    def test_noindex_im_html_oder_header_ist_ok(self):
        assert (
            pruefe_werkzeugseiten([_s("/login", robots_meta="noindex, follow")]) == []
        )
        assert pruefe_werkzeugseiten([_s("/suche", x_robots_tag="noindex")]) == []

    def test_canonical_woanders_hin_ist_ok(self):
        assert pruefe_werkzeugseiten([_s("/suche", canonical=f"{D}/")]) == []

    @pytest.mark.parametrize(
        "pfad", ["/unternehmensprofil", "/blog/suchmaschinen", "/konto-eroeffnen"]
    )
    def test_inhaltsseiten_mit_aehnlichem_namen(self, pfad):
        assert pruefe_werkzeugseiten([_s(pfad)]) == []


class TestLokalesSchema:
    ADRESSE = "<footer>Muster GmbH, Hochstraße 22, 94405 Landau</footer>"

    def test_adresse_ohne_lokalen_typ(self):
        seiten = [_s("/", html=self.ADRESSE, schema_data=[{"@type": "Organization"}])]
        befunde = pruefe_lokales_schema(seiten, D)
        assert [(b["type"], b["severity"]) for b in befunde] == [
            ("missing_local_business_schema", "medium")
        ]
        assert "Postadresse" in befunde[0]["description"]

    def test_telefonlink_reicht(self):
        seiten = [_s("/kontakt", html='<a href="tel:+491711234567">Anrufen</a>')]
        assert len(pruefe_lokales_schema(seiten, D)) == 1

    def test_nur_einmal_je_website(self):
        seiten = [_s(p, html=self.ADRESSE) for p in ("/", "/kontakt", "/anfahrt")]
        assert len(pruefe_lokales_schema(seiten, D)) == 1

    def test_adresse_nur_auf_ueber_uns_oder_presse_zaehlt_nicht(self):
        """coaching-beispiel: Firmenadresse auf /about und /press — kein Ortsbezug."""
        seiten = [_s("/about", html=self.ADRESSE), _s("/press", html=self.ADRESSE)]
        assert pruefe_lokales_schema(seiten, D) == []

    @pytest.mark.parametrize(
        "typ",
        [
            ["Organization", "FinancialService"],
            "LocalBusiness",
            "Campground",
            "ProfessionalService",
        ],
    )
    def test_lokaler_typ_vorhanden(self, typ):
        seiten = [_s("/", html=self.ADRESSE, schema_data=[{"@type": typ}])]
        assert pruefe_lokales_schema(seiten, D) == []

    def test_adresse_nur_im_impressum_zaehlt_nicht(self):
        """Ein Impressum hat jede Website — daraus folgt kein Ortsbezug."""
        seiten = [_s("/impressum", html=self.ADRESSE), _s("/", html="<p>Software</p>")]
        assert pruefe_lokales_schema(seiten, D) == []


class TestUeberschriften:
    def test_sprung_h1_h3(self):
        assert erster_ebenensprung("<h1>A</h1><h3>B</h3>").startswith("h1 -> h3")

    def test_lueckenlos_und_zurueckspringen_ist_ok(self):
        assert (
            erster_ebenensprung("<h1>A</h1><h2>B</h2><h3>C</h3><h2>D</h2><h3>E</h3>")
            is None
        )

    def test_befund_ist_low(self):
        befunde = pruefe_ueberschriften([_s("/", html="<h2>A</h2><h4>B</h4>")])
        assert [(b["type"], b["severity"]) for b in befunde] == [
            ("heading_level_skipped", "low")
        ]

    def test_h1_nur_optisch(self):
        """tentacl.ai /hotel-software/: <p class="h1"> statt <h1>."""
        assert 'class="h1"' in h1_nur_optisch('<p class="h1 big">Hotelsoftware</p>')
        assert h1_nur_optisch('<h1 class="h1">Echt</h1>') is None
        assert h1_nur_optisch('<div class="h10">x</div>') is None


class TestDoppelteTitel:
    def test_gleicher_titel(self):
        seiten = [_s("/a", title="Start"), _s("/b", title=" start ")]
        befunde = pruefe_doppelte_titel(seiten)
        assert [(b["type"], b["severity"]) for b in befunde] == [
            ("duplicate_title", "medium")
        ]

    def test_canonical_paar_ausgenommen(self):
        seiten = [_s("/a", title="X"), _s("/a?utm=1", title="X", canonical=f"{D}/a")]
        assert pruefe_doppelte_titel(seiten) == []

    def test_noindex_ausgenommen(self):
        seiten = [_s("/a", title="X"), _s("/b", title="X", robots_meta="noindex")]
        assert pruefe_doppelte_titel(seiten) == []

    def test_sprachfassungen_mit_gleichem_titel_ausgenommen(self):
        """coaching-beispiel /faq und /en/faq heissen beide 'FAQ · Coaching-Beispiel'."""
        verbund = [
            {"hreflang": "de", "href": f"{D}/faq"},
            {"hreflang": "en", "href": f"{D}/en/faq"},
        ]
        seiten = [
            _s("/faq", title="FAQ", hreflang=verbund),
            _s("/en/faq", title="FAQ", hreflang=verbund),
        ]
        assert pruefe_doppelte_titel(seiten) == []

    def test_gleiche_beschreibung_ist_low(self):
        seiten = [
            _s("/a", meta_description="Gleich"),
            _s("/b", meta_description="Gleich"),
        ]
        assert [(b["type"], b["severity"]) for b in pruefe_doppelte_titel(seiten)] == [
            ("duplicate_meta_description", "low")
        ]


class TestMixedContent:
    def test_http_skript_auf_https_ist_medium(self):
        seiten = [_s("/", html='<script src="http://cdn.x.de/a.js"></script>')]
        assert pruefe_mixed_content(seiten)[0]["severity"] == "medium"

    def test_http_bild_ist_low(self):
        seiten = [_s("/", html='<img src="http://x.de/a.jpg">')]
        assert pruefe_mixed_content(seiten)[0]["severity"] == "low"

    def test_http_link_ist_kein_mixed_content(self):
        seiten = [
            _s(
                "/",
                html='<a href="http://x.de">x</a><link rel="alternate" href="http://x.de">',
            )
        ]
        assert pruefe_mixed_content(seiten) == []

    def test_http_seite_selbst_wird_nicht_geprueft(self):
        seite = _s("/", html='<script src="http://x.de/a.js"></script>')
        seite["url"] = seite["final_url"] = "http://example.com/"
        assert pruefe_mixed_content([seite]) == []
