"""
Wochenbericht für den Auftraggeber ("Wie steht es um meine Seiten?").

Der Autopilot sammelt pro Lauf hunderte Einzelbefunde. Für die tägliche
Arbeit ist das richtig, als Bericht ist es unbrauchbar: Niemand liest 134
Zeilen "GEO: First 150 words contain no direct answer". Dieses Modul dreht
die Blickrichtung um — nicht "was hat das Werkzeug gefunden", sondern
"wie steht jedes Projekt da, was hat sich verändert, was bringt am meisten".

Drei Regeln, die den Bericht lesbar halten:

1. **Zusammenfassen statt aufzählen.** 23-mal derselbe Befundtyp ist EIN
   Punkt mit der Zahl 23 dahinter, keine 23 Zeilen.
2. **Deutsch ohne Fachjargon.** Die Befundtypen der Analyse sind englische
   Kürzel; hier werden sie in ganze Sätze übersetzt (siehe `BEFUND_TEXTE`).
   Kommt ein unbekannter Typ dazu, fällt der Bericht auf den Originaltitel
   zurück, statt den Punkt zu verschlucken.
3. **Kein Lauf, kein Urteil.** Ein Projekt ohne Daten im Zeitfenster wird
   als "keine Daten" ausgewiesen — nicht als "0 Punkte" und schon gar nicht
   als Absturz.

    from seo_autopilot.weekly_report import baue_wochenbericht, als_text
    bericht = baue_wochenbericht("seo_autopilot.db", "projects.yaml")
    print(als_text(bericht))
"""

from __future__ import annotations

import html as html_escape
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Ab dieser Punktzahl-Differenz sprechen wir von einer Veränderung. Darunter
# ist es Messrauschen (ein Crawl erwischt mal eine Seite mehr oder weniger).
SCHWELLE_VERAENDERUNG = 1.0

# Schweregrade der Analyse -> zwei Töpfe. Alles darunter (low/info) taucht im
# Wochenbericht nicht auf; es würde die wichtigen Punkte zudecken.
SCHWER = ("critical", "high")
MITTEL = ("medium",)

PFEILE = {
    "verbessert": "▲",
    "verschlechtert": "▼",
    "unveraendert": "→",
    "unbekannt": "·",
}

TREND_TEXT = {
    "verbessert": "verbessert",
    "verschlechtert": "verschlechtert",
    "unveraendert": "unverändert",
    "unbekannt": "keine Vergleichszahl",
}

