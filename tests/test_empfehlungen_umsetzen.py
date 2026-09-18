"""Empfehlungen umsetzen (v1.16): Adapter-Methoden idempotent und ohne Raten,
Schutzgelaender (gesperrte Seiten, Platzhalter, Deckel, zweite KI-Pruefung),
Betriebsarten (nur Autopilot schreibt ungefragt) und Wirkungs-Pakete."""

import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from seo_autopilot import empfehlungen as em
from seo_autopilot import empfehlungen_umsetzen as eu
from seo_autopilot.adapters.static_files import StaticFilesAdapter
from seo_autopilot.changelog_book import aenderungen, notiere_aenderung
from seo_autopilot.wirkung import (
    URTEIL_NICHT_ZURECHENBAR,
    URTEIL_VERBESSERT,
    bilanz,
    miss_eine,
)

SEITE = "https://x.de/software/"
BLOG = "https://x.de/blog/"

HTML = """<html lang="de"><head><title>Software für Campingplätze</title></head><body>
<main>
<h1>Buchung für Ihren Platz</h1>
<p>Mit X buchen Ihre Gäste den Stellplatz online. Die Software läuft im Browser.</p>
<h2>Funktionen</h2>
<p>Online-Buchung, Kasse und Meldewesen in einem System. Sie sparen Zeit an der Rezeption.</p>
</main>
<footer>Kontakt</footer></body></html>"""

BLOG_HTML = """<html lang="de"><head><title>Blog</title></head><body><main>
<h1>Neuigkeiten</h1>
<p>Viele Betreiber suchen eine Campingplatz Software mit Meldewesen.</p>
</main></body></html>"""

