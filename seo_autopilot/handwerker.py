"""Handwerker — der Teil des Autopiloten, der wirklich repariert.

Bis v1.12 konnte der Autopilot messen, aber nicht handwerklich sauber
reparieren: Vorschläge kamen ohne Seitenkontext (oder aus Vorlagen mit
erfundenen Behauptungen), und der Static-Adapter schrieb jede Änderung in
die Startseite, egal welche Seite gemeint war.

Dieses Modul liefert die drei fehlenden Bausteine:

1. **Seitenkontext** — Titel, Beschreibung, H1, Bilder und den sichtbaren
   Text einer Seite (aus der lokalen Datei oder per HTTP), damit die KI
   Texte schreibt, die zur Seite passen, statt zu raten.
2. **Plausibilitätsprüfung** — jeder Vorschlag muss Längen, Sprache und die
   Projektregeln (verbotene Wörter, keine erfundenen Zahlen) einhalten,
   bevor er in eine Datei darf. Im Zweifel wird NICHT geschrieben.
3. **Datei-Zuordnung** — welche HTML-Datei gehört zu welcher Adresse.

Alles hier ist bewusst ohne Nebenwirkungen: Es wird gelesen und geprüft,
geschrieben wird nur im Adapter.
"""

from __future__ import annotations

import base64
import html as html_mod
import io
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# --- Grenzen, wie Suchmaschinen sie anzeigen ---------------------------------

TITEL_MIN, TITEL_MAX = 25, 65
BESCHREIBUNG_MIN, BESCHREIBUNG_MAX = 70, 160
OG_TITEL_MAX = 90
ALT_MAX = 125
H1_MAX = 90

# Eingriffe, die ohne Aufsicht laufen dürfen, sobald ein plausibler Vorschlag
# vorliegt. Bewusst nur Meta-Ebene und Auszeichnung — nie sichtbarer Fließtext,
# nie Struktur, nie Adressen (die harte Sperrliste liegt in ausfuehrung.py).
SICHERE_EINGRIFFE = frozenset(
    {
        "missing_title",
        "short_title",
        "long_title",
        "missing_meta_description",
        "short_meta_description",
        "long_meta_description",
        "missing_og_title",
        "missing_og_image",
        "missing_twitter_card",
        "images_without_alt",
        "image_missing_dimensions",
        # "no_jsonld" entfernt 18.09.2026: generisches WebPage-JSON-LD bringt laut
        # Google weder Rich Results noch KI-Sichtbarkeit (befund_arten.HINWEIS).
    }
)

# Vorschlagsquellen: "claude" = KI mit Seitenkontext, "regel" = deterministisch
# aus vorhandenen Daten (z. B. twitter:card aus og:*), "template" = geratene
# Vorlage ohne Wissen über die Seite. Vorlagen laufen NIE automatisch.
QUELLE_KI = "claude"
QUELLE_REGEL = "regel"
QUELLE_VORLAGE = "template"

# Formulierungen, die Vorlagen und schlechte KI-Antworten verraten.
_ERFUNDENE_BEHAUPTUNGEN = (
    r"\b\d{1,3}\s*\+\s*jahre",  # "20+ Jahre Erfahrung"
    r"\bjahre\s+erfahrung\b",
    r"\btestsieger\b",
    r"\bmarktführer\b",
    r"\bnr\.?\s*1\b",
    r"\b(garantiert|100\s*%)\b",
)
_KI_ARTEFAKTE = (
    r"^(title|titel|desc|description|beschreibung|alt)\s*:",
    r"^```",
    r"^\s*[\"„“']",  # in Anführungszeichen gesetzte Antwort
    r"\bhier ist\b",
    r"\bals ki\b",
)


# ---------------------------------------------------------------------------
# Datei-Zuordnung
# ---------------------------------------------------------------------------


def datei_fuer_seite(root: Path, seite: Optional[str]) -> Optional[Path]:
    """Welche Datei unter `root` liefert die Adresse `seite` aus?

    `https://x.de/` → root/index.html · `/a/b/` → root/a/b/index.html ·
    `/a/b` → root/a/b/index.html oder root/a/b.html · `/a.html` → root/a.html.
    Gibt None zurück, wenn nichts passt — dann wird NICHT geschrieben.
    """
    if not seite:
        return None
    pfad = urlparse(seite).path if "://" in seite else seite.split("?")[0]
    pfad = pfad.split("#")[0].split("?")[0]
    rel = pfad.strip("/")
    if ".." in rel.split("/"):
        return None
    kandidaten: List[Path] = []
    if rel == "":
        kandidaten.append(root / "index.html")
    elif rel.endswith(".html") or rel.endswith(".htm"):
        kandidaten.append(root / rel)
    else:
        kandidaten.append(root / rel / "index.html")
        kandidaten.append(root / f"{rel}.html")
    for k in kandidaten:
        try:
            if (
                k.exists()
                and k.is_file()
                and k.resolve().is_relative_to(root.resolve())
            ):
                return k
        except (OSError, ValueError):
            continue
    return None