# Befundtyp -> (Klartext-Titel, Empfehlung). Deutsch, ohne Fachbegriffe;
# wo ein Fachwort unvermeidbar ist, steht die Erklärung in Klammern dahinter.
BEFUND_TEXTE: Dict[str, tuple] = {
    # --- Auffindbarkeit ---
    "unreachable_page": (
        "Seiten sind von der Startseite aus nicht erreichbar",
        "Diese Seiten aus dem Menü oder aus passenden Texten heraus verlinken. "
        "Was Google nicht durchklicken kann, findet auch kein Besucher.",
    ),
    "orphan_page": (
        "Seiten, auf die keine einzige andere Seite verlinkt",
        "Von thematisch passenden Seiten aus darauf verlinken.",
    ),
    "noindex_detected": (
        "Seiten sind für Google gesperrt (Sperrvermerk im Seitenkopf)",
        "Prüfen, ob das gewollt ist: Impressum und Datenschutz dürfen gesperrt "
        "sein, echte Inhaltsseiten nicht.",
    ),
    "soft_404": (
        "Seiten sind praktisch leer, melden dem Besucher aber 'alles in Ordnung'",
        "Entweder Inhalt liefern oder die Seite sauber als 'nicht gefunden' melden.",
    ),
    "ai_crawler_blocked": (
        "KI-Suchdienste werden ausgesperrt",
        "In der Datei robots.txt (Zutrittsregeln für Suchmaschinen) die "
        "KI-Dienste erlauben, sonst kann ChatGPT & Co. die Seite nie empfehlen.",
    ),
    "geo_ai_crawler_blocked": (
        "KI-Suchdienste werden ausgesperrt",
        "In der Datei robots.txt (Zutrittsregeln für Suchmaschinen) die "
        "KI-Dienste erlauben, sonst kann ChatGPT & Co. die Seite nie empfehlen.",
    ),
    # --- Trefferliste bei Google ---
    "missing_meta_description": (
        "Seiten ohne Kurzbeschreibung für die Google-Trefferliste",
        "Je Seite ein bis zwei Sätze ergänzen — das ist der graue Text unter "
        "dem blauen Link bei Google und entscheidet über den Klick.",
    ),
    "missing_title": (
        "Seiten ohne Seitentitel",
        "Jede Seite braucht einen Titel — er ist die blaue Überschrift bei Google.",
    ),
    "short_title": (
        "Seitentitel zu kurz",
        "Titel auf etwa 40 bis 60 Zeichen ausbauen und den wichtigsten "
        "Suchbegriff unterbringen.",
    ),
    "long_title": (
        "Seitentitel zu lang (wird bei Google abgeschnitten)",
        "Auf etwa 60 Zeichen kürzen, das Wichtigste nach vorne.",
    ),
    "missing_h1": (
        "Seiten ohne sichtbare Hauptüberschrift",
        "Je Seite genau eine Hauptüberschrift setzen, die sagt, worum es geht.",
    ),
    "low_ctr_opportunity": (
        "Gute Platzierung, aber kaum Klicks (schwache Klickrate)",
        "Titel und Kurzbeschreibung neu schreiben — die Seite wird gefunden, "
        "nur nicht angeklickt. Das ist der schnellste Hebel überhaupt.",
    ),
    "duplicate_title": (
        "Mehrere Seiten tragen denselben Titel",
        "Titel unterscheidbar machen, sonst konkurrieren die Seiten miteinander.",
    ),
    "duplicate_meta_description": (
        "Mehrere Seiten haben dieselbe Kurzbeschreibung",
        "Je Seite eine eigene Beschreibung schreiben.",
    ),
    # --- Inhalt ---
    "thin_content": (
        "Seiten mit sehr wenig Text",
        "Inhalt ausbauen oder die Seite mit einer verwandten zusammenlegen.",
    ),
    "duplicate_content": (
        "Mehrere Seiten mit fast gleichem Text",
        "Zusammenlegen oder deutlich unterscheiden.",
    ),
    "cluster_cannibalization": (
        "Mehrere eigene Seiten konkurrieren um dasselbe Thema",
        "Themen sauber aufteilen oder Seiten zusammenlegen — sonst nehmen sich "
        "die eigenen Seiten gegenseitig die Platzierung weg.",
    ),
    "weak_cluster_linking": (
        "Seiten zum selben Thema sind untereinander kaum verlinkt",
        "Verwandte Seiten gegenseitig verlinken.",
    ),
    # --- Sichtbarkeit in KI-Antworten ---
    "geo_answer_first": (
        "Die ersten Sätze beantworten die Frage der Seite nicht",
        "Jede Seite mit einer direkten Antwort beginnen. KI-Assistenten zitieren "
        "fast immer den Anfang eines Textes.",
    ),
    "geo_freshness_signals": (
        "Auf den Seiten steht kein Datum — sie wirken veraltet",
        "Sichtbares Datum der letzten Aktualisierung ergänzen.",
    ),
    "geo_entity_clarity": (
        "Aus dem Text geht nicht klar hervor, um welche Firma es geht",
        "Firmenname und Angebot im Fließtext ausschreiben, nicht nur im Logo.",
    ),
    "geo_structured_format": (
        "Texte ohne Gliederung (keine Zwischenüberschriften, keine Aufzählungen)",
        "In Abschnitte mit Zwischenüberschriften und Listen gliedern — so "
        "übernehmen KI-Antworten einzelne Passagen.",
    ),
    "geo_fact_density": (
        "Zu wenig Handfestes im Text (Zahlen, Namen, Beispiele)",
        "Konkrete Zahlen, Orte und Beispiele ergänzen.",
    ),
    "llms_no_links": (
        "Der Wegweiser für KI-Dienste (Datei llms.txt) enthält keine Links",
        "Die wichtigsten Seiten dort eintragen.",
    ),
    "llms_txt_missing": (
        "Es fehlt der Wegweiser für KI-Dienste (Datei llms.txt)",
        "Datei anlegen und die wichtigsten Seiten eintragen.",
    ),
    # --- Zusatzangaben für Google ---
    "schema_syntax_error": (
        "Fehler in den Zusatzangaben für Google (maschinenlesbare Steckbriefe)",
        "Die fehlerhaften Angaben reparieren — fehlerhafte werden komplett "
        "ignoriert, die Arbeit war also umsonst.",
    ),
    "schema_missing_required_field": (
        "Pflichtangaben in den Google-Steckbriefen fehlen",
        "Fehlende Felder ergänzen.",
    ),
    "missing_org_schema": (
        "Es fehlt der Firmen-Steckbrief für Google",
        "Firmenangaben maschinenlesbar hinterlegen (Name, Logo, Adresse).",
    ),
    "org_schema_no_sameas": (
        "Der Firmen-Steckbrief verweist nicht auf die eigenen Profile",
        "LinkedIn, Instagram & Co. im Steckbrief verlinken — das verbindet die "
        "Auftritte für Google zu einer Firma.",
    ),
    # --- Technik ---
    "canonical_missing": (
        "Kein Hinweis auf die Originaladresse der Seite",
        "Je Seite die Originaladresse angeben, damit Google bei mehreren "
        "Adressen für denselben Inhalt die richtige nimmt.",
    ),
    "sitemap_non_canonical_urls": (
        "In der Seitenübersicht für Google stehen Adressen, die nicht die "
        "Originaladresse sind",
        "Nur die Originaladressen in die Übersicht (sitemap.xml) aufnehmen.",
    ),
    "sitemap_no_lastmod": (
        "Die Seitenübersicht für Google enthält keine Änderungsdaten",
        "Datum der letzten Änderung je Adresse eintragen.",
    ),
    "sitemap_missing": (
        "Es fehlt die Seitenübersicht für Google (sitemap.xml)",
        "Übersicht erzeugen und in der robots.txt eintragen.",
    ),
    "missing_security_headers": (
        "Sicherheits-Einstellungen am Server fehlen",
        "Die fehlenden Schutzeinstellungen im Webserver nachtragen.",
    ),
    "slow_response": (
        "Der Server antwortet langsam",
        "Ladezeit prüfen — langsame Seiten verlieren Besucher und Platzierung.",
    ),
    "images_without_alt": (
        "Bilder ohne Bildbeschreibung",
        "Kurze Beschreibung je Bild ergänzen (für Google und für Blinde).",
    ),
    "missing_og_image": (
        "Kein Vorschaubild beim Teilen in sozialen Netzen",
        "Je Seite ein Vorschaubild hinterlegen, sonst erscheint beim Teilen ein "
        "grauer Kasten.",
    ),
    # --- Pflichtseiten ---
    "missing_impressum": (
        "Kein Impressum gefunden",
        "Impressum anlegen und von jeder Seite aus verlinken (Pflicht in "
        "Deutschland).",
    ),
    "missing_datenschutz": (
        "Keine Datenschutzerklärung gefunden",
        "Datenschutzerklärung anlegen und verlinken (Pflicht).",
    ),
    "missing_privacy": (
        "Keine Datenschutzerklärung gefunden",
        "Datenschutzerklärung anlegen und verlinken (Pflicht).",
    ),
    "missing_contact_page": (
        "Keine Kontaktseite gefunden",
        "Kontaktseite mit Adresse und Erreichbarkeit anlegen.",
    ),
}


