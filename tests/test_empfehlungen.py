"""Empfehlungen je Seite (v1.16): Erzeugen ohne Netz/KI, Plausibilitaet, Cache,
interne Links an der Seite (D), Stand-Funktionen, Kundenbericht-Anschluss."""

import json
import sqlite3

import pytest

from seo_autopilot import empfehlungen as em
from seo_autopilot import kundenbericht as kb
from seo_autopilot.weekly_report import _klartext

SEITE_A = "https://x.de/software/"
SEITE_B = "https://x.de/blog/"
IMPRESSUM = "https://x.de/impressum/"

HTML_A = """<html lang="de"><head><title>Software für Campingplätze | X</title>
<meta name="description" content="Buchung, Kasse und Meldewesen für Ihren Campingplatz."></head>
<body><header><a href="/">X</a></header><main>
<h1>Buchung für Ihren Platz</h1>
<p>Mit X buchen Ihre Gäste den Stellplatz online. Die Software kostet 49 Euro im Monat und läuft im Browser.</p>
<h2>Funktionen</h2>
<p>Online-Buchung, Kasse und Meldewesen in einem System. Sie sparen Zeit an der Rezeption.</p>
</main><footer>Impressum</footer></body></html>"""

HTML_B = """<html lang="de"><head><title>Blog | X</title></head><body><main>
<h1>Neuigkeiten</h1>
<p>Viele Betreiber suchen eine Campingplatz Software, die auch das Meldewesen erledigt.</p>
</main></body></html>"""

HTML_IMPRESSUM = "<html><body><main><h1>Impressum</h1><p>Angaben gemäß § 5 TMG, X GmbH, Musterstraße 1.</p></main></body></html>"

ZEILEN = [
    {
        "query": "campingplatz software",
        "seite": SEITE_A,
        "einblendungen": 200,
        "klicks": 2,
        "position": 12.0,
    },
    {
        "query": "was kostet campingplatz software",
        "seite": SEITE_A,
        "einblendungen": 40,
        "klicks": 0,
        "position": 9.0,
    },
    {
        "query": "campingplatz software kosten",
        "seite": SEITE_A,
        "einblendungen": 30,
        "klicks": 0,
        "position": 9.5,
    },
    {
        "query": "wie funktioniert online buchung campingplatz",
        "seite": SEITE_A,
        "einblendungen": 20,
        "klicks": 0,
        "position": 14.0,
    },
    {
        "query": "site:x.de",
        "seite": SEITE_A,
        "einblendungen": 50,
        "klicks": 0,
        "position": 1.0,
    },
]

CFG = {
    "domain": "https://x.de",
    "name": "X",
    "adapter_type": "generic",
    "adapter_config": {"seo_regeln": {"verbotene_woerter": ["kostenlos"]}},
}

KI_GUT = {
    "faq": [
        {
            "frage": "Was kostet die Software für Campingplätze?",
            "antwort": "Die Software kostet 49 Euro im Monat.",
            "suchbegriff": "was kostet campingplatz software",
        },
        {
            "frage": "Wie funktioniert die Online-Buchung?",
            "antwort": "Ihre Gäste buchen den Stellplatz online im Browser.",
            "suchbegriff": "wie funktioniert online buchung campingplatz",
        },
        {
            "frage": "Was kostet Campingplatz-Software im Vergleich?",
            "antwort": em.PLATZHALTER,
            "suchbegriff": "campingplatz software kosten",
        },
        {
            "frage": "Gibt es eine Schnittstelle zur Buchhaltung?",
            "antwort": "Online-Buchung, Kasse und Meldewesen in einem System.",
            "suchbegriff": "",
        },
    ],
    "ueberschrift": {
        "neu": "Campingplatz Software für Buchung und Kasse",
        "grund": "Hauptbegriff",
    },
}


def _lade(url):
    return {SEITE_A: HTML_A, SEITE_B: HTML_B, IMPRESSUM: HTML_IMPRESSUM}.get(url)


