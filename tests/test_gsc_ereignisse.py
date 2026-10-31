"""Tests fuer die Search-Console-Ereignisse (ohne Netz, ohne Google)."""

import email
from datetime import date

from seo_autopilot import gsc_ereignisse as g

PROJEKTE = {
    "camping": {
        "domain": "https://camping-beispiel.de",
        "betriebsart": "autopilot",
        "source_config": {"indexnow": {"key": "abc123"}},
    },
    "tentacl-ai": {"domain": "https://tentacl.ai"},
}

MAIL_SCHEMA = {
    "id": "<m1@google>",
    "betreff": "Neue Probleme vom Typ „Ereignisse für strukturierte Daten“ für camping-beispiel.de erkannt",
    "text": (
        "Die Search Console hat festgestellt, dass Ihre Website von 7 Problem(en) des Typs "
        '„Ereignisse für strukturierte Daten“ betroffen ist.\n* Feld "image" fehlt\n'
        '* Feld "organizer" fehlt\n* Feld "endDate" fehlt\n'
    ),
}

SEITE_GUT = (
    '<script type="application/ld+json">{"@type":"ItemList","itemListElement":'
    '[{"@type":"ListItem","item":{"@type":"Event","name":"Markt","image":["x.jpg"],'
    '"organizer":{"@type":"Organization","name":"Gemeinde"},"endDate":"2026-11-01"}}]}'
    "</script>"
)
SEITE_LUECKE = (
    '<script type="application/ld+json">{"@type":"Event","name":"Fest",'
    '"endDate":"2026-11-01"}</script>'
)


def holer(seiten):
    def holen(url):
        if url not in seiten:
            raise RuntimeError("404")
        return seiten[url]

    return holen


def test_einstufen_nach_betreff():
    assert g.einstufen(MAIL_SCHEMA["betreff"], "") == ("probleme", g.WARNUNG)
    assert g.einstufen("Manuelle Maßnahme für example.com", "")[1] == g.KRITISCH
    assert (
        g.einstufen("Traffic der Google-Suche für camping-beispiel.de überwachen", "")[1]
        == g.INFO
    )
    assert (
        g.einstufen("Präsenz von camping-beispiel.de auf Google verbessern", "")[1]
        == g.INFO
    )
    assert (
        g.einstufen("Neue Probleme mit der Seitenindexierung", "")[0] == "indexierung"
    )


def test_domain_und_projekt():
    assert g.domain_aus(MAIL_SCHEMA["betreff"], "") == "camping-beispiel.de"
    assert g.projekt_zu_domain(PROJEKTE, "www.camping-beispiel.de") == "camping"
    assert g.projekt_zu_domain(PROJEKTE, "fremd.de") is None


def test_felder_und_typ_aus_mail():
    assert g.fehlende_felder(MAIL_SCHEMA["text"]) == ["endDate", "image", "organizer"]
    assert g.schema_typ_aus(MAIL_SCHEMA["text"]) == "Event"


def test_mail_fremder_domain_wird_ignoriert():
    mail = dict(MAIL_SCHEMA, betreff="Neue Probleme für fremd.de erkannt")
    assert g.ereignis_aus_mail(mail, PROJEKTE) is None


def test_gegenprobe_schon_behoben_und_offen():
    holen = holer({"https://a/1": SEITE_GUT, "https://a/2": SEITE_LUECKE})
    gut = g.schema_gegenprobe(["https://a/1"], "Event", ["image", "organizer"], holen)
    assert gut["objekte"] == 1 and gut["fehlt"] == {}
    assert "Schon behoben" in g.gegenprobe_text(gut)
    offen = g.schema_gegenprobe(
        ["https://a/1", "https://a/2"],
        "Event",
        ["image", "organizer", "endDate"],
        holen,
    )
    assert offen["fehlt"] == {"image": 1, "organizer": 1}
    assert "noch offen" in g.gegenprobe_text(offen)


def test_mail_lauf_meldet_jede_mail_nur_einmal():
    con = g.db_oeffnen(":memory:")
    seiten = {
        "https://camping-beispiel.de/sitemap.xml": "<urlset><url><loc>https://camping-beispiel.de/v/</loc></url></urlset>",
        "https://camping-beispiel.de/v/": SEITE_GUT,
    }
    erst = g.mail_lauf(con, PROJEKTE, holer(seiten), [MAIL_SCHEMA])
    assert len(erst) == 1 and "Schon behoben" in erst[0]["reaktion"]
    assert g.mail_lauf(con, PROJEKTE, holer(seiten), [MAIL_SCHEMA]) == []


def test_sitemap_ereignisse():
    heute = date(2026, 10, 31)
    sm = [
        {
            "path": "https://x/sitemap.xml",
            "errors": "2",
            "lastDownloaded": "2026-10-01T00:00:00Z",
        }
    ]
    arten = {e["art"] for e in g.sitemap_ereignisse("p", "sc-domain:x", sm, heute)}
    assert arten == {"sitemap_fehler", "sitemap_alt"}
    frisch = [
        {
            "path": "https://x/sitemap.xml",
            "errors": "0",
            "lastDownloaded": "2026-10-30T00:00:00Z",
        }
    ]
    assert g.sitemap_ereignisse("p", "sc-domain:x", frisch, heute) == []
    assert (
        g.sitemap_ereignisse("p", "sc-domain:x", [], heute)[0]["art"] == "sitemap_fehlt"
    )


