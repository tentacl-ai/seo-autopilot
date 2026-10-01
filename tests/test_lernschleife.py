"""Lernschleife: nur belegte Vorschlaege, Go per Knopf, Waechter meldet Liegengebliebenes."""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from seo_autopilot import lernschleife as ls
from seo_autopilot import marktradar as mr
from seo_autopilot.health import HealthReport, _pruefe_lernschleife
from seo_autopilot.policy_radar import THEMEN

URL_SCHEMA = "https://developers.google.com/search/blog/2026/09/video-creator"
URL_NEWS = "https://example-news.test/ads-tipp"


@pytest.fixture
def db(tmp_path):
    pfad = str(tmp_path / "a.db")
    con = sqlite3.connect(pfad)
    mr.speichern(
        con,
        [
            {
                "url": URL_SCHEMA,
                "titel": "Video structured data gets creator property",
                "was_neu": "Neues Feld creator",
                "themen": ["strukturierte_daten"],
                "relevanz": "hoch",
            },
            {
                "url": URL_NEWS,
                "titel": "Fuenf Tipps fuer Kampagnen",
                "relevanz": "mittel",
            },
        ],
    )
    con.close()
    return pfad


@pytest.fixture
def ordner(tmp_path):
    o = tmp_path / "ent"
    o.mkdir()
    return o


def _ki(antwort):
    aufrufe = []

    def fragen(prompt, **kw):
        aufrufe.append(prompt)
        return json.dumps(antwort)

    fragen.aufrufe = aufrufe
    return fragen


GUT = {
    "url": URL_SCHEMA,
    "bereich": "schema_validation",
    "titel": "Video-Schema: Feld creator",
    "was_aendern": "creator im VideoObject pruefen.",
    "warum": "Google zeigt es an.",
    "aufwand": "klein",
}


def test_alle_radar_bereiche_bekannt():
    for thema in THEMEN:
        for bereich in thema.pruefbereiche:
            assert bereich in ls.BEREICHE, bereich


def test_alle_dateien_existieren():
    for _titel, dateien in ls.BEREICHE.values():
        for d in dateien:
            assert (ls.PAKET / d).exists(), d


def test_radar_zuordnung_kommt_in_den_auftrag(db):
    kand = ls.kandidaten(db)
    assert kand[0]["url"] == URL_SCHEMA  # Radar-Treffer zuerst
    assert kand[0]["pruefbereiche"] == ["schema_validation"]
    assert kand[1]["pruefbereiche"] == []
    text = ls.auftrag(kand)
    assert URL_SCHEMA in text and "schema_validation" in text


def test_erfundene_quelle_und_bereich_fliegen_raus(db):
    kand = ls.kandidaten(db)
    antwort = [
        GUT,
        {**GUT, "url": "https://erfunden.test/x"},
        {**GUT, "bereich": "gibts_nicht"},
        {**GUT, "warum": ""},
        "kein dict",
    ]
    gut, verworfen = ls.pruefen(antwort, kand)
    assert [g["url"] for g in gut] == [URL_SCHEMA]
    assert verworfen == 4
    assert gut[0]["dateien"] == ["analyzers/schema_validation.py"]


def test_hoechstens_drei(db):
    kand = [{"url": f"https://q.test/{i}", "titel": "t"} for i in range(5)]
    antwort = [{**GUT, "url": f"https://q.test/{i}"} for i in range(5)]
    gut, verworfen = ls.pruefen(antwort, kand)
    assert len(gut) == 3 and verworfen == 2


def test_lauf_legt_knopf_an_und_mailt(db, ordner, tmp_path):
    gesendet = []
    erg = ls.lauf(
        db,
        "fehlt.yaml",
        fragen=_ki([GUT]),
        stand_pfad=tmp_path / "s.json",
        ordner=ordner,
        sender=lambda *a: gesendet.append(a) or (True, "ok"),
        heute=date(2026, 10, 5),
    )
    assert [v["id"] for v in erg.neue_vorschlaege] == ["lernschleife-2026-10-05-1"]
    dyn = json.loads((ordner / "entscheidungen_dyn.json").read_text())
    assert "lernschleife-2026-10-05-1" in dyn
    assert erg.mail == "verschickt" and "KW41" in gesendet[0][1]
    assert "/freigabe/e/" in gesendet[0][2]


def test_bewertete_meldungen_kommen_nicht_nochmal(db, ordner, tmp_path):
    ki = _ki([])
    kw = dict(
        stand_pfad=tmp_path / "s.json", ordner=ordner, sender=lambda *a: (True, "")
    )
    ls.lauf(db, "x", fragen=ki, **kw)
    assert ls.kandidaten(db) == []
    erg = ls.lauf(db, "x", fragen=ki, **kw)
    assert erg.kandidaten == 0 and len(ki.aufrufe) == 1  # kein zweiter KI-Aufruf
    assert erg.mail.startswith("nicht nötig")


