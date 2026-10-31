"""
KI-Sichtbarkeit: wie oft KI-Crawler und KI-Assistenten unsere Seiten lesen,
wie viele Menschen ueber KI-Antworten kommen, und was es bei den Crawlern Neues gibt.

Drei Teile, ein Wochenlauf:

1. **Bot-Abrufe** aus den Webserver-Protokollen (Befehl je Projekt in projects.yaml,
   z.B. ``ssh server tail ...`` oder ``docker logs``). Jeder Abruf wird einem Bot
   zugeordnet (Training, KI-Suche, Abruf im Auftrag eines Menschen, klassische Suche)
   und geprueft: echte Bots kommen aus den veroeffentlichten IP-Listen der Anbieter.
   Scanner, die sich nur als GPTBot ausgeben und nach ``.env`` suchen, zaehlen nicht.
2. **KI-Besucher** aus Umami: Besuche, die von chatgpt.com, perplexity.ai, claude.ai,
   gemini.google.com, copilot usw. kommen.
3. **Radar**: offizielle Crawler-Seiten der Anbieter auf neue Bot-Namen pruefen,
   unbekannte Bots in den eigenen Protokollen finden, robots.txt jedes Projekts darauf
   pruefen, dass die KI-Suche nicht versehentlich gesperrt ist.

Konfiguration je Projekt (projects.yaml)::

    ki_sichtbarkeit:
      umami_id: "<uuid>"            # optional
      logs:
        - befehl: "ssh server tail -n 300000 /var/log/nginx/access.log"
          format: combined            # oder visitor (ip|zeit|host|pfad|status|referer|ua)
          hosts: [example.com]        # nur bei visitor: welche Hosts zaehlen

Aufruf: ``python -m seo_autopilot.ki_sichtbarkeit`` (Cron montags)
        ``python -m seo_autopilot.ki_sichtbarkeit --trocken`` (nur anzeigen)
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import re
import shlex
import socket
import sqlite3
import subprocess
import urllib.robotparser
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

TRAINING, SUCHE, NUTZER, KLASSISCH = "training", "suche", "nutzer", "klassisch"
ECHT, UNBESTAETIGT, FALSCH = "echt", "unbestaetigt", "falsch"
TAGE = 7
BEFEHL_TIMEOUT = 180

# Reihenfolge zaehlt: der erste Treffer gewinnt (Claude-SearchBot vor ClaudeBot).
BOTS: List[Tuple[str, str, str]] = [
    ("OAI-SearchBot", "OpenAI", SUCHE),
    ("ChatGPT-User", "OpenAI", NUTZER),
    ("GPTBot", "OpenAI", TRAINING),
    ("OAI-AdsBot", "OpenAI", KLASSISCH),  # prueft Zielseiten von ChatGPT-Anzeigen
    ("Claude-SearchBot", "Anthropic", SUCHE),
    ("Claude-User", "Anthropic", NUTZER),
    ("Claude-Web", "Anthropic", NUTZER),
    ("ClaudeBot", "Anthropic", TRAINING),
    ("anthropic-ai", "Anthropic", TRAINING),
    ("Perplexity-User", "Perplexity", NUTZER),
    ("PerplexityBot", "Perplexity", SUCHE),
    ("Google-CloudVertexBot", "Google", TRAINING),
    ("GoogleOther", "Google", TRAINING),
    ("Applebot", "Apple", SUCHE),
    ("DuckAssistBot", "DuckDuckGo", SUCHE),
    ("meta-externalfetcher", "Meta", NUTZER),
    ("meta-externalagent", "Meta", TRAINING),
    ("Amazonbot", "Amazon", SUCHE),
    ("MistralAI-User", "Mistral", NUTZER),
    ("cohere-ai", "Cohere", TRAINING),
    ("YouBot", "You.com", SUCHE),
    ("Bytespider", "ByteDance", TRAINING),
    ("CCBot", "Common Crawl", TRAINING),
    ("Diffbot", "Diffbot", TRAINING),
    ("PetalBot", "Huawei", KLASSISCH),  # Petal Search, keine KI-Antwortmaschine
    ("bingbot", "Microsoft", KLASSISCH),
    ("Googlebot", "Google", KLASSISCH),
]

# Veroeffentlichte IP-Listen je Anbieter. Fehlt ein Anbieter hier, gilt ein Abruf
# mit plausiblem Pfad als "unbestaetigt" (nicht als falsch).
IP_LISTEN: Dict[str, List[str]] = {
    "OpenAI": [
        "https://openai.com/gptbot.json",
        "https://openai.com/searchbot.json",
        "https://openai.com/chatgpt-user.json",
    ],
    "Perplexity": [
        "https://www.perplexity.ai/perplexitybot.json",
        "https://www.perplexity.ai/perplexity-user.json",
    ],
    "Google": [
        "https://developers.google.com/static/search/apis/ipranges/googlebot.json",
        "https://developers.google.com/static/search/apis/ipranges/special-crawlers.json",
        "https://developers.google.com/static/search/apis/ipranges/user-triggered-fetchers.json",
        "https://developers.google.com/static/search/apis/ipranges/user-triggered-fetchers-google.json",
    ],
    "Microsoft": ["https://www.bing.com/toolbox/bingbot.json"],
}
# Bots, fuer die der Anbieter (noch) keine eigene IP-Liste veroeffentlicht: nie "falsch"
# nur wegen fremder Netze desselben Anbieters.
OHNE_IP_LISTE = {"OAI-AdsBot"}
# Anbieter ohne IP-Liste, aber mit Rueckwaerts-DNS
RDNS: Dict[str, Tuple[str, ...]] = {
    "Apple": (".applebot.apple.com",),
    "Amazon": (".crawl.amazonbot.amazon",),
}

# Pfade, die nur Scanner abfragen (Zugangsdaten, Quellcode, Server-Interna)
SCANNER = re.compile(
    r"(\.env\b|/\.git/|wp-config|credentials|secrets?\.|/\.aws|/\.vscode|/\.vercel"
    r"|phpinfo|\.terraform|swagger\.json|token\.json|serviceaccount|/\.ssh|\.sql\b"
    r"|/etc/passwd|netlify\.toml|debug-trigger)",
    re.I,
)
TECHNIK = re.compile(
    r"(\.(css|js|png|jpe?g|webp|avif|svg|ico|woff2?|mp4|map|xml|json)(\?|$)"
    r"|^/robots\.txt|^/favicon|^/\.well-known/)",
    re.I,
)

KI_REFERRER = [
    "chatgpt.com",
    "chat.openai.com",
    "perplexity.ai",
    "claude.ai",
    "gemini.google.com",
    "copilot.microsoft.com",
    "you.com",
    "chat.mistral.ai",
    "chat.deepseek.com",
    "meta.ai",
]

# Offizielle Crawler-Seiten der Anbieter fuer das Radar
QUELLEN: Dict[str, str] = {
    "OpenAI": "https://platform.openai.com/docs/bots",
    "Anthropic": "https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler",
    "Google": "https://developers.google.com/crawling/docs/crawlers-fetchers/overview-google-crawlers",
    "Google (KI)": "https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers",
    "Perplexity": "https://docs.perplexity.ai/guides/bots",
    "Apple": "https://support.apple.com/en-us/119829",
    "Meta": "https://developers.facebook.com/docs/sharing/webmasters/web-crawlers",
    "Common Crawl": "https://commoncrawl.org/ccbot",
}
BOT_NAME = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9]*|[a-z]+)(?:-[A-Za-z0-9]+)*"
    r"(?:-?[Bb]ot|-?[Aa]gent|-User|-?[Cc]rawler|-?[Ss]pider|-?[Ff]etcher|-Extended)\b"
)
# Bots, die keine KI sind und im Radar nicht als "unbekannt" auftauchen sollen
BEKANNT_SONST = {
    "ahrefsbot",
    "semrushbot",
    "mj12bot",
    "dotbot",
    "yandexbot",
    "baiduspider",
    "twitterbot",
    "linkedinbot",
    "slackbot",
    "telegrambot",
    "whatsapp",
    "discordbot",
    "duckduckbot",
    "seznambot",
    "applebot-extended",
    "google-extended",
    "adsbot",
    "mediapartners",
    "googlebot-image",
    "storebot",
    "pinterestbot",
    "bingpreview",
    "facebookbot",
    "petalbot",
    "yandexrenderresourcesbot",
    "dataforseobot",
    "barkrowler",
    "serpstatbot",
    "blexbot",
    "awariobot",
    "amazonbot",
    "coccocbot",
    "seoautopilotbot",  # unser eigener Pruefer
    "searchbot",
    "sitelockspider",
    "seojuice-searchbot",
}

KEINE_NAMEN = {
    "robot",
    "chatbot",
    "useragent",
    "user-agent",
    "webcrawler",
    "web-crawler",
}

LOG_KOMBINIERT = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<zeit>[^\]]+)\] "(?:\S+) (?P<pfad>\S+)[^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ki_wochen (
    project_id TEXT, woche TEXT, daten TEXT, PRIMARY KEY (project_id, woche));
CREATE TABLE IF NOT EXISTS ki_radar_namen (
    quelle TEXT, name TEXT, erstmals TEXT, PRIMARY KEY (quelle, name));
CREATE TABLE IF NOT EXISTS ki_laeufe (am TEXT, art TEXT);
"""


