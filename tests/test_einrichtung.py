"""Tests fuer die Einrichtung „grosse Packung" (einrichtung.py, CLI `einrichten`)
und die Paket-Pruefung im Waechter (health._pruefe_paket).

Kein Netz: Alle Aussenkontakte laufen ueber `Zugriffe` — hier eine Attrappe,
die jede angefragte Adresse mitschreibt (damit auch bewiesen ist, was NICHT
angefragt wurde, z. B. bei abgeschalteten Projekten).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

import pytest
import yaml
from click.testing import CliRunner

from seo_autopilot import einrichtung as ein
from seo_autopilot import health
from seo_autopilot.einrichtung import (
    FEHLER,
    INFO,
    OK,
    WARNUNG,
    Antwort,
    Auftrag,
    Website,
)

HEUTE = date(2026, 9, 18)
KONTO = "bot@tentacl-seo.iam.gserviceaccount.com"

CRONTAB_LIVE = """\
0 7 * * * . /pfad/zugangsdaten.env && ... run --project-id tentacl-ai >> log 2>&1 # SEO Autopilot Weekly Audit
0 8 * * * ... run --project-id coaching-beispiel >> log 2>&1 # SEO Autopilot coaching-beispiel
0 9 * * * ... run --project-id shop-beispiel >> log 2>&1 # SEO Autopilot shop-beispiel
0 10 * * * ... run --project-id beratung-beispiel >> log 2>&1 # SEO Autopilot beratung-beispiel
30 10 * * * ... run --project-id natur-beispiel >> log 2>&1 # SEO Autopilot natur-beispiel
30 11 * * * cd <Installationsordner> && ... selfcheck --notify # SEO Autopilot Waechter
15 11 * * * cd <Installationsordner> && ... historie --importieren # SEO Historie
30 6 * * * ... marktradar --sammeln # SEO Marktbeobachter
40 7 * * 1 ... kundenbericht --senden # SEO Kunden-Wochenbericht
30 8 * * 1 ... chancen --anzahl 10 # SEO Chancenliste
"""

STARTSEITE = """<html><head><title>Finanzierung für Unternehmer | Muster GmbH</title>
<meta name="description" content="Factoring und Leasing für den Mittelstand in Niederbayern.">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"muster.de"},
 {"@type":"Organization","name":"Muster GmbH","url":"https://muster.de"}]}</script>
</head><body><h1>Geld, wenn Sie es brauchen</h1></body></html>"""

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://muster.de/</loc></url>
<url><loc>https://muster.de/factoring</loc></url>
<url><loc>https://muster.de/leasing</loc></url>
</urlset>"""


def _seiten_muster(**extra):
    """Eine gesunde Website: www und http leiten per 301 auf https://muster.de."""
    seiten = {
        "https://muster.de/": Antwort(200, "https://muster.de/", STARTSEITE),
        "https://www.muster.de/": Antwort(
            200, "https://muster.de/", STARTSEITE, [("https://www.muster.de/", 301)]
        ),
        "http://muster.de/": Antwort(
            200, "https://muster.de/", STARTSEITE, [("http://muster.de/", 301)]
        ),
        "https://muster.de/robots.txt": Antwort(
            200,
            "https://muster.de/robots.txt",
            "Sitemap: https://muster.de/sitemap.xml",
        ),
        "https://muster.de/sitemap.xml": Antwort(
            200, "https://muster.de/sitemap.xml", SITEMAP
        ),
    }
    seiten.update(extra)
    return seiten


