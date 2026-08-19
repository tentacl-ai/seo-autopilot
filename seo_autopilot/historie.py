"""
Langzeit-Historie aus der Google Search Console.

Bisher kannte der Autopilot die Suchdaten nur als Momentaufnahme: `pull_analytics`
holt rollierend die letzten 28 Tage, `pull_url_window` genau eine Adresse fuer ein
Messfenster. Beides beantwortet nicht die Frage, die bei jedem Kunden zuerst kommt:
**"Wie lief das eigentlich die letzten anderthalb Jahre?"** — Saisonalitaet,
weggebrochene Suchbegriffe, Seiten die still verschwunden sind.

Dieses Modul holt die Zeitreihe **monatsweise** und legt sie in `gsc_historie` ab:

    from seo_autopilot.historie import importiere, monatsreihe, bericht_text

    await importiere("seo_autopilot.db", "tentacl-ai", projekt, monate=16)
    for m in monatsreihe("seo_autopilot.db", "tentacl-ai"):
        print(m.monat, m.klicks, m.impressionen)

## Die 16-Monats-Grenze

Google gibt in der Search Console **maximal 16 Monate** heraus, aeltere Zeitraeume
liefern schlicht leere Antworten. Wer heute nicht importiert, hat die Daten von vor
17 Monaten fuer immer verloren. Genau deshalb ist der Import als **Archiv** gebaut:
einmal geholt, bleibt ein Monat in der eigenen Datenbank stehen, auch wenn Google
ihn Jahre spaeter nicht mehr kennt.

## Drei bewusste Sperren

1. **Abfragefehler wird NIE als Null gespeichert.** Liefert die API einen Fehler,
   entsteht kein Eintrag — der Monat bleibt schlicht offen und wird beim naechsten
   Lauf erneut versucht. Wer einen Netzwerkfehler als "0 Klicks" in eine Zeitreihe
   schreibt, erzeugt einen Einbruch, den es nie gab, und der Autopilot wuerde
   anschliessend nach dessen Ursache suchen. (Dieselbe Regel wie in `wirkung.py`.)
2. **Der laufende Monat gilt als unvollstaendig.** Er wird importiert, aber
   ausdruecklich als `vollstaendig=0` markiert und aus jedem Vergleich
   herausgehalten. Ein am 3. des Monats gemessener Monat sieht sonst immer aus wie
   ein Absturz. Dazu kommt: Die Search Console hinkt **rund drei Tage** hinterher,
   selbst der Vormonat ist am 1. noch nicht final — deshalb wird der jeweils letzte
   abgeschlossene Monat bei jedem Lauf neu geholt (`NACHZIEHFRIST_TAGE`).
3. **Abgeschlossene Monate werden nicht neu geholt.** Sie aendern sich nicht mehr.
   Das spart beim taeglichen Lauf saemtliche Abfragen — nur der laufende und der
   eben abgeschlossene Monat kosten noch etwas.

Kein alembic (dessen `env.py` ist auf async verdrahtet und hinterlaesst halb
migrierte Zustaende) — die Tabelle wird per `CREATE TABLE IF NOT EXISTS` angelegt.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

TABELLE = "gsc_historie"

# Soweit reicht die Search Console zurueck. Aeltere Zeitraeume liefern leer.
MAX_MONATE = 16

# Die Search Console hinkt rund drei Tage hinterher. Innerhalb dieser Frist nach
# Monatsende gilt auch der abgeschlossene Monat noch als nachzuziehen.
NACHZIEHFRIST_TAGE = 5

# Wie viele Suchbegriffe und Seiten je Monat archiviert werden. Alles darueber ist
# Rauschen mit einer Einblendung, kostet aber Speicher in jeder Auswertung.
TOP_BEGRIFFE = 200
TOP_SEITEN = 200

# Ab wie vielen Einblendungen ein Vergleich zweier Zeitraeume ueberhaupt etwas
# aussagt. Darunter ist jede Prozentangabe Zufall.
MIN_IMPRESSIONEN_VERGLEICH = 30

# Ab wie vielen Einblendungen im VORJAHRESZEITRAUM (drei Monate zusammen) ein
# Jahresvergleich etwas ueber Fortschritt aussagt. Darunter gab es die Website
# in der Suche schlicht noch nicht — dann ist jeder Zuwachs ein Rechenergebnis,
# keine Leistung. Aufgedeckt beim ersten Live-Import von tentacl.ai.
MIN_IMPRESSIONEN_JAHRESVERGLEICH = 100

_SCHEMA = f"""
create table if not exists {TABELLE} (
    id integer primary key autoincrement,
    project_id text not null,
    property_url text not null,
    monat text not null,
    dimension text not null,
    wert text not null default '',
    klicks integer not null default 0,
    impressionen integer not null default 0,
    ctr real not null default 0.0,
    position real not null default 0.0,
    vollstaendig integer not null default 1,
    abgerufen_am text not null,
    unique (project_id, monat, dimension, wert)
);
create index if not exists idx_{TABELLE}_projekt_monat
    on {TABELLE} (project_id, monat);
