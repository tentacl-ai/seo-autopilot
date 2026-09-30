#!/usr/bin/env python3
"""KI-Sichtbarkeitstest: Wird eine Website von ChatGPT, Gemini und Claude (jeweils MIT Websuche) genannt?

    cd <Installationsordner>
    set -a && . <eigene Zugangsdaten-Datei> && set +a
    venv/bin/python scripts/ki_sichtbarkeit.py --fragen lokal/ki-fragen.json   # JSON auf stdout

Fragen-Datei (je Website eine):
    {"host": "natur-beispiel.at", "marken": ["natur-beispiel.at", "Beispiel Natur-Beispiel"],
     "fragen": ["Ich suche ein Coaching-Retreat auf Kreta ...", ...],
     "wettbewerber": [{"name": "Anderes Retreat", "host": "anderes-retreat.de"}]}  # optional

Ergebnis je Frage und KI: genannt (Link auf den Host = "verlinkt", nur Name im Text = "erwaehnt", sonst "nein"),
zitierte Quellen (Domains) und ein kurzer Antwortauszug. Die Antworten schwanken von Lauf zu Lauf -
aussagekraeftig ist der Verlauf ueber Wochen, nicht ein einzelner Lauf.
Kosten: wenige Cent je Lauf (5 Fragen x 3 KIs, je eine Websuche).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx

# Pfad der eigenen Zugangsdaten-Datei: SECRETS_DATEI in der .env (der Ordner ist
# oeffentlich auf GitHub, deshalb steht hier kein Serverpfad).
try:
    from seo_autopilot.core.config import settings as _einst

    SECRETS = Path(_einst.SECRETS_DATEI or "")
except Exception:  # pragma: no cover - Skript laeuft auch ohne Paket
    SECRETS = Path(os.environ.get("SEO_SECRETS_DATEI", ""))
OPENAI_MODELL = os.environ.get("KI_SICHT_OPENAI_MODELL", "gpt-5-mini")
GEMINI_MODELL = os.environ.get("KI_SICHT_GEMINI_MODELL", "gemini-flash-latest")
CLAUDE_MODELL = os.environ.get("KI_SICHT_CLAUDE_MODELL", "claude-opus-5")
HINWEIS = (
    "Antworte auf Deutsch, knapp (hoechstens 150 Woerter), und nenne konkrete Anbieter "
    "mit ihrer Website, wenn es welche gibt."
)


def _schluessel(name: str) -> str:
    """Schluessel aus der Umgebung, sonst aus der Datei SECRETS_DATEI (letzte Zeile gewinnt). Nie ausgeben."""
    if os.environ.get(name):
        return os.environ[name]
    wert = ""
    if SECRETS.exists():
        for zeile in SECRETS.read_text(encoding="utf-8").splitlines():
            k, _, v = zeile.partition("=")
            if k.strip() == name:
                wert = v.strip().strip('"').strip("'")
    if not wert:
        raise RuntimeError(f"{name} fehlt")
    return wert


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def frage_chatgpt(frage: str, hinweis: str = HINWEIS) -> dict:
    r = httpx.post(
        "https://api.openai.com/v1/responses",
        timeout=300,  # Websuche dauert teils > 2 Min
        headers={"Authorization": f"Bearer {_schluessel('OPENAI_API_KEY')}"},
        json={
            "model": OPENAI_MODELL,
            "tools": [{"type": "web_search"}],
            "input": f"{frage}\n\n{hinweis}",
        },
    )
    r.raise_for_status()
    text, quellen = "", []
    for teil in r.json().get("output", []):
        for c in teil.get("content", []) or []:
            if c.get("type") == "output_text":
                text += c.get("text", "")
                quellen += [a["url"] for a in c.get("annotations", []) if a.get("url")]
    return {"text": text, "quellen": quellen}


def frage_gemini(frage: str, hinweis: str = HINWEIS) -> dict:
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODELL}:generateContent",
        params={"key": _schluessel("GEMINI_API_KEY")},
        timeout=120,
        json={
            "contents": [{"parts": [{"text": f"{frage}\n\n{hinweis}"}]}],
            "tools": [{"google_search": {}}],
        },
    )
    r.raise_for_status()
    kandidat = (r.json().get("candidates") or [{}])[0]
    text = "".join(
        p.get("text", "") for p in kandidat.get("content", {}).get("parts", [])
    )
    # Gemini liefert Weiterleitungs-Links; die echte Domain steht im Titel des Treffers
    chunks = kandidat.get("groundingMetadata", {}).get("groundingChunks", [])
    quellen = [f"https://{c['web'].get('title', '')}" for c in chunks if c.get("web")]
    return {"text": text, "quellen": quellen}


def frage_claude(frage: str, hinweis: str = HINWEIS) -> dict:
    """Claude mit Websuche ueber Roberts Max-Abo (seit 16.09.2026, nicht mehr ueber den API-Key)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from seo_autopilot import abo_ki

    antwort = abo_ki.lauf(
        [{"type": "text", "text": f"{frage}\n\n{hinweis}"}],
        modell=CLAUDE_MODELL,
        websuche=True,
        zeitlimit=300,
    )
    return {"text": antwort["text"], "quellen": antwort["urls"]}


