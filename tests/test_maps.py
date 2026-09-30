"""Local SEO ueber die Places API (ohne Netz: Google im Test nachgebaut)."""

from datetime import date

import httpx
import pytest

from seo_autopilot import maps

WIR = "ChIJwir"


def falsches_google(treffer_seiten=None, sterne=4.8, bewertungen=12, fehler_bei=None):
    """Nachbau: Profil + Textsuche mit Seiten; zaehlt die Aufrufe."""
    seiten = treffer_seiten or [["a", "b", WIR]]
    aufrufe = []

    def abruf(methode, pfad, body, felder):
        aufrufe.append((methode, pfad, body, felder))
        if fehler_bei and fehler_bei in pfad:
            raise RuntimeError("Places API: HTTP 403 PERMISSION_DENIED")
        if methode == "GET":
            return {
                "id": WIR,
                "displayName": {"text": "Beispiel <GmbH>"},
                "formattedAddress": "Hauptstr. 1, 12345 Ort",
                "nationalPhoneNumber": "0123 456",
                "websiteUri": "https://www.beispiel.de/",
                "rating": sterne,
                "userRatingCount": bewertungen,
                "businessStatus": "OPERATIONAL",
                "googleMapsUri": "https://maps.google.com/?cid=1",
                "location": {"latitude": 48.1, "longitude": 12.5},
                "primaryTypeDisplayName": {"text": "Berater"},
            }
        nr = int(body.get("pageToken", "0"))
        antwort = {"places": [{"id": i} for i in seiten[nr]]}
        if nr + 1 < len(seiten):
            antwort["nextPageToken"] = str(nr + 1)
        return antwort

    abruf.aufrufe = aufrufe
    return abruf


CFG = {"place_id": WIR, "begriffe": ["Berater", "Makler"], "radius_km": 10}


class TestAbrufe:
    def test_profil_gelesen(self):
        p = maps.profil(WIR, falsches_google())
        assert p["sterne"] == 4.8 and p["bewertungen"] == 12 and p["lat"] == 48.1
        assert p["art"] == "Berater" and p["telefon"] == "0123 456"

    def test_platz_ueber_mehrere_seiten_nur_ids(self):
        g = falsches_google([[f"x{i}" for i in range(20)], ["y1", "y2", WIR]])
        assert maps.platz("Berater", WIR, 48.1, 12.5, abruf=g) == 23
        assert all(
            a[3] == "places.id,nextPageToken" for a in g.aufrufe
        )  # kostenlose SKU
        assert g.aufrufe[0][2]["locationBias"]["circle"]["radius"] == 20000

    def test_nicht_gefunden_und_nie_ueber_60(self):
        seiten = [[f"s{n}-{i}" for i in range(20)] for n in range(5)]
        g = falsches_google(seiten)
        assert maps.platz("Berater", WIR, 48.1, 12.5, abruf=g) is None
        assert len(g.aufrufe) == 3

    def test_schluessel_nie_in_der_fehlermeldung(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "GEHEIM-NUR-TEST")
        gesendet = {}

        def falsch(methode, url, json=None, headers=None, timeout=None):
            gesendet.update(url=url, headers=headers)
            return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})

        monkeypatch.setattr(maps.httpx, "request", falsch)
        with pytest.raises(RuntimeError) as e:
            maps.google_abruf("GET", "places/x?languageCode=de", None, "id")
        assert "GEHEIM" not in str(e.value) and "PERMISSION_DENIED" in str(e.value)
        assert "GEHEIM" not in gesendet["url"]
        assert gesendet["headers"]["X-Goog-Api-Key"] == "GEHEIM-NUR-TEST"