def _ki(antwort):
    aufrufe = []

    def fragen(prompt, system):
        aufrufe.append((prompt, system))
        return json.dumps(antwort, ensure_ascii=False)

    fragen.aufrufe = aufrufe
    return fragen


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "t.db")


SITEMAP = "".join(f"<url><loc>{u}</loc></url>" for u in (SEITE_A, SEITE_B, IMPRESSUM))


@pytest.fixture(autouse=True)
def _sitemap_ohne_netz(monkeypatch):
    monkeypatch.setattr(
        em,
        "html_laden_text",
        lambda url: SITEMAP if url.endswith("sitemap.xml") else None,
    )


def _erzeuge(db, antwort=KI_GUT, zeilen=ZEILEN, seiten=None, cfg=CFG):
    fr = _ki(antwort)
    b = em.erzeugen(
        "p",
        cfg,
        db,
        zeilen=(
            em.aufbereiten(
                [
                    {
                        "keys": [z["query"], z["seite"]],
                        "impressions": z["einblendungen"],
                        "clicks": z["klicks"],
                        "position": z["position"],
                    }
                    for z in zeilen
                ]
            )
            if zeilen
            else []
        ),
        lade=_lade,
        fragen=fr,
        nur_seiten=seiten,
    )
    return b, fr


class TestDatengrundlage:
    def test_such_operatoren_fliegen_raus(self):
        zeilen = em.aufbereiten(
            [
                {"keys": ["site:x.de", SEITE_A], "impressions": 9},
                {"keys": ["camping", SEITE_A], "impressions": 3},
            ]
        )
        assert [z["query"] for z in zeilen] == ["camping"]

    def test_struktur_liest_h2_faq_und_anrede(self):
        html = HTML_A.replace(
            "</head>",
            '<script type="application/ld+json">{"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"Was ist X?"}]}</script></head>',
        )
        s = em.seiten_struktur(html, SEITE_A)
        assert s["h2"] == ["Funktionen"]
        assert s["faq_fragen"] == ["Was ist X?"] and s["hat_faq_schema"]
        assert s["erster_absatz"].startswith("Mit X buchen")
        assert s["anrede"] == "sie"

    def test_gesperrte_seiten(self):
        assert em.seite_gesperrt(IMPRESSUM)
        assert em.seite_gesperrt("https://x.de/datenschutz.html")
        assert em.seite_gesperrt(
            "https://x.de/preise/", {"gesperrte_seiten": ["/preise/"]}
        )
        assert not em.seite_gesperrt(SEITE_A, {"gesperrte_seiten": ["/preise/"]})


