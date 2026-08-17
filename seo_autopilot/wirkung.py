"""
Wirkungsmessung — hat eine Änderung tatsächlich etwas gebracht?

Das Änderungsbuch (`changelog_book.py`) hält fest, WAS wann an einer Website
geändert wurde. Dieses Modul beantwortet die Frage danach: Ist die Seite
seitdem besser gefunden worden — und zwar so, dass die Antwort auch dann noch
trägt, wenn sie jemandem vorgelegt wird.

Gemessen wird je Änderung nach **7, 14, 28 und 56 Tagen**, indem das gleich
lange Fenster VOR der Änderung gegen das Fenster DANACH gestellt wird
(Search-Console-Daten für genau diese Adresse). Mehrere Fenster, weil SEO
unterschiedlich schnell wirkt: Ein Titel schlägt oft binnen einer Woche durch,
strukturierte Daten und Inhaltsarbeit brauchen Wochen.

Warum das Ganze so vorsichtig gebaut ist
----------------------------------------

Eine Wirkungsmessung, die zu gern "verbessert" sagt, ist schlimmer als gar
keine: Sie führt dazu, dass wirkungslose Maßnahmen wiederholt werden. Deshalb
gibt es hier fünf Sperren, die lieber kein Urteil fällen als ein schlechtes:

1. **Zu dünne Datenlage → kein Urteil.** Unterhalb von
   `mindest_impressionen(fenster)` Einblendungen im Vorher-Fenster ist jede
   Veränderung Rauschen. Die Anforderung wächst mit der Fensterlänge: 30
   Einblendungen in einer Woche sind dünn, über acht Wochen sind sie nichts.
   Die Messung wird trotzdem gespeichert (mit allen Zahlen), aber als
   `zu_wenig_daten` gekennzeichnet.
2. **Widersprechen sich die Signale, gibt es keinen Erfolg.** Position nach
   vorn, aber gleichzeitig weniger Einblendungen UND weniger Klicks: Das ist
   meist ein verschobener Suchbegriff-Mix, kein Gewinn. Dieser Fall trat beim
   ersten Live-Lauf sofort auf und hätte eine Maßnahme belohnt, die
   Sichtbarkeit gekostet hat.
3. **Mehrere Änderungen an derselben Adresse im Messzeitraum → nicht
   zurechenbar.** Wer im selben Fenster Titel UND Beschreibung ändert, kann
   hinterher nicht sagen, welche der beiden gewirkt hat.
4. **Fremde Änderungen zählen nicht als unser Erfolg.** Einträge mit
   `urheber="mensch"` werden gemessen (die Zahlen sind ja interessant), aber in
   der Bilanz getrennt ausgewiesen.
5. **Ein Abfragefehler ist kein Messergebnis.** Liefert die Search Console
   `None`, wird nichts gespeichert und beim nächsten Lauf erneut versucht —
   statt eine kaputte Abfrage als "keine Wirkung" zu verbuchen.

Die Position ist das Hauptkriterium, nicht die Klicks. Bei den Größenordnungen,
um die es hier geht (zweistellige Klickzahlen pro Monat), schwanken Klicks
stärker durch Saison und Zufall als durch alles, was wir tun; die
durchschnittliche Position ist deutlich stabiler. Klicks und Einblendungen
werden mitgeschrieben und angezeigt, entscheiden aber mit — als Gegenprobe
(Sperre 2) und bei einem Patt der Position.

Benutzung:

    from seo_autopilot.wirkung import miss_faellige, messungen, als_text

    anzahl = await miss_faellige(db_pfad, projekte)
    print(als_text(messungen(db_pfad, project_id="joseph")))
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .changelog_book import (
    URHEBER_AUTOPILOT,
    URHEBER_MENSCH,
    Aenderung,
    aenderungen,
    standard_db_pfad,  # noqa: F401  (bewusst re-exportiert)
)

logger = logging.getLogger(__name__)

TABELLE = "wirkung_messungen"

# Nach so vielen Tagen wird jeweils nachgeschaut.
MESSFENSTER: Tuple[int, ...] = (7, 14, 28, 56)

# Die Search Console liefert die letzten Tage verzoegert. Wer zu frueh misst,
# vergleicht ein volles Vorher-Fenster mit einem halb leeren Nachher-Fenster
# und sieht garantiert einen "Einbruch", den es nie gab.
GSC_VERZUG_TAGE = 3

# Unter so vielen Einblendungen im Vorher-Fenster wird kein Urteil gefaellt —
# absolute Untergrenze, unabhaengig von der Fensterlaenge.
MIN_IMPRESSIONEN = 30

# Zusaetzlich skaliert die Anforderung mit der Fensterlaenge. 30 Einblendungen
# in 7 Tagen sind duenn, in 56 Tagen sind sie nichts. Ohne diese Skalierung
# waeren lange Fenster die unzuverlaessigsten, obwohl sie die wichtigsten sind.
MIN_IMPRESSIONEN_PRO_TAG = 5

# Ab dieser Veraenderung der Durchschnittsposition gilt eine Bewegung als
# echt. Darunter ist es Rauschen — Positionen schwanken taeglich, ohne dass
# jemand etwas getan haette.
POSITION_SCHWELLE = 1.0

# Wenn die Position sich kaum bewegt hat, entscheiden die Einblendungen mit —
# aber erst ab einer deutlichen relativen Veraenderung.
IMPRESSIONEN_SCHWELLE = 0.20  # 20 %

# Urteile.
URTEIL_VERBESSERT = "verbessert"
URTEIL_VERSCHLECHTERT = "verschlechtert"
URTEIL_UNVERAENDERT = "unveraendert"
URTEIL_ZU_WENIG_DATEN = "zu_wenig_daten"
URTEIL_NICHT_ZURECHENBAR = "nicht_zurechenbar"
URTEILE = (
    URTEIL_VERBESSERT,
    URTEIL_VERSCHLECHTERT,
    URTEIL_UNVERAENDERT,
    URTEIL_ZU_WENIG_DATEN,
    URTEIL_NICHT_ZURECHENBAR,
)

# Urteile, die eine belastbare Aussage enthalten. Nur diese gehen in die Bilanz.
URTEILE_BELASTBAR = (URTEIL_VERBESSERT, URTEIL_VERSCHLECHTERT, URTEIL_UNVERAENDERT)

_URTEIL_KLARTEXT = {
    URTEIL_VERBESSERT: "besser",
    URTEIL_VERSCHLECHTERT: "schlechter",
    URTEIL_UNVERAENDERT: "unverändert",
    URTEIL_ZU_WENIG_DATEN: "zu wenig Daten",
    URTEIL_NICHT_ZURECHENBAR: "nicht zurechenbar",
}

_SCHEMA = f"""
create table if not exists {TABELLE} (
    id text primary key,
    change_id text not null,
    project_id text not null,
    ziel_url text not null,
    aktion text not null,
    urheber text not null,
    fenster_tage integer not null,
    geaendert_am text not null,
    gemessen_am text not null,
    vorher_von text not null,
    vorher_bis text not null,
    nachher_von text not null,
    nachher_bis text not null,
    vorher_klicks integer not null default 0,
    vorher_impressionen integer not null default 0,
    vorher_position real not null default 0,
    nachher_klicks integer not null default 0,
    nachher_impressionen integer not null default 0,
    nachher_position real not null default 0,
    urteil text not null,
    notiz text,
    unique (change_id, fenster_tage)
);
create index if not exists idx_{TABELLE}_projekt on {TABELLE} (project_id);
create index if not exists idx_{TABELLE}_urteil on {TABELLE} (urteil);
create index if not exists idx_{TABELLE}_aktion on {TABELLE} (aktion, urteil);
"""

_SPALTEN = (
    "id",
    "change_id",
    "project_id",
    "ziel_url",
    "aktion",
    "urheber",
    "fenster_tage",
    "geaendert_am",
    "gemessen_am",
    "vorher_von",
    "vorher_bis",
    "nachher_von",
    "nachher_bis",
    "vorher_klicks",
    "vorher_impressionen",
    "vorher_position",
    "nachher_klicks",
    "nachher_impressionen",
    "nachher_position",
    "urteil",
    "notiz",
)


@dataclass
class Messung:
    """Eine Wirkungsmessung: eine Änderung, ein Zeitfenster, ein Urteil."""

    id: str
    change_id: str
    project_id: str
    ziel_url: str
    aktion: str
    urheber: str
    fenster_tage: int
    geaendert_am: str
    gemessen_am: str
    vorher_von: str
    vorher_bis: str
    nachher_von: str
    nachher_bis: str
    vorher_klicks: int = 0
    vorher_impressionen: int = 0
    vorher_position: float = 0.0
    nachher_klicks: int = 0
    nachher_impressionen: int = 0
    nachher_position: float = 0.0
    urteil: str = URTEIL_ZU_WENIG_DATEN
    notiz: str = ""

    @property
    def positions_differenz(self) -> float:
        """Um wie viele Plätze hat sich die Seite bewegt.

        Positiv = nach vorn gerückt. In der Search Console ist Position 1 die
        beste, kleinere Zahlen sind also besser — hier wird bewusst gedreht,
        damit "positiv = gut" gilt und niemand das Vorzeichen falsch liest.
        """
        return round(self.vorher_position - self.nachher_position, 2)

    @property
    def klick_differenz(self) -> int:
        return self.nachher_klicks - self.vorher_klicks

    @property
    def impressionen_differenz(self) -> int:
        return self.nachher_impressionen - self.vorher_impressionen

    @property
    def belastbar(self) -> bool:
        """Enthält diese Messung eine verwertbare Aussage?"""
        return self.urteil in URTEILE_BELASTBAR

    @property
    def uns_zurechenbar(self) -> bool:
        """Zählt diese Messung als Beleg für unsere Arbeit?"""
        return self.belastbar and self.urheber == URHEBER_AUTOPILOT

    @property
    def urteil_klartext(self) -> str:
        return _URTEIL_KLARTEXT.get(self.urteil, self.urteil)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _heute(wert: Optional[date] = None) -> date:
    return wert or datetime.now(timezone.utc).date()


def _als_datum(zeitpunkt: str) -> Optional[date]:
    """ISO-Zeitstempel aus dem Änderungsbuch zu einem Datum.

    Tolerant gegenüber Zeitzonen-Suffixen und fehlender Uhrzeit — ein
    unlesbarer Zeitstempel darf die Messung anderer Änderungen nicht stoppen.
    """
    if not zeitpunkt:
        return None
    text = str(zeitpunkt).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        logger.debug(f"[wirkung] Zeitstempel nicht lesbar: {zeitpunkt!r}")
        return None


def fensterbereiche(
    geaendert_am: date, fenster_tage: int
) -> Tuple[Tuple[date, date], Tuple[date, date]]:
    """Die beiden Vergleichszeiträume rund um eine Änderung.

    Der Tag der Änderung selbst gehört in keines der beiden Fenster: An ihm
    stand die Seite teils in der alten, teils in der neuen Fassung online. Ihn
    mitzuzählen würde jede Messung um einen Mischtag verunreinigen.

    Rückgabe: ((vorher_von, vorher_bis), (nachher_von, nachher_bis)), jeweils
    inklusive Randtage.
    """
    vorher_bis = geaendert_am - timedelta(days=1)
    vorher_von = vorher_bis - timedelta(days=fenster_tage - 1)
    nachher_von = geaendert_am + timedelta(days=1)
    nachher_bis = nachher_von + timedelta(days=fenster_tage - 1)
    return (vorher_von, vorher_bis), (nachher_von, nachher_bis)


def messbar_ab(geaendert_am: date, fenster_tage: int) -> date:
    """Ab wann liegen genug Daten für dieses Fenster vor.

    Das Nachher-Fenster muss komplett sein UND die Search Console muss die
    Daten geliefert haben — daher der Aufschlag von `GSC_VERZUG_TAGE`.
    """
    _, (_, nachher_bis) = fensterbereiche(geaendert_am, fenster_tage)
    return nachher_bis + timedelta(days=GSC_VERZUG_TAGE)


def tabelle_anlegen(db_pfad: str) -> bool:
    """Legt `wirkung_messungen` an, falls sie fehlt. Mehrfach aufrufbar."""
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[wirkung] Datenbank nicht erreichbar ({db_pfad}): {exc}")
        return False
    try:
        con.executescript(_SCHEMA)
        con.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning(f"[wirkung] Tabelle {TABELLE} nicht anlegbar: {exc}")
        return False
    finally:
        con.close()


def _zeile_zu_messung(row: sqlite3.Row) -> Messung:
    return Messung(
        id=row["id"],
        change_id=row["change_id"],
        project_id=row["project_id"],
        ziel_url=row["ziel_url"],
        aktion=row["aktion"],
        urheber=row["urheber"],
        fenster_tage=int(row["fenster_tage"]),
        geaendert_am=row["geaendert_am"],
        gemessen_am=row["gemessen_am"],
        vorher_von=row["vorher_von"],
        vorher_bis=row["vorher_bis"],
        nachher_von=row["nachher_von"],
        nachher_bis=row["nachher_bis"],
        vorher_klicks=int(row["vorher_klicks"] or 0),
        vorher_impressionen=int(row["vorher_impressionen"] or 0),
        vorher_position=float(row["vorher_position"] or 0.0),
        nachher_klicks=int(row["nachher_klicks"] or 0),
        nachher_impressionen=int(row["nachher_impressionen"] or 0),
        nachher_position=float(row["nachher_position"] or 0.0),
        urteil=row["urteil"],
        notiz=row["notiz"] or "",
    )


# ---------------------------------------------------------------------------
# Beurteilen
# ---------------------------------------------------------------------------


def mindest_impressionen(fenster_tage: int) -> int:
    """Wie viele Einblendungen ein Fenster braucht, damit es zählt.

    Skaliert mit der Fensterlänge: Was in einer Woche eine dünne, aber
    vertretbare Basis ist, ist über acht Wochen nichts.
    """
    return max(MIN_IMPRESSIONEN, MIN_IMPRESSIONEN_PRO_TAG * int(fenster_tage or 0))


def beurteile(
    vorher: Dict[str, Any],
    nachher: Dict[str, Any],
    fenster_tage: int = 7,
) -> Tuple[str, str]:
    """Vergleicht zwei Zeitfenster und fällt ein Urteil.

    Reihenfolge der Prüfungen ist Absicht:

    1. Reicht die Datenmenge überhaupt? Sonst `zu_wenig_daten` — und zwar
       BEVOR irgendeine Zahl verglichen wird. Ein Sprung von 2 auf 5 Klicks
       sieht nach +150 % aus und bedeutet nichts.
    2. War die Seite vorher gar nicht sichtbar und ist es jetzt? Das ist der
       einzige Fall, in dem eine fehlende Vorher-Position kein Ausschluss ist:
       Von "kommt nicht vor" zu "wird eingeblendet" ist eine echte Aussage.
    3. Widersprechen sich die Signale, wird kein Erfolg gemeldet (siehe unten).
    4. Sonst entscheidet die Positionsveränderung, und nur bei einem Patt die
       Zahl der Einblendungen.

    Rückgabe: (Urteil, Notiz im Klartext).
    """
    v_impr = int(vorher.get("impressions", 0) or 0)
    n_impr = int(nachher.get("impressions", 0) or 0)
    v_pos = float(vorher.get("position", 0.0) or 0.0)
    n_pos = float(nachher.get("position", 0.0) or 0.0)
    v_klicks = int(vorher.get("clicks", 0) or 0)
    n_klicks = int(nachher.get("clicks", 0) or 0)

    noetig = mindest_impressionen(fenster_tage)

    # 2. Neu sichtbar geworden — vor der Datenmengen-Sperre, weil hier gerade
    #    das Fehlen von Vorher-Daten die Aussage ist.
    if v_impr == 0 and n_impr >= noetig:
        return (
            URTEIL_VERBESSERT,
            f"Seite war vorher nicht in der Suche sichtbar, jetzt "
            f"{n_impr} Einblendungen (Position {n_pos:.1f}).",
        )

    # 1. Datenmenge
    if v_impr < noetig:
        return (
            URTEIL_ZU_WENIG_DATEN,
            f"Nur {v_impr} Einblendungen vor der Änderung "
            f"(nötig für {fenster_tage} Tage: {noetig}). "
            "Jede Veränderung wäre Zufall.",
        )

    # Ganz verschwunden ist ebenfalls eine Aussage.
    if n_impr == 0:
        return (
            URTEIL_VERSCHLECHTERT,
            f"Seite taucht in der Suche nicht mehr auf "
            f"(vorher {v_impr} Einblendungen). Dringend prüfen.",
        )

    # 3. Position
    pos_diff = round(v_pos - n_pos, 2)  # positiv = nach vorn
    impr_diff_rel = (n_impr - v_impr) / max(v_impr, 1)

    basis = (
        f"Position {v_pos:.1f} → {n_pos:.1f}, "
        f"Einblendungen {v_impr} → {n_impr}, "
        f"Klicks {v_klicks} → {n_klicks}."
    )

    # 3. Widersprechen sich die Signale?
    #
    # Der Fall ist häufiger, als er klingt, und wurde beim ersten Live-Lauf
    # sofort sichtbar: Die Durchschnittsposition springt nach vorn, während
    # Einblendungen UND Klicks zurückgehen. Das ist meist kein Erfolg, sondern
    # ein verschobener Suchbegriff-Mix — die Seite erscheint nur noch zu
    # wenigen, sehr passenden Suchen (dort weit vorn) und fällt aus den
    # breiten Suchen heraus. Als "besser" verbucht, würde das eine Maßnahme
    # belohnen, die Sichtbarkeit gekostet hat.
    signale_widersprechen = (
        abs(pos_diff) >= POSITION_SCHWELLE
        and (n_impr < v_impr)
        and (n_klicks < v_klicks)
        and pos_diff > 0
    )
    if signale_widersprechen:
        return (
            URTEIL_UNVERAENDERT,
            f"Widersprüchlich: {pos_diff:+.1f} Plätze nach vorn, aber weniger "
            f"Einblendungen und weniger Klicks. Deutet auf einen verschobenen "
            f"Suchbegriff-Mix hin, nicht auf einen Erfolg. {basis}",
        )

    if pos_diff >= POSITION_SCHWELLE:
        return (URTEIL_VERBESSERT, f"{pos_diff:+.1f} Plätze nach vorn. {basis}")
    if pos_diff <= -POSITION_SCHWELLE:
        return (URTEIL_VERSCHLECHTERT, f"{pos_diff:+.1f} Plätze zurück. {basis}")

    # Patt bei der Position — dann entscheiden die Einblendungen.
    if impr_diff_rel >= IMPRESSIONEN_SCHWELLE:
        return (
            URTEIL_VERBESSERT,
            f"Position kaum bewegt, aber {impr_diff_rel:+.0%} Einblendungen. {basis}",
        )
    if impr_diff_rel <= -IMPRESSIONEN_SCHWELLE:
        return (
            URTEIL_VERSCHLECHTERT,
            f"Position kaum bewegt, aber {impr_diff_rel:+.0%} Einblendungen. {basis}",
        )

    return (URTEIL_UNVERAENDERT, f"Keine nennenswerte Bewegung. {basis}")


# ---------------------------------------------------------------------------
# Fälligkeit
# ---------------------------------------------------------------------------


def faellige_messungen(
    db_pfad: str,
    project_id: Optional[str] = None,
    heute: Optional[date] = None,
    max_alter_tage: int = 120,
) -> List[Tuple[Aenderung, int]]:
    """Welche Änderungen sind jetzt für welches Fenster dran.

    Fällig ist ein Paar (Änderung, Fenster), wenn das Nachher-Fenster samt
    Search-Console-Verzug vorbei ist und für dieses Paar noch keine Messung
    existiert. Jedes Fenster wird genau einmal gemessen — später ändern sich
    die Zahlen für einen abgeschlossenen Zeitraum nicht mehr.

    Übersprungen werden Änderungen ohne Adresse (eine Datei ohne öffentliche
    URL lässt sich in der Search Console nicht nachschlagen) und
    zurückgenommene Änderungen (die erklären keine heutigen Rankings mehr).
    """
    tag = _heute(heute)
    alle = aenderungen(db_pfad, project_id=project_id, tage=max_alter_tage)
    if not alle:
        return []

    bereits = _gemessene_paare(db_pfad)
    faellig: List[Tuple[Aenderung, int]] = []

    for aenderung in alle:
        if not aenderung.ziel_url:
            continue
        if aenderung.zurueckgenommen:
            continue
        geaendert = _als_datum(aenderung.zeitpunkt)
        if not geaendert:
            continue
        for fenster in MESSFENSTER:
            if (aenderung.id, fenster) in bereits:
                continue
            if tag >= messbar_ab(geaendert, fenster):
                faellig.append((aenderung, fenster))

    return faellig


def _gemessene_paare(db_pfad: str) -> set:
    """Alle bereits gemessenen (change_id, fenster)-Paare."""
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[wirkung] Datenbank nicht lesbar ({db_pfad}): {exc}")
        return set()
    try:
        rows = con.execute(f"select change_id, fenster_tage from {TABELLE}").fetchall()
    except sqlite3.Error as exc:
        if "no such table" in str(exc).lower():
            logger.debug("[wirkung] noch keine Messungen vorhanden")
        else:
            logger.warning(f"[wirkung] Messungen nicht lesbar: {exc}")
        return set()
    finally:
        con.close()
    return {(r[0], int(r[1])) for r in rows}


def _konkurrierende_aenderungen(
    alle: Sequence[Aenderung],
    aenderung: Aenderung,
    von: date,
    bis: date,
) -> List[Aenderung]:
    """Andere Änderungen an derselben Adresse im Messzeitraum.

    Solange hier etwas drinsteht, lässt sich eine Wirkung nicht einer einzelnen
    Maßnahme zuschreiben — auch dann nicht, wenn die Zahlen eindeutig aussehen.
    """
    treffer = []
    for andere in alle:
        if andere.id == aenderung.id:
            continue
        if andere.ziel_url != aenderung.ziel_url:
            continue
        tag = _als_datum(andere.zeitpunkt)
        if tag and von <= tag <= bis:
            treffer.append(andere)
    return treffer


# ---------------------------------------------------------------------------
# Messen
# ---------------------------------------------------------------------------


def speichere_messung(db_pfad: str, messung: Messung) -> bool:
    """Schreibt eine Messung. Vorhandene Paare werden ersetzt, nicht verdoppelt."""
    if not tabelle_anlegen(db_pfad):
        return False
    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[wirkung] Datenbank nicht erreichbar ({db_pfad}): {exc}")
        return False
    try:
        werte = [getattr(messung, spalte) for spalte in _SPALTEN]
        con.execute(
            f"insert or replace into {TABELLE} ({', '.join(_SPALTEN)}) "
            f"values ({', '.join('?' * len(_SPALTEN))})",
            werte,
        )
        con.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning(f"[wirkung] Messung nicht speicherbar: {exc}")
        return False
    finally:
        con.close()


async def miss_eine(
    db_pfad: str,
    aenderung: Aenderung,
    fenster_tage: int,
    hole_fenster: Callable,
    alle_aenderungen: Optional[Sequence[Aenderung]] = None,
    heute: Optional[date] = None,
) -> Optional[Messung]:
    """Misst eine einzelne Änderung in einem Fenster und speichert das Ergebnis.

    `hole_fenster(url, von, bis)` liefert die Kennzahlen — als eigener
    Parameter, damit die Messlogik ohne Netz testbar bleibt und nicht an die
    Search Console gebunden ist. Gibt sie `None` zurück (Abfragefehler), wird
    NICHTS gespeichert: Beim nächsten Lauf ist dieselbe Messung wieder fällig.
    """
    geaendert = _als_datum(aenderung.zeitpunkt)
    if not geaendert:
        return None
    if not aenderung.ziel_url:
        return None

    (v_von, v_bis), (n_von, n_bis) = fensterbereiche(geaendert, fenster_tage)

    vorher = await hole_fenster(aenderung.ziel_url, v_von, v_bis)
    nachher = await hole_fenster(aenderung.ziel_url, n_von, n_bis)
    if vorher is None or nachher is None:
        logger.info(
            f"[wirkung] {aenderung.ziel_url} ({fenster_tage} Tage): "
            "keine Daten abrufbar, wird beim nächsten Lauf erneut versucht"
        )
        return None

    urteil, notiz = beurteile(vorher, nachher, fenster_tage=fenster_tage)

    # Zurechenbarkeit schlaegt jedes Zahlenergebnis: Wer im selben Zeitraum
    # mehrfach an derselben Seite gearbeitet hat, kann die Wirkung nicht einer
    # einzelnen Massnahme zuschreiben.
    if alle_aenderungen:
        konkurrenz = _konkurrierende_aenderungen(
            alle_aenderungen, aenderung, v_von, n_bis
        )
        if konkurrenz:
            arten = sorted({k.aktion for k in konkurrenz})
            urteil = URTEIL_NICHT_ZURECHENBAR
            notiz = (
                f"{len(konkurrenz)} weitere Änderung(en) an derselben Seite im "
                f"Messzeitraum ({', '.join(arten)}). {notiz}"
            )

    messung = Messung(
        id=str(uuid.uuid4()),
        change_id=aenderung.id,
        project_id=aenderung.project_id,
        ziel_url=aenderung.ziel_url,
        aktion=aenderung.aktion,
        urheber=aenderung.urheber,
        fenster_tage=fenster_tage,
        geaendert_am=geaendert.isoformat(),
        gemessen_am=_heute(heute).isoformat(),
        vorher_von=v_von.isoformat(),
        vorher_bis=v_bis.isoformat(),
        nachher_von=n_von.isoformat(),
        nachher_bis=n_bis.isoformat(),
        vorher_klicks=int(vorher.get("clicks", 0) or 0),
        vorher_impressionen=int(vorher.get("impressions", 0) or 0),
        vorher_position=float(vorher.get("position", 0.0) or 0.0),
        nachher_klicks=int(nachher.get("clicks", 0) or 0),
        nachher_impressionen=int(nachher.get("impressions", 0) or 0),
        nachher_position=float(nachher.get("position", 0.0) or 0.0),
        urteil=urteil,
        notiz=notiz,
    )
    speichere_messung(db_pfad, messung)
    return messung


def _gsc_holer(property_url: str, quelle: Any) -> Callable:
    """Baut die `hole_fenster`-Funktion für ein Projekt aus einer GSC-Quelle."""

    async def hole(url: str, von: date, bis: date):
        return await quelle.pull_url_window(property_url, url, von, bis)

    return hole


def _gsc_konfiguration(projekt: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(property_url, credentials_path) eines Projekts — oder None.

    Ohne Search Console ist keine Wirkungsmessung möglich. Das ist kein Fehler,
    sondern eine Tatsache über das Projekt; sie wird gemeldet, nicht geworfen.
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


async def miss_faellige(
    db_pfad: str,
    projekte: Dict[str, Dict[str, Any]],
    project_id: Optional[str] = None,
    heute: Optional[date] = None,
    max_messungen: int = 200,
) -> List[Messung]:
    """Misst alles, was fällig ist — über alle Projekte mit Search Console.

    Das ist der Einstiegspunkt für den täglichen Lauf. Pro Projekt wird die
    Search Console einmal angemeldet und dann jede fällige Änderung
    nachgeschlagen.

    `max_messungen` ist eine Bremse gegen den ersten Lauf auf einem alten
    Änderungsbuch: Dort können auf einen Schlag Hunderte Paare fällig sein,
    und jedes davon sind zwei API-Abfragen. Was nicht drankommt, bleibt fällig
    und wird beim nächsten Lauf gemessen — die Zahlen für einen
    abgeschlossenen Zeitraum ändern sich ja nicht mehr.
    """
    from .sources.gsc import GSCDataSource

    tag = _heute(heute)
    faellig = faellige_messungen(db_pfad, project_id=project_id, heute=tag)
    if not faellig:
        logger.info("[wirkung] nichts fällig")
        return []

    # Nach Projekt buendeln — eine Anmeldung je Projekt, nicht je Messung.
    je_projekt: Dict[str, List[Tuple[Aenderung, int]]] = {}
    for aenderung, fenster in faellig:
        je_projekt.setdefault(aenderung.project_id, []).append((aenderung, fenster))

    ergebnisse: List[Messung] = []
    verbleibend = max_messungen

    for pid, paare in je_projekt.items():
        if verbleibend <= 0:
            logger.info(
                f"[wirkung] Obergrenze {max_messungen} erreicht — "
                f"restliche Messungen bleiben fällig"
            )
            break

        projekt = projekte.get(pid) or {}
        konfig = _gsc_konfiguration(projekt)
        if not konfig:
            logger.info(
                f"[wirkung] {pid}: keine Search Console konfiguriert — "
                f"{len(paare)} Messung(en) nicht möglich"
            )
            continue

        property_url, credentials = konfig
        try:
            quelle = GSCDataSource(credentials)
            if not await quelle.authenticate():
                logger.warning(
                    f"[wirkung] {pid}: Search-Console-Anmeldung fehlgeschlagen"
                )
                continue
        except Exception as exc:
            logger.warning(f"[wirkung] {pid}: Search Console nicht nutzbar: {exc}")
            continue

        hole = _gsc_holer(property_url, quelle)
        # Fuer die Zurechenbarkeitspruefung: alle Aenderungen des Projekts,
        # nicht nur die faelligen.
        alle_des_projekts = aenderungen(db_pfad, project_id=pid, tage=0)

        for aenderung, fenster in paare:
            if verbleibend <= 0:
                break
            try:
                messung = await miss_eine(
                    db_pfad,
                    aenderung,
                    fenster,
                    hole,
                    alle_aenderungen=alle_des_projekts,
                    heute=tag,
                )
            except Exception as exc:
                logger.warning(
                    f"[wirkung] {pid}: Messung fehlgeschlagen "
                    f"({aenderung.ziel_url}, {fenster} Tage): {exc}"
                )
                continue
            verbleibend -= 1
            if messung:
                ergebnisse.append(messung)

    belastbar = sum(1 for m in ergebnisse if m.belastbar)
    logger.info(
        f"[wirkung] {len(ergebnisse)} Messung(en) durchgeführt, "
        f"{belastbar} mit belastbarem Ergebnis"
    )
    return ergebnisse


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


def messungen(
    db_pfad: str,
    project_id: Optional[str] = None,
    fenster_tage: Optional[int] = None,
    nur_belastbar: bool = False,
    tage: int = 0,
    heute: Optional[date] = None,
) -> List[Messung]:
    """Liest gespeicherte Messungen, neueste zuerst.

    `tage` begrenzt auf kürzlich gemessene (0 = alles), `nur_belastbar`
    blendet `zu_wenig_daten` und `nicht_zurechenbar` aus.
    """
    bedingungen: List[str] = []
    werte: List[Any] = []

    if project_id:
        bedingungen.append("project_id = ?")
        werte.append(project_id)
    if fenster_tage:
        bedingungen.append("fenster_tage = ?")
        werte.append(int(fenster_tage))
    if nur_belastbar:
        platzhalter = ", ".join("?" * len(URTEILE_BELASTBAR))
        bedingungen.append(f"urteil in ({platzhalter})")
        werte.extend(URTEILE_BELASTBAR)
    if tage and tage > 0:
        grenze = _heute(heute) - timedelta(days=int(tage))
        bedingungen.append("gemessen_am >= ?")
        werte.append(grenze.isoformat())

    wo = f"where {' and '.join(bedingungen)}" if bedingungen else ""

    try:
        con = sqlite3.connect(db_pfad)
    except sqlite3.Error as exc:
        logger.warning(f"[wirkung] Datenbank nicht lesbar ({db_pfad}): {exc}")
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"select {', '.join(_SPALTEN)} from {TABELLE} {wo} "
            "order by gemessen_am desc, fenster_tage asc, id asc",
            werte,
        ).fetchall()
    except sqlite3.Error as exc:
        if "no such table" in str(exc).lower():
            logger.debug("[wirkung] noch keine Wirkungsmessungen angelegt")
        else:
            logger.warning(f"[wirkung] Messungen nicht lesbar: {exc}")
        return []
    finally:
        con.close()

    return [_zeile_zu_messung(r) for r in rows]


def bilanz(
    db_pfad: str,
    project_id: Optional[str] = None,
    fenster_tage: Optional[int] = None,
    nur_eigene: bool = True,
) -> List[Dict[str, Any]]:
    """Was wirkt — Trefferquote je Art der Änderung.

    Das ist der eigentliche Ertrag der ganzen Buchführung: nicht "Seite X ist
    gestiegen", sondern "Titel umschreiben hat in 7 von 10 Fällen geholfen,
    og:image nachrüsten in 0 von 4". Genau diese Quote braucht der spätere
    Chancen-Motor, um Aufwand sinnvoll zu verteilen.

    `nur_eigene=True` (Standard) zählt ausschließlich Änderungen des
    Autopiloten — fremde Arbeit darf die eigene Trefferquote nicht schönen.
    Nicht belastbare Messungen fließen nirgends ein, werden aber als
    `ohne_urteil` mitgezählt, damit sichtbar bleibt, wie dünn die Basis ist.
    """
    alle = messungen(db_pfad, project_id=project_id, fenster_tage=fenster_tage)
    if nur_eigene:
        alle = [m for m in alle if m.urheber == URHEBER_AUTOPILOT]

    je_aktion: Dict[str, Dict[str, Any]] = {}
    for m in alle:
        eintrag = je_aktion.setdefault(
            m.aktion,
            {
                "aktion": m.aktion,
                "gemessen": 0,
                "besser": 0,
                "schlechter": 0,
                "unveraendert": 0,
                "ohne_urteil": 0,
                "positions_summe": 0.0,
            },
        )
        eintrag["gemessen"] += 1
        if m.urteil == URTEIL_VERBESSERT:
            eintrag["besser"] += 1
        elif m.urteil == URTEIL_VERSCHLECHTERT:
            eintrag["schlechter"] += 1
        elif m.urteil == URTEIL_UNVERAENDERT:
            eintrag["unveraendert"] += 1
        else:
            eintrag["ohne_urteil"] += 1
        if m.belastbar:
            eintrag["positions_summe"] += m.positions_differenz

    ergebnis = []
    for eintrag in je_aktion.values():
        belastbar = eintrag["besser"] + eintrag["schlechter"] + eintrag["unveraendert"]
        eintrag["belastbar"] = belastbar
        eintrag["trefferquote"] = (
            round(eintrag["besser"] / belastbar, 2) if belastbar else None
        )
        eintrag["positions_schnitt"] = (
            round(eintrag["positions_summe"] / belastbar, 2) if belastbar else None
        )
        eintrag.pop("positions_summe")
        ergebnis.append(eintrag)

    # Sortierung: erst was belegbar wirkt, dann was oft gemessen wurde.
    ergebnis.sort(
        key=lambda e: (
            e["trefferquote"] if e["trefferquote"] is not None else -1,
            e["belastbar"],
        ),
        reverse=True,
    )
    return ergebnis


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


def als_text(
    liste: Iterable[Messung],
    mit_notiz: bool = True,
) -> str:
    """Wirkungsmessungen als lesbarer Text für CLI und Telegram."""
    liste = list(liste)
    if not liste:
        return "Noch keine Wirkungsmessungen. (Nach der ersten Änderung dauert es 7 Tage bis zur ersten Auswertung.)"

    belastbar = [m for m in liste if m.belastbar]
    besser = sum(1 for m in belastbar if m.urteil == URTEIL_VERBESSERT)
    schlechter = sum(1 for m in belastbar if m.urteil == URTEIL_VERSCHLECHTERT)

    zeilen = [
        f"Wirkungsmessung — {len(liste)} Messung(en), davon {len(belastbar)} "
        f"mit belastbarem Ergebnis ({besser}× besser, {schlechter}× schlechter).",
        "",
    ]

    nach_projekt: Dict[str, List[Messung]] = {}
    for m in liste:
        nach_projekt.setdefault(m.project_id, []).append(m)

    for projekt, gruppe in nach_projekt.items():
        zeilen.append(f"[{projekt}]")
        for m in gruppe:
            marke = {
                URTEIL_VERBESSERT: "▲",
                URTEIL_VERSCHLECHTERT: "▼",
                URTEIL_UNVERAENDERT: "=",
            }.get(m.urteil, "·")
            wer = "" if m.urheber == URHEBER_AUTOPILOT else f" [{m.urheber}]"
            zeilen.append(
                f"  {marke} nach {m.fenster_tage:>2} Tagen  {m.aktion:<18} "
                f"{m.urteil_klartext:<18}{wer} {m.ziel_url}"
            )
            if mit_notiz and m.notiz:
                zeilen.append(f"      {m.notiz}")
        zeilen.append("")

    fremde = [m for m in liste if m.urheber == URHEBER_MENSCH and m.belastbar]
    if fremde:
        zeilen.append(
            f"Hinweis: {len(fremde)} Messung(en) betreffen fremde Änderungen. "
            "Deren Wirkung ist nicht unser Verdienst."
        )

    return "\n".join(zeilen).rstrip()


def bilanz_als_text(zeilen_bilanz: Iterable[Dict[str, Any]]) -> str:
    """Die Trefferquote je Änderungsart als Tabelle."""
    zeilen_bilanz = list(zeilen_bilanz)
    if not zeilen_bilanz:
        return "Noch keine Bilanz möglich — es liegen keine eigenen Messungen vor."

    zeilen = [
        "Was wirkt — Trefferquote je Art der Änderung",
        "(nur eigene Änderungen, nur belastbare Messungen)",
        "",
        f"  {'Art':<20} {'belastbar':>9} {'besser':>7} {'schlechter':>10} "
        f"{'Quote':>7} {'Plätze Ø':>9}",
    ]
    for e in zeilen_bilanz:
        quote = "–" if e["trefferquote"] is None else f"{e['trefferquote']:.0%}"
        plaetze = (
            "–" if e["positions_schnitt"] is None else f"{e['positions_schnitt']:+.1f}"
        )
        zeilen.append(
            f"  {e['aktion']:<20} {e['belastbar']:>9} {e['besser']:>7} "
            f"{e['schlechter']:>10} {quote:>7} {plaetze:>9}"
        )

    ohne = sum(e["ohne_urteil"] for e in zeilen_bilanz)
    if ohne:
        zeilen.append("")
        zeilen.append(
            f"  {ohne} Messung(en) ohne Urteil (zu wenig Daten oder nicht "
            "eindeutig zurechenbar) — bewusst nicht eingerechnet."
        )
    return "\n".join(zeilen)