KIS = {"ChatGPT": frage_chatgpt, "Gemini": frage_gemini, "Claude": frage_claude}


def _wettbewerber_im(
    text: str, domains: list[str], wettbewerber: list[dict]
) -> list[str]:
    """Namen der Wettbewerber, die im VOLLEN Antworttext oder in den Quellen vorkommen."""
    klein = text.lower()
    return [
        w["name"]
        for w in wettbewerber
        if (w.get("host") and any(w["host"] in d for d in domains))
        or any(m.lower() in klein for m in [w["name"], *w.get("marken", [])] if m)
    ]


def bewerten(
    ergebnis: dict, host: str, marken: list[str], wettbewerber: list[dict] | None = None
) -> dict:
    domains = list(dict.fromkeys(_domain(q) for q in ergebnis["quellen"] if q))
    text = ergebnis["text"]
    if any(host in d for d in domains):
        genannt = "verlinkt"
    elif host in text.lower() or any(m.lower() in text.lower() for m in marken):
        genannt = "erwaehnt"
    else:
        genannt = "nein"
    return {
        "genannt": genannt,
        "quellen": domains[:8],
        "wettbewerber": _wettbewerber_im(text, domains, wettbewerber or []),
        "auszug": " ".join(text.split())[:300],
    }


def einzeln(
    ki: str,
    frage: str,
    host: str,
    marken: list[str],
    wettbewerber: list[dict] | None = None,
) -> tuple[str, str, dict]:
    try:
        return ki, frage, bewerten(KIS[ki](frage), host, marken, wettbewerber)
    except (
        Exception
    ) as e:  # noqa: BLE001 - eine KI darf ausfallen, der Test laeuft weiter
        print(
            f"[KI-Sichtbarkeit] {ki} fehlgeschlagen: {type(e).__name__}: {str(e)[:160]}",
            file=sys.stderr,
        )
        return (
            ki,
            frage,
            {"genannt": "fehler", "fehler": f"{type(e).__name__}: {str(e)[:160]}"},
        )


def pruefen(konfig: dict) -> dict:
    host, marken = konfig["host"], konfig.get("marken", [konfig["host"]])
    # optional: [{"name": "Wettbewerber GmbH", "host": "wettbewerber.de", "marken": [...]}]
    wettbewerber = konfig.get("wettbewerber") or []
    auftraege = [(ki, f) for f in konfig["fragen"] for ki in KIS]
    with ThreadPoolExecutor(max_workers=6) as pool:
        ergebnisse = list(
            pool.map(
                lambda a: einzeln(a[0], a[1], host, marken, wettbewerber), auftraege
            )
        )
    je_frage: dict[str, dict] = {}
    for ki, frage, e in ergebnisse:
        je_frage.setdefault(frage, {})[ki] = e
    zaehler = {
        ki: sum(
            1
            for k, _, e in ergebnisse
            if k == ki and e["genannt"] in ("verlinkt", "erwaehnt")
        )
        for ki in KIS
    }
    return {
        "host": host,
        "fragen": len(konfig["fragen"]),
        "genannt_je_ki": zaehler,
        "modelle": {
            "ChatGPT": OPENAI_MODELL,
            "Gemini": GEMINI_MODELL,
            "Claude": CLAUDE_MODELL,
        },
        "ergebnisse": je_frage,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fragen", required=True, help="JSON mit host, marken, fragen")
    a = p.parse_args()
    konfig = json.loads(Path(a.fragen).read_text(encoding="utf-8"))
    print(json.dumps(pruefen(konfig), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