JA = json.dumps({"hilfreich": "ja", "freigabe": "ja", "gruende": []})
NUR_FUELLUNG = json.dumps(
    {"hilfreich": "nein", "freigabe": "ja", "gruende": ["nur Keyword-Füllung"]}
)
NEIN = json.dumps({"freigabe": "nein", "gruende": ["Aussage nicht belegt"]})


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    for rel, inhalt in (("software/index.html", HTML), ("blog/index.html", BLOG_HTML)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(inhalt, encoding="utf-8")
    (root / "impressum").mkdir()
    (root / "impressum" / "index.html").write_text(HTML, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "t.db")


def _cfg(site, art="autopilot", regeln=None):
    return {
        "domain": "https://x.de",
        "name": "X",
        "adapter_type": "static",
        "betriebsart": art,
        "adapter_config": {
            "root_path": str(site),
            "seo_regeln": regeln or {"verbotene_woerter": ["kostenlos"]},
        },
    }


FRAGEN = [
    {
        "frage": "Wie buchen Gäste den Stellplatz?",
        "antwort": "Ihre Gäste buchen den Stellplatz online im Browser.",
    },
    {
        "frage": "Welche Funktionen gibt es?",
        "antwort": "Online-Buchung, Kasse und Meldewesen in einem System.",
    },
]


def _faq(seite=SEITE, fragen=FRAGEN, prio=10.0, **kw):
    anw = {
        "typ": "empfehlung_faq_ergaenzen",
        "seite": seite,
        "fragen": fragen,
        "html": em.faq_html(fragen),
        "jsonld": em._faq_jsonld(fragen),
    }
    return em.Empfehlung(
        "p",
        seite,
        em.ART_FAQ,
        "FAQ ergänzen",
        "t",
        {"fragen": fragen, "anwendung": anw},
        prioritaet=prio,
        cache_schluessel=uuid.uuid4().hex,
        **kw,
    )


def _ueberschrift(seite=SEITE, prio=5.0):
    anw = {
        "typ": "empfehlung_ueberschrift_verbessern",
        "seite": seite,
        "alt": "Buchung für Ihren Platz",
        "neu": "Campingplatz Software für Buchung und Kasse",
    }
    return em.Empfehlung(
        "p",
        seite,
        em.ART_UEBERSCHRIFT,
        "H1",
        "t",
        {"anwendung": anw},
        prioritaet=prio,
        cache_schluessel=uuid.uuid4().hex,
    )


def _datei(site, rel="software/index.html"):
    return (site / rel).read_text(encoding="utf-8")


def _commits(site):
    return subprocess.run(
        ["git", "-C", str(site), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()


# ---------------------------------------------------------------------------
# Adapter-Methoden
# ---------------------------------------------------------------------------


class TestAdapter:
    def test_faq_block_idempotent_vor_main_ende_mit_jsonld(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        fix = {**_faq().vorschlag["anwendung"], "type": "empfehlung_faq_ergaenzen"}
        r1 = a.apply_fix(fix)
        inhalt = _datei(site)
        assert r1.success and r1.files_changed == ["software/index.html"]
        assert (
            inhalt.index('data-seo-autopilot="faq"')
            < inhalt.index("</main>")
            < inhalt.index("<footer")
        )
        assert '"@type": "FAQPage"' in inhalt.split("</head>")[0]
        r2 = a.apply_fix(fix)
        assert r2.success and r2.files_changed == [] and _datei(site) == inhalt

    def test_platzhalter_geht_nie_live(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        fragen = FRAGEN + [{"frage": "Was kostet es?", "antwort": em.PLATZHALTER}]
        fix = {
            **_faq(fragen=fragen).vorschlag["anwendung"],
            "type": "empfehlung_faq_ergaenzen",
        }
        fix["html"] = em.faq_html(fragen)  # absichtlich mit Platzhalter
        r = a.apply_fix(fix)
        assert r.nicht_behebbar and "Platzhalter" in r.nicht_behebbar
        assert em.PLATZHALTER not in _datei(site)

    def test_vorhandene_faq_und_unklare_einfuegestelle_nicht_raten(self, site):
        (site / "software" / "index.html").write_text(
            HTML.replace(
                "<title>",
                '<script type="application/ld+json">{"@type":"FAQPage"}</script><title>',
            ),
            encoding="utf-8",
        )
        (site / "blog" / "index.html").write_text(
            "<html><body><main>a</main><main>b</main></body></html>", encoding="utf-8"
        )
        # Vorbereitung festhalten — offene Aenderungen haelt der Adapter seit
        # 18.09.2026 fuer die Arbeit einer anderen Sitzung und fasst sie nicht an
        _git(site, "add", ".")
        _git(site, "commit", "-q", "-m", "testvorbereitung")
        a = StaticFilesAdapter({"root_path": str(site)})
        r1 = a.apply_fix(
            {**_faq().vorschlag["anwendung"], "type": "empfehlung_faq_ergaenzen"}
        )
        r2 = a.apply_fix(
            {
                **_faq(seite=BLOG).vorschlag["anwendung"],
                "type": "empfehlung_faq_ergaenzen",
            }
        )
        assert "bereits eine FAQ" in r1.nicht_behebbar
        assert "Einfügestelle" in r2.nicht_behebbar

    def test_ueberschrift_nur_bei_unveraendertem_alttext(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        fix = {
            **_ueberschrift().vorschlag["anwendung"],
            "type": "empfehlung_ueberschrift_verbessern",
        }
        assert a.apply_fix(fix).files_changed == ["software/index.html"]
        assert "<h1>Campingplatz Software für Buchung und Kasse</h1>" in _datei(site)
        assert a.apply_fix(fix).files_changed == []  # idempotent
        fix2 = {**fix, "alt": "Etwas ganz anderes", "neu": "Noch eine H1"}
        assert "veraltet" in a.apply_fix(fix2).nicht_behebbar

    def test_erster_absatz_und_abschnitt(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        alt = "Mit X buchen Ihre Gäste den Stellplatz online. Die Software läuft im Browser."
        neu = "Die Software von X ist eine Campingplatz Software: Ihre Gäste buchen den Stellplatz online im Browser."
        fix = {
            "type": "empfehlung_antwort_zuerst",
            "seite": SEITE,
            "alt": alt,
            "neu": neu,
        }
        assert a.apply_fix(fix).files_changed
        assert a.apply_fix(fix).files_changed == []
        assert f"<p>{neu}</p>" in _datei(site)
        ab = {
            "type": "empfehlung_abschnitt_ergaenzen",
            "seite": SEITE,
            "marker": "abschnitt-meldewesen",
            "html": em.abschnitt_html(
                "Meldewesen",
                "Das Meldewesen ist im System enthalten und spart Zeit an der Rezeption jeden Tag.",
            ),
        }
        assert a.apply_fix(ab).files_changed and a.apply_fix(ab).files_changed == []

    def test_interner_link_idempotent_und_nur_woertlich(self, site):
        a = StaticFilesAdapter({"root_path": str(site)})
        fix = {
            "type": "empfehlung_interne_links",
            "seite": BLOG,
            "ziel": "/software/",
            "muster": em._muster("campingplatz software"),
        }
        assert a.apply_fix(fix).files_changed == ["blog/index.html"]
        assert '<a href="/software/">Campingplatz Software</a>' in _datei(
            site, "blog/index.html"
        )
        assert a.apply_fix(fix).files_changed == []
        fix2 = {**fix, "ziel": "/andere/", "muster": em._muster("hotel software")}
        assert a.apply_fix(fix2).nicht_behebbar


# ---------------------------------------------------------------------------
# Umsetzen mit Schutzgelaendern
# ---------------------------------------------------------------------------


class TestUmsetzen:
    def test_autopilot_schreibt_committet_und_bucht(self, site, db):
        em.speichern(db, [_faq()])
        vorher = _commits(site)
        b = eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: JA)
        assert len(b["geschrieben"]) == 1
        assert int(_commits(site)) == int(vorher) + 1  # ein Commit je Aenderung
        e = em.laden(db, "p")[0]
        assert (
            e.status == em.STATUS_UMGESETZT
            and e.change_id
            and e.geaenderte_seite == SEITE
        )
        buch = aenderungen(db, project_id="p", tage=0)
        assert buch[0].aktion == "text_faq_ergaenzen" and buch[0].ziel_url == SEITE
        # zweiter Lauf: nichts mehr offen, nichts doppelt
        assert (
            eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: JA)["geschrieben"]
            == []
        )

    def test_zweite_pruefung_nein_schreibt_nichts(self, site, db):
        em.speichern(db, [_faq()])
        inhalt = _datei(site)
        b = eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: NEIN)
        assert b["geschrieben"] == [] and _datei(site) == inhalt
        e = em.laden(db, "p")[0]
        assert e.status == em.STATUS_PRUEFUNG_NEIN and "nicht belegt" in e.notiz

    def test_nicht_hilfreich_schreibt_nichts_auch_bei_freigabe_ja(self, site, db):
        em.speichern(db, [_faq()])
        inhalt = _datei(site)
        b = eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: NUR_FUELLUNG)
        assert b["geschrieben"] == [] and _datei(site) == inhalt
        assert "Keyword-Füllung" in em.laden(db, "p")[0].notiz

    def test_pruefauftrag_fragt_nach_nutzen_fuer_menschen(self, site, db):
        em.speichern(db, [_faq()])
        gesehen = []
        eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: gesehen.append(s) or JA)
        assert "hilfreich" in gesehen[0] and "Keyword-Füllung" in gesehen[0]

    def test_unklare_pruefantwort_gilt_als_nein(self, site, db):
        em.speichern(db, [_faq()])
        b = eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: "Ja, sieht gut aus")
        assert b["geschrieben"] == []

    def test_gesperrte_seiten(self, site, db):
        imp = "https://x.de/impressum/"
        em.speichern(db, [_faq(seite=imp), _ueberschrift(prio=1)])
        cfg = _cfg(site, regeln={"gesperrte_seiten": ["/software/"]})
        b = eu.umsetzen("p", cfg, db, fragen=lambda p, s: JA)
        assert b["geschrieben"] == []
        assert "Kontakt" in _datei(
            site, "impressum/index.html"
        ) and "seo-faq" not in _datei(site, "impressum/index.html")
        assert all("gesperrt" in g for _, g in b["nicht_behebbar"])

    def test_platzhalter_im_vorschlag_blockt(self, site, db):
        fragen = FRAGEN + [{"frage": "Was kostet es?", "antwort": em.PLATZHALTER}]
        em.speichern(db, [_faq(fragen=fragen)])
        b = eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: JA)
        assert b["geschrieben"] == [] and "Platzhalter" in b["nicht_behebbar"][0][1]

    def test_erfundene_zahl_blockt_vor_der_ki(self, site, db):
        fragen = [
            FRAGEN[0],
            {
                "frage": "Was kostet es?",
                "antwort": "Die Software kostet 99 Euro im Monat.",
            },
        ]
        em.speichern(db, [_faq(fragen=fragen)])
        aufrufe = []
        b = eu.umsetzen(
            "p", _cfg(site), db, fragen=lambda p, s: aufrufe.append(1) or JA
        )
        assert b["geschrieben"] == [] and aufrufe == []
        assert "Zahl 99" in b["nicht_behebbar"][0][1]

    def test_deckel_drei_je_lauf_und_eine_je_seite_und_woche(self, site, db, tmp_path):
        seiten = []
        for i in range(5):
            rel = f"s{i}/index.html"
            (site / rel).parent.mkdir()
            (site / rel).write_text(HTML, encoding="utf-8")
            seiten.append(f"https://x.de/s{i}/")
        _git(site, "add", ".")
        _git(site, "commit", "-q", "-m", "mehr")
        em.speichern(db, [_faq(seite=s, prio=10 - i) for i, s in enumerate(seiten)])
        em.speichern(db, [_ueberschrift(seite=seiten[0], prio=20)])
        b = eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: JA)
        assert len(b["geschrieben"]) == 3
        assert (
            len({g["seite"] for g in b["geschrieben"]}) == 3
        )  # je Seite hoechstens eine
        # am naechsten Tag: s0 ist noch gesperrt (Woche), die anderen duerfen
        morgen = datetime.now(timezone.utc) + timedelta(days=1)
        b2 = eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: JA, jetzt=morgen)
        assert all(
            g["seite"] not in {x["seite"] for x in b["geschrieben"]}
            for g in b2["geschrieben"]
        )
        assert any("Woche" in grund for _, grund in b2["uebersprungen"])

    def test_copilot_nur_nach_freigabe(self, site, db):
        e = _faq()
        em.speichern(db, [e])
        inhalt = _datei(site)
        b = eu.umsetzen("p", _cfg(site, art="copilot"), db, fragen=lambda p, s: JA)
        assert b["geschrieben"] == [] and _datei(site) == inhalt
        assert em.laden(db, "p")[0].status == em.STATUS_OFFEN
        em.status_setzen(db, e.id, em.STATUS_FREIGEGEBEN)
        b2 = eu.umsetzen("p", _cfg(site, art="copilot"), db, fragen=lambda p, s: JA)
        assert len(b2["geschrieben"]) == 1

    def test_beobachter_und_fremde_website_ohne_dateizugriff(self, site, db):
        e = _faq()
        em.speichern(db, [e])
        assert (
            eu.umsetzen("p", _cfg(site, art="beobachter"), db, fragen=lambda p, s: JA)[
                "geschrieben"
            ]
            == []
        )
        em.status_setzen(db, e.id, em.STATUS_FREIGEGEBEN)
        cfg = {**_cfg(site, art="copilot"), "adapter_type": "generic"}
        b = eu.umsetzen("p", cfg, db, fragen=lambda p, s: JA)
        assert (
            b["geschrieben"] == []
            and em.laden(db, "p")[0].status == em.STATUS_NICHT_BEHEBBAR
        )

    def test_trocken_schreibt_nichts(self, site, db):
        em.speichern(db, [_faq()])
        inhalt = _datei(site)
        b = eu.umsetzen("p", _cfg(site), db, fragen=lambda p, s: JA, trocken=True)
        assert b["trocken"] and _datei(site) == inhalt


