"""
Search-Console-Ereignisse: erkennen, selbst nachpruefen, reagieren, melden.

Google hat fuer seine Warnmails (neue Probleme, manuelle Massnahmen, Sicherheit,
Indexierung) keine Schnittstelle. Darum zwei Quellen:

1. Mails von ``sc-noreply@google.com`` im Postfach (IMAP, nur lesen). Jede Mail
   wird einem Projekt aus projects.yaml zugeordnet und eingestuft (info/warnung/
   kritisch). Meldet Google fehlende Felder in strukturierten Daten, prueft der
   Autopilot die Live-Seiten selbst und sagt, ob das noch stimmt.
2. Search-Console-API je Projekt mit ``gsc`` in ``enabled_sources``:
   - Sitemaps: Fehler, Warnungen, lange nicht abgeholt.
   - URL-Pruefung: Startseite plus taeglich wechselnde Adressen aus der Sitemap.
     Faellt eine Seite aus dem Index, ist das ein Ereignis.

Reaktionen, die nichts an der Website aendern, fuehrt der Autopilot selbst aus
(Betriebsart ``autopilot``): Sitemap neu einreichen, IndexNow fuer Bing.
In ``copilot``/``beobachter`` steht in der Meldung nur, was er tun wuerde.
Gemeldet wird per Telegram, jedes Ereignis genau einmal.

Aufruf: ``python -m seo_autopilot.gsc_ereignisse`` (alle Projekte, Cron taeglich 07:50)
        ``python -m seo_autopilot.gsc_ereignisse --nur-mails`` (Cron alle zwei Stunden)
        ``python -m seo_autopilot.gsc_ereignisse --projekt kunde --trocken``
"""

from __future__ import annotations

import argparse
import email
import hashlib
import html
import imaplib
import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import httpx

from .ausfuehrung import BETRIEBSART_AUTOPILOT, betriebsart_von

logger = logging.getLogger(__name__)

ABSENDER = "sc-noreply@google.com"
MAIL_KONTO = os.environ.get(
    "GSC_MAIL_KONTO", ""
)  # Postfach, in dem Google-Mails ankommen
MAIL_PASSWORT_DATEI = os.environ.get("GSC_MAIL_PASSWORT_DATEI", "")
MAIL_TAGE = 14
URL_PRUEFUNGEN_JE_TAG = 20
SITEMAP_ALT_TAGE = 14
NEU_EINREICHEN_SPERRE_TAGE = 7
SCHEMA_SEITEN_MAX = 250

INFO, WARNUNG, KRITISCH = "info", "warnung", "kritisch"

# Reihenfolge zaehlt: die erste passende Regel gewinnt.
MAIL_REGELN: List[Tuple[str, str, str]] = [
    (r"manuelle ma(ss|ß)nahme|manual action", "manuelle_massnahme", KRITISCH),
    (r"sicherheitsproblem|gehackt|malware|security issue", "sicherheit", KRITISCH),
    (r"nicht indexiert|indexierung|indexing|seitenindexierung", "indexierung", WARNUNG),
    (
        r"validierung.*(fehlgeschlagen|nicht bestanden)|validation failed",
        "validierung_fehlgeschlagen",
        WARNUNG,
    ),
    (
        r"(erfolgreich|bestanden).*validier|behoben|validation passed",
        "validierung_bestanden",
        INFO,
    ),
    (
        r"core web vitals|nutzerfreundlichkeit|page experience",
        "nutzererfahrung",
        WARNUNG,
    ),
    (r"probleme vom typ|neue probleme|issues detected|problem", "probleme", WARNUNG),
    (r"traffic|impressionen|leistung", "erste_daten", INFO),
    (r"bestätigt|verified|präsenz|presence|willkommen", "willkommen", INFO),
]

