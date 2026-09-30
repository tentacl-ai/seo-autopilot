"""
KI-Crawler in robots.txt — eine Quelle fuer robots_sitemap.py und geo_audit.py.

Vorher hatten beide Analyzer eigene, unterschiedliche Listen und einen eigenen
Parser. Beide Parser liessen den vorletzten von zwei direkt aufeinanderfolgenden
``User-agent``-Zeilen fallen (RFC 9309: sie bilden EINE Gruppe) und kannten die
Such-Crawler OAI-SearchBot, Claude-SearchBot und Perplexity-User nicht.

Unterschieden wird nach Zweck, weil das fuer die Sichtbarkeit entscheidet:
- ``suche``/``nutzer``: Sperre = die Seite kann in KI-Antworten nicht genannt
  werden -> Fehler.
- ``training``/``steuerung``: Sperre betrifft nur das Modelltraining; die
  KI-Suche des Anbieters laeuft ueber einen eigenen Crawler -> nur Hinweis.

Tabelle und Gruppen-/Longest-Match-Logik nach claude-seo
(scripts/agentic_check.py, v2.4.1, MIT, Copyright (c) 2026 agricidaniel),
Stand der Herstellerangaben dort: 2026-09-23.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

SUCHE = "suche"
NUTZER = "nutzer"
TRAINING = "training"
STEUERUNG = "steuerung"

# (Token, Anbieter, Zweck)
KI_CRAWLER: List[Tuple[str, str, str]] = [
    ("OAI-SearchBot", "OpenAI", SUCHE),
    ("ChatGPT-User", "OpenAI", NUTZER),
    ("GPTBot", "OpenAI", TRAINING),
    ("Claude-SearchBot", "Anthropic", SUCHE),
    ("Claude-User", "Anthropic", NUTZER),
    ("ClaudeBot", "Anthropic", TRAINING),
    ("anthropic-ai", "Anthropic", TRAINING),
    ("PerplexityBot", "Perplexity", SUCHE),
    ("Perplexity-User", "Perplexity", NUTZER),
    ("Google-Extended", "Google", STEUERUNG),
    ("Applebot-Extended", "Apple", STEUERUNG),
    ("CCBot", "Common Crawl", TRAINING),
    ("Bytespider", "ByteDance", TRAINING),
    ("Meta-ExternalAgent", "Meta", TRAINING),
    ("FacebookBot", "Meta", TRAINING),
    ("cohere-ai", "Cohere", TRAINING),
]

SICHTBARKEITS_ZWECKE = {SUCHE, NUTZER}

# RFC 9309: Zeilen enden nur an CR/LF, nicht an Unicode-Trennern wie U+2028
_ZEILENENDE = re.compile(r"\r\n|\r|\n")


@dataclass
class Gruppe:
    agents: List[str] = field(default_factory=list)
    regeln: List[Tuple[str, str]] = field(
        default_factory=list
    )  # (allow|disallow, pfad)


def gruppen_lesen(robots_txt: str) -> List[Gruppe]:
    """robots.txt in Gruppen zerlegen. Aufeinanderfolgende User-agent-Zeilen = eine Gruppe."""
    gruppen: List[Gruppe] = []
    aktuell = None
    letzte_war_agent = False
    for roh in _ZEILENENDE.split((robots_txt or "").lstrip("﻿")):
        zeile = roh.split("#", 1)[0].strip()
        if not zeile or ":" not in zeile:
            continue
        feld, wert = (t.strip() for t in zeile.split(":", 1))
        feld = feld.lower()
        if feld == "user-agent":
            if aktuell is None or not letzte_war_agent:
                aktuell = Gruppe()
                gruppen.append(aktuell)
            aktuell.agents.append(wert)
            letzte_war_agent = True
            continue
        letzte_war_agent = False
        if aktuell is not None and feld in ("allow", "disallow"):
            aktuell.regeln.append((feld, wert))
    return gruppen


def regeln_fuer(gruppen: List[Gruppe], token: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Alle Gruppen, die den Token nennen; sonst alle ``*``-Gruppen (RFC 9309).

    Rueckgabe: ("eigen" | "stern" | "keine", Regeln).
    """
    t = token.lower()
    eigene = [g for g in gruppen if any(a.lower() == t for a in g.agents)]
    if eigene:
        return "eigen", [r for g in eigene for r in g.regeln]
    stern = [g for g in gruppen if "*" in g.agents]
    if stern:
        return "stern", [r for g in stern for r in g.regeln]
    return "keine", []


def _passt(muster: str, pfad: str) -> bool:
    verankert = muster.endswith("$")
    rumpf = muster[:-1] if verankert else muster
    regex = "^" + ".*".join(re.escape(teil) for teil in rumpf.split("*"))
    if verankert:
        regex += "$"
    return re.match(regex, pfad) is not None


def erlaubt(regeln: List[Tuple[str, str]], pfad: str = "/") -> bool:
    """Laengste passende Regel gewinnt, bei Gleichstand Allow; leeres Disallow erlaubt alles."""
    beste_laenge, ok = -1, True
    for art, wert in regeln:
        if art == "disallow" and wert == "":
            continue
        if _passt(wert, pfad):
            if len(wert) > beste_laenge or (
                len(wert) == beste_laenge and art == "allow"
            ):
                beste_laenge, ok = len(wert), art == "allow"
    return ok


def gesperrte_ki_crawler(robots_txt: str) -> Dict[str, List[str]]:
    """Welche KI-Crawler duerfen die Startseite nicht abrufen?

    Rueckgabe:
      ``sichtbarkeit``: Such-/Nutzer-Crawler mit eigener Sperre -> Fehler
      ``training``: Trainings-/Steuer-Token mit eigener Sperre -> Hinweis
      ``ueber_stern``: nur ueber ``User-agent: *`` gesperrt (Ursache ist die
                       Sternsperre, die ``wildcard_disallow`` schon meldet)
    """
    ergebnis: Dict[str, List[str]] = {
        "sichtbarkeit": [],
        "training": [],
        "ueber_stern": [],
    }
    if not robots_txt:
        return ergebnis
    gruppen = gruppen_lesen(robots_txt)
    for token, _anbieter, zweck in KI_CRAWLER:
        quelle, regeln = regeln_fuer(gruppen, token)
        if erlaubt(regeln, "/"):
            continue
        if quelle == "stern":
            ergebnis["ueber_stern"].append(token)
        elif zweck in SICHTBARKEITS_ZWECKE:
            ergebnis["sichtbarkeit"].append(token)
        else:
            ergebnis["training"].append(token)
    return ergebnis
