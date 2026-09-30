"""
Empfehlungen je Seite — Stufe 3 des SEO-Autopiloten (18.09.2026).

Robert: "Es sollte auch Empfehlungen geben, wie z. B. FAQs hinzufuegen oder was
on-page gemacht werden kann." Bis hierher gab es nur feste englische Einzeiler
("Add lists, tables, or FAQ sections.", 1.404x gespeichert), fuer jede Seite
gleich. Dieses Modul liefert stattdessen konkrete Vorschlaege JE SEITE:

  faq_ergaenzen            Fragen, die Suchende wirklich stellen, mit Antwortentwurf
  abschnitt_ergaenzen      Thema mit Einblendungen, das die Seite kaum behandelt
  ueberschrift_verbessern  H1 nennt den Hauptsuchbegriff nicht
  interne_links            Position 8-20: welche eigene Seite mit welchem Wort verlinken
  antwort_zuerst           erster Absatz beantwortet die Hauptfrage nicht
  neue_seite               Suchbegriffe ohne passende Seite (projektweit)
  titel_beschreibung       gute Position, schwache Klickrate -> Verweis auf Handwerker

Datengrundlage je Seite:
  1. Search Console Suchbegriff x Seite, letzte 90 Tage (`pull_range`)
  2. Seiteninhalt (Handwerker-Seitenkontext + H2/H3, erster Absatz, FAQ)
  3. Claude ueber `abo_ki` (Roberts Abo, NIE die bezahlte API) mit strengem Prompt

Was die KI liefert, wird mit den Handwerker-Regeln geprueft (keine erfundenen
Zahlen/Auszeichnungen, Tabuwoerter, kein HTML, Sie/du wie die Seite). Verworfen
heisst: nicht gespeichert. HTML-Fragmente und JSON-LD baut der Code, nie die KI.

Kosten: hoechstens `source_config.empfehlungen.max_seiten` (Standard 6) KI-Aufrufe
je Projekt und Lauf, Seiten nach Einblendungen. Ein Inhalts-Hash je Seite (Text +
Suchbegriffe) verhindert, dass Unveraendertes neu berechnet wird — dadurch rotiert
der Lauf von selbst ueber die Seiten.

Umsetzen (sichtbarer Text) steht in `empfehlungen_umsetzen.py`.

Grundsatz (Google-Leitfaden "Optimizing your website for generative AI features
on Google Search", 10.07.2026): Jede Empfehlung begruendet sich aus echter
Nachfrage (Search-Console-Suchbegriffe bzw. echte Fragen). Ohne Suchdaten gibt
es keine Empfehlung; "besser fuer KI/GEO" ist nie ein Grund. FAQPage-JSON-LD
wird nicht mehr vorgeschlagen: Google hat FAQ-Rich-Results am 07.05.2026 ganz
abgeschaltet, der Nutzen liegt allein im sichtbaren Text.

Warum `intent_geo_agent` nie einen Befund lieferte (Befund 18.09.2026)
----------------------------------------------------------------------
Drei Ursachen, jede fuer sich toedlich (cron.log: 470x "Intent/GEO: no GSC
keywords available, skipped"):
  1. `AnalyzerAgent` sucht `context.gsc_keywords` — dieses Feld gibt es im
     AuditContext nicht, und projects.yaml hat kein `source_config.gsc_keywords`.
  2. Der Analyzer laeuft VOR dem KeywordAgent; Suchdaten gab es zu dem
     Zeitpunkt ohnehin noch nicht.
  3. Selbst mit Daten: `top_queries` tragen `query` statt `keyword` und keine
     `url` — jede Analyse waere an KeyError/leerer Seite gescheitert. Vor dem
     16.09. fehlte ausserdem CLAUDE_API_KEY im Cron.
Dieses Modul ersetzt den Zweck (Suchabsicht, Inhaltsluecken) je Seite.

Betrieb: eigener CLI-Befehl statt Schritt im Audit (KI-Aufrufe dauern, ein
Fehler soll kein Audit kippen, der Cache macht taegliche Laeufe billig).
Cron-Vorschlag (nach Audits, Historie, Waechter und Wirkungsmessung):
  0 12 * * * cd <Installationsordner> && venv/bin/python3 -m
      seo_autopilot.cli.main empfehlungen --erzeugen --umsetzen
      >> logs/empfehlungen.log 2>&1
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_mod
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from . import handwerker as hw

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

TABELLE = "empfehlungen"
TABELLE_SEITEN = "empfehlungen_seiten"

ART_FAQ = "faq_ergaenzen"
ART_ABSCHNITT = "abschnitt_ergaenzen"
ART_UEBERSCHRIFT = "ueberschrift_verbessern"
ART_LINKS = "interne_links"
ART_ANTWORT = "antwort_zuerst"
ART_NEUE_SEITE = "neue_seite"
ART_TITEL = "titel_beschreibung"
ARTEN = (
    ART_FAQ,
    ART_ABSCHNITT,
    ART_UEBERSCHRIFT,
    ART_LINKS,
    ART_ANTWORT,
    ART_NEUE_SEITE,
    ART_TITEL,
)
# Arten, fuer die es einen anwendbaren Aenderungsvorschlag geben kann
ANWENDBARE_ARTEN = (ART_FAQ, ART_ABSCHNITT, ART_UEBERSCHRIFT, ART_LINKS, ART_ANTWORT)

ART_KLARTEXT = {
    ART_FAQ: "Häufige Fragen ergänzen",
    ART_ABSCHNITT: "Abschnitt ergänzen",
    ART_UEBERSCHRIFT: "Hauptüberschrift schärfen",
    ART_LINKS: "Interne Links setzen",
    ART_ANTWORT: "Ersten Absatz als Antwort formulieren",
    ART_NEUE_SEITE: "Neue Seite anlegen",
    ART_TITEL: "Titel und Kurzbeschreibung",
}

# Gewicht je Art fuer die Arbeitsliste (Nutzen = Einblendungen x Potenzial x Gewicht)
GEWICHT = {
    ART_FAQ: 1.0,
    ART_ABSCHNITT: 0.9,
    ART_ANTWORT: 0.8,
    ART_UEBERSCHRIFT: 0.7,
    ART_LINKS: 0.6,
    ART_NEUE_SEITE: 0.5,
    ART_TITEL: 0.3,
}

STATUS_OFFEN = "offen"
STATUS_FREIGEGEBEN = "freigegeben"  # per Knopf "Ja" (Copilot) - darf umgesetzt werden
STATUS_ABGELEHNT = "abgelehnt"
STATUS_UMGESETZT = "umgesetzt"
STATUS_NICHT_BEHEBBAR = "nicht_behebbar"  # von Hand umsetzen
STATUS_PRUEFUNG_NEIN = "pruefung_nein"  # zweiter KI-Pruefdurchgang sagte nein
STATUS_ERSETZT = "ersetzt"  # neuere Empfehlung derselben Art fuer dieselbe Seite
STATUS_KLARTEXT = {
    STATUS_OFFEN: "offen",
    STATUS_FREIGEGEBEN: "freigegeben, wartet auf Umsetzung",
    STATUS_ABGELEHNT: "abgelehnt",
    STATUS_UMGESETZT: "umgesetzt",
    STATUS_NICHT_BEHEBBAR: "von Hand umsetzen",
    STATUS_PRUEFUNG_NEIN: "Prüfung nicht bestanden",
    STATUS_ERSETZT: "ersetzt",
}

PLATZHALTER = "[Antwort vom Betreiber ergänzen]"
# Hochzaehlen, wenn sich die Regeln aendern — dann wird jede Seite neu bewertet
LOGIK_VERSION = "10"
MAX_SEITEN_STANDARD = 6
GSC_TAGE = 90
GSC_VERZUG_TAGE = 3
MIN_EINBLENDUNGEN_BEGRIFF = 5  # darunter ist ein Suchbegriff Rauschen (90 Tage)
LINK_POSITION = (8.0, 20.0)
MIN_EINBLENDUNGEN_LINK = 10
MAX_EIGENE_SEITEN = 60
FAQ_MIN, FAQ_MAX = 2, 6  # nur echte Fragen aus der Search Console

# Rechtsseiten sind immer gesperrt — dort wird weder empfohlen noch geschrieben.
IMMER_GESPERRT = re.compile(
    r"(impressum|datenschutz|privacy|imprint|agb|terms|widerruf|cookie)", re.I
)

FRAGEWOERTER = {
    "wie",
    "was",
    "wo",
    "wann",
    "warum",
    "wieso",
    "weshalb",
    "welche",
    "welcher",
    "welches",
    "wer",
    "wem",
    "wen",
    "wieviel",
    "kann",
    "darf",
    "muss",
    "ist",
    "sind",
    "gibt",
    "lohnt",
    "braucht",
    "how",
    "what",
    "why",
    "when",
    "where",
    "which",
    "who",
    "can",
    "does",
    "is",
    "are",
}
ABSICHTSWOERTER = {
    "kosten",
    "kostet",
    "preis",
    "preise",
    "vergleich",
    "erfahrung",
    "erfahrungen",
    "test",
    "anleitung",
    "voraussetzung",
    "voraussetzungen",
    "unterschied",
    "vorteile",
    "nachteile",
    "dauer",
    "ablauf",
    "beantragen",
    "beispiel",
    "definition",
    "bedeutung",
    "funktioniert",
}
STOPPWOERTER = {
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einen",
    "einem",
    "einer",
    "und",
    "oder",
    "fuer",
    "mit",
    "von",
    "auf",
    "aus",
    "bei",
    "nach",
    "zum",
    "zur",
    "ist",
    "sind",
    "wie",
    "was",
    "wer",
    "wo",
    "wann",
    "warum",
    "welche",
    "welcher",
    "welches",
    "kann",
    "man",
    "sich",
    "ich",
    "mein",
    "the",
    "and",
    "for",
    "with",
    "how",
    "what",
    "near",
    "nahe",
    "beste",
    "bester",
    "online",
    "gibt",
    "mir",
    "mich",
    "im",
    "in",
    "am",
    "an",
    "zu",
    "vs",
}

_SCHEMA = f"""
create table if not exists {TABELLE} (
    id text primary key,
    project_id text not null,
    seite text not null default '',
    art text not null,
    erstellt_am text not null,
    cache_schluessel text,
    prioritaet real default 0,
    titel text,
    text text,
    vorschlag text,
    suchbegriffe text,
    ohne_suchdaten integer default 0,
    status text not null default '{STATUS_OFFEN}',
    entschieden_am text,
    umgesetzt_am text,
    geaenderte_seite text,
    change_id text,
    git_commit text,
    notiz text
);
create index if not exists idx_{TABELLE}_projekt on {TABELLE} (project_id, status);
create table if not exists {TABELLE_SEITEN} (
    project_id text not null,
    seite text not null,
    schluessel text,
    geprueft_am text,
    anzahl integer default 0,
    primary key (project_id, seite)
);
"""

_SPALTEN = (
    "id",
    "project_id",
    "seite",
    "art",
    "erstellt_am",
    "cache_schluessel",
    "prioritaet",
    "titel",
    "text",
    "vorschlag",
    "suchbegriffe",
    "ohne_suchdaten",
    "status",
    "entschieden_am",
    "umgesetzt_am",
    "geaenderte_seite",
    "change_id",
    "git_commit",
    "notiz",
)


@dataclass
class Empfehlung:
    project_id: str
    seite: str
    art: str
    titel: str
    text: str
    vorschlag: Dict[str, Any] = field(default_factory=dict)
    prioritaet: float = 0.0
    suchbegriffe: List[Dict[str, Any]] = field(default_factory=list)
    ohne_suchdaten: bool = False
    cache_schluessel: str = ""
    id: str = ""
    erstellt_am: str = ""
    status: str = STATUS_OFFEN
    entschieden_am: Optional[str] = None
    umgesetzt_am: Optional[str] = None
    geaenderte_seite: Optional[str] = None
    change_id: Optional[str] = None
    git_commit: Optional[str] = None
    notiz: str = ""

    @property
    def anwendbar(self) -> bool:
        return bool((self.vorschlag or {}).get("anwendung"))

    @property
    def status_klartext(self) -> str:
        t = STATUS_KLARTEXT.get(self.status, self.status)
        if self.status == STATUS_UMGESETZT and self.umgesetzt_am:
            t += f" am {self.umgesetzt_am[:10]}"
        return t


# ---------------------------------------------------------------------------
# Text-Hilfen
# ---------------------------------------------------------------------------

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_ZAHL = re.compile(r"\d+(?:[.,]\d+)*")
_WS = re.compile(r"\s+")


def norm(text: str) -> str:
    """Kleinschreibung, Umlaute ausgeschrieben, nur Buchstaben/Ziffern."""
    t = (text or "").lower().translate(_UMLAUTE)
    return " " + re.sub(r"[^a-z0-9]+", " ", t).strip() + " "


def woerter(text: str, marke: Iterable[str] = ()) -> List[str]:
    marke = set(marke)
    return [
        w
        for w in norm(text).split()
        if len(w) >= 3 and w not in STOPPWOERTER and w not in marke
    ]


def _stamm(wort: str) -> str:
    return wort if len(wort) <= 5 else wort[: max(5, len(wort) - 2)]


def abdeckung(begriff: str, text_norm: str, marke: Iterable[str] = ()) -> float:
    """Anteil der Begriffswoerter, die (als Wortstamm) im Text vorkommen."""
    ws = woerter(begriff, marke)
    if not ws:
        return 1.0
    return sum(1 for w in ws if _stamm(w) in text_norm) / len(ws)


def ist_frage(begriff: str) -> bool:
    ws = norm(begriff).split()
    # Mindestens zwei Inhaltswoerter: "ist es normal" ist keine Frage zum Angebot
    if not ws or len(woerter(begriff)) < 2:
        return False
    return (
        "?" in begriff or ws[0] in FRAGEWOERTER or any(w in ABSICHTSWOERTER for w in ws)
    )


def marken_woerter(cfg: Dict[str, Any]) -> List[str]:
    host = urlparse(cfg.get("domain", "")).netloc.removeprefix("www.")
    teile = re.split(r"[.\-]", host.rsplit(".", 1)[0]) if host else []
    teile += norm(cfg.get("name", "")).split()
    return sorted({t for t in teile if len(t) >= 3 and t not in STOPPWOERTER})


def ist_marke(begriff: str, marke: Iterable[str]) -> bool:
    ns = norm(begriff)
    return any(f" {m} " in ns or m in ns.replace(" ", "") for m in marke)


def anrede(text: str) -> Optional[str]:
    """'sie', 'du' oder None — wie spricht die Seite ihre Leser an."""
    du = len(re.findall(r"\b(du|dich|dir|dein|deine|deinen|deinem|deiner)\b", text))
    sie = len(re.findall(r"[a-zäöüß,] (Sie|Ihnen|Ihre|Ihren|Ihrem|Ihrer|Ihr)\b", text))
    if du == 0 and sie == 0:
        return None
    return "du" if du > sie else "sie"


def zahlen(text: str) -> List[str]:
    return [z.replace(".", "").replace(",", "") for z in _ZAHL.findall(text or "")]


def seite_gesperrt(seite: str, regeln: Optional[Dict[str, Any]] = None) -> bool:
    """Rechtsseiten immer, dazu `seo_regeln.gesperrte_seiten` (Pfad-Anfaenge oder URLs)."""
    pfad = urlparse(seite).path if "://" in (seite or "") else (seite or "")
    if IMMER_GESPERRT.search(pfad):
        return True
    for eintrag in (regeln or {}).get("gesperrte_seiten", []) or []:
        e = str(eintrag).strip()
        if not e:
            continue
        e_pfad = urlparse(e).path if "://" in e else e
        if e_pfad in ("", "/"):
            if pfad in ("", "/"):
                return True
            continue
        if pfad.rstrip("/").startswith(e_pfad.rstrip("/")):
            return True
    return False


# ---------------------------------------------------------------------------
# Seiteninhalt
# ---------------------------------------------------------------------------


def _faq_aus_jsonld(roh: str) -> List[str]:
    fragen: List[str] = []

    def suche(o: Any) -> None:
        if isinstance(o, dict):
            typ = o.get("@type")
            if typ == "Question" or (isinstance(typ, list) and "Question" in typ):
                if o.get("name"):
                    fragen.append(str(o["name"]))
            for v in o.values():
                suche(v)
        elif isinstance(o, list):
            for v in o:
                suche(v)

    for block in re.findall(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", roh, re.I | re.S
    ):
        try:
            suche(json.loads(block))
        except ValueError:
            continue
    return fragen


def seiten_struktur(roh: str, seite: str = "") -> Dict[str, Any]:
    """Handwerker-Kontext plus H1-H3, erster Absatz, vorhandene FAQ, Links, Anrede."""
    from bs4 import BeautifulSoup

    k = hw.kontext_aus_html(roh, seite)
    faq_schema = _faq_aus_jsonld(roh)
    soup = BeautifulSoup(roh, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "template"]):
        t.decompose()
    bereich = soup.find("main") or soup.find("article") or soup.body or soup

    def txt(el: Any) -> str:
        return _WS.sub(" ", el.get_text(" ", strip=True)).strip()

    h1 = [txt(h) for h in soup.find_all("h1")]
    h2 = [txt(h) for h in bereich.find_all("h2")]
    h3 = [txt(h) for h in bereich.find_all("h3")]
    faq_sichtbar = [txt(s) for s in bereich.find_all("summary")]
    faq_sichtbar += [t for t in h2 + h3 if t.endswith("?")]
    erster = ""
    for p in bereich.find_all("p"):
        t = txt(p)
        if len(t) >= 40:
            erster = t
            break
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0].split("?")[0]
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        links.add(urljoin(seite or "/", href))
    text_voll = txt(bereich)
    k.update(
        {
            "h1_liste": h1,
            "h2": h2,
            "h3": h3,
            "faq_fragen": faq_schema + faq_sichtbar,
            "hat_faq_schema": bool(faq_schema) or "FAQPage" in roh,
            "erster_absatz": erster,
            "links": sorted(links),
            "text_voll": text_voll,
            "anrede": anrede(text_voll),
        }
    )
    return k


def seiten_hash(s: Dict[str, Any]) -> str:
    roh = "|".join(
        [
            s.get("title", ""),
            s.get("description", ""),
            " / ".join(s.get("h1_liste", [])),
            " / ".join(s.get("h2", [])),
            s.get("text_voll", "")[:20000],
        ]
    )
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def html_laden(
    seite: str, root: Optional[Path] = None, timeout: float = 15.0
) -> Optional[str]:
    """Rohes HTML — lokale Datei (statischer Adapter) bevorzugt, sonst HTTP."""
    if root is not None:
        datei = hw.datei_fuer_seite(root, seite)
        if datei is not None:
            try:
                return datei.read_text(encoding="utf-8")
            except OSError:
                pass
    if "://" not in (seite or ""):
        return None
    try:
        import httpx

        r = httpx.get(
            seite,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "seo-autopilot/empfehlungen"},
        )
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        return r.text
    except Exception as exc:  # Netz ist keine Katastrophe
        logger.warning(f"[Empfehlungen] Abruf {seite} gescheitert: {exc}")
        return None


def _root_von(cfg: Dict[str, Any]) -> Optional[Path]:
    if cfg.get("adapter_type") != "static":
        return None
    r = (cfg.get("adapter_config") or {}).get("root_path")
    return Path(r) if r and Path(r).exists() else None


def eigene_seiten_urls(
    cfg: Dict[str, Any], zusatz: Iterable[str] = (), grenze: int = MAX_EIGENE_SEITEN
) -> List[str]:
    """Seiten der Website: sitemap.xml (lokal oder per HTTP) + Search-Console-Seiten."""
    domain = (cfg.get("domain") or "").rstrip("/")
    urls: List[str] = []
    root = _root_von(cfg)
    roh = None
    if root is not None and (root / "sitemap.xml").exists():
        roh = (root / "sitemap.xml").read_text(encoding="utf-8", errors="ignore")
    elif domain:
        roh = html_laden_text(f"{domain}/sitemap.xml")
    for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", roh or "", re.I | re.S):
        urls.append(html_mod.unescape(loc))
    urls += list(zusatz)
    host = urlparse(domain).netloc.removeprefix("www.")
    aus: List[str] = []
    for u in urls:
        if urlparse(u).netloc.removeprefix("www.") != host or u.endswith(".xml"):
            continue
        if u not in aus:
            aus.append(u)
    return aus[:grenze]


def html_laden_text(url: str) -> Optional[str]:
    try:
        import httpx

        r = httpx.get(url, timeout=15, follow_redirects=True)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Search Console
# ---------------------------------------------------------------------------


def gsc_zugang(cfg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if "gsc" not in (cfg.get("enabled_sources") or []):
        return None
    z = (cfg.get("source_config") or {}).get("gsc") or {}
    if not z.get("property_url") or not z.get("credentials_path"):
        return None
    return str(z["property_url"]), str(z["credentials_path"])


def suchdaten(
    cfg: Dict[str, Any], heute: Optional[date] = None, tage: int = GSC_TAGE
) -> Optional[List[Dict[str, Any]]]:
    """Suchbegriff x Seite der letzten `tage` Tage. None = keine/kaputte Search Console."""
    zugang = gsc_zugang(cfg)
    if not zugang:
        return None
    from .sources.gsc import GSCDataSource

    prop, cred = zugang
    ende = (heute or date.today()) - timedelta(days=GSC_VERZUG_TAGE)
    start = ende - timedelta(days=tage - 1)

    async def _hole():
        quelle = GSCDataSource(cred)
        await quelle.authenticate()
        return await quelle.pull_range(
            prop, start, ende, dimensions=["query", "page"], row_limit=5000
        )

    try:
        rows = asyncio.run(_hole())
    except Exception as exc:
        logger.warning(f"[Empfehlungen] Search Console nicht lesbar: {exc}")
        return None
    if rows is None:
        return None
    return aufbereiten(rows)


def aufbereiten(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """GSC-Rohzeilen -> {query, seite, einblendungen, klicks, position}, ohne Such-Operatoren."""
    from .agents.keyword import echte_suchbegriffe

    zeilen = [
        {
            "query": r["keys"][0],
            "seite": r["keys"][1],
            "einblendungen": int(r.get("impressions", 0)),
            "klicks": int(r.get("clicks", 0)),
            "position": round(float(r.get("position", 0.0)), 1),
        }
        for r in rows
        if len(r.get("keys", [])) >= 2
    ]
    return echte_suchbegriffe(zeilen)


def je_seite(zeilen: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    aus: Dict[str, Dict[str, Any]] = {}
    for z in zeilen:
        s = aus.setdefault(
            z["seite"],
            {"einblendungen": 0, "klicks": 0, "_pos": 0.0, "begriffe": []},
        )
        s["einblendungen"] += z["einblendungen"]
        s["klicks"] += z["klicks"]
        s["_pos"] += z["position"] * z["einblendungen"]
        s["begriffe"].append(z)
    for s in aus.values():
        s["position"] = (
            round(s.pop("_pos") / s["einblendungen"], 1) if s["einblendungen"] else None
        )
        s["begriffe"].sort(key=lambda z: -z["einblendungen"])
    return aus


def cache_schluessel(
    struktur: Dict[str, Any], begriffe: Sequence[Dict[str, Any]]
) -> str:
    namen = sorted(b["query"] for b in begriffe[:20])
    roh = f"{LOGIK_VERSION}|" + seiten_hash(struktur) + "|" + ",".join(namen)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Deterministische Pruefungen: welche Aufgaben braucht die Seite?
# ---------------------------------------------------------------------------


def aufgaben_fuer_seite(
    struktur: Dict[str, Any],
    begriffe: Sequence[Dict[str, Any]],
    marke: Sequence[str],
) -> Dict[str, Any]:
    """Was die KI fuer diese Seite ausarbeiten soll (nur Ausloeser, keine Texte)."""
    text_n = norm(struktur.get("text_voll", "") + " " + struktur.get("title", ""))
    koepfe_n = norm(" ".join(struktur.get("h1_liste", []) + struktur.get("h2", [])))
    faq_n = norm(
        " ".join(
            struktur.get("faq_fragen", [])
            + struktur.get("h2", [])
            + struktur.get("h3", [])
        )
    )
    relevant = [b for b in begriffe if b["einblendungen"] >= MIN_EINBLENDUNGEN_BEGRIFF]
    ohne_marke = [b for b in relevant if not ist_marke(b["query"], marke)]
    aufgaben: Dict[str, Any] = {}

    # Grundsatz (Google-Leitfaden "Optimizing your website for generative AI
    # features", 10.07.2026): Jede Empfehlung braucht echte Nachfrage — einen
    # Suchbegriff aus der Search Console. Ohne Suchdaten wird nichts empfohlen;
    # "besser fuer KI" ist keine Begruendung.
    if not begriffe:
        return aufgaben

    # Fragen zeigen die Absicht schon bei wenigen Einblendungen — hier gilt
    # keine Mindestzahl (Probe 18.09.: echte Frage mit 1 Einblendung).
    fragen = [
        b
        for b in begriffe
        if not ist_marke(b["query"], marke)
        and ist_frage(b["query"])
        and abdeckung(b["query"], faq_n, marke) < 0.6
    ]
    if len(fragen) >= FAQ_MIN:
        aufgaben["faq"] = {"fragen": fragen[:8], "luecke": 1.0}

    haupt = ohne_marke[0] if ohne_marke else None
    luecken = [b for b in ohne_marke if abdeckung(b["query"], text_n, marke) < 0.5]
    if luecken:
        aufgaben["abschnitt"] = {
            "begriffe": luecken[:4],
            "luecke": 1 - abdeckung(luecken[0]["query"], text_n, marke),
        }

    if (
        haupt
        and abdeckung(haupt["query"], koepfe_n, marke) < 0.5
        and abdeckung(haupt["query"], text_n, marke) >= 1.0
    ):
        aufgaben["ueberschrift"] = {
            "begriff": haupt,
            "luecke": 1 - abdeckung(haupt["query"], koepfe_n, marke),
        }

    erster_n = norm(struktur.get("erster_absatz", ""))
    if (
        haupt
        and struktur.get("erster_absatz")
        and abdeckung(haupt["query"], erster_n, marke) < 0.5
    ):
        aufgaben["antwort"] = {
            "begriff": haupt,
            "luecke": 1 - abdeckung(haupt["query"], erster_n, marke),
        }
    return aufgaben


# ---------------------------------------------------------------------------
# KI
# ---------------------------------------------------------------------------

KI_SYSTEM = """Du bist On-Page-SEO-Berater und Texter für die Website '{name}' ({domain}).
Sprache: {sprache}. Ansprache der Leser: {anrede}.
Harte Regeln:
- Nutze AUSSCHLIESSLICH Fakten aus dem gelieferten Seitentext und den Belegen von derselben Website.
- Erfinde nichts: keine Zahlen, Preise, Jahre, Auszeichnungen, Kundenzahlen, Garantien oder Versprechen.
- Fehlt für eine Antwort die Information, schreibe genau: {platzhalter}
- Kein HTML, kein Markdown, keine Emojis. Natürliche Sprache, kein Keyword-Stuffing.
- Antworte ausschließlich mit einem JSON-Objekt, ohne Text davor oder danach.{zusatz}"""


def ki_system(cfg: Dict[str, Any], struktur: Dict[str, Any]) -> str:
    regeln = (cfg.get("adapter_config") or {}).get("seo_regeln") or {}
    sprache = {"de": "Deutsch", "en": "Englisch"}.get(
        struktur.get("lang", "de"), "Deutsch"
    )
    an = {"sie": "Sie (siezen)", "du": "du (duzen)"}.get(
        struktur.get("anrede"), "wie im Seitentext"
    )
    zusatz = ""
    if regeln.get("verbotene_woerter"):
        zusatz += (
            "\n- Diese Wörter sind tabu: "
            + ", ".join(map(str, regeln["verbotene_woerter"]))
            + "."
        )
    for h in regeln.get("hinweise", []) or []:
        zusatz += f"\n- {h}"
    return KI_SYSTEM.format(
        name=cfg.get("name", ""),
        domain=cfg.get("domain", ""),
        sprache=sprache,
        anrede=an,
        platzhalter=PLATZHALTER,
        zusatz=zusatz,
    )


def _begriffszeile(b: Dict[str, Any]) -> str:
    pos = f", Position {b['position']}" if b.get("position") else ""
    return f"- „{b['query']}“ ({b.get('einblendungen', 0)} Einblendungen{pos})"


def ki_auftrag(
    struktur: Dict[str, Any],
    begriffe: Sequence[Dict[str, Any]],
    aufgaben: Dict[str, Any],
    belege: Sequence[Dict[str, str]],
) -> str:
    s = struktur
    teile = [
        f"Seite: {s.get('seite')}",
        f"Titel: {s.get('title') or '(keiner)'}",
        f"Beschreibung: {s.get('description') or '(keine)'}",
        f"H1: {s.get('h1') or '(keine)'}",
        "H2: " + (" | ".join(s.get("h2", [])[:15]) or "(keine)"),
        "Vorhandene FAQ-Fragen: "
        + (" | ".join(s.get("faq_fragen", [])[:12]) or "(keine)"),
        f"Erster Absatz: {s.get('erster_absatz') or '(keiner)'}",
        "Seitentext: " + " ".join(s.get("text_voll", "").split()[:1400]),
    ]
    if begriffe:
        teile.append("Suchbegriffe, für die Google diese Seite zeigt (90 Tage):")
        teile += [_begriffszeile(b) for b in begriffe[:15]]
    if belege:
        teile.append(
            "Belege von anderen Seiten derselben Website (dürfen als Fakten genutzt werden):"
        )
        teile += [f"- [{b['seite']}] {b['satz']}" for b in belege[:8]]

    schema: Dict[str, str] = {}
    auftraege = [
        "Schreibe für Menschen, nicht für Suchmaschinen oder KI: hilfreich, konkret, natürlich, "
        "nicht im Telegrammstil. Übernimm einen Suchbegriff nur dann wörtlich, wenn der Seitentext "
        "das Thema wirklich behandelt; sonst formuliere mit den Worten der Seite. Kein Suchbegriff "
        "darf eine neue Behauptung über das Angebot einführen. Zerlege nichts in Mini-Abschnitte "
        "und füge keine zusätzlichen Zwischenüberschriften ein."
    ]
    if "faq" in aufgaben:
        fr = aufgaben["faq"]["fragen"]
        auftraege.append(
            f"FAQ: Formuliere zu JEDER dieser echten Suchanfragen genau eine Frage in natürlicher "
            f"Sprache (höchstens {FAQ_MAX}), keine weiteren Fragen erfinden: "
            + ", ".join(f"„{b['query']}“" for b in fr)
            + ". Trage im Feld 'suchbegriff' die Suchanfrage wörtlich ein. Antwort 1-3 Sätze nur aus "
            f"Fakten der Website; sonst genau {PLATZHALTER}."
        )
        schema["faq"] = '[{"frage": "...?", "antwort": "...", "suchbegriff": "..."}]'
    if "abschnitt" in aufgaben:
        lb = aufgaben["abschnitt"]["begriffe"]
        auftraege.append(
            "ABSCHNITT: Die Seite wird bei "
            + (", ".join(f"„{b['query']}“" for b in lb) or "(keine Suchdaten)")
            + " eingeblendet, behandelt das Thema aber kaum. Nur wenn das eine echte inhaltliche Lücke für "
            "Besucher ist, schlage EINEN neuen Abschnitt vor (sonst 'abschnitt': null): Überschrift, "
            "2-3 Stichpunkte was hinein gehört, und 'text' = fertiger Absatz (2-4 Sätze) NUR wenn die Fakten "
            "dafür im Seitentext oder in den Belegen stehen, sonst leerer String."
        )
        schema["abschnitt"] = (
            '{"thema": "...", "ueberschrift": "...", "stichpunkte": ["..."], "text": ""}'
        )
    if "ueberschrift" in aufgaben:
        b = aufgaben["ueberschrift"]["begriff"]
        auftraege.append(
            f"UEBERSCHRIFT: Die H1 nennt den Hauptsuchbegriff „{b['query']}“ nicht. Schlage eine neue H1 vor "
            "(3-10 Wörter), die den Begriff natürlich aufnimmt und zum Inhalt passt."
        )
        schema["ueberschrift"] = '{"neu": "...", "grund": "..."}'
    if "antwort" in aufgaben:
        b = aufgaben["antwort"]["begriff"]
        auftraege.append(
            f"ERSTER_ABSATZ: Der erste Absatz beantwortet die Hauptfrage (Suche „{b['query']}“) nicht direkt. "
            "Formuliere ihn neu (2-4 Sätze): erst die direkte Antwort, dann das Wichtigste, nur Fakten der Seite. "
            "Nicht 'für KI' umschreiben — nur wenn Suchende die Antwort auf ihre Suche sonst nicht "
            "finden. Ist der Absatz schon gut, gib 'neu' als leeren String."
        )
        schema["erster_absatz"] = '{"neu": "...", "grund": "..."}'
    teile.append("\nAUFGABEN:\n" + "\n".join(auftraege))
    teile.append(
        "Antworte als JSON mit genau diesen Schlüsseln:\n{"
        + ", ".join(f'"{k}": {v}' for k, v in schema.items())
        + "}"
    )
    return "\n".join(teile)


def json_aus(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    t = re.sub(r"^```[a-z]*\s*|\s*```$", "", t, flags=re.I).strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return {}
    try:
        o = json.loads(t[a : b + 1])
    except ValueError:
        return {}
    return o if isinstance(o, dict) else {}


def _ki_fragen(prompt: str, system: str) -> str:
    from . import abo_ki

    return abo_ki.fragen(prompt, system=system, modell="sonnet", zeitlimit=240)


# ---------------------------------------------------------------------------
# Plausibilitaet (Handwerker-Regeln + Zahlen + Ansprache)
# ---------------------------------------------------------------------------


def pruefe_text(
    text: str,
    quelle: str,
    regeln: Optional[Dict[str, Any]],
    seiten_anrede: Optional[str],
    min_len: int = 3,
    max_len: int = 900,
    streng: bool = False,
) -> Tuple[bool, str]:
    """Darf dieser Satz als Vorschlag stehen? Verworfen = nicht speichern.

    `streng` (Antworten und Texte, die live gehen koennen): Fachbegriffe, die auf
    der Website nirgends vorkommen, gelten als neue Behauptung. Probe 18.09.:
    "funktioniert auch bei Erstvermietung" — das Wort stand nur im Suchbegriff.
    """
    s = hw.saeubern(text)
    if not (min_len <= len(s) <= max_len):
        return False, f"Länge {len(s)} außerhalb {min_len}-{max_len}"
    ok, grund = hw.plausibel("empfehlung", s, regeln)
    if not ok:
        return False, grund
    quell_zahlen = set(zahlen(quelle))
    for z in zahlen(s):
        if z not in quell_zahlen:
            return False, f"Zahl {z} steht nicht auf der Website"
    if seiten_anrede == "sie" and re.search(r"\b(du|dich|dir|dein\w*)\b", s):
        return False, "duzt, die Seite siezt"
    if seiten_anrede == "du" and re.search(r"[a-zäöüß,] (Sie|Ihnen|Ihre\w*)\b", s):
        return False, "siezt, die Seite duzt"
    if _stuffing(s):
        return False, "Wortwiederholung (Keyword-Stuffing)"
    if _META.search(s):
        return False, "spricht über den Prompt statt zum Leser"
    if streng:
        fremd = unbelegte_begriffe(s, quelle)
        if fremd:
            return False, "Begriff steht nicht auf der Website: " + ", ".join(fremd[:3])
    return True, "ok"


_SATZANFAENGE = {
    "zusätzlich",
    "außerdem",
    "gleichzeitig",
    "anschließend",
    "automatisch",
    "beispielsweise",
    "insbesondere",
    "grundsätzlich",
    "allerdings",
    "darüber",
    "deshalb",
    "dadurch",
    "trotzdem",
    "natürlich",
    "stattdessen",
    "außerdem",
    "ebenfalls",
    "schließlich",
}


def unbelegte_begriffe(text: str, quelle: str) -> List[str]:
    """Substantive (gross geschrieben, >= 8 Zeichen), deren Wortstamm weder im
    Quelltext noch in dessen zusammengeschriebener Form vorkommt."""
    q = norm(quelle)
    q_zusammen = q.replace(" ", "")
    fremd = []
    for wort in re.findall(r"[A-ZÄÖÜ][\wäöüß-]{7,}", text):
        if wort.lower() in _SATZANFAENGE:
            continue
        if not _belegt(wort, q, q_zusammen) and not all(
            _belegt(teil, q, q_zusammen) for teil in wort.split("-") if len(teil) >= 3
        ):
            fremd.append(wort)
    return fremd


def _belegt(wort: str, q: str, q_zusammen: str) -> bool:
    stamm = _stamm(norm(wort).strip().replace(" ", ""))
    stamm = stamm[: max(6, len(stamm) - 2)] if len(stamm) > 6 else stamm
    return bool(stamm) and (stamm in q or stamm in q_zusammen)


# "laut Seitentext" landete in der Probe vom 18.09. in einer Kundenantwort
_META = re.compile(
    r"(seitentext|laut (der )?(seite|website|webseite)|im gelieferten|laut belegen"
    r"|die (seite|website|webseite) (nennt|beschreibt|zeigt|erwähnt|erwaehnt))",
    re.I,
)


def _stuffing(s: str) -> bool:
    ws = [w for w in norm(s).split() if len(w) >= 5]
    if len(ws) < 6:
        return False
    return max(ws.count(w) for w in set(ws)) > max(3, len(ws) // 6)


# ---------------------------------------------------------------------------
# Aus KI-Antwort + Befunden Empfehlungen bauen
# ---------------------------------------------------------------------------


def faq_html(
    fragen: Sequence[Dict[str, str]], ueberschrift: str = "Häufige Fragen"
) -> str:
    e = html_mod.escape
    teile = [
        '<section class="seo-faq" data-seo-autopilot="faq">',
        f"<h2>{e(ueberschrift)}</h2>",
    ]
    for f in fragen:
        teile.append(f"<h3>{e(f['frage'])}</h3>\n<p>{e(f['antwort'])}</p>")
    teile.append("</section>")
    return "\n".join(teile)


def slug(text: str) -> str:
    return (
        re.sub(r"-+", "-", norm(text).strip().replace(" ", "-"))[:40].strip("-")
        or "abschnitt"
    )


def abschnitt_html(ueberschrift: str, text: str) -> str:
    e = html_mod.escape
    return (
        f'<section data-seo-autopilot="abschnitt-{slug(ueberschrift)}">\n'
        f"<h2>{e(ueberschrift)}</h2>\n<p>{e(text)}</p>\n</section>"
    )


def _suchbegriffe_kurz(
    begriffe: Sequence[Dict[str, Any]], n: int = 5
) -> List[Dict[str, Any]]:
    return [
        {k: b.get(k) for k in ("query", "einblendungen", "klicks", "position")}
        for b in begriffe[:n]
    ]


def empfehlungen_aus_ki(
    antwort: Dict[str, Any],
    projekt: str,
    struktur: Dict[str, Any],
    begriffe: Sequence[Dict[str, Any]],
    aufgaben: Dict[str, Any],
    belege: Sequence[Dict[str, str]],
    regeln: Optional[Dict[str, Any]],
    marke: Sequence[str],
) -> Tuple[List[Empfehlung], List[str]]:
    """Prueft jede KI-Teilantwort; zurueck (gueltige Empfehlungen, Verwerfungsgruende)."""
    seite = struktur.get("seite", "")
    quelle = (
        struktur.get("text_voll", "")
        + " "
        + struktur.get("title", "")
        + " "
        + " ".join(b["satz"] for b in belege)
    )
    an = struktur.get("anrede")
    ohne = not begriffe
    seiten_impr = sum(b["einblendungen"] for b in begriffe)
    pos = begriffe[0]["position"] if begriffe else None
    aus: List[Empfehlung] = []
    verworfen: List[str] = []

    # --- FAQ ---
    if "faq" in aufgaben and isinstance(antwort.get("faq"), list):
        fragen = []
        echte = {norm(b["query"]): b for b in aufgaben["faq"]["fragen"]}
        benutzt: set = set()
        for f in antwort["faq"][: FAQ_MAX + 2]:
            if not isinstance(f, dict):
                continue
            frage, antw = hw.saeubern(str(f.get("frage", ""))), hw.saeubern(
                str(f.get("antwort", ""))
            )
            if "[" in antw or "]" in antw:
                antw = PLATZHALTER  # halb beantwortet = unbeantwortet (Probe 18.09.)
            ok, grund = pruefe_text(
                frage,
                quelle + " " + " ".join(b["query"] for b in begriffe),
                regeln,
                an,
                10,
                160,
            )
            if ok and not frage.endswith("?"):
                ok, grund = False, "keine Frage"
            if (
                ok
                and abdeckung(
                    frage, norm(" ".join(struktur.get("faq_fragen", []))), marke
                )
                >= 0.8
            ):
                ok, grund = False, "Frage steht schon auf der Seite"
            if ok and antw != PLATZHALTER:
                ok, grund = pruefe_text(antw, quelle, regeln, an, 20, 450, streng=True)
            sb = norm(str(f.get("suchbegriff", "")))
            if ok and (sb not in echte or sb in benutzt):
                # Nur Fragen, die Menschen wirklich stellen (GSC), je Suchanfrage eine
                ok, grund = False, "keine echte Suchanfrage dahinter"
            if not ok:
                verworfen.append(f"FAQ „{frage[:60]}“: {grund}")
                continue
            benutzt.add(sb)
            fragen.append(
                {
                    "frage": frage,
                    "antwort": antw,
                    # die echte Suchanfrage, nicht die (evtl. gekuerzte) KI-Abschrift
                    "suchbegriff": echte[sb]["query"],
                }
            )
        fragen = fragen[:FAQ_MAX]
        if len(fragen) >= FAQ_MIN:
            beantwortet = [f for f in fragen if f["antwort"] != PLATZHALTER]
            gesucht = [echte[norm(f["suchbegriff"])] for f in fragen]
            grund = (
                "Suchende fragen bei Google: "
                + ", ".join(
                    f"„{b['query']}“ ({b['einblendungen']}×)" for b in gesucht[:4]
                )
                + " – die Seite beantwortet das bisher nicht als eigene Frage."
            )
            text = (
                grund
                + " Vorschlag für einen Abschnitt „Häufige Fragen“:\n"
                + "\n".join(f"• {f['frage']} – {f['antwort']}" for f in fragen)
            )
            anwendung = None
            if len(beantwortet) >= 2:
                anwendung = {
                    "typ": "empfehlung_faq_ergaenzen",
                    "seite": seite,
                    "fragen": beantwortet,
                    "html": faq_html(beantwortet),
                    # Kein FAQPage-JSON-LD: FAQ-Rich-Results seit 07.05.2026 abgeschaltet;
                    # der Nutzen liegt im sichtbaren Text.
                    "einfuegestelle": "vor </main> (sonst vor </article>), vor dem Footer",
                }
            aus.append(
                Empfehlung(
                    projekt,
                    seite,
                    ART_FAQ,
                    f"{len(fragen)} häufige Fragen ergänzen",
                    text,
                    {
                        "fragen": fragen,
                        "anwendung": anwendung,
                        "belege": list(belege)[:6],
                    },
                    suchbegriffe=_suchbegriffe_kurz(gesucht or begriffe),
                    ohne_suchdaten=ohne,
                    prioritaet=_nutzen(
                        sum(b["einblendungen"] for b in gesucht) or seiten_impr,
                        min((b["position"] for b in gesucht), default=pos),
                        ART_FAQ,
                        ohne,
                        aufgaben["faq"].get("luecke", 1.0),
                    ),
                )
            )
        elif antwort.get("faq"):
            verworfen.append(f"FAQ: nur {len(fragen)} gültige Fragen (mind. {FAQ_MIN})")

    # --- Abschnitt ---
    ab = antwort.get("abschnitt")
    if "abschnitt" in aufgaben and isinstance(ab, dict) and ab.get("ueberschrift"):
        ue = hw.saeubern(str(ab.get("ueberschrift", "")))
        punkte = [
            hw.saeubern(str(p))
            for p in (ab.get("stichpunkte") or [])[:3]
            if str(p).strip()
        ]
        fehler = [
            g
            for g in [
                pruefe_text(
                    ue,
                    quelle + " " + " ".join(b["query"] for b in begriffe),
                    regeln,
                    an,
                    5,
                    90,
                )
            ]
            if not g[0]
        ]
        for p in punkte:
            ok, grund = pruefe_text(p, quelle, regeln, an, 5, 200)
            if not ok:
                fehler.append((ok, f"Stichpunkt: {grund}"))
        absatz = hw.saeubern(str(ab.get("text") or ""))
        if absatz:
            ok, grund = pruefe_text(absatz, quelle, regeln, an, 80, 900, streng=True)
            if not ok:
                verworfen.append(f"Abschnittstext verworfen: {grund}")
                absatz = ""
        if fehler or len(punkte) < 2:
            verworfen.append(
                "Abschnitt: "
                + ("; ".join(g for _, g in fehler) or "zu wenige Stichpunkte")
            )
        else:
            lb = aufgaben["abschnitt"]["begriffe"]
            grund = (
                "Vermutung ohne Suchdaten."
                if ohne
                else "Google zeigt die Seite bei "
                + ", ".join(f"„{b['query']}“ ({b['einblendungen']}×)" for b in lb[:3])
                + ", die Seite behandelt das Thema aber kaum."
            )
            text = f"{grund} Neuer Abschnitt „{ue}“ – hinein gehört:\n" + "\n".join(
                f"• {p}" for p in punkte
            )
            anwendung = (
                {
                    "typ": "empfehlung_abschnitt_ergaenzen",
                    "seite": seite,
                    "ueberschrift": ue,
                    "text": absatz,
                    "html": abschnitt_html(ue, absatz),
                    "marker": f"abschnitt-{slug(ue)}",
                }
                if absatz and not ohne
                else None
            )
            aus.append(
                Empfehlung(
                    projekt,
                    seite,
                    ART_ABSCHNITT,
                    f"Abschnitt „{ue}“ ergänzen",
                    text,
                    {
                        "thema": str(ab.get("thema", ""))[:120],
                        "ueberschrift": ue,
                        "stichpunkte": punkte,
                        "text_entwurf": absatz,
                        "anwendung": anwendung,
                        "belege": list(belege)[:6],
                    },
                    suchbegriffe=_suchbegriffe_kurz(lb),
                    ohne_suchdaten=ohne,
                    prioritaet=_nutzen(
                        sum(b["einblendungen"] for b in lb) or 1,
                        lb[0]["position"] if lb else None,
                        ART_ABSCHNITT,
                        ohne,
                        aufgaben["abschnitt"].get("luecke", 1.0),
                    ),
                )
            )

    # --- Ueberschrift ---
    ueb = antwort.get("ueberschrift")
    if "ueberschrift" in aufgaben and isinstance(ueb, dict) and ueb.get("neu"):
        b = aufgaben["ueberschrift"]["begriff"]
        neu = hw.saeubern(str(ueb["neu"]))
        ok, grund = pruefe_text(neu, quelle, regeln, an, 5, hw.H1_MAX, streng=True)
        if ok and abdeckung(b["query"], norm(neu), marke) < 0.5:
            ok, grund = False, "neuer Vorschlag nennt den Suchbegriff auch nicht"
        if ok and norm(neu) == norm(struktur.get("h1", "")):
            ok, grund = False, "identisch mit der vorhandenen H1"
        if ok:
            alt = struktur.get("h1", "")
            aus.append(
                Empfehlung(
                    projekt,
                    seite,
                    ART_UEBERSCHRIFT,
                    f"Hauptüberschrift auf „{b['query']}“ ausrichten",
                    f"Die Seite wird vor allem bei „{b['query']}“ gefunden ({b['einblendungen']}× eingeblendet), "
                    f"die Hauptüberschrift „{alt or '(keine)'}“ nennt das aber nicht. Vorschlag: „{neu}“.",
                    {
                        "alt": alt,
                        "neu": neu,
                        "grund": hw.saeubern(str(ueb.get("grund", "")))[:200],
                        "anwendung": (
                            {
                                "typ": "empfehlung_ueberschrift_verbessern",
                                "seite": seite,
                                "alt": alt,
                                "neu": neu,
                            }
                            if alt
                            else None
                        ),
                    },
                    suchbegriffe=_suchbegriffe_kurz([b]),
                    prioritaet=_nutzen(
                        b["einblendungen"],
                        b.get("position"),
                        ART_UEBERSCHRIFT,
                        False,
                        aufgaben["ueberschrift"].get("luecke", 1.0),
                    ),
                )
            )
        else:
            verworfen.append(f"Überschrift: {grund}")

    # --- Erster Absatz ---
    ea = antwort.get("erster_absatz")
    if (
        "antwort" in aufgaben
        and isinstance(ea, dict)
        and str(ea.get("neu") or "").strip()
    ):
        b = aufgaben["antwort"]["begriff"]
        neu = hw.saeubern(str(ea["neu"]))
        ok, grund = pruefe_text(neu, quelle, regeln, an, 60, 600, streng=True)
        if ok:
            alt = struktur.get("erster_absatz", "")
            vermutung = bool(aufgaben["antwort"].get("vermutung"))
            aus.append(
                Empfehlung(
                    projekt,
                    seite,
                    ART_ANTWORT,
                    "Ersten Absatz als direkte Antwort formulieren",
                    f"Wer „{b['query']}“ sucht ({b.get('einblendungen', 0)}× eingeblendet), findet im "
                    f"ersten Absatz keine direkte Antwort darauf. Vorschlag: „{neu}“",
                    {
                        "alt": alt,
                        "neu": neu,
                        "grund": hw.saeubern(str(ea.get("grund", "")))[:200],
                        "anwendung": (
                            {
                                "typ": "empfehlung_antwort_zuerst",
                                "seite": seite,
                                "alt": alt,
                                "neu": neu,
                            }
                            if alt and not vermutung
                            else None
                        ),
                    },
                    suchbegriffe=_suchbegriffe_kurz([b]),
                    ohne_suchdaten=vermutung,
                    prioritaet=_nutzen(
                        b.get("einblendungen") or 1,
                        b.get("position"),
                        ART_ANTWORT,
                        vermutung,
                        aufgaben["antwort"].get("luecke", 1.0),
                    ),
                )
            )
        else:
            verworfen.append(f"Erster Absatz: {grund}")
    return aus, verworfen


def _nutzen(
    einblendungen: int,
    position: Optional[float],
    art: str,
    ohne_suchdaten: bool,
    luecke: float = 1.0,
) -> float:
    """Arbeitsliste: Nutzernachfrage (Einblendungen x Positions-Potenzial) x
    Inhaltsluecke (0-1) x Art — nie ein GEO-/KI-Befund."""
    from .chancen import potenzial

    basis = 5 if ohne_suchdaten else max(int(einblendungen or 0), 1)
    luecke = min(max(float(luecke), 0.1), 1.0)
    return round(basis * potenzial(position) * luecke * GEWICHT.get(art, 0.5), 2)


# ---------------------------------------------------------------------------
# Deterministische Arten: interne Links (D), neue Seite, Titel/Beschreibung
# ---------------------------------------------------------------------------


def _muster(begriff: str) -> Optional[str]:
    ws = [re.escape(w) for w in re.findall(r"[\wäöüÄÖÜß]+", begriff) if len(w) >= 2]
    if not ws or len(ws) > 5:
        return None
    return r"(?<![\wäöüß])" + r"[\s\-]+".join(ws) + r"(?![\wäöüß])"


def _in_absatz(roh: str, muster: str) -> Optional[str]:
    """Findet den Begriff woertlich in einem <p> ohne Link im Hauptbereich — liefert die Schreibweise."""
    m = re.search(r"<main\b[^>]*>(.*?)</main>", roh, re.I | re.S)
    bereich = m.group(1) if m else roh
    for p in re.finditer(r"<p\b[^>]*>(.*?)</p>", bereich, re.I | re.S):
        inner = p.group(1)
        if "<a" in inner.lower():
            continue
        for teil in re.split(r"<[^>]+>", inner):
            t = re.search(muster, html_mod.unescape(teil), re.I)
            if t:
                return t.group(0)
    return None


def _pfad(url: str) -> str:
    p = urlparse(url).path or "/"
    return p if p.endswith("/") or "." in p.rsplit("/", 1)[-1] else p + "/"


def interne_links(
    projekt: str,
    seite: str,
    begriffe: Sequence[Dict[str, Any]],
    eigene: Dict[str, Dict[str, Any]],
    marke: Sequence[str],
    regeln: Optional[Dict[str, Any]],
) -> Optional[Empfehlung]:
    """Position 8-20: welche eigene Seite erwaehnt den Begriff und verlinkt noch nicht hierher."""
    lo, hi = LINK_POSITION
    kandidaten = [
        b
        for b in begriffe
        if lo <= (b.get("position") or 0) <= hi
        and b["einblendungen"] >= MIN_EINBLENDUNGEN_LINK
        and not ist_marke(b["query"], marke)
    ]
    ziel = _pfad(seite)
    for b in kandidaten[:5]:
        muster = _muster(b["query"])
        if not muster:
            continue
        quellen = []
        for url, s in eigene.items():
            if _pfad(url) == ziel or seite_gesperrt(url, regeln) or not s.get("_roh"):
                continue
            if any(_pfad(link) == ziel for link in s.get("links", [])):
                continue
            wort = _in_absatz(s["_roh"], muster)
            if wort:
                quellen.append({"seite": url, "linktext": wort})
        if not quellen:
            continue
        erste = quellen[0]
        text = (
            f"Für „{b['query']}“ steht die Seite auf Position {b['position']} ({b['einblendungen']} Einblendungen in 90 Tagen). "
            "Links von passenden eigenen Seiten helfen auf Seite 1: "
            + "; ".join(
                f"auf {_pfad(q['seite'])} das Wort „{q['linktext']}“ verlinken"
                for q in quellen[:3]
            )
            + "."
        )
        return Empfehlung(
            projekt,
            seite,
            ART_LINKS,
            f"{len(quellen[:3])} interne Link(s) für „{b['query']}“ setzen",
            text,
            {
                "suchbegriff": b["query"],
                "position": b["position"],
                "einblendungen": b["einblendungen"],
                "zielseite": seite,
                "quellen": quellen[:3],
                "anwendung": {
                    "typ": "empfehlung_interne_links",
                    "seite": erste["seite"],
                    "quelle": erste["seite"],
                    "ziel": ziel,
                    "zielseite": seite,
                    "muster": muster,
                    "linktext": erste["linktext"],
                },
            },
            suchbegriffe=_suchbegriffe_kurz([b]),
            prioritaet=_nutzen(b["einblendungen"], b["position"], ART_LINKS, False),
        )
    return None


def titel_beschreibung(
    projekt: str, seite: str, info: Dict[str, Any]
) -> Optional[Empfehlung]:
    if not info.get("position") or info["position"] > 10 or info["einblendungen"] < 50:
        return None
    ctr = info["klicks"] / info["einblendungen"]
    if ctr >= 0.03:
        return None
    return Empfehlung(
        projekt,
        seite,
        ART_TITEL,
        "Titel und Kurzbeschreibung überarbeiten",
        f"Position {info['position']}, aber nur {ctr * 100:.1f} % Klickrate ({info['klicks']} Klicks bei "
        f"{info['einblendungen']} Einblendungen). Das übernimmt die Titel-Werkstatt des Autopiloten – "
        "Vorschläge erscheinen unter Entscheidungen bzw. werden im Autopilot direkt gesetzt.",
        {"anwendung": None, "verweis": "handwerker/low_ctr_opportunity"},
        suchbegriffe=_suchbegriffe_kurz(info.get("begriffe", [])),
        prioritaet=_nutzen(info["einblendungen"], info["position"], ART_TITEL, False),
    )


def neue_seiten(
    projekt: str,
    zeilen: Sequence[Dict[str, Any]],
    eigene: Dict[str, Dict[str, Any]],
    marke: Sequence[str],
) -> Optional[Empfehlung]:
    """Suchbegriffe mit Einblendungen, fuer die keine eigene Seite das Thema behandelt."""
    je_begriff: Dict[str, Dict[str, Any]] = {}
    for z in zeilen:
        e = je_begriff.setdefault(
            z["query"],
            {
                "query": z["query"],
                "einblendungen": 0,
                "klicks": 0,
                "_pos": 0.0,
                "_max": -1,
            },
        )
        if z["einblendungen"] > e["_max"]:
            e["seite"], e["_max"] = z["seite"], z["einblendungen"]
        e["einblendungen"] += z["einblendungen"]
        e["klicks"] += z["klicks"]
        e["_pos"] += z["position"] * z["einblendungen"]
    texte = {
        u: norm(s.get("text_voll", "") + " " + s.get("title", ""))
        for u, s in eigene.items()
    }
    if not texte:
        return None
    luecken = []
    for b in sorted(je_begriff.values(), key=lambda x: -x["einblendungen"]):
        b["position"] = (
            round(b.pop("_pos") / b["einblendungen"], 1) if b["einblendungen"] else None
        )
        b.pop("_max", None)
        if b["einblendungen"] < 3 * MIN_EINBLENDUNGEN_BEGRIFF or ist_marke(
            b["query"], marke
        ):
            continue
        if len(woerter(b["query"], marke)) < 2:
            continue
        if max(abdeckung(b["query"], t, marke) for t in texte.values()) >= 0.5:
            continue
        luecken.append(b)
    if not luecken:
        return None
    thema = luecken[0]["query"]
    text = (
        "Für diese Suchbegriffe wird die Website eingeblendet, hat aber keine Seite, die das Thema behandelt: "
        + ", ".join(
            f"„{b['query']}“ ({b['einblendungen']}×, Position {b['position']})"
            for b in luecken[:5]
        )
        + ". Eine eigene Seite dazu kann die Einblendungen in Besucher verwandeln – nur anlegen, wenn Sie das Thema wirklich anbieten."
    )
    return Empfehlung(
        projekt,
        "",
        ART_NEUE_SEITE,
        f"Neue Seite zu „{thema}“ prüfen",
        text,
        {"themen": luecken[:5], "anwendung": None},
        suchbegriffe=_suchbegriffe_kurz(luecken),
        prioritaet=_nutzen(
            sum(b["einblendungen"] for b in luecken[:5]),
            luecken[0]["position"],
            ART_NEUE_SEITE,
            False,
        ),
    )


def belege_fuer(
    begriffe: Sequence[Dict[str, Any]],
    seite: str,
    eigene: Dict[str, Dict[str, Any]],
    marke: Sequence[str],
    grenze: int = 6,
) -> List[Dict[str, str]]:
    """Saetze von anderen eigenen Seiten, die die Luecken-Begriffe erwaehnen (Fakten fuer die KI)."""
    aus: List[Dict[str, str]] = []
    for b in begriffe:
        stems = [_stamm(w) for w in woerter(b["query"], marke)]
        if not stems:
            continue
        for url, s in eigene.items():
            if _pfad(url) == _pfad(seite):
                continue
            for satz in re.split(r"(?<=[.!?])\s+", s.get("text_voll", "")):
                n = norm(satz)
                if 30 <= len(satz) <= 300 and sum(1 for st in stems if st in n) >= max(
                    1, len(stems) - 1
                ):
                    aus.append({"seite": url, "satz": satz.strip()})
                    break
            if len(aus) >= grenze:
                return aus
    return aus


# ---------------------------------------------------------------------------
# Speicher
# ---------------------------------------------------------------------------


def tabelle_anlegen(db: str) -> bool:
    try:
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA)
        con.commit()
        con.close()
        return True
    except sqlite3.Error as exc:
        logger.warning(f"[Empfehlungen] Tabelle nicht anlegbar: {exc}")
        return False


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _zeile(r: sqlite3.Row) -> Empfehlung:
    return Empfehlung(
        project_id=r["project_id"],
        seite=r["seite"],
        art=r["art"],
        titel=r["titel"] or "",
        text=r["text"] or "",
        vorschlag=json.loads(r["vorschlag"] or "{}"),
        prioritaet=r["prioritaet"] or 0.0,
        suchbegriffe=json.loads(r["suchbegriffe"] or "[]"),
        ohne_suchdaten=bool(r["ohne_suchdaten"]),
        cache_schluessel=r["cache_schluessel"] or "",
        id=r["id"],
        erstellt_am=r["erstellt_am"],
        status=r["status"],
        entschieden_am=r["entschieden_am"],
        umgesetzt_am=r["umgesetzt_am"],
        geaenderte_seite=r["geaenderte_seite"],
        change_id=r["change_id"],
        git_commit=r["git_commit"],
        notiz=r["notiz"] or "",
    )


def speichern(db: str, liste: Sequence[Empfehlung]) -> int:
    """Neue Empfehlungen speichern; aeltere offene derselben Art/Seite gelten als ersetzt."""
    if not liste or not tabelle_anlegen(db):
        return 0
    con = sqlite3.connect(db)
    neu = 0
    try:
        for e in liste:
            e.id = e.id or str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{e.project_id}|{e.seite}|{e.art}|{e.cache_schluessel}|{e.titel}",
                )
            )
            e.erstellt_am = e.erstellt_am or _jetzt()
            if con.execute(f"select 1 from {TABELLE} where id=?", (e.id,)).fetchone():
                continue
            if con.execute(
                f"select 1 from {TABELLE} where project_id=? and seite=? and art=? and status=?",
                (e.project_id, e.seite, e.art, STATUS_FREIGEGEBEN),
            ).fetchone():
                continue  # Robert hat die vorige schon freigegeben - die zaehlt
            con.execute(
                f"update {TABELLE} set status=?, notiz=? where project_id=? and seite=? and art=? and status in (?,?,?)",
                (
                    STATUS_ERSETZT,
                    f"ersetzt am {e.erstellt_am[:10]}",
                    e.project_id,
                    e.seite,
                    e.art,
                    STATUS_OFFEN,
                    STATUS_PRUEFUNG_NEIN,
                    STATUS_NICHT_BEHEBBAR,
                ),
            )
            werte = {
                **{s: getattr(e, s, None) for s in _SPALTEN},
                "vorschlag": json.dumps(e.vorschlag, ensure_ascii=False),
                "suchbegriffe": json.dumps(e.suchbegriffe, ensure_ascii=False),
                "ohne_suchdaten": int(e.ohne_suchdaten),
            }
            con.execute(
                f"insert into {TABELLE} ({', '.join(_SPALTEN)}) values ({', '.join('?' * len(_SPALTEN))})",
                [werte[s] for s in _SPALTEN],
            )
            neu += 1
        con.commit()
    finally:
        con.close()
    return neu


def veraltete_schliessen(db: str, projekt: str, seite: str, aktuell: set) -> int:
    """Offene Empfehlungen einer neu bewerteten Seite, deren Art nicht mehr
    zutrifft (z. B. Suchanfrage verschwunden), gelten als ersetzt. Freigegebene,
    umgesetzte und abgelehnte bleiben unangetastet."""
    arten = [a for a in ARTEN if a not in aktuell and a != ART_NEUE_SEITE]
    if not arten:
        return 0
    try:
        con = sqlite3.connect(db)
        n = con.execute(
            f"update {TABELLE} set status=?, notiz=? where project_id=? and seite=? "
            f"and status in (?,?,?) and art in ({','.join('?' * len(arten))})",
            (
                STATUS_ERSETZT,
                "trifft nach neuer Prüfung nicht mehr zu",
                projekt,
                seite,
                STATUS_OFFEN,
                STATUS_PRUEFUNG_NEIN,
                STATUS_NICHT_BEHEBBAR,
                *arten,
            ),
        ).rowcount
        con.commit()
        con.close()
        return n
    except sqlite3.Error as exc:
        logger.warning(f"[Empfehlungen] Aufraeumen fehlgeschlagen: {exc}")
        return 0


def seite_gemerkt(db: str, projekt: str, seite: str) -> Optional[str]:
    if not tabelle_anlegen(db):
        return None
    con = sqlite3.connect(db)
    try:
        r = con.execute(
            f"select schluessel from {TABELLE_SEITEN} where project_id=? and seite=?",
            (projekt, seite),
        ).fetchone()
        return r[0] if r else None
    finally:
        con.close()


def seite_merken(
    db: str, projekt: str, seite: str, schluessel: str, anzahl: int
) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute(
            f"insert or replace into {TABELLE_SEITEN} values (?,?,?,?,?)",
            (projekt, seite, schluessel, _jetzt(), anzahl),
        )
        con.commit()
    finally:
        con.close()


def laden(
    db: str,
    projekt: Optional[str] = None,
    status: Optional[Sequence[str]] = None,
    art: Optional[str] = None,
) -> List[Empfehlung]:
    bed, werte = [], []
    if projekt:
        bed.append("project_id = ?")
        werte.append(projekt)
    if status:
        bed.append(f"status in ({','.join('?' * len(status))})")
        werte += list(status)
    if art:
        bed.append("art = ?")
        werte.append(art)
    wo = ("where " + " and ".join(bed)) if bed else ""
    try:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"select {', '.join(_SPALTEN)} from {TABELLE} {wo} order by prioritaet desc, erstellt_am desc",
            werte,
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    return [_zeile(r) for r in rows]


def status_setzen(db: str, eid: str, status: str, **felder: Any) -> bool:
    felder = {k: v for k, v in felder.items() if k in _SPALTEN}
    felder["status"] = status
    if status in (STATUS_FREIGEGEBEN, STATUS_ABGELEHNT):
        felder.setdefault("entschieden_am", _jetzt())
    try:
        con = sqlite3.connect(db)
        n = con.execute(
            f"update {TABELLE} set {', '.join(f'{k}=?' for k in felder)} where id=?",
            [*felder.values(), eid],
        ).rowcount
        con.commit()
        con.close()
        return n > 0
    except sqlite3.Error as exc:
        logger.warning(f"[Empfehlungen] Status nicht setzbar: {exc}")
        return False


# ---------------------------------------------------------------------------
# Lauf
# ---------------------------------------------------------------------------


def erzeugen(
    projekt: str,
    cfg: Dict[str, Any],
    db: str,
    heute: Optional[date] = None,
    max_seiten: Optional[int] = None,
    zeilen: Optional[List[Dict[str, Any]]] = None,
    lade: Optional[Callable[[str], Optional[str]]] = None,
    fragen: Optional[Callable[[str, str], str]] = None,
    nur_seiten: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Ein Lauf fuer ein Projekt. Rueckgabe: Zusammenfassung (Seiten, neu, verworfen, KI-Aufrufe).

    `zeilen`/`lade`/`fragen` sind injizierbar (Tests ohne Netz und ohne KI).
    """
    regeln = (cfg.get("adapter_config") or {}).get("seo_regeln") or {}
    grenze = max_seiten or int(
        ((cfg.get("source_config") or {}).get("empfehlungen") or {}).get(
            "max_seiten", MAX_SEITEN_STANDARD
        )
    )
    root = _root_von(cfg)
    lade = lade or (lambda u: html_laden(u, root))
    fragen = fragen or _ki_fragen
    marke = marken_woerter(cfg)
    if zeilen is None:
        zeilen = suchdaten(cfg, heute)
    ohne_gsc = not zeilen
    if ohne_gsc and not nur_seiten:
        # Ohne Suchdaten keine Nachfrage — und ohne Nachfrage keine Empfehlung
        logger.info(
            f"[Empfehlungen] {projekt}: keine Search-Console-Daten – nichts empfohlen"
        )
        return {
            "projekt": projekt,
            "seiten": [],
            "neu": 0,
            "verworfen": [],
            "ki_aufrufe": 0,
            "ohne_suchdaten": True,
            "hinweis": "Search Console anbinden – ohne echte Suchanfragen gibt es keine Empfehlungen.",
        }
    seiten_info = je_seite(zeilen or [])
    host = urlparse(cfg.get("domain", "")).netloc.removeprefix("www.")
    seiten_info = {
        u: i
        for u, i in seiten_info.items()
        if urlparse(u).netloc.removeprefix("www.") == host
    }

    # Reihenfolge: nach Einblendungen; ohne Search Console die Seiten aus der Sitemap
    if nur_seiten:
        reihenfolge = list(nur_seiten)
    elif seiten_info:
        reihenfolge = sorted(
            seiten_info, key=lambda u: -seiten_info[u]["einblendungen"]
        )
    else:
        reihenfolge = eigene_seiten_urls(cfg)
    reihenfolge = [u for u in reihenfolge if not seite_gesperrt(u, regeln)]

    # Eigene Seiten (fuer Links, Belege, neue Seiten) — einmal je Lauf laden
    eigene: Dict[str, Dict[str, Any]] = {}

    def eigene_laden() -> Dict[str, Dict[str, Any]]:
        if not eigene:
            for u in eigene_seiten_urls(cfg, zusatz=list(seiten_info)):
                roh = lade(u)
                if roh:
                    s = seiten_struktur(roh, u)
                    s["_roh"] = roh
                    eigene[u] = s
        return eigene

    bericht = {
        "projekt": projekt,
        "seiten": [],
        "neu": 0,
        "verworfen": [],
        "ki_aufrufe": 0,
        "ohne_suchdaten": ohne_gsc,
    }
    ki_seiten = 0
    for seite in reihenfolge:
        if ki_seiten >= grenze:
            break
        roh = lade(seite)
        if not roh:
            continue
        struktur = seiten_struktur(roh, seite)
        info = seiten_info.get(
            seite, {"einblendungen": 0, "klicks": 0, "position": None, "begriffe": []}
        )
        begriffe = info["begriffe"]
        schluessel = cache_schluessel(struktur, begriffe)
        if seite_gemerkt(db, projekt, seite) == schluessel:
            continue  # unveraendert seit der letzten Pruefung — nichts neu berechnen
        aufgaben = aufgaben_fuer_seite(struktur, begriffe, marke)
        liste: List[Empfehlung] = []
        if aufgaben:
            belege = []
            if "abschnitt" in aufgaben and aufgaben["abschnitt"]["begriffe"]:
                belege = belege_fuer(
                    aufgaben["abschnitt"]["begriffe"], seite, eigene_laden(), marke
                )
            try:
                antwort = json_aus(
                    fragen(
                        ki_auftrag(struktur, begriffe, aufgaben, belege),
                        ki_system(cfg, struktur),
                    )
                )
                bericht["ki_aufrufe"] += 1
            except (
                Exception
            ) as exc:  # Abo weg/Zeitlimit: Seite beim naechsten Lauf erneut
                logger.warning(
                    f"[Empfehlungen] KI fuer {seite} nicht erreichbar: {exc}"
                )
                bericht["verworfen"].append(
                    f"{seite}: KI nicht erreichbar ({type(exc).__name__})"
                )
                ki_seiten += 1
                continue
            ki_seiten += 1
            try:
                neu, verworfen = empfehlungen_aus_ki(
                    antwort,
                    projekt,
                    struktur,
                    begriffe,
                    aufgaben,
                    belege,
                    regeln,
                    marke,
                )
            except (
                Exception
            ) as exc:  # eine kaputte KI-Antwort kippt nie den ganzen Lauf
                logger.warning(
                    f"[Empfehlungen] Antwort fuer {seite} unbrauchbar: {exc}"
                )
                neu, verworfen = [], [f"KI-Antwort unbrauchbar ({type(exc).__name__})"]
            liste += neu
            bericht["verworfen"] += [f"{seite}: {v}" for v in verworfen]
        if begriffe:
            link = interne_links(
                projekt, seite, begriffe, eigene_laden(), marke, regeln
            )
            if link:
                liste.append(link)
            tb = titel_beschreibung(projekt, seite, info)
            if tb:
                liste.append(tb)
        for e in liste:
            e.cache_schluessel = schluessel
        bericht["neu"] += speichern(db, liste)
        veraltete_schliessen(db, projekt, seite, {e.art for e in liste})
        seite_merken(db, projekt, seite, schluessel, len(liste))
        bericht["seiten"].append(
            {"seite": seite, "empfehlungen": [e.art for e in liste]}
        )

    if zeilen and not nur_seiten:
        ns = neue_seiten(projekt, zeilen, eigene_laden(), marke)
        if ns:
            ns.cache_schluessel = hashlib.sha256(
                ",".join(sorted(t["query"] for t in ns.vorschlag["themen"])).encode()
            ).hexdigest()[:16]
            bericht["neu"] += speichern(db, [ns])
    logger.info(
        f"[Empfehlungen] {projekt}: {len(bericht['seiten'])} Seite(n) geprueft, {bericht['neu']} neu, "
        f"{len(bericht['verworfen'])} verworfen, {bericht['ki_aufrufe']} KI-Aufrufe"
    )
    return bericht


