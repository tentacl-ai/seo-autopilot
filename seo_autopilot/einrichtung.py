"""
Einrichtung einer Website mit der „großen Packung" (seit Stufe 4).

Robert (18.09.2026): „Wenn wir den das nächste Mal installieren, machen wir immer
die große Packung mit API zu Analytics und Google Webmaster Tools, weil wir die
Daten im Autopiloten auch auswerten wollen. [...] Änderungen müssen auch bei den
bestehenden Kunden gemacht werden."

Große Packung = Search Console (mit Daten), Google Analytics 4, Kunden-Wochenbericht,
Bing/IndexNow, PageSpeed-Schlüssel, täglicher Cron, 16 Monate Suchhistorie.
DataForSEO gehört ausdrücklich NICHT dazu.

Zwei Wege:

    seo-autopilot einrichten --projekt kunde --domain https://kunde.de           # anzeigen
    seo-autopilot einrichten --projekt kunde --domain https://kunde.de --schreiben
    seo-autopilot einrichten --pruefen                                           # alle Kunden

Grundregeln:
- Alles Live-Zugreifen ist LESEND (HTTP GET, Search Console sites.list + eine
  Mini-Abfrage, GA4 runReport mit einer Zeile, GA4-Admin nur Listen).
- Geschrieben wird nur mit `--schreiben`, und dann nur die projects.yaml (mit
  Sicherung daneben) und die Suchhistorie in die eigene Datenbank.
- Die Cron-Zeile wird nur AUSGEGEBEN, nie installiert. Das GA4-Einrichtungsskript
  (Kanalgruppe/Schlüsselereignisse) wird nur empfohlen, nie ausgeführt.
- Jeder ❌ trägt eine Handlungsanweisung für einen Menschen und sagt, wer sie
  erledigen muss (Kunde oder wir).
- Projekte mit `paket: klein` sind bewusst ohne GA4/Bericht — keine Mahnung.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import yaml
from .core.config import settings

logger = logging.getLogger(__name__)

KONTO_STANDARD = settings.GSC_CREDENTIALS_PATH
INDEXNOW_SITES = settings.INDEXNOW_SITES or ""
AUTOPILOT_WURZEL = str(Path(__file__).resolve().parent.parent)


def _env_vorspann() -> str:
    """`. datei &&` je Datei aus CRON_ENV_DATEIEN (Zugangsdaten der eigenen
    Umgebung — Pfade stehen in der .env, nicht im oeffentlichen Code)."""
    dateien = [
        d.strip() for d in (settings.CRON_ENV_DATEIEN or "").split(",") if d.strip()
    ]
    if not dateien:
        return ""
    return "set -a && " + " && ".join(f". {d}" for d in dateien) + " && set +a && "


CRON_MUSTER = (
    "{m} {h} * * * {env}{wurzel}/venv/bin/python3 -m seo_autopilot.cli.main run"
    " --project-id {pid} >> {wurzel}/logs/cron.log 2>&1 # SEO Autopilot {host}"
)
# Audits laufen vor der Historie (11:15) und dem Wächter (11:30).
CRON_SLOTS = [(h, m) for h in range(7, 11) for m in (0, 30)] + [(11, 0)]

MAX_PAGES_MIN = 20
MAX_PAGES_MAX = 200
SITEMAP_KINDER_MAX = 20
HTTP_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; tentacl-seo-autopilot-einrichtung/1.0)"

OK, FEHLER, WARNUNG, INFO = "ok", "fehler", "warnung", "info"
SYMBOL = {OK: "✅", FEHLER: "❌", WARNUNG: "⚠️", INFO: "ℹ️"}
_RANG = {FEHLER: 0, WARNUNG: 1, INFO: 2, OK: 3}

WER_KUNDE = "Kunde"
WER_WIR = "wir"
WER_BETREUER = "Website-Betreuer"


# --------------------------------------------------------------------------
# Datenstrukturen
# --------------------------------------------------------------------------


@dataclass
class Schritt:
    nr: str
    titel: str
    status: str
    text: str
    anweisung: str = ""
    wer: str = ""

    def als_text(self) -> str:
        zeilen = [f"{SYMBOL[self.status]} {self.nr}. {self.titel}: {self.text}"]
        if self.anweisung:
            wer = f" [{self.wer}]" if self.wer else ""
            zeilen.append(f"      → Zu tun{wer}: {self.anweisung}")
        return "\n".join(zeilen)


@dataclass
class Website:
    """Was die Website selbst über sich verrät (Schritt 1)."""

    eingabe: str
    kanonisch: Optional[str] = None
    erreichbar: bool = False
    html: str = ""
    sitemap_url: Optional[str] = None
    seiten: List[str] = field(default_factory=list)
    fremde_hosts: List[str] = field(default_factory=list)

    @property
    def host(self) -> str:
        return host_von(self.kanonisch or self.eingabe)


@dataclass
class Ergebnis:
    projekt: str
    schritte: List[Schritt] = field(default_factory=list)
    yaml_block: str = ""
    cron_zeile: str = ""
    geschrieben: Optional[str] = None
    sicherung: Optional[str] = None

    @property
    def offene(self) -> List[Schritt]:
        return [s for s in self.schritte if s.status in (FEHLER, WARNUNG)]

    @property
    def vollstaendig(self) -> bool:
        return not any(s.status == FEHLER for s in self.schritte)

    def status_von(self, nr: str) -> Optional[Schritt]:
        return next((s for s in self.schritte if s.nr == nr), None)


# --------------------------------------------------------------------------
# Reine Hilfsfunktionen (ohne Netz, direkt testbar)
# --------------------------------------------------------------------------


def host_von(url: str) -> str:
    teil = url if "://" in url else f"https://{url}"
    return (urlparse(teil).hostname or "").lower()


def origin_von(url: str) -> str:
    teil = url if "://" in url else f"https://{url}"
    p = urlparse(teil)
    return f"{p.scheme}://{(p.hostname or '').lower()}"


def ohne_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def andere_www_variante(host: str) -> str:
    return host[4:] if host.startswith("www.") else f"www.{host}"


def schlechtester(stati: List[str]) -> str:
    return min(stati, key=lambda s: _RANG[s]) if stati else OK


def sitemap_locs(xml: str) -> Tuple[List[str], bool]:
    """(<loc>-Adressen, ist_index). Ohne XML-Bibliothek: robust gegen Namensräume."""
    locs = [
        m.strip()
        for m in re.findall(r"<(?:\w+:)?loc>\s*(.*?)\s*</(?:\w+:)?loc>", xml, re.S)
    ]
    ist_index = bool(re.search(r"<(?:\w+:)?sitemapindex\b", xml))
    return [l.replace("&amp;", "&") for l in locs if l], ist_index


def fremde_hosts(adressen: List[str], kanon_host: str) -> List[str]:
    """Hosts in der Sitemap, die nicht die kanonische Domain sind.

    Auch die www-Variante zählt als fremd: Google soll nur EINE Schreibweise
    sehen. (18.09.2026: unter einer Port-Adresse wurde eine ganz andere
    Kundenseite geprüft — eine Sitemap mit fremden Hosts ist dafür ein Vorbote.)
    """
    gefunden = {host_von(a) for a in adressen if a.startswith("http")}
    return sorted(h for h in gefunden if h and h != kanon_host)


def max_pages_vorschlag(anzahl_seiten: int) -> int:
    """Genug Platz für alle Sitemap-Seiten plus 10 %, auf Zehner gerundet."""
    if anzahl_seiten <= 0:
        return 40
    ziel = int(math.ceil(anzahl_seiten * 1.1 / 10.0) * 10)
    return max(MAX_PAGES_MIN, min(MAX_PAGES_MAX, ziel))


_TRENNER = re.compile(r"\s+[|–—·:-]\s+")


def _schema_namen(html: str) -> List[Tuple[str, str]]:
    """(Typ, Name) aus allen JSON-LD-Blöcken der Seite."""
    ergebnis: List[Tuple[str, str]] = []
    for roh in re.findall(
        r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I
    ):
        try:
            daten = json.loads(roh.strip())
        except (ValueError, TypeError):
            continue
        stapel = [daten]
        while stapel:
            teil = stapel.pop(0)
            if isinstance(teil, list):
                stapel.extend(teil)
            elif isinstance(teil, dict):
                stapel.extend(teil.get("@graph") or [])
                typ = teil.get("@type")
                typ = typ[0] if isinstance(typ, list) and typ else typ
                if isinstance(typ, str) and isinstance(teil.get("name"), str):
                    ergebnis.append((typ, teil["name"].strip()))
    return ergebnis


def _meta(html: str, eigenschaft: str) -> Optional[str]:
    for muster in (
        rf'<meta[^>]+(?:property|name)=["\']{eigenschaft}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{eigenschaft}["\']',
    ):
        m = re.search(muster, html, re.I)
        if m:
            return m.group(1).strip()
    return None


def _titel(html: str) -> Optional[str]:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if not m:
        return None
    import html as html_mod

    return re.sub(r"\s+", " ", html_mod.unescape(m.group(1))).strip() or None


ORGANISATIONS_TYPEN = (
    "Organization",
    "LocalBusiness",
    "Corporation",
    "ProfessionalService",
    "Campground",
    "Person",
)


def erwartet_vorschlag(html: str) -> Optional[str]:
    """Text, der auf der Startseite stehen MUSS (Feld `erwartet`).

    Zuerst der Name der Organisation im Schema, dann der Seitentitel ohne
    angehängten Zusatz („Startseite | Firma" → „Startseite"). Der Audit bricht ab,
    wenn der Text in Titel/H1/Schema-Name fehlt — so wird nie wieder eine fremde
    Website unter falschem Namen geprüft.
    """
    namen = _schema_namen(html)
    for typ in ORGANISATIONS_TYPEN:
        for t, name in namen:
            if t == typ and name:
                return name
    titel = _titel(html)
    if not titel:
        return None
    return _TRENNER.split(titel)[0].strip() or titel


def erwartet_auf_seite(html: str, erwartet: str) -> bool:
    """Steht `erwartet` in Titel, einer H1 oder einem Schema-Namen (ohne Groß/klein)?"""
    import html as html_mod

    ziel = erwartet.strip().lower()
    orte = [_titel(html) or ""] + [n for _, n in _schema_namen(html)]
    orte += [
        re.sub(r"<[^>]+>", " ", html_mod.unescape(h))
        for h in re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    ]
    return any(ziel in re.sub(r"\s+", " ", o).lower() for o in orte)


def branche_vorschlag(html: str) -> Optional[str]:
    text = _meta(html, "description") or _meta(html, "og:description")
    if not text:
        return None
    return text if len(text) <= 200 else text[:197].rsplit(" ", 1)[0] + " …"


def cron_zeiten(crontab: str) -> List[Tuple[int, int]]:
    """Uhrzeiten aller aktiven Zeilen, die den SEO-Autopiloten betreffen."""
    zeiten = []
    for zeile in crontab.splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "seo" not in zeile.lower():
            continue
        teile = zeile.split()
        if len(teile) > 2 and teile[0].isdigit() and teile[1].isdigit():
            zeiten.append((int(teile[1]), int(teile[0])))
    return zeiten


def naechster_slot(crontab: str) -> Tuple[int, int]:
    belegt = set(cron_zeiten(crontab))
    for slot in CRON_SLOTS:
        if slot not in belegt:
            return slot
    return (11, 5)


def cron_zeile(pid: str, host: str, slot: Tuple[int, int]) -> str:
    h, m = slot
    return CRON_MUSTER.format(
        m=m, h=h, pid=pid, host=host, wurzel=AUTOPILOT_WURZEL, env=_env_vorspann()
    )


def cron_vorhanden(crontab: str, pid: str) -> Optional[str]:
    """Uhrzeit (HH:MM) der aktiven Audit-Zeile des Projekts, sonst None."""
    for zeile in crontab.splitlines():
        s = zeile.strip()
        if s.startswith("#") or not re.search(
            rf"--project-id\s+{re.escape(pid)}(\s|$)", s
        ):
            continue
        teile = s.split()
        if teile[0].isdigit() and teile[1].isdigit():
            return f"{int(teile[1]):02d}:{int(teile[0]):02d}"
        return "?"
    return None


def finde_gsc_property(
    host: str, eintraege: List[Dict[str, Any]], gewuenscht: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Passende Search-Console-Property aus sites.list.

    Domain-Property (sc-domain:) schlägt URL-Präfix, weil sie http/https und
    www/ohne-www zugleich abdeckt. Nicht bestätigte Einträge zählen nur, wenn
    nichts anderes passt (dann meldet der Aufrufer „Zugriff fehlt").
    """
    if gewuenscht:
        for e in eintraege:
            if e.get("siteUrl") == gewuenscht:
                return e
        return None
    kern = ohne_www(host)
    domain_treffer, praefix_treffer = [], []
    for e in eintraege:
        site = e.get("siteUrl", "")
        if site.startswith("sc-domain:"):
            d = site.split(":", 1)[1].lower()
            if kern == d or kern.endswith("." + d) or host == d:
                domain_treffer.append((len(d), e))
        elif ohne_www(host_von(site)) == kern:
            praefix_treffer.append((0 if host_von(site) == host else 1, e))
    kandidaten = [e for _, e in sorted(domain_treffer, key=lambda x: -x[0])]
    kandidaten += [e for _, e in sorted(praefix_treffer, key=lambda x: x[0])]
    bestaetigt = [
        e for e in kandidaten if e.get("permissionLevel") != "siteUnverifiedUser"
    ]
    return (bestaetigt or kandidaten or [None])[0]