# ---------------------------------------------------------------------------
# Datenstruktur
# ---------------------------------------------------------------------------


@dataclass
class Massnahme:
    """Ein zusammengefasster Punkt — nicht ein einzelner Befund."""

    typ: str
    titel: str
    empfehlung: str
    anzahl: int = 1
    schwere: str = "mittel"  # schwer | mittel

    @property
    def anzahl_text(self) -> str:
        if self.anzahl <= 1:
            return ""
        return f"{self.anzahl}×"


@dataclass
class Projektstand:
    """Wie ein einzelnes Projekt dasteht."""

    schluessel: str
    name: str
    domain: str  # ohne Adress-Vorsatz, damit der Bericht keine Links enthält
    hat_daten: bool = False
    hinweis: str = ""
    score: Optional[float] = None
    score_vorher: Optional[float] = None
    veraenderung: Optional[float] = None
    trend: str = "unbekannt"
    schwere_befunde: int = 0
    mittlere_befunde: int = 0
    seiten: int = 0
    klicks: Optional[int] = None
    impressionen: Optional[int] = None
    klickrate: Optional[float] = None
    position: Optional[float] = None
    laeufe: int = 0
    letzter_lauf: Optional[datetime] = None
    top_punkte: List[Massnahme] = field(default_factory=list)

    @property
    def pfeil(self) -> str:
        return PFEILE.get(self.trend, "·")

    @property
    def trend_text(self) -> str:
        return TREND_TEXT.get(self.trend, self.trend)

    @property
    def durchlauf_text(self) -> str:
        return (
            f"{self.laeufe} Durchlauf"
            if self.laeufe == 1
            else (f"{self.laeufe} Durchläufen")
        )

    @property
    def domain_zusatz(self) -> str:
        """Domain nur zeigen, wenn sie nicht ohnehin schon im Namen steckt."""
        if not self.domain or self.domain.lower() in self.name.lower():
            return ""
        return self.domain