# ---------------------------------------------------------------------------
# Lesen / Stand (fuer CLI, Kundenbericht und das Gesamt-Audit)
# ---------------------------------------------------------------------------


def wirkung_fuer(
    db: str, change_ids: Sequence[str]
) -> Dict[str, Dict[int, Dict[str, Any]]]:
    """change_id -> {fenster: {urteil, notiz, positions_differenz}} aus der Wirkungsmessung."""
    ids = [c for c in change_ids if c]
    if not ids:
        return {}
    try:
        con = sqlite3.connect(db)
        rows = con.execute(
            f"select change_id, fenster_tage, urteil, notiz, vorher_position, nachher_position "
            f"from wirkung_messungen where change_id in ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    aus: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for cid, fenster, urteil, notiz, vp, np_ in rows:
        aus.setdefault(cid, {})[int(fenster)] = {
            "urteil": urteil,
            "notiz": notiz,
            "positions_differenz": round((vp or 0) - (np_ or 0), 2),
        }
    return aus


def stand(db: str, projekt: str) -> Dict[str, Any]:
    """Wo steht ein Projekt: umgesetzt (mit Wirkung), offen, von Hand, abgelehnt.

    Datenfunktion fuer das Gesamt-Audit ("wo wir stehen").
    """
    alle = laden(db, projekt)
    aktiv = [e for e in alle if e.status != STATUS_ERSETZT]
    umgesetzt = [e for e in aktiv if e.status == STATUS_UMGESETZT]
    wirkung = wirkung_fuer(db, [e.change_id for e in umgesetzt])
    zaehler: Dict[str, int] = {}
    for e in aktiv:
        zaehler[e.status] = zaehler.get(e.status, 0) + 1
    urteile = {
        "besser": 0,
        "schlechter": 0,
        "unveraendert": 0,
        "ohne_urteil": 0,
        "noch_nicht_gemessen": 0,
    }
    liste_umgesetzt = []
    for e in umgesetzt:
        w = wirkung.get(e.change_id or "", {})
        letzte = w[max(w)] if w else None
        if not letzte:
            urteile["noch_nicht_gemessen"] += 1
        else:
            schluessel = {
                "verbessert": "besser",
                "verschlechtert": "schlechter",
                "unveraendert": "unveraendert",
            }.get(letzte["urteil"], "ohne_urteil")
            urteile[schluessel] += 1
        liste_umgesetzt.append(
            {
                "id": e.id,
                "seite": e.seite,
                "art": e.art,
                "titel": e.titel,
                "umgesetzt_am": e.umgesetzt_am,
                "geaenderte_seite": e.geaenderte_seite,
                "git_commit": e.git_commit,
                "wirkung": w,
            }
        )

    def kurz(e: Empfehlung) -> Dict[str, Any]:
        return {
            "id": e.id,
            "seite": e.seite,
            "art": e.art,
            "titel": e.titel,
            "prioritaet": e.prioritaet,
            "status": e.status,
            "anwendbar": e.anwendbar,
            "notiz": e.notiz,
        }

    return {
        "projekt": projekt,
        "zaehler": zaehler,
        "umgesetzt": liste_umgesetzt,
        "offen": [
            kurz(e) for e in aktiv if e.status in (STATUS_OFFEN, STATUS_FREIGEGEBEN)
        ],
        "von_hand": [
            kurz(e)
            for e in aktiv
            if e.status in (STATUS_NICHT_BEHEBBAR, STATUS_PRUEFUNG_NEIN)
        ],
        "abgelehnt": zaehler.get(STATUS_ABGELEHNT, 0),
        "wirkung": urteile,
    }


def stand_alle(
    db: str, projekte: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    return {pid: stand(db, pid) for pid in projekte}


def als_text(
    liste: Sequence[Empfehlung], db: Optional[str] = None, mit_details: bool = True
) -> str:
    if not liste:
        return "Keine Empfehlungen. ('empfehlungen --projekt X --erzeugen' erstellt welche.)"
    wirkung = wirkung_fuer(db, [e.change_id for e in liste]) if db else {}
    zeilen = []
    for i, e in enumerate(liste, 1):
        ort = (urlparse(e.seite).path or "/") if e.seite else "(ganze Website)"
        w = wirkung.get(e.change_id or "", {})
        wtext = ""
        if e.status == STATUS_UMGESETZT:
            wtext = " · Wirkung: " + (
                ", ".join(f"{f} T {v['urteil']}" for f, v in sorted(w.items()))
                if w
                else "noch nicht gemessen (14/28 Tage)"
            )
        marke = " [Vermutung ohne Suchdaten]" if e.ohne_suchdaten else ""
        zeilen.append(
            f"{i:>2}. [{e.status_klartext}] {ort} – {e.titel}{marke}  (Nutzen {e.prioritaet:g}){wtext}"
        )
        if mit_details:
            for z in e.text.splitlines():
                zeilen.append(f"      {z}")
            if e.notiz:
                zeilen.append(f"      Notiz: {e.notiz}")
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Kundenbericht-Anschluss (C): Befundtyp -> Verweis auf konkrete Empfehlung
# ---------------------------------------------------------------------------

BEFUND_ZU_ARTEN = {
    "thin_content": (ART_ABSCHNITT, ART_FAQ),
    "striking_distance": (ART_LINKS,),
    "low_ctr_opportunity": (ART_TITEL,),
    "missing_h1": (ART_UEBERSCHRIFT,),
    "multiple_h1": (ART_UEBERSCHRIFT,),
    "orphan_page": (ART_LINKS,),
    "weak_cluster_linking": (ART_LINKS,),
}


def verweis_fuer_befund(db: str, projekt: str, typ: str) -> Optional[str]:
    arten = BEFUND_ZU_ARTEN.get(typ)
    if not arten:
        return None
    treffer = [
        e
        for e in laden(
            db, projekt, status=(STATUS_OFFEN, STATUS_FREIGEGEBEN, STATUS_UMGESETZT)
        )
        if e.art in arten
    ]
    if not treffer:
        return None
    seiten = {e.seite for e in treffer}
    return f"Konkrete Vorschläge für {len(seiten)} Seite(n) stehen unter „Was Sie auf Ihren Seiten verbessern können“."