class TestErzeugen:
    def test_faq_mit_entwurf_und_platzhalter_nur_im_vorschlag(self, db):
        b, fr = _erzeuge(db, seiten=[SEITE_A])
        assert b["ki_aufrufe"] == 1
        faq = em.laden(db, "p", art=em.ART_FAQ)[0]
        # nur Fragen mit echter Suchanfrage dahinter; die erfundene fliegt raus
        assert len(faq.vorschlag["fragen"]) == 3
        assert "Buchhaltung" not in faq.text
        assert "keine echte Suchanfrage" in " ".join(b["verworfen"])
        assert em.PLATZHALTER in faq.text  # der Kunde sieht, was fehlt
        anw = faq.vorschlag["anwendung"]
        assert len(anw["fragen"]) == 2 and em.PLATZHALTER not in anw["html"]
        # FAQPage-JSON-LD ist optional und geht nicht automatisch mit
        assert "jsonld" not in anw
        assert faq.vorschlag["jsonld_entwurf_optional"]["@type"] == "FAQPage"
        assert "„was kostet campingplatz software“ (40×)" in faq.text

    def test_system_prompt_traegt_regeln_und_anrede(self, db):
        _, fr = _erzeuge(db, seiten=[SEITE_A])
        prompt, system = fr.aufrufe[0]
        assert "kostenlos" in system and "Sie (siezen)" in system
        assert em.PLATZHALTER in system
        assert "site:x.de" not in prompt

    def test_ueberschrift_vorschlag_mit_anwendung(self, db):
        _erzeuge(db, seiten=[SEITE_A])
        u = em.laden(db, "p", art=em.ART_UEBERSCHRIFT)[0]
        assert u.vorschlag["anwendung"] == {
            "typ": "empfehlung_ueberschrift_verbessern",
            "seite": SEITE_A,
            "alt": "Buchung für Ihren Platz",
            "neu": "Campingplatz Software für Buchung und Kasse",
        }

    def test_erfundene_zahl_tabuwort_und_duzen_werden_verworfen(self, db):
        schlecht = {
            "faq": [
                {
                    "frage": "Was kostet die Software?",
                    "antwort": "Nur 29 Euro im Monat.",
                },
                {
                    "frage": "Ist die Software kostenlos testbar?",
                    "antwort": "Ja, sie ist kostenlos testbar.",
                },
                {
                    "frage": "Wie buche ich?",
                    "antwort": "Du buchst deinen Platz online im Browser.",
                },
                {
                    "frage": "Welche Funktionen gibt es?",
                    "antwort": "Online-Buchung, Kasse und Meldewesen.",
                },
            ]
        }
        b, _ = _erzeuge(db, antwort=schlecht, seiten=[SEITE_A])
        assert (
            em.laden(db, "p", art=em.ART_FAQ) == []
        )  # nur 1 gueltige Frage -> verworfen, nicht gespeichert
        gruende = " ".join(b["verworfen"])
        assert "Zahl 29" in gruende and "kostenlos" in gruende and "duzt" in gruende

    def test_cache_kein_zweiter_ki_aufruf_bei_unveraenderter_seite(self, db):
        _erzeuge(db, seiten=[SEITE_A])
        b2, fr2 = _erzeuge(db, seiten=[SEITE_A])
        assert fr2.aufrufe == [] and b2["seiten"] == []

    def test_kostendeckel_max_seiten(self, db):
        zeilen = ZEILEN + [
            {
                "query": "campingplatz software",
                "seite": SEITE_B,
                "einblendungen": 5,
                "klicks": 0,
                "position": 30.0,
            }
        ]
        fr = _ki(KI_GUT)
        em.erzeugen(
            "p",
            CFG,
            db,
            zeilen=[z for z in zeilen if not z["query"].startswith("site:")],
            lade=_lade,
            fragen=fr,
            max_seiten=1,
        )
        assert len(fr.aufrufe) == 1
        assert (
            SEITE_A in fr.aufrufe[0][0]
        )  # die Seite mit den meisten Einblendungen zuerst

    def test_rechtsseiten_werden_nie_bearbeitet(self, db):
        fr = _ki(KI_GUT)
        em.erzeugen(
            "p", CFG, db, zeilen=[], lade=_lade, fragen=fr, nur_seiten=[IMPRESSUM]
        )
        assert fr.aufrufe == [] and em.laden(db, "p") == []

    def test_ohne_suchdaten_keine_empfehlung(self, db):
        """Google-Leitfaden 10.07.2026: ohne echte Nachfrage kein Vorschlag."""
        text = " ".join(["Wort"] * 200)
        html = HTML_A.replace("</main>", f"<p>{text}.</p></main>")
        fr = _ki({"faq": KI_GUT["faq"]})
        b = em.erzeugen("p", CFG, db, zeilen=[], lade=lambda u: html, fragen=fr)
        assert fr.aufrufe == [] and em.laden(db, "p") == []
        assert "Search Console" in b["hinweis"]
        em.erzeugen(
            "p",
            CFG,
            db,
            zeilen=[],
            lade=lambda u: html,
            fragen=fr,
            nur_seiten=[SEITE_A],
        )
        assert fr.aufrufe == [] and em.laden(db, "p") == []

    def test_prompt_schreibt_fuer_menschen_nicht_fuer_ki(self, db):
        _, fr = _erzeuge(db, seiten=[SEITE_A])
        prompt = fr.aufrufe[0][0]
        assert "Schreibe für Menschen" in prompt and "Mini-Abschnitte" in prompt
        assert "keine weiteren Fragen erfinden" in prompt

    def test_arbeitsliste_nach_nachfrage_mal_luecke(self):
        voll = em._nutzen(100, 12.0, em.ART_FAQ, False, 1.0)
        halb = em._nutzen(100, 12.0, em.ART_FAQ, False, 0.5)
        wenig = em._nutzen(10, 12.0, em.ART_FAQ, False, 1.0)
        assert voll > halb and voll > wenig