@dataclass
class Wochenbericht:
    """Der ganze Bericht über alle Projekte."""

    zeitpunkt: datetime
    tage: int
    projekte: List[Projektstand] = field(default_factory=list)

    @property
    def mit_daten(self) -> List[Projektstand]:
        return [p for p in self.projekte if p.hat_daten]

    @property
    def ohne_daten(self) -> List[Projektstand]:
        return [p for p in self.projekte if not p.hat_daten]

    @property
    def verbessert(self) -> List[Projektstand]:
        return [p for p in self.projekte if p.trend == "verbessert"]

    @property
    def verschlechtert(self) -> List[Projektstand]:
        return [p for p in self.projekte if p.trend == "verschlechtert"]

    @property
    def durchschnittsnote(self) -> Optional[float]:
        werte = [p.score for p in self.projekte if p.score is not None]
        if not werte:
            return None
        return sum(werte) / len(werte)

    @property
    def klicks_gesamt(self) -> int:
        return sum(p.klicks or 0 for p in self.projekte)

    @property
    def schwere_gesamt(self) -> int:
        return sum(p.schwere_befunde for p in self.projekte)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _lade_projekte(pfad: Path) -> Dict[str, Dict[str, Any]]:
    """Projektliste lesen. Fehlt sie, bekommen wir eben einen leeren Bericht."""
    if not pfad.exists():
        logger.warning(f"[Wochenbericht] Projektliste fehlt: {pfad}")
        return {}
    try:
        daten = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"[Wochenbericht] Projektliste unlesbar: {exc}")
        return {}
    if not isinstance(daten, dict):
        return {}
    projekte = daten.get("projects", daten)
    return projekte if isinstance(projekte, dict) else {}


def _als_datum(wert: Any) -> Optional[datetime]:
    if wert is None:
        return None
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _ohne_vorsatz(domain: str) -> str:
    """'https://www.example.com/' -> 'example.com'.

    Der Bericht soll keine anklickbaren Fremdadressen enthalten (er wird als
    Datei verschickt); außerdem liest sich die nackte Domain einfach besser.
    """
    text = (domain or "").strip()
    for vorsatz in ("https://", "http://"):
        if text.lower().startswith(vorsatz):
            text = text[len(vorsatz) :]
    if text.lower().startswith("www."):
        text = text[4:]
    return text.rstrip("/")


def _klartext(typ: str, original_titel: str) -> tuple:
    """Befundtyp in verständliches Deutsch übersetzen.

    Unbekannte Typen werden nicht verschluckt: dann steht der Originaltitel
    da (ohne den angehängten Adress-Teil nach dem Doppelpunkt).
    """
    if typ in BEFUND_TEXTE:
        return BEFUND_TEXTE[typ]
    titel = (original_titel or typ or "Unbekannter Punkt").strip()
    if ": http" in titel:
        titel = titel.split(": http")[0].strip()
    return (titel, "Details stehen im ausführlichen Prüfbericht.")


def _zahl(wert: Optional[float], nachkommastellen: int = 0) -> str:
    """Deutsche Zahlenschreibweise (Komma statt Punkt)."""
    if wert is None:
        return "–"
    return f"{wert:.{nachkommastellen}f}".replace(".", ",")


# ---------------------------------------------------------------------------
# Datenbeschaffung
# ---------------------------------------------------------------------------


