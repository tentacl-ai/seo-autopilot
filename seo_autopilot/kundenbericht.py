"""
Kunden-Wochenbericht — EIN Bericht, gleich aufgebaut fuer jede Website (seit v1.14.0).

Robert (16.09.2026): "fuer den Josef, fuer den natur-beispiel, fuer tentacl, dass das
identisch ist." Vorher gab es drei Inseln: ein natur-beispiel-eigenes Skript, den
Autopilot-Wochenbericht (nur Telegram, das es nicht mehr gibt) und bei Beispiel-Beratung eine
Mail aus dem alten Lovable-System mit erfundenen Empfehlungen.

Aufbau je Website (alles lesend, eine tote Quelle verhindert nie den Bericht):

  1. Auffaellig            - Hinweise aus allen Abschnitten, oben zuerst
  2. Entscheidungen        - Knoepfe (tentacl.de/freigabe/e), Impulse + offene Freigaben
  3. Google-Suche          - Search Console, Woche gegen Vorwoche, Suchbegriffe, Seiten
  4. Besuche nach Herkunft - GA4
  5. Neu im Markt          - Marktbeobachter (marktradar.py), von Claude auf die Website bezogen
  6. KI-Sichtbarkeit       - ChatGPT, Gemini, Claude mit Websuche
  7. Website-Pruefung      - letzter Audit, Note und Trend, wichtigste Punkte
  8. Bing                  - Stand aus bing_webmaster.py
  9. Zusatz der Website    - `bericht.extras` (z. B. natur-beispiel: Umami, Crawler, Bewerbungen)
 10. Zustand des Werkzeugs - Selbstpruefung: laeuft alles, was ist kaputt

Konfiguration in projects.yaml je Projekt:

    bericht:
      aktiv: true
      empfaenger: empfaenger@beispiel.de
      branche: "Finanzberatung fuer Unternehmer in Niederbayern"
      ki_fragen: konfig/ki-fragen/beratung-beispiel.json
      extras: lokal/extras.py      # optional, Funktion abschnitte()
"""

from __future__ import annotations

import html
import importlib.util
import json
import logging
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yaml

from . import entscheidungen as ent
from .core.config import settings

logger = logging.getLogger(__name__)

WURZEL = Path(__file__).resolve().parents[1]
SCRIPTS = WURZEL / "scripts"
ABLAGE = WURZEL / "reports" / "kundenbericht"
MAILER = settings.MAILER_PFAD or ""
BING_STAND = settings.BING_STATE or ""
GSC_VERZUG_TAGE = 3
MAX_IMPULSE = 3
MAX_FREIGABE_GRUPPEN = 6

ORANGE, TEXT, GRAU, LINIE, GRUEN, ROT = (
    "#e8540a",
    "#1a1a1a",
    "#6f6862",
    "#e7e1d9",
    "#2e7d32",
    "#c62828",
)
SCHRIFT = "-apple-system,Segoe UI,Roboto,sans-serif"


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------


def lade_projekte(projects_pfad: str) -> Dict[str, Dict[str, Any]]:
    daten = yaml.safe_load(Path(projects_pfad).read_text(encoding="utf-8")) or {}
    projekte = daten.get("projects", daten)
    return (
        {k: (v or {}) for k, v in projekte.items()}
        if isinstance(projekte, dict)
        else {}
    )


def berichtsprojekte(projekte: Dict[str, Dict[str, Any]]) -> List[str]:
    return [
        k
        for k, v in projekte.items()
        if v.get("enabled", True) and (v.get("bericht") or {}).get("aktiv")
    ]


def host_von(cfg: Dict[str, Any]) -> str:
    return urlparse(cfg.get("domain", "")).netloc.removeprefix("www.")


def _pfad(wert: str) -> Path:
    p = Path(wert)
    return p if p.is_absolute() else WURZEL / p


# ---------------------------------------------------------------------------
# Quellen
# ---------------------------------------------------------------------------


def _google_zugang(cfg: Dict[str, Any], quelle: str) -> Dict[str, Any]:
    return (
        ((cfg.get("source_config") or {}).get(quelle) or {})
        if quelle in (cfg.get("enabled_sources") or [])
        else {}
    )