create index if not exists idx_{TABELLE}_dimension
    on {TABELLE} (project_id, dimension, monat);
"""


@dataclass
class Monatswert:
    """Ein Monat einer Zeitreihe (Gesamtwerte des Projekts)."""

    monat: str
    klicks: int
    impressionen: int
    ctr: float
    position: float
    vollstaendig: bool


@dataclass
class Veraenderung:
    """Ein Suchbegriff oder eine Seite im Vergleich zweier Zeitraeume."""

    wert: str
    klicks_vorher: int
    klicks_nachher: int
    impressionen_vorher: int
    impressionen_nachher: int
    position_vorher: float
    position_nachher: float

    @property
    def klick_differenz(self) -> int:
        return self.klicks_nachher - self.klicks_vorher

    @property
    def belastbar(self) -> bool:
        """Genug Datenmenge, damit der Vergleich etwas aussagt."""
        return (
            max(self.impressionen_vorher, self.impressionen_nachher)
            >= MIN_IMPRESSIONEN_VERGLEICH
        )


@dataclass
class Importergebnis:
    """Was ein Importlauf tatsaechlich bewirkt hat."""

    project_id: str
    monate_geholt: int = 0
    monate_uebersprungen: int = 0
    monate_fehlgeschlagen: int = 0
    zeilen: int = 0
    fehler: Optional[str] = None

    @property
    def erfolgreich(self) -> bool:
        return self.fehler is None


# --------------------------------------------------------------------------
# Datenbank
# --------------------------------------------------------------------------


def standard_db_pfad() -> str:
    """Absoluter Pfad zur Audit-Datenbank — nicht vom Arbeitsverzeichnis abhaengig."""
    from .core.config import settings

    url = str(settings.DATABASE_URL)
    if "sqlite" in url and ":///" in url:
        return url.split(":///", 1)[1]
    return str(Path(__file__).resolve().parent.parent / "seo_autopilot.db")


def _verbinde(db_pfad: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_pfad)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------
# Monatsrechnung
# --------------------------------------------------------------------------


def _heute(heute: Optional[date] = None) -> date:
    return heute or datetime.now(timezone.utc).date()


def monatsgrenzen(monat: str) -> Tuple[date, date]:
    """Erster und letzter Tag eines Monats 'YYYY-MM' (beide inklusiv)."""
    jahr, mon = (int(t) for t in monat.split("-"))
    erster = date(jahr, mon, 1)
    if mon == 12:
        naechster = date(jahr + 1, 1, 1)
    else:
        naechster = date(jahr, mon + 1, 1)
    return erster, naechster - timedelta(days=1)


def monatsschluessel(tag: date) -> str:
    return f"{tag.year:04d}-{tag.month:02d}"


def monatsliste(bis: date, monate: int) -> List[str]:
    """Die letzten `monate` Monatsschluessel, aelteste zuerst, inklusive `bis`.

    Wird bei `MAX_MONATE` gekappt: Was Google nicht mehr herausgibt, muss man
    auch nicht abfragen.
    """
    monate = max(1, min(int(monate), MAX_MONATE))
    liste: List[str] = []
    jahr, mon = bis.year, bis.month
    for _ in range(monate):
        liste.append(f"{jahr:04d}-{mon:02d}")
        mon -= 1
        if mon == 0:
            jahr, mon = jahr - 1, 12
    return list(reversed(liste))


def _ist_offen(monat: str, heute: date) -> bool:
    """Laeuft dieser Monat noch — oder ist er so frisch, dass Google nachliefert?

    Beides fuehrt zur selben Behandlung: erneut holen, nicht als endgueltig
    verbuchen.
    """
    if monat == monatsschluessel(heute):
        return True
    _, letzter = monatsgrenzen(monat)
    return (heute - letzter).days <= NACHZIEHFRIST_TAGE


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def vorhandene_monate(db_pfad: str, project_id: str) -> Dict[str, bool]:
    """{Monat: vollstaendig} aller bereits archivierten Monate eines Projekts."""
    try:
        with _verbinde(db_pfad) as conn:
            zeilen = conn.execute(
                f"select monat, vollstaendig from {TABELLE} "
                f"where project_id = ? and dimension = 'gesamt'",
                (project_id,),
            ).fetchall()
        return {z["monat"]: bool(z["vollstaendig"]) for z in zeilen}
    except sqlite3.Error as exc:
        logger.warning(f"[historie] Monate nicht lesbar: {exc}")
        return {}


def _speichere_monat(
    db_pfad: str,
    project_id: str,
    property_url: str,
    monat: str,
    vollstaendig: bool,
    gesamt: Dict[str, Any],
    begriffe: List[Dict[str, Any]],
    seiten: List[Dict[str, Any]],
) -> int:
    """Einen kompletten Monat ablegen. Ersetzt einen bereits vorhandenen Stand."""
    jetzt = datetime.now(timezone.utc).isoformat()
    saetze: List[Tuple] = []

    def satz(dimension: str, wert: str, daten: Dict[str, Any]) -> Tuple:
        return (
            project_id,
            property_url,
            monat,
            dimension,
            wert,
            int(daten.get("clicks", 0) or 0),
            int(daten.get("impressions", 0) or 0),
            round(float(daten.get("ctr", 0.0) or 0.0) * 100, 2),
            round(float(daten.get("position", 0.0) or 0.0), 2),
            1 if vollstaendig else 0,
            jetzt,
        )

    saetze.append(satz("gesamt", "", gesamt))
    for zeile in begriffe[:TOP_BEGRIFFE]:
        schluessel = (zeile.get("keys") or [""])[0]
        saetze.append(satz("begriff", str(schluessel), zeile))
    for zeile in seiten[:TOP_SEITEN]:
        schluessel = (zeile.get("keys") or [""])[0]
        saetze.append(satz("seite", str(schluessel), zeile))

    try:
        with _verbinde(db_pfad) as conn:
            # Alten Stand des Monats raeumen: Faellt ein Suchbegriff aus den
            # Top 200, darf er nicht als Karteileiche stehenbleiben.
            conn.execute(
                f"delete from {TABELLE} where project_id = ? and monat = ?",
                (project_id, monat),
            )
            conn.executemany(
                f"insert into {TABELLE} "
                f"(project_id, property_url, monat, dimension, wert, klicks, "
                f" impressionen, ctr, position, vollstaendig, abgerufen_am) "
                f"values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                saetze,
            )
        return len(saetze)
    except sqlite3.Error as exc:
        logger.warning(f"[historie] {project_id}/{monat} nicht gespeichert: {exc}")
        return 0


def gsc_konfiguration(projekt: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(property_url, credentials_path) — oder None, wenn kein GSC eingerichtet ist.

    Kein Fehler, sondern eine Tatsache ueber das Projekt: sie wird gemeldet,
    nicht geworfen.
    """
    quellen = projekt.get("enabled_sources") or []
    if "gsc" not in quellen:
        return None
    konfig = (projekt.get("source_config") or {}).get("gsc") or {}
    property_url = konfig.get("property_url")
    credentials = konfig.get("credentials_path")
    if not property_url or not credentials:
        return None
    return (str(property_url), str(credentials))