class TestMessen:
    def test_zwei_wochen_mit_vergleich(self, tmp_path):
        db = str(tmp_path / "t.db")
        projekte = {
            "p": {"domain": "https://beispiel.de", "maps": CFG},
            "ohne": {"domain": "https://x.de"},
        }
        g1 = falsches_google([["a", WIR]], bewertungen=10)
        assert maps.messen_und_speichern(db, projekte, date(2026, 9, 21), g1) == {
            "p": "4.8 Sterne, 10 Bewertungen, 2 Plaetze"
        }
        g2 = falsches_google([[WIR]], bewertungen=13)
        maps.messen_und_speichern(db, projekte, date(2026, 9, 28), g2)
        a = maps.auswertung(db, "p", "https://beispiel.de")
        assert a["woche"] == "2026-W40" and a["bewertungen"] == 13
        assert a["bewertungen_davor"] == 10
        assert {
            "begriff": "Berater",
            "ort": "am Standort",
            "platz": 1,
            "davor": 2,
            "neu": False,
        } in a["plaetze"]
        assert a["hinweise"] == []

    def test_schon_gemessen_und_fehler_haelt_andere_nicht_auf(self, tmp_path):
        db = str(tmp_path / "t.db")
        projekte = {
            "p": {"maps": CFG},
            "q": {"maps": {"place_id": "ChIJq", "begriffe": []}},
        }
        g = falsches_google(fehler_bei="ChIJq")
        aus = maps.messen_und_speichern(db, projekte, date(2026, 9, 28), g)
        assert aus["p"].startswith("4.8") and aus["q"].startswith("Fehler: Places API")
        n = len(g.aufrufe)
        assert maps.messen_und_speichern(
            db, {"p": projekte["p"]}, date(2026, 9, 30), g
        ) == {"p": "schon gemessen"}
        assert len(g.aufrufe) == n and maps.auswertung(db, "q") == {}

    def test_weitere_orte(self, tmp_path):
        cfg = {
            **CFG,
            "begriffe": ["Berater"],
            "orte": [
                {"name": "Nachbarort", "lat": 48.5, "lng": 12.9},
                {"name": "kaputt"},
            ],
        }
        m = maps.messen(cfg, falsches_google())
        assert [z["ort"] for z in m["plaetze"]] == ["am Standort", "Nachbarort"]


class TestHinweise:
    def test_was_am_eintrag_fehlt(self):
        p = {"status": "CLOSED_TEMPORARILY", "website": "https://www.naturcoach.at/blog/artikel",
             "telefon": None, "bewertungen": 0, "art": "POI - Ort von Interesse"}  # fmt: skip
        h = " ".join(maps.hinweise(p, "https://naturcoach.at"))
        assert "nicht als geöffnet" in h and "Unterseite" in h and "Telefonnummer" in h
        assert "Weniger als 5" in h and "Kategorie" in h

    def test_fremde_website(self):
        p = {"status": "OPERATIONAL", "website": "https://lovable.app/x", "telefon": "1",
             "bewertungen": 9, "art": "Berater"}  # fmt: skip
        assert maps.hinweise(p, "https://joseph.de") == [
            "Der Eintrag verweist auf lovable.app, nicht auf joseph.de."
        ]


class TestImBericht:
    def test_abschnitt_mit_vergleich_und_escaping(self, tmp_path):
        from seo_autopilot.kundenbericht import _abschnitt_maps, maps_fuer_bericht

        db = str(tmp_path / "t.db")
        projekte = {"p": {"maps": {**CFG, "begriffe": ["<b>Berater</b>"]}}}
        maps.messen_und_speichern(
            db, projekte, date(2026, 9, 21), falsches_google([["a"]], bewertungen=10)
        )
        maps.messen_und_speichern(
            db,
            projekte,
            date(2026, 9, 28),
            falsches_google([["a", WIR]], bewertungen=12),
        )
        html = "".join(
            _abschnitt_maps({"maps": maps_fuer_bericht(db, "p", "https://beispiel.de")})
        )
        assert "4,8 Sterne" in html and "(+2 seit letzter Woche)" in html
        assert "Platz 2" in html and "nicht unter 60" in html
        assert "<b>Berater</b>" not in html and "&lt;b&gt;Berater&lt;/b&gt;" in html

    def test_erste_messung_ohne_bewertungen(self, tmp_path):
        from seo_autopilot.kundenbericht import _abschnitt_maps, maps_fuer_bericht

        db = str(tmp_path / "t.db")
        g = falsches_google([[WIR]], sterne=None, bewertungen=0)
        maps.messen_und_speichern(db, {"p": {"maps": CFG}}, date(2026, 9, 28), g)
        html = "".join(_abschnitt_maps({"maps": maps_fuer_bericht(db, "p")}))
        assert "Noch keine Bewertungen" in html and "Sterne" not in html
        assert "erste Messung" in html

    def test_ohne_maps_kein_abschnitt(self, tmp_path):
        from seo_autopilot.kundenbericht import _abschnitt_maps, maps_fuer_bericht

        assert (
            _abschnitt_maps({"maps": maps_fuer_bericht(str(tmp_path / "t.db"), "p")})
            == []
        )


def test_maps_block_ueberlebt_das_speichern_der_projektliste(tmp_path):
    from seo_autopilot.core.project_manager import ProjectManager

    pfad = tmp_path / "projects.yaml"
    pfad.write_text(
        "projects:\n  p:\n    domain: https://beispiel.de\n    name: P\n"
        "    maps:\n      place_id: ChIJwir\n      begriffe: [Berater]\n",
        encoding="utf-8",
    )
    pm = ProjectManager(str(pfad))
    pm.update_project("p", run_interval_days=3)  # schreibt die Datei neu
    assert ProjectManager(str(pfad)).get_project("p").maps == {
        "place_id": "ChIJwir",
        "begriffe": ["Berater"],
    }
