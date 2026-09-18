#!/usr/bin/env python3
"""SEO und KI-Suche (GEO) fuer tentacl.ai in Zahlen - fuer den Marketing-Tagesbericht.

    cd <Installationsordner> && venv/bin/python scripts/tagesstand.py   # JSON auf stdout

Reines Lesen, keine Aenderung:
  - Search Console: Klicks, Einblendungen, Position der letzten 7 Tage gegen die
    7 Tage davor (Google liefert mit ~3 Tagen Verzug), Top-Suchbegriffe, Top-Seiten
  - SEO-Autopilot: letzter Audit (Score, Probleme nach Schwere, GEO-Probleme),
    Verlauf 7 Tage, offene Vorschlaege, fehlgeschlagene Auto-Fixes
  - KI-Crawler: Besuche von GPTBot, ClaudeBot, Perplexity & Co. auf tentacl.ai
    (gestern und 7 Tage) aus dem nginx-Tracking-Log - das ist der GEO-Puls
  - IndexNow: letzter Lauf (Bing/Copilot)
  - Hinweise, wenn etwas nicht stimmt
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

HIER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HIER))

from seo_autopilot.sources.gsc import GSCDataSource  # noqa: E402

PROJEKT = "tentacl-ai"
HOST = "tentacl.ai"
PROPERTY = "sc-domain:tentacl.ai"
from seo_autopilot.core.config import settings as _einst  # noqa: E402

CREDENTIALS = _einst.GSC_CREDENTIALS_PATH
DB = HIER / "seo_autopilot.db"
TRACKING_LOG = Path("/var/log/nginx-tracking/visitor-tracking.log")
INDEXNOW_STATE = Path(
    (_einst.BING_STATE or "").replace("bing-{host}", "indexnow-tentacl.ai")
)
GSC_VERZUG_TAGE = 3

# Wer zaehlt als KI-Crawler (Name in der Ausgabe -> Muster im User-Agent)
KI_BOTS = {
    "OpenAI (GPTBot, ChatGPT, SearchBot)": r"gptbot|chatgpt-user|oai-searchbot",
    "Anthropic (Claude)": r"claudebot|claude-user|anthropic-ai",
    "Perplexity": r"perplexitybot|perplexity-user",
    "Google (Googlebot, Gemini)": r"googlebot|google-extended",
    "Microsoft (Bing, Copilot)": r"bingbot",
    "Meta": r"meta-externalagent",
    "Amazon": r"amazonbot",
    "Apple": r"applebot",
}


def _summe(rows: list[dict] | None) -> dict:
    rows = rows or []
    klicks = sum(int(r.get("clicks", 0) or 0) for r in rows)
    impr = sum(int(r.get("impressions", 0) or 0) for r in rows)
    pos = [
        float(r.get("position", 0) or 0) * int(r.get("impressions", 0) or 0)
        for r in rows
    ]
    return {
        "klicks": klicks,
        "impressionen": impr,
        "ctr_prozent": round(100 * klicks / impr, 2) if impr else 0.0,
        "position": round(sum(pos) / impr, 1) if impr else None,
    }


async def search_console() -> dict:
    g = GSCDataSource(CREDENTIALS)
    ende = date.today() - timedelta(days=GSC_VERZUG_TAGE)
    s7 = ende - timedelta(days=6)
    vor_ende, vor_start = s7 - timedelta(days=1), s7 - timedelta(days=7)
    aus = {
        "zeitraum": f"{s7.isoformat()} bis {ende.isoformat()}",
        "hinweis": f"Google liefert mit {GSC_VERZUG_TAGE} Tagen Verzug",
    }
    aus["tage7"] = _summe(await g.pull_range(PROPERTY, s7, ende, dimensions=["date"]))
    aus["vor7"] = _summe(
        await g.pull_range(PROPERTY, vor_start, vor_ende, dimensions=["date"])
    )
    q = (
        await g.pull_range(PROPERTY, s7, ende, dimensions=["query"], row_limit=200)
        or []
    )
    q.sort(
        key=lambda r: (
            -int(r.get("clicks", 0) or 0),
            -int(r.get("impressions", 0) or 0),
        )
    )
    aus["top_suchbegriffe"] = [
        {
            "begriff": r["keys"][0],
            "klicks": int(r.get("clicks", 0) or 0),
            "impressionen": int(r.get("impressions", 0) or 0),
            "position": round(float(r.get("position", 0) or 0), 1),
        }
        for r in q[:8]
    ]
    aus["suchbegriffe_gesamt"] = len(q)
    camping = [
        r
        for r in q
        if re.search(
            r"camping|stellplatz|wohnmobil|buchungssystem|kurtaxe|meldeschein",
            r["keys"][0],
            re.I,
        )
    ]
    aus["camping_begriffe"] = [
        {
            "begriff": r["keys"][0],
            "impressionen": int(r.get("impressions", 0) or 0),
            "position": round(float(r.get("position", 0) or 0), 1),
        }
        for r in camping[:8]
    ]
    p = await g.pull_range(PROPERTY, s7, ende, dimensions=["page"], row_limit=100) or []
    p.sort(
        key=lambda r: (
            -int(r.get("clicks", 0) or 0),
            -int(r.get("impressions", 0) or 0),
        )
    )
    aus["top_seiten"] = [
        {
            "seite": r["keys"][0].replace("https://tentacl.ai", "") or "/",
            "klicks": int(r.get("clicks", 0) or 0),
            "impressionen": int(r.get("impressions", 0) or 0),
        }
        for r in p[:6]
    ]
    return aus


def autopilot() -> dict:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    audits = [
        dict(r)
        for r in c.execute(
            "select id, score, issues_found, total_pages, started_at from seo_audits "
            "where project_id=? and status='completed' order by started_at desc limit 8",
            (PROJEKT,),
        )
    ]
    aus: dict = {"dashboard": "https://tentacl.ai/seo-autopilot/dashboard"}
    if not audits:
        aus["hinweis"] = "noch kein abgeschlossener Audit"
        return aus
    letzter = audits[0]
    aus["audit"] = {
        "am": letzter["started_at"][:16],
        "score": letzter["score"],
        "probleme": letzter["issues_found"],
        "seiten": letzter["total_pages"],
    }
    vor7 = next(
        (
            a
            for a in audits
            if a["started_at"][:10] <= (date.today() - timedelta(days=7)).isoformat()
        ),
        audits[-1],
    )
    aus["score_vor_7_tagen"] = vor7["score"]
    aus["score_verlauf"] = [
        {"tag": a["started_at"][:10], "score": a["score"]} for a in reversed(audits)
    ]
    schwere = Counter()
    kategorien = Counter()
    for r in c.execute(
        "select severity, category from seo_issues where audit_id=?", (letzter["id"],)
    ):
        schwere[r["severity"]] += 1
        kategorien[r["category"]] += 1
    aus["nach_schwere"] = dict(schwere)
    aus["nach_kategorie"] = dict(kategorien.most_common(8))
    aus["geo_probleme"] = sum(
        v for k, v in kategorien.items() if k in ("geo", "llms_ai")
    )
    aus["top_probleme"] = [
        {
            "schwere": r["severity"],
            "kategorie": r["category"],
            "titel": r["title"],
            "anzahl": r["count"],
        }
        for r in c.execute(
            "select severity, category, title, count from seo_issues where audit_id=? and severity='high' "
            "order by priority desc limit 6",
            (letzter["id"],),
        )
    ]
    aus["vorschlaege_offen"] = c.execute(
        "select count(*) from freigaben where project_id=? and status='offen'",
        (PROJEKT,),
    ).fetchone()[0]
    aus["vorschlaege_beispiele"] = [
        {
            "typ": r["issue_type"],
            "seite": (r["ziel_url"] or "").replace("https://tentacl.ai", ""),
        }
        for r in c.execute(
            "select issue_type, ziel_url from freigaben where project_id=? and status='offen' "
            "order by erstellt_am desc limit 5",
            (PROJEKT,),
        )
    ]
    ab = (datetime.utcnow() - timedelta(days=7)).isoformat()
    fixes = Counter()
    for r in c.execute(
        "select status from change_log where project_id=? and zeitpunkt>=?",
        (PROJEKT, ab),
    ):
        fixes[r["status"] or "unbekannt"] += 1
    aus["auto_fixes_7_tage"] = dict(fixes)
    return aus


def ki_crawler() -> dict:
    """Besuche je KI-Crawler auf tentacl.ai, gestern und letzte 7 Tage."""
    aus = {"gestern": {}, "tage7": {}, "quelle": str(TRACKING_LOG)}
    if not TRACKING_LOG.exists():
        aus["hinweis"] = "Tracking-Log fehlt"
        return aus
    gestern = (date.today() - timedelta(days=1)).strftime("%d/%b/%Y")
    tage = {
        (date.today() - timedelta(days=i)).strftime("%d/%b/%Y") for i in range(1, 8)
    }
    g7: Counter = Counter()
    g1: Counter = Counter()
    muster = {name: re.compile(m, re.I) for name, m in KI_BOTS.items()}
    with TRACKING_LOG.open("r", encoding="utf-8", errors="replace") as fh:
        for zeile in fh:
            teile = zeile.split("|")
            if len(teile) < 7 or teile[2] != HOST:
                continue
            tag = teile[1][:11]
            if tag not in tage:
                continue
            ua = teile[6]
            for name, rx in muster.items():
                if rx.search(ua):
                    g7[name] += 1
                    if tag == gestern:
                        g1[name] += 1
                    break
    aus["gestern"] = dict(g1.most_common())
    aus["tage7"] = dict(g7.most_common())
    aus["ki_gesamt_7_tage"] = sum(
        v
        for k, v in g7.items()
        if k.startswith(("OpenAI", "Anthropic", "Perplexity", "Meta", "Amazon"))
    )
    return aus


GA4_PROPERTY = "528143447"


def _ga4_kanal(quelle: str, medium: str) -> str:
    """Dieselben Regeln wie die Kanalgruppe 'tentacl Herkunft' in GA4 (ga4_einrichten.py)."""
    q, m = (quelle or "").lower(), (medium or "").lower()
    paid = m in ("cpc", "paid", "paid_social", "ppc", "paidsocial")
    if q == "chatgpt" and paid:
        return "ChatGPT-Anzeigen"
    if q in ("meta", "facebook", "instagram", "fb", "ig") and paid:
        return "Meta-Anzeigen"
    if q == "google" and paid:
        return "Google Ads"
    if "linkedin" in q or "lnkd.in" in q:
        return "LinkedIn"
    if "instagram" in q:
        return "Instagram organisch"
    if "facebook" in q or "fb.com" in q or "fb.me" in q:
        return "Facebook organisch"
    if any(
        k in q
        for k in (
            "chatgpt",
            "openai",
            "perplexity",
            "copilot",
            "gemini",
            "claude.ai",
            "you.com",
            "meta.ai",
            "mistral",
            "aichat",
        )
    ):
        return "KI-Suche organisch"
    if q == "google" and m == "organic":
        return "Google organisch"
    if m == "organic":
        return "Andere Suche organisch"
    if m in ("email", "e-mail", "mail", "newsletter"):
        return "E-Mail"
    if q == "(direct)":
        return "Direkt"
    if m == "referral":
        return "Verweise"
    return "Sonstiges"


def ga4_herkunft() -> dict:
    """Besuche der letzten 7 Tage nach Herkunft + Kontaktabsichten, aus der GA4 Data API."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )
    from google.oauth2 import service_account

    cred = service_account.Credentials.from_service_account_file(
        CREDENTIALS, scopes=["https://www.googleapis.com/auth/analytics.readonly"]
    )
    cl = BetaAnalyticsDataClient(credentials=cred)
    prop = f"properties/{GA4_PROPERTY}"
    antwort = cl.run_report(
        RunReportRequest(
            property=prop,
            date_ranges=[DateRange(start_date="7daysAgo", end_date="yesterday")],
            dimensions=[
                Dimension(name="sessionSource"),
                Dimension(name="sessionMedium"),
            ],
            metrics=[
                Metric(name="sessions"),
                Metric(name="totalUsers"),
                Metric(name="keyEvents"),
            ],
            limit=200,
        )
    )
    je_kanal: dict[str, dict] = {}
    ki_quellen: dict[str, int] = {}
    for r in antwort.rows:
        q, m = r.dimension_values[0].value, r.dimension_values[1].value
        s = int(r.metric_values[0].value or 0)
        u = int(r.metric_values[1].value or 0)
        k = float(r.metric_values[2].value or 0)
        kanal = _ga4_kanal(q, m)
        e = je_kanal.setdefault(
            kanal, {"besuche": 0, "nutzer": 0, "kontaktabsichten": 0.0}
        )
        e["besuche"] += s
        e["nutzer"] += u
        e["kontaktabsichten"] += k
        if kanal == "KI-Suche organisch":
            ki_quellen[q] = ki_quellen.get(q, 0) + s
    vor = cl.run_report(
        RunReportRequest(
            property=prop,
            date_ranges=[DateRange(start_date="14daysAgo", end_date="8daysAgo")],
            metrics=[Metric(name="sessions"), Metric(name="keyEvents")],
        )
    )
    vor_s = int(vor.rows[0].metric_values[0].value or 0) if vor.rows else 0
    vor_k = float(vor.rows[0].metric_values[1].value or 0) if vor.rows else 0.0
    ereignisse = cl.run_report(
        RunReportRequest(
            property=prop,
            date_ranges=[DateRange(start_date="7daysAgo", end_date="yesterday")],
            dimensions=[Dimension(name="eventName")],
            metrics=[Metric(name="eventCount")],
            limit=60,
        )
    )
    wichtig = ("formular_gesendet", "anruf_klick", "mail_klick", "terminwahl_geoeffnet")
    ereignis_zahlen = {
        r.dimension_values[0].value: int(r.metric_values[0].value or 0)
        for r in ereignisse.rows
        if r.dimension_values[0].value in wichtig
    }
    besuche = sum(e["besuche"] for e in je_kanal.values())
    return {
        "verbunden": True,
        "zeitraum": "letzte 7 Tage bis gestern",
        "besuche": besuche,
        "besuche_vorwoche": vor_s,
        "kontaktabsichten": round(
            sum(e["kontaktabsichten"] for e in je_kanal.values()), 1
        ),
        "kontaktabsichten_vorwoche": round(vor_k, 1),
        "je_herkunft": dict(sorted(je_kanal.items(), key=lambda x: -x[1]["besuche"])),
        "ki_quellen": ki_quellen,
        "ereignisse": ereignis_zahlen,
    }


