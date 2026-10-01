"""
Lernschleife — einmal die Woche: Was ist neu bei SEO und KI-Suche, und was muss
der Autopilot deshalb anders pruefen?

Robert (01.10.2026): "einmal die Woche dir Informationen holen, was gibt es Neues
auf SEO und GEO, und das bitte in diesen Autopiloten einfliessen lassen."

Ablauf (nur lesen, bewerten, vorschlagen - hier wird NIE Code geaendert):

1. **Kandidaten** - die Meldungen des Marktbeobachters (`marktradar.py`) der
   letzten 7 Tage. Das Richtlinien-Radar (`policy_radar.py`) ordnet jede Meldung
   den betroffenen Pruefbereichen zu; noch nicht bewertete Meldungen kommen dran.
2. **Bewerten** - ein KI-Aufruf ohne Werkzeuge ueber das Abo (`abo_ki.py`).
   Er bekommt die Meldungen und je Pruefbereich, was der Autopilot heute prueft,
   und nennt hoechstens drei konkrete Anpassungen. Jede Antwort wird gegen die
   Kandidatenliste geprueft: erfundene Quellen oder Bereiche fliegen raus.
3. **Fragen** - je Vorschlag ein Entscheidungsknopf (Ja / Spaeter / Nein) auf der
   Entscheidungsseite und eine Mail an den Betreiber.
4. **Nachhalten** - der naechste Lauf uebernimmt die Klicks. Ein "Ja" bleibt als
   "Go, Umsetzung offen" stehen, bis jemand ihn mit `lernschleife --erledigt <id>`
   abhakt; der Waechter (`health.py`) meldet ein Go, das liegen bleibt.

Umgesetzt wird ein Go in einer normalen, beaufsichtigten Arbeitssitzung mit Tests,
nie von diesem Lauf. Faellt die KI aus, gibt es keine Vorschlaege, aber einen
Fehlervermerk im Stand - der Lauf bricht nie mit einer Ausnahme ab.
"""

from __future__ import annotations

import ast
import json
import logging
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .core.config import settings
from .marktradar import json_liste_aus_text, neueste
from .policy_radar import THEMEN_NACH_SCHLUESSEL

logger = logging.getLogger(__name__)

PAKET = Path(__file__).resolve().parent
STAND_DATEI = PAKET.parent / "logs" / "lernschleife-stand.json"
TABELLE = "lernschleife_gesehen"
MAX_VORSCHLAEGE = 3
GO_ALARM_TAGE = 7
LAUF_ALARM_TAGE = 8

# Pruefbereich -> (was der Autopilot dort prueft, zugehoerige Dateien im Paket).
# Die Schluessel decken alle Pruefbereiche des Richtlinien-Radars ab
# (Test: test_lernschleife.test_alle_radar_bereiche_bekannt).
BEREICHE: Dict[str, tuple] = {
    "canonical_engine": (
        "Canonical-Tags und Weiterleitungen",
        ["analyzers/canonical_engine.py", "analyzers/redirect_audit.py"],
    ),
    "core_web_vitals": (
        "Ladezeiten / Core Web Vitals",
        ["sources/pagespeed.py", "analyzers/lcp_abgleich.py"],
    ),
    "pagespeed": ("PageSpeed-Messung", ["sources/pagespeed.py"]),
    "duplicate_content": ("Doppelte Inhalte", ["analyzers/duplicate_content.py"]),
    "eeat": ("Vertrauenssignale (E-E-A-T), Spam-Richtlinien", ["analyzers/eeat.py"]),
    "geo_audit": (
        "Sichtbarkeit in KI-Suche (AI Overviews, AI Mode, ChatGPT, Gemini)",
        ["analyzers/geo_audit.py", "analyzers/ki_crawler.py"],
    ),
    "llms_ai_txt": ("llms.txt / ai.txt", ["analyzers/llms_ai_txt.py"]),
    "robots_sitemap": ("robots.txt und Sitemaps", ["analyzers/robots_sitemap.py"]),
    "schema_validation": (
        "Strukturierte Daten (Schema.org)",
        ["analyzers/schema_validation.py"],
    ),
    "topical_authority": ("Themenabdeckung", ["analyzers/topical_authority.py"]),
    "gsc": ("Search-Console-Daten", ["sources/gsc.py", "historie.py"]),
    "ga4": ("Analytics-Daten (GA4)", ["sources/ga4.py"]),
    "werbung": ("Hinweise zu bezahlter Suche im Wochenbericht", ["kundenbericht.py"]),
    "lokal": ("Local SEO / Google-Unternehmensprofil", ["maps.py"]),
}

