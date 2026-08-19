"""
Tests der Search-Console-Langzeithistorie.

Schwerpunkt liegt auf den Sperren, nicht auf dem Normalfall. Eine Zeitreihe ist
nur so viel wert wie ihre schlechteste Zeile: Ein einziger als "0 Klicks"
gespeicherter Abfragefehler erzeugt einen Einbruch, den es nie gab — und der
Autopilot sucht anschliessend nach dessen Ursache oder meldet ihn dem Kunden.

Deshalb wird hier vor allem geprueft, dass GAR NICHTS gespeichert wird, wo die
Datenlage unklar ist, und dass unvollstaendige Monate aus jedem Vergleich
herausfallen.
"""

import csv
import sqlite3
import uuid
from datetime import date
from pathlib import Path

import pytest

from seo_autopilot.historie import (
    MAX_MONATE,
    MIN_IMPRESSIONEN_VERGLEICH,
    NACHZIEHFRIST_TAGE,
    TABELLE,
    Veraenderung,
    _ist_offen,
    bericht_text,
    exportiere_csv,
    importiere,
    jahresvergleich,
    monatsgrenzen,
    monatsliste,
    monatsreihe,
    gsc_konfiguration,
    vergleich,
    vorhandene_monate,
)

# --------------------------------------------------------------------------
# Hilfsmittel
# --------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / f"test_{uuid.uuid4().hex}.db")


def projekt_mit_gsc():
    return {
        "enabled_sources": ["gsc"],
        "source_config": {
            "gsc": {
                "property_url": "sc-domain:example.de",
                "credentials_path": "/dev/null",
            }
        },
    }


class FakeGSC:
    """Search Console als Attrappe.

    `antworten` bildet (dimension_schluessel) auf Rueckgabewerte ab. `None` steht
    fuer einen Abfragefehler — genau die Unterscheidung, um die es hier geht.
    """

    def __init__(self, gesamt=None, begriffe=None, seiten=None, je_monat=None):
        self.gesamt = (
            gesamt
            if gesamt is not None
            else [{"clicks": 10, "impressions": 100, "ctr": 0.1, "position": 5.0}]
        )
        self.begriffe = (
            begriffe
            if begriffe is not None
            else [
                {
                    "keys": ["campingplatz bodensee"],
                    "clicks": 7,
                    "impressions": 70,
                    "ctr": 0.1,
                    "position": 4.0,
                }
            ]
        )
        self.seiten = (
            seiten
            if seiten is not None
            else [
                {
                    "keys": ["https://example.de/"],
                    "clicks": 6,
                    "impressions": 60,
                    "ctr": 0.1,
                    "position": 3.0,
                }
            ]
        )
        self.je_monat = je_monat or {}
        self.aufrufe = []

    async def authenticate(self):
        return True

    async def pull_range(self, property_url, von, bis, dimensions=None, row_limit=5000):
        self.aufrufe.append((von, bis, tuple(dimensions or ())))
        monat = f"{von.year:04d}-{von.month:02d}"
        if monat in self.je_monat:
            return self.je_monat[monat].get(tuple(dimensions or ()), [])
        if not dimensions:
            return self.gesamt
        if dimensions == ["query"]:
            return self.begriffe
        return self.seiten