def test_neu_einreichen_hoechstens_alle_sieben_tage():
    con = g.db_oeffnen(":memory:")

    class Dienst:
        aufrufe = 0

        def sitemaps(self):
            return self

        def submit(self, **kw):
            Dienst.aufrufe += 1
            return self

        def execute(self):
            return {}

    heute = date(2026, 10, 31)
    a = g.sitemap_neu_einreichen(Dienst(), con, "p", "sc", "s.xml", heute, True)
    b = g.sitemap_neu_einreichen(Dienst(), con, "p", "sc", "s.xml", heute, True)
    assert "eingereicht" in a and "schon neu eingereicht" in b and Dienst.aufrufe == 1
    c = g.sitemap_neu_einreichen(Dienst(), con, "q", "sc", "s.xml", heute, False)
    assert c.startswith("Wuerde") and Dienst.aufrufe == 1


def test_index_ereignis_nur_bei_wechsel_aus_dem_index():
    con = g.db_oeffnen(":memory:")
    con.execute(
        "INSERT INTO gsc_url_status VALUES ('p','u','PASS','Indexiert','2026-10-30')"
    )
    alt = con.execute("SELECT verdict FROM gsc_url_status").fetchone()
    assert g.index_ereignis("p", "u", alt, "PASS", "") is None
    assert g.index_ereignis("p", "u", None, "NEUTRAL", "Gecrawlt") is None
    e = g.index_ereignis("p", "u", alt, "NEUTRAL", "Gecrawlt – zurzeit nicht indexiert")
    assert e["art"] == "aus_index"


def test_tagesauswahl_wandert_und_enthaelt_startseite():
    urls = [f"https://x/{i}/" for i in range(50)]
    a = g.heutige_auswahl(urls, "https://x/", date(2026, 10, 31), 5)
    b = g.heutige_auswahl(urls, "https://x/", date(2026, 11, 1), 5)
    assert a[0] == b[0] == "https://x/" and len(a) == 5 and a[1:] != b[1:]


def test_meldung_info_wird_nicht_gesendet(capsys):
    con = g.db_oeffnen(":memory:")
    info = {
        "schluessel": "k1",
        "project_id": "camping",
        "schwere": g.INFO,
        "titel": "Hallo",
    }
    warn = {
        "schluessel": "k2",
        "project_id": "camping",
        "schwere": g.WARNUNG,
        "titel": "Neue Probleme",
        "felder": ["image"],
        "reaktion": "Schon behoben.",
    }
    g.melden(con, [info, warn], PROJEKTE, trocken=True)
    aus = capsys.readouterr().out
    assert "Neue Probleme" in aus and "Hallo" not in aus and "camping-beispiel.de" in aus


def test_mail_text_aus_html():
    msg = email.message_from_string(
        "Content-Type: text/html; charset=utf-8\n\n<p>Feld &quot;image&quot; fehlt</p>"
    )
    assert 'Feld "image" fehlt' in g.mail_text(msg)


def test_fehlender_zugriff_wird_einmal_gemeldet():
    con = g.db_oeffnen(":memory:")
    p = {"source_config": {"gsc": {"property_url": "sc-domain:camping-beispiel.de"}}}
    e = g.zugang_ereignis(
        "camping", p, "HttpError 403 User does not have sufficient permission"
    )
    assert e["art"] == "zugang_fehlt" and "tentacl-seo-bot" in e["titel"]
    assert g.ereignis_neu(con, e) is True
    assert (
        g.ereignis_neu(con, g.zugang_ereignis("camping", p, "403 forbidden again"))
        is False
    )
    assert g.zugang_ereignis("camping", p, "timeout")["art"] == "api_fehler"


def test_waechter_meldet_stillstand():
    from datetime import datetime, timezone

    from seo_autopilot import health

    jetzt = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)
    con = g.db_oeffnen(":memory:")
    r = health.HealthReport(zeitpunkt=jetzt)
    health._pruefe_gsc_ereignisse(con, "x gsc_ereignisse", jetzt, r)
    assert any("laeuft nicht" in b.titel for b in r.befunde)
    con.execute("INSERT INTO gsc_laeufe VALUES ('2026-11-03T07:50:00+00:00','voll')")
    r2 = health.HealthReport(zeitpunkt=jetzt)
    health._pruefe_gsc_ereignisse(con, "x gsc_ereignisse", jetzt, r2)
    assert r2.befunde == []
    r3 = health.HealthReport(zeitpunkt=jetzt)
    health._pruefe_gsc_ereignisse(con, "kein eintrag", jetzt, r3)
    assert r3.befunde == []
