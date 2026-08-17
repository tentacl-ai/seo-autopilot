"""
Richtlinien-Radar — von der Nachricht zur Handlungsanweisung.

Der Autopilot zieht seit Welle 3 fleißig RSS-Feeds (Google Search Central,
Search Engine Land, Moz, Ahrefs, web.dev, Chrome Developers, Google News).
Bisher landete das alles nur als *Nachrichtentext* im Bericht: interessant zu
lesen, aber niemand hat daraus je etwas abgeleitet. Wenn Google morgen die
Regeln für AI-Crawler ändert, steht es zwar im Bericht — es sagt aber niemand,
dass genau deshalb unser `robots_sitemap`-Prüfer angepasst gehört.

Dieses Modul schließt die Lücke. Es beantwortet zwei Fragen:

1. **Ist das überhaupt eine Richtlinienänderung?** (Themen-Erkennung)
2. **Welche unserer eigenen Prüfregeln hängt daran?** (Prüfbereiche)

Damit wird aus "Google hat etwas zu INP geschrieben" ein handfestes
"→ pagespeed / core_web_vitals ansehen".

    from seo_autopilot.policy_radar import analysiere_meldungen, radar_zusammenfassung

    treffer = analysiere_meldungen(feed_eintraege, max_alter_tage=14)
    print(radar_zusammenfassung(treffer))

Grundregel: **Das Radar darf niemals den Lauf killen.** Feeds liefern
regelmäßig kaputte Einträge (fehlende Felder, `None`, leere Listen, fremde
Datentypen). Jeder unbrauchbare Eintrag wird still übersprungen, statt eine
Ausnahme zu werfen — ein abgestürzter Bericht ist schlimmer als eine
verpasste Meldung.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Pattern, Tuple

logger = logging.getLogger(__name__)

RELEVANZ_HOCH = "hoch"
RELEVANZ_MITTEL = "mittel"
RELEVANZ_NIEDRIG = "niedrig"

RELEVANZ_REIHENFOLGE = {RELEVANZ_HOCH: 0, RELEVANZ_MITTEL: 1, RELEVANZ_NIEDRIG: 2}


# ---------------------------------------------------------------------------
# Themen-Landkarte: Schlüsselbegriff -> betroffener Prüfbereich
# ---------------------------------------------------------------------------
#
# "pruefbereiche" nennt die Module/Analyzer, die bei einer Änderung angefasst
# werden müssen (Dateinamen unter seo_autopilot/analyzers/ bzw. sources/).
# "gewicht" ist die Grundrelevanz EINER Meldung zu genau diesem Thema; sie
# steigt auf "hoch", sobald die Meldung von Google selbst kommt oder mehrere
# Themen trifft (siehe _bewerte_relevanz).


@dataclass(frozen=True)
class Thema:
    """Ein überwachtes Richtlinien-Thema."""

    schluessel: str
    bezeichnung: str
    begriffe: Tuple[str, ...]
    pruefbereiche: Tuple[str, ...]
    gewicht: str = RELEVANZ_MITTEL
    hinweis: str = ""


THEMEN: Tuple[Thema, ...] = (
    Thema(
        schluessel="core_web_vitals",
        bezeichnung="Ladeleistung / Core Web Vitals",
        begriffe=(
            "core web vitals",
            "web vitals",
            "inp",
            "interaction to next paint",
            "largest contentful paint",
            "lcp",
            "cumulative layout shift",
            "cls",
            "page experience",
            "pagespeed",
            "page speed",
            "ladezeit",
        ),
        pruefbereiche=("pagespeed", "core_web_vitals"),
        gewicht=RELEVANZ_MITTEL,
        hinweis="Schwellwerte und Kennzahlen der Geschwindigkeitsprüfung abgleichen.",
    ),
    Thema(
        schluessel="ki_suche",
        bezeichnung="KI-Suche (AI Overviews / AI Mode / GEO)",
        begriffe=(
            "ai overview",
            "ai overviews",
            "ai mode",
            "generative search",
            "search generative experience",
            "sge",
            "geo",
            "generative engine optimization",
            "ai search",
            "chatgpt search",
            "perplexity",
            "ki-suche",
        ),
        pruefbereiche=("geo_audit", "llms_ai_txt"),
        gewicht=RELEVANZ_MITTEL,
        hinweis="Zitierfähigkeit und llms.txt-Regeln nachziehen.",
    ),
    Thema(
        schluessel="ki_crawler",
        bezeichnung="KI-Crawler und Zugriffsregeln",
        begriffe=(
            "gptbot",
            "claudebot",
            "oai-searchbot",
            "perplexitybot",
            "google-extended",
            "ccbot",
            "ai crawler",
            "crawler",
            "crawling",
            "robots.txt",
            "user-agent",
        ),
        pruefbereiche=("robots_sitemap",),
        gewicht=RELEVANZ_MITTEL,
        hinweis="robots.txt-Regeln für KI-Bots prüfen (erlauben oder sperren?).",
    ),
    Thema(
        schluessel="strukturierte_daten",
        bezeichnung="Strukturierte Daten / Rich Results",
        begriffe=(
            "structured data",
            "strukturierte daten",
            "rich result",
            "rich results",
            "rich snippet",
            "schema.org",
            "json-ld",
            "merchant listing",
            "produktauszeichnung",
        ),
        pruefbereiche=("schema_validation",),
        gewicht=RELEVANZ_MITTEL,
        hinweis="Pflichtfelder der betroffenen Schema-Typen im Validator anpassen.",
    ),
    Thema(
        schluessel="inhaltsqualitaet",
        bezeichnung="Inhaltsqualität (Helpful Content / E-E-A-T / Spam)",
        begriffe=(
            "helpful content",
            "e-e-a-t",
            "eeat",
            "e-a-t",
            "spam policy",
            "spam policies",
            "spam update",
            "site reputation abuse",
            "scaled content abuse",
            "quality rater",
            "hilfreiche inhalte",
        ),
        pruefbereiche=("eeat",),
        gewicht=RELEVANZ_MITTEL,
        hinweis="Signale für Autorenschaft, Erfahrung und Vertrauen nachschärfen.",
    ),
    Thema(
        schluessel="doppelte_inhalte",
        bezeichnung="Kanonisierung / doppelte Inhalte",
        begriffe=(
            "canonical",
            "kanonisch",
            "duplicate content",
            "duplicate",
            "doppelte inhalte",
        ),
        pruefbereiche=("canonical_engine", "duplicate_content"),
        gewicht=RELEVANZ_MITTEL,
        hinweis="Regeln zur Auswahl der Vorzugsadresse gegenprüfen.",
    ),
    Thema(
        schluessel="indexierung",
        bezeichnung="Indexierung / Sitemaps",
        begriffe=(
            "sitemap",
            "sitemaps",
            "indexing",
            "indexierung",
            "deindex",
            "noindex",
            "crawl budget",
            "url inspection",
            "index coverage",
        ),
        pruefbereiche=("robots_sitemap",),
        gewicht=RELEVANZ_NIEDRIG,
        hinweis="Sitemap- und Indexierbarkeitsprüfung auf neue Vorgaben abklopfen.",
    ),
    Thema(
        schluessel="ranking_update",
        bezeichnung="Ranking-/Core-Update",
        begriffe=(
            "core update",
            "broad core update",
            "algorithm update",
            "ranking update",
            "search ranking update",
            "core-update",
        ),
        pruefbereiche=("eeat", "topical_authority"),
        gewicht=RELEVANZ_MITTEL,
        hinweis="Nach dem Ausrollen Scores und Sichtbarkeit der Projekte vergleichen.",
    ),
)

THEMEN_NACH_SCHLUESSEL: Dict[str, Thema] = {t.schluessel: t for t in THEMEN}


# Quellen, die Google SELBST betreibt. Google News (news.google.com) gehört
# ausdrücklich NICHT dazu — das ist nur ein Aggregator fremder Artikel.
GOOGLE_QUELLEN = {
    "google_search_central",
    "google_status",
    "google_webmaster_central",
    "google_developers",
}

GOOGLE_HOSTS = (
    "developers.google.com",
    "developer.chrome.com",
    "blog.google",
    "googleblog.com",
    "status.search.google.com",
    "search.google.com",
    "web.dev",
)


@dataclass
class RadarTreffer:
    """Eine Meldung, die mindestens ein Richtlinien-Thema berührt."""

    titel: str
    quelle: str
    datum: Optional[datetime] = None
    url: str = ""
    themen: List[str] = field(default_factory=list)
    pruefbereiche: List[str] = field(default_factory=list)
    relevanz: str = RELEVANZ_NIEDRIG
    begriffe: List[str] = field(default_factory=list)
    begruendung: str = ""
    von_google: bool = False

    @property
    def themen_bezeichnungen(self) -> List[str]:
        return [
            THEMEN_NACH_SCHLUESSEL[s].bezeichnung
            for s in self.themen
            if s in THEMEN_NACH_SCHLUESSEL
        ]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "titel": self.titel,
            "quelle": self.quelle,
            "datum": self.datum.isoformat() if self.datum else None,
            "url": self.url,
            "themen": list(self.themen),
            "pruefbereiche": list(self.pruefbereiche),
            "relevanz": self.relevanz,
            "begriffe": list(self.begriffe),
            "begruendung": self.begruendung,
            "von_google": self.von_google,
        }


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _baue_muster(begriff: str) -> Pattern[str]:
    """Baut ein Suchmuster mit Wortgrenzen.

    Wortgrenzen sind Pflicht: sonst findet "inp" auch in "input" und "geo"
    in "geography". Leerzeichen dürfen im Text auch Bindestriche sein
    ("core web vitals" == "core-web-vitals").
    """
    teile = [re.escape(t) for t in str(begriff).split()]
    kern = r"[\s\-]+".join(teile)
    return re.compile(rf"(?<!\w){kern}(?!\w)", re.IGNORECASE)


# Einmal beim Import übersetzen — pro Meldung neu zu kompilieren wäre teuer.
_MUSTER: Tuple[Tuple[Thema, str, Pattern[str]], ...] = tuple(
    (thema, begriff, _baue_muster(begriff))
    for thema in THEMEN
    for begriff in thema.begriffe
)


def _als_text(wert: Any) -> str:
    """Macht aus irgendetwas einen durchsuchbaren String — ohne zu knallen."""
    if wert is None:
        return ""
    if isinstance(wert, str):
        return wert
    try:
        return str(wert)
    except Exception:  # pragma: no cover - extrem exotische Objekte
        return ""


def _feld(eintrag: Any, *namen: str) -> Any:
    """Holt das erste vorhandene Feld — egal ob dict oder Objekt (FeedItem)."""
    for name in namen:
        try:
            if isinstance(eintrag, dict):
                if name in eintrag and eintrag[name] is not None:
                    return eintrag[name]
            else:
                wert = getattr(eintrag, name, None)
                if wert is not None:
                    return wert
        except Exception:  # pragma: no cover - kaputte __getattr__-Implementierungen
            continue
    return None


def _als_datum(wert: Any) -> Optional[datetime]:
    """Wandelt Datumsangaben in ein zeitzonenbewusstes datetime — oder None."""
    if wert is None:
        return None
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _ist_google_quelle(quelle: str, url: str) -> bool:
    """Stammt die Meldung von Google selbst (nicht von Google News)?"""
    quelle_klein = (quelle or "").lower()
    url_klein = (url or "").lower()
    if "news.google.com" in url_klein or quelle_klein.startswith("google_news"):
        return False
    if quelle_klein in GOOGLE_QUELLEN:
        return True
    return any(host in url_klein for host in GOOGLE_HOSTS)


def _bewerte_relevanz(themen: List[Thema], von_google: bool) -> str:
    """Hoch = von Google selbst ODER mehrere Themen betroffen.

    Sonst zählt das stärkste Einzelgewicht der getroffenen Themen.
    """
    if not themen:
        return RELEVANZ_NIEDRIG
    if von_google or len(themen) > 1:
        return RELEVANZ_HOCH
    return themen[0].gewicht


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------


def erkenne_themen(text: str) -> Tuple[List[Thema], List[str]]:
    """Findet alle Themen in einem Text.

    Rückgabe: (Themen in Reihenfolge der Landkarte, gefundene Schlüsselbegriffe).
    """
    text = _als_text(text)
    if not text.strip():
        return [], []

    getroffen: Dict[str, Thema] = {}
    begriffe: List[str] = []
    for thema, begriff, muster in _MUSTER:
        if muster.search(text):
            getroffen.setdefault(thema.schluessel, thema)
            if begriff not in begriffe:
                begriffe.append(begriff)
    return list(getroffen.values()), begriffe


def analysiere_meldung(eintrag: Any) -> Optional[RadarTreffer]:
    """Wertet eine einzelne Feed-Meldung aus.

    Gibt None zurück, wenn der Eintrag unbrauchbar ist oder kein Thema trifft.
    Wirft nie eine Ausnahme.
    """
    try:
        titel = _als_text(_feld(eintrag, "title", "titel"))
        zusammenfassung = _als_text(_feld(eintrag, "summary", "description", "text"))
        quelle = _als_text(_feld(eintrag, "source", "quelle", "feed")) or "unbekannt"
        url = _als_text(_feld(eintrag, "url", "link", "href"))
        datum = _als_datum(_feld(eintrag, "published", "datum", "date", "updated"))

        if not titel.strip() and not zusammenfassung.strip():
            return None

        themen, begriffe = erkenne_themen(f"{titel} {zusammenfassung}")
        if not themen:
            return None

        von_google = _ist_google_quelle(quelle, url)
        relevanz = _bewerte_relevanz(themen, von_google)

        pruefbereiche: List[str] = []
        for thema in themen:
            for bereich in thema.pruefbereiche:
                if bereich not in pruefbereiche:
                    pruefbereiche.append(bereich)

        if von_google:
            begruendung = "Von Google selbst veröffentlicht"
        elif len(themen) > 1:
            begruendung = f"Trifft {len(themen)} Themen gleichzeitig"
        else:
            begruendung = themen[0].hinweis.rstrip(".") or "Einzelthema aus Fremdquelle"

        return RadarTreffer(
            titel=titel.strip() or "(ohne Titel)",
            quelle=quelle,
            datum=datum,
            url=url,
            themen=[t.schluessel for t in themen],
            pruefbereiche=pruefbereiche,
            relevanz=relevanz,
            begriffe=begriffe,
            begruendung=begruendung,
            von_google=von_google,
        )
    except Exception as exc:  # Das Radar darf den Bericht nie killen
        logger.warning(f"[radar] Meldung nicht auswertbar, übersprungen: {exc}")
        return None


def analysiere_meldungen(
    eintraege: Any,
    max_alter_tage: Optional[int] = None,
    jetzt: Optional[datetime] = None,
) -> List[RadarTreffer]:
    """Wertet eine Liste von Feed-Meldungen aus.

    `eintraege` darf alles sein, was der Feed hergibt: FeedItem-Objekte,
    dicts, None, kaputte Mischungen. Unbrauchbares wird übersprungen.

    `max_alter_tage` filtert alte Meldungen weg. Einträge OHNE Datum bleiben
    drin — im Zweifel lieber eine Meldung zu viel als eine übersehene.

    Rückgabe: Treffer, sortiert nach Relevanz und dann nach Datum (neu zuerst).
    """
    if not eintraege:
        return []
    if isinstance(eintraege, (str, bytes, dict)):
        # Ein einzelnes Objekt statt einer Liste — freundlich behandeln.
        eintraege = [eintraege]
    if not isinstance(eintraege, Iterable):
        logger.warning("[radar] Eingabe ist nicht iterierbar — nichts auszuwerten")
        return []

    grenze: Optional[datetime] = None
    if max_alter_tage is not None:
        try:
            bezug = jetzt or datetime.now(timezone.utc)
            if bezug.tzinfo is None:
                bezug = bezug.replace(tzinfo=timezone.utc)
            grenze = bezug - timedelta(days=int(max_alter_tage))
        except (TypeError, ValueError):
            grenze = None

    treffer: List[RadarTreffer] = []
    try:
        liste = list(eintraege)
    except Exception as exc:  # pragma: no cover - kaputte Generatoren
        logger.warning(f"[radar] Eingabe nicht lesbar: {exc}")
        return []

    for eintrag in liste:
        if eintrag is None:
            continue
        einzel = analysiere_meldung(eintrag)
        if einzel is None:
            continue
        if grenze is not None and einzel.datum is not None and einzel.datum < grenze:
            continue
        treffer.append(einzel)

    treffer.sort(
        key=lambda t: (
            RELEVANZ_REIHENFOLGE.get(t.relevanz, 9),
            -(t.datum or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            t.titel,
        )
    )
    logger.info(f"[radar] {len(treffer)} von {len(liste)} Meldungen sind relevant")
    return treffer


def betroffene_pruefbereiche(treffer: List[RadarTreffer]) -> List[Tuple[str, int]]:
    """Zählt, wie oft jeder Prüfbereich betroffen ist (häufigster zuerst)."""
    zaehler: Dict[str, int] = {}
    for t in treffer or []:
        for bereich in getattr(t, "pruefbereiche", None) or []:
            zaehler[bereich] = zaehler.get(bereich, 0) + 1
    return sorted(zaehler.items(), key=lambda p: (-p[1], p[0]))


def radar_zusammenfassung(
    treffer: Optional[List[RadarTreffer]], max_eintraege: int = 10
) -> str:
    """Baut den lesbaren Klartext für Bericht und Telegram.

    Bewusst ohne Markup: derselbe Text soll im Terminal, im Bericht und in
    einer Telegram-Nachricht funktionieren.
    """
    treffer = list(treffer or [])
    if not treffer:
        return (
            "Richtlinien-Radar: keine relevanten Richtlinien-Meldungen gefunden.\n"
            "Kein Handlungsbedarf — die überwachten Themen (Core Web Vitals, "
            "KI-Suche, KI-Crawler, strukturierte Daten, Inhaltsqualität, "
            "Kanonisierung, Indexierung, Ranking-Updates) sind ruhig."
        )

    try:
        hohe = [t for t in treffer if t.relevanz == RELEVANZ_HOCH]
        zeilen = [
            f"Richtlinien-Radar: {len(treffer)} relevante Meldung(en), "
            f"davon {len(hohe)} mit hoher Relevanz."
        ]

        bereiche = betroffene_pruefbereiche(treffer)
        if bereiche:
            zeilen.append("")
            zeilen.append("Betroffene Prüfbereiche (häufigster zuerst):")
            for name, zahl in bereiche:
                zeilen.append(f"  - {name}: {zahl} Meldung(en)")

        symbole = {
            RELEVANZ_HOCH: "[!]",
            RELEVANZ_MITTEL: "[~]",
            RELEVANZ_NIEDRIG: "[i]",
        }
        grenze = max(int(max_eintraege or 0), 0)
        gezeigt = treffer[:grenze] if grenze else []

        if gezeigt:
            zeilen.append("")
            zeilen.append("Meldungen:")
        for t in gezeigt:
            datum = f"{t.datum:%Y-%m-%d}" if t.datum else "ohne Datum"
            zeilen.append(
                f"{symbole.get(t.relevanz, '[?]')} {t.titel} "
                f"({t.quelle}, {datum}) — Relevanz {t.relevanz}"
            )
            themen = ", ".join(t.themen_bezeichnungen) or "-"
            zeilen.append(f"      Thema: {themen}")
            zeilen.append(
                f"      → prüfen: {', '.join(t.pruefbereiche) or '-'} "
                f"({t.begruendung})"
            )
            if t.url:
                zeilen.append(f"      {t.url}")

        rest = len(treffer) - len(gezeigt)
        if rest > 0:
            zeilen.append("")
            zeilen.append(f"... {rest} weitere Meldung(en) nicht aufgeführt.")

        return "\n".join(zeilen)
    except Exception as exc:  # pragma: no cover - Zusammenfassung darf nie knallen
        logger.warning(f"[radar] Zusammenfassung fehlgeschlagen: {exc}")
        return "Richtlinien-Radar: Auswertung nicht möglich."