def zeilen(db_pfad, project_id="testprojekt"):
    """Alle archivierten Zeilen. Fehlt die Tabelle, wurde nie etwas geschrieben."""
    conn = sqlite3.connect(db_pfad)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            f"select * from {TABELLE} where project_id = ? order by monat, dimension",
            (project_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def lege_monat_an(
    db_pfad,
    project_id,
    monat,
    klicks=10,
    impressionen=100,
    vollstaendig=True,
    dimension="gesamt",
    wert="",
    position=5.0,
):
    from seo_autopilot.historie import _verbinde

    with _verbinde(db_pfad) as conn:
        conn.execute(
            f"insert or replace into {TABELLE} (project_id, property_url, monat, "
            f"dimension, wert, klicks, impressionen, ctr, position, vollstaendig, "
            f"abgerufen_am) values (?, 'sc-domain:example.de', ?, ?, ?, ?, ?, 1.0, ?, ?, '2026-08-19')",
            (
                project_id,
                monat,
                dimension,
                wert,
                klicks,
                impressionen,
                position,
                1 if vollstaendig else 0,
            ),
        )


# --------------------------------------------------------------------------
# Monatsrechnung
# --------------------------------------------------------------------------


def test_monatsgrenzen_normal():
    assert monatsgrenzen("2026-08") == (date(2026, 8, 1), date(2026, 8, 31))


def test_monatsgrenzen_dezember_springt_ins_folgejahr():
    assert monatsgrenzen("2025-12") == (date(2025, 12, 1), date(2025, 12, 31))


def test_monatsgrenzen_schaltjahr():
    assert monatsgrenzen("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))


def test_monatsliste_aelteste_zuerst_und_ueber_jahreswechsel():
    liste = monatsliste(date(2026, 2, 15), 4)
    assert liste == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_monatsliste_kappt_bei_16_monaten():
    """Was Google nicht mehr herausgibt, wird gar nicht erst abgefragt."""
    liste = monatsliste(date(2026, 8, 19), 36)
    assert len(liste) == MAX_MONATE


def test_laufender_monat_gilt_als_offen():
    assert _ist_offen("2026-08", date(2026, 8, 19)) is True


def test_frisch_abgeschlossener_monat_gilt_als_offen():
    """Die Search Console liefert rund drei Tage verspaetet nach."""
    assert _ist_offen("2026-07", date(2026, 8, 2)) is True


def test_laengst_abgeschlossener_monat_ist_endgueltig():
    assert _ist_offen("2026-05", date(2026, 8, 19)) is False
    assert _ist_offen("2026-07", date(2026, 8, 19)) is False


# --------------------------------------------------------------------------
# Import — Normalfall
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_legt_gesamt_begriff_und_seite_ab(db):
    ergebnis = await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=2,
        heute=date(2026, 8, 19),
        quelle=FakeGSC(),
    )
    assert ergebnis.erfolgreich
    assert ergebnis.monate_geholt == 2
    dimensionen = {z["dimension"] for z in zeilen(db)}
    assert dimensionen == {"gesamt", "begriff", "seite"}


@pytest.mark.asyncio
async def test_import_rechnet_ctr_in_prozent_um(db):
    await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=1,
        heute=date(2026, 8, 19),
        quelle=FakeGSC(),
    )
    gesamt = [z for z in zeilen(db) if z["dimension"] == "gesamt"][0]
    assert gesamt["ctr"] == 10.0  # 0.1 aus der API


@pytest.mark.asyncio
async def test_leeres_ergebnis_ist_eine_tatsache_kein_fehler(db):
    """Kein Traffic ist eine Aussage — sie gehoert mit Nullen ins Archiv."""
    quelle = FakeGSC(gesamt=[], begriffe=[], seiten=[])
    ergebnis = await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=1,
        heute=date(2026, 8, 19),
        quelle=quelle,
    )
    assert ergebnis.monate_geholt == 1
    gesamt = [z for z in zeilen(db) if z["dimension"] == "gesamt"]
    assert len(gesamt) == 1
    assert gesamt[0]["klicks"] == 0


# --------------------------------------------------------------------------
# SPERRE 1 — Abfragefehler wird nie als Null gespeichert
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abfragefehler_speichert_nichts(db):
    """Der wichtigste Test: ein API-Fehler darf keinen Einbruch erfinden."""
    quelle = FakeGSC()
    quelle.je_monat = {"2026-08": {(): None}}

    ergebnis = await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=1,
        heute=date(2026, 8, 19),
        quelle=quelle,
    )

    assert ergebnis.monate_fehlgeschlagen == 1
    assert ergebnis.monate_geholt == 0
    assert zeilen(db) == []


@pytest.mark.asyncio
async def test_teilfehler_speichert_keinen_halben_monat(db):
    """Gesamtwerte da, Suchbegriffe kaputt: dann lieber gar nichts."""
    quelle = FakeGSC()
    quelle.je_monat = {
        "2026-08": {
            (): [{"clicks": 10, "impressions": 100, "ctr": 0.1, "position": 5.0}],
            ("query",): None,
            ("page",): [],
        }
    }

    ergebnis = await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=1,
        heute=date(2026, 8, 19),
        quelle=quelle,
    )

    assert ergebnis.monate_fehlgeschlagen == 1
    assert zeilen(db) == []


@pytest.mark.asyncio
async def test_fehlgeschlagener_monat_bleibt_offen_und_wird_erneut_versucht(db):
    quelle = FakeGSC()
    quelle.je_monat = {"2026-06": {(): None}}
    await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=3,
        heute=date(2026, 8, 19),
        quelle=quelle,
    )
    assert "2026-06" not in vorhandene_monate(db, "testprojekt")

    # Zweiter Lauf, diesmal antwortet Google
    ergebnis = await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=3,
        heute=date(2026, 8, 19),
        quelle=FakeGSC(),
    )
    assert "2026-06" in vorhandene_monate(db, "testprojekt")
    assert ergebnis.monate_geholt >= 1