def lokales_bild(root: Path, seite: Optional[str], src: str) -> Optional[Path]:
    """Lokale Datei zu einem <img src>. Absolute Fremd-URLs → None."""
    if not src or src.startswith("data:"):
        return None
    if "://" in src:
        return None
    src = src.split("?")[0].split("#")[0]
    if src.startswith("/"):
        kandidat = root / src.lstrip("/")
    else:
        seiten_datei = datei_fuer_seite(root, seite)
        basis = seiten_datei.parent if seiten_datei else root
        kandidat = basis / src
    try:
        if kandidat.exists() and kandidat.resolve().is_relative_to(root.resolve()):
            return kandidat
    except (OSError, ValueError):
        pass
    return None


# ---------------------------------------------------------------------------
# Seitenkontext
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_SKRIPT = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.I | re.S)
_WS = re.compile(r"\s+")


def _attr(tag: str, name: str) -> Optional[str]:
    m = re.search(rf'\b{name}\s*=\s*"([^"]*)"', tag, re.I) or re.search(
        rf"\b{name}\s*=\s*'([^']*)'", tag, re.I
    )
    return html_mod.unescape(m.group(1)) if m else None


def _meta(html: str, key: str, attr: str = "name") -> Optional[str]:
    m = re.search(
        rf'<meta\s+[^>]*{attr}\s*=\s*["\']{re.escape(key)}["\'][^>]*>', html, re.I
    )
    return _attr(m.group(0), "content") if m else None


def bilder_der_seite(html: str) -> List[Dict[str, Any]]:
    """Alle <img>-Tags mit src, alt (None = fehlt), width/height, dekorativ."""
    out = []
    for m in re.finditer(r"<img\b[^>]*>", html, re.I):
        tag = m.group(0)
        alt = _attr(tag, "alt")
        role = (_attr(tag, "role") or "").lower()
        hidden = (_attr(tag, "aria-hidden") or "").lower() == "true"
        out.append(
            {
                "tag": tag,
                "src": _attr(tag, "src") or "",
                "alt": alt,
                "width": _attr(tag, "width"),
                "height": _attr(tag, "height"),
                "dekorativ": role in ("presentation", "none") or hidden or alt == "",
            }
        )
    return out


def kontext_aus_html(html: str, seite: str = "") -> Dict[str, Any]:
    """Zieht die Angaben aus dem HTML, die ein Texter braucht."""
    t = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    lang = re.search(r"<html[^>]*\blang\s*=\s*[\"']([a-zA-Z-]+)", html, re.I)
    haupt = re.search(r"<(main|article)[^>]*>(.*?)</\1>", html, re.I | re.S)
    koerper = (
        haupt.group(2)
        if haupt
        else (re.search(r"<body[^>]*>(.*)</body>", html, re.I | re.S) or [None, html])[
            1
        ]
    )
    text = _WS.sub(
        " ", html_mod.unescape(_TAG.sub(" ", _SKRIPT.sub(" ", koerper or "")))
    ).strip()
    woerter = text.split(" ")
    return {
        "seite": seite,
        "lang": (lang.group(1).split("-")[0].lower() if lang else "de"),
        "title": (
            _WS.sub(" ", html_mod.unescape(_TAG.sub("", t.group(1)))).strip()
            if t
            else ""
        ),
        "description": _meta(html, "description") or "",
        "h1": (
            _WS.sub(" ", html_mod.unescape(_TAG.sub("", h1.group(1)))).strip()
            if h1
            else ""
        ),
        "og_title": _meta(html, "og:title", "property") or "",
        "og_image": _meta(html, "og:image", "property") or "",
        "og_description": _meta(html, "og:description", "property") or "",
        "twitter_card": _meta(html, "twitter:card") or "",
        "hat_jsonld": "application/ld+json" in html,
        "text": " ".join(woerter[:700]),
        "wortzahl": len(woerter) if text else 0,
        "bilder": bilder_der_seite(html),
    }