SYSTEM = (
    "Du pflegst ein SEO-Pruefwerkzeug (SEO-Autopilot). Du entscheidest, welche "
    "Branchenneuigkeiten eine Anpassung an seinen Pruefungen oder Berichten "
    "erfordern. Du antwortest nur mit JSON."
)

AUFTRAG = """Hier sind die SEO-/KI-Suche-Meldungen der letzten 7 Tage und die Pruefbereiche des Werkzeugs.

Waehle HOECHSTENS {max} Meldungen, bei denen das Werkzeug konkret etwas anders pruefen,
messen oder berichten muss. Nimm nur echte Aenderungen (neue Richtlinie, neues Feld,
neue Messgroesse, neue Suchoberflaeche). Keine reinen Nachrichten, keine Kampagnentipps,
nichts, was das Werkzeug laut Beschreibung schon tut. Lieber ein leeres Array als ein
schwacher Vorschlag.

PRUEFBEREICHE:
{bereiche}

MELDUNGEN:
{meldungen}

Antworte NUR mit einem JSON-Array:
[{{"url": "<genau eine URL aus MELDUNGEN>", "bereich": "<Schluessel aus PRUEFBEREICHE>",
  "titel": "<kurze deutsche Ueberschrift ohne Fachjargon>",
  "was_aendern": "<was genau im Werkzeug angepasst wird, 1-3 Saetze>",
  "warum": "<warum das fuer die Websites zaehlt, 1-2 Saetze>",
  "aufwand": "klein|mittel|gross"}}]"""


@dataclass
class Laufergebnis:
    kandidaten: int = 0
    neue_vorschlaege: List[Dict[str, Any]] = field(default_factory=list)
    verworfen: int = 0
    go_offen: List[Dict[str, Any]] = field(default_factory=list)
    mail: str = "nicht versucht"
    fehler: List[str] = field(default_factory=list)

    def als_text(self) -> str:
        zeilen = [
            f"Lernschleife: {self.kandidaten} Meldung(en) bewertet, "
            f"{len(self.neue_vorschlaege)} Vorschlag/Vorschlaege, {self.verworfen} verworfen",
            f"Go, Umsetzung offen: {len(self.go_offen)}",
            f"Mail: {self.mail}",
        ]
        if self.fehler:
            zeilen.append("Fehler: " + "; ".join(self.fehler))
        return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# 1. Kandidaten
# ---------------------------------------------------------------------------


def pruefbereiche(meldung: Dict[str, Any]) -> List[str]:
    """Radar-Zuordnung: Themen der Meldung -> Pruefbereiche des Werkzeugs."""
    aus: List[str] = []
    for schluessel in meldung.get("themen") or []:
        thema = THEMEN_NACH_SCHLUESSEL.get(schluessel)
        for bereich in thema.pruefbereiche if thema else ():
            if bereich not in aus:
                aus.append(bereich)
    return aus


def _gesehen(con: sqlite3.Connection) -> set:
    con.execute(
        f"create table if not exists {TABELLE} (url text primary key, gesehen_am text not null)"
    )
    return {r[0] for r in con.execute(f"select url from {TABELLE}")}


def kandidaten(db_pfad: str, tage: int = 7) -> List[Dict[str, Any]]:
    """Noch nicht bewertete Meldungen, Meldungen mit Radar-Treffer zuerst."""
    con = sqlite3.connect(db_pfad)
    try:
        schon = _gesehen(con)
    finally:
        con.close()
    aus = []
    for m in neueste(db_pfad, tage=tage):
        if m["url"] in schon:
            continue
        aus.append({**m, "pruefbereiche": pruefbereiche(m)})
    return sorted(aus, key=lambda m: not m["pruefbereiche"])