# --------------------------------------------------------------------------
# SPERRE 2 — laufender Monat ist unvollstaendig
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_laufender_monat_wird_als_unvollstaendig_markiert(db):
    await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=2,
        heute=date(2026, 8, 19),
        quelle=FakeGSC(),
    )
    monate = vorhandene_monate(db, "testprojekt")
    assert monate["2026-08"] is False
    assert monate["2026-07"] is True


@pytest.mark.asyncio
async def test_laufender_monat_endet_heute_nicht_am_monatsletzten(db):
    quelle = FakeGSC()
    await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=1,
        heute=date(2026, 8, 19),
        quelle=quelle,
    )
    _, bis, _ = quelle.aufrufe[0]
    assert bis == date(2026, 8, 19)


def test_unvollstaendiger_monat_faellt_aus_dem_vergleich(db):
    """Sonst sieht jeder angebrochene Monat aus wie ein Absturz."""
    for monat in ("2026-03", "2026-04", "2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat)
    lege_monat_an(
        db, "testprojekt", "2026-08", klicks=1, impressionen=5, vollstaendig=False
    )

    reihe_alle = monatsreihe(db, "testprojekt")
    reihe_voll = monatsreihe(db, "testprojekt", nur_vollstaendige=True)

    assert len(reihe_alle) == 6
    assert len(reihe_voll) == 5
    assert all(m.vollstaendig for m in reihe_voll)


# --------------------------------------------------------------------------
# SPERRE 3 — abgeschlossene Monate werden nicht neu geholt
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archivierter_monat_wird_uebersprungen(db):
    await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=3,
        heute=date(2026, 8, 19),
        quelle=FakeGSC(),
    )

    zweite = FakeGSC()
    ergebnis = await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=3,
        heute=date(2026, 8, 19),
        quelle=zweite,
    )

    # 2026-06 und 2026-07 sind endgueltig, nur der laufende Monat wird geholt
    assert ergebnis.monate_uebersprungen == 2
    assert ergebnis.monate_geholt == 1
    assert all(von.month == 8 for von, _, _ in zweite.aufrufe)


@pytest.mark.asyncio
async def test_alles_neu_erzwingt_neuabruf(db):
    await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=3,
        heute=date(2026, 8, 19),
        quelle=FakeGSC(),
    )
    zweite = FakeGSC()
    ergebnis = await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=3,
        heute=date(2026, 8, 19),
        quelle=zweite,
        alles_neu=True,
    )
    assert ergebnis.monate_geholt == 3
    assert ergebnis.monate_uebersprungen == 0


@pytest.mark.asyncio
async def test_neuabruf_hinterlaesst_keine_karteileichen(db):
    """Faellt ein Suchbegriff aus den Top-Listen, darf er nicht stehenbleiben."""
    erst = FakeGSC(
        begriffe=[
            {
                "keys": ["alter begriff"],
                "clicks": 5,
                "impressions": 50,
                "ctr": 0.1,
                "position": 4.0,
            }
        ]
    )
    await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=1,
        heute=date(2026, 8, 19),
        quelle=erst,
    )

    zweit = FakeGSC(
        begriffe=[
            {
                "keys": ["neuer begriff"],
                "clicks": 5,
                "impressions": 50,
                "ctr": 0.1,
                "position": 4.0,
            }
        ]
    )
    await importiere(
        db,
        "testprojekt",
        projekt_mit_gsc(),
        monate=1,
        heute=date(2026, 8, 19),
        quelle=zweit,
        alles_neu=True,
    )

    begriffe = {z["wert"] for z in zeilen(db) if z["dimension"] == "begriff"}
    assert begriffe == {"neuer begriff"}


# --------------------------------------------------------------------------
# Fehlende Konfiguration
# --------------------------------------------------------------------------


def test_projekt_ohne_gsc_hat_keine_konfiguration():
    assert gsc_konfiguration({"enabled_sources": ["ga4"]}) is None
    assert gsc_konfiguration({"enabled_sources": ["gsc"], "source_config": {}}) is None
    assert (
        gsc_konfiguration(
            {
                "enabled_sources": ["gsc"],
                "source_config": {"gsc": {"property_url": "sc-domain:x.de"}},
            }
        )
        is None
    )