# ---------------------------------------------------------------------------
# Wirkung: Aenderungspaket (E)
# ---------------------------------------------------------------------------


class TestWirkungPaket:
    def _zwei_am_selben_tag(self, db, tage_her=40, url=SEITE):
        t = datetime.now(timezone.utc) - timedelta(days=tage_her)
        notiere_aenderung(db, "p", "meta_title", ziel_url=url, zeitpunkt=t)
        notiere_aenderung(
            db,
            "p",
            "meta_description",
            ziel_url=url,
            zeitpunkt=t + timedelta(minutes=1),
        )
        return aenderungen(db, project_id="p", tage=0)

    @staticmethod
    async def _hole(url, von, bis):
        vorher = von < (datetime.now(timezone.utc) - timedelta(days=40)).date()
        return {
            "clicks": 5,
            "impressions": 100 if vorher else 200,
            "position": 12.0 if vorher else 8.0,
        }

    def test_titel_und_beschreibung_am_selben_tag_sind_ein_paket(self, db):
        import asyncio

        alle = self._zwei_am_selben_tag(db)
        m = asyncio.run(miss_eine(db, alle[0], 14, self._hole, alle_aenderungen=alle))
        assert m.urteil == URTEIL_VERBESSERT
        assert "Änderungspaket: 2 Änderungen" in m.notiz
        asyncio.run(miss_eine(db, alle[1], 14, self._hole, alle_aenderungen=alle))
        zeile = [z for z in bilanz(db, project_id="p") if "+" in z["aktion"]]
        assert (
            zeile
            and zeile[0]["aktion"] == "meta_description+meta_title"
            and zeile[0]["gemessen"] == 1
        )

    def test_aenderung_an_anderem_tag_bleibt_nicht_zurechenbar(self, db):
        import asyncio

        alle = self._zwei_am_selben_tag(db)
        notiere_aenderung(
            db,
            "p",
            "schema",
            ziel_url=SEITE,
            zeitpunkt=datetime.now(timezone.utc) - timedelta(days=35),
        )
        alle = aenderungen(db, project_id="p", tage=0)
        m = asyncio.run(miss_eine(db, alle[0], 14, self._hole, alle_aenderungen=alle))
        assert m.urteil == URTEIL_NICHT_ZURECHENBAR


def test_startseite_ohne_schraegstrich_wird_fuer_gsc_ergaenzt():
    from seo_autopilot.wirkung import gsc_adresse

    assert (
        gsc_adresse("https://beratung-beispiel.de") == "https://beratung-beispiel.de/"
    )
    assert (
        gsc_adresse("https://beratung-beispiel.de/ueber-mich")
        == "https://beratung-beispiel.de/ueber-mich"
    )
    assert gsc_adresse("https://x.de/") == "https://x.de/"


def test_einfuegestelle_vor_einzigem_footer_sonst_keine():
    seite = "<body><section>a</section><footer>f</footer></body>"
    assert StaticFilesAdapter.einfuegestelle(seite) == seite.index("<footer")
    assert (
        StaticFilesAdapter.einfuegestelle("<body><section>a</section></body>") is None
    )
    assert (
        StaticFilesAdapter.einfuegestelle("<footer></footer><footer></footer>") is None
    )