def db_oeffnen(pfad: str) -> sqlite3.Connection:
    con = sqlite3.connect(pfad)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


# ---------------------------------------------------------------- Protokolle


def bot_von(ua: str) -> Optional[Tuple[str, str, str]]:
    klein = (ua or "").lower()
    for name, anbieter, art in BOTS:
        if name.lower() in klein:
            return name, anbieter, art
    return None


def _zeit(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text.strip(), "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None


def zeile_lesen(zeile: str, fmt: str) -> Optional[Dict[str, Any]]:
    """Eine Protokollzeile in ein Woerterbuch; None, wenn sie nicht passt."""
    if fmt == "visitor":
        teile = zeile.rstrip("\n").split("|", 6)
        if len(teile) < 7:
            return None
        ip, zeit, host, pfad, status, referer, ua = teile
    else:
        m = LOG_KOMBINIERT.match(zeile)
        if not m:
            return None
        ip, zeit, pfad, status = m["ip"], m["zeit"], m["pfad"], m["status"]
        host, referer, ua = "", m["referer"], m["ua"]
    t = _zeit(zeit)
    if t is None:
        return None
    return {
        "ip": ip,
        "zeit": t,
        "host": host.lower(),
        "pfad": pfad,
        "status": status,
        "referer": referer,
        "ua": ua,
    }


def befehl_ausfuehren(befehl: str) -> Iterable[str]:
    """Fuehrt den Protokoll-Befehl aus projects.yaml aus (ohne Shell)."""
    erg = subprocess.run(
        shlex.split(befehl),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=BEFEHL_TIMEOUT,
        check=False,
    )
    if erg.returncode != 0 and not erg.stdout:
        raise RuntimeError(
            f"Befehl fehlgeschlagen ({erg.returncode}): {erg.stderr[:200]}"
        )
    return erg.stdout.splitlines()


def abrufe_sammeln(
    zeilen: Iterable[str], fmt: str, hosts: Sequence[str], ab: datetime
) -> List[Dict[str, Any]]:
    """Alle Bot-Abrufe seit ``ab``; bei visitor nur fuer die genannten Hosts."""
    erlaubt = {h.lower() for h in hosts}
    raus = []
    for zeile in zeilen:
        z = zeile_lesen(zeile, fmt)
        if not z or z["zeit"] < ab:
            continue
        if erlaubt and z["host"] and z["host"] not in erlaubt:
            continue
        bot = bot_von(z["ua"])
        if bot:
            z["bot"], z["anbieter"], z["art"] = bot
            raus.append(z)
    return raus


# ---------------------------------------------------------------- Echtheit


def netze_aus(daten: Any) -> List[Any]:
    """Alle IP-Netze aus einer Anbieter-Liste, egal wie sie verschachtelt ist."""
    netze, stapel = [], [daten]
    while stapel:
        x = stapel.pop()
        if isinstance(x, dict):
            stapel.extend(x.values())
        elif isinstance(x, list):
            stapel.extend(x)
        elif isinstance(x, str) and "/" in x:
            try:
                netze.append(ipaddress.ip_network(x, strict=False))
            except ValueError:
                pass
    return netze


def ip_listen_laden(
    holen: Callable[[str], str],
) -> Tuple[Dict[str, List[Any]], List[str]]:
    """IP-Netze je Anbieter und die Liste der Adressen, die nicht ladbar waren."""
    netze: Dict[str, List[Any]] = defaultdict(list)
    kaputt = []
    for anbieter, urls in IP_LISTEN.items():
        for url in urls:
            try:
                gefunden = netze_aus(json.loads(holen(url)))
            except Exception:
                gefunden = []
            if not gefunden:
                kaputt.append(url)
            netze[anbieter].extend(gefunden)
    return dict(netze), kaputt


def _rdns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0].lower()
    except OSError:
        return ""