def _hole_laeufe(con: sqlite3.Connection, projekt: str, grenze: datetime) -> tuple:
    """Läufe im Zeitfenster + Vergleichslauf davor.

    Rückgabe: (Läufe im Fenster aufsteigend, letzter Lauf VOR dem Fenster).
    Fehlt die Tabelle oder eine Spalte, gilt das als "keine Daten" — der
    Bericht darf an einer halbfertigen Datenbank nicht sterben.
    """
    try:
        zeilen = con.execute(
            "select id, project_id, started_at, status, score, total_pages, "
            "gsc_clicks, gsc_impressions, gsc_ctr, gsc_avg_position "
            "from seo_audits where project_id=? order by started_at asc",
            (projekt,),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning(f"[Wochenbericht] Läufe nicht lesbar ({projekt}): {exc}")
        return [], None

    im_fenster: List[sqlite3.Row] = []
    davor: Optional[sqlite3.Row] = None
    for zeile in zeilen:
        gestartet = _als_datum(zeile["started_at"])
        if gestartet is None:
            continue
        if gestartet >= grenze:
            im_fenster.append(zeile)
        else:
            davor = zeile  # aufsteigend sortiert -> am Ende der jüngste davor
    return im_fenster, davor


def _hole_massnahmen(con: sqlite3.Connection, audit_id: str) -> tuple:
    """Befunde eines Laufs zu Maßnahmen zusammenfassen.

    Rückgabe: (Top-Maßnahmen sortiert, Anzahl schwer, Anzahl mittel).
    23-mal derselbe Typ wird zu EINER Maßnahme mit anzahl=23.
    """
    relevant = SCHWER + MITTEL
    platzhalter = ",".join("?" * len(relevant))
    try:
        zeilen = con.execute(
            "select type, severity, title, count from seo_issues "
            f"where audit_id=? and severity in ({platzhalter})",
            (audit_id, *relevant),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning(f"[Wochenbericht] Befunde nicht lesbar ({audit_id}): {exc}")
        return [], 0, 0

    gruppen: Dict[str, Massnahme] = {}
    schwer = mittel = 0
    for zeile in zeilen:
        anzahl = zeile["count"] or 1
        try:
            anzahl = max(int(anzahl), 1)
        except (TypeError, ValueError):
            anzahl = 1
        ist_schwer = (zeile["severity"] or "") in SCHWER
        if ist_schwer:
            schwer += anzahl
        else:
            mittel += anzahl

        typ = zeile["type"] or "unbekannt"
        titel, empfehlung = _klartext(typ, zeile["title"])
        vorhanden = gruppen.get(typ)
        if vorhanden is None:
            gruppen[typ] = Massnahme(
                typ=typ,
                titel=titel,
                empfehlung=empfehlung,
                anzahl=anzahl,
                schwere="schwer" if ist_schwer else "mittel",
            )
        else:
            vorhanden.anzahl += anzahl
            # Ein einziger schwerer Fall macht die ganze Gruppe schwer.
            if ist_schwer:
                vorhanden.schwere = "schwer"

    sortiert = sorted(
        gruppen.values(),
        key=lambda m: (0 if m.schwere == "schwer" else 1, -m.anzahl, m.titel),
    )
    return sortiert, schwer, mittel


def _gsc_werte(laeufe: List[sqlite3.Row]) -> Dict[str, Any]:
    """Search-Console-Zahlen des jüngsten Laufs, der überhaupt welche hat.

    Nicht jeder Lauf bekommt Zahlen (Zugang fehlt, Google antwortet nicht).
    Dann lieber die letzte bekannte Zahl aus dem Fenster als gar nichts.
    """
    for zeile in reversed(laeufe):
        if zeile["gsc_clicks"] is not None or zeile["gsc_impressions"] is not None:
            return {
                "klicks": zeile["gsc_clicks"],
                "impressionen": zeile["gsc_impressions"],
                "klickrate": zeile["gsc_ctr"],
                "position": zeile["gsc_avg_position"],
            }
    return {}


# ---------------------------------------------------------------------------
# Aufbau des Berichts
# ---------------------------------------------------------------------------


def baue_wochenbericht(
    db_pfad: str = "seo_autopilot.db",
    projects_pfad: str = "projects.yaml",
    tage: int = 7,
    jetzt: Optional[datetime] = None,
) -> Wochenbericht:
    """Baut den Wochenbericht über alle aktiven Projekte.

    `jetzt` ist injizierbar, damit Tests nicht von der echten Uhr abhängen.
    Fehlt die Datenbank oder ist sie leer, kommt trotzdem ein gültiger
    Bericht heraus — dann eben mit lauter "keine Daten"-Projekten.
    """
    jetzt = jetzt or datetime.now(timezone.utc)
    grenze = jetzt - timedelta(days=tage)
    bericht = Wochenbericht(zeitpunkt=jetzt, tage=tage)

    projekte = _lade_projekte(Path(projects_pfad))
    aktive = {k: v for k, v in projekte.items() if (v or {}).get("enabled", True)}
    if not aktive:
        return bericht

    db = Path(db_pfad)
    if not db.exists():
        logger.warning(f"[Wochenbericht] Datenbank fehlt: {db_pfad}")
        for name, cfg in aktive.items():
            bericht.projekte.append(
                _leerer_stand(name, cfg, "Datenbank nicht gefunden")
            )
        return bericht

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        for name, cfg in aktive.items():
            bericht.projekte.append(_baue_projektstand(con, name, cfg or {}, grenze))
    finally:
        con.close()

    # Reihenfolge: erst die Sorgenkinder (niedrigste Note), dann der Rest,
    # Projekte ohne Daten ganz nach unten.
    bericht.projekte.sort(
        key=lambda p: (0 if p.hat_daten else 1, p.score if p.score is not None else 999)
    )
    return bericht


def _leerer_stand(name: str, cfg: Dict[str, Any], hinweis: str) -> Projektstand:
    cfg = cfg or {}
    return Projektstand(
        schluessel=name,
        name=cfg.get("name") or name,
        domain=_ohne_vorsatz(cfg.get("domain", "")),
        hat_daten=False,
        hinweis=hinweis,
    )


def _baue_projektstand(
    con: sqlite3.Connection,
    name: str,
    cfg: Dict[str, Any],
    grenze: datetime,
) -> Projektstand:
    im_fenster, davor = _hole_laeufe(con, name, grenze)

    if not im_fenster:
        if davor is None:
            return _leerer_stand(name, cfg, "Noch nie geprüft")
        gestartet = _als_datum(davor["started_at"])
        hinweis = "Kein Lauf im Zeitraum"
        if gestartet is not None:
            hinweis += f", letzter Lauf am {gestartet:%d.%m.%Y}"
        stand = _leerer_stand(name, cfg, hinweis)
        stand.letzter_lauf = gestartet
        return stand

    aktuell = im_fenster[-1]
    # Vergleichspunkt: bei mehreren Läufen im Fenster der älteste darin,
    # sonst der letzte Lauf davor ("der Lauf vor rund einer Woche").
    vergleich = im_fenster[0] if len(im_fenster) > 1 else davor

    stand = Projektstand(
        schluessel=name,
        name=cfg.get("name") or name,
        domain=_ohne_vorsatz(cfg.get("domain", "")),
        hat_daten=True,
        score=aktuell["score"],
        seiten=aktuell["total_pages"] or 0,
        laeufe=len(im_fenster),
        letzter_lauf=_als_datum(aktuell["started_at"]),
    )

    if vergleich is not None and vergleich["score"] is not None:
        stand.score_vorher = vergleich["score"]
    if stand.score is not None and stand.score_vorher is not None:
        stand.veraenderung = stand.score - stand.score_vorher
        if stand.veraenderung >= SCHWELLE_VERAENDERUNG:
            stand.trend = "verbessert"
        elif stand.veraenderung <= -SCHWELLE_VERAENDERUNG:
            stand.trend = "verschlechtert"
        else:
            stand.trend = "unveraendert"

    massnahmen, schwer, mittel = _hole_massnahmen(con, aktuell["id"])
    stand.schwere_befunde = schwer
    stand.mittlere_befunde = mittel
    stand.top_punkte = massnahmen[:3]

    for schluessel, wert in _gsc_werte(im_fenster).items():
        setattr(stand, schluessel, wert)

    return stand


# ---------------------------------------------------------------------------
# Ausgabe: Klartext
# ---------------------------------------------------------------------------


def als_text(bericht: Wochenbericht, kompakt: bool = False) -> str:
    """Deutscher Klartext-Bericht (Konsole, Mail, Telegram).

    `kompakt=True` lässt die Handlungsempfehlungen weg und nennt nur die drei
    Punkte selbst. Das ist für Telegram gedacht: dort werden Nachrichten über
    4096 Zeichen abgeschnitten — und zwar stillschweigend, also genau die Art
    Fehler, die man erst bemerkt, wenn der Bericht seit Wochen unvollständig
    ankommt.
    """
    zeilen: List[str] = []
    zeilen.append(
        f"SEO-Wochenbericht — Stand {bericht.zeitpunkt:%d.%m.%Y}, "
        f"Zeitraum: letzte {bericht.tage} Tage"
    )
    zeilen.append("=" * 60)
    zeilen.append(_gesamtzeile(bericht))
    zeilen.append("")

    if not bericht.projekte:
        zeilen.append("Keine aktiven Projekte in der Projektliste.")
        return "\n".join(zeilen)

    for p in bericht.projekte:
        kopf = f"{p.pfeil} {p.name}"
        if p.domain_zusatz:
            kopf += f" ({p.domain_zusatz})"
        zeilen.append(kopf)

        if not p.hat_daten:
            zeilen.append(f"   Keine Daten — {p.hinweis}.")
            zeilen.append("")
            continue

        note = f"   Note: {_zahl(p.score)} von 100"
        if p.trend == "unveraendert":
            note += " (unverändert gegenüber der Vorwoche)"
        elif p.veraenderung is not None:
            richtung = "+" if p.veraenderung >= 0 else "−"
            note += (
                f" ({p.trend_text}, vorher {_zahl(p.score_vorher)}, "
                f"{richtung}{_zahl(abs(p.veraenderung))} Punkte)"
            )
        else:
            note += " (kein Vergleichswert aus der Vorwoche)"
        zeilen.append(note)

        zeilen.append(
            f"   Probleme: {p.schwere_befunde} schwer, {p.mittlere_befunde} mittel "
            f"— geprüft: {p.seiten} Seiten in {p.durchlauf_text}"
        )

        if p.klicks is not None or p.position is not None:
            teile = []
            if p.klicks is not None:
                teile.append(f"{p.klicks} Klicks über Google")
            if p.impressionen is not None:
                teile.append(f"{p.impressionen} Mal angezeigt")
            if p.klickrate is not None:
                teile.append(f"Klickrate {_zahl(p.klickrate, 1)} %")
            if p.position is not None:
                teile.append(f"im Schnitt auf Platz {_zahl(p.position, 1)}")
            zeilen.append("   Google: " + ", ".join(teile))
        else:
            zeilen.append("   Google: keine Zahlen (Search Console nicht verbunden)")

        if p.top_punkte:
            zeilen.append("   Das bringt am meisten:")
            for i, m in enumerate(p.top_punkte, 1):
                menge = f" ({m.anzahl_text})" if m.anzahl_text else ""
                marke = "!" if m.schwere == "schwer" else "-"
                zeilen.append(f"     {i}. [{marke}] {m.titel}{menge}")
                if not kompakt:
                    zeilen.append(f"        → {m.empfehlung}")
        else:
            zeilen.append("   Keine schweren oder mittleren Punkte offen. Sauber.")
        zeilen.append("")

    zeilen.append("[!] = schwer, [-] = mittel. Note 0–100, höher ist besser.")
    return "\n".join(zeilen)


def _gesamtzeile(bericht: Wochenbericht) -> str:
    """Eine Zeile, die den Stand über alle Projekte zusammenfasst."""
    gesamt = len(bericht.projekte)
    if gesamt == 0:
        return "Keine aktiven Projekte."
    mit = len(bericht.mit_daten)
    schnitt = bericht.durchschnittsnote
    teile = [f"{gesamt} Projekte"]
    if schnitt is not None:
        teile.append(f"Durchschnittsnote {_zahl(schnitt)} von 100")
    teile.append(f"{len(bericht.verbessert)} besser")
    teile.append(f"{len(bericht.verschlechtert)} schlechter")
    if bericht.klicks_gesamt:
        teile.append(f"{bericht.klicks_gesamt} Google-Klicks insgesamt")
    if mit < gesamt:
        teile.append(f"{gesamt - mit} ohne Daten")
    return "Gesamt: " + ", ".join(teile) + "."


# ---------------------------------------------------------------------------
# Ausgabe: HTML
# ---------------------------------------------------------------------------

# Bewusst ohne externe Dateien: keine Schriften, keine Symbole, kein Script
# von fremden Servern. Der Bericht muss auch als Mail-Anhang oder auf einem
# Rechner ohne Internet vollständig aussehen.
_HTML_KOPF = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEO-Wochenbericht</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px 16px;
    background: #faf9f7; color: #1a1a1a;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 Helvetica, Arial, sans-serif;
    font-size: 16px; line-height: 1.55;
  }}
  .blatt {{ max-width: 820px; margin: 0 auto; }}
  h1 {{ font-size: 26px; margin: 0 0 4px; }}
  .zeitraum {{ color: #6b6b6b; font-size: 14px; margin-bottom: 20px; }}
  .gesamt {{
    background: #fff; border-left: 5px solid #e8540a;
    border-radius: 8px; padding: 14px 18px; margin-bottom: 28px;
    font-size: 17px;
  }}
  .projekt {{
    background: #fff; border: 1px solid #e9e5df; border-radius: 10px;
    padding: 18px; margin-bottom: 20px;
  }}
  .projekt h2 {{ font-size: 19px; margin: 0 0 2px; }}
  .domain {{ color: #6b6b6b; font-size: 13px; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 14px; }}
  th, td {{
    text-align: left; padding: 7px 8px; font-size: 15px;
    border-bottom: 1px solid #f0ece6; vertical-align: top;
  }}
  th {{ width: 42%; color: #6b6b6b; font-weight: 500; }}
  td {{ font-variant-numeric: tabular-nums; }}
  .note {{ font-size: 20px; font-weight: 600; }}
  .hoch {{ color: #1f7a3d; }}
  .runter {{ color: #b4231c; }}
  .gleich {{ color: #6b6b6b; }}
  .punkte {{ margin: 0; padding: 0; list-style: none; }}
  .punkte li {{ padding: 9px 0; border-top: 1px solid #f0ece6; }}
  .marke {{
    display: inline-block; font-size: 11px; letter-spacing: .04em;
    text-transform: uppercase; padding: 2px 7px; border-radius: 4px;
    margin-right: 7px; vertical-align: 2px;
  }}
  .schwer {{ background: #fdece4; color: #b8400a; }}
  .mittel {{ background: #f1efeb; color: #5d5751; }}
  .tipp {{ color: #4a4a4a; font-size: 14px; margin: 3px 0 0 0; }}
  .keine {{ color: #6b6b6b; font-style: italic; }}
  .fuss {{ color: #6b6b6b; font-size: 13px; margin-top: 26px; }}
  @media (max-width: 520px) {{
    body {{ padding: 14px 10px; font-size: 15px; }}
    th, td {{ display: block; width: 100%; border-bottom: none; padding: 2px 0; }}
    th {{ padding-top: 8px; }}
    td {{ border-bottom: 1px solid #f0ece6; padding-bottom: 8px; }}
  }}
</style>
</head>
<body>
<div class="blatt">
<h1>SEO-Wochenbericht</h1>
<div class="zeitraum">Stand {stand} &middot; Zeitraum: letzte {tage} Tage</div>
<div class="gesamt">{gesamt}</div>
"""

_HTML_FUSS = """<p class="fuss">
Note von 0 bis 100 &ndash; h&ouml;her ist besser. &bdquo;Schwer&ldquo; bedeutet:
kostet unmittelbar Sichtbarkeit. &bdquo;Mittel&ldquo;: sollte erledigt werden,
brennt aber nicht.
</p>
</div>
</body>
</html>
"""


def _e(text: Any) -> str:
    """Text sicher in HTML einsetzen."""
    return html_escape.escape(str(text if text is not None else ""))


def als_html(bericht: Wochenbericht) -> str:
    """Eigenständige HTML-Seite — keine externen Dateien, alles inline."""
    teile = [
        _HTML_KOPF.format(
            stand=_e(f"{bericht.zeitpunkt:%d.%m.%Y}"),
            tage=_e(bericht.tage),
            gesamt=_e(_gesamtzeile(bericht)),
        )
    ]

    if not bericht.projekte:
        teile.append(
            '<div class="projekt"><p class="keine">Keine aktiven Projekte '
            "in der Projektliste.</p></div>"
        )

    for p in bericht.projekte:
        teile.append('<div class="projekt">')
        teile.append(f"<h2>{p.pfeil} {_e(p.name)}</h2>")
        if p.domain_zusatz:
            teile.append(f'<div class="domain">{_e(p.domain_zusatz)}</div>')

        if not p.hat_daten:
            teile.append(
                f'<p class="keine">Keine Daten &ndash; {_e(p.hinweis)}.</p></div>'
            )
            continue

        klasse = {
            "verbessert": "hoch",
            "verschlechtert": "runter",
            "unveraendert": "gleich",
        }.get(p.trend, "gleich")

        if p.trend == "unveraendert":
            entwicklung = (
                f'<span class="{klasse}">{p.pfeil} unverändert gegenüber '
                "der Vorwoche</span>"
            )
        elif p.veraenderung is None:
            entwicklung = "kein Vergleichswert aus der Vorwoche"
        else:
            richtung = "+" if p.veraenderung >= 0 else "&minus;"
            entwicklung = (
                f'<span class="{klasse}">{p.pfeil} {_e(p.trend_text)} '
                f"({richtung}{_e(_zahl(abs(p.veraenderung)))} Punkte, "
                f"vorher {_e(_zahl(p.score_vorher))})</span>"
            )

        if p.klicks is not None or p.position is not None:
            google = []
            if p.klicks is not None:
                google.append(f"{_e(p.klicks)} Klicks")
            if p.impressionen is not None:
                google.append(f"{_e(p.impressionen)} Mal angezeigt")
            if p.klickrate is not None:
                google.append(f"Klickrate {_e(_zahl(p.klickrate, 1))} %")
            if p.position is not None:
                google.append(f"im Schnitt Platz {_e(_zahl(p.position, 1))}")
            google_text = ", ".join(google)
        else:
            google_text = "keine Zahlen (Search Console nicht verbunden)"

        teile.append("<table>")
        teile.append(
            "<tr><th>Note</th>"
            f'<td class="note">{_e(_zahl(p.score))} von 100</td></tr>'
        )
        teile.append(f"<tr><th>Entwicklung</th><td>{entwicklung}</td></tr>")
        teile.append(
            "<tr><th>Offene Probleme</th>"
            f"<td>{_e(p.schwere_befunde)} schwer, "
            f"{_e(p.mittlere_befunde)} mittel</td></tr>"
        )
        teile.append(
            "<tr><th>Gepr&uuml;ft</th>"
            f"<td>{_e(p.seiten)} Seiten in {_e(p.durchlauf_text)}</td></tr>"
        )
        teile.append(f"<tr><th>Bei Google</th><td>{google_text}</td></tr>")
        teile.append("</table>")

        if p.top_punkte:
            teile.append("<strong>Das bringt am meisten</strong>")
            teile.append('<ul class="punkte">')
            for m in p.top_punkte:
                marke = "schwer" if m.schwere == "schwer" else "mittel"
                menge = f" ({_e(m.anzahl_text)})" if m.anzahl_text else ""
                teile.append(
                    f'<li><span class="marke {marke}">{marke}</span>'
                    f"{_e(m.titel)}{menge}"
                    f'<p class="tipp">{_e(m.empfehlung)}</p></li>'
                )
            teile.append("</ul>")
        else:
            teile.append(
                '<p class="keine">Keine schweren oder mittleren Punkte offen.</p>'
            )
        teile.append("</div>")

    teile.append(_HTML_FUSS)
    return "\n".join(teile)


def schreibe_html(bericht: Wochenbericht, pfad: str) -> Path:
    """HTML-Bericht auf die Platte schreiben und den Pfad zurückgeben."""
    ziel = Path(pfad)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(als_html(bericht), encoding="utf-8")
    return ziel