class TestInterneLinks:
    def test_striking_distance_haengt_an_der_seite_und_nennt_quelle(self, db):
        _erzeuge(db, seiten=[SEITE_A])
        link = em.laden(db, "p", art=em.ART_LINKS)[0]
        v = link.vorschlag
        assert v["zielseite"] == SEITE_A and v["position"] == 12.0
        assert v["quellen"] == [{"seite": SEITE_B, "linktext": "Campingplatz Software"}]
        assert (
            v["anwendung"]["quelle"] == SEITE_B
            and v["anwendung"]["ziel"] == "/software/"
        )

    def test_quelle_die_schon_verlinkt_zaehlt_nicht(self, db):
        html_b = HTML_B.replace(
            "</main>", '<p><a href="/software/">Zur Software</a></p></main>'
        )
        fr = _ki(KI_GUT)
        lade = lambda u: {SEITE_A: HTML_A, SEITE_B: html_b}.get(u)
        em.erzeugen(
            "p",
            CFG,
            db,
            zeilen=[z for z in ZEILEN if "site:" not in z["query"]],
            lade=lade,
            fragen=fr,
            nur_seiten=[SEITE_A],
        )
        assert em.laden(db, "p", art=em.ART_LINKS) == []


class TestNeueSeite:
    def test_begriff_ohne_passende_seite(self, db):
        eigene = {SEITE_A: em.seiten_struktur(HTML_A, SEITE_A)}
        zeilen = [
            {
                "query": "ferienwohnung verwaltung",
                "seite": SEITE_A,
                "einblendungen": 60,
                "klicks": 0,
                "position": 40.0,
            }
        ]
        e = em.neue_seiten("p", zeilen, eigene, ["x"])
        assert e.art == em.ART_NEUE_SEITE and "ferienwohnung verwaltung" in e.text
        assert e.vorschlag["anwendung"] is None


class TestStand:
    def test_stand_zaehlt_und_haengt_wirkung_an(self, db):
        _erzeuge(db, seiten=[SEITE_A])
        faq = em.laden(db, "p", art=em.ART_FAQ)[0]
        em.status_setzen(
            db,
            faq.id,
            em.STATUS_UMGESETZT,
            umgesetzt_am="2026-09-18T10:00:00",
            change_id="c1",
        )
        con = sqlite3.connect(db)
        con.execute(
            "create table wirkung_messungen (change_id text, fenster_tage int, urteil text, notiz text, vorher_position real, nachher_position real)"
        )
        con.execute(
            "insert into wirkung_messungen values ('c1', 14, 'verbessert', 'x', 12.0, 8.0)"
        )
        con.commit()
        con.close()
        s = em.stand(db, "p")
        assert s["umgesetzt"][0]["wirkung"][14]["urteil"] == "verbessert"
        assert s["wirkung"]["besser"] == 1
        assert s["zaehler"][em.STATUS_OFFEN] >= 1
        assert "Wirkung: 14 T verbessert" in em.als_text(
            em.laden(db, "p", status=(em.STATUS_UMGESETZT,)), db=db
        )