def echtheit(
    abruf: Dict[str, Any],
    netze: Dict[str, List[Any]],
    rdns: Callable[[str], str] = _rdns,
) -> str:
    if SCANNER.search(abruf["pfad"]):
        return FALSCH
    anbieter = abruf["anbieter"]
    try:
        ip = ipaddress.ip_address(abruf["ip"])
    except ValueError:
        return FALSCH
    if abruf.get("bot") in OHNE_IP_LISTE:
        return UNBESTAETIGT
    if netze.get(anbieter):
        return ECHT if any(ip in n for n in netze[anbieter]) else FALSCH
    if anbieter in RDNS:
        return ECHT if rdns(abruf["ip"]).endswith(RDNS[anbieter]) else FALSCH
    return UNBESTAETIGT


# ---------------------------------------------------------------- Auswertung


def auswerten(abrufe: List[Dict[str, Any]], netze, rdns=_rdns) -> Dict[str, Any]:
    """Zahlen fuer eine Woche: je Bot echt/unbestaetigt/falsch, je Art, gelesene Seiten."""
    rdns_cache: Dict[str, str] = {}

    def rdns_gemerkt(ip: str) -> str:
        if ip not in rdns_cache:
            rdns_cache[ip] = rdns(ip)
        return rdns_cache[ip]

    je_bot: Dict[str, Counter] = defaultdict(Counter)
    je_art, seiten = Counter(), Counter()
    for a in abrufe:
        e = echtheit(a, netze, rdns_gemerkt)
        je_bot[a["bot"]][e] += 1
        if e == FALSCH:
            continue
        je_art[a["art"]] += 1
        if a["art"] != KLASSISCH and not TECHNIK.search(a["pfad"]):
            seiten[a["pfad"].split("?")[0]] += 1
    return {
        "bots": {b: dict(c) for b, c in je_bot.items()},
        "arten": dict(je_art),
        "seiten": seiten.most_common(5),
        "falsch": sum(c[FALSCH] for c in je_bot.values()),
    }