def als_gesehen_merken(db_pfad: str, urls: List[str]) -> None:
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = sqlite3.connect(db_pfad)
    try:
        _gesehen(con)
        con.executemany(
            f"insert or ignore into {TABELLE} (url, gesehen_am) values (?, ?)",
            [(u, jetzt) for u in urls],
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 2. Bewerten
# ---------------------------------------------------------------------------


def _kurzbeschreibung(datei: str, laenge: int = 700) -> str:
    """Modul-Docstring als Beschreibung dessen, was der Bereich heute prueft."""
    try:
        doc = ast.get_docstring(ast.parse((PAKET / datei).read_text("utf-8"))) or ""
    except (OSError, SyntaxError):
        return ""
    doc = " ".join(doc.split())
    return doc[:laenge] + ("..." if len(doc) > laenge else "")


def auftrag(kand: List[Dict[str, Any]], max_vorschlaege: int = MAX_VORSCHLAEGE) -> str:
    bereiche = "\n".join(
        f"- {k}: {titel}. Heute: "
        + " | ".join(f"{d}: {_kurzbeschreibung(d)}" for d in dateien)
        for k, (titel, dateien) in BEREICHE.items()
    )
    meldungen = "\n".join(
        f"- {m['url']} | {m['titel'][:160]} | {(m.get('was_neu') or '')[:300]}"
        f" | Radar: {', '.join(m['pruefbereiche']) or '-'}"
        for m in kand
    )
    return AUFTRAG.format(max=max_vorschlaege, bereiche=bereiche, meldungen=meldungen)


def pruefen(
    antwort: List[Dict[str, Any]],
    kand: List[Dict[str, Any]],
    max_vorschlaege: int = MAX_VORSCHLAEGE,
) -> tuple:
    """Nur Vorschlaege mit echter Quelle aus der Liste und bekanntem Bereich."""
    nach_url = {m["url"]: m for m in kand}
    gut, verworfen = [], 0
    for v in antwort:
        if not isinstance(v, dict):
            verworfen += 1
            continue
        url, bereich = str(v.get("url", "")).strip(), str(v.get("bereich", "")).strip()
        texte = [str(v.get(k, "")).strip() for k in ("titel", "was_aendern", "warum")]
        if url not in nach_url or bereich not in BEREICHE or not all(texte):
            verworfen += 1
            continue
        if any(g["url"] == url and g["bereich"] == bereich for g in gut):
            continue
        aufwand = str(v.get("aufwand", "")).strip().lower()
        gut.append(
            {
                "url": url,
                "quelle_titel": nach_url[url]["titel"],
                "bereich": bereich,
                "titel": texte[0][:160],
                "was_aendern": texte[1][:800],
                "warum": texte[2][:500],
                "aufwand": (
                    aufwand if aufwand in ("klein", "mittel", "gross") else "mittel"
                ),
                "dateien": list(BEREICHE[bereich][1]),
            }
        )
    return gut[:max_vorschlaege], verworfen + max(0, len(gut) - max_vorschlaege)


def bewerten(
    kand: List[Dict[str, Any]], fragen: Optional[Callable[..., str]] = None
) -> tuple:
    """Ein KI-Aufruf ohne Werkzeuge. Rueckgabe (vorschlaege, verworfen)."""
    if not kand:
        return [], 0
    if fragen is None:
        from .abo_ki import fragen
    text = fragen(auftrag(kand), system=SYSTEM, modell="opus", zeitlimit=600)
    return pruefen(json_liste_aus_text(text), kand)


# ---------------------------------------------------------------------------
# 3./4. Stand, Entscheidungen, Mail
# ---------------------------------------------------------------------------


def stand_laden(pfad: Path = STAND_DATEI) -> Dict[str, Any]:
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"vorschlaege": []}


def stand_speichern(stand: Dict[str, Any], pfad: Path = STAND_DATEI) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(stand, ensure_ascii=False, indent=1), encoding="utf-8")


def klicks_uebernehmen(stand: Dict[str, Any], antworten: Dict[str, Dict]) -> None:
    """Knopf-Klicks in den Stand: ja -> go, nein -> abgelehnt, spaeter -> spaeter."""
    status = {"ja": "go", "nein": "abgelehnt", "spaeter": "spaeter"}
    for v in stand["vorschlaege"]:
        a = antworten.get(v["id"])
        if v["status"] in ("offen", "spaeter") and a and a.get("wahl") in status:
            v["status"] = status[a["wahl"]]
            v["entschieden_am"] = str(a.get("zeit") or date.today().isoformat())[:10]