def test_ki_ausfall_bricht_nicht_ab_und_merkt_nichts(db, ordner, tmp_path):
    def kaputt(*a, **k):
        raise RuntimeError("Abo weg")

    erg = ls.lauf(
        db,
        "x",
        fragen=kaputt,
        stand_pfad=tmp_path / "s.json",
        ordner=ordner,
        sender=lambda *a: (True, ""),
    )
    assert erg.fehler and erg.neue_vorschlaege == []
    assert len(ls.kandidaten(db)) == 2  # naechste Woche wieder dran


def test_go_klick_wird_uebernommen_und_erinnert(db, ordner, tmp_path):
    stand = tmp_path / "s.json"
    kw = dict(stand_pfad=stand, ordner=ordner, sender=lambda *a: (True, ""))
    ls.lauf(db, "x", fragen=_ki([GUT]), heute=date(2026, 10, 5), **kw)
    (ordner / "entscheidungen_antworten.jsonl").write_text(
        json.dumps(
            {
                "zeit": "2026-10-05T09:00",
                "id": "lernschleife-2026-10-05-1",
                "wahl": "ja",
            }
        )
        + "\n"
    )
    erg = ls.lauf(db, "x", fragen=_ki([]), heute=date(2026, 10, 12), **kw)
    assert [v["status"] for v in erg.go_offen] == ["go"]
    assert erg.mail == "verschickt"
    assert ls.erledigt("lernschleife-2026-10-05-1", stand_pfad=stand)
    assert ls.stand_laden(stand)["vorschlaege"][0]["status"] == "erledigt"


def test_mail_gesperrt_ohne_zulaessigen_empfaenger():
    ok, info = ls.mail_senden("robert@beispiel.de", "b", "t", "<p>h</p>")
    assert not ok and "gesperrt" in info


def test_empfaenger_aus_projects_yaml(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("projects: {}\nlernschleife:\n  empfaenger: chef@firma.de\n")
    assert ls.empfaenger(str(p)) == "chef@firma.de"
    assert ls.empfaenger(str(tmp_path / "fehlt.yaml")) == ""


class TestWaechter:
    JETZT = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)

    def _stand(self, tmp_path, **daten):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"vorschlaege": [], **daten}))
        return p

    def _pruefe(
        self, stand, crontab="... seo_autopilot.cli.main lernschleife --senden"
    ):
        r = HealthReport()
        _pruefe_lernschleife(crontab, self.JETZT, r, stand_pfad=stand)
        return [b.titel for b in r.befunde]

    def test_ohne_cron_still(self, tmp_path):
        assert self._pruefe(tmp_path / "fehlt.json", crontab="") == []

    def test_nie_gelaufen_ist_rot(self, tmp_path):
        assert self._pruefe(tmp_path / "fehlt.json") == ["Lernschleife laeuft nicht"]

    def test_frischer_lauf_ist_gruen(self, tmp_path):
        s = self._stand(
            tmp_path, letzter_lauf=(self.JETZT - timedelta(days=2)).isoformat()
        )
        assert self._pruefe(s) == []

    def test_fehler_im_letzten_lauf(self, tmp_path):
        s = self._stand(
            tmp_path, letzter_lauf=self.JETZT.isoformat(), fehler=["Abo weg"]
        )
        assert self._pruefe(s) == ["Lernschleife konnte nicht bewerten"]

    def test_liegengebliebenes_go(self, tmp_path):
        v = {
            "id": "x",
            "titel": "Video-Schema",
            "status": "go",
            "entschieden_am": "2026-10-06",
        }
        s = self._stand(tmp_path, letzter_lauf=self.JETZT.isoformat(), vorschlaege=[v])
        befunde = self._pruefe(s)
        assert len(befunde) == 1 and "Video-Schema" in befunde[0]


def test_probelauf_verbraucht_nichts(db, ordner, tmp_path):
    stand = tmp_path / "s.json"
    erg = ls.lauf(
        db,
        "x",
        senden=False,
        fragen=_ki([GUT]),
        stand_pfad=stand,
        ordner=ordner,
        sender=lambda *a: pytest.fail("Probelauf darf nicht mailen"),
    )
    assert len(erg.neue_vorschlaege) == 1 and erg.mail == "Probelauf"
    assert len(ls.kandidaten(db)) == 2 and not stand.exists()
    assert not (ordner / "entscheidungen_dyn.json").exists()