def unbekannte_bots(zeilen: Iterable[str], fmt: str, mindestens: int = 20) -> Counter:
    """Bot-Namen in den Protokollen, die weder in BOTS noch in BEKANNT_SONST stehen."""
    eigene = {n.lower() for n, _, _ in BOTS}
    zaehler: Counter = Counter()
    for zeile in zeilen:
        z = zeile_lesen(zeile, fmt)
        if not z:
            continue
        for name in set(BOT_NAME.findall(z["ua"])):
            k = name.lower()
            if k in eigene or k in BEKANNT_SONST or k in KEINE_NAMEN:
                continue
            if len(k) > 4:
                zaehler[name] += 1
    return Counter({n: c for n, c in zaehler.items() if c >= mindestens})


UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def umami_sql(umami_id: str, tage: int = TAGE) -> str:
    """SQL fuer KI-Besucher. Die ID wird streng geprueft, weil psql -c keine Parameter kennt."""
    if not UUID.match(umami_id):
        raise ValueError("umami_id ungueltig")
    bedingung = " OR ".join(
        f"referrer_domain = '{d}' OR referrer_domain LIKE '%.{d}'" for d in KI_REFERRER
    )
    return (
        "SELECT referrer_domain, count(DISTINCT session_id) FROM website_event"
        f" WHERE website_id='{umami_id}' AND created_at > now() - interval '{int(tage)} days'"
        f" AND ({bedingung}) GROUP BY 1 ORDER BY 2 DESC"
    )


