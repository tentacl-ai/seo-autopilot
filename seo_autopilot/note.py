"""
Die Note (0-100) einer Website — nach Ursache, nicht nach Menge.

Bis v1.15 (``berechne_note_alt``)::

    note = 100 - min(50; 3 * high * f) - min(30; 1 * medium * f) - min(20; 0,3 * low * f)
    f    = 15 / geprueft_seiten   (ohne bekannte Seitenzahl: f = 1)

Jeder Befund zaehlte einzeln und gleich schwer. Drei Verzerrungen
(Rundumschlag 18.09.2026):

1. Eine Ursache zaehlte vielfach: EINE fehlende Navigation erzeugte bei
   coaching-beispiel 17x ``unreachable_page`` (high) und kostete ~19 Punkte.
2. Dieselbe Adresse stand als ``unreachable_page`` UND ``orphan_page`` in der
   Rechnung, /hilfe vs. /en/help-center als Doppelung UND Kannibalisierung.
3. Faustregeln (GEO-Stil, Titellaenge, duenner Text auf Kontaktseiten ...)
   wogen so viel wie echte Fehler.

Seit v1.16 (``berechne_note``)::

    1. Doppelte Ursachen raus: Befunde derselben Ursachen-Familie an derselben
       Adresse (befund_arten.URSACHEN_FAMILIEN) zaehlen nur einmal, der schwerste.
    2. Gruppen bilden: je (Befundtyp, Schwere) eine Gruppe mit n Befunden.
       Befunde ohne Typ sind je eine eigene Gruppe (keine Ursache erkennbar).
    3. Menge je Gruppe, normiert auf die Seitenzahl wie bisher:
           x     = n * f                      (f = 15 / Seitenzahl, sonst 1)
           menge = x            fuer x <= 1   (Domain-Befund auf grosser Website: wie bisher abgeschwaecht)
           menge = 1 + log2(x)  fuer x  > 1   (abnehmender Grenznutzen)
       17 gleiche Befunde auf 15 Seiten zaehlen damit wie ~5, nicht wie 17.
    4. Abzug je Gruppe = Gewicht(Schwere) * menge   (high 3, medium 1, low 0,3;
       critical wie high, info 0).
    5. Fehler (befund_arten: ``fehler``) werden je Schwere gedeckelt wie
       bisher: high 50, medium 30, low 20.
       Empfehlungen zaehlen nur zu einem Drittel und zusammen hoechstens 10.

    note = 100 - min(50; H) - min(30; M) - min(20; L) - min(10; E / 3)

Ohne Typen (alte Testdaten) und bei n <= 15/f je Typ ergibt sich exakt die
alte Rechnung — die Normierung auf die Seitenzahl bleibt erhalten, weil sie
sich bewaehrt hat (Vergleichbarkeit ueber Laeufe und Websitegroessen).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .befund_arten import (
    DOMAINWEITE_FAMILIEN,
    EMPFEHLUNG,
    HINWEIS,
    art_von,
    familie_von,
)

REFERENZ_SEITEN = 15

GEWICHTE = {"critical": 3.0, "high": 3.0, "medium": 1.0, "low": 0.3, "info": 0.0}
# critical wird wie high gedeckelt, info zaehlt nicht.
_STUFE = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}
DECKEL_FEHLER = {"high": 50.0, "medium": 30.0, "low": 20.0}
DECKEL_EMPFEHLUNG = 10.0
EMPFEHLUNG_FAKTOR = 1.0 / 3.0


@dataclass
class NotenErgebnis:
    note: float
    abzuege: Dict[str, float] = field(default_factory=dict)
    gezaehlt: int = 0
    zusammengefasst: int = 0  # wegen gleicher Ursache nicht gezaehlt


def seitenfaktor(seiten: Optional[int]) -> float:
    return REFERENZ_SEITEN / seiten if seiten and seiten > 0 else 1.0


def menge(x: float) -> float:
    """Abnehmender Grenznutzen: linear bis 1, danach logarithmisch."""
    return x if x <= 1.0 else 1.0 + math.log2(x)


def _schwere(issue: Dict[str, Any]) -> str:
    return (issue.get("severity") or "low").lower()


def _url(issue: Dict[str, Any]) -> str:
    url = issue.get("affected_url")
    if not url:
        roh = issue.get("affected_items")
        try:
            daten = json.loads(roh) if isinstance(roh, str) else roh
        except (TypeError, ValueError):
            daten = None
        if isinstance(daten, dict):
            url = daten.get("url")
    return (url or "").split("#")[0].rstrip("/").lower()


def ohne_doppelte_ursachen(
    issues: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Je Ursachen-Familie und Adresse bleibt nur der schwerste Befund.

    Rueckgabe: (zaehlende Befunde, Anzahl zusammengefasster Befunde).
    """
    liste = list(issues)
    bester: Dict[Tuple[str, str], int] = {}
    for idx, issue in enumerate(liste):
        fam = familie_von(issue.get("type"))
        if not fam:
            continue
        key = (fam, "" if fam in DOMAINWEITE_FAMILIEN else _url(issue))
        alt = bester.get(key)
        if alt is None or GEWICHTE.get(_schwere(issue), 0) > GEWICHTE.get(
            _schwere(liste[alt]), 0
        ):
            bester[key] = idx
    behalten_idx = set(bester.values())
    zaehlend = [
        i
        for idx, i in enumerate(liste)
        if not familie_von(i.get("type")) or idx in behalten_idx
    ]
    return zaehlend, len(liste) - len(zaehlend)