# Search-Console-Berichtsnamen -> schema.org-Typ
SCHEMA_TYPEN = {
    "ereignisse": "Event",
    "events": "Event",
    "veranstaltung": "Event",
    "produkt": "Product",
    "product": "Product",
    "faq": "FAQPage",
    "breadcrumb": "BreadcrumbList",
    "navigationspfad": "BreadcrumbList",
    "bewertungs": "Review",
    "review": "Review",
    "logo": "Organization",
    "unternehmen": "LocalBusiness",
    "local business": "LocalBusiness",
    "artikel": "Article",
    "article": "Article",
    "video": "VideoObject",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gsc_ereignisse (
    schluessel TEXT PRIMARY KEY,
    project_id TEXT, quelle TEXT, art TEXT, schwere TEXT,
    titel TEXT, details TEXT, reaktion TEXT,
    erstmals TEXT, gemeldet INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gsc_url_status (
    project_id TEXT, url TEXT, verdict TEXT, abdeckung TEXT, geprueft TEXT,
    PRIMARY KEY (project_id, url)
);
CREATE TABLE IF NOT EXISTS gsc_sitemap_einreichung (
    project_id TEXT, sitemap TEXT, am TEXT, PRIMARY KEY (project_id, sitemap)
);
CREATE TABLE IF NOT EXISTS gsc_laeufe (am TEXT, art TEXT);
"""


# --------------------------------------------------------------------------- DB


def db_oeffnen(pfad: str) -> sqlite3.Connection:
    con = sqlite3.connect(pfad, timeout=30)  # Datei teilen sich mehrere Cron-Jobs
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def ereignis_neu(con: sqlite3.Connection, e: Dict[str, Any]) -> bool:
    """Legt ein Ereignis an. False, wenn es schon bekannt war (nie doppelt melden)."""
    try:
        con.execute(
            "INSERT INTO gsc_ereignisse (schluessel, project_id, quelle, art, schwere,"
            " titel, details, reaktion, erstmals) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                e["schluessel"],
                e["project_id"],
                e["quelle"],
                e["art"],
                e["schwere"],
                e["titel"],
                e.get("details", ""),
                e.get("reaktion", ""),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def schluessel(*teile: str) -> str:
    return hashlib.sha1("|".join(teile).encode("utf-8")).hexdigest()[:20]


# ------------------------------------------------------------------------ Mails


def _kopf(wert: Optional[str]) -> str:
    teile = []
    for t, cs in decode_header(wert or ""):
        teile.append(t.decode(cs or "utf-8", "replace") if isinstance(t, bytes) else t)
    return "".join(teile)


def mail_text(msg: email.message.Message) -> str:
    """Klartext einer Mail; HTML wird zur Not grob entkleidet."""
    for typ in ("text/plain", "text/html"):
        for teil in msg.walk():
            if teil.get_content_type() != typ:
                continue
            roh = teil.get_payload(decode=True) or b""
            text = roh.decode(teil.get_content_charset() or "utf-8", "replace")
            if typ == "text/html":
                text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
                text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            return re.sub(r"[ \t]+", " ", text)
    return ""


def einstufen(betreff: str, text: str) -> Tuple[str, str]:
    """(art, schwere) aus Betreff und Text; der Betreff entscheidet zuerst."""
    for quelle in (betreff, text[:600]):
        klein = quelle.lower()
        for muster, art, schwere in MAIL_REGELN:
            if re.search(muster, klein):
                return art, schwere
    return "sonstiges", INFO


def domain_aus(betreff: str, text: str) -> Optional[str]:
    treffer = re.search(
        r"\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b", f"{betreff} {text[:400]}".lower()
    )
    if not treffer:
        return None
    d = treffer.group(1)
    return None if d.endswith("google.com") else d


def projekt_zu_domain(
    projekte: Dict[str, Dict[str, Any]], domain: str
) -> Optional[str]:
    domain = domain.lower().removeprefix("www.")
    for pid, p in projekte.items():
        host = (urlparse(p.get("domain") or "").hostname or "").removeprefix("www.")
        if host and host == domain:
            return pid
    return None


def fehlende_felder(text: str) -> List[str]:
    """'Feld "image" fehlt' / 'Missing field "image"' -> ['image']."""
    treffer = re.findall(
        r'[Ff]eld\s+[„"“]?(\w+)[“"”]?\s+fehlt|[Mm]issing field\s+[„"“]?(\w+)', text
    )
    return sorted({a or b for a, b in treffer})


def schema_typ_aus(text: str) -> Optional[str]:
    m = re.search(r"[Tt]yp\s+[„\"“]([^“\"”]+)[“\"”]", text)
    bericht = (m.group(1) if m else text[:300]).lower()
    for wort, typ in SCHEMA_TYPEN.items():
        if wort in bericht:
            return typ
    return None


def mails_holen(tage: int = MAIL_TAGE) -> List[Dict[str, Any]]:
    """Mails von Google der letzten Tage (nur lesen, BODY.PEEK laesst sie ungelesen)."""
    pfad = Path(MAIL_PASSWORT_DATEI) if MAIL_PASSWORT_DATEI else None
    if not MAIL_KONTO or not pfad or not pfad.exists():
        logger.warning("[GSC-Ereignisse] Mailzugang fehlt, Mails werden uebersprungen")
        return []
    seit = (date.today() - timedelta(days=tage)).strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(MAIL_KONTO, pfad.read_text().strip())
        imap.select("INBOX", readonly=True)
        _, ids = imap.search(None, "FROM", ABSENDER, "SINCE", seit)
        mails = []
        for nr in ids[0].split():
            _, daten = imap.fetch(nr, "(BODY.PEEK[])")
            msg = email.message_from_bytes(daten[0][1])
            mails.append(
                {
                    "id": msg.get("Message-ID") or nr.decode(),
                    "betreff": _kopf(msg.get("Subject")),
                    "datum": msg.get("Date") or "",
                    "text": mail_text(msg),
                }
            )
        return mails
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def ereignis_aus_mail(
    mail: Dict[str, Any], projekte: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    domain = domain_aus(mail["betreff"], mail["text"])
    pid = projekt_zu_domain(projekte, domain) if domain else None
    if not pid:
        return None
    art, schwere = einstufen(mail["betreff"], mail["text"])
    e = {
        "schluessel": schluessel("mail", mail["id"]),
        "project_id": pid,
        "quelle": "mail",
        "art": art,
        "schwere": schwere,
        "titel": mail["betreff"].strip(),
        "details": "",
    }
    felder = fehlende_felder(mail["text"])
    if felder:
        e["felder"] = felder
        e["schema_typ"] = schema_typ_aus(mail["text"]) or schema_typ_aus(
            mail["betreff"]
        )
    return e


# --------------------------------------------------------- Live-Gegenprobe Schema


def sitemap_urls(
    domain: str, holen: Callable[[str], str], grenze: int = 400
) -> List[str]:
    """Alle Seiten aus sitemap.xml (auch Sitemap-Index, eine Ebene tief)."""
    basis = domain.rstrip("/")
    try:
        xml = holen(f"{basis}/sitemap.xml")
    except Exception as exc:
        logger.warning(f"[GSC-Ereignisse] Sitemap nicht lesbar: {exc}")
        return []
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    if "<sitemapindex" in xml:
        unter: List[str] = []
        for teil in locs[:10]:
            try:
                unter += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", holen(teil))
            except Exception:
                continue
        locs = unter
    return locs[:grenze]


def jsonld_objekte(seite: str) -> List[Dict[str, Any]]:
    gefunden: List[Dict[str, Any]] = []

    def laufen(x: Any) -> None:
        if isinstance(x, dict):
            gefunden.append(x)
            for v in x.values():
                laufen(v)
        elif isinstance(x, list):
            for v in x:
                laufen(v)

    for block in re.findall(
        r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", seite, re.S | re.I
    ):
        try:
            laufen(json.loads(block))
        except ValueError:
            continue
    return gefunden


def _ist_typ(obj: Dict[str, Any], typ: str) -> bool:
    t = obj.get("@type")
    return typ in t if isinstance(t, list) else t == typ


def schema_gegenprobe(
    urls: Iterable[str], typ: str, felder: Sequence[str], holen: Callable[[str], str]
) -> Dict[str, Any]:
    """Zaehlt live, wie viele Objekte des Typs die gemeldeten Felder noch vermissen."""
    objekte, fehlt, seiten = 0, {f: 0 for f in felder}, 0
    for url in list(urls)[:SCHEMA_SEITEN_MAX]:
        try:
            seite = holen(url)
        except Exception:
            continue
        treffer = [o for o in jsonld_objekte(seite) if _ist_typ(o, typ)]
        if treffer:
            seiten += 1
        for o in treffer:
            objekte += 1
            for f in felder:
                if o.get(f) in (None, "", [], {}):
                    fehlt[f] += 1
    return {
        "typ": typ,
        "seiten": seiten,
        "objekte": objekte,
        "fehlt": {f: n for f, n in fehlt.items() if n},
    }


def gegenprobe_text(g: Dict[str, Any]) -> str:
    if not g["objekte"]:
        return (
            f"Gegenprobe: live keine {g['typ']}-Daten gefunden "
            f"(Seite geaendert oder Typ entfernt). Bitte einmal ansehen."
        )
    if not g["fehlt"]:
        return (
            f"Gegenprobe live: alle {g['objekte']} {g['typ']}-Eintraege auf {g['seiten']} "
            f"Seiten haben die Felder. Schon behoben, Google prueft beim naechsten Besuch neu."
        )
    liste = ", ".join(f"{f} ({n}x)" for f, n in g["fehlt"].items())
    return f"Gegenprobe live: noch offen bei {g['typ']}: {liste}."


# ------------------------------------------------------------------ Search Console


def gsc_dienst(credentials_path: str):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    cr = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=["https://www.googleapis.com/auth/webmasters"]
    )
    return build("searchconsole", "v1", credentials=cr, cache_discovery=False)


def sitemap_ereignisse(
    pid: str, prop: str, sitemaps: List[Dict[str, Any]], heute: date
) -> List[Dict[str, Any]]:
    """Ereignisse aus dem Sitemap-Bericht; jedes traegt die Sitemap fuer die Reaktion."""
    aus: List[Dict[str, Any]] = []
    if not sitemaps:
        aus.append(
            {
                "art": "sitemap_fehlt",
                "schwere": WARNUNG,
                "sitemap": None,
                "titel": "Keine Sitemap in der Search Console eingereicht",
            }
        )
    for s in sitemaps:
        pfad, fehler = s.get("path", ""), int(s.get("errors") or 0)
        abgeholt = (s.get("lastDownloaded") or "")[:10]
        if fehler:
            aus.append(
                {
                    "art": "sitemap_fehler",
                    "schwere": WARNUNG,
                    "sitemap": pfad,
                    "titel": f"Sitemap mit {fehler} Fehler(n): {pfad}",
                    "stand": abgeholt,
                }
            )
        if abgeholt and (heute - date.fromisoformat(abgeholt)).days > SITEMAP_ALT_TAGE:
            aus.append(
                {
                    "art": "sitemap_alt",
                    "schwere": WARNUNG,
                    "sitemap": pfad,
                    "titel": f"Google hat die Sitemap seit {abgeholt} nicht mehr abgeholt",
                    "stand": abgeholt,
                }
            )
    for e in aus:
        e.update(
            project_id=pid,
            quelle="api",
            schluessel=schluessel(
                "sitemap",
                pid,
                prop,
                e["art"],
                e.get("sitemap") or "",
                e.get("stand", ""),
            ),
        )
    return aus


def heutige_auswahl(
    urls: List[str], startseite: str, heute: date, anzahl: int = URL_PRUEFUNGEN_JE_TAG
) -> List[str]:
    """Startseite immer, dazu ein Tagesfenster, das ueber die Sitemap wandert."""
    rest = [u for u in urls if u.rstrip("/") != startseite.rstrip("/")]
    if not rest:
        return [startseite]
    n = max(0, anzahl - 1)
    beginn = (heute.toordinal() * n) % len(rest)
    fenster = (rest + rest)[beginn : beginn + min(n, len(rest))]
    return [startseite] + fenster


def index_ereignis(
    pid: str, url: str, alt: Optional[sqlite3.Row], verdict: str, abdeckung: str
) -> Optional[Dict[str, Any]]:
    """Nur ein Wechsel von 'im Index' zu 'nicht im Index' ist ein Ereignis."""
    if not alt or alt["verdict"] != "PASS" or verdict == "PASS":
        return None
    return {
        "project_id": pid,
        "quelle": "api",
        "art": "aus_index",
        "schwere": WARNUNG,
        "url": url,
        "titel": f"Seite nicht mehr im Google-Index: {url}",
        "details": f"Search Console: {abdeckung or verdict}",
        "schluessel": schluessel("index", pid, url, verdict, abdeckung),
    }


def url_pruefen(dienst, prop: str, url: str) -> Tuple[str, str]:
    antwort = (
        dienst.urlInspection()
        .index()
        .inspect(body={"inspectionUrl": url, "siteUrl": prop, "languageCode": "de-DE"})
        .execute()
    )
    st = (antwort.get("inspectionResult") or {}).get("indexStatusResult") or {}
    return st.get("verdict", "VERDICT_UNSPECIFIED"), st.get("coverageState", "")


# --------------------------------------------------------------------- Reaktionen


def darf_neu_einreichen(
    con: sqlite3.Connection, pid: str, sitemap: str, heute: date
) -> bool:
    row = con.execute(
        "SELECT am FROM gsc_sitemap_einreichung WHERE project_id=? AND sitemap=?",
        (pid, sitemap),
    ).fetchone()
    return (
        not row
        or (heute - date.fromisoformat(row["am"])).days >= NEU_EINREICHEN_SPERRE_TAGE
    )


def sitemap_neu_einreichen(
    dienst, con, pid: str, prop: str, sitemap: str, heute: date, ausfuehren: bool
) -> str:
    if not darf_neu_einreichen(con, pid, sitemap, heute):
        return "Sitemap wurde in den letzten 7 Tagen schon neu eingereicht, warte auf Google."
    if not ausfuehren:
        return "Wuerde die Sitemap neu einreichen (Betriebsart ist nicht Autopilot)."
    dienst.sitemaps().submit(siteUrl=prop, feedpath=sitemap).execute()
    con.execute(
        "INSERT OR REPLACE INTO gsc_sitemap_einreichung VALUES (?,?,?)",
        (pid, sitemap, heute.isoformat()),
    )
    con.commit()
    return "Sitemap neu bei Google eingereicht."


def indexnow_melden(projekt: Dict[str, Any], urls: List[str], ausfuehren: bool) -> str:
    key = ((projekt.get("source_config") or {}).get("indexnow") or {}).get("key")
    if not key or not urls:
        return ""
    if not ausfuehren:
        return "Wuerde die Seite per IndexNow (Bing) neu melden."
    host = urlparse(projekt["domain"]).hostname
    r = httpx.post(
        "https://api.indexnow.org/indexnow",
        timeout=20,
        json={
            "host": host,
            "key": key,
            "urlList": urls[:100],
            "keyLocation": f"https://{host}/{key}.txt",
        },
    )
    return f"Per IndexNow an Bing gemeldet (Antwort {r.status_code})."


# ------------------------------------------------------------------------- Laeufe


def _holer() -> Callable[[str], str]:
    client = httpx.Client(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "tentacl-seo-autopilot/gsc-ereignisse"},
    )
    return lambda url: client.get(url).raise_for_status().text


def mail_lauf(
    con,
    projekte: Dict[str, Dict[str, Any]],
    holen: Callable[[str], str],
    mails: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    neu = []
    for mail in mails:
        e = ereignis_aus_mail(mail, projekte)
        if not e:
            continue
        if con.execute(
            "SELECT 1 FROM gsc_ereignisse WHERE schluessel=?", (e["schluessel"],)
        ).fetchone():
            continue
        if e.get("felder") and e.get("schema_typ"):
            urls = sitemap_urls(projekte[e["project_id"]]["domain"], holen)
            e["reaktion"] = gegenprobe_text(
                schema_gegenprobe(urls, e["schema_typ"], e["felder"], holen)
            )
        if ereignis_neu(con, e):
            neu.append(e)
    return neu


def api_lauf(
    con,
    pid: str,
    projekt: Dict[str, Any],
    holen: Callable[[str], str],
    heute: date,
    dienst=None,
    trocken: bool = False,
) -> List[Dict[str, Any]]:
    gsc = (projekt.get("source_config") or {}).get("gsc") or {}
    prop, pfad = gsc.get("property_url"), gsc.get("credentials_path")
    if not prop or not pfad:
        return []
    dienst = dienst or gsc_dienst(pfad)
    ausfuehren = betriebsart_von(projekt) == BETRIEBSART_AUTOPILOT and not trocken
    neu = []
    sitemaps = dienst.sitemaps().list(siteUrl=prop).execute().get("sitemap", [])
    for e in sitemap_ereignisse(pid, prop, sitemaps, heute):
        if e.get("sitemap"):
            e["reaktion"] = sitemap_neu_einreichen(
                dienst, con, pid, prop, e["sitemap"], heute, ausfuehren
            )
        if ereignis_neu(con, e):
            neu.append(e)
    neu += index_lauf(con, dienst, pid, prop, projekt, holen, heute, ausfuehren)
    return neu


def index_lauf(
    con,
    dienst,
    pid: str,
    prop: str,
    projekt: Dict[str, Any],
    holen: Callable[[str], str],
    heute: date,
    ausfuehren: bool,
) -> List[Dict[str, Any]]:
    neu = []
    start = projekt["domain"].rstrip("/") + "/"
    for url in heutige_auswahl(sitemap_urls(projekt["domain"], holen), start, heute):
        try:
            verdict, abdeckung = url_pruefen(dienst, prop, url)
        except Exception as exc:
            logger.warning(f"[GSC-Ereignisse] URL-Pruefung {url}: {exc}")
            continue
        alt = con.execute(
            "SELECT verdict FROM gsc_url_status WHERE project_id=? AND url=?",
            (pid, url),
        ).fetchone()
        e = index_ereignis(pid, url, alt, verdict, abdeckung)
        con.execute(
            "INSERT OR REPLACE INTO gsc_url_status VALUES (?,?,?,?,?)",
            (pid, url, verdict, abdeckung, heute.isoformat()),
        )
        if e:
            e["reaktion"] = indexnow_melden(projekt, [url], ausfuehren)
            if ereignis_neu(con, e):
                neu.append(e)
    con.commit()
    return neu


def zugang_ereignis(pid: str, projekt: Dict[str, Any], fehler: str) -> Dict[str, Any]:
    """Fehlt der Zugriff, einmal je Fehlerart melden statt still nichts zu pruefen."""
    gsc = (projekt.get("source_config") or {}).get("gsc") or {}
    art = (
        "zugang_fehlt"
        if re.search(r"403|permission|forbidden", fehler, re.I)
        else "api_fehler"
    )
    titel = (
        "Kein Zugriff auf die Search Console. Bitte das Dienstkonto "
        "tentacl-seo-bot@tentacl-seo.iam.gserviceaccount.com dort als Nutzer hinzufügen."
        if art == "zugang_fehlt"
        else "Search Console antwortet nicht, Prüfung übersprungen."
    )
    return {
        "schluessel": schluessel("zugang", pid, gsc.get("property_url", ""), art),
        "project_id": pid,
        "quelle": "api",
        "art": art,
        "schwere": WARNUNG,
        "titel": titel,
        "details": fehler[:160],
    }


def meldung(e: Dict[str, Any], projekte: Dict[str, Dict[str, Any]]) -> str:
    zeichen = {KRITISCH: "🔴", WARNUNG: "🟠", INFO: "ℹ️"}.get(e["schwere"], "")
    name = (projekte.get(e["project_id"]) or {}).get("domain", e["project_id"])
    teile = [
        f"{zeichen} Search Console · {urlparse(name).hostname or name}",
        e["titel"],
    ]
    if e.get("felder"):
        teile.append("Gemeldet fehlt: " + ", ".join(e["felder"]))
    if e.get("details"):
        teile.append(e["details"])
    if e.get("reaktion"):
        teile.append("Autopilot: " + e["reaktion"])
    if e["schwere"] == KRITISCH:
        teile.append("Bitte heute in der Search Console ansehen.")
    return "\n".join(teile)


def melden(con, neu: List[Dict[str, Any]], projekte, trocken: bool) -> int:
    from .notifications.telegram import send_plain_message

    gesendet = 0
    for e in neu:
        if e["schwere"] == INFO:
            continue
        text = meldung(e, projekte)
        if trocken:
            print("--- wuerde melden ---\n" + text)
            continue
        if send_plain_message(text):
            con.execute(
                "UPDATE gsc_ereignisse SET gemeldet=1 WHERE schluessel=?",
                (e["schluessel"],),
            )
            gesendet += 1
    con.commit()
    return gesendet


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .historie import standard_db_pfad
    from .kundenbericht import WURZEL, lade_projekte

    ap = argparse.ArgumentParser(prog="python -m seo_autopilot.gsc_ereignisse")
    ap.add_argument("--projekt", help="nur dieses Projekt")
    ap.add_argument("--projects", default=str(WURZEL / "projects.yaml"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--ohne-mails", action="store_true")
    ap.add_argument(
        "--nur-mails",
        action="store_true",
        help="nur Google-Mails pruefen (fuer den Lauf alle zwei Stunden)",
    )
    ap.add_argument(
        "--trocken",
        action="store_true",
        help="nichts speichern, nichts senden, nur anzeigen",
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    projekte = {
        k: v for k, v in lade_projekte(args.projects).items() if v.get("enabled", True)
    }
    if args.projekt:
        projekte = {k: v for k, v in projekte.items() if k == args.projekt}
    con = db_oeffnen(":memory:" if args.trocken else (args.db or standard_db_pfad()))
    holen, heute = _holer(), date.today()
    neu = [] if args.ohne_mails else mail_lauf(con, projekte, holen, mails_holen())
    for pid, p in ({} if args.nur_mails else projekte).items():
        if "gsc" not in (p.get("enabled_sources") or []):
            continue
        try:
            neu += api_lauf(con, pid, p, holen, heute, trocken=args.trocken)
        except Exception as exc:
            logger.warning(
                f"[GSC-Ereignisse] {pid}: Search Console nicht erreichbar: {exc}"
            )
            e = zugang_ereignis(pid, p, str(exc))
            if ereignis_neu(con, e):
                neu.append(e)
    gesendet = melden(con, neu, projekte, args.trocken)
    if not args.trocken:
        con.execute(
            "INSERT INTO gsc_laeufe VALUES (?,?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "mails" if args.nur_mails else "voll",
            ),
        )
        con.commit()
    print(f"[GSC-Ereignisse] {len(neu)} neue Ereignisse, {gesendet} gemeldet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