def knoepfe_anlegen(vorschlaege: List[Dict[str, Any]], ordner: Optional[Path]) -> None:
    from . import entscheidungen as ent

    punkte = [
        {
            "id": v["id"],
            "titel": f"SEO-Autopilot anpassen: {v['titel']}",
            "text": f"{v['was_aendern']}\n\nWarum: {v['warum']}\nAufwand: {v['aufwand']}",
            "kanal": "SEO",
            "link": v["url"],
            "knopf": "Quelle",
        }
        for v in vorschlaege
    ]
    urls = ent.punkte_anlegen(punkte, **({"ordner": ordner} if ordner else {}))
    for v in vorschlaege:
        v["knopf_url"] = urls.get(v["id"], "")


def mail_text(neu: List[Dict[str, Any]], go_offen: List[Dict[str, Any]]) -> str:
    teile = []
    if neu:
        teile.append("Neue Vorschläge (Go oder Nein per Knopf):\n")
    for v in neu:
        teile.append(
            f"■ {v['titel']}\n{v['was_aendern']}\nWarum: {v['warum']}\n"
            f"Aufwand: {v['aufwand']} · Bereich: {BEREICHE[v['bereich']][0]}\n"
            f"Quelle: {v['url']}\n"
            + (f"Entscheiden: {v['knopf_url']}\n" if v.get("knopf_url") else "")
        )
    if go_offen:
        teile.append("Go erteilt, Umsetzung noch offen:\n")
        teile += [
            f"■ {v['titel']} (Go am {v.get('entschieden_am', '?')})" for v in go_offen
        ]
    teile.append(
        "\nUmgesetzt wird nur nach Go, in einer Arbeitssitzung mit Tests. "
        "Dieser Lauf ändert keinen Code."
    )
    return "\n".join(teile)


def mail_html(neu: List[Dict[str, Any]], go_offen: List[Dict[str, Any]]) -> str:
    from .entscheidungen import knopf_html

    karten = []
    for v in neu:
        knopf = knopf_html(v["knopf_url"], "Go / Nein") if v.get("knopf_url") else ""
        karten.append(
            '<div style="border:1px solid #e5e2dc;border-radius:12px;padding:14px;margin:0 0 12px">'
            f'<div style="font-weight:600;font-size:16px">{escape(v["titel"])}</div>'
            f'<p style="margin:8px 0">{escape(v["was_aendern"])}</p>'
            f'<p style="margin:8px 0;color:#555">Warum: {escape(v["warum"])}</p>'
            f'<div style="color:#777;font-size:13px">Aufwand: {escape(v["aufwand"])} · '
            f'<a href="{escape(v["url"])}" style="color:#e8540a">Quelle</a></div>{knopf}</div>'
        )
    offen = "".join(
        f'<li>{escape(v["titel"])} (Go am {escape(str(v.get("entschieden_am", "?")))})</li>'
        for v in go_offen
    )
    return (
        '<div style="background:#faf9f7;color:#1a1a1a;font:15px/1.5 -apple-system,Segoe UI,sans-serif;padding:16px">'
        + "".join(karten)
        + (
            f"<p><b>Go erteilt, Umsetzung offen:</b></p><ul>{offen}</ul>"
            if offen
            else ""
        )
        + '<p style="color:#777;font-size:13px">Umgesetzt wird nur nach Go, in einer Arbeitssitzung '
        "mit Tests. Dieser Lauf ändert keinen Code.</p></div>"
    )