def indexnow() -> dict:
    if not INDEXNOW_STATE.exists():
        return {"hinweis": "kein Stand"}
    try:
        d = json.loads(INDEXNOW_STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"hinweis": "Stand unlesbar"}
    # Der Stand ist eine Karte Adresse -> Fingerabdruck; der Lauf selbst steht im Dateidatum.
    mtime = datetime.fromtimestamp(INDEXNOW_STATE.stat().st_mtime)
    return {
        "seiten_bekannt": len([k for k in d if str(k).startswith("http")]),
        "letzter_lauf": mtime.isoformat(timespec="minutes"),
        "tage_seit_lauf": (datetime.now() - mtime).days,
    }


def main() -> int:
    aus: dict = {
        "kanal": "seo",
        "host": HOST,
        "stand": date.today().isoformat(),
        "hinweise": [],
    }
    try:
        aus["search_console"] = asyncio.run(search_console())
    except Exception as e:  # noqa: BLE001
        aus["search_console"] = {"fehler": f"{type(e).__name__}: {str(e)[:200]}"}
        aus["hinweise"].append(f"Search Console nicht lesbar: {str(e)[:120]}")
    try:
        aus["autopilot"] = autopilot()
    except Exception as e:  # noqa: BLE001
        aus["autopilot"] = {"fehler": f"{type(e).__name__}: {str(e)[:200]}"}
        aus["hinweise"].append(f"SEO-Autopilot-Daten nicht lesbar: {str(e)[:120]}")
    try:
        aus["ki_crawler"] = ki_crawler()
    except Exception as e:  # noqa: BLE001
        aus["ki_crawler"] = {"fehler": f"{type(e).__name__}: {str(e)[:200]}"}
    try:
        aus["indexnow"] = indexnow()
    except Exception as e:  # noqa: BLE001
        aus["indexnow"] = {"fehler": str(e)[:120]}
    try:
        aus["ga4"] = ga4_herkunft()
    except Exception as e:  # noqa: BLE001
        aus["ga4"] = {
            "verbunden": False,
            "hinweis": f"GA4 nicht lesbar: {str(e)[:160]}",
        }

    sc = aus.get("search_console", {})
    camping_impr = sum(b.get("impressionen", 0) for b in sc.get("camping_begriffe", []))
    if sc.get("tage7") and camping_impr < 20:
        aus["hinweise"].append(
            f"Camping-Suchbegriffe bringen bei Google nur {camping_impr} Einblendungen "
            f"in 7 Tagen - die Campingplatz-Seiten sind fuer Google praktisch unsichtbar."
        )
    ix = aus.get("indexnow", {})
    if isinstance(ix.get("tage_seit_lauf"), int) and ix["tage_seit_lauf"] > 2:
        aus["hinweise"].append(
            f"IndexNow (Bing/Copilot) lief zuletzt vor {ix['tage_seit_lauf']} Tagen."
        )
    ap = aus.get("autopilot", {})
    if ap.get("auto_fixes_7_tage", {}).get("fehlgeschlagen"):
        aus["hinweise"].append(
            f"{ap['auto_fixes_7_tage']['fehlgeschlagen']} Auto-Fixes des SEO-Autopiloten "
            f"schlugen in 7 Tagen fehl (gleicher Fehler taeglich)."
        )
    if ap.get("audit") and ap.get("score_vor_7_tagen") is not None:
        if ap["audit"]["score"] < ap["score_vor_7_tagen"]:
            aus["hinweise"].append(
                f"SEO-Score gefallen: {ap['score_vor_7_tagen']} -> {ap['audit']['score']} "
                f"in 7 Tagen; {ap.get('vorschlaege_offen', 0)} Vorschlaege warten."
            )
        elif ap["audit"]["score"] == ap["score_vor_7_tagen"] and ap.get(
            "vorschlaege_offen"
        ):
            aus["hinweise"].append(
                f"SEO-Score steht seit 7 Tagen bei {ap['audit']['score']} - "
                f"{ap['vorschlaege_offen']} Vorschlaege warten unbearbeitet."
            )
    aus["ok"] = "fehler" not in sc
    print(json.dumps(aus, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