async def importiere(
    db_pfad: str,
    project_id: str,
    projekt: Dict[str, Any],
    monate: int = MAX_MONATE,
    heute: Optional[date] = None,
    quelle: Any = None,
    alles_neu: bool = False,
) -> Importergebnis:
    """Holt die Monatshistorie eines Projekts in die eigene Datenbank.

    Pro Monat drei Abfragen: Gesamtwerte, Top-Suchbegriffe, Top-Seiten. Bereits
    abgeschlossene und archivierte Monate werden uebersprungen (`alles_neu=True`
    erzwingt den Neuabruf).

    `quelle` ist nur fuer Tests da — normal wird die GSC-Anbindung aus der
    Projektkonfiguration gebaut.
    """
    tag = _heute(heute)
    ergebnis = Importergebnis(project_id=project_id)

    konfig = gsc_konfiguration(projekt)
    if not konfig:
        ergebnis.fehler = "keine Search Console konfiguriert"
        return ergebnis
    property_url, credentials = konfig

    if quelle is None:
        from .sources.gsc import GSCDataSource

        try:
            quelle = GSCDataSource(credentials)
            if not await quelle.authenticate():
                ergebnis.fehler = "Search-Console-Anmeldung fehlgeschlagen"
                return ergebnis
        except Exception as exc:
            ergebnis.fehler = f"Search Console nicht nutzbar: {exc}"
            return ergebnis

    bekannt = {} if alles_neu else vorhandene_monate(db_pfad, project_id)

    for monat in monatsliste(tag, monate):
        offen = _ist_offen(monat, tag)
        if monat in bekannt and bekannt[monat] and not offen:
            ergebnis.monate_uebersprungen += 1
            continue

        von, bis = monatsgrenzen(monat)
        # Ein laufender Monat endet heute, nicht am 31.
        if bis > tag:
            bis = tag
        if von > tag:
            continue

        gesamt_zeilen = await quelle.pull_range(property_url, von, bis, None, 1)
        if gesamt_zeilen is None:
            # Abfragefehler: nichts speichern, Monat bleibt offen.
            ergebnis.monate_fehlgeschlagen += 1
            logger.warning(f"[historie] {project_id}/{monat}: Abfrage fehlgeschlagen")
            continue

        begriffe = await quelle.pull_range(
            property_url, von, bis, ["query"], TOP_BEGRIFFE
        )
        seiten = await quelle.pull_range(property_url, von, bis, ["page"], TOP_SEITEN)
        if begriffe is None or seiten is None:
            ergebnis.monate_fehlgeschlagen += 1
            logger.warning(
                f"[historie] {project_id}/{monat}: Teilabfrage fehlgeschlagen — "
                f"Monat wird nicht halb gespeichert"
            )
            continue

        gesamt = gesamt_zeilen[0] if gesamt_zeilen else {}
        zeilen = _speichere_monat(
            db_pfad,
            project_id,
            property_url,
            monat,
            vollstaendig=not offen,
            gesamt=gesamt,
            begriffe=begriffe,
            seiten=seiten,
        )
        if zeilen:
            ergebnis.monate_geholt += 1
            ergebnis.zeilen += zeilen

    return ergebnis


