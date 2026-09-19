"""Kunden-Wochenbericht: Knoepfe, Selbstheilung der Freigaben, keine erfundenen Quellen, Mail-Meldungen."""

import json
import sqlite3
from datetime import date

import pytest

from seo_autopilot import entscheidungen as ent
from seo_autopilot import kundenbericht as kb
from seo_autopilot.ausfuehrung import freigaben, tabelle_anlegen, zur_freigabe
from seo_autopilot.notifications import mail


@pytest.fixture
def ordner(tmp_path):
    o = tmp_path / "content"
    o.mkdir()
    return o


class TestEntscheidungen:
    def test_punkt_bekommt_token_mit_datum(self, ordner):
        urls = ent.punkte_anlegen(
            [{"id": "seo-x-1", "titel": "Titel"}],
            ordner=ordner,
            heute=date(2026, 9, 21),
        )
        token = urls["seo-x-1"].rsplit("/", 1)[1]
        tokens = json.loads((ordner / "entscheidungen_tokens.json").read_text())
        assert tokens[token] == {"id": "seo-x-1", "zuletzt": "2026-09-21"}
        dyn = json.loads((ordner / "entscheidungen_dyn.json").read_text())
        assert (
            dyn["seo-x-1"]["quelle"] == "seo-bericht" and dyn["seo-x-1"]["dyn"] is True
        )

    def test_gleicher_punkt_behaelt_token_und_erstes_datum(self, ordner):
        a = ent.punkte_anlegen(
            [{"id": "p", "titel": "T"}], ordner=ordner, heute=date(2026, 9, 14)
        )
        b = ent.punkte_anlegen(
            [{"id": "p", "titel": "T"}], ordner=ordner, heute=date(2026, 9, 21)
        )
        assert a == b
        dyn = json.loads((ordner / "entscheidungen_dyn.json").read_text())
        assert dyn["p"]["erstmals"] == "2026-09-14" and dyn["p"]["seit"] == "2026-09-21"

    def test_beantworteter_punkt_kommt_nicht_wieder(self, ordner):
        (ordner / "entscheidungen_antworten.jsonl").write_text(
            json.dumps({"id": "p", "wahl": "nein"}) + "\n"
        )
        assert ent.punkte_anlegen([{"id": "p", "titel": "T"}], ordner=ordner) == {}

    def test_alte_string_tokens_werden_weiterverwendet(self, ordner):
        (ordner / "entscheidungen_tokens.json").write_text(json.dumps({"ALT": "p"}))
        assert ent.token_fuer("p", ordner=ordner, heute=date(2026, 9, 21)) == "ALT"

    def test_fehlender_ordner_gibt_keine_knoepfe_statt_absturz(self, tmp_path):
        assert (
            ent.punkte_anlegen(
                [{"id": "p", "titel": "T"}], ordner=tmp_path / "gibtsnicht"
            )
            == {}
        )


class TestImpulse:
    MELDUNGEN = [
        {
            "titel": "Google Ads AI Max",
            "was_neu": "neu",
            "url": "https://blog.google/ai-max",
            "quelle": "blog.google",
            "relevanz": "hoch",
        },
        {
            "titel": "Core Update",
            "was_neu": "neu",
            "url": "https://developers.google.com/u",
            "quelle": "google",
            "relevanz": "hoch",
        },
    ]

    def test_url_kommt_aus_der_meldung_nicht_aus_der_ki(self):
        antwort = '[{"nr": 2, "titel": "Update beobachten", "warum": "w", "vorschlag": "v", "url": "https://erfunden.de"}]'
        aus = kb.impulse({"name": "X"}, {}, self.MELDUNGEN, fragen=lambda a: antwort)
        assert aus == [
            {
                "titel": "Update beobachten",
                "warum": "w",
                "vorschlag": "v",
                "url": "https://developers.google.com/u",
                "quelle": "google",
            }
        ]

    def test_unbekannte_nummer_und_leere_antwort_werden_ignoriert(self):
        antwort = '[{"nr": 99, "titel": "x", "vorschlag": "y"}, {"nr": 1, "titel": "", "vorschlag": "y"}]'
        assert kb.impulse({}, {}, self.MELDUNGEN, fragen=lambda a: antwort) == []

    def test_hoechstens_drei(self):
        antwort = json.dumps(
            [{"nr": 1, "titel": f"t{i}", "vorschlag": "v"} for i in range(6)]
        )
        assert (
            len(kb.impulse({}, {}, self.MELDUNGEN, fragen=lambda a: antwort))
            == kb.MAX_IMPULSE
        )

    def test_ohne_meldungen_keine_ki_frage(self):
        def darf_nicht(a):
            raise AssertionError("keine Frage ohne Meldungen")

        assert kb.impulse({}, {}, [], fragen=darf_nicht) == []


def _audit_db(pfad, projekt, typen):
    con = sqlite3.connect(pfad)
    con.execute(
        "create table seo_issues (audit_id text, project_id text, type text, detected_at text)"
    )
    for t in typen:
        con.execute(
            "insert into seo_issues values ('a1', ?, ?, '2026-09-16T10:00:00')",
            (projekt, t),
        )
    con.commit()
    con.close()