class TestBericht:
    def test_abschnitt_und_knopf_uebernahme(self, db, tmp_path, monkeypatch):
        _erzeuge(db, seiten=[SEITE_A])
        daten = kb.empfehlungen_fuer_bericht(db, "p")
        assert 1 <= len(daten["offen"]) <= kb.MAX_EMPFEHLUNGEN
        b = {"name": "X", "empfehlungen": daten, "entscheidungen": []}
        punkte, bedeutung = kb._empfehlungs_punkte("p", b)
        assert bedeutung[0]["art"] == "empfehlung"
        b["entscheidungen"] = [
            {**bedeutung[0], "url": "https://tentacl.de/freigabe/e/T"}
        ]
        html = "".join(kb._abschnitt_empfehlungen(b))
        assert (
            "Was Sie auf Ihren Seiten verbessern können" in html
            and "freigabe/e/T" in html
        )
        # Klick "Ja" -> Status freigegeben
        ablage = tmp_path / "ablage"
        (ablage / "p").mkdir(parents=True)
        (ablage / "p" / "bericht-2026-09-21.json").write_text(
            json.dumps({"entscheidungen": b["entscheidungen"]})
        )
        monkeypatch.setattr(
            kb.ent,
            "antworten",
            lambda ids=None, **k: {bedeutung[0]["id"]: {"wahl": "ja"}},
        )
        meldungen = kb.antworten_uebernehmen(db, "p", ablage=ablage)
        assert meldungen and "freigegeben" in meldungen[0]
        assert (
            em.laden(db, "p", status=(em.STATUS_FREIGEGEBEN,))[0].id
            == bedeutung[0]["empfehlung_id"]
        )

    def test_verweis_von_befund_auf_empfehlung(self, db):
        _erzeuge(db, seiten=[SEITE_A])
        assert "verbessern können" in em.verweis_fuer_befund(db, "p", "thin_content")
        # GEO-Faustregeln verweisen nicht (keine Empfehlung nur "fuer KI")
        assert em.verweis_fuer_befund(db, "p", "geo_structured_format") is None
        assert em.verweis_fuer_befund(db, "p", "missing_impressum") is None

    def test_generische_englische_hinweise_jetzt_deutsch(self):
        for typ in (
            "striking_distance",
            "schema_rich_result_opportunity",
            "geo_paragraph_length",
            "multiple_h1",
        ):
            titel, text = _klartext(typ, "English title")
            assert titel != "English title" and "Details stehen" not in text


def test_frage_braucht_zwei_inhaltswoerter():
    assert not em.ist_frage("ist es normal")
    assert em.ist_frage("was kostet campingplatz software")
    assert em.ist_frage("factoring kosten berechnen")


def test_halbe_antwort_wird_platzhalter_und_metasprache_verworfen():
    ok, grund = em.pruefe_text(
        "Ja, die Unterschrift gilt laut Seitentext als rechtsverbindlich.",
        "Unterschrift rechtsverbindlich",
        {},
        "sie",
    )
    assert not ok and "Prompt" in grund
    struktur = em.seiten_struktur(HTML_A, SEITE_A)
    antwort = {
        "faq": [
            {
                "frage": "Was kostet die Software?",
                "antwort": "Die Software kostet 49 Euro im Monat.",
                "suchbegriff": "was kostet campingplatz software",
            },
            {
                "frage": "Wie buchen Gäste online?",
                "antwort": "Ihre Gäste buchen den Stellplatz online.",
                "suchbegriff": "wie funktioniert online buchung campingplatz",
            },
            {
                "frage": "Welche Funktionen gibt es?",
                "antwort": "Online-Buchung und Kasse. [Antwort vom Betreiber ergänzen]",
                "suchbegriff": "campingplatz software kosten",
            },
        ]
    }
    aufgaben = {"faq": {"fragen": [z for z in ZEILEN if "site:" not in z["query"]]}}
    liste, _ = em.empfehlungen_aus_ki(antwort, "p", struktur, [], aufgaben, [], {}, [])
    fragen = liste[0].vorschlag["fragen"]
    assert fragen[2]["antwort"] == em.PLATZHALTER
    assert len(liste[0].vorschlag["anwendung"]["fragen"]) == 2