def finde_ga4_property(
    host: str, properties: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """GA4-Property, deren Web-Datenstream auf diese Website zeigt."""
    kern = ohne_www(host)
    for p in properties:
        for uri in p.get("streams") or []:
            if ohne_www(host_von(uri)) == kern:
                return p
    return None


def indexnow_eintrag(host: str, pfad: str = INDEXNOW_SITES) -> Optional[Dict[str, Any]]:
    """Eintrag der Website in der Bing/IndexNow-Liste (nur lesen)."""
    try:
        daten = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for s in daten.get("sites") or []:
        if ohne_www(str(s.get("host", "")).lower()) == ohne_www(host):
            return s
    return None


def pagespeed_da(projects_pfad: Optional[str] = None) -> bool:
    """Ist ein PageSpeed-Schlüssel hinterlegt? Nur ja/nein, nie der Wert.

    Wie der Wächter (Umgebung, Settings) — zusätzlich die `.env` neben der
    geprüften Projektliste, damit `--pruefen` aus einer Arbeitskopie heraus die
    Installation bewertet, deren Projekte es prüft.
    """
    from .health import _globaler_pagespeed_schluessel

    if _globaler_pagespeed_schluessel():
        return True
    if not projects_pfad:
        return False
    env = Path(projects_pfad).resolve().parent / ".env"
    try:
        for zeile in env.read_text(encoding="utf-8").splitlines():
            name, _, wert = zeile.partition("=")
            if name.strip() == "PAGESPEED_API_KEY" and wert.strip().strip("\"'"):
                return True
    except OSError:
        return False
    return False


# --------------------------------------------------------------------------
# Live-Zugriffe (nur lesend) — in Tests durch eine Attrappe ersetzt
# --------------------------------------------------------------------------


@dataclass
class Antwort:
    status: int
    url: str
    text: str = ""
    kette: List[Tuple[str, int]] = field(default_factory=list)


class Zugriffe:
    """Alle Außenkontakte der Einrichtung an einer Stelle — und alle nur lesend."""

    GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
    GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

    def __init__(self, credentials_path: str = KONTO_STANDARD):
        self.credentials_path = credentials_path
        self._gsc = None

    # -- HTTP --------------------------------------------------------------
    def get(self, url: str) -> Optional[Antwort]:
        import httpx

        try:
            with httpx.Client(
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as c:
                r = c.get(url)
            kette = [(str(h.url), h.status_code) for h in r.history]
            return Antwort(r.status_code, str(r.url), r.text, kette)
        except Exception as exc:
            logger.info(f"[Einrichtung] {url} nicht erreichbar: {type(exc).__name__}")
            return None

    # -- Dienstkonto -------------------------------------------------------
    def dienstkonto(self) -> str:
        try:
            daten = json.loads(Path(self.credentials_path).read_text(encoding="utf-8"))
            return str(daten.get("client_email") or "?")
        except (OSError, ValueError):
            return "?"

    def _cred(self, scope: str):
        from google.oauth2.service_account import Credentials

        return Credentials.from_service_account_file(
            self.credentials_path, scopes=[scope]
        )

    # -- Search Console ----------------------------------------------------
    def _gsc_dienst(self):
        if self._gsc is None:
            from googleapiclient.discovery import build

            self._gsc = build(
                "webmasters",
                "v3",
                credentials=self._cred(self.GSC_SCOPE),
                cache_discovery=False,
            )
        return self._gsc

    def gsc_properties(self) -> List[Dict[str, Any]]:
        return self._gsc_dienst().sites().list().execute().get("siteEntry", [])

    def gsc_summe(self, property_url: str, von: date, bis: date) -> Dict[str, Any]:
        body = {"startDate": von.isoformat(), "endDate": bis.isoformat(), "rowLimit": 1}
        rows = (
            self._gsc_dienst()
            .searchanalytics()
            .query(siteUrl=property_url, body=body)
            .execute()
            .get("rows", [])
        )
        return rows[0] if rows else {}

    def gsc_quelle(self):
        from .sources.gsc import GSCDataSource

        return GSCDataSource(self.credentials_path)

    # -- GA4 ---------------------------------------------------------------
    def ga4_properties(self) -> List[Dict[str, Any]]:
        from google.analytics.admin_v1alpha import AnalyticsAdminServiceClient

        admin = AnalyticsAdminServiceClient(credentials=self._cred(self.GA4_SCOPE))
        ergebnis = []
        for konto in admin.list_account_summaries():
            for p in konto.property_summaries:
                streams = [
                    s.web_stream_data.default_uri
                    for s in admin.list_data_streams(parent=p.property)
                    if s.web_stream_data and s.web_stream_data.default_uri
                ]
                ergebnis.append(
                    {
                        "id": p.property.split("/")[-1],
                        "name": p.display_name,
                        "konto": konto.display_name,
                        "streams": streams,
                    }
                )
        return ergebnis

    def ga4_test(self, property_id: str) -> int:
        """runReport mit genau einer Zeile. Rückgabe: aktive Nutzer (7 Tage)."""
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange,
            Metric,
            RunReportRequest,
        )

        client = BetaAnalyticsDataClient(credentials=self._cred(self.GA4_SCOPE))
        antwort = client.run_report(
            RunReportRequest(
                property=f"properties/{property_id}",
                metrics=[Metric(name="activeUsers")],
                date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
                limit=1,
            )
        )
        if not antwort.rows:
            return 0
        return int(float(antwort.rows[0].metric_values[0].value or 0))

    # -- Crontab -----------------------------------------------------------
    def crontab(self) -> str:
        from .health import _crontab_text

        return _crontab_text()


def _kurz(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip()
    return (text[:160] + " …") if len(text) > 160 else text


def _ist_zugriffsfehler(exc: Exception) -> bool:
    t = str(exc).lower()
    return any(s in t for s in ("403", "permission", "forbidden", "not have access"))


# --------------------------------------------------------------------------
# Schritt 1 — Website, Weiterleitungen, Sitemap, `erwartet`
# --------------------------------------------------------------------------


def _weiterleitung_art(kette: List[Tuple[str, int]]) -> str:
    codes = {c for _, c in kette}
    return "dauerhaft" if codes and codes <= {301, 308} else "vorübergehend"


def pruefe_website(z: Zugriffe, domain: str) -> Tuple[Schritt, Website]:
    w = Website(eingabe=origin_von(domain))
    haupt = z.get(w.eingabe + "/")
    if haupt is None or haupt.status >= 400:
        grund = "keine Antwort" if haupt is None else f"HTTP {haupt.status}"
        return (
            Schritt(
                "1",
                "Website",
                FEHLER,
                f"{w.eingabe} nicht erreichbar ({grund}).",
                "Adresse prüfen (Tippfehler? DNS beim Hoster eingerichtet? Server läuft?). "
                "Ohne erreichbare Website ist keine Einrichtung möglich.",
                WER_BETREUER,
            ),
            w,
        )
    w.erreichbar, w.html = True, haupt.text
    w.kanonisch = origin_von(haupt.url)
    stati, zeilen, auftraege = [OK], [], []
    if w.kanonisch != w.eingabe:
        stati.append(WARNUNG)
        zeilen.append(
            f"{w.eingabe} leitet {_weiterleitung_art(haupt.kette)} weiter auf {w.kanonisch}"
        )
        auftraege.append(
            (
                f"Als Domain {w.kanonisch} eintragen (im YAML-Vorschlag schon so).",
                WER_WIR,
            )
        )
    else:
        zeilen.append(f"{w.kanonisch} erreichbar (HTTP {haupt.status})")
    for url in (f"https://{andere_www_variante(w.host)}/", f"http://{w.host}/"):
        status, satz, anweisung = _pruefe_variante(z, w, url)
        stati.append(status)
        zeilen.append(satz)
        if anweisung:
            auftraege.append((anweisung, WER_BETREUER))
    wer = ", ".join(dict.fromkeys(w_ for _, w_ in auftraege))
    return (
        Schritt(
            "1",
            "Website",
            schlechtester(stati),
            "; ".join(zeilen) + f". Kanonische Domain: {w.kanonisch}",
            " ".join(a for a, _ in auftraege),
            wer,
        ),
        w,
    )


def _pruefe_variante(z: Zugriffe, w: Website, url: str) -> Tuple[str, str, str]:
    """(Status, Satz, Anweisung) für eine Nebenadresse (www-Variante, http)."""
    a = z.get(url)
    ziel = url.rstrip("/")
    if a is None:
        if url.startswith("http://"):
            return INFO, f"{ziel} nicht erreichbar", ""
        return INFO, f"{ziel} nicht erreichbar (kein DNS-Eintrag, unkritisch)", ""
    if origin_von(a.url) == w.kanonisch and a.kette:
        if _weiterleitung_art(a.kette) == "dauerhaft":
            return OK, f"{ziel} → {w.kanonisch} (301)", ""
        return (
            WARNUNG,
            f"{ziel} leitet nur vorübergehend (302/307) um",
            f"Beim Hoster {ziel} per 301 (dauerhaft) auf {w.kanonisch} umleiten.",
        )
    if a.status < 400:
        return (
            WARNUNG,
            f"{ziel} liefert eigene Seiten ohne Weiterleitung (doppelte Inhalte)",
            f"Beim Hoster {ziel} per 301 auf {w.kanonisch} umleiten.",
        )
    return INFO, f"{ziel} antwortet mit HTTP {a.status}", ""


def _sitemap_kandidaten(z: Zugriffe, w: Website) -> List[str]:
    robots = z.get(f"{w.kanonisch}/robots.txt")
    gefunden = []
    if robots and robots.status == 200:
        gefunden = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots.text)
    return gefunden or [
        f"{w.kanonisch}/sitemap.xml",
        f"{w.kanonisch}/sitemap_index.xml",
    ]


def lade_sitemap(z: Zugriffe, w: Website) -> None:
    """Füllt w.seiten, w.sitemap_url und w.fremde_hosts (Sitemap-Index aufgelöst)."""
    for kandidat in _sitemap_kandidaten(z, w):
        a = z.get(kandidat)
        if not a or a.status != 200 or "<" not in a.text:
            continue
        locs, ist_index = sitemap_locs(a.text)
        if not locs:
            continue
        w.sitemap_url = kandidat
        if ist_index:
            kinder, locs = locs[:SITEMAP_KINDER_MAX], []
            w.fremde_hosts = fremde_hosts(kinder, w.host)
            for kind in kinder:
                k = z.get(kind)
                if k and k.status == 200:
                    locs.extend(sitemap_locs(k.text)[0])
        w.seiten = list(dict.fromkeys(locs))
        w.fremde_hosts = sorted(
            set(w.fremde_hosts) | set(fremde_hosts(w.seiten, w.host))
        )
        return


def schritt_sitemap(w: Website, max_pages: Optional[int] = None) -> Schritt:
    if not w.sitemap_url:
        return Schritt(
            "1b",
            "Sitemap",
            WARNUNG,
            "Keine Sitemap gefunden (robots.txt, /sitemap.xml).",
            "sitemap.xml anlegen und in robots.txt eintragen — sonst findet der "
            "Autopilot Unterseiten nur über Links.",
            WER_BETREUER,
        )
    teile = [f"{len(w.seiten)} Seiten in {w.sitemap_url}"]
    stati, anweisung = [OK], []
    if w.fremde_hosts:
        stati.append(WARNUNG)
        teile.append(f"FREMDE Hosts in der Sitemap: {', '.join(w.fremde_hosts)}")
        anweisung.append(
            f"Sitemap so erzeugen, dass sie nur Adressen von {w.host} enthält "
            "(Sitemap-Generator/Basis-URL prüfen)."
        )
    if host_von(w.sitemap_url) != w.host:
        stati.append(WARNUNG)
        teile.append(
            f"robots.txt verweist auf {w.sitemap_url} (nicht die kanonische Domain)"
        )
        anweisung.append(
            f"In robots.txt die Zeile `Sitemap: {w.kanonisch}/{w.sitemap_url.split('/', 3)[-1]}` "
            "verwenden."
        )
    if max_pages is not None and max_pages < len(w.seiten):
        stati.append(WARNUNG)
        teile.append(f"max_pages {max_pages} < {len(w.seiten)} Sitemap-Seiten")
        anweisung.append(
            f"adapter_config.max_pages auf {max_pages_vorschlag(len(w.seiten))} setzen."
        )
    betreuer = w.fremde_hosts or host_von(w.sitemap_url) != w.host
    wer = WER_BETREUER if betreuer else (WER_WIR if anweisung else "")
    if betreuer and max_pages is not None and max_pages < len(w.seiten):
        wer = f"{WER_BETREUER}, {WER_WIR}"
    return Schritt(
        "1b",
        "Sitemap",
        schlechtester(stati),
        "; ".join(teile),
        " ".join(anweisung),
        wer,
    )


def schritt_erwartet(
    w: Website, vorhanden: Optional[str]
) -> Tuple[Schritt, Optional[str]]:
    vorschlag = erwartet_vorschlag(w.html) if w.html else None
    if vorhanden:
        if w.html and not erwartet_auf_seite(w.html, vorhanden):
            return (
                Schritt(
                    "1c",
                    "Erkennung (erwartet)",
                    FEHLER,
                    f'„{vorhanden}" steht NICHT in Titel/H1/Schema der Startseite '
                    "— falsche Website oder Name geändert?",
                    f"Domain prüfen; ist die Seite richtig, `erwartet` anpassen "
                    f'(Vorschlag: „{vorschlag}").',
                    WER_WIR,
                ),
                vorhanden,
            )
        return Schritt("1c", "Erkennung (erwartet)", OK, f'„{vorhanden}"'), vorhanden
    if vorschlag:
        return (
            Schritt(
                "1c",
                "Erkennung (erwartet)",
                WARNUNG,
                f'Feld `erwartet` fehlt — Vorschlag aus der Startseite: „{vorschlag}"',
                f"In projects.yaml `erwartet: {vorschlag}` eintragen (im YAML-Block "
                "schon enthalten). Schützt davor, eine fremde Website zu prüfen.",
                WER_WIR,
            ),
            vorschlag,
        )
    return (
        Schritt(
            "1c",
            "Erkennung (erwartet)",
            WARNUNG,
            "Feld `erwartet` fehlt, Startseite liefert weder Schema-Namen noch Titel.",
            "Firmennamen von Hand als `erwartet:` eintragen.",
            WER_WIR,
        ),
        None,
    )


# --------------------------------------------------------------------------
# Schritt 2 — Search Console
# --------------------------------------------------------------------------

GSC_ANLEITUNG = (
    "In Search Console (search.google.com/search-console) die Property für {host} "
    'öffnen — am besten als Domain-Property „sc-domain:{kern}" (Bestätigung per '
    "DNS-TXT-Eintrag beim Hoster) — dann Einstellungen → Nutzer und Berechtigungen → "
    'Nutzer hinzufügen: {konto} mit Berechtigung „Eingeschränkt".'
)


def pruefe_gsc(
    z: Zugriffe,
    host: str,
    gewuenscht: Optional[str] = None,
    heute: Optional[date] = None,
) -> Tuple[Schritt, Optional[str]]:
    anleitung = GSC_ANLEITUNG.format(
        host=host, kern=ohne_www(host), konto=z.dienstkonto()
    )
    try:
        eintraege = z.gsc_properties()
    except Exception as exc:
        return (
            Schritt(
                "2",
                "Search Console",
                FEHLER,
                f"Dienstkonto kann Search Console " f"nicht lesen: {_kurz(exc)}",
                "Schlüsseldatei/API-Freigabe im "
                "Google-Cloud-Projekt tentacl-seo prüfen.",
                WER_WIR,
            ),
            None,
        )
    treffer = finde_gsc_property(host, eintraege, gewuenscht)
    if not treffer or treffer.get("permissionLevel") == "siteUnverifiedUser":
        was = f"Property {gewuenscht}" if gewuenscht else f"keine Property für {host}"
        return (
            Schritt(
                "2",
                "Search Console",
                FEHLER,
                f"Zugriff fehlt — {was} im " "Dienstkonto sichtbar.",
                anleitung,
                WER_KUNDE,
            ),
            None,
        )
    prop = treffer["siteUrl"]
    return _gsc_datenprobe(z, prop, treffer.get("permissionLevel", ""), heute), prop


def _gsc_datenprobe(
    z: Zugriffe, prop: str, recht: str, heute: Optional[date]
) -> Schritt:
    ende = (heute or datetime.now(timezone.utc).date()) - timedelta(days=3)
    try:
        summe = z.gsc_summe(prop, ende - timedelta(days=6), ende)
    except Exception as exc:
        status_wer = (
            (FEHLER, WER_KUNDE) if _ist_zugriffsfehler(exc) else (FEHLER, WER_WIR)
        )
        return Schritt(
            "2",
            "Search Console",
            status_wer[0],
            f"{prop}: Abfrage " f"scheitert ({_kurz(exc)})",
            "Berechtigung des Dienstkontos in "
            'Search Console prüfen (mind. „Eingeschränkt").',
            status_wer[1],
        )
    klicks, einbl = int(summe.get("clicks", 0)), int(summe.get("impressions", 0))
    if einbl == 0:
        return Schritt(
            "2",
            "Search Console",
            WARNUNG,
            f"{prop} ({recht}): Zugriff ok, aber 0 Einblendungen in 7 Tagen",
            "Website ist in Google (noch) nicht sichtbar: Sitemap in Search Console "
            'einreichen, Indexierung prüfen (Seiten → „Nicht indexiert").',
            WER_WIR,
        )
    return Schritt(
        "2",
        "Search Console",
        OK,
        f"{prop} ({recht}): Daten kommen an — "
        f"{klicks} Klicks, {einbl} Einblendungen in 7 Tagen",
    )


# --------------------------------------------------------------------------
# Schritt 3 — Google Analytics 4
# --------------------------------------------------------------------------

GA4_ANLEITUNG = (
    'In Google Analytics → Verwaltung → Property-Zugriffsverwaltung → „+" → Nutzer '
    'hinzufügen: {konto}, Rolle „Betrachter". Gibt es noch keine GA4-Property, zuerst '
    "anlegen und das Tag (G-…) auf der Website einbauen lassen. Siehe docs/ga4-setup.md."
)
GA4_SKRIPT_HINWEIS = (
    'Optional: Kanalgruppe „Herkunft" + Schlüsselereignisse mit '
    "scripts/ga4_einrichten.py (Trockenlauf zuerst; Dienstkonto braucht dafür "
    '„Bearbeiter"; PROPERTY im Skript anpassen) — wird NIE automatisch ausgeführt.'
)


def pruefe_ga4(
    z: Zugriffe, host: str, gewuenscht: Optional[str] = None, mit_hinweis: bool = True
) -> Tuple[Schritt, Optional[str]]:
    anleitung = GA4_ANLEITUNG.format(konto=z.dienstkonto())
    pid, zusatz = (str(gewuenscht).strip() if gewuenscht else None), ""
    if not pid:
        try:
            gefunden = finde_ga4_property(host, z.ga4_properties())
        except Exception as exc:
            gefunden = None
            zusatz = f" (Suche über GA4-Verwaltung scheiterte: {_kurz(exc)})"
        if not gefunden:
            return (
                Schritt(
                    "3",
                    "Google Analytics 4",
                    FEHLER,
                    "Keine GA4-Property für "
                    f"{host} im Dienstkonto sichtbar{zusatz}.",
                    anleitung,
                    WER_KUNDE,
                ),
                None,
            )
        pid = gefunden["id"]
        zusatz = f" (automatisch gefunden: „{gefunden['name']}\")"
    try:
        nutzer = z.ga4_test(pid)
    except Exception as exc:
        if _ist_zugriffsfehler(exc):
            return (
                Schritt(
                    "3",
                    "Google Analytics 4",
                    FEHLER,
                    f"Property {pid}: " "Zugriff fehlt.",
                    anleitung,
                    WER_KUNDE,
                ),
                None,
            )
        return (
            Schritt(
                "3",
                "Google Analytics 4",
                FEHLER,
                f"Property {pid}: Abfrage " f"scheitert ({_kurz(exc)}).",
                "Property-ID prüfen (nur " "Ziffern, nicht G-…).",
                WER_WIR,
            ),
            None,
        )
    if nutzer == 0:
        return (
            Schritt(
                "3",
                "Google Analytics 4",
                WARNUNG,
                f"Property {pid}{zusatz}: " "Zugriff ok, aber 0 Nutzer in 7 Tagen.",
                "Prüfen, ob das "
                "GA4-Tag auf der Website eingebaut ist und die Einwilligung "
                "(Cookie-Banner) Messung zulässt.",
                WER_BETREUER,
            ),
            pid,
        )
    return (
        Schritt(
            "3",
            "Google Analytics 4",
            OK,
            f"Property {pid}{zusatz}: Daten kommen "
            f"an — {nutzer} Nutzer in 7 Tagen."
            + (f" {GA4_SKRIPT_HINWEIS}" if mit_hinweis else ""),
        ),
        pid,
    )


# --------------------------------------------------------------------------
# Schritt 4/5 — Bing/IndexNow, PageSpeed
# --------------------------------------------------------------------------


def pruefe_indexnow(
    z: Zugriffe,
    w: Website,
    key: Optional[str],
    adapter: str = "generic",
    root_path: Optional[str] = None,
    zeige_schluessel: bool = True,
) -> Tuple[Schritt, str]:
    """IndexNow-Schlüsseldatei. Ohne Schlüssel wird einer erzeugt — hochladen
    muss ihn ein Mensch (der Autopilot legt nichts auf fremden Servern ab)."""
    kanon = w.kanonisch or w.eingabe
    if not key:
        neu = secrets.token_hex(16)
        ort = (
            f"nach {root_path.rstrip('/')}/{neu}.txt legen"
            if adapter == "static" and root_path
            else "ins Wurzelverzeichnis der Website hochladen"
        )
        return (
            Schritt(
                "4",
                "IndexNow (Bing/Copilot)",
                WARNUNG,
                "Kein Schlüssel vorhanden — " "neuen erzeugt (steht im YAML-Block).",
                f'Datei „{neu}.txt" mit '
                f'genau dem Inhalt „{neu}" {ort}, abrufbar unter {kanon}/{neu}.txt.',
                WER_BETREUER if adapter != "static" else WER_WIR,
            ),
            neu,
        )
    name = f"{key}.txt" if zeige_schluessel else "<schlüssel>.txt"
    a = z.get(f"{kanon}/{key}.txt")
    if a and a.status == 200 and a.text.strip() == key:
        return (
            Schritt(
                "4",
                "IndexNow (Bing/Copilot)",
                OK,
                f"Schlüsseldatei online " f"({kanon}/{name}).",
            ),
            key,
        )
    return (
        Schritt(
            "4",
            "IndexNow (Bing/Copilot)",
            WARNUNG,
            f"Schlüssel bekannt, aber "
            f"{kanon}/{name} fehlt oder hat falschen Inhalt.",
            "Schlüsseldatei "
            "(Inhalt = Schlüssel aus projects.yaml source_config.indexnow.key) ins "
            "Wurzelverzeichnis legen.",
            WER_BETREUER,
        ),
        key,
    )


def pruefe_bing(host: str, sites_pfad: str = INDEXNOW_SITES) -> Schritt:
    if indexnow_eintrag(host, sites_pfad):
        return Schritt(
            "4b",
            "Bing Webmaster",
            OK,
            f"{host} steht im täglichen "
            "Bing-Abgleich (<INDEXNOW_SITES aus der .env>).",
        )
    return Schritt(
        "4b",
        "Bing Webmaster",
        WARNUNG,
        f"{host} fehlt im Bing-Abgleich.",
        "In Bing Webmaster Tools die Website hinzufügen (am schnellsten: „Aus Google "
        'Search Console importieren") und in <INDEXNOW_SITES aus der .env> '
        "eintragen (host, key, sitemap) — erst dann erscheint Bing im Wochenbericht.",
        WER_WIR,
    )


def pruefe_pagespeed(projects_pfad: Optional[str]) -> Schritt:
    if pagespeed_da(projects_pfad):
        return Schritt("5", "PageSpeed-Schlüssel", OK, "vorhanden")
    return Schritt(
        "5",
        "PageSpeed-Schlüssel",
        FEHLER,
        "fehlt — keine Core Web Vitals.",
        "Kostenlosen Schlüssel anlegen (developers.google.com/speed/docs/"
        "insights/v5/get-started) und als PAGESPEED_API_KEY in die .env "
        "des Autopiloten eintragen.",
        WER_WIR,
    )


# --------------------------------------------------------------------------
# Schritt 6 — Projekt-Eintrag
# --------------------------------------------------------------------------


def standard_konto(projekte: Dict[str, Dict[str, Any]]) -> str:
    for cfg in projekte.values():
        pfad = ((cfg.get("source_config") or {}).get("gsc") or {}).get(
            "credentials_path"
        )
        if pfad:
            return str(pfad)
    return KONTO_STANDARD


def standard_empfaenger(projekte: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Häufigster Berichtsempfänger der bestehenden Projekte (nie hart verdrahtet)."""
    zaehler: Dict[str, int] = {}
    for cfg in projekte.values():
        e = (cfg.get("bericht") or {}).get("empfaenger")
        if e:
            zaehler[e] = zaehler.get(e, 0) + 1
    return max(zaehler, key=zaehler.get) if zaehler else None


def baue_eintrag(
    alt: Optional[Dict[str, Any]],
    *,
    domain: str,
    name: str,
    konto: str,
    gsc_property: Optional[str],
    ga4_property: Optional[str],
    indexnow_key: Optional[str],
    empfaenger: Optional[str],
    branche: Optional[str],
    adapter: str,
    root_path: Optional[str],
    max_pages: int,
    slot: Tuple[int, int],
    erwartet: Optional[str],
) -> Dict[str, Any]:
    """Vollständiger Projekt-Eintrag. Bestehende Werte werden NIE überschrieben —
    nur Lücken gefüllt (und max_pages angehoben, falls zu klein)."""
    e: Dict[str, Any] = copy.deepcopy(alt or {})
    e.setdefault("domain", domain)
    e.setdefault("name", name)
    e.setdefault("adapter_type", adapter)
    ac = e.setdefault("adapter_config", {}) or {}
    e["adapter_config"] = ac
    if adapter == "static" and root_path:
        ac.setdefault("root_path", root_path)
    ac["max_pages"] = max(int(ac.get("max_pages") or 0), max_pages)
    quellen = list(e.get("enabled_sources") or [])
    sc = e.setdefault("source_config", {}) or {}
    e["source_config"] = sc
    if gsc_property:
        sc.setdefault("gsc", {"property_url": gsc_property, "credentials_path": konto})
    if ga4_property:
        sc.setdefault(
            "ga4", {"property_id": str(ga4_property), "credentials_path": konto}
        )
    for q in ("gsc", "ga4"):
        if q not in quellen:
            quellen.append(q)
    e["enabled_sources"] = quellen
    if indexnow_key:
        sc.setdefault("indexnow", {"key": indexnow_key})
    e.setdefault("enabled", True)
    e.setdefault("schedule_cron", f"{slot[1]} {slot[0]} * * *")
    e.setdefault("run_interval_days", 1)
    e.setdefault("betriebsart", "copilot")
    e.setdefault("auto_fix_enabled", False)
    if not e.get("bericht"):
        e["bericht"] = {"aktiv": True, "empfaenger": empfaenger or "BITTE-EINTRAGEN"}
        if branche:
            e["bericht"]["branche"] = branche
    if erwartet:
        e.setdefault("erwartet", erwartet)
    return e


def yaml_block(pid: str, eintrag: Dict[str, Any]) -> str:
    return yaml.dump(
        {pid: eintrag}, default_flow_style=False, sort_keys=False, allow_unicode=True
    )


def schreibe_projekt(
    pfad: str, pid: str, eintrag: Dict[str, Any], heute: Optional[date] = None
) -> str:
    """Schreibt den Eintrag in die Projektliste — IMMER mit Sicherung davor.

    Rückgabe: Pfad der Sicherung `projects.yaml.bak-einrichtung-<id>-<datum>`.
    """
    ziel = Path(pfad)
    tag = (heute or date.today()).isoformat()
    sicherung = ziel.with_name(f"{ziel.name}.bak-einrichtung-{pid}-{tag}")
    daten: Dict[str, Any] = {"projects": {}}
    if ziel.exists():
        shutil.copy2(ziel, sicherung)
        daten = yaml.safe_load(ziel.read_text(encoding="utf-8")) or {"projects": {}}
    daten.setdefault("projects", {})
    daten["projects"][pid] = eintrag
    ziel.write_text(
        yaml.dump(daten, default_flow_style=False, sort_keys=False), encoding="utf-8"
    )
    return str(sicherung)


# --------------------------------------------------------------------------
# Ablauf „einrichten --projekt"
# --------------------------------------------------------------------------


@dataclass
class Auftrag:
    projekt: str
    domain: Optional[str] = None
    name: Optional[str] = None
    gsc_property: Optional[str] = None
    ga4_property: Optional[str] = None
    bericht_an: Optional[str] = None
    adapter: str = "generic"
    root_path: Optional[str] = None
    schreiben: bool = False


def _lade(projects_pfad: str) -> Dict[str, Dict[str, Any]]:
    from .health import _lade_projekte

    return _lade_projekte(Path(projects_pfad))


def einrichten(
    auftrag: Auftrag,
    projects_pfad: str,
    z: Optional[Zugriffe] = None,
    db_pfad: Optional[str] = None,
    heute: Optional[date] = None,
    sites_pfad: str = INDEXNOW_SITES,
) -> Ergebnis:
    projekte = _lade(projects_pfad)
    alt = projekte.get(auftrag.projekt)
    z = z or Zugriffe(standard_konto(projekte))
    erg = Ergebnis(projekt=auftrag.projekt)
    domain = auftrag.domain or (alt or {}).get("domain")
    if not domain:
        erg.schritte.append(
            Schritt(
                "1",
                "Website",
                FEHLER,
                "Keine Domain angegeben.",
                "--domain https://… angeben.",
                WER_WIR,
            )
        )
        return erg
    if alt and alt.get("enabled") is False and auftrag.schreiben:
        erg.schritte.append(
            Schritt(
                "0",
                "Projekt",
                FEHLER,
                f"{auftrag.projekt} ist "
                "abgeschaltet (enabled: false) — wird hier nicht "
                "verändert.",
                "Erst bewusst einschalten.",
                WER_WIR,
            )
        )
        return erg
    s1, w = pruefe_website(z, domain)
    erg.schritte.append(s1)
    if not w.erreichbar:
        return erg
    lade_sitemap(z, w)
    daten = _sammle_schritte(erg, auftrag, alt, w, z, projects_pfad, heute, sites_pfad)
    _schritt_eintrag(erg, auftrag, alt, w, z, projekte, daten, projects_pfad, heute)
    _schritt_historie(erg, auftrag, daten, projects_pfad, z, db_pfad, heute)
    _schritt_zusammenfassung(erg)
    return erg


def _sammle_schritte(erg, auftrag, alt, w, z, projects_pfad, heute, sites_pfad):
    alt = alt or {}
    sc = alt.get("source_config") or {}
    ac = alt.get("adapter_config") or {}
    erg.schritte.append(schritt_sitemap(w, ac.get("max_pages")))
    s_erw, erwartet = schritt_erwartet(w, alt.get("erwartet"))
    erg.schritte.append(s_erw)
    gsc_wunsch = auftrag.gsc_property or (sc.get("gsc") or {}).get("property_url")
    s2, gsc_prop = pruefe_gsc(z, w.host, gsc_wunsch, heute)
    erg.schritte.append(s2)
    ga4_wunsch = auftrag.ga4_property or (sc.get("ga4") or {}).get("property_id")
    s3, ga4_prop = pruefe_ga4(z, w.host, ga4_wunsch)
    erg.schritte.append(s3)
    key = (sc.get("indexnow") or {}).get("key") or (
        indexnow_eintrag(w.host, sites_pfad) or {}
    ).get("key")
    adapter = alt.get("adapter_type") or auftrag.adapter
    root = ac.get("root_path") or auftrag.root_path
    s4, key = pruefe_indexnow(z, w, key, adapter, root)
    erg.schritte.append(s4)
    erg.schritte.append(pruefe_bing(w.host, sites_pfad))
    erg.schritte.append(pruefe_pagespeed(projects_pfad))
    return {
        "gsc": gsc_prop if s2.status != FEHLER else None,
        "gsc_ok": s2.status in (OK, WARNUNG),
        "ga4": ga4_prop,
        "key": key,
        "erwartet": erwartet,
        "adapter": adapter,
        "root": root,
    }


def _schritt_eintrag(erg, auftrag, alt, w, z, projekte, d, projects_pfad, heute):
    crontab = z.crontab()
    vorhanden = cron_vorhanden(crontab, auftrag.projekt)
    slot = naechster_slot(crontab)
    if d["adapter"] == "static" and d["root"] and not Path(d["root"]).exists():
        erg.schritte.append(
            Schritt(
                "6a",
                "Adapter",
                WARNUNG,
                f"root_path {d['root']} " "existiert auf diesem Server nicht.",
                "Pfad prüfen " "oder --adapter generic nehmen.",
                WER_WIR,
            )
        )
    eintrag = baue_eintrag(
        alt,
        domain=w.kanonisch,
        name=auftrag.name or (alt or {}).get("name") or w.host,
        konto=z.credentials_path,
        gsc_property=d["gsc"],
        ga4_property=d["ga4"],
        indexnow_key=d["key"],
        empfaenger=auftrag.bericht_an or standard_empfaenger(projekte),
        branche=branche_vorschlag(w.html),
        adapter=d["adapter"],
        root_path=d["root"],
        max_pages=max_pages_vorschlag(len(w.seiten)),
        slot=slot,
        erwartet=d["erwartet"],
    )
    erg.yaml_block = yaml_block(auftrag.projekt, eintrag)
    offen = []
    if eintrag["bericht"].get("empfaenger") == "BITTE-EINTRAGEN":
        offen.append("Berichtsempfänger fehlt (--bericht-an)")
    if not alt:
        offen.append("bericht.branche prüfen (aus der Meta-Beschreibung übernommen)")
    if auftrag.schreiben:
        erg.sicherung = schreibe_projekt(projects_pfad, auftrag.projekt, eintrag, heute)
        erg.geschrieben = projects_pfad
        text = f"in {projects_pfad} geschrieben (Sicherung: {erg.sicherung})"
    else:
        text = "Vorschlag unten — nur angezeigt (mit --schreiben eintragen)"
    erg.schritte.append(
        Schritt(
            "6",
            "Projekt-Eintrag",
            WARNUNG if offen else OK,
            text,
            "; ".join(offen),
            WER_WIR if offen else "",
        )
    )
    if vorhanden:
        erg.schritte.append(
            Schritt(
                "7", "Zeitplan (Cron)", OK, f"vorhanden, täglich " f"{vorhanden} Uhr"
            )
        )
    else:
        h = w.host
        erg.cron_zeile = cron_zeile(auftrag.projekt, h, slot)
        erg.schritte.append(
            Schritt(
                "7",
                "Zeitplan (Cron)",
                WARNUNG,
                f"fehlt — nächster " f"freier Platz {slot[0]:02d}:{slot[1]:02d} Uhr",
                "Zeile unten mit `sudo crontab -e` eintragen "
                "(wird nicht automatisch installiert).",
                WER_WIR,
            )
        )


def _schritt_historie(erg, auftrag, d, projects_pfad, z, db_pfad, heute):
    if not d["gsc_ok"]:
        erg.schritte.append(
            Schritt(
                "8",
                "Suchhistorie (16 Monate)",
                INFO,
                "übersprungen — Search Console fehlt noch.",
            )
        )
        return
    if not auftrag.schreiben:
        erg.schritte.append(
            Schritt(
                "8",
                "Suchhistorie (16 Monate)",
                INFO,
                "wird mit --schreiben importiert.",
            )
        )
        return
    import asyncio

    from .historie import importiere, standard_db_pfad

    projekt = _lade(projects_pfad).get(auftrag.projekt) or {}
    quelle = z.gsc_quelle()
    try:
        if hasattr(quelle, "authenticate"):
            asyncio.run(quelle.authenticate())
        r = asyncio.run(
            importiere(
                db_pfad or standard_db_pfad(),
                auftrag.projekt,
                projekt,
                heute=heute,
                quelle=quelle,
            )
        )
    except Exception as exc:
        erg.schritte.append(
            Schritt(
                "8",
                "Suchhistorie (16 Monate)",
                WARNUNG,
                f"Import gescheitert: {_kurz(exc)}",
                "Später mit "
                f"`historie --importieren --projekt {auftrag.projekt}`"
                " nachholen (läuft auch täglich 11:15).",
                WER_WIR,
            )
        )
        return
    status = OK if r.erfolgreich and not r.monate_fehlgeschlagen else WARNUNG
    text = (
        (
            f"{r.monate_geholt} Monat(e) geholt, {r.monate_uebersprungen} schon im "
            f"Archiv, {r.monate_fehlgeschlagen} fehlgeschlagen"
        )
        if r.erfolgreich
        else r.fehler
    )
    erg.schritte.append(
        Schritt(
            "8",
            "Suchhistorie (16 Monate)",
            status,
            text,
            (
                ""
                if status == OK
                else "Der tägliche Historien-Cron " "(11:15) holt fehlende Monate nach."
            ),
            "",
        )
    )


def _schritt_zusammenfassung(erg: Ergebnis) -> None:
    offen = erg.offene
    if not offen:
        erg.schritte.append(Schritt("9", "Zusammenfassung", OK, "Paket vollständig."))
        return
    fehler = sum(1 for s in offen if s.status == FEHLER)
    status = FEHLER if fehler else WARNUNG
    erg.schritte.append(
        Schritt(
            "9",
            "Zusammenfassung",
            status,
            f"{len(offen)} offene "
            f"Punkte ({fehler} blockierend): " + ", ".join(s.titel for s in offen),
        )
    )


def als_text(erg: Ergebnis) -> str:
    zeilen = [f'Einrichtung „große Packung" — Projekt {erg.projekt}', ""]
    zeilen += [s.als_text() for s in erg.schritte]
    if erg.yaml_block:
        zeilen += [
            "",
            "--- projects.yaml (Eintrag unter projects:) ---",
            erg.yaml_block,
        ]
    if erg.cron_zeile:
        zeilen += [
            "--- Cron-Zeile (nur ausgeben, von Hand eintragen) ---",
            erg.cron_zeile,
        ]
    return "\n".join(zeilen)


# --------------------------------------------------------------------------
# „einrichten --pruefen" — Paket-Prüfung aller bestehenden Projekte
# --------------------------------------------------------------------------

SPALTEN = [
    ("1", "Domain"),
    ("1b", "Sitemap"),
    ("1c", "erwartet"),
    ("2", "GSC"),
    ("3", "GA4"),
    ("B", "Bericht"),
    ("7", "Cron"),
    ("4", "IndexNow"),
    ("4b", "Bing"),
    ("5", "PageSpeed"),
]


@dataclass
class Paketstatus:
    projekt: str
    domain: str
    abgeschaltet: Optional[str] = None
    klein: bool = False
    schritte: List[Schritt] = field(default_factory=list)

    @property
    def todos(self) -> List[Schritt]:
        return [s for s in self.schritte if s.status in (FEHLER, WARNUNG)]

    def symbol(self, nr: str) -> str:
        s = next((x for x in self.schritte if x.nr == nr), None)
        return SYMBOL[s.status] if s else "–"


def schritt_bericht(cfg: Dict[str, Any], klein: bool) -> Schritt:
    b = cfg.get("bericht") or {}
    if b.get("aktiv") and b.get("empfaenger"):
        return Schritt("B", "Wochenbericht", OK, f"aktiv an {b['empfaenger']}")
    if b.get("extern_aktiv") and b.get("extern_timer"):
        empfaenger = ", ".join(b.get("extern_empfaenger") or [])
        zusatz = f" an {empfaenger}" if empfaenger else ""
        return Schritt(
            "B",
            "Wochenbericht",
            OK,
            f"extern über {b['extern_timer']}{zusatz}",
        )
    if klein:
        return Schritt("B", "Wochenbericht", INFO, "bewusst aus (paket: klein)")
    return Schritt(
        "B",
        "Wochenbericht",
        FEHLER,
        "nicht eingerichtet",
        "Abschnitt `bericht: {aktiv: true, empfaenger: …, branche: …}` in "
        "projects.yaml ergänzen (Vorschlag: `einrichten --projekt "
        f"{cfg.get('_id', '<id>')}`).",
        WER_WIR,
    )


def pruefe_projekt(
    pid: str,
    cfg: Dict[str, Any],
    z: Zugriffe,
    crontab: str,
    projects_pfad: Optional[str],
    heute: Optional[date] = None,
    sites_pfad: str = INDEXNOW_SITES,
) -> Paketstatus:
    ps = Paketstatus(pid, str(cfg.get("domain", "")))
    if cfg.get("enabled") is False:
        ps.abgeschaltet = f"abgeschaltet — {cfg.get('name') or pid}"
        return ps
    ps.klein = str(cfg.get("paket", "")).lower() == "klein"
    sc = cfg.get("source_config") or {}
    s1, w = pruefe_website(z, ps.domain)
    if w.kanonisch and w.kanonisch != w.eingabe:
        s1.anweisung = s1.anweisung.replace(
            f"Als Domain {w.kanonisch} eintragen (im YAML-Vorschlag schon so).",
            f"In projects.yaml `domain: {w.kanonisch}` eintragen.",
        )
    ps.schritte.append(s1)
    if w.erreichbar:
        lade_sitemap(z, w)
        ps.schritte.append(
            schritt_sitemap(w, (cfg.get("adapter_config") or {}).get("max_pages"))
        )
        ps.schritte.append(schritt_erwartet(w, cfg.get("erwartet"))[0])
    ps.schritte.append(_pruefe_quelle_gsc(z, w, cfg, sc, heute))
    ps.schritte.append(_pruefe_quelle_ga4(z, w, cfg, sc, ps.klein))
    ps.schritte.append(schritt_bericht(dict(cfg, _id=pid), ps.klein))
    zeit = cron_vorhanden(crontab, pid)
    ps.schritte.append(
        Schritt("7", "Zeitplan (Cron)", OK, f"täglich {zeit} Uhr")
        if zeit
        else Schritt(
            "7",
            "Zeitplan (Cron)",
            FEHLER,
            "kein Audit-Cron",
            "Cron-Zeile aus `einrichten --projekt " + pid + "` eintragen.",
            WER_WIR,
        )
    )
    key = (sc.get("indexnow") or {}).get("key")
    fremd = (indexnow_eintrag(w.host, sites_pfad) or {}).get("key")
    s4, _ = (
        pruefe_indexnow(z, w, key or fremd, zeige_schluessel=False)
        if (key or fremd)
        else (
            Schritt(
                "4",
                "IndexNow (Bing/Copilot)",
                WARNUNG,
                "kein Schlüssel",
                f"`einrichten --projekt {pid}` erzeugt einen; Datei hochladen.",
                WER_WIR,
            ),
            None,
        )
    )
    if fremd and not key:
        s4.status = schlechtester([s4.status, WARNUNG])
        s4.text += (
            " Schlüssel steht nur in indexnow_sites.json, nicht in projects.yaml."
        )
        s4.anweisung = (
            "Schlüssel aus <INDEXNOW_SITES aus der .env> als "
            "source_config.indexnow.key übernehmen. " + s4.anweisung
        ).strip()
        s4.wer = s4.wer or WER_WIR
    ps.schritte.append(s4)
    ps.schritte.append(pruefe_bing(w.host, sites_pfad))
    ps.schritte.append(pruefe_pagespeed(projects_pfad))
    return ps


def _pruefe_quelle_gsc(z, w, cfg, sc, heute) -> Schritt:
    quellen = cfg.get("enabled_sources") or []
    prop = (sc.get("gsc") or {}).get("property_url")
    if "gsc" not in quellen or not prop:
        s, gefunden = pruefe_gsc(z, w.host, None, heute)
        if gefunden:
            s.status, s.wer = FEHLER, WER_WIR
            s.text = (
                f"nicht eingetragen — Property {gefunden} ist aber lesbar. " + s.text
            )
            s.anweisung = f"source_config.gsc.property_url: {gefunden} + 'gsc' in enabled_sources."
        return s
    return pruefe_gsc(z, w.host, prop, heute)[0]


def _pruefe_quelle_ga4(z, w, cfg, sc, klein) -> Schritt:
    quellen = cfg.get("enabled_sources") or []
    pid = (sc.get("ga4") or {}).get("property_id")
    if "ga4" in quellen and pid:
        return pruefe_ga4(z, w.host, pid, mit_hinweis=False)[0]
    if klein:
        return Schritt("3", "Google Analytics 4", INFO, "bewusst aus (paket: klein)")
    s, gefunden = pruefe_ga4(z, w.host, None, mit_hinweis=False)
    if gefunden:
        s.status, s.wer = FEHLER, WER_WIR
        s.text = f"nicht eingetragen — Property {gefunden} ist aber lesbar. " + s.text
        s.anweisung = (
            f"source_config.ga4: {{property_id: '{gefunden}', credentials_path: "
            f"{z.credentials_path}}} + 'ga4' in enabled_sources."
        )
    return s


def pruefe_alle(
    projects_pfad: str,
    z: Optional[Zugriffe] = None,
    nur: Optional[str] = None,
    heute: Optional[date] = None,
    crontab: Optional[str] = None,
    sites_pfad: str = INDEXNOW_SITES,
) -> List[Paketstatus]:
    projekte = _lade(projects_pfad)
    z = z or Zugriffe(standard_konto(projekte))
    crontab = z.crontab() if crontab is None else crontab
    ergebnis = []
    for pid, cfg in projekte.items():
        if nur and pid != nur:
            continue
        ergebnis.append(
            pruefe_projekt(pid, cfg or {}, z, crontab, projects_pfad, heute, sites_pfad)
        )
    return ergebnis


def pruef_tabelle(stati: List[Paketstatus]) -> str:
    kopf = ["Projekt"] + [t for _, t in SPALTEN]
    zeilen: List[List[str]] = []
    for ps in stati:
        name = ps.projekt + (" (klein)" if ps.klein else "")
        if ps.abgeschaltet:
            zeilen.append([name, ps.abgeschaltet])
        else:
            zeilen.append([name] + [ps.symbol(nr) for nr, _ in SPALTEN])
    breite = max([len(kopf[0])] + [len(z[0]) for z in zeilen])
    aus = [kopf[0].ljust(breite) + " | " + " | ".join(kopf[1:])]
    for z in zeilen:
        if len(z) == 2:
            aus.append(z[0].ljust(breite) + " | " + z[1])
            continue
        # Jedes Symbol ist im Terminal zwei Zeichen breit.
        zellen = [c + " " * max(0, len(k) - 2) for c, k in zip(z[1:], kopf[1:])]
        aus.append(z[0].ljust(breite) + " | " + " | ".join(zellen))
    return "\n".join(aus)


def pruef_text(stati: List[Paketstatus]) -> str:
    teile = [
        'Paket-Prüfung „große Packung" — alle Projekte',
        "",
        pruef_tabelle(stati),
        "",
    ]
    for ps in stati:
        if ps.abgeschaltet:
            continue
        if not ps.todos:
            teile.append(f"{ps.projekt}: Paket vollständig ✅")
            continue
        teile.append(f"{ps.projekt} ({ps.domain}) — {len(ps.todos)} To-do(s):")
        for s in ps.todos:
            wer = f"[{s.wer}] " if s.wer else ""
            teile.append(f"  {SYMBOL[s.status]} {s.titel}: {s.text}")
            if s.anweisung:
                teile.append(f"      → {wer}{s.anweisung}")
        teile.append("")
    return "\n".join(teile)