def _gruppen(issues: List[Dict[str, Any]]) -> Dict[Any, Tuple[str, str, int]]:
    """(typ, schwere) -> (art, stufe, anzahl). Ohne Typ: jeder Befund eine Gruppe."""
    gruppen: Dict[Any, List] = {}
    for idx, issue in enumerate(issues):
        schwere = _schwere(issue)
        stufe = _STUFE.get(schwere)
        if stufe is None:  # info o. ae.
            continue
        typ = issue.get("type")
        key = (typ, schwere) if typ else ("__ohne_typ__", idx)
        eintrag = gruppen.setdefault(key, [art_von(typ), schwere, 0])
        eintrag[2] += 1
    return {k: tuple(v) for k, v in gruppen.items()}


def berechne_note(
    issues: Iterable[Dict[str, Any]], seiten: Optional[int] = None
) -> NotenErgebnis:
    """Neue Note (siehe Modul-Docstring)."""
    zaehlend, zusammengefasst = ohne_doppelte_ursachen(issues)
    f = seitenfaktor(seiten)
    roh = {"high": 0.0, "medium": 0.0, "low": 0.0, "empfehlung": 0.0}
    for art, schwere, anzahl in _gruppen(zaehlend).values():
        if art == HINWEIS:  # laut Google ohne Wirkung -> kein Abzug
            continue
        abzug = GEWICHTE[schwere] * menge(anzahl * f)
        if art == EMPFEHLUNG:
            roh["empfehlung"] += abzug
        else:
            roh[_STUFE[schwere]] += abzug
    abzuege = {s: min(DECKEL_FEHLER[s], roh[s]) for s in DECKEL_FEHLER}
    abzuege["empfehlung"] = min(
        DECKEL_EMPFEHLUNG, roh["empfehlung"] * EMPFEHLUNG_FAKTOR
    )
    note = max(0.0, round(100.0 - sum(abzuege.values()), 1))
    return NotenErgebnis(
        note=note,
        abzuege={k: round(v, 2) for k, v in abzuege.items()},
        gezaehlt=len(zaehlend),
        zusammengefasst=zusammengefasst,
    )


def berechne_note_alt(
    issues: Iterable[Dict[str, Any]], seiten: Optional[int] = None
) -> float:
    """Die Rechnung bis v1.15 — nur noch fuer Vergleiche (Nachweis alt -> neu)."""
    zaehler = {"high": 0, "medium": 0, "low": 0}
    for issue in issues:
        s = _schwere(issue)
        if s in zaehler:
            zaehler[s] += 1
    f = seitenfaktor(seiten)
    abzug = (
        min(50.0, 3.0 * zaehler["high"] * f)
        + min(30.0, 1.0 * zaehler["medium"] * f)
        + min(20.0, 0.3 * zaehler["low"] * f)
    )
    return max(0.0, round(100.0 - abzug, 1))