# --------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------


def monatsreihe(
    db_pfad: str, project_id: str, nur_vollstaendige: bool = False
) -> List[Monatswert]:
    """Die Gesamt-Zeitreihe eines Projekts, aelteste zuerst."""
    bedingung = "and vollstaendig = 1" if nur_vollstaendige else ""
    try:
        with _verbinde(db_pfad) as conn:
            zeilen = conn.execute(
                f"select monat, klicks, impressionen, ctr, position, vollstaendig "
                f"from {TABELLE} "
                f"where project_id = ? and dimension = 'gesamt' {bedingung} "
                f"order by monat",
                (project_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning(f"[historie] Zeitreihe nicht lesbar: {exc}")
        return []

    return [
        Monatswert(
            monat=z["monat"],
            klicks=z["klicks"],
            impressionen=z["impressionen"],
            ctr=z["ctr"],
            position=z["position"],
            vollstaendig=bool(z["vollstaendig"]),
        )
        for z in zeilen
    ]


def _werte_je_monat(
    db_pfad: str, project_id: str, dimension: str, monate: Iterable[str]
) -> Dict[str, Dict[str, Any]]:
    monate = list(monate)
    if not monate:
        return {}
    platzhalter = ",".join("?" for _ in monate)
    try:
        with _verbinde(db_pfad) as conn:
            zeilen = conn.execute(
                f"select wert, sum(klicks) as klicks, sum(impressionen) as impressionen, "
                f"avg(position) as position from {TABELLE} "
                f"where project_id = ? and dimension = ? and monat in ({platzhalter}) "
                f"group by wert",
                (project_id, dimension, *monate),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning(f"[historie] Vergleich nicht lesbar: {exc}")
        return {}
    return {
        z["wert"]: {
            "klicks": int(z["klicks"] or 0),
            "impressionen": int(z["impressionen"] or 0),
            "position": round(float(z["position"] or 0.0), 2),
        }
        for z in zeilen
    }


def vergleich(
    db_pfad: str,
    project_id: str,
    dimension: str = "begriff",
    monate: int = 3,
    nur_belastbare: bool = True,
) -> List[Veraenderung]:
    """Die letzten `monate` gegen die gleich langen `monate` davor.

    Nur **vollstaendige** Monate gehen ein — ein angebrochener Monat wuerde jeden
    Vergleich nach unten ziehen und lauter Scheineinbrueche erzeugen.
    """
    reihe = [m.monat for m in monatsreihe(db_pfad, project_id, nur_vollstaendige=True)]
    if len(reihe) < monate * 2:
        return []

    nachher_monate = reihe[-monate:]
    vorher_monate = reihe[-monate * 2 : -monate]

    vorher = _werte_je_monat(db_pfad, project_id, dimension, vorher_monate)
    nachher = _werte_je_monat(db_pfad, project_id, dimension, nachher_monate)

    ergebnis: List[Veraenderung] = []
    for wert in set(vorher) | set(nachher):
        v = vorher.get(wert, {})
        n = nachher.get(wert, {})
        eintrag = Veraenderung(
            wert=wert,
            klicks_vorher=v.get("klicks", 0),
            klicks_nachher=n.get("klicks", 0),
            impressionen_vorher=v.get("impressionen", 0),
            impressionen_nachher=n.get("impressionen", 0),
            position_vorher=v.get("position", 0.0),
            position_nachher=n.get("position", 0.0),
        )
        if nur_belastbare and not eintrag.belastbar:
            continue
        ergebnis.append(eintrag)

    ergebnis.sort(key=lambda e: e.klick_differenz)
    return ergebnis


def jahresvergleich(db_pfad: str, project_id: str) -> Optional[Dict[str, Any]]:
    """Die letzten 3 vollstaendigen Monate gegen dieselben Monate im Vorjahr.

    Nur so trennt man Saisonalitaet von echter Entwicklung: Ein Campingplatz im
    November mit dem August zu vergleichen sagt nichts.
    """
    reihe = monatsreihe(db_pfad, project_id, nur_vollstaendige=True)
    if len(reihe) < 3:
        return None

    aktuell = reihe[-3:]
    gesucht = []
    for m in aktuell:
        jahr, mon = (int(t) for t in m.monat.split("-"))
        gesucht.append(f"{jahr - 1:04d}-{mon:02d}")

    vorjahr = [m for m in reihe if m.monat in gesucht]
    if len(vorjahr) < 3:
        return None

    def summe(werte: List[Monatswert]) -> Dict[str, Any]:
        return {
            "klicks": sum(w.klicks for w in werte),
            "impressionen": sum(w.impressionen for w in werte),
            "position": round(sum(w.position for w in werte) / len(werte), 2),
        }

    werte_vorjahr = summe(vorjahr)
    belastbar = werte_vorjahr["impressionen"] >= MIN_IMPRESSIONEN_JAHRESVERGLEICH
    hinweis = ""
    if not belastbar:
        hinweis = (
            f"Im Vorjahreszeitraum hatte die Website praktisch keine Sichtbarkeit "
            f"({werte_vorjahr['impressionen']} Einblendungen). Ein Zuwachs gegenueber "
            f"dieser Zeit sagt nichts ueber Fortschritt aus — die Seite war damals "
            f"in der Suche schlicht noch nicht vorhanden."
        )

    return {
        "zeitraum_aktuell": f"{aktuell[0].monat} bis {aktuell[-1].monat}",
        "zeitraum_vorjahr": f"{vorjahr[0].monat} bis {vorjahr[-1].monat}",
        "aktuell": summe(aktuell),
        "vorjahr": werte_vorjahr,
        "belastbar": belastbar,
        "hinweis": hinweis,
    }


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------


def _pfeil(differenz: float) -> str:
    if differenz > 0:
        return "+"
    return ""


def bericht_text(db_pfad: str, project_id: str, top: int = 10) -> str:
    """Verstaendlicher deutscher Bericht ueber die Historie eines Projekts."""
    reihe = monatsreihe(db_pfad, project_id)
    if not reihe:
        return (
            f"{project_id}: noch keine Historie archiviert.\n"
            f"Einmalig holen mit: seo-autopilot historie --importieren "
            f"--projekt {project_id}"
        )

    zeilen: List[str] = []
    zeilen.append(f"HISTORIE — {project_id}")
    zeilen.append("=" * 60)
    zeilen.append(
        f"{len(reihe)} Monate archiviert: {reihe[0].monat} bis {reihe[-1].monat}"
    )

    # Wann die Seite in der Suche ueberhaupt aufgetaucht ist. Ohne diese Zeile
    # liest sich eine Reihe von Nullmonaten wie ein Datenfehler — dabei ist sie
    # die Wahrheit ueber eine junge Domain.
    sichtbar_ab = next((m.monat for m in reihe if m.impressionen > 0), None)
    if sichtbar_ab is None:
        zeilen.append("In keinem archivierten Monat Sichtbarkeit in der Suche.")
    elif sichtbar_ab != reihe[0].monat:
        stumme = sum(1 for m in reihe if m.monat < sichtbar_ab)
        zeilen.append(
            f"Sichtbarkeit in der Suche beginnt {sichtbar_ab} "
            f"({stumme} Monate davor ohne jede Einblendung)."
        )
    zeilen.append("")
    zeilen.append("Monat      Klicks  Einblendungen   CTR    Position")
    zeilen.append("-" * 60)
    for m in reihe:
        marke = "  (laeuft noch)" if not m.vollstaendig else ""
        zeilen.append(
            f"{m.monat}   {m.klicks:>6}   {m.impressionen:>12}  "
            f"{m.ctr:>5.1f}%  {m.position:>7.1f}{marke}"
        )

    jv = jahresvergleich(db_pfad, project_id)
    zeilen.append("")
    if jv:
        zeilen.append("VORJAHRESVERGLEICH (gleiche Monate, ohne Saison-Verzerrung)")
        zeilen.append("-" * 60)
        a, v = jv["aktuell"], jv["vorjahr"]
        d_klicks = a["klicks"] - v["klicks"]
        d_impr = a["impressionen"] - v["impressionen"]
        zeilen.append(
            f"{jv['zeitraum_vorjahr']}: {v['klicks']} Klicks, "
            f"{v['impressionen']} Einblendungen, Position {v['position']}"
        )
        zeilen.append(
            f"{jv['zeitraum_aktuell']}: {a['klicks']} Klicks, "
            f"{a['impressionen']} Einblendungen, Position {a['position']}"
        )
        if jv["belastbar"]:
            zeilen.append(
                f"Veraenderung: {_pfeil(d_klicks)}{d_klicks} Klicks, "
                f"{_pfeil(d_impr)}{d_impr} Einblendungen"
            )
        else:
            # Bewusst KEINE Zuwachszahl: siehe MIN_IMPRESSIONEN_JAHRESVERGLEICH.
            zeilen.append(jv["hinweis"])
    else:
        zeilen.append(
            "Vorjahresvergleich: noch nicht moeglich — dafuer braucht es "
            "dieselben Monate aus dem Vorjahr im Archiv."
        )

    for dimension, ueberschrift in (("begriff", "SUCHBEGRIFFE"), ("seite", "SEITEN")):
        veraenderungen = vergleich(db_pfad, project_id, dimension=dimension, monate=3)
        if not veraenderungen:
            continue
        verlierer = [v for v in veraenderungen if v.klick_differenz < 0][:top]
        gewinner = [v for v in reversed(veraenderungen) if v.klick_differenz > 0][:top]
        zeilen.append("")
        zeilen.append(f"{ueberschrift} — letzte 3 Monate gegen die 3 davor")
        zeilen.append("-" * 60)
        if verlierer:
            zeilen.append("Weggebrochen:")
            for v in verlierer:
                zeilen.append(
                    f"  {v.klick_differenz:>5} Klicks  {v.wert[:48]}  "
                    f"(Position {v.position_vorher} -> {v.position_nachher})"
                )
        if gewinner:
            zeilen.append("Dazugewonnen:")
            for g in gewinner:
                zeilen.append(
                    f"  +{g.klick_differenz:>4} Klicks  {g.wert[:48]}  "
                    f"(Position {g.position_vorher} -> {g.position_nachher})"
                )
        if not verlierer and not gewinner:
            zeilen.append("  Keine belastbaren Veraenderungen.")

    return "\n".join(zeilen)


def exportiere_csv(
    db_pfad: str,
    ziel: str,
    project_id: Optional[str] = None,
    dimension: Optional[str] = None,
) -> int:
    """Historie als CSV herausschreiben. Gibt die Zeilenzahl zurueck.

    Semikolon als Trenner und BOM, damit Excel die Datei auf einem deutschen
    System ohne Zwischenschritt richtig oeffnet.
    """
    bedingungen: List[str] = []
    werte: List[Any] = []
    if project_id:
        bedingungen.append("project_id = ?")
        werte.append(project_id)
    if dimension:
        bedingungen.append("dimension = ?")
        werte.append(dimension)
    wo = ("where " + " and ".join(bedingungen)) if bedingungen else ""

    try:
        with _verbinde(db_pfad) as conn:
            zeilen = conn.execute(
                f"select project_id, monat, dimension, wert, klicks, impressionen, "
                f"ctr, position, vollstaendig from {TABELLE} {wo} "
                f"order by project_id, monat, dimension, klicks desc",
                werte,
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning(f"[historie] Export nicht moeglich: {exc}")
        return 0

    pfad = Path(ziel)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("w", newline="", encoding="utf-8-sig") as f:
        schreiber = csv.writer(f, delimiter=";")
        schreiber.writerow(
            [
                "Projekt",
                "Monat",
                "Ebene",
                "Wert",
                "Klicks",
                "Einblendungen",
                "CTR %",
                "Position",
                "Monat vollstaendig",
            ]
        )
        for z in zeilen:
            schreiber.writerow(
                [
                    z["project_id"],
                    z["monat"],
                    {
                        "gesamt": "Gesamt",
                        "begriff": "Suchbegriff",
                        "seite": "Seite",
                    }.get(z["dimension"], z["dimension"]),
                    z["wert"],
                    z["klicks"],
                    z["impressionen"],
                    str(z["ctr"]).replace(".", ","),
                    str(z["position"]).replace(".", ","),
                    "ja" if z["vollstaendig"] else "nein",
                ]
            )
    return len(zeilen)