class TestFreigabeSelbstheilung:
    def test_behobener_befund_wird_geschlossen_offener_bleibt(self, tmp_path):
        db = str(tmp_path / "a.db")
        _audit_db(db, "beratung-beispiel", ["image_missing_dimensions"])
        tabelle_anlegen(db)
        zur_freigabe(
            db,
            "beratung-beispiel",
            {
                "type": "image_missing_dimensions",
                "url": "https://j.de/a",
                "title": "Bild",
                "suggestion": "v",
            },
            "copilot",
        )
        zur_freigabe(
            db,
            "beratung-beispiel",
            {
                "type": "missing_contact_page",
                "url": "https://j.de/",
                "title": "Kontakt",
                "suggestion": "v",
            },
            "copilot",
        )

        gruppen = kb.freigabe_gruppen(db, "beratung-beispiel")

        assert [g["typ"] for g in gruppen] == ["image_missing_dimensions"]
        offen = {f.issue_type for f in freigaben(db, project_id="beratung-beispiel")}
        assert offen == {"image_missing_dimensions"}

    def test_ohne_audit_wird_nichts_geschlossen(self, tmp_path):
        db = str(tmp_path / "a.db")
        _audit_db(db, "anderes", ["x"])
        tabelle_anlegen(db)
        zur_freigabe(
            db,
            "beratung-beispiel",
            {
                "type": "missing_contact_page",
                "url": "https://j.de/",
                "title": "Kontakt",
                "suggestion": "v",
            },
            "copilot",
        )
        assert [g["typ"] for g in kb.freigabe_gruppen(db, "beratung-beispiel")] == [
            "missing_contact_page"
        ]


class TestHinweise:
    def test_klickeinbruch_wird_gemeldet(self):
        b = {
            "search_console": {
                "woche": {"klicks": 3},
                "vorwoche": {"klicks": 10},
                "begriffe": [],
            }
        }
        assert any("gefallen" in h for h in kb.hinweise(b))

    def test_kleine_zahlen_loesen_keinen_alarm_aus(self):
        b = {
            "search_console": {
                "woche": {"klicks": 1},
                "vorwoche": {"klicks": 3},
                "begriffe": [],
            }
        }
        assert kb.hinweise(b) == []

    def test_nicht_angebunden_ist_kein_lesefehler(self):
        b = {
            "analytics": {"fehler": "Google Analytics nicht angebunden"},
            "bing": {"fehler": "Timeout: x"},
        }
        assert kb.hinweise(b) == ["Nicht lesbar diese Woche: bing."]

    def test_html_ohne_daten_bricht_nicht(self):
        b = {
            "projekt": "x",
            "name": "X",
            "host": "x.de",
            "stand": "2026-09-21T07:40",
            "hinweise": [],
        }
        assert "X – Wochenbericht" in kb.als_html(b)


class TestMailMeldung:
    @pytest.fixture(autouse=True)
    def gueltige_mailkonfiguration(self, monkeypatch):
        monkeypatch.setattr(mail, "MAILER", "/opt/test-mailer.py")
        monkeypatch.setattr(mail, "EMPFAENGER", "alerts@tentacl.ai")

    def test_gleicher_inhalt_nur_einmal(self, tmp_path, monkeypatch):
        gesendet = []
        monkeypatch.setattr(
            mail.subprocess,
            "run",
            lambda *a, **k: gesendet.append(a) or type("R", (), {"returncode": 0})(),
        )
        assert mail.an_robert("B", "Warnung A", schluessel="s", ordner=tmp_path) is True
        assert (
            mail.an_robert("B", "Warnung A", schluessel="s", ordner=tmp_path) is False
        )
        assert mail.an_robert("B", "Warnung B", schluessel="s", ordner=tmp_path) is True
        assert len(gesendet) == 2

    def test_fehlgeschlagener_versand_wird_nicht_als_gemeldet_gemerkt(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            mail.subprocess,
            "run",
            lambda *a, **k: type("R", (), {"returncode": 1, "stderr": "x"})(),
        )
        assert mail.an_robert("B", "T", schluessel="s", ordner=tmp_path) is False
        assert not mail.schon_gemeldet("s", "T", ordner=tmp_path)

    @pytest.mark.parametrize(
        "adresse",
        ["", "ungueltig", "empfaenger@beispiel.de", "test@example.com", "x@y.invalid"],
    )
    def test_unconfigured_or_placeholder_recipient_fails_closed(
        self, adresse, tmp_path, monkeypatch
    ):
        aufrufe = []
        monkeypatch.setattr(mail, "EMPFAENGER", adresse)
        monkeypatch.setattr(
            mail.subprocess,
            "run",
            lambda *a, **k: aufrufe.append(a) or type("R", (), {"returncode": 0})(),
        )

        assert mail.an_robert("B", "T", ordner=tmp_path) is False
        assert aufrufe == []