def umami_besucher(umami_id: str, befehl: Sequence[str]) -> Dict[str, int]:
    erg = subprocess.run(
        [*befehl, "-At", "-F", "|", "-c", umami_sql(umami_id)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    raus = {}
    for zeile in erg.stdout.splitlines():
        if "|" in zeile:
            d, n = zeile.rsplit("|", 1)
            raus[d] = int(n)
    return raus


def robots_pruefen(robots_txt: str, basis: str) -> List[str]:
    """KI-Such- und Nutzer-Bots, die per robots.txt von der Startseite ausgesperrt sind."""
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_txt.splitlines())
    return [
        name
        for name, _, art in BOTS
        if art in (SUCHE, NUTZER) and not rp.can_fetch(name, basis.rstrip("/") + "/")
    ]


# ---------------------------------------------------------------- Radar


def namen_auf_seite(text: str) -> Dict[str, str]:
    """Bot-Namen einer Seite, klein geschrieben -> erste Schreibweise im Text."""
    namen: Dict[str, str] = {}
    for n in BOT_NAME.findall(text):
        # klein geschriebenes "x-agent"/"x-user" sind Adress-Teile der Doku, keine Bots
        if n.islower() and n.endswith(("-agent", "-user")):
            continue
        if n.lower() not in KEINE_NAMEN:
            namen.setdefault(n.lower(), n)
    return namen


def radar_quellen(
    con, holen: Callable[[str], str], speichern: bool
) -> Tuple[List[str], List[str]]:
    """Neue Bot-Namen auf den offiziellen Anbieterseiten. Erster Lauf = Grundstand."""
    neu, kaputt = [], []
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for quelle, url in QUELLEN.items():
        try:
            namen = namen_auf_seite(holen(url))
        except Exception:
            namen = {}
        if not namen:
            kaputt.append(quelle)
            continue
        alt = {
            r["name"]
            for r in con.execute(
                "SELECT name FROM ki_radar_namen WHERE quelle=?", (quelle,)
            )
        }
        if alt:
            neu += [f"{quelle}: {namen[k]}" for k in sorted(set(namen) - alt)]
        if speichern:
            con.executemany(
                "INSERT OR IGNORE INTO ki_radar_namen VALUES (?,?,?)",
                [(quelle, k, jetzt) for k in namen],
            )
    con.commit()
    return neu, kaputt


def radar_logs_neu(con, unbekannt: Counter, speichern: bool) -> List[str]:
    """Unbekannte Bots aus den Protokollen, jeder nur beim ersten Mal."""
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    neu = []
    for name, anzahl in unbekannt.most_common(8):
        gesehen = con.execute(
            "SELECT 1 FROM ki_radar_namen WHERE quelle='protokoll' AND name=?",
            (name.lower(),),
        ).fetchone()
        if gesehen:
            continue
        neu.append(f"{name} ({anzahl} Abrufe)")
        if speichern:
            con.execute(
                "INSERT OR IGNORE INTO ki_radar_namen VALUES ('protokoll',?,?)",
                (name.lower(), jetzt),
            )
    con.commit()
    return neu


# ---------------------------------------------------------------- Bericht

ANBIETER_KURZ = {"OpenAI": "ChatGPT", "Anthropic": "Claude"}


def _je_anbieter(bots: Dict[str, Dict[str, int]], art: str) -> str:
    zaehler: Counter = Counter()
    for name, anbieter, a in BOTS:
        if a == art and name in bots:
            zaehler[ANBIETER_KURZ.get(anbieter, anbieter)] += sum(
                v for k, v in bots[name].items() if k != FALSCH
            )
    return ", ".join(f"{a} {n}" for a, n in zaehler.most_common(4) if n)


def _veraenderung(jetzt: int, vorher: Optional[int]) -> str:
    if not vorher:
        return ""
    p = round((jetzt - vorher) / vorher * 100)
    return f" ({'▲' if p >= 0 else '▼'} {abs(p)} %)" if abs(p) >= 5 else ""


def projekt_block(
    name: str, w: Dict[str, Any], vorher: Optional[Dict[str, Any]]
) -> str:
    z = [name]
    if w.get("auswertung"):
        a, va = w["auswertung"], (vorher or {}).get("auswertung") or {}
        arten, varten = a["arten"], va.get("arten", {})
        n = arten.get(SUCHE, 0)
        z.append(
            f"· KI-Suche liest: {n}{_veraenderung(n, varten.get(SUCHE))}"
            + (f" – {_je_anbieter(a['bots'], SUCHE)}" if n else "")
        )
        n = arten.get(NUTZER, 0)
        z.append(
            f"· Menschen fragen KI: {n}{_veraenderung(n, varten.get(NUTZER))}"
            + (f" – {_je_anbieter(a['bots'], NUTZER)}" if n else "")
        )
        z.append(
            f"· Training {arten.get(TRAINING, 0)} · Google/Bing {arten.get(KLASSISCH, 0)}"
        )
        if a["seiten"]:
            z.append("· Meistgelesen: " + ", ".join(p for p, _ in a["seiten"][:3]))
    elif not w.get("fehler"):
        z.append("· Bot-Abrufe: kein Protokoll angebunden")
    if "besucher" in w:
        b = w["besucher"]
        z.append(
            f"· Besucher aus KI: {sum(b.values())}"
            + (" – " + ", ".join(f"{d} {n}" for d, n in b.items()) if b else "")
        )
    if w.get("gesperrt"):
        z.append("⚠️ robots.txt sperrt: " + ", ".join(w["gesperrt"]))
    if w.get("fehler"):
        z.append("⚠️ " + w["fehler"])
    return "\n".join(z)


def bericht(
    bloecke: List[str], radar: Dict[str, List[str]], falsch: int, bis: datetime
) -> List[str]:
    """Telegram-Nachrichten (je hoechstens ~3800 Zeichen)."""
    kopf = f"🤖 KI-Sichtbarkeit · 7 Tage bis {bis:%d.%m.%Y}"
    fuss = []
    if radar.get("quellen"):
        fuss.append("🆕 Neu bei den Anbietern: " + "; ".join(radar["quellen"]))
    if radar.get("protokoll"):
        fuss.append("🆕 Neu in unseren Protokollen: " + "; ".join(radar["protokoll"]))
    if radar.get("ip_kaputt"):
        fuss.append("⚠️ IP-Liste nicht ladbar: " + "; ".join(radar["ip_kaputt"]))
    if radar.get("quelle_kaputt"):
        fuss.append(
            "⚠️ Anbieterseite nicht lesbar: " + ", ".join(radar["quelle_kaputt"])
        )
    if not (radar.get("quellen") or radar.get("protokoll")):
        fuss.append("Radar: keine neuen KI-Crawler.")
    fuss.append(f"Aussortiert (Scanner mit falschem Bot-Namen): {falsch}")
    nachrichten, aktuell = [], kopf
    for teil in bloecke + ["\n".join(fuss)]:
        if len(aktuell) + len(teil) + 2 > 3800:
            nachrichten.append(aktuell)
            aktuell = teil
        else:
            aktuell += "\n\n" + teil
    nachrichten.append(aktuell)
    return nachrichten


# ---------------------------------------------------------------- Ablauf


def _holer() -> Callable[[str], str]:
    client = httpx.Client(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (seo-autopilot ki-radar)"},
    )

    def holen(url: str) -> str:
        r = client.get(url)
        r.raise_for_status()
        return r.text

    return holen


def projekt_lauf(
    pid: str,
    p: Dict[str, Any],
    ab: datetime,
    netze,
    holen,
    umami_befehl: Optional[Sequence[str]],
) -> Tuple[Dict[str, Any], Counter]:
    """Woche eines Projekts: Bot-Abrufe, KI-Besucher, robots.txt."""
    k = p.get("ki_sichtbarkeit") or {}
    w: Dict[str, Any] = {}
    abrufe, unbekannt = [], Counter()
    try:
        for quelle in k.get("logs") or []:
            zeilen = list(befehl_ausfuehren(quelle["befehl"]))
            fmt = quelle.get("format", "combined")
            abrufe += abrufe_sammeln(zeilen, fmt, quelle.get("hosts") or [], ab)
            unbekannt += unbekannte_bots(zeilen, fmt)
        if k.get("logs"):
            w["auswertung"] = auswerten(abrufe, netze)
    except Exception as exc:
        logger.warning(f"[KI-Sichtbarkeit] {pid}: Protokoll nicht lesbar: {exc}")
        w["fehler"] = "Protokoll nicht lesbar"
    if k.get("umami_id") and umami_befehl:
        try:
            w["besucher"] = umami_besucher(k["umami_id"], umami_befehl)
        except Exception as exc:
            logger.warning(f"[KI-Sichtbarkeit] {pid}: Umami nicht lesbar: {exc}")
    try:
        w["gesperrt"] = robots_pruefen(
            holen(p["domain"].rstrip("/") + "/robots.txt"), p["domain"]
        )
    except Exception:
        w["gesperrt"] = []
    return w, unbekannt


def vorwoche(con, pid: str, woche: str) -> Optional[Dict[str, Any]]:
    r = con.execute(
        "SELECT daten FROM ki_wochen WHERE project_id=? AND woche<? ORDER BY woche DESC LIMIT 1",
        (pid, woche),
    ).fetchone()
    return json.loads(r["daten"]) if r else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    import os

    from .historie import standard_db_pfad
    from .kundenbericht import WURZEL, lade_projekte

    ap = argparse.ArgumentParser(prog="python -m seo_autopilot.ki_sichtbarkeit")
    ap.add_argument("--projekt", help="nur dieses Projekt")
    ap.add_argument("--projects", default=str(WURZEL / "projects.yaml"))
    ap.add_argument("--db", default=None)
    ap.add_argument(
        "--trocken", action="store_true", help="nichts speichern, nichts senden"
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    projekte = {
        k: v
        for k, v in lade_projekte(args.projects).items()
        if v.get("ki_sichtbarkeit") and (not args.projekt or k == args.projekt)
    }
    con = db_oeffnen(":memory:" if args.trocken else (args.db or standard_db_pfad()))
    holen, jetzt = _holer(), datetime.now(timezone.utc)
    umami = shlex.split(os.environ.get("KI_UMAMI_BEFEHL", "")) or None
    netze, ip_kaputt = ip_listen_laden(holen)
    woche, bloecke, unbekannt, falsch = jetzt.strftime("%G-W%V"), [], Counter(), 0
    for pid, p in projekte.items():
        w, u = projekt_lauf(pid, p, jetzt - timedelta(days=TAGE), netze, holen, umami)
        unbekannt += u
        falsch += (w.get("auswertung") or {}).get("falsch", 0)
        name = urlparse(p.get("domain", pid)).hostname or pid
        bloecke.append(projekt_block(name, w, vorwoche(con, pid, woche)))
        if not args.trocken:
            con.execute(
                "INSERT OR REPLACE INTO ki_wochen VALUES (?,?,?)",
                (pid, woche, json.dumps(w)),
            )
    quellen_neu, quelle_kaputt = radar_quellen(con, holen, not args.trocken)
    radar = {
        "quellen": quellen_neu,
        "quelle_kaputt": quelle_kaputt,
        "ip_kaputt": ip_kaputt,
        "protokoll": radar_logs_neu(con, unbekannt, not args.trocken),
    }
    return _senden(con, bericht(bloecke, radar, falsch, jetzt), args.trocken)


def _senden(con, nachrichten: List[str], trocken: bool) -> int:
    from .notifications.telegram import send_plain_message

    for text in nachrichten:
        if trocken:
            print("--- wuerde senden ---\n" + text)
        elif not send_plain_message(text):
            logger.warning("[KI-Sichtbarkeit] Telegram-Versand fehlgeschlagen")
    if not trocken:
        con.execute(
            "INSERT INTO ki_laeufe VALUES (?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), "voll"),
        )
        con.commit()
    print(f"[KI-Sichtbarkeit] {len(nachrichten)} Nachricht(en)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