def seiten_kontext(
    seite: str, root: Optional[Path] = None, timeout: float = 15.0
) -> Optional[Dict[str, Any]]:
    """Kontext aus der lokalen Datei (bevorzugt) oder per HTTP."""
    if root is not None:
        datei = datei_fuer_seite(root, seite)
        if datei is not None:
            try:
                return kontext_aus_html(datei.read_text(encoding="utf-8"), seite)
            except OSError as exc:
                logger.warning(f"[handwerker] {datei} nicht lesbar: {exc}")
    if "://" not in (seite or ""):
        return None
    try:
        import httpx

        r = httpx.get(
            seite,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "seo-autopilot/handwerker"},
        )
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        return kontext_aus_html(r.text, seite)
    except Exception as exc:  # Netz ist keine Katastrophe — dann eben kein Kontext
        logger.warning(f"[handwerker] Abruf {seite} gescheitert: {exc}")
        return None


# ---------------------------------------------------------------------------
# Plausibilität
# ---------------------------------------------------------------------------


def saeubern(text: str) -> str:
    """Nimmt Anführungszeichen, Codezäune und Labels weg, die KI-Antworten
    gern mitliefern — ändert aber nie den Inhalt."""
    s = (text or "").strip()
    s = re.sub(r"^```[a-z]*\s*|\s*```$", "", s, flags=re.I).strip()
    s = re.sub(
        r"^(title|titel|desc|description|beschreibung|alt|h1)\s*:\s*", "", s, flags=re.I
    )
    if len(s) >= 2 and s[0] in "\"„“'" and s[-1] in "\"“”'":
        s = s[1:-1].strip()
    return _WS.sub(" ", s)


def plausibel(
    typ: str, text: str, regeln: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str]:
    """Darf dieser Text in die Datei? (ja/nein, Grund)."""
    regeln = regeln or {}
    s = saeubern(text)
    if not s:
        return False, "leer"
    if "\n" in s:
        return False, "mehrzeilig"
    tief = s.lower()
    for muster in _KI_ARTEFAKTE:
        if re.search(muster, tief, re.I):
            return False, "sieht nach Antwort-Artefakt aus"
    for muster in _ERFUNDENE_BEHAUPTUNGEN:
        if re.search(muster, tief, re.I):
            return False, "enthält eine unbelegte Behauptung"
    for wort in regeln.get("verbotene_woerter", []) or []:
        if re.search(rf"\b{re.escape(str(wort).lower())}\b", tief):
            return False, f"verbotenes Wort: {wort}"
    if "<" in s or ">" in s:
        return False, "enthält HTML"

    grenzen = {
        "missing_title": (TITEL_MIN, TITEL_MAX),
        "short_title": (TITEL_MIN, TITEL_MAX),
        "long_title": (TITEL_MIN, TITEL_MAX),
        "missing_meta_description": (BESCHREIBUNG_MIN, BESCHREIBUNG_MAX),
        "short_meta_description": (BESCHREIBUNG_MIN, BESCHREIBUNG_MAX),
        "long_meta_description": (BESCHREIBUNG_MIN, BESCHREIBUNG_MAX),
        "missing_og_title": (10, OG_TITEL_MAX),
        "missing_h1": (3, H1_MAX),
        "images_without_alt": (3, ALT_MAX),
    }
    if typ in grenzen:
        lo, hi = grenzen[typ]
        if len(s) < lo:
            return False, f"zu kurz ({len(s)} < {lo})"
        if len(s) > hi:
            return False, f"zu lang ({len(s)} > {hi})"
    return True, "ok"


# ---------------------------------------------------------------------------
# KI-Aufrufe (nur hier, damit Modell/Regeln an einer Stelle stehen)
# ---------------------------------------------------------------------------


def system_prompt(
    name: str, domain: str, regeln: Optional[Dict[str, Any]], lang: str
) -> str:
    sprache = {
        "de": "Deutsch",
        "en": "Englisch",
        "fr": "Französisch",
        "nl": "Niederländisch",
    }.get(lang, "Deutsch")
    zeilen = [
        f"Du bist SEO-Texter für die Website '{name}' ({domain}).",
        f"Sprache der Antwort: {sprache}. Schreibe natürlich, konkret, ohne Werbefloskeln.",
        "Nutze NUR Informationen, die im gelieferten Seitentext stehen. Erfinde keine Zahlen, "
        "Jahre, Auszeichnungen, Kundenzahlen oder Versprechen.",
        "Gib ausschließlich den gewünschten Text aus: keine Erklärung, keine Anführungszeichen, "
        "kein Label, kein Markdown.",
    ]
    regeln = regeln or {}
    if regeln.get("verbotene_woerter"):
        zeilen.append(
            "Diese Wörter sind tabu: "
            + ", ".join(map(str, regeln["verbotene_woerter"]))
            + "."
        )
    for hinweis in regeln.get("hinweise", []) or []:
        zeilen.append(str(hinweis))
    return "\n".join(zeilen)


