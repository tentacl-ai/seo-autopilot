"""Tests für das Änderungsbuch.

Das Buch ist die Grundlage jeder späteren Wirkungsmessung, und dafür muss es
zwei Dinge gleichzeitig leisten:

  1. **Lückenlos** — jede angewendete Änderung mit Vorher, Nachher und
     Begründung wiederfindbar, auch Wochen später.
  2. **Ehrlich** — was NICHT von uns kam, muss als fremd markiert sein. Sonst
     wandert ein Ranking-Sprung, den der Kunde selbst ausgelöst hat, in unseren
     Erfolgsbericht.

Dazu die harte Betriebsregel aus `learning.py`: Ein kaputter Datenbankpfad
kostet uns einen Buchungssatz, aber niemals die Änderung selbst.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from seo_autopilot.changelog_book import (
    AKTION_META_DESCRIPTION,
    AKTION_META_TITLE,
    AKTION_SCHEMA,
    AKTION_UNBEKANNT,
    STATUS_FEHLGESCHLAGEN,
    STATUS_ZURUECKGENOMMEN,
    TABELLE,
    URHEBER_AUTOPILOT,
    URHEBER_MENSCH,
    URHEBER_UNBEKANNT,
    Aenderung,
    aenderungen,
    aktion_fuer,
    als_text,
    diff_text,
    erkenne_fremde_aenderungen,
    letzter_stand,
    markiere_zurueckgenommen,
    notiere_aenderung,
    protokolliere_fremde_aenderungen,
    tabelle_anlegen,
    vorher_nachher_aus_diff,
)

JETZT = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "buch.db")


@pytest.fixture
def kaputte_db(tmp_path):
    """Eine Datei, die aussieht wie eine DB, aber keine ist."""
    pfad = tmp_path / "kaputt.db"
    pfad.write_bytes(b"das ist keine sqlite-datei, sondern muell" * 20)
    return str(pfad)


def _tabellen(pfad):
    con = sqlite3.connect(pfad)
    try:
        return {
            r[0]
            for r in con.execute("select name from sqlite_master where type='table'")
        }
    finally:
        con.close()


def seite(url, titel=None, beschreibung=None):
    return {"url": url, "title": titel, "meta_description": beschreibung}


# ---------------------------------------------------------------------------
# Tabelle
# ---------------------------------------------------------------------------


class TestTabelle:
    def test_tabelle_wird_angelegt(self, db):
        assert tabelle_anlegen(db) is True
        assert TABELLE in _tabellen(db)

    def test_anlegen_ist_idempotent(self, db):
        """Dreimal anlegen darf nicht knallen — es gibt bewusst kein alembic."""
        assert tabelle_anlegen(db) is True
        assert tabelle_anlegen(db) is True
        assert tabelle_anlegen(db) is True
        assert TABELLE in _tabellen(db)

    def test_idempotent_ohne_datenverlust(self, db):
        """Der zweite Aufruf darf bestehende Eintraege nicht wegwerfen."""
        notiere_aenderung(db, "joseph", AKTION_META_TITLE, nachher="Titel")
        tabelle_anlegen(db)
        assert len(aenderungen(db)) == 1

    def test_alle_pflichtspalten_vorhanden(self, db):
        tabelle_anlegen(db)
        con = sqlite3.connect(db)
        try:
            spalten = {r[1] for r in con.execute(f"pragma table_info({TABELLE})")}
        finally:
            con.close()
        for pflicht in (
            "id",
            "project_id",
            "audit_id",
            "zeitpunkt",
            "urheber",
            "aktion",
            "ziel_url",
            "datei_pfad",
            "vorher",
            "nachher",
            "begruendung",
            "issue_type",
            "git_commit",
            "rueckgaengig_moeglich",
            "rueckgaengig_am",
            "status",
        ):
            assert pflicht in spalten


# ---------------------------------------------------------------------------
# Schreiben und Wiederfinden
# ---------------------------------------------------------------------------


class TestNotieren:
    def test_aenderung_wird_vollstaendig_protokolliert(self, db):
        kennung = notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            audit_id="audit-7",
            ziel_url="https://example.com/preise",
            datei_pfad="dist/index.html",
            vorher="Preise",
            nachher="Preise & Pakete — Beispiel GmbH",
            begruendung="short_title (Quelle: claude)",
            issue_type="short_title",
            git_commit="abc123def456",
            rueckgaengig_moeglich=True,
            zeitpunkt=JETZT,
        )
        assert kennung  # nicht leer

        liste = aenderungen(db, jetzt=JETZT)
        assert len(liste) == 1
        a = liste[0]
        assert isinstance(a, Aenderung)
        assert a.id == kennung
        assert a.project_id == "joseph"
        assert a.audit_id == "audit-7"
        assert a.urheber == URHEBER_AUTOPILOT
        assert a.aktion == AKTION_META_TITLE
        assert a.ziel_url == "https://example.com/preise"
        assert a.datei_pfad == "dist/index.html"
        assert a.vorher == "Preise"
        assert a.nachher == "Preise & Pakete — Beispiel GmbH"
        assert a.begruendung == "short_title (Quelle: claude)"
        assert a.issue_type == "short_title"
        assert a.git_commit == "abc123def456"
        assert a.rueckgaengig_moeglich is True
        assert a.rueckgaengig_am is None
        assert a.wirksam is True

    def test_jede_aenderung_bekommt_eigene_id(self, db):
        a = notiere_aenderung(db, "joseph", AKTION_META_TITLE, nachher="A")
        b = notiere_aenderung(db, "joseph", AKTION_META_TITLE, nachher="B")
        assert a and b and a != b
        assert len(aenderungen(db)) == 2

    def test_unbekannter_urheber_wird_nicht_erfunden(self, db):
        """Ein Tippfehler darf keine vierte Urheber-Kategorie ins Buch bringen."""
        notiere_aenderung(db, "joseph", AKTION_META_TITLE, urheber="hacker")
        assert aenderungen(db)[0].urheber == URHEBER_UNBEKANNT

    def test_leere_aktion_wird_zu_sonstiges(self, db):
        notiere_aenderung(db, "joseph", "")
        assert aenderungen(db)[0].aktion == AKTION_UNBEKANNT

    def test_none_werte_werden_zu_leerem_text(self, db):
        notiere_aenderung(db, "joseph", AKTION_META_TITLE, vorher=None, nachher=None)
        a = aenderungen(db)[0]
        assert a.vorher == "" and a.nachher == ""


# ---------------------------------------------------------------------------
# Robustheit — Protokollieren darf die Aenderung nie kosten
# ---------------------------------------------------------------------------


class TestRobustheit:
    def test_kaputte_db_bricht_protokollieren_nicht_ab(self, kaputte_db):
        """Rueckgabe leerer String, KEINE Ausnahme — sonst stirbt der Fix."""
        kennung = notiere_aenderung(
            kaputte_db, "joseph", AKTION_META_TITLE, nachher="Neu"
        )
        assert kennung == ""

    def test_unerreichbarer_pfad_bricht_nichts_ab(self, tmp_path):
        pfad = str(tmp_path / "gibt" / "es" / "nicht" / "buch.db")
        assert tabelle_anlegen(pfad) is False
        assert notiere_aenderung(pfad, "joseph", AKTION_META_TITLE) == ""
        assert aenderungen(pfad) == []
        assert letzter_stand(pfad, "joseph", AKTION_META_TITLE, "https://x/") is None

    def test_kaputte_db_bricht_lesen_nicht_ab(self, kaputte_db):
        assert aenderungen(kaputte_db) == []

    def test_frische_db_ohne_tabelle_ist_leer(self, tmp_path):
        """Audit-DB existiert, es wurde nur noch nie etwas geaendert."""
        pfad = str(tmp_path / "frisch.db")
        sqlite3.connect(pfad).close()
        assert aenderungen(pfad) == []

    def test_fremderkennung_ueberlebt_kaputte_db(self, kaputte_db):
        funde = erkenne_fremde_aenderungen(
            kaputte_db, "joseph", [seite("https://example.com/", "Titel")]
        )
        assert funde == []

    def test_muell_in_der_seitenliste_wird_uebersprungen(self, db):
        funde = erkenne_fremde_aenderungen(
            db,
            "joseph",
            [None, "kaputt", {}, seite("https://example.com/", "Titel")],
        )
        assert len(funde) == 1
        assert funde[0]["ziel_url"] == "https://example.com/"


# ---------------------------------------------------------------------------
# Filter: Zeitfenster, Projekt, offene Aenderungen
# ---------------------------------------------------------------------------


class TestFilter:
    def test_zeitfenster_greift(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            nachher="alt",
            zeitpunkt=JETZT - timedelta(days=60),
        )
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            nachher="neu",
            zeitpunkt=JETZT - timedelta(days=3),
        )

        letzte_30 = aenderungen(db, tage=30, jetzt=JETZT)
        assert [a.nachher for a in letzte_30] == ["neu"]

        alles = aenderungen(db, tage=365, jetzt=JETZT)
        assert [a.nachher for a in alles] == ["alt", "neu"]

    def test_ohne_zeitfenster_kommt_alles(self, db):
        notiere_aenderung(
            db, "joseph", AKTION_META_TITLE, zeitpunkt=JETZT - timedelta(days=900)
        )
        assert len(aenderungen(db, tage=0, jetzt=JETZT)) == 1

    def test_projektfilter_greift(self, db):
        notiere_aenderung(db, "joseph", AKTION_META_TITLE, nachher="J", zeitpunkt=JETZT)
        notiere_aenderung(db, "topal", AKTION_META_TITLE, nachher="T", zeitpunkt=JETZT)

        nur_joseph = aenderungen(db, project_id="joseph", jetzt=JETZT)
        assert [a.nachher for a in nur_joseph] == ["J"]
        assert len(aenderungen(db, jetzt=JETZT)) == 2

    def test_chronologische_reihenfolge(self, db):
        notiere_aenderung(
            db, "joseph", AKTION_META_TITLE, nachher="zweit", zeitpunkt=JETZT
        )
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            nachher="erst",
            zeitpunkt=JETZT - timedelta(days=2),
        )
        assert [a.nachher for a in aenderungen(db, jetzt=JETZT)] == ["erst", "zweit"]

    def test_nur_offene_blendet_zurueckgenommene_aus(self, db):
        bleibt = notiere_aenderung(
            db, "joseph", AKTION_META_TITLE, nachher="bleibt", zeitpunkt=JETZT
        )
        weg = notiere_aenderung(
            db, "joseph", AKTION_META_DESCRIPTION, nachher="weg", zeitpunkt=JETZT
        )
        assert markiere_zurueckgenommen(db, weg, zeitpunkt=JETZT) is True

        offene = aenderungen(db, nur_offene=True, jetzt=JETZT)
        assert [a.id for a in offene] == [bleibt]
        assert len(aenderungen(db, jetzt=JETZT)) == 2

    def test_nur_offene_blendet_fehlgeschlagene_aus(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_SCHEMA,
            nachher="{}",
            status=STATUS_FEHLGESCHLAGEN,
            zeitpunkt=JETZT,
        )
        assert aenderungen(db, nur_offene=True, jetzt=JETZT) == []
        assert len(aenderungen(db, jetzt=JETZT)) == 1


# ---------------------------------------------------------------------------
# Ruecknahme
# ---------------------------------------------------------------------------


class TestRuecknahme:
    def test_zurueckgenommene_aenderung_ist_erkennbar(self, db):
        kennung = notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            nachher="Neuer Titel",
            rueckgaengig_moeglich=True,
            zeitpunkt=JETZT,
        )
        assert markiere_zurueckgenommen(db, kennung, zeitpunkt=JETZT) is True

        a = aenderungen(db, jetzt=JETZT)[0]
        assert a.status == STATUS_ZURUECKGENOMMEN
        assert a.rueckgaengig_am
        assert a.zurueckgenommen is True
        assert a.wirksam is False

    def test_ruecknahme_loescht_den_eintrag_nicht(self, db):
        """Im Buch wird nie geloescht — sonst fehlt die Erklaerung fuer den Einbruch."""
        kennung = notiere_aenderung(
            db, "joseph", AKTION_META_TITLE, vorher="alt", nachher="neu"
        )
        markiere_zurueckgenommen(db, kennung)
        a = aenderungen(db)[0]
        assert a.vorher == "alt" and a.nachher == "neu"

    def test_ruecknahme_unbekannter_id_meldet_false(self, db):
        tabelle_anlegen(db)
        assert markiere_zurueckgenommen(db, "gibtesnicht") is False

    def test_zurueckgenommene_zaehlt_nicht_als_letzter_stand(self, db):
        """Sonst vergleicht die Fremderkennung gegen einen entfernten Wert."""
        kennung = notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            ziel_url="https://example.com/",
            nachher="Weg",
            zeitpunkt=JETZT,
        )
        markiere_zurueckgenommen(db, kennung, zeitpunkt=JETZT)
        assert (
            letzter_stand(db, "joseph", AKTION_META_TITLE, "https://example.com/")
            is None
        )


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


class TestDiff:
    def test_diff_zeigt_vorher_und_nachher(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            vorher="Startseite",
            nachher="Steuerberatung Wien — Kanzlei Hehenwarter",
        )
        text = diff_text(aenderungen(db)[0])
        assert "-Startseite" in text
        assert "+Steuerberatung Wien — Kanzlei Hehenwarter" in text

    def test_diff_ohne_vorher_zeigt_nur_das_neue(self, db):
        notiere_aenderung(
            db, "joseph", AKTION_META_DESCRIPTION, vorher="", nachher="Ganz neu"
        )
        text = diff_text(aenderungen(db)[0])
        assert "vorher nichts vorhanden" in text
        assert "+ Ganz neu" in text

    def test_diff_ohne_daten_ist_ehrlich(self, db):
        notiere_aenderung(db, "joseph", "robots_txt")
        assert "kein Vorher/Nachher" in diff_text(aenderungen(db)[0])

    def test_diff_bei_gleichem_text(self, db):
        notiere_aenderung(db, "joseph", AKTION_META_TITLE, vorher="X", nachher="X")
        assert "keine Textänderung" in diff_text(aenderungen(db)[0])

    def test_diff_wird_gekuerzt(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_SCHEMA,
            vorher="\n".join(f"alt {i}" for i in range(200)),
            nachher="\n".join(f"neu {i}" for i in range(200)),
        )
        text = diff_text(aenderungen(db)[0], max_zeilen=10)
        assert len(text.splitlines()) <= 11  # 10 Zeilen + Hinweis
        assert "weitere Zeilen" in text

    def test_diff_kuerzt_ueberlange_zeilen(self, db):
        notiere_aenderung(
            db, "joseph", AKTION_SCHEMA, vorher="a" * 5000, nachher="b" * 5000
        )
        for zeile in diff_text(aenderungen(db)[0]).splitlines():
            assert len(zeile) < 400


# ---------------------------------------------------------------------------
# Fremderkennung
# ---------------------------------------------------------------------------


class TestFremderkennung:
    def test_erster_crawl_meldet_niemanden_an(self, db):
        """Ohne Vergleichspunkt ist ein Vorwurf unmoeglich — nur Basis erfassen."""
        funde = erkenne_fremde_aenderungen(
            db, "joseph", [seite("https://example.com/", "Titel", "Beschreibung")]
        )
        assert all(f["urheber"] == URHEBER_UNBEKANNT for f in funde)
        assert {f["aktion"] for f in funde} == {
            AKTION_META_TITLE,
            AKTION_META_DESCRIPTION,
        }

    def test_geaenderter_titel_ohne_eigenen_eintrag_ist_mensch(self, db):
        """Kernfall: Der Kunde hat den Titel selbst umgeschrieben."""
        seiten = [seite("https://example.com/", "Alter Titel")]
        protokolliere_fremde_aenderungen(
            db,
            "joseph",
            "audit-1",
            erkenne_fremde_aenderungen(db, "joseph", seiten),
            zeitpunkt=JETZT - timedelta(days=7),
        )

        funde = erkenne_fremde_aenderungen(
            db, "joseph", [seite("https://example.com/", "Vom Kunden geaendert")]
        )
        fremd = [f for f in funde if f["urheber"] == URHEBER_MENSCH]
        assert len(fremd) == 1
        assert fremd[0]["aktion"] == AKTION_META_TITLE
        assert fremd[0]["vorher"] == "Alter Titel"
        assert fremd[0]["nachher"] == "Vom Kunden geaendert"
        assert "nicht" in fremd[0]["begruendung"].lower()

    def test_eigene_aenderung_wird_nicht_als_fremd_gemeldet(self, db):
        """Was der Autopilot selbst geschrieben hat, ist keine Fremdaenderung."""
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            ziel_url="https://example.com/",
            vorher="Startseite",
            nachher="Steuerberatung Wien — Kanzlei",
            urheber=URHEBER_AUTOPILOT,
            zeitpunkt=JETZT - timedelta(days=1),
        )

        funde = erkenne_fremde_aenderungen(
            db,
            "joseph",
            [seite("https://example.com/", "Steuerberatung Wien — Kanzlei")],
            basis_erfassen=False,
        )
        assert [f for f in funde if f["urheber"] == URHEBER_MENSCH] == []

    def test_fremde_meta_description_wird_erkannt(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_DESCRIPTION,
            ziel_url="https://example.com/",
            nachher="Von uns gesetzt.",
            zeitpunkt=JETZT - timedelta(days=2),
        )
        funde = erkenne_fremde_aenderungen(
            db,
            "joseph",
            [seite("https://example.com/", None, "Vom Kunden ueberschrieben.")],
            basis_erfassen=False,
        )
        assert [f["aktion"] for f in funde] == [AKTION_META_DESCRIPTION]
        assert funde[0]["urheber"] == URHEBER_MENSCH

    def test_fremderkennung_trennt_projekte(self, db):
        """Der Stand von topal darf nie den von joseph erklaeren."""
        notiere_aenderung(
            db,
            "topal",
            AKTION_META_TITLE,
            ziel_url="https://example.com/",
            nachher="Topal-Titel",
            zeitpunkt=JETZT - timedelta(days=1),
        )
        funde = erkenne_fremde_aenderungen(
            db,
            "joseph",
            [seite("https://example.com/", "Ganz anderer Titel")],
            basis_erfassen=False,
        )
        assert funde == []

    def test_leerraum_allein_ist_keine_aenderung(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            ziel_url="https://example.com/",
            nachher="Titel",
            zeitpunkt=JETZT - timedelta(days=1),
        )
        funde = erkenne_fremde_aenderungen(
            db,
            "joseph",
            [seite("https://example.com/", "  Titel  ")],
            basis_erfassen=False,
        )
        assert funde == []

    def test_final_url_schlaegt_url(self, db):
        """Nach einem Redirect zaehlt die Adresse, die wirklich ausgeliefert wird."""
        funde = erkenne_fremde_aenderungen(
            db,
            "joseph",
            [
                {
                    "url": "http://example.com",
                    "final_url": "https://example.com/",
                    "title": "Titel",
                    "meta_description": None,
                }
            ],
        )
        assert [f["ziel_url"] for f in funde] == ["https://example.com/"]

    def test_funde_landen_als_mensch_im_buch(self, db):
        seiten = [seite("https://example.com/", "Alt")]
        protokolliere_fremde_aenderungen(
            db,
            "joseph",
            "audit-1",
            erkenne_fremde_aenderungen(db, "joseph", seiten),
            zeitpunkt=JETZT - timedelta(days=5),
        )
        funde = erkenne_fremde_aenderungen(
            db, "joseph", [seite("https://example.com/", "Neu von Hand")]
        )
        geschrieben = protokolliere_fremde_aenderungen(
            db, "joseph", "audit-2", funde, zeitpunkt=JETZT
        )
        assert geschrieben >= 1

        menschlich = [
            a for a in aenderungen(db, jetzt=JETZT) if a.urheber == URHEBER_MENSCH
        ]
        assert len(menschlich) == 1
        assert menschlich[0].nachher == "Neu von Hand"
        assert menschlich[0].audit_id == "audit-2"


# ---------------------------------------------------------------------------
# Klartext-Ausgabe
# ---------------------------------------------------------------------------


class TestAlsText:
    def test_leeres_buch_sagt_das_auch(self, db):
        assert "Keine protokollierten Änderungen" in als_text(aenderungen(db), tage=30)

    def test_text_nennt_urheber_und_ziel(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            ziel_url="https://example.com/preise",
            nachher="Neuer Titel",
            begruendung="short_title (Quelle: claude)",
            git_commit="abc123",
            zeitpunkt=JETZT,
        )
        text = als_text(aenderungen(db, jetzt=JETZT))
        assert "[joseph]" in text
        assert "Autopilot" in text
        assert "meta_title" in text
        assert "https://example.com/preise" in text
        assert "short_title (Quelle: claude)" in text
        assert "abc123" in text

    def test_text_warnt_bei_fremden_aenderungen(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            urheber=URHEBER_MENSCH,
            ziel_url="https://example.com/",
            nachher="Von Hand",
            zeitpunkt=JETZT,
        )
        text = als_text(aenderungen(db, jetzt=JETZT))
        assert "Mensch" in text
        assert "NICHT vom Autopilot" in text

    def test_text_markiert_zurueckgenommene(self, db):
        kennung = notiere_aenderung(
            db, "joseph", AKTION_META_TITLE, nachher="X", zeitpunkt=JETZT
        )
        markiere_zurueckgenommen(db, kennung, zeitpunkt=JETZT)
        assert "zurueckgenommen" in als_text(aenderungen(db, jetzt=JETZT))

    def test_text_mit_diff_zeigt_den_vergleich(self, db):
        notiere_aenderung(
            db,
            "joseph",
            AKTION_META_TITLE,
            vorher="Alt",
            nachher="Neu",
            zeitpunkt=JETZT,
        )
        text = als_text(aenderungen(db, jetzt=JETZT), mit_diff=True)
        assert "-Alt" in text and "+Neu" in text


# ---------------------------------------------------------------------------
# Kleine Helfer
# ---------------------------------------------------------------------------


class TestHelfer:
    def test_aktion_je_issue_type(self):
        assert aktion_fuer("short_title") == AKTION_META_TITLE
        assert aktion_fuer("long_meta_description") == AKTION_META_DESCRIPTION
        assert aktion_fuer("missing_organization_schema") == AKTION_SCHEMA

    def test_unbekannter_issue_type_verschwindet_nicht(self):
        assert aktion_fuer("voellig_neuer_typ") == AKTION_UNBEKANNT
        assert aktion_fuer(None) == AKTION_UNBEKANNT

    def test_vorher_nachher_aus_git_diff(self):
        diff = (
            "diff --git a/index.html b/index.html\n"
            "--- a/index.html\n"
            "+++ b/index.html\n"
            "@@ -3,1 +3,1 @@\n"
            "-  <title>Alt</title>\n"
            "+  <title>Neu</title>\n"
            '   <meta charset="utf-8">\n'
        )
        vorher, nachher = vorher_nachher_aus_diff(diff)
        assert vorher == "<title>Alt</title>"
        assert nachher == "<title>Neu</title>"

    def test_platzhalter_diff_liefert_nichts(self):
        assert vorher_nachher_aus_diff("(no git repo at root)") == ("", "")
        assert vorher_nachher_aus_diff("") == ("", "")

    def test_letzter_stand_nimmt_den_juengsten(self, db):
        for tag, wert in ((5, "erst"), (3, "dann"), (1, "zuletzt")):
            notiere_aenderung(
                db,
                "joseph",
                AKTION_META_TITLE,
                ziel_url="https://example.com/",
                nachher=wert,
                zeitpunkt=JETZT - timedelta(days=tag),
            )
        stand = letzter_stand(db, "joseph", AKTION_META_TITLE, "https://example.com/")
        assert stand is not None and stand.nachher == "zuletzt"
