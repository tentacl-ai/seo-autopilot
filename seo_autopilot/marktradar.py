"""
Marktbeobachter — jeden Tag nachsehen, was sich bei SEO, SEA, KI-Suche und Messung tut.

Robert (16.09.2026): "dass da so ein Typ sitzt, der jeden Tag schaut, gibt es
irgendwelche Neuerungen im SEO-Bereich, im SEA-Bereich ... dass da neue Impulse
reinfliessen."

Zwei Wege, beide landen in derselben Tabelle `markt_meldungen`:

1. **Fachquellen (RSS)** — Google Search Central, Google Ads, Google Analytics,
   Bing Webmaster, Microsoft Ads, Search Engine Land/Journal, PPC Land,
   SE Roundtable u. a. (`sources/intelligence.py`). Einordnung über das
   Richtlinien-Radar (`policy_radar.py`).
2. **KI-Späher** — ChatGPT und Gemini recherchieren MIT Websuche die Neuerungen
   der letzten Tage. KIs erfinden gern Quellen (die alte Lovable-Mail bei Beispiel-Beratung
   hat "LCP < 2,0 s" und "llm.txt v2.0" frei erfunden). Deshalb gilt hart:
   **ohne erreichbare Quell-Adresse keine Meldung.** Jede genannte URL wird
   abgerufen; was nicht mit Status < 400 antwortet, fliegt raus.

Grundregel wie beim Radar: der Beobachter darf den Lauf nie abbrechen. Fällt eine
Quelle oder eine KI aus, wird das gezählt und gemeldet, der Rest läuft weiter.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .policy_radar import RELEVANZ_MITTEL, RELEVANZ_REIHENFOLGE, analysiere_meldung

logger = logging.getLogger(__name__)

TABELLE = "markt_meldungen"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# Adressen, die nur Weiterleitungen oder Suchseiten sind - keine echte Quelle.
KEINE_QUELLE = (
    "vertexaisearch.cloud.google.com",
    "google.com/search",
    "bing.com/search",
)

KI_AUFTRAG = """Recherchiere im Web die wichtigsten NEUERUNGEN der letzten 7 Tage (heute ist {heute}) zu:
- Google-Suche und SEO (Updates, neue Richtlinien, Search Console)
- KI-Suche (Google AI Overviews / AI Mode, ChatGPT Search, Perplexity, Copilot)
- Bezahlte Suche (Google Ads, Microsoft Ads)
- Messung (Google Analytics 4, Consent Mode, Bing Webmaster Tools)
- Lokale Suche (Google-Unternehmensprofil)

Nur tatsaechlich veroeffentlichte Ankuendigungen oder Fachartikel mit DIREKTER Quell-URL zum Artikel.
Keine allgemeinen Tipps, keine Vermutungen, keine Startseiten als Quelle."""

KI_FORMAT = """Antworte NUR mit einem JSON-Array, ohne Text davor oder danach, hoechstens 8 Eintraege:
[{"titel": "...", "datum": "JJJJ-MM-TT", "url": "https://...", "was_neu": "1-2 Saetze auf Deutsch", "bereich": "SEO|KI-Suche|SEA|Analytics|Lokal"}]"""


@dataclass
class Sammelergebnis:
    """Was ein Lauf gebracht hat - fuer Log, Selbstpruefung und Bericht."""

    feeds_gelesen: int = 0
    feed_treffer: int = 0
    ki_vorschlaege: int = 0
    ki_verworfen: int = 0
    neu: int = 0
    fehler: List[str] = field(default_factory=list)

    def als_text(self) -> str:
        teile = [
            f"Marktbeobachter: {self.neu} neue Meldung(en)",
            f"Fachquellen: {self.feeds_gelesen} Einträge gelesen, {self.feed_treffer} themenrelevant",
            f"KI-Späher: {self.ki_vorschlaege} Vorschläge, {self.ki_verworfen} ohne erreichbare Quelle verworfen",
        ]
        if self.fehler:
            teile.append("Ausfälle: " + "; ".join(self.fehler))
        return "\n".join(teile)


# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------


def tabelle_anlegen(con: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS - bewusst ohne alembic (env.py ist async verdrahtet)."""
    con.execute(f"""create table if not exists {TABELLE} (
            url text primary key,
            titel text not null,
            quelle text,
            via text,
            datum text,
            was_neu text,
            themen text,
            relevanz text,
            gefunden_am text not null
        )""")
    con.commit()