def auftrag(typ: str, kontext: Dict[str, Any], issue: Dict[str, Any]) -> str:
    """Der Nutzer-Teil des Prompts: Aufgabe + Seitenkontext."""
    k = kontext
    lage = (
        f"Seite: {k.get('seite')}\n"
        f"Aktueller Titel: {k.get('title') or '(keiner)'}\n"
        f"Aktuelle Beschreibung: {k.get('description') or '(keine)'}\n"
        f"H1: {k.get('h1') or '(keine)'}\n"
        f"Seitentext (Auszug): {k.get('text') or '(leer)'}\n"
    )
    if typ in ("missing_title", "short_title", "long_title"):
        aufgabe = (
            f"Schreibe den HTML-<title> dieser Seite: {TITEL_MIN}–{TITEL_MAX - 8} Zeichen (harte Obergrenze {TITEL_MAX}), "
            "wichtigster Begriff vorn, Marke am Ende nur wenn Platz ist."
        )
    elif typ in (
        "missing_meta_description",
        "short_meta_description",
        "long_meta_description",
    ):
        aufgabe = (
            f"Schreibe die Meta-Description dieser Seite: {BESCHREIBUNG_MIN + 30}–{BESCHREIBUNG_MAX - 15} Zeichen (harte Obergrenze {BESCHREIBUNG_MAX}, zähle mit), "
            "ein bis zwei Sätze, sagt konkret was die Seite bietet, endet mit einer leisen Handlungsaufforderung."
        )
    elif typ == "missing_og_title":
        aufgabe = f"Schreibe den og:title für das Teilen in sozialen Netzwerken: bis {OG_TITEL_MAX} Zeichen, ohne Markenanhang."
    elif typ == "missing_h1":
        aufgabe = "Schlage die H1-Überschrift dieser Seite vor: 3–10 Wörter, beschreibt den Kern der Seite."
    elif typ == "low_ctr_opportunity":
        aufgabe = (
            f"Die Seite erscheint für '{issue.get('keyword')}' auf Position {issue.get('position')}, "
            f"wird aber selten geklickt (CTR {issue.get('ctr')} %). Schreibe einen neuen Titel "
            f"({TITEL_MIN}–{TITEL_MAX} Zeichen) und eine neue Beschreibung ({BESCHREIBUNG_MIN}–{BESCHREIBUNG_MAX} Zeichen), "
            "die den Suchbegriff aufgreifen. Format genau so:\nTITLE: ...\nDESC: ..."
        )
    else:
        aufgabe = (
            f"SEO-Befund: {issue.get('title', '')} — {issue.get('description', '')}. "
            "Gib eine kurze, umsetzbare Empfehlung in maximal 3 Sätzen."
        )
    return f"{aufgabe}\n\n{lage}"


def bild_fuer_ki(pfad: Path, max_kante: int = 1024) -> Optional[Tuple[str, str]]:
    """Bild auf ≤1024px JPEG normalisieren (E339) → (media_type, base64)."""
    try:
        from PIL import Image

        im = Image.open(pfad)
        im = im.convert("RGB")
        im.thumbnail((max_kante, max_kante))
        puffer = io.BytesIO()
        im.save(puffer, format="JPEG", quality=85)
        return "image/jpeg", base64.standard_b64encode(puffer.getvalue()).decode(
            "ascii"
        )
    except Exception as exc:
        logger.warning(f"[handwerker] Bild {pfad} nicht lesbar: {exc}")
        return None


def alt_text_prompt(kontext: Dict[str, Any], src: str) -> str:
    return (
        "Schreibe den Alt-Text für dieses Bild, wie es auf der Seite eingesetzt ist: "
        f"ein Satz, höchstens {ALT_MAX} Zeichen, beschreibt was zu sehen ist und wozu es dient. "
        "Nicht mit 'Bild von' oder 'Foto von' beginnen. Keine Behauptungen, die das Bild nicht zeigt.\n\n"
        f"Seite: {kontext.get('seite')} — {kontext.get('title')}\n"
        f"Dateiname: {src}\n"
        f"Seitentext (Auszug): {(kontext.get('text') or '')[:600]}"
    )