def test_begriff_nur_aus_dem_suchbegriff_gilt_als_erfunden():
    seite = "Die Wohnungsübergabe läuft digital am Tablet mit Übergabe-Protokollen."
    ok, grund = em.pruefe_text(
        "Das funktioniert auch bei der Erstvermietung einer Wohnung.",
        seite,
        {},
        None,
        streng=True,
    )
    assert not ok and "Erstvermietung" in grund
    ok, _ = em.pruefe_text(
        "Zusätzlich entstehen die Übergabeprotokolle digital am Tablet.",
        seite,
        {},
        None,
        streng=True,
    )
    assert ok


def test_lange_suchanfrage_bleibt_zuordenbar():
    lang = "wie funktioniert der prozess des factorings und was sind die voraussetzungen fuer kleine firmen"
    zeilen = [
        {
            "query": lang,
            "seite": SEITE_A,
            "einblendungen": 3,
            "klicks": 0,
            "position": 20.0,
        },
        {
            "query": "was kostet campingplatz software",
            "seite": SEITE_A,
            "einblendungen": 40,
            "klicks": 0,
            "position": 9.0,
        },
    ]
    struktur = em.seiten_struktur(HTML_A, SEITE_A)
    antwort = {
        "faq": [
            {
                "frage": "Wie funktioniert der Ablauf?",
                "antwort": em.PLATZHALTER,
                "suchbegriff": lang[:80],
            },
            {
                "frage": "Was kostet die Software?",
                "antwort": "Die Software kostet 49 Euro im Monat.",
                "suchbegriff": "was kostet campingplatz software",
            },
        ]
    }
    aufgaben = {"faq": {"fragen": zeilen}}
    liste, verworfen = em.empfehlungen_aus_ki(
        antwort, "p", struktur, zeilen, aufgaben, [], {}, []
    )
    # gekuerzte Abschrift passt nicht exakt -> verworfen statt Absturz; die echte bleibt
    assert any("keine echte Suchanfrage" in v for v in verworfen)
    assert liste == [] or all(
        f["suchbegriff"] in (lang, zeilen[1]["query"])
        for f in liste[0].vorschlag["fragen"]
    )


def test_nicht_mehr_zutreffende_empfehlung_wird_ersetzt(tmp_path):
    db = str(tmp_path / "t.db")
    alt = em.Empfehlung("p", SEITE_A, em.ART_FAQ, "FAQ", "t", {}, cache_schluessel="a")
    fest = em.Empfehlung(
        "p", SEITE_A, em.ART_ABSCHNITT, "A", "t", {}, cache_schluessel="a"
    )
    em.speichern(db, [alt, fest])
    em.status_setzen(db, fest.id, em.STATUS_FREIGEGEBEN)
    assert em.veraltete_schliessen(db, "p", SEITE_A, set()) == 1
    status = {e.art: e.status for e in em.laden(db, "p")}
    assert status == {
        em.ART_FAQ: em.STATUS_ERSETZT,
        em.ART_ABSCHNITT: em.STATUS_FREIGEGEBEN,
    }


def test_metasprache_die_seite_nennt():
    ok, _ = em.pruefe_text(
        "Ja, die Seite nennt passende Ansätze für Bau.", "Bau Ansätze", {}, None
    )
    assert not ok


def test_bindestrich_zusammensetzung_aus_belegten_teilen_ist_ok():
    quelle = "Mitarbeiterbenefits und Konzepte für Unternehmen."
    assert (
        em.unbelegte_begriffe("Wir bieten Mitarbeiterbenefit-Konzepte an.", quelle)
        == []
    )
    assert em.unbelegte_begriffe("Wir bieten Erstvermietung-Konzepte an.", quelle) == [
        "Erstvermietung-Konzepte"
    ]