def search_console(cfg: Dict[str, Any], heute: Optional[date] = None) -> Dict[str, Any]:
    zugang = _google_zugang(cfg, "gsc")
    if not zugang.get("property_url"):
        return {"fehler": "Search Console nicht angebunden"}
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    cred = service_account.Credentials.from_service_account_file(
        zugang["credentials_path"],
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    sc = build("searchconsole", "v1", credentials=cred, cache_discovery=False)
    ende = (heute or date.today()) - timedelta(days=GSC_VERZUG_TAGE)
    start = ende - timedelta(days=6)

    def abfrage(
        von: date, bis: date, dimension: str, zeilen: int = 25
    ) -> List[Dict[str, Any]]:
        antwort = (
            sc.searchanalytics()
            .query(
                siteUrl=zugang["property_url"],
                body={
                    "startDate": von.isoformat(),
                    "endDate": bis.isoformat(),
                    "dimensions": [dimension],
                    "rowLimit": zeilen,
                },
            )
            .execute()
        )
        return antwort.get("rows", [])

    def summe(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        i = sum(r["impressions"] for r in rows)
        return {
            "klicks": int(sum(r["clicks"] for r in rows)),
            "einblendungen": int(i),
            "position": (
                round(sum(r["position"] * r["impressions"] for r in rows) / i, 1)
                if i
                else None
            ),
        }

    def liste(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        basis = zugang["property_url"].rstrip("/")
        return [
            {
                "name": r["keys"][0].replace(basis, "") or "/",
                "klicks": int(r["clicks"]),
                "einblendungen": int(r["impressions"]),
                "position": round(r["position"], 1),
            }
            for r in rows
        ]

    return {
        "zeitraum": f"{start:%d.%m.}–{ende:%d.%m.%Y}",
        "woche": summe(abfrage(start, ende, "date", 10)),
        "vorwoche": summe(
            abfrage(start - timedelta(days=7), start - timedelta(days=1), "date", 10)
        ),
        "begriffe": liste(abfrage(start, ende, "query", 12)),
        "seiten": liste(abfrage(start, ende, "page", 10)),
    }


def analytics(cfg: Dict[str, Any]) -> Dict[str, Any]:
    zugang = _google_zugang(cfg, "ga4")
    if not zugang.get("property_id"):
        return {"fehler": "Google Analytics nicht angebunden"}
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )
    from google.oauth2 import service_account
    from tagesstand import (
        _ga4_kanal,
    )  # dieselbe Kanal-Einteilung wie der Marketing-Tagesbericht

    cred = service_account.Credentials.from_service_account_file(
        zugang["credentials_path"],
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    cl = BetaAnalyticsDataClient(credentials=cred)
    prop = f"properties/{zugang['property_id']}"
    antwort = cl.run_report(
        RunReportRequest(
            property=prop,
            date_ranges=[DateRange(start_date="7daysAgo", end_date="yesterday")],
            dimensions=[
                Dimension(name="sessionSource"),
                Dimension(name="sessionMedium"),
            ],
            metrics=[Metric(name="sessions"), Metric(name="keyEvents")],
            limit=250,
        )
    )
    je: Dict[str, int] = {}
    ki: Dict[str, int] = {}
    ziele = 0.0
    for r in antwort.rows:
        kanal = _ga4_kanal(r.dimension_values[0].value, r.dimension_values[1].value)
        s = int(r.metric_values[0].value or 0)
        je[kanal] = je.get(kanal, 0) + s
        ziele += float(r.metric_values[1].value or 0)
        if kanal == "KI-Suche organisch":
            ki[r.dimension_values[0].value] = ki.get(r.dimension_values[0].value, 0) + s
    vor = cl.run_report(
        RunReportRequest(
            property=prop,
            date_ranges=[DateRange(start_date="14daysAgo", end_date="8daysAgo")],
            metrics=[Metric(name="sessions"), Metric(name="keyEvents")],
        )
    )
    return {
        "besuche": sum(je.values()),
        "besuche_vorwoche": (
            int(vor.rows[0].metric_values[0].value or 0) if vor.rows else 0
        ),
        "ziele": round(ziele),
        "ziele_vorwoche": (
            round(float(vor.rows[0].metric_values[1].value or 0)) if vor.rows else 0
        ),
        "je_herkunft": dict(sorted(je.items(), key=lambda x: -x[1])),
        "ki_quellen": ki,
    }


def bing(cfg: Dict[str, Any]) -> Dict[str, Any]:
    pfad = Path(BING_STAND.format(host=host_von(cfg)))
    if not pfad.exists():
        return {"fehler": "Bing Webmaster nicht angebunden"}
    d = json.loads(pfad.read_text(encoding="utf-8"))
    return {"stand": str(d.get("stand", ""))[:10], "probleme": d.get("probleme", [])}


def ki_sichtbarkeit(
    bericht: Dict[str, Any],
    vorwoche: Optional[Dict[str, Any]],
    db: Optional[str] = None,
    schluessel: str = "",
    host: str = "",
) -> Dict[str, Any]:
    if not bericht.get("ki_fragen"):
        return {"fehler": "keine KI-Fragen hinterlegt"}
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from ki_sichtbarkeit import pruefen

    aus = pruefen(json.loads(_pfad(bericht["ki_fragen"]).read_text(encoding="utf-8")))
    if vorwoche and "genannt_je_ki" in vorwoche:
        aus["vorwoche"] = vorwoche["genannt_je_ki"]
    if db and schluessel:
        aus["gedaechtnis"] = _ki_gedaechtnis(db, schluessel, host or aus["host"], aus)
    return aus


def _ki_gedaechtnis(
    db: str, schluessel: str, host: str, ergebnis: Dict[str, Any]
) -> Dict[str, Any]:
    """Ergebnis ablegen (beim ersten Mal alte Berichte nachtragen) und auswerten."""
    from . import ki_verlauf

    if not ki_verlauf.verlauf(db, schluessel, laeufe=1):
        ki_verlauf.nachtragen(db, schluessel, ABLAGE / schluessel)
    ki_verlauf.speichere(db, schluessel, ergebnis)
    return ki_verlauf.auswertung(db, schluessel, host)


# Ab hier rechnet der Autopilot die Note nach Ursache statt nach Menge (v1.16.0).
# Aeltere Noten sind mit neueren nicht vergleichbar.
NOTENUMSTELLUNG = "2026-09-18 07:19"


def rangliste_fuer_bericht(db: str, schluessel: str, top: int = 15) -> Dict[str, Any]:
    """Woechentliche Positionen je Suchbegriff (rangliste.py, eigener Cron montags)."""
    from .rangliste import tabelle

    woche, zeilen = tabelle(db, schluessel)
    if not zeilen:
        return {}
    return {
        "woche": woche,
        "gefunden": sum(1 for z in zeilen if z.position is not None),
        "seite1": sum(1 for z in zeilen if z.position is not None and z.position <= 10),
        "begriffe": len(zeilen),
        "zeilen": [z.__dict__ for z in zeilen if z.position is not None][:top],
        "nicht_gefunden": [z.begriff for z in zeilen if z.position is None],
    }


def backlinks_fuer_bericht(db: str, schluessel: str) -> Dict[str, Any]:
    """Verlinkende Websites aus dem Common-Crawl-Graphen (backlinks.py, eigener Cron monatlich)."""
    from .backlinks import auswertung, graph_text

    aus = auswertung(db, schluessel, top=10)
    if aus:
        aus["zeitraum"] = graph_text(aus["graph"])
    return aus


def maps_fuer_bericht(db: str, schluessel: str, domain: str = "") -> Dict[str, Any]:
    """Google-Maps-Eintrag: Sterne, Bewertungen, Platz je Suchbegriff (maps.py, Cron montags)."""
    from .maps import auswertung

    return auswertung(db, schluessel, domain)


def wo_wir_stehen(db: str, schluessel: str, tage: int = 7) -> Dict[str, Any]:
    """Kopfzahlen fuer das Audit "wo wir stehen" (Robert 18.09.2026).

    Note mit Verlauf, Befunde nach Art, was der Autopilot SELBST getan hat und
    was noch aussteht. Alles aus der eigenen Datenbank, keine neuen Abrufe.
    """
    import sqlite3

    aus: Dict[str, Any] = {"selbst_erledigt": 0, "offen": 0, "umgesetzt": 0}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        laeufe = con.execute(
            "select score, started_at, total_pages from seo_audits "
            "where project_id = ? and status = 'completed' "
            "order by started_at desc limit 30",
            (schluessel,),
        ).fetchall()
        if laeufe:
            aus["note"] = round(laeufe[0]["score"] or 0, 1)
            aus["seiten"] = laeufe[0]["total_pages"]
            aelter = [r for r in laeufe[1:] if r["score"] is not None]
            aus["note_vor_woche"] = round(aelter[-1]["score"], 1) if aelter else None
            if aelter and str(aelter[-1]["started_at"]) < NOTENUMSTELLUNG:
                # Der Sprung kaeme sonst wie eine echte Verbesserung rueber
                aus["umgestellt"] = True
            aus["laeufe"] = len(laeufe)
        seit = (datetime.now() - timedelta(days=tage)).isoformat()
        aus["selbst_erledigt"] = con.execute(
            "select count(*) from change_log where project_id = ? "
            "and urheber = 'autopilot' and status = 'angewendet' and zeitpunkt >= ?",
            (schluessel, seit),
        ).fetchone()[0]
    except sqlite3.Error as exc:  # pragma: no cover - defensiv
        aus["fehler"] = str(exc)
    finally:
        con.close()
    try:
        from . import empfehlungen as em

        stand = em.stand(db, schluessel)
        aus["offen"] = len(stand.get("offen", []))
        aus["umgesetzt"] = len(stand.get("umgesetzt", []))
        aus["von_hand"] = len(stand.get("von_hand", []))
        aus["naechste"] = [
            f"{e.get('titel')} ({e.get('seite') or 'ganze Website'})"
            for e in stand.get("offen", [])[:3]
        ]
        aus["wirkung"] = stand.get("wirkung") or {}
    except Exception as exc:  # pragma: no cover - defensiv
        aus.setdefault("fehler", str(exc))
    return aus


def website_pruefung(db: str, projects: str, schluessel: str) -> Dict[str, Any]:
    from .weekly_report import baue_wochenbericht

    stand = next(
        (
            p
            for p in baue_wochenbericht(db, projects).projekte
            if p.schluessel == schluessel
        ),
        None,
    )
    if not stand or not stand.hat_daten:
        return {"fehler": stand.hinweis if stand else "keine Pruefung"}
    return {
        "note": round(stand.score, 1) if stand.score is not None else None,
        "note_vorher": (
            round(stand.score_vorher, 1) if stand.score_vorher is not None else None
        ),
        "trend": stand.trend_text,
        "schwer": stand.schwere_befunde,
        "mittel": stand.mittlere_befunde,
        "seiten": stand.seiten,
        "punkte": [
            {
                "titel": m.titel,
                "empfehlung": m.empfehlung,
                "anzahl": m.anzahl,
                "typ": m.typ,
                # v1.16: Verweis auf die konkrete Empfehlung je Seite, falls vorhanden
                "verweis": _verweis(db, schluessel, m.typ),
            }
            for m in stand.top_punkte
        ],
    }


def _verweis(db: str, schluessel: str, typ: str) -> Optional[str]:
    try:
        from .empfehlungen import verweis_fuer_befund

        return verweis_fuer_befund(db, schluessel, typ)
    except Exception:  # noqa: BLE001 - ein fehlender Verweis ist kein Berichtsfehler
        return None


def werkzeug_zustand(db: str, projects: str, schluessel: str) -> Dict[str, Any]:
    from .health import run_selfcheck

    report = run_selfcheck(db_pfad=db, projects_pfad=projects)
    eigene = [b for b in report.befunde if b.projekt in (schluessel, "-", "*", "")]
    return {
        "befunde": [
            {
                "schwere": b.schwere,
                "titel": b.titel,
                "detail": b.detail,
                "abhilfe": b.abhilfe,
            }
            for b in eigene
        ]
    }


def extras(bericht: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Website-eigene Zusatzabschnitte: Modul mit abschnitte() -> [{titel, html, hinweise}]."""
    if not bericht.get("extras"):
        return []
    pfad = _pfad(bericht["extras"])
    spec = importlib.util.spec_from_file_location(f"extras_{pfad.stem}", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)  # type: ignore[union-attr]
    return list(modul.abschnitte())


# ---------------------------------------------------------------------------
# Impulse aus dem Markt (Claude ordnet zu, erfindet aber keine Quellen)
# ---------------------------------------------------------------------------

IMPULS_AUFTRAG = """Du bist SEO-/SEA-Berater fuer genau diese Website:
{kontext}

Hier sind Neuerungen aus dem Markt der letzten 7 Tage (nummeriert):
{meldungen}

Waehle hoechstens {anzahl} Neuerungen, aus denen fuer DIESE Website in den naechsten Wochen konkret etwas zu tun ist.
Nur wenn es wirklich passt - lieber ein leeres Array als ein Allgemeinplatz. Keine Zahlen erfinden.
Antworte NUR mit JSON: [{{"nr": <Nummer>, "titel": "kurz, Deutsch", "warum": "1 Satz: was ist neu, warum betrifft es die Website", "vorschlag": "1 Satz: was konkret tun"}}]"""


def _website_kontext(cfg: Dict[str, Any], b: Dict[str, Any]) -> str:
    teile = [
        f"- {cfg.get('name')} ({host_von(cfg)})",
        f"- Branche/Angebot: {(cfg.get('bericht') or {}).get('branche', 'unbekannt')}",
    ]
    sc = b.get("search_console", {})
    if sc.get("begriffe"):
        teile.append(
            "- Wichtigste Suchbegriffe: "
            + ", ".join(x["name"] for x in sc["begriffe"][:8])
        )
    wp = b.get("website_pruefung", {})
    if wp.get("punkte"):
        teile.append(
            "- Offene Pruefpunkte: " + "; ".join(p["titel"] for p in wp["punkte"])
        )
    if cfg.get("ads") or (cfg.get("bericht") or {}).get("werbung"):
        teile.append(f"- Werbung: {(cfg.get('bericht') or {}).get('werbung')}")
    return "\n".join(teile)


def impulse(
    cfg: Dict[str, Any],
    b: Dict[str, Any],
    meldungen: List[Dict[str, Any]],
    fragen: Optional[Callable[[str], str]] = None,
) -> List[Dict[str, Any]]:
    """Waehlt passende Marktneuerungen; die URL kommt IMMER aus der Meldung, nie aus der KI."""
    if not meldungen:
        return []
    # Google-News-Eintraege sind nur Weiterleitungen auf fremde Artikel - als Quelle fuer
    # einen Knopf taugen sie nicht (Robert soll den Originalartikel lesen koennen).
    liste = [m for m in meldungen if "news.google.com" not in m.get("url", "")][:40]
    text = "\n".join(
        f"{i}. [{m.get('relevanz')}] {m['titel']} — {str(m.get('was_neu') or '')[:220]}"
        for i, m in enumerate(liste, 1)
    )
    auftrag = IMPULS_AUFTRAG.format(
        kontext=_website_kontext(cfg, b), meldungen=text, anzahl=MAX_IMPULSE
    )
    fragen = fragen or _claude_text
    from .marktradar import json_liste_aus_text

    aus = []
    for e in json_liste_aus_text(fragen(auftrag))[:MAX_IMPULSE]:
        try:
            m = liste[int(e["nr"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if not e.get("titel") or not e.get("vorschlag"):
            continue
        aus.append(
            {
                "titel": str(e["titel"])[:120],
                "warum": str(e.get("warum", ""))[:300],
                "vorschlag": str(e["vorschlag"])[:300],
                "url": m["url"],
                "quelle": m.get("quelle", ""),
            }
        )
    return aus


def _claude_text(auftrag: str) -> str:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from ki_sichtbarkeit import CLAUDE_MODELL

    from . import abo_ki  # Roberts Max-Abo statt bezahlter API (16.09.2026)

    return abo_ki.fragen(auftrag, modell=CLAUDE_MODELL, zeitlimit=300)


# ---------------------------------------------------------------------------
# Entscheidungen
# ---------------------------------------------------------------------------


def aktuelle_befundtypen(db: str, schluessel: str) -> Optional[set]:
    """Befundtypen des juengsten Audits; None, wenn es keinen gibt (dann nichts schliessen)."""
    import sqlite3

    try:
        con = sqlite3.connect(db)
        zeile = con.execute(
            "select audit_id from seo_issues where project_id = ? order by detected_at desc limit 1",
            (schluessel,),
        ).fetchone()
        if not zeile:
            return None
        typen = {
            r[0]
            for r in con.execute(
                "select distinct type from seo_issues where audit_id = ?", (zeile[0],)
            )
        }
        con.close()
    except sqlite3.Error:
        return None
    return typen or None


def freigabe_gruppen(db: str, schluessel: str) -> List[Dict[str, Any]]:
    """Offene Freigaben je Befundtyp buendeln - ein Knopf je Art statt 30 Einzelknoepfe."""
    from .ausfuehrung import entscheiden, freigaben
    from .weekly_report import _klartext

    aktuell = aktuelle_befundtypen(db, schluessel)
    gruppen: Dict[str, Dict[str, Any]] = {}
    for f in freigaben(db, project_id=schluessel):
        if f.ist_gesperrt:
            continue
        if aktuell is not None and f.issue_type not in aktuell:
            # Selbstheilung: Der Befund steht im letzten Audit nicht mehr (behoben oder
            # Fehlalarm korrigiert). Kein Knopf fuer etwas, das es nicht mehr gibt.
            entscheiden(
                db,
                f.id,
                "abgelehnt",
                von="kundenbericht",
                notiz="Befund im letzten Audit nicht mehr vorhanden - automatisch geschlossen",
            )
            continue
        g = gruppen.setdefault(
            f.issue_type,
            {"typ": f.issue_type, "ids": [], "seiten": [], "beispiel": f.vorschlag},
        )
        g["ids"].append(f.id)
        if f.ziel_url:
            g["seiten"].append(f.ziel_url)
    aus = []
    for typ, g in sorted(gruppen.items(), key=lambda x: -len(x[1]["ids"]))[
        :MAX_FREIGABE_GRUPPEN
    ]:
        titel, empfehlung = _klartext(typ, typ)
        g["titel"], g["empfehlung"] = titel, empfehlung
        aus.append(g)
    return aus


def entscheidungen_anlegen(
    schluessel: str, b: Dict[str, Any], heute: date
) -> List[Dict[str, Any]]:
    """Macht aus Impulsen und Freigabe-Gruppen Knoepfe; merkt sich, was ein 'Ja' bedeutet."""
    punkte, bedeutung = [], []
    kw = heute.isocalendar()
    for i, imp in enumerate(b.get("impulse", []), 1):
        eid = f"seo-{schluessel}-impuls-{kw.year}w{kw.week:02d}-{i}"
        punkte.append(
            {
                "id": eid,
                "titel": f"{b['name']}: {imp['titel']}",
                "kanal": f"SEO · Neu im Markt · {b['name']}",
                "text": f"{html.escape(imp['warum'])}<br><b>Vorschlag:</b> {html.escape(imp['vorschlag'])}",
                "link": imp["url"],
                "knopf": "Quelle lesen",
                "optionen": [
                    ["ja", "Ja, umsetzen"],
                    ["spaeter", "Später"],
                    ["nein", "Nein"],
                ],
            }
        )
        bedeutung.append({"id": eid, "art": "impuls", "titel": imp["titel"]})
    for g in b.get("freigaben", []):
        eid = f"seo-{schluessel}-freigabe-{g['typ']}"
        seiten = "".join(f"<li>{html.escape(s)}</li>" for s in g["seiten"][:8])
        punkte.append(
            {
                "id": eid,
                "titel": f"{b['name']}: {g['titel']} ({len(g['ids'])}×)",
                "kanal": f"SEO · Freigabe · {b['name']}",
                "text": f"{html.escape(g['empfehlung'])}<ul>{seiten}</ul>",
                "optionen": [
                    ["ja", "Ja, freigeben"],
                    ["spaeter", "Später"],
                    ["nein", "Nein, ablehnen"],
                ],
            }
        )
        bedeutung.append(
            {
                "id": eid,
                "art": "freigabe",
                "freigabe_ids": g["ids"],
                "titel": g["titel"],
            }
        )
    p2, b2 = _empfehlungs_punkte(schluessel, b)
    punkte += p2
    bedeutung += b2
    urls = ent.punkte_anlegen(punkte, heute=heute)
    for d in bedeutung:
        d["url"] = urls.get(d["id"])
    return [d for d in bedeutung if d["url"]]


def antworten_uebernehmen(db: str, schluessel: str, ablage: Path = ABLAGE) -> List[str]:
    """Klicks aus frueheren Berichten in die Freigabe-Schlange uebertragen (Freigaben)
    bzw. als Auftrag festhalten (Impulse). Laeuft vor jedem neuen Bericht."""
    from .ausfuehrung import entscheiden

    erledigt_pfad = ablage / schluessel / "uebernommen.json"
    erledigt = (
        set(json.loads(erledigt_pfad.read_text())) if erledigt_pfad.exists() else set()
    )
    bedeutungen: Dict[str, Dict[str, Any]] = {}
    for alt in sorted((ablage / schluessel).glob("bericht-*.json")):
        for d in json.loads(alt.read_text(encoding="utf-8")).get("entscheidungen", []):
            bedeutungen[d["id"]] = d
    meldungen = []
    for eid, antwort in ent.antworten(ids=list(bedeutungen)).items():
        if eid in erledigt:
            continue
        d, wahl = bedeutungen[eid], antwort.get("wahl", "")
        if d["art"] == "freigabe":
            status, notiz = ent.wahl_zu_status(wahl)
            if status:
                n = sum(
                    entscheiden(
                        db,
                        fid,
                        status,
                        von="robert-knopf",
                        notiz=(antwort.get("text") or notiz),
                    )
                    for fid in d["freigabe_ids"]
                )
                meldungen.append(f"{d['titel']}: {n}× {status}")
        elif d["art"] == "empfehlung":
            # "Später" aendert nichts: die Empfehlung bleibt offen und bekommt im
            # naechsten Bericht einen neuen Knopf (Kennung enthaelt die Woche).
            meldung = _empfehlung_entschieden(db, d, wahl)
            if meldung:
                meldungen.append(meldung)
        elif wahl == "ja":
            meldungen.append(f"Impuls angenommen, wartet auf Umsetzung: {d['titel']}")
        erledigt.add(eid)
    erledigt_pfad.parent.mkdir(parents=True, exist_ok=True)
    erledigt_pfad.write_text(json.dumps(sorted(erledigt)))
    return meldungen


# ---------------------------------------------------------------------------
# Empfehlungen je Seite (v1.16) - "Was Sie auf Ihren Seiten verbessern koennen"
# ---------------------------------------------------------------------------

MAX_EMPFEHLUNGEN = 5


def empfehlungen_fuer_bericht(db: str, schluessel: str) -> Dict[str, Any]:
    """Top-5 offene Empfehlungen und was in den letzten 7 Tagen umgesetzt wurde."""
    from . import empfehlungen as em

    def kurz(e: Any) -> Dict[str, Any]:
        return {
            "id": e.id,
            "seite": (urlparse(e.seite).path or "/") if e.seite else "",
            "art": e.art,
            "titel": e.titel,
            "text": e.text,
            "vermutung": e.ohne_suchdaten,
            "umgesetzt_am": e.umgesetzt_am,
        }

    offen = em.laden(db, schluessel, status=(em.STATUS_OFFEN,))
    grenze = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    umgesetzt = [
        e
        for e in em.laden(db, schluessel, status=(em.STATUS_UMGESETZT,))
        if (e.umgesetzt_am or "") >= grenze
    ]
    return {
        "offen": [kurz(e) for e in offen[:MAX_EMPFEHLUNGEN]],
        "offen_gesamt": len(offen),
        "umgesetzt": [kurz(e) for e in umgesetzt],
    }


def _empfehlungs_punkte(
    schluessel: str, b: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    daten = b.get("empfehlungen") if isinstance(b.get("empfehlungen"), dict) else {}
    kw = date.today().isocalendar()
    punkte, bedeutung = [], []
    for e in daten.get("offen", []):
        eid = f"seo-{schluessel}-empf-{e['id'][:10]}-{kw.year}w{kw.week:02d}"
        punkte.append(
            {
                "id": eid,
                "titel": f"{b['name']}: {e['titel']} ({e['seite'] or 'ganze Website'})",
                "kanal": f"SEO · Seite verbessern · {b['name']}",
                "text": html.escape(e["text"]).replace("\n", "<br>"),
                "optionen": [
                    ["ja", "Ja, umsetzen"],
                    ["spaeter", "Später"],
                    ["nein", "Nein"],
                ],
            }
        )
        bedeutung.append(
            {
                "id": eid,
                "art": "empfehlung",
                "empfehlung_id": e["id"],
                "titel": e["titel"],
            }
        )
    return punkte, bedeutung


def _empfehlung_entschieden(db: str, d: Dict[str, Any], wahl: str) -> Optional[str]:
    from . import empfehlungen as em

    status = {"ja": em.STATUS_FREIGEGEBEN, "nein": em.STATUS_ABGELEHNT}.get(wahl)
    if not status or not em.status_setzen(db, d["empfehlung_id"], status):
        return None
    return f"Empfehlung {'freigegeben' if wahl == 'ja' else 'abgelehnt'}: {d['titel']}"


def _abschnitt_empfehlungen(b: Dict[str, Any]) -> List[str]:
    daten = b.get("empfehlungen") if isinstance(b.get("empfehlungen"), dict) else {}
    if not daten.get("offen") and not daten.get("umgesetzt"):
        return []
    t = [_h("Was Sie auf Ihren Seiten verbessern können")]
    if daten.get("umgesetzt"):
        t.append(
            _p(
                "<b>Diese Woche umgesetzt:</b> "
                + "; ".join(
                    f"{_e(e['titel'])} ({_e(e['seite'] or 'Website')})"
                    for e in daten["umgesetzt"]
                )
            )
        )
    knoepfe = {
        d.get("empfehlung_id"): d.get("url")
        for d in b.get("entscheidungen", [])
        if d.get("art") == "empfehlung"
    }
    for e in daten.get("offen", []):
        zeilen = e["text"].split("\n")
        inhalt = _p(
            f"<span style='color:{GRAU};font-size:12px'>{_e(e['seite'] or 'ganze Website')}"
            f"{' · Vermutung ohne Suchdaten' if e.get('vermutung') else ''}</span><br><b>{_e(e['titel'])}</b>"
        ) + _p(_e(zeilen[0]))
        if len(zeilen) > 1:
            inhalt += (
                "<ul style='margin:4px 0;padding-left:18px'>"
                + "".join(
                    f"<li style='font:400 14px/1.5 {SCHRIFT}'>{_e(z.lstrip('• '))}</li>"
                    for z in zeilen[1:]
                )
                + "</ul>"
            )
        if knoepfe.get(e["id"]):
            inhalt += ent.knopf_html(knoepfe[e["id"]])
        t.append(_karte(inhalt))
    rest = daten.get("offen_gesamt", 0) - len(daten.get("offen", []))
    if rest > 0:
        t.append(
            _p(f"… und {rest} weitere Vorschläge in der Arbeitsliste.", klein=True)
        )
    t.append(
        _p(
            "Texte stammen nur aus Angaben Ihrer Website; fehlt eine Angabe, steht dort "
            "„[Antwort vom Betreiber ergänzen]“.",
            klein=True,
        )
    )
    return t


# ---------------------------------------------------------------------------
# Zusammenbauen
# ---------------------------------------------------------------------------


def _letzter_bericht(schluessel: str, ablage: Path = ABLAGE) -> Dict[str, Any]:
    alle = sorted((ablage / schluessel).glob("bericht-*.json"))
    return json.loads(alle[-1].read_text(encoding="utf-8")) if alle else {}


def _versuche(b: Dict[str, Any], name: str, fn: Callable[[], Any]) -> None:
    try:
        b[name] = fn()
    except (
        Exception
    ) as exc:  # noqa: BLE001 - eine tote Quelle darf den Bericht nicht verhindern
        b[name] = {"fehler": f"{type(exc).__name__}: {str(exc)[:160]}"}
        logger.warning(f"[Kundenbericht] {name} nicht lesbar: {exc}")


def sammeln(
    schluessel: str,
    db: str,
    projects: str,
    heute: Optional[date] = None,
    mit_ki: bool = True,
) -> Dict[str, Any]:
    heute = heute or date.today()
    cfg = lade_projekte(projects)[schluessel]
    bericht_cfg = cfg.get("bericht") or {}
    vorher = _letzter_bericht(schluessel)
    b: Dict[str, Any] = {
        "projekt": schluessel,
        "name": cfg.get("name") or schluessel,
        "host": host_von(cfg),
        "stand": datetime.now().isoformat(timespec="minutes"),
        "selbst_ausfuehrbar": cfg.get("adapter_type") == "static",
    }
    _versuche(b, "uebernommen", lambda: antworten_uebernehmen(db, schluessel))
    _versuche(b, "search_console", lambda: search_console(cfg, heute))
    _versuche(b, "analytics", lambda: analytics(cfg))
    _versuche(b, "bing", lambda: bing(cfg))
    _versuche(b, "wo_wir_stehen", lambda: wo_wir_stehen(db, schluessel))
    _versuche(b, "rangliste", lambda: rangliste_fuer_bericht(db, schluessel))
    _versuche(b, "backlinks", lambda: backlinks_fuer_bericht(db, schluessel))
    _versuche(
        b, "maps", lambda: maps_fuer_bericht(db, schluessel, cfg.get("domain", ""))
    )
    _versuche(b, "website_pruefung", lambda: website_pruefung(db, projects, schluessel))
    _versuche(b, "werkzeug", lambda: werkzeug_zustand(db, projects, schluessel))
    _versuche(b, "extras", lambda: extras(bericht_cfg))
    if mit_ki:
        _versuche(
            b,
            "ki_sichtbarkeit",
            lambda: ki_sichtbarkeit(
                bericht_cfg,
                vorher.get("ki_sichtbarkeit"),
                db=db,
                schluessel=schluessel,
                host=b["host"],
            ),
        )
        from .marktradar import neueste

        _versuche(b, "markt", lambda: neueste(db, tage=7)[:40])
        _versuche(
            b,
            "impulse",
            lambda: impulse(
                cfg, b, b["markt"] if isinstance(b.get("markt"), list) else []
            ),
        )
    _versuche(b, "freigaben", lambda: freigabe_gruppen(db, schluessel))
    _versuche(b, "empfehlungen", lambda: empfehlungen_fuer_bericht(db, schluessel))
    for name in ("impulse", "freigaben"):
        if not isinstance(b.get(name), list):
            b[name] = []
    b["hinweise"] = hinweise(b)
    return b


def hinweise(b: Dict[str, Any]) -> List[str]:
    h: List[str] = []
    sc = b.get("search_console", {})
    if "woche" in sc and sc["vorwoche"]["klicks"] >= 5:
        w, v = sc["woche"]["klicks"], sc["vorwoche"]["klicks"]
        if w <= v * 0.7:
            h.append(f"Google-Klicks gefallen: {w} statt {v} in der Vorwoche.")
        elif w >= v * 1.3:
            h.append(f"Google-Klicks gestiegen: {w} statt {v} in der Vorwoche.")
    marke = [x for x in sc.get("begriffe", []) if x["klicks"]]
    if marke and len(marke) == 1 and sc.get("woche", {}).get("klicks"):
        h.append(
            f"Alle Google-Klicks kommen über einen einzigen Suchbegriff („{marke[0]['name']}“)."
        )
    wp = b.get("website_pruefung", {})
    if (
        wp.get("note") is not None
        and wp.get("note_vorher") is not None
        and wp["note"] - wp["note_vorher"] <= -5
    ):
        h.append(f"Prüfnote gesunken: {wp['note']} (vorher {wp['note_vorher']}).")
    ks = b.get("ki_sichtbarkeit", {})
    if "genannt_je_ki" in ks and not any(ks["genannt_je_ki"].values()):
        h.append(
            "Keine KI (ChatGPT, Gemini, Claude) nennt die Website bei den Testfragen."
        )
    bg = b.get("bing", {})
    if bg.get("probleme"):
        h.append(f"Bing meldet {len(bg['probleme'])} Crawl-Problem(e).")
    kritisch = [
        x
        for x in b.get("werkzeug", {}).get("befunde", [])
        if x["schwere"] == "kritisch"
    ]
    if kritisch:
        h.append(f"Werkzeug: {len(kritisch)} kritische Störung(en) – siehe unten.")
    for e in b.get("extras", []) if isinstance(b.get("extras"), list) else []:
        h += e.get("hinweise", [])
    fehler = [
        n
        for n, v in b.items()
        if isinstance(v, dict)
        and "fehler" in v
        and "nicht angebunden" not in v["fehler"]
    ]
    if fehler:
        h.append("Nicht lesbar diese Woche: " + ", ".join(fehler) + ".")
    return h


# ---------------------------------------------------------------------------
# Mail (Inline-Styles, Gmail wirft <style> weg - E453)
# ---------------------------------------------------------------------------


def _e(x: Any) -> str:
    return html.escape(str(x))


def _h(titel: str) -> str:
    return f'<h3 style="font:700 17px {SCHRIFT};color:{TEXT};margin:28px 0 8px;border-top:1px solid {LINIE};padding-top:18px">{_e(titel)}</h3>'


def _p(inhalt: str, klein: bool = False) -> str:
    return f'<p style="font:400 {13 if klein else 15}px/1.55 {SCHRIFT};color:{GRAU if klein else TEXT};margin:4px 0">{inhalt}</p>'


def _vergleich(jetzt: Any, vorher: Any) -> str:
    try:
        d = float(jetzt) - float(vorher)
    except (TypeError, ValueError):
        return _e(jetzt)
    farbe = GRUEN if d > 0 else ROT if d < 0 else GRAU
    return f'<b>{_e(jetzt)}</b> <span style="color:{farbe}">({"+" if d > 0 else ""}{d:g} zur Vorwoche)</span>'


def _tabelle(kopf: List[str], zeilen: List[List[Any]]) -> str:
    z = f'style="border-bottom:1px solid {LINIE};padding:5px 8px;font:400 13px {SCHRIFT};text-align:left;vertical-align:top"'
    k = "".join(f"<th {z}>{_e(x)}</th>" for x in kopf)
    rumpf = "".join(
        "<tr>" + "".join(f"<td {z}>{_e(c)}</td>" for c in r) + "</tr>" for r in zeilen
    )
    return f'<table role="presentation" style="border-collapse:collapse;width:100%;max-width:640px">{k and "<tr>" + k + "</tr>"}{rumpf}</table>'


def _karte(inhalt: str, farbe: str = ORANGE) -> str:
    return f'<div style="border-left:4px solid {farbe};background:#fff;padding:12px 16px;margin:10px 0;border-radius:0 10px 10px 0">{inhalt}</div>'


def als_html(b: Dict[str, Any]) -> str:
    t = [
        f'<div style="background:#faf9f7;padding:20px 16px"><div style="max-width:680px;margin:0 auto">',
        f'<h2 style="font:700 24px {SCHRIFT};color:{TEXT};margin:0">{_e(b["name"])} – Wochenbericht</h2>',
        _p(
            f'{_e(b["host"])} · {datetime.fromisoformat(b["stand"]):%d.%m.%Y}',
            klein=True,
        ),
    ]
    if b["hinweise"]:
        t.append(
            _karte(
                "<b>Auffällig</b><ul style='margin:6px 0 0;padding-left:18px'>"
                + "".join(f"<li>{_e(x)}</li>" for x in b["hinweise"])
                + "</ul>"
            )
        )
    t += _abschnitt_stand(b)
    t += (
        _abschnitt_entscheidungen(b)
        + _abschnitt_empfehlungen(b)
        + _abschnitt_google(b)
        + _abschnitt_rangliste(b)
        + _abschnitt_backlinks(b)
        + _abschnitt_maps(b)
        + _abschnitt_analytics(b)
        + _abschnitt_markt(b)
    )
    t += _abschnitt_ki(b) + _abschnitt_pruefung(b) + _abschnitt_bing(b)
    for e in b.get("extras", []) if isinstance(b.get("extras"), list) else []:
        t += [_h(e["titel"]), e["html"]]
    t += _abschnitt_werkzeug(b)
    t.append(
        _p(
            "Automatisch erstellt vom SEO-Autopiloten. Zahlen aus Search Console, Google Analytics, Bing; "
            "Neuerungen nur mit geprüfter Quelle.",
            klein=True,
        )
    )
    t.append("</div></div>")
    return "".join(t)


def _abschnitt_stand(b: Dict[str, Any]) -> List[str]:
    """Kopf des Audits: Note, Verlauf, was der Autopilot selbst erledigt hat."""
    # NICHT b["stand"] - das ist seit jeher der Zeitstempel des Berichts
    st = b.get("wo_wir_stehen") or {}
    if "note" not in st:
        return []
    verlauf = (
        _vergleich(st["note"], st["note_vor_woche"])
        if st.get("note_vor_woche") is not None
        else _e(st["note"])
    )
    zeilen = [
        _h("Wo wir stehen"),
        _p(f"Note {verlauf} von 100 · {st.get('seiten') or '?'} Seiten geprüft"),
    ]
    if st.get("umgestellt"):
        zeilen.append(
            _p(
                "Die Bewertung wurde am 18.09.2026 umgestellt: Eine Ursache zählt jetzt "
                "einmal statt vielfach, und Faustregeln zählen weniger als echte Fehler. "
                "Der Sprung kommt von der neuen Rechnung, nicht von der Website.",
                klein=True,
            )
        )
    zeilen += [
        _p(
            f"Der Autopilot hat in den letzten 7 Tagen {st['selbst_erledigt']} Änderung(en) "
            f"selbst umgesetzt. Umgesetzte Empfehlungen gesamt: {st.get('umgesetzt', 0)} · "
            f"offen: {st.get('offen', 0)}"
            + (
                f" · von Hand zu erledigen: {st['von_hand']}"
                if st.get("von_hand")
                else ""
            )
        ),
    ]
    w = st.get("wirkung") or {}
    gemessen = w.get("besser", 0) + w.get("schlechter", 0) + w.get("unveraendert", 0)
    if gemessen:
        zeilen.append(
            _p(
                f"Gemessene Wirkung: {w.get('besser', 0)} besser, "
                f"{w.get('schlechter', 0)} schlechter, {w.get('unveraendert', 0)} unverändert "
                f"(Rest wartet auf die 14- und 28-Tage-Messung).",
                klein=True,
            )
        )
    if st.get("naechste"):
        zeilen.append(
            "<ul style='padding-left:18px'>"
            + "".join(
                f"<li style='font:400 14px/1.5 {SCHRIFT}'>Als Nächstes: {_e(x)}</li>"
                for x in st["naechste"]
            )
            + "</ul>"
        )
    return zeilen


def _abschnitt_entscheidungen(b: Dict[str, Any]) -> List[str]:
    t: List[str] = []
    if b.get("uebernommen"):
        t += [
            _h("Deine Antworten seit dem letzten Bericht"),
            "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in b["uebernommen"]) + "</ul>",
        ]
    # Empfehlungs-Knoepfe stehen in ihrem eigenen Abschnitt
    punkte = [d for d in b.get("entscheidungen", []) if d.get("art") != "empfehlung"]
    if not punkte:
        return t
    t.append(_h(f"Entscheidungen ({len(punkte)})"))
    t.append(
        _p(
            "Ein Klick genügt – auf der Seite stehen danach alle offenen Punkte zum Durchklicken.",
            klein=True,
        )
    )
    for d in punkte:
        text = ""
        if d["art"] == "impuls":
            imp = next(
                (i for i in b.get("impulse", []) if i["titel"] == d["titel"]), {}
            )
            text = _p(_e(imp.get("warum", ""))) + _p(
                "<b>Vorschlag:</b> " + _e(imp.get("vorschlag", ""))
            )
        else:
            g = next(
                (g for g in b.get("freigaben", []) if g["titel"] == d["titel"]), {}
            )
            text = _p(
                f"{_e(g.get('empfehlung', ''))} – {len(d.get('freigabe_ids', []))} Stelle(n)"
            )
            if not b.get("selbst_ausfuehrbar"):
                text += _p(
                    "Für diese Website gibt es keinen automatischen Zugriff auf den Code: "
                    "Ein „Ja“ landet als Auftrag bei Claude und wird von Hand umgesetzt.",
                    klein=True,
                )
        art = "Neu im Markt" if d["art"] == "impuls" else "Freigabe"
        t.append(
            _karte(
                _p(
                    f"<span style='color:{GRAU};font-size:12px'>{art}</span><br><b>{_e(d['titel'])}</b>"
                )
                + text
                + ent.knopf_html(d["url"])
            )
        )
    return t


def _abschnitt_google(b: Dict[str, Any]) -> List[str]:
    sc = b.get("search_console", {})
    if "woche" not in sc:
        return [_h("Google-Suche"), _p(_e(sc.get("fehler", "keine Daten")), klein=True)]
    w, v = sc["woche"], sc["vorwoche"]
    t = [
        _h(f"Google-Suche ({sc['zeitraum']})"),
        _p(
            f"Klicks {_vergleich(w['klicks'], v['klicks'])} · Einblendungen {_vergleich(w['einblendungen'], v['einblendungen'])}"
            f" · Ø Position {_e(w['position'])} (Vorwoche {_e(v['position'])})"
        ),
    ]
    if sc["begriffe"]:
        t.append(
            _tabelle(
                ["Suchbegriff", "Einbl.", "Klicks", "Pos."],
                [
                    [x["name"], x["einblendungen"], x["klicks"], x["position"]]
                    for x in sc["begriffe"]
                ],
            )
        )
    if sc["seiten"]:
        t += [
            _p("<b>Seiten</b>"),
            _tabelle(
                ["Seite", "Einbl.", "Klicks", "Pos."],
                [
                    [x["name"], x["einblendungen"], x["klicks"], x["position"]]
                    for x in sc["seiten"]
                ],
            ),
        ]
    return t


def _abschnitt_rangliste(b: Dict[str, Any]) -> List[str]:
    rl = b.get("rangliste")
    if not isinstance(rl, dict) or "woche" not in rl:
        return []
    from .rangliste import _p as pos, _trend

    return [
        _h(f"Rangliste der wichtigsten Suchbegriffe (Woche {rl['woche']})"),
        _p(
            f"{rl['gefunden']} von {rl['begriffe']} Begriffen bei Google gefunden, "
            f"{rl['seite1']} davon auf Seite 1. Position 1 ist ganz oben; "
            "▲ = nach vorn gerückt."
        ),
        _tabelle(
            ["Suchbegriff", "Pos.", "Vorwoche", "4 Wochen", "Handy", "PC"],
            [
                [
                    z["begriff"],
                    pos(z["position"]),
                    _trend(z["position"], z["vorwoche"]),
                    _trend(z["position"], z["vor_4_wochen"]),
                    pos(z["handy"]),
                    pos(z["computer"]),
                ]
                for z in rl["zeilen"]
            ],
        ),
    ] + (
        [
            _p(
                "Diese Woche nicht bei Google gefunden: "
                + _e(", ".join(rl["nicht_gefunden"])),
                klein=True,
            )
        ]
        if rl.get("nicht_gefunden")
        else []
    )


def _abschnitt_backlinks(b: Dict[str, Any]) -> List[str]:
    bl = b.get("backlinks")
    if not isinstance(bl, dict) or "graph" not in bl:
        return []
    kopf = _h(f"Wer auf die Website verlinkt (Crawl {bl['zeitraum']})")
    if not bl["im_graphen"]:
        return [
            kopf,
            _p(
                "Die Website steht im öffentlichen Link-Verzeichnis von Common Crawl noch "
                "nicht drin (zu neu oder bisher zu selten verlinkt).",
                klein=True,
            ),
        ]
    vergleich = (
        f" (vorher {bl['davor']})" if bl.get("davor") is not None else " (erster Stand)"
    )
    t = [kopf, _p(f"<b>{bl['domains']}</b> Websites verlinken hierher{vergleich}.")]
    if bl["neu"]:
        t.append(_p("Neu: " + _e(", ".join(bl["neu"][:15])), klein=True))
    if bl["weg"]:
        t.append(
            _p("Nicht mehr verlinkt: " + _e(", ".join(bl["weg"][:15])), klein=True)
        )
    if bl["wichtigste"]:
        t.append(
            _tabelle(
                ["Website", "Bedeutung (Platz im Netz)"],
                [
                    [
                        w["domain"],
                        f"{w['rang']:,}".replace(",", ".") if w["rang"] else "–",
                    ]
                    for w in bl["wichtigste"]
                ],
            )
        )
    return t


def _platz_text(n: Optional[int]) -> str:
    return f"Platz {n}" if n else "nicht unter 60"


def _abschnitt_maps(b: Dict[str, Any]) -> List[str]:
    m = b.get("maps")
    if not isinstance(m, dict) or "woche" not in m:
        return []
    erste = m.get("bewertungen_davor") is None
    if not m["bewertungen"]:
        satz = "Noch keine Bewertungen im Eintrag."
    else:
        sterne = f"{m['sterne']:.1f}".replace(".", ",")
        satz = f"<b>{sterne} Sterne</b> aus <b>{m['bewertungen']}</b> Bewertungen"
        if not erste:
            d = m["bewertungen"] - m["bewertungen_davor"]
            satz += f" ({'+' if d >= 0 else ''}{d} seit letzter Woche)"
        satz += "."
    t = [_h("Google Maps"), _p(satz)]
    if m["plaetze"]:
        zeilen = []
        for z in m["plaetze"]:
            vorher = (
                "erste Messung"
                if erste
                else ("neu" if z["neu"] else _platz_text(z["davor"]))
            )
            zeilen.append([z["begriff"], z["ort"], _platz_text(z["platz"]), vorher])
        t.append(
            _tabelle(["Suche in Maps", "Gesucht von", "Platz", "Vorwoche"], zeilen)
        )
    for h in m.get("hinweise") or []:
        t.append(_p("Hinweis: " + _e(h), klein=True))
    return t


def _abschnitt_analytics(b: Dict[str, Any]) -> List[str]:
    ga = b.get("analytics", {})
    if "besuche" not in ga:
        return [
            _h("Besuche nach Herkunft"),
            _p(_e(ga.get("fehler", "keine Daten")), klein=True),
        ]
    t = [
        _h("Besuche nach Herkunft (Google Analytics, letzte 7 Tage)"),
        _p(
            f"Besuche {_vergleich(ga['besuche'], ga['besuche_vorwoche'])} · Zielaktionen {_vergleich(ga['ziele'], ga['ziele_vorwoche'])}"
        ),
        _tabelle(
            ["Herkunft", "Besuche"], [[k, v] for k, v in ga["je_herkunft"].items()]
        ),
    ]
    if ga["ki_quellen"]:
        t.append(
            _p(
                "davon aus KI-Suche: "
                + ", ".join(f"{_e(k)} {v}" for k, v in ga["ki_quellen"].items()),
                klein=True,
            )
        )
    t.append(_p("Analytics zählt nur Besucher, die Cookies akzeptieren.", klein=True))
    return t


def _abschnitt_markt(b: Dict[str, Any]) -> List[str]:
    markt = b.get("markt") if isinstance(b.get("markt"), list) else []
    if not markt:
        return []
    t = [_h("Neu im Markt (SEO, KI-Suche, Werbung, Messung)")]
    if not b.get("impulse"):
        t.append(
            _p(
                "Diese Woche passt keine Neuerung konkret zu dieser Website.",
                klein=True,
            )
        )
    wichtig = [m for m in markt if m.get("relevanz") == "hoch"][:5] or markt[:5]
    t.append(
        "<ul style='padding-left:18px'>"
        + "".join(
            f"<li style='font:400 14px/1.5 {SCHRIFT};margin-bottom:4px'><a href='{_e(m['url'])}' style='color:{ORANGE}'>{_e(m['titel'])}</a>"
            f" <span style='color:{GRAU};font-size:12px'>({_e(m.get('quelle', ''))}{', ' + _e(m['datum']) if m.get('datum') else ''})</span></li>"
            for m in wichtig
        )
        + "</ul>"
    )
    return t


def _abschnitt_ki(b: Dict[str, Any]) -> List[str]:
    ks = b.get("ki_sichtbarkeit", {})
    if "ergebnisse" not in ks:
        return (
            [
                _h("KI-Sichtbarkeit"),
                _p(_e(ks.get("fehler", "nicht geprüft")), klein=True),
            ]
            if ks
            else []
        )
    vor = ks.get("vorwoche") or {}
    t = [
        _h("KI-Sichtbarkeit (ChatGPT, Gemini, Claude – mit Websuche)"),
        _p(
            "Genannt: "
            + " · ".join(
                f"{k} {v} von {ks['fragen']}"
                + (f" (Vorwoche {vor.get(k, 0)})" if vor else "")
                for k, v in ks["genannt_je_ki"].items()
            )
        ),
    ]
    kis = list(ks["genannt_je_ki"])
    t.append(
        _tabelle(
            ["Frage", *kis],
            [
                [
                    f,
                    *[
                        je.get(k, {}).get("genannt", "–").replace("nein", "–")
                        for k in kis
                    ],
                ]
                for f, je in ks["ergebnisse"].items()
            ],
        )
    )
    t += _ki_gedaechtnis_html(ks.get("gedaechtnis") or {})
    t.append(
        _p(
            "verlinkt = Link auf die Website, erwaehnt = nur Name. Antworten schwanken – der Verlauf zählt.",
            klein=True,
        )
    )
    return t


def _ki_gedaechtnis_html(g: Dict[str, Any]) -> List[str]:
    """Verlauf, meistzitierte Quellen, Wettbewerber. Keine Punktzahl, nur Beobachtetes."""
    t: List[str] = []
    laeufe = g.get("verlauf") or []
    if len(laeufe) > 1:
        kis = sorted({k for l in laeufe for k in l["je_ki"]})
        t.append(
            _tabelle(
                ["Verlauf", *kis],
                [
                    [
                        datetime.fromisoformat(l["datum"]).strftime("%d.%m."),
                        *[
                            (
                                f"{l['je_ki'][k]['genannt']} von {l['je_ki'][k]['von']}"
                                if k in l["je_ki"]
                                else "–"
                            )
                            for k in kis
                        ],
                    ]
                    for l in laeufe[-6:]
                ],
            )
        )
    if g.get("quellen"):
        t.append(
            _p(
                "<b>Hier holen sich die KIs ihre Antworten</b> (letzte Wochen): "
                + _e(
                    ", ".join(
                        f"{q['domain']} ({q['antworten']}×)" for q in g["quellen"][:6]
                    )
                )
                + ". Wer dort vorkommt, wird eher genannt."
            )
        )
    wb = g.get("wettbewerb") or {}
    if wb.get("andere"):
        t.append(
            _p(
                f"<b>Andere Anbieter genannt</b>: "
                + _e(", ".join(f"{n} ({z}×)" for n, z in wb["andere"].items()))
                + f" · Sie: {wb.get('wir', 0)}× in {wb.get('antworten', 0)} Antworten."
            )
        )
    for frage, namen in list((wb.get("luecken") or {}).items())[:3]:
        t.append(
            _p(
                f"Nur andere genannt bei „{_e(frage)}“: {_e(', '.join(namen))}",
                klein=True,
            )
        )
    return t


def _abschnitt_pruefung(b: Dict[str, Any]) -> List[str]:
    wp = b.get("website_pruefung", {})
    if "note" not in wp:
        return [
            _h("Website-Prüfung"),
            _p(_e(wp.get("fehler", "keine Daten")), klein=True),
        ]
    t = [
        _h("Website-Prüfung (SEO-Autopilot, täglich)"),
        _p(
            f"Note {_vergleich(wp['note'], wp['note_vorher']) if wp['note_vorher'] is not None else _e(wp['note'])} von 100 · "
            f"{wp['schwer']} wichtige, {wp['mittel']} mittlere Punkte · {wp['seiten']} Seiten"
        ),
    ]
    if wp["punkte"]:
        t.append(
            "<ul style='padding-left:18px'>"
            + "".join(
                f"<li style='font:400 14px/1.5 {SCHRIFT}'><b>{_e(p['titel'])}</b>"
                f"{' (' + str(p['anzahl']) + '×)' if p['anzahl'] > 1 else ''} – {_e(p['empfehlung'])}"
                f"{' ' + _e(p['verweis']) if p.get('verweis') else ''}</li>"
                for p in wp["punkte"]
            )
            + "</ul>"
        )
    return t


def _abschnitt_bing(b: Dict[str, Any]) -> List[str]:
    bg = b.get("bing", {})
    if "stand" not in bg:
        return [_h("Bing"), _p(_e(bg.get("fehler", "keine Daten")), klein=True)]
    return [
        _h("Bing / Copilot"),
        _p(f"Stand {_e(bg['stand'])} · Crawl-Probleme: {len(bg['probleme'])}"),
    ]


def _abschnitt_werkzeug(b: Dict[str, Any]) -> List[str]:
    befunde = b.get("werkzeug", {}).get("befunde", [])
    t = [_h("Zustand des Werkzeugs")]
    if not befunde:
        return t + [
            _p("Alle Läufe, Zugänge und Messungen arbeiten normal.", klein=True)
        ]
    farben = {"kritisch": ROT, "warnung": ORANGE, "hinweis": GRAU}
    for x in befunde:
        t.append(
            _karte(
                _p(f"<b>{_e(x['titel'])}</b>")
                + _p(_e(x["detail"]), klein=True)
                + (_p("→ " + _e(x["abhilfe"]), klein=True) if x.get("abhilfe") else ""),
                farben.get(x["schwere"], GRAU),
            )
        )
    return t


# ---------------------------------------------------------------------------
# Ablage und Versand
# ---------------------------------------------------------------------------


def ablegen(b: Dict[str, Any], heute: date, ablage: Path = ABLAGE) -> Path:
    ordner = ablage / b["projekt"]
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"bericht-{heute.isoformat()}.json"
    pfad.write_text(
        json.dumps(b, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return pfad


def senden(b: Dict[str, Any], empfaenger: str) -> Tuple[bool, str]:
    n = len(b.get("entscheidungen", []))
    betreff = f"{b['name']} · SEO-Wochenbericht {date.today():%d.%m.}" + (
        f" · {n} Entscheidung{'en' if n != 1 else ''}" if n else ""
    )
    with tempfile.TemporaryDirectory() as tmp:
        text, mail = Path(tmp, "text.txt"), Path(tmp, "mail.html")
        text.write_text(
            f"{betreff}\n\n"
            + "\n".join(f"- {h}" for h in b["hinweise"])
            + "\n\nBitte HTML-Ansicht nutzen.",
            encoding="utf-8",
        )
        mail.write_text(als_html(b), encoding="utf-8")
        lauf = subprocess.run(
            [
                "python3",
                MAILER,
                "--to",
                empfaenger,
                "--subject",
                betreff,
                "--body-file",
                str(text),
                "--html-file",
                str(mail),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    return lauf.returncode == 0, (lauf.stdout.strip() or lauf.stderr.strip())[-300:]


def erstellen(
    schluessel: str,
    db: str,
    projects: str,
    senden_an: Optional[str] = None,
    mit_ki: bool = True,
    knoepfe: bool = True,
) -> Dict[str, Any]:
    heute = date.today()
    b = sammeln(schluessel, db, projects, heute, mit_ki=mit_ki)
    b["entscheidungen"] = (
        entscheidungen_anlegen(schluessel, b, heute) if knoepfe else []
    )
    ablegen(b, heute)
    if senden_an:
        ok, meldung = senden(b, senden_an)
        b["versand"] = {"ok": ok, "meldung": meldung}
        logger.info(f"[Kundenbericht] {schluessel} an {senden_an}: {meldung}")
    return b