def speichern(con: sqlite3.Connection, meldungen: Iterable[Dict[str, Any]]) -> int:
    """Legt neue Meldungen ab; dieselbe URL wird nie doppelt gezaehlt."""
    tabelle_anlegen(con)
    neu = 0
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for m in meldungen:
        url = _normalisiere_url(m.get("url", ""))
        if not url or not m.get("titel"):
            continue
        cur = con.execute(
            f"insert or ignore into {TABELLE} "
            "(url, titel, quelle, via, datum, was_neu, themen, relevanz, gefunden_am) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                url,
                str(m["titel"])[:300],
                str(m.get("quelle") or "")[:80],
                str(m.get("via") or "")[:40],
                str(m.get("datum") or "")[:10],
                str(m.get("was_neu") or "")[:600],
                json.dumps(m.get("themen") or [], ensure_ascii=False),
                m.get("relevanz") or RELEVANZ_MITTEL,
                jetzt,
            ),
        )
        neu += cur.rowcount
    con.commit()
    return neu


def neueste(
    db_pfad: str,
    tage: int = 7,
    min_relevanz: str = RELEVANZ_MITTEL,
    jetzt: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Meldungen der letzten Tage ab einer Mindest-Relevanz, wichtigste zuerst."""
    grenze = ((jetzt or datetime.now(timezone.utc)) - timedelta(days=tage)).isoformat()
    try:
        con = sqlite3.connect(db_pfad)
        con.row_factory = sqlite3.Row
        tabelle_anlegen(con)
        rows = con.execute(
            f"select * from {TABELLE} where gefunden_am >= ? order by gefunden_am desc",
            (grenze,),
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.warning(f"[Marktradar] Datenbank nicht lesbar: {exc}")
        return []
    schwelle = RELEVANZ_REIHENFOLGE.get(min_relevanz, 1)
    aus = []
    for r in rows:
        d = dict(r)
        if RELEVANZ_REIHENFOLGE.get(d.get("relevanz"), 2) > schwelle:
            continue
        d["themen"] = json.loads(d.get("themen") or "[]")
        aus.append(d)
    return sorted(aus, key=lambda d: RELEVANZ_REIHENFOLGE.get(d["relevanz"], 2))


# ---------------------------------------------------------------------------
# Weg 1: Fachquellen
# ---------------------------------------------------------------------------


def aus_feeds(
    eintraege: Iterable[Any], max_alter_tage: int = 10
) -> List[Dict[str, Any]]:
    """Feed-Eintraege -> Meldungen (nur themenrelevante, nur juengere)."""
    grenze = datetime.now(timezone.utc) - timedelta(days=max_alter_tage)
    aus = []
    for e in eintraege:
        t = analysiere_meldung(e)
        if not t or not t.url:
            continue
        if t.datum and t.datum.astimezone(timezone.utc) < grenze:
            continue
        zusammenfassung = getattr(e, "summary", None) or (
            e.get("summary") if isinstance(e, dict) else ""
        )
        aus.append(
            {
                "url": t.url,
                "titel": t.titel,
                "quelle": t.quelle,
                "via": "Fachquelle",
                "datum": t.datum.date().isoformat() if t.datum else "",
                "was_neu": re.sub(r"<[^>]+>", " ", str(zusammenfassung or ""))[:600],
                "themen": t.themen,
                "relevanz": t.relevanz,
            }
        )
    return aus


def feeds_holen() -> List[Any]:
    from .sources.intelligence import IntelligenceFeed

    feed = IntelligenceFeed()
    if not feed.available:
        raise RuntimeError("feedparser nicht installiert")
    return feed.poll_feeds()


# ---------------------------------------------------------------------------
# Weg 2: KI-Spaeher
# ---------------------------------------------------------------------------


def json_liste_aus_text(text: str) -> List[Dict[str, Any]]:
    """Holt das JSON-Array aus einer KI-Antwort (auch mit ```json-Zaun oder Vortext)."""
    if not text:
        return []
    start, ende = text.find("["), text.rfind("]")
    if start < 0 or ende <= start:
        return []
    try:
        daten = json.loads(text[start : ende + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in daten if isinstance(d, dict)] if isinstance(daten, list) else []


def _normalisiere_url(url: str) -> str:
    url = (url or "").strip()
    url = re.sub(r"[?&]utm_[^&#]+", "", url)
    return url.rstrip("?&")


def quelle_pruefen(
    url: str, abrufen: Optional[Callable[[str], Any]] = None
) -> Optional[str]:
    """Gibt die endgueltige Adresse zurueck, wenn die Quelle echt erreichbar ist, sonst None."""
    url = _normalisiere_url(url)
    if not url.startswith(("http://", "https://")) or any(
        k in url for k in KEINE_QUELLE
    ):
        return None
    # Eine Startseite ist keine Quelle fuer eine Neuerung
    if re.fullmatch(r"https?://[^/]+/?", url):
        return None
    if abrufen is None:
        import httpx

        def abrufen(u: str):
            return httpx.get(
                u,
                follow_redirects=True,
                timeout=15.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (tentacl SEO-Autopilot Marktbeobachter)"
                },
            )

    try:
        antwort = abrufen(url)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - Netz faellt aus, Quelle gilt dann als nicht belegt
        logger.info(f"[Marktradar] Quelle nicht erreichbar {url}: {exc}")
        return None
    if getattr(antwort, "status_code", 999) >= 400:
        return None
    return _normalisiere_url(str(getattr(antwort, "url", url)))


def aus_ki(
    spaeher: Dict[str, Callable[..., Dict[str, Any]]],
    heute: Optional[date] = None,
    pruefer: Callable[[str], Optional[str]] = quelle_pruefen,
    ergebnis: Optional[Sammelergebnis] = None,
) -> List[Dict[str, Any]]:
    """Fragt jede KI nach Neuerungen und behaelt nur Eintraege mit echter Quelle."""
    ergebnis = ergebnis or Sammelergebnis()
    auftrag = KI_AUFTRAG.format(heute=(heute or date.today()).isoformat())
    aus: Dict[str, Dict[str, Any]] = {}
    for name, fragen in spaeher.items():
        try:
            antwort = fragen(auftrag, KI_FORMAT)
        except Exception as exc:  # noqa: BLE001 - eine KI darf ausfallen
            ergebnis.fehler.append(f"{name}: {type(exc).__name__}")
            continue
        for eintrag in json_liste_aus_text(antwort.get("text", "")):
            ergebnis.ki_vorschlaege += 1
            echte_url = pruefer(str(eintrag.get("url", "")))
            if not echte_url or not eintrag.get("titel"):
                ergebnis.ki_verworfen += 1
                continue
            t = analysiere_meldung(
                {
                    "title": eintrag["titel"],
                    "summary": eintrag.get("was_neu", ""),
                    "source": name,
                    "url": echte_url,
                }
            )
            vorhanden = aus.get(echte_url)
            if vorhanden:
                vorhanden["via"] = (
                    f"{vorhanden['via']}+{name}"  # zwei KIs nennen dasselbe = staerkeres Signal
                )
                continue
            aus[echte_url] = {
                "url": echte_url,
                "titel": eintrag["titel"],
                "quelle": re.sub(r"^https?://(www\.)?", "", echte_url).split("/")[0],
                "via": name,
                "datum": str(eintrag.get("datum") or "")[:10],
                "was_neu": eintrag.get("was_neu", ""),
                "themen": t.themen if t else [str(eintrag.get("bereich") or "")],
                # Die KI hat vorsortiert; ohne Themen-Treffer trotzdem "mittel", nie "hoch"
                "relevanz": t.relevanz if t else RELEVANZ_MITTEL,
            }
    return list(aus.values())


def standard_spaeher() -> Dict[str, Callable[..., Dict[str, Any]]]:
    """ChatGPT + Gemini mit Websuche - dieselben Zugaenge wie der KI-Sichtbarkeitstest."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from ki_sichtbarkeit import frage_chatgpt, frage_gemini  # noqa: E402

    return {"ChatGPT": frage_chatgpt, "Gemini": frage_gemini}


# ---------------------------------------------------------------------------
# Tageslauf
# ---------------------------------------------------------------------------


def sammeln(db_pfad: str, mit_ki: bool = True) -> Sammelergebnis:
    ergebnis = Sammelergebnis()
    meldungen: List[Dict[str, Any]] = []
    try:
        eintraege = feeds_holen()
        ergebnis.feeds_gelesen = len(eintraege)
        treffer = aus_feeds(eintraege)
        ergebnis.feed_treffer = len(treffer)
        meldungen += treffer
    except Exception as exc:  # noqa: BLE001
        ergebnis.fehler.append(f"Fachquellen: {type(exc).__name__}: {str(exc)[:80]}")
    if mit_ki:
        try:
            meldungen += aus_ki(standard_spaeher(), ergebnis=ergebnis)
        except Exception as exc:  # noqa: BLE001
            ergebnis.fehler.append(f"KI-Späher: {type(exc).__name__}: {str(exc)[:80]}")
    con = sqlite3.connect(db_pfad)
    try:
        ergebnis.neu = speichern(con, meldungen)
    finally:
        con.close()
    logger.info(f"[Marktradar] {ergebnis.als_text()}")
    return ergebnis