@pytest.mark.asyncio
async def test_import_ohne_gsc_meldet_statt_zu_werfen(db):
    """Ein Projekt ohne Search Console ist kein Absturzgrund — topal ist so eins."""
    ergebnis = await importiere(
        db, "topal", {"enabled_sources": []}, heute=date(2026, 8, 19)
    )
    assert not ergebnis.erfolgreich
    assert "Search Console" in ergebnis.fehler
    assert zeilen(db, "topal") == []


# --------------------------------------------------------------------------
# Vergleich
# --------------------------------------------------------------------------


def test_vergleich_braucht_genug_monate(db):
    for monat in ("2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat)
    assert vergleich(db, "testprojekt", monate=3) == []


def test_vergleich_findet_weggebrochene_begriffe(db):
    for monat in ("2026-02", "2026-03", "2026-04"):
        lege_monat_an(db, "testprojekt", monat)
        lege_monat_an(
            db,
            "testprojekt",
            monat,
            dimension="begriff",
            wert="stellplatz",
            klicks=20,
            impressionen=200,
        )
    for monat in ("2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat)
        lege_monat_an(
            db,
            "testprojekt",
            monat,
            dimension="begriff",
            wert="stellplatz",
            klicks=2,
            impressionen=40,
        )

    ergebnis = vergleich(db, "testprojekt", dimension="begriff", monate=3)
    assert len(ergebnis) == 1
    assert ergebnis[0].wert == "stellplatz"
    assert ergebnis[0].klick_differenz == 6 - 60


def test_zu_wenig_daten_gilt_nicht_als_veraenderung(db):
    """Unter der Mindestmenge ist jede Prozentangabe Zufall."""
    for monat in ("2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat)
        lege_monat_an(
            db,
            "testprojekt",
            monat,
            dimension="begriff",
            wert="randbegriff",
            klicks=1,
            impressionen=3,
        )

    assert vergleich(db, "testprojekt", dimension="begriff", monate=3) == []
    unbeschraenkt = vergleich(
        db, "testprojekt", dimension="begriff", monate=3, nur_belastbare=False
    )
    assert len(unbeschraenkt) == 1


def test_belastbarkeit_haengt_an_der_mindestmenge():
    knapp_drunter = Veraenderung("x", 0, 0, MIN_IMPRESSIONEN_VERGLEICH - 1, 0, 0.0, 0.0)
    genau_drauf = Veraenderung("x", 0, 0, MIN_IMPRESSIONEN_VERGLEICH, 0, 0.0, 0.0)
    assert not knapp_drunter.belastbar
    assert genau_drauf.belastbar


def test_unvollstaendiger_monat_verzerrt_den_vergleich_nicht(db):
    """Der laufende Monat darf nicht als Einbruch in den Vergleich rutschen."""
    for monat in ("2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat)
        lege_monat_an(
            db,
            "testprojekt",
            monat,
            dimension="begriff",
            wert="stellplatz",
            klicks=20,
            impressionen=200,
        )
    lege_monat_an(db, "testprojekt", "2026-08", vollstaendig=False)
    lege_monat_an(
        db,
        "testprojekt",
        "2026-08",
        dimension="begriff",
        wert="stellplatz",
        klicks=1,
        impressionen=10,
        vollstaendig=False,
    )

    ergebnis = vergleich(db, "testprojekt", dimension="begriff", monate=3)
    # Ohne die Sperre wuerde der angebrochene August die letzten drei Monate
    # nach unten ziehen und einen Einbruch melden.
    assert ergebnis == [] or all(e.klick_differenz == 0 for e in ergebnis)


# --------------------------------------------------------------------------
# Vorjahresvergleich
# --------------------------------------------------------------------------


def test_jahresvergleich_ohne_vorjahresdaten_faellt_kein_urteil(db):
    for monat in ("2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat)
    assert jahresvergleich(db, "testprojekt") is None


def test_jahresvergleich_stellt_gleiche_monate_gegenueber(db):
    for monat in ("2025-05", "2025-06", "2025-07"):
        lege_monat_an(db, "testprojekt", monat, klicks=100, impressionen=1000)
    for monat in ("2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat, klicks=150, impressionen=1200)

    jv = jahresvergleich(db, "testprojekt")
    assert jv is not None
    assert jv["vorjahr"]["klicks"] == 300
    assert jv["aktuell"]["klicks"] == 450
    assert jv["zeitraum_vorjahr"] == "2025-05 bis 2025-07"


# --------------------------------------------------------------------------
# Bericht und Export
# --------------------------------------------------------------------------


def test_bericht_ohne_daten_sagt_was_zu_tun_ist(db):
    text = bericht_text(db, "testprojekt")
    assert "noch keine Historie" in text
    assert "--importieren" in text


def test_bericht_markiert_den_laufenden_monat(db):
    lege_monat_an(db, "testprojekt", "2026-07")
    lege_monat_an(db, "testprojekt", "2026-08", vollstaendig=False)
    text = bericht_text(db, "testprojekt")
    assert "laeuft noch" in text


def test_csv_export_ist_excel_tauglich(db, tmp_path):
    lege_monat_an(db, "testprojekt", "2026-07", klicks=42, impressionen=500)
    ziel = tmp_path / "historie.csv"
    anzahl = exportiere_csv(db, str(ziel), project_id="testprojekt")

    assert anzahl == 1
    roh = ziel.read_bytes()
    assert roh.startswith(b"\xef\xbb\xbf")  # BOM fuer Excel
    with ziel.open(encoding="utf-8-sig") as f:
        zeilen_csv = list(csv.reader(f, delimiter=";"))
    assert zeilen_csv[0][0] == "Projekt"
    assert zeilen_csv[1][4] == "42"
    assert "," in zeilen_csv[1][7] or zeilen_csv[1][7] == "0"  # deutsches Dezimalkomma


def test_export_kann_auf_ein_projekt_begrenzt_werden(db, tmp_path):
    lege_monat_an(db, "testprojekt", "2026-07")
    lege_monat_an(db, "anderes", "2026-07")
    ziel = tmp_path / "nur_eins.csv"
    assert exportiere_csv(db, str(ziel), project_id="testprojekt") == 1


# --------------------------------------------------------------------------
# SPERRE 5 — kein Vorjahresvergleich gegen eine Website, die es noch nicht gab
#
# Aufgedeckt beim ersten Live-Import (tentacl.ai, 19.08.2026): Die Domain hat
# erst ab Maerz 2026 Sichtbarkeit, davor zehn echte Nullmonate. Der Bericht
# meldete daraufhin "+732 Einblendungen gegenueber Vorjahr" — rechnerisch
# richtig, als Aussage aber wertlos: Verglichen wurde gegen die Zeit, in der
# es die Seite praktisch nicht gab. Genau solche Zahlen landen sonst in einer
# Kundenmail.
# --------------------------------------------------------------------------


def test_vorjahr_ohne_sichtbarkeit_gilt_nicht_als_zuwachs(db):
    for monat in ("2025-05", "2025-06", "2025-07"):
        lege_monat_an(db, "testprojekt", monat, klicks=0, impressionen=0)
    for monat in ("2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat, klicks=9, impressionen=244)

    jv = jahresvergleich(db, "testprojekt")
    assert jv is not None, "Die Zahlen sollen sichtbar bleiben"
    assert jv["belastbar"] is False
    assert "keine Sichtbarkeit" in jv["hinweis"]


def test_vorjahr_mit_echten_daten_ist_belastbar(db):
    for monat in ("2025-05", "2025-06", "2025-07"):
        lege_monat_an(db, "testprojekt", monat, klicks=80, impressionen=900)
    for monat in ("2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat, klicks=150, impressionen=1200)

    jv = jahresvergleich(db, "testprojekt")
    assert jv["belastbar"] is True
    assert jv["hinweis"] == ""


def test_bericht_nennt_keinen_zuwachs_wenn_das_vorjahr_leer_war(db):
    for monat in ("2025-05", "2025-06", "2025-07"):
        lege_monat_an(db, "testprojekt", monat, klicks=0, impressionen=0)
    for monat in ("2026-05", "2026-06", "2026-07"):
        lege_monat_an(db, "testprojekt", monat, klicks=9, impressionen=244)

    text = bericht_text(db, "testprojekt")
    assert "Veraenderung:" not in text
    assert "keine Sichtbarkeit" in text


def test_bericht_weist_den_beginn_der_sichtbarkeit_aus(db):
    for monat in ("2025-11", "2025-12", "2026-01"):
        lege_monat_an(db, "testprojekt", monat, klicks=0, impressionen=0)
    lege_monat_an(db, "testprojekt", "2026-02", klicks=4, impressionen=27)

    text = bericht_text(db, "testprojekt")
    assert "2026-02" in text
    assert "Sichtbarkeit" in text