def empfaenger(projects_pfad: str) -> str:
    """Abschnitt `lernschleife.empfaenger` in projects.yaml (leer = keine Mail)."""
    try:
        daten = yaml.safe_load(Path(projects_pfad).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ""
    return str(((daten.get("lernschleife") or {}).get("empfaenger")) or "").strip()


def mail_senden(an: str, betreff: str, text: str, html: str) -> tuple:
    from .notifications.mail import empfaenger_erlaubt

    if not settings.MAILER_PFAD or not empfaenger_erlaubt(an):
        return False, "gesperrt (kein Mailer oder kein zulässiger Empfänger)"
    with tempfile.TemporaryDirectory() as tmp:
        t, h = Path(tmp, "text.txt"), Path(tmp, "mail.html")
        t.write_text(text, encoding="utf-8")
        h.write_text(html, encoding="utf-8")
        lauf = subprocess.run(
            [
                "python3",
                settings.MAILER_PFAD,
                "--to",
                an,
                "--subject",
                betreff,
                "--body-file",
                str(t),
                "--html-file",
                str(h),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    return lauf.returncode == 0, (lauf.stdout.strip() or lauf.stderr.strip())[-200:]


# ---------------------------------------------------------------------------
# Ein Lauf
# ---------------------------------------------------------------------------


def lauf(
    db_pfad: str,
    projects_pfad: str,
    senden: bool = True,
    fragen: Optional[Callable[..., str]] = None,
    stand_pfad: Path = STAND_DATEI,
    ordner: Optional[Path] = None,
    sender: Callable[..., tuple] = mail_senden,
    heute: Optional[date] = None,
) -> Laufergebnis:
    heute = heute or date.today()
    erg, stand = Laufergebnis(), stand_laden(stand_pfad)
    from . import entscheidungen as ent

    klicks_uebernehmen(stand, ent.antworten(**({"ordner": ordner} if ordner else {})))
    kand = []
    try:
        kand = kandidaten(db_pfad)
        erg.kandidaten = len(kand)
        neu, erg.verworfen = bewerten(kand, fragen)
    except Exception as exc:  # KI/DB-Ausfall: vermerken, nie abbrechen
        logger.warning(f"[Lernschleife] Bewertung fehlgeschlagen: {exc}")
        erg.fehler.append(f"Bewertung: {str(exc)[:200]}")
        neu = []
    for i, v in enumerate(neu, 1):
        v.update(
            id=f"lernschleife-{heute.isoformat()}-{i}",
            status="offen",
            am=heute.isoformat(),
        )
    erg.neue_vorschlaege = neu
    erg.go_offen = [v for v in stand["vorschlaege"] if v["status"] == "go"]
    if not senden:  # Probelauf: nichts merken, nichts anlegen, nichts schicken
        erg.mail = "Probelauf"
        return erg
    if not erg.fehler:
        als_gesehen_merken(db_pfad, [m["url"] for m in kand])
    if neu:
        knoepfe_anlegen(neu, ordner)
    stand["vorschlaege"] += neu
    erg.go_offen = [v for v in stand["vorschlaege"] if v["status"] == "go"]
    if neu or erg.go_offen:
        betreff = f"SEO-Autopilot · Lernschleife KW{heute.isocalendar()[1]}: " + (
            f"{len(neu)} Vorschlag/Vorschläge"
            if neu
            else f"{len(erg.go_offen)} Go offen"
        )
        an = empfaenger(projects_pfad)
        ok, info = sender(
            an, betreff, mail_text(neu, erg.go_offen), mail_html(neu, erg.go_offen)
        )
        erg.mail = "verschickt" if ok else f"nicht verschickt: {info}"
    else:
        erg.mail = "nicht nötig (nichts Neues, kein Go offen)"
    stand.update(
        letzter_lauf=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        kandidaten=erg.kandidaten,
        fehler=erg.fehler,
        mail=erg.mail,
    )
    stand_speichern(stand, stand_pfad)
    return erg


def erledigt(vorschlag_id: str, stand_pfad: Path = STAND_DATEI) -> bool:
    """Go umgesetzt -> abhaken (nach Commit mit Tests)."""
    stand = stand_laden(stand_pfad)
    for v in stand["vorschlaege"]:
        if v["id"] == vorschlag_id:
            v["status"], v["erledigt_am"] = "erledigt", date.today().isoformat()
            stand_speichern(stand, stand_pfad)
            return True
    return False


def go_ueberfaellig(
    stand: Dict[str, Any], heute: Optional[date] = None, tage: int = GO_ALARM_TAGE
) -> List[Dict[str, Any]]:
    grenze = (heute or date.today()) - timedelta(days=tage)
    aus = []
    for v in stand.get("vorschlaege", []):
        try:
            seit = date.fromisoformat(str(v.get("entschieden_am", ""))[:10])
        except ValueError:
            continue
        if v.get("status") == "go" and seit <= grenze:
            aus.append(v)
    return aus