class FakeGSCQuelle:
    """Wie GSCDataSource.pull_range — fuer den Historien-Import."""

    def __init__(self):
        self.aufrufe = 0

    async def authenticate(self):
        return True

    async def pull_range(self, prop, von, bis, dimensions=None, row_limit=5000):
        self.aufrufe += 1
        if not dimensions:
            return [{"clicks": 5, "impressions": 100, "ctr": 0.05, "position": 8.0}]
        return [
            {"keys": ["x"], "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 5}
        ]


class FakeZugriffe(ein.Zugriffe):
    def __init__(
        self,
        seiten=None,
        gsc_sites=None,
        gsc_summe=None,
        gsc_fehler=None,
        ga4_props=None,
        ga4_nutzer=None,
        ga4_fehler=None,
        crontab=CRONTAB_LIVE,
    ):
        super().__init__("<Ordner mit dem Dienstkonto>/konto.json")
        self.seiten = seiten if seiten is not None else _seiten_muster()
        self.gsc_sites = (
            gsc_sites
            if gsc_sites is not None
            else [{"siteUrl": "sc-domain:muster.de", "permissionLevel": "siteFullUser"}]
        )
        self._summe = (
            gsc_summe if gsc_summe is not None else {"clicks": 12, "impressions": 340}
        )
        self.gsc_fehler = gsc_fehler
        self.ga4_props = (
            ga4_props
            if ga4_props is not None
            else [
                {
                    "id": "111222333",
                    "name": "Muster",
                    "konto": "K",
                    "streams": ["https://www.muster.de/"],
                }
            ]
        )
        self.ga4_nutzer = 57 if ga4_nutzer is None else ga4_nutzer
        self.ga4_fehler = ga4_fehler
        self._crontab = crontab
        self.angefragt = []
        self.quelle = FakeGSCQuelle()

    def get(self, url):
        self.angefragt.append(url)
        return self.seiten.get(url)

    def dienstkonto(self):
        return KONTO

    def gsc_properties(self):
        return self.gsc_sites

    def gsc_summe(self, prop, von, bis):
        if self.gsc_fehler:
            raise self.gsc_fehler
        return self._summe

    def gsc_quelle(self):
        return self.quelle

    def ga4_properties(self):
        return self.ga4_props

    def ga4_test(self, property_id):
        if self.ga4_fehler:
            raise self.ga4_fehler
        return self.ga4_nutzer

    def crontab(self):
        return self._crontab


@pytest.fixture(autouse=True)
def _kein_pagespeed_aus_umgebung(monkeypatch):
    """Der Schluessel-Test soll nicht von der Maschine abhaengen."""
    monkeypatch.setattr(health, "_globaler_pagespeed_schluessel", lambda: "platzhalter")


def _projekte(tmp_path, daten):
    pfad = tmp_path / "projects.yaml"
    pfad.write_text(yaml.dump({"projects": daten}, sort_keys=False), encoding="utf-8")
    return str(pfad)


# --------------------------------------------------------------------------
# Reine Hilfsfunktionen
# --------------------------------------------------------------------------


class TestHilfen:
    def test_sitemap_mit_namensraum_und_index(self):
        locs, index = ein.sitemap_locs(SITEMAP)
        assert len(locs) == 3 and not index
        idx = (
            '<sitemapindex xmlns="x"><sitemap><loc>https://a.de/s1.xml</loc>'
            "</sitemap></sitemapindex>"
        )
        assert ein.sitemap_locs(idx) == (["https://a.de/s1.xml"], True)

    def test_fremde_hosts_auch_www_variante(self):
        adressen = ["https://a.de/", "https://www.a.de/x", "http://kunde-b.de:8080/y"]
        assert ein.fremde_hosts(adressen, "a.de") == ["kunde-b.de", "www.a.de"]

    def test_max_pages_passt_zur_sitemap(self):
        assert ein.max_pages_vorschlag(0) == 40
        assert ein.max_pages_vorschlag(5) == 20
        assert ein.max_pages_vorschlag(45) == 50
        assert ein.max_pages_vorschlag(70) == 80
        assert ein.max_pages_vorschlag(5000) == ein.MAX_PAGES_MAX

    def test_erwartet_aus_schema_organisation(self):
        assert ein.erwartet_vorschlag(STARTSEITE) == "Muster GmbH"

    def test_erwartet_sonst_titel_ohne_zusatz(self):
        html = "<title>natur-beispiel Wien – Coaching in der Natur</title>"
        assert ein.erwartet_vorschlag(html) == "natur-beispiel Wien"
        assert ein.erwartet_vorschlag("<p>nichts</p>") is None

    def test_erwartet_auf_seite(self):
        assert ein.erwartet_auf_seite(STARTSEITE, "muster gmbh")
        assert ein.erwartet_auf_seite(STARTSEITE, "Geld, wenn")  # H1
        assert not ein.erwartet_auf_seite(STARTSEITE, "Campingplatz camping-beispiel")

    def test_naechster_slot_kollidiert_nicht(self):
        # Belegt: 07:00, 08:00, 09:00, 10:00, 10:30 (Audits), 08:30 (chancen Mo)
        assert ein.naechster_slot(CRONTAB_LIVE) == (7, 30)
        mehr = CRONTAB_LIVE + "30 7 * * * ... run --project-id neu # SEO Autopilot x\n"
        assert ein.naechster_slot(mehr) == (9, 30)

    def test_cron_zeile_im_bestehenden_muster(self, monkeypatch):
        # Die Zugangsdaten-Dateien der eigenen Umgebung stehen in der .env
        # (CRON_ENV_DATEIEN), nicht im oeffentlichen Code.
        monkeypatch.setattr(
            ein.settings, "CRON_ENV_DATEIEN", "/pfad/eins.env,/pfad/zwei.env"
        )
        zeile = ein.cron_zeile("kunde", "kunde.de", (7, 30))
        assert zeile.startswith(
            "30 7 * * * set -a && . /pfad/eins.env && . /pfad/zwei.env && set +a && "
        )
        assert zeile.endswith(
            "-m seo_autopilot.cli.main run --project-id kunde >> "
            f"{ein.AUTOPILOT_WURZEL}/logs/cron.log 2>&1 # SEO Autopilot kunde.de"
        )

    def test_cron_zeile_ohne_env_dateien(self, monkeypatch):
        monkeypatch.setattr(ein.settings, "CRON_ENV_DATEIEN", "")
        assert ein.cron_zeile("kunde", "kunde.de", (7, 30)).startswith(
            f"30 7 * * * {ein.AUTOPILOT_WURZEL}/venv/bin/python3"
        )

    def test_cron_vorhanden_exakter_projektname(self):
        assert ein.cron_vorhanden(CRONTAB_LIVE, "beratung-beispiel") == "10:00"
        assert ein.cron_vorhanden(CRONTAB_LIVE, "natur-beispiel") == "10:30"
        assert ein.cron_vorhanden(CRONTAB_LIVE, "jose") is None
        auskommentiert = "# 0 7 * * * ... run --project-id handel-beispiel >> log\n"
        assert ein.cron_vorhanden(auskommentiert, "handel-beispiel") is None


class TestPropertySuche:
    SITES = [
        {
            "siteUrl": "https://shop-beispiel.de/",
            "permissionLevel": "siteRestrictedUser",
        },
        {"siteUrl": "sc-domain:tentacl.ai", "permissionLevel": "siteOwner"},
        {"siteUrl": "https://tentacl.ai/", "permissionLevel": "siteOwner"},
        {"siteUrl": "sc-domain:neu.de", "permissionLevel": "siteUnverifiedUser"},
    ]

    def test_domain_property_schlaegt_praefix(self):
        e = ein.finde_gsc_property("tentacl.ai", self.SITES)
        assert e["siteUrl"] == "sc-domain:tentacl.ai"

    def test_praefix_fuer_subdomain(self):
        e = ein.finde_gsc_property("shop-beispiel.de", self.SITES)
        assert e["siteUrl"] == "https://shop-beispiel.de/"

    def test_www_host_findet_domain_property(self):
        e = ein.finde_gsc_property("www.tentacl.ai", self.SITES)
        assert e["siteUrl"] == "sc-domain:tentacl.ai"

    def test_unbestaetigt_wird_als_solches_geliefert(self):
        e = ein.finde_gsc_property("neu.de", self.SITES)
        assert e["permissionLevel"] == "siteUnverifiedUser"

    def test_gewuenschte_property_muss_existieren(self):
        assert (
            ein.finde_gsc_property("tentacl.ai", self.SITES, "sc-domain:x.de") is None
        )

    def test_ga4_ueber_datenstream_auch_mit_www(self):
        props = [
            {"id": "1", "streams": ["https://tentacl.ai"]},
            {"id": "2", "streams": ["https://www.coaching-beispiel.de/"]},
        ]
        assert ein.finde_ga4_property("coaching-beispiel.de", props)["id"] == "2"
        assert ein.finde_ga4_property("shop-beispiel.de", props) is None


# --------------------------------------------------------------------------
# Schritt 1 — Website
# --------------------------------------------------------------------------


class TestWebsite:
    def test_gesunde_website(self):
        s, w = ein.pruefe_website(FakeZugriffe(), "https://muster.de")
        assert s.status == OK, s.als_text()
        assert w.kanonisch == "https://muster.de"

    def test_www_eingabe_leitet_um_kanonisch_vorschlagen(self):
        """Der coaching-beispiel-Fall: www leitet per 301 auf die Domain ohne www."""
        seiten = _seiten_muster()
        seiten["https://www.muster.de/"] = Antwort(
            200, "https://muster.de/", STARTSEITE, [("https://www.muster.de/", 301)]
        )
        seiten["https://muster.de/"] = Antwort(200, "https://muster.de/", STARTSEITE)
        s, w = ein.pruefe_website(FakeZugriffe(seiten), "https://www.muster.de")
        assert s.status == WARNUNG
        assert w.kanonisch == "https://muster.de"
        assert "dauerhaft weiter auf https://muster.de" in s.text

    def test_www_ohne_weiterleitung_ist_doppelter_inhalt(self):
        seiten = _seiten_muster()
        seiten["https://www.muster.de/"] = Antwort(
            200, "https://www.muster.de/", STARTSEITE
        )
        s, _ = ein.pruefe_website(FakeZugriffe(seiten), "https://muster.de")
        assert s.status == WARNUNG
        assert "301" in s.anweisung and s.wer == ein.WER_BETREUER

    def test_vorlaeufige_weiterleitung_wird_gemeldet(self):
        seiten = _seiten_muster()
        seiten["http://muster.de/"] = Antwort(
            200, "https://muster.de/", STARTSEITE, [("http://muster.de/", 302)]
        )
        s, _ = ein.pruefe_website(FakeZugriffe(seiten), "https://muster.de")
        assert s.status == WARNUNG and "vorübergehend" in s.text

    def test_nicht_erreichbar_mit_handlungsanweisung(self):
        s, w = ein.pruefe_website(FakeZugriffe(seiten={}), "https://muster.de")
        assert s.status == FEHLER and not w.erreichbar
        assert "DNS" in s.anweisung and s.wer

    def test_sitemap_fremde_hosts_werden_gemeldet(self):
        """18.09.: unter einer Port-Adresse lief eine fremde Kundenseite."""
        fremd = SITEMAP.replace(
            "https://muster.de/leasing", "http://anderer-kunde.de:8080/"
        )
        z = FakeZugriffe(
            _seiten_muster(
                **{
                    "https://muster.de/sitemap.xml": Antwort(
                        200, "https://muster.de/sitemap.xml", fremd
                    )
                }
            )
        )
        _, w = ein.pruefe_website(z, "https://muster.de")
        ein.lade_sitemap(z, w)
        s = ein.schritt_sitemap(w, 40)
        assert s.status == WARNUNG
        assert "anderer-kunde.de" in s.text

    def test_sitemap_index_wird_aufgeloest(self):
        index = (
            "<sitemapindex><sitemap><loc>https://muster.de/s1.xml</loc></sitemap>"
            "<sitemap><loc>https://muster.de/s2.xml</loc></sitemap></sitemapindex>"
        )
        seiten = _seiten_muster(
            **{
                "https://muster.de/sitemap.xml": Antwort(200, "x", index),
                "https://muster.de/s1.xml": Antwort(200, "x", SITEMAP),
                "https://muster.de/s2.xml": Antwort(
                    200,
                    "x",
                    "<urlset><url><loc>https://muster.de/neu</loc></url></urlset>",
                ),
            }
        )
        z = FakeZugriffe(seiten)
        _, w = ein.pruefe_website(z, "https://muster.de")
        ein.lade_sitemap(z, w)
        assert len(w.seiten) == 4
        s = ein.schritt_sitemap(w, 3)
        assert s.status == WARNUNG and "max_pages" in s.anweisung

    def test_robots_verweist_auf_www_sitemap(self):
        seiten = _seiten_muster()
        seiten["https://muster.de/robots.txt"] = Antwort(
            200, "x", "Sitemap: https://www.muster.de/sitemap.xml"
        )
        seiten["https://www.muster.de/sitemap.xml"] = Antwort(200, "x", SITEMAP)
        z = FakeZugriffe(seiten)
        _, w = ein.pruefe_website(z, "https://muster.de")
        ein.lade_sitemap(z, w)
        s = ein.schritt_sitemap(w, 40)
        assert s.status == WARNUNG
        assert "Sitemap: https://muster.de/sitemap.xml" in s.anweisung

    def test_erwartet_fehlt_vorschlag(self):
        w = Website("https://muster.de", "https://muster.de", True, STARTSEITE)
        s, wert = ein.schritt_erwartet(w, None)
        assert s.status == WARNUNG and wert == "Muster GmbH"

    def test_erwartet_passt_nicht_zur_seite(self):
        w = Website("https://muster.de", "https://muster.de", True, STARTSEITE)
        s, _ = ein.schritt_erwartet(w, "Campingplatz camping-beispiel")
        assert s.status == FEHLER and "falsche Website" in s.text


# --------------------------------------------------------------------------
# Schritt 2–5 — Quellen
# --------------------------------------------------------------------------


class TestQuellen:
    def test_gsc_daten_kommen_an(self):
        s, prop = ein.pruefe_gsc(FakeZugriffe(), "muster.de", heute=HEUTE)
        assert s.status == OK and prop == "sc-domain:muster.de"
        assert "340 Einblendungen" in s.text

    def test_gsc_ohne_zugriff_sagt_wer_was_tun_muss(self):
        s, prop = ein.pruefe_gsc(FakeZugriffe(gsc_sites=[]), "muster.de", heute=HEUTE)
        assert s.status == FEHLER and prop is None
        assert s.wer == ein.WER_KUNDE
        assert KONTO in s.anweisung
        assert "Nutzer und Berechtigungen" in s.anweisung
        assert "Eingeschränkt" in s.anweisung

    def test_gsc_null_einblendungen_ist_warnung(self):
        """Der shop-beispiel-Fall: Zugriff ok, aber nicht sichtbar."""
        z = FakeZugriffe(gsc_summe={})
        s, _ = ein.pruefe_gsc(z, "muster.de", heute=HEUTE)
        assert s.status == WARNUNG and "0 Einblendungen" in s.text

    def test_gsc_abfrage_403(self):
        z = FakeZugriffe(gsc_fehler=RuntimeError("HttpError 403 User does not have"))
        s, _ = ein.pruefe_gsc(z, "muster.de", heute=HEUTE)
        assert s.status == FEHLER and s.wer == ein.WER_KUNDE

    def test_ga4_automatisch_gefunden_mit_skript_hinweis(self):
        s, pid = ein.pruefe_ga4(FakeZugriffe(), "muster.de")
        assert s.status == OK and pid == "111222333"
        assert "ga4_einrichten.py" in s.text and "NIE automatisch" in s.text

    def test_ga4_fehlt_mit_anleitung(self):
        s, pid = ein.pruefe_ga4(FakeZugriffe(ga4_props=[]), "muster.de")
        assert s.status == FEHLER and pid is None
        assert "Betrachter" in s.anweisung and KONTO in s.anweisung

    def test_ga4_zugriff_verweigert(self):
        z = FakeZugriffe(ga4_fehler=RuntimeError("403 PERMISSION_DENIED"))
        s, _ = ein.pruefe_ga4(z, "muster.de", "123")
        assert s.status == FEHLER and s.wer == ein.WER_KUNDE

    def test_ga4_ohne_nutzer_ist_warnung(self):
        s, _ = ein.pruefe_ga4(FakeZugriffe(ga4_nutzer=0), "muster.de", "123")
        assert s.status == WARNUNG and "Tag" in s.anweisung

    def test_indexnow_ohne_schluessel_erzeugt_einen(self):
        w = Website("https://muster.de", "https://muster.de", True)
        s, key = ein.pruefe_indexnow(
            FakeZugriffe(), w, None, adapter="static", root_path="/srv/www"
        )
        assert s.status == WARNUNG
        assert re.fullmatch(r"[0-9a-f]{32}", key)
        assert f"/srv/www/{key}.txt" in s.anweisung

    def test_indexnow_datei_online(self):
        key = "a" * 32
        z = FakeZugriffe(
            _seiten_muster(**{f"https://muster.de/{key}.txt": Antwort(200, "x", key)})
        )
        w = Website("https://muster.de", "https://muster.de", True)
        s, _ = ein.pruefe_indexnow(z, w, key, zeige_schluessel=False)
        assert s.status == OK and key not in s.text

    def test_bing_liste(self, tmp_path):
        liste = tmp_path / "sites.json"
        liste.write_text('{"sites": [{"host": "muster.de", "key": "k"}]}')
        assert ein.pruefe_bing("www.muster.de", str(liste)).status == OK
        assert ein.pruefe_bing("andere.de", str(liste)).status == WARNUNG

    def test_pagespeed_aus_env_neben_projektliste(self, tmp_path, monkeypatch):
        monkeypatch.setattr(health, "_globaler_pagespeed_schluessel", lambda: None)
        pfad = _projekte(tmp_path, {})
        assert ein.pruefe_pagespeed(pfad).status == FEHLER
        (tmp_path / ".env").write_text("PAGESPEED_API_KEY=abc\n")
        assert ein.pruefe_pagespeed(pfad).status == OK


# --------------------------------------------------------------------------
# Gesamtablauf „einrichten"
# --------------------------------------------------------------------------


@pytest.fixture
def liste(tmp_path):
    sites = tmp_path / "indexnow_sites.json"
    sites.write_text('{"sites": []}')
    return str(sites)


class TestEinrichten:
    def _auftrag(self, **kw):
        basis = dict(projekt="muster", domain="https://muster.de", bericht_an="a@b.de")
        basis.update(kw)
        return Auftrag(**basis)

    def test_ohne_schreiben_bleibt_projektliste_unberuehrt(self, tmp_path, liste):
        pfad = _projekte(tmp_path, {"alt": {"domain": "https://alt.de"}})
        vorher = open(pfad, "rb").read()
        erg = ein.einrichten(
            self._auftrag(), pfad, FakeZugriffe(), heute=HEUTE, sites_pfad=liste
        )
        assert open(pfad, "rb").read() == vorher
        assert not list(tmp_path.glob("*.bak-*"))
        block = yaml.safe_load(erg.yaml_block)["muster"]
        assert block["enabled_sources"] == ["gsc", "ga4"]
        assert block["source_config"]["gsc"]["property_url"] == "sc-domain:muster.de"
        assert block["source_config"]["ga4"]["property_id"] == "111222333"
        assert block["bericht"]["aktiv"] is True
        assert block["bericht"]["empfaenger"] == "a@b.de"
        assert block["betriebsart"] == "copilot"
        assert block["erwartet"] == "Muster GmbH"
        assert block["adapter_config"]["max_pages"] == 20
        assert re.fullmatch(r"[0-9a-f]{32}", block["source_config"]["indexnow"]["key"])
        assert "dataforseo" not in erg.yaml_block
        assert erg.cron_zeile.startswith("30 7 * * * ")
        assert "--project-id muster " in erg.cron_zeile
        assert erg.status_von("8").status == INFO  # Historie nur mit --schreiben

    def test_jeder_fehler_hat_anweisung_und_zustaendigen(self, tmp_path, liste):
        pfad = _projekte(tmp_path, {})
        z = FakeZugriffe(gsc_sites=[], ga4_props=[])
        erg = ein.einrichten(self._auftrag(), pfad, z, heute=HEUTE, sites_pfad=liste)
        fehler = [s for s in erg.schritte if s.status == FEHLER and s.nr != "9"]
        assert {s.nr for s in fehler} >= {"2", "3"}
        for s in fehler:
            assert s.anweisung and s.wer, s.als_text()
        assert not erg.vollstaendig
        assert "Search Console" in erg.status_von("9").text
        assert erg.status_von("8").status == INFO  # ohne GSC keine Historie

    def test_schreiben_mit_sicherung_und_historie(self, tmp_path, liste):
        pfad = _projekte(tmp_path, {"alt": {"domain": "https://alt.de"}})
        vorher = open(pfad, "rb").read()
        db = str(tmp_path / "audit.db")
        z = FakeZugriffe()
        erg = ein.einrichten(
            self._auftrag(schreiben=True),
            pfad,
            z,
            db_pfad=db,
            heute=HEUTE,
            sites_pfad=liste,
        )
        sicherung = tmp_path / "projects.yaml.bak-einrichtung-muster-2026-09-18"
        assert sicherung.exists() and sicherung.read_bytes() == vorher
        daten = yaml.safe_load(open(pfad))["projects"]
        assert set(daten) == {"alt", "muster"}
        assert daten["alt"] == {"domain": "https://alt.de"}
        assert erg.status_von("8").status == OK
        assert z.quelle.aufrufe > 0
        n = sqlite3.connect(db).execute("select count(*) from gsc_historie").fetchone()
        assert n[0] > 0

    def test_bestehendes_projekt_wird_nur_ergaenzt(self, tmp_path, liste):
        alt = {
            "domain": "https://muster.de",
            "name": "Muster alt",
            "adapter_type": "generic",
            "adapter_config": {"max_pages": 40},
            "enabled_sources": ["gsc"],
            "source_config": {
                "gsc": {"property_url": "https://muster.de/", "credentials_path": "/k"}
            },
            "bericht": {"aktiv": True, "empfaenger": "alt@kunde.de"},
            "betriebsart": "autopilot",
        }
        pfad = _projekte(tmp_path, {"muster": alt})
        z = FakeZugriffe(
            gsc_sites=[
                {"siteUrl": "https://muster.de/", "permissionLevel": "siteOwner"}
            ]
        )
        erg = ein.einrichten(
            Auftrag(projekt="muster", schreiben=True),
            pfad,
            z,
            db_pfad=str(tmp_path / "a.db"),
            heute=HEUTE,
            sites_pfad=liste,
        )
        neu = yaml.safe_load(open(pfad))["projects"]["muster"]
        assert neu["name"] == "Muster alt"
        assert neu["betriebsart"] == "autopilot"
        assert neu["bericht"]["empfaenger"] == "alt@kunde.de"
        assert neu["source_config"]["gsc"]["credentials_path"] == "/k"
        assert neu["source_config"]["ga4"]["property_id"] == "111222333"
        assert neu["enabled_sources"] == ["gsc", "ga4"]
        assert neu["adapter_config"]["max_pages"] == 40  # nie kleiner machen
        assert erg.status_von("7").status == WARNUNG  # kein Cron fuer "muster"

    def test_abgeschaltetes_projekt_wird_nicht_geschrieben(self, tmp_path, liste):
        pfad = _projekte(
            tmp_path, {"camping-beispiel": {"domain": "https://x.de", "enabled": False}}
        )
        vorher = open(pfad, "rb").read()
        z = FakeZugriffe()
        erg = ein.einrichten(
            Auftrag(projekt="camping-beispiel", schreiben=True),
            pfad,
            z,
            heute=HEUTE,
            sites_pfad=liste,
        )
        assert erg.schritte[0].status == FEHLER
        assert open(pfad, "rb").read() == vorher
        assert z.angefragt == []  # nicht einmal lesend angefasst


# --------------------------------------------------------------------------
# „einrichten --pruefen"
# --------------------------------------------------------------------------


class TestPruefen:
    def _liste(self, tmp_path):
        return _projekte(
            tmp_path,
            {
                "voll": {
                    "domain": "https://muster.de",
                    "enabled_sources": ["gsc", "ga4"],
                    "adapter_config": {"max_pages": 40},
                    "source_config": {
                        "gsc": {
                            "property_url": "sc-domain:muster.de",
                            "credentials_path": "/k",
                        },
                        "ga4": {"property_id": "111222333", "credentials_path": "/k"},
                        "indexnow": {"key": "b" * 32},
                    },
                    "bericht": {"aktiv": True, "empfaenger": "a@b.de"},
                    "erwartet": "Muster GmbH",
                },
                "nur-gsc": {
                    "domain": "https://muster.de",
                    "enabled_sources": ["gsc"],
                    "source_config": {
                        "gsc": {
                            "property_url": "sc-domain:muster.de",
                            "credentials_path": "/k",
                        }
                    },
                },
                "klein": {
                    "domain": "https://muster.de",
                    "paket": "klein",
                    "enabled_sources": ["gsc"],
                    "source_config": {
                        "gsc": {
                            "property_url": "sc-domain:muster.de",
                            "credentials_path": "/k",
                        }
                    },
                },
                "camping-beispiel": {
                    "domain": "https://camping-beispiel.de",
                    "name": "Campingplatz camping-beispiel - AUS bis Livegang Nov 2026",
                    "enabled": False,
                },
            },
        )

    def _z(self):
        key = "b" * 32
        return FakeZugriffe(
            _seiten_muster(**{f"https://muster.de/{key}.txt": Antwort(200, "x", key)})
        )

    def test_tabelle_und_todos(self, tmp_path, liste):
        pfad = self._liste(tmp_path)
        z = self._z()
        crontab = "0 7 * * * ... run --project-id voll >> log\n"
        stati = ein.pruefe_alle(pfad, z, heute=HEUTE, crontab=crontab, sites_pfad=liste)
        nach = {ps.projekt: ps for ps in stati}

        voll = nach["voll"]
        assert voll.symbol("2") == "✅" and voll.symbol("3") == "✅"
        assert voll.symbol("B") == "✅" and voll.symbol("7") == "✅"
        assert voll.symbol("1c") == "✅"

        nur = nach["nur-gsc"]
        ga4 = next(s for s in nur.schritte if s.nr == "3")
        assert (
            ga4.status == FEHLER and "111222333" in ga4.text
        )  # lesbar, nur nicht eingetragen
        assert next(s for s in nur.schritte if s.nr == "B").status == FEHLER
        assert next(s for s in nur.schritte if s.nr == "7").status == FEHLER
        assert next(s for s in nur.schritte if s.nr == "1c").status == WARNUNG

        klein = nach["klein"]
        assert next(s for s in klein.schritte if s.nr == "3").status == INFO
        assert next(s for s in klein.schritte if s.nr == "B").status == INFO

        text = ein.pruef_text(stati)
        assert "camping-beispiel" in text and "Livegang Nov 2026" in text
        assert "camping-beispiel" not in " ".join(z.angefragt)
        assert "b" * 32 not in text  # IndexNow-Schluessel nicht ausgeben

    def test_nur_ein_projekt(self, tmp_path, liste):
        stati = ein.pruefe_alle(
            self._liste(tmp_path),
            self._z(),
            nur="voll",
            heute=HEUTE,
            crontab="",
            sites_pfad=liste,
        )
        assert [ps.projekt for ps in stati] == ["voll"]


class TestCli:
    def test_pruefen_und_schreiben_schliessen_sich_aus(self):
        from seo_autopilot.cli.main import cli

        r = CliRunner().invoke(cli, ["einrichten", "--pruefen", "--schreiben"])
        assert r.exit_code != 0 and "schreibt nie" in r.output

    def test_ohne_projekt(self):
        from seo_autopilot.cli.main import cli

        r = CliRunner().invoke(cli, ["einrichten"])
        assert r.exit_code != 0 and "--projekt" in r.output

    def test_einrichten_zeigt_yaml_und_cron(self, tmp_path, monkeypatch, liste):
        from seo_autopilot.cli.main import cli

        monkeypatch.setattr(ein, "Zugriffe", lambda *a, **k: FakeZugriffe())
        monkeypatch.setattr(ein, "INDEXNOW_SITES", liste)
        pfad = _projekte(tmp_path, {})
        r = CliRunner().invoke(
            cli,
            [
                "einrichten",
                "--projekt",
                "muster",
                "--domain",
                "https://muster.de",
                "--bericht-an",
                "a@b.de",
                "--projects",
                pfad,
            ],
        )
        assert "projects.yaml (Eintrag unter projects:)" in r.output, r.output
        assert "--project-id muster" in r.output
        assert yaml.safe_load(open(pfad)) == {"projects": {}}


# --------------------------------------------------------------------------
# Waechter: _pruefe_paket
# --------------------------------------------------------------------------


class TestWaechterPaket:
    VOLL = {
        "enabled_sources": ["gsc", "ga4"],
        "source_config": {
            "gsc": {"property_url": "sc-domain:a.de", "credentials_path": "/k"},
            "ga4": {"property_id": "1", "credentials_path": "/k"},
        },
        "bericht": {"aktiv": True, "empfaenger": "a@b.de"},
    }

    def _pruefe(self, projekte):
        report = health.HealthReport()
        health._pruefe_paket(projekte, report)
        return report

    def test_ohne_ga4_und_bericht_warnt_mit_verweis(self):
        """Rot bewiesen: coaching-beispiel/shop-beispiel-Fall (nur GSC)."""
        cfg = {
            "enabled_sources": ["gsc"],
            "source_config": {"gsc": {"property_url": "x", "credentials_path": "/k"}},
        }
        report = self._pruefe({"shop-beispiel": cfg})
        assert len(report.warnungen) == 1
        b = report.warnungen[0]
        assert "Google Analytics 4" in b.titel and "Wochenbericht" in b.titel
        assert "Search Console" not in b.titel
        assert "einrichten --projekt shop-beispiel" in b.abhilfe

    def test_volles_paket_ist_still(self):
        assert self._pruefe({"voll": self.VOLL}).befunde == []

    def test_externer_wochenbericht_gilt_als_eingerichtet(self):
        cfg = dict(
            self.VOLL,
            bericht={
                "aktiv": False,
                "extern_aktiv": True,
                "extern_timer": "kunde-bericht.timer",
            },
        )
        assert self._pruefe({"extern": cfg}).befunde == []

    def test_paket_klein_ist_ausgenommen(self):
        assert (
            self._pruefe({"k": {"paket": "klein", "enabled_sources": []}}).befunde == []
        )

    def test_abgeschaltet_ist_ausgenommen(self):
        assert self._pruefe({"aus": {"enabled": False}}).befunde == []

    def test_ga4_in_quellen_aber_ohne_id_zaehlt_als_fehlend(self):
        cfg = dict(self.VOLL, source_config={"gsc": self.VOLL["source_config"]["gsc"]})
        report = self._pruefe({"x": cfg})
        assert "Google Analytics 4" in report.warnungen[0].titel

    def test_im_selfcheck_verdrahtet(self, tmp_path):
        db = tmp_path / "t.db"
        con = sqlite3.connect(db)
        con.executescript(
            "create table alembic_version (version_num text);"
            "create table seo_projects (id text);"
            "create table seo_issues (id text, audit_id text);"
            "create table seo_audits (id text, project_id text, score real, status text,"
            " issues_found int, total_pages int, started_at text);"
        )
        con.close()
        pfad = _projekte(
            tmp_path, {"nackt": {"domain": "https://a.de", "enabled_sources": []}}
        )
        report = health.run_selfcheck(str(db), pfad, crontab_text="")
        assert any(b.titel.startswith("Paket unvollständig") for b in report.befunde)

    def test_paket_feld_wird_nicht_verworfen(self, tmp_path):
        """ProjectConfig kennt `paket` — sonst verwirft _save_config es still."""
        from seo_autopilot.core.project_manager import ProjectManager

        pfad = _projekte(
            tmp_path, {"k": {"domain": "https://a.de", "name": "K", "paket": "klein"}}
        )
        pm = ProjectManager(pfad)
        pm._save_config()
        assert yaml.safe_load(open(pfad))["projects"]["k"]["paket"] == "klein"
