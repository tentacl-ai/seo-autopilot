"""
Chancen-Motor — welche Maßnahme lohnt sich als Nächstes.

Bis hierher liefert der Autopilot eine Liste von Befunden, sortiert nach
Schweregrad. Das beantwortet aber nicht die Frage, die tatsächlich gestellt
wird: *Womit fange ich montags an?* Ein „kritischer" Befund auf einer Seite,
die niemand besucht, ist weniger wert als ein „mittlerer" auf der Seite, über
die die Anfragen kommen.

Die Rechnung
------------

    Chance = Geschäftswert × Potenzial × Sicherheit ÷ Aufwand

* **Geschäftswert** — was die Seite einbringt (Phase 3). Fehlt die Angabe,
  wird ersatzweise die Sichtbarkeit herangezogen, und das Ergebnis wird
  ausdrücklich als *nach Besuchern statt nach Umsatz* gekennzeichnet.
* **Potenzial** — wie viel an dieser Stelle überhaupt zu holen ist. Position 8
  auf Seite 1 hat mehr Luft nach oben als Position 2, und Position 60 ist
  realistisch unerreichbar.
* **Sicherheit** — wie oft dieser Eingriff früher gewirkt hat. Genau hier
  zahlt sich die Wirkungsmessung aus: Statt zu glauben, dass Titeländerungen
  helfen, steht in der Bilanz, ob sie es bei uns getan haben.
* **Aufwand** — grobe Einordnung je Befundtyp.

Warum keine erfundene Genauigkeit
---------------------------------

Die Zahl am Ende ist eine **Rangfolge, keine Prognose**. Sie sagt „das hier
zuerst", nicht „das bringt 1.240 EUR". Deshalb gibt es hier bewusst keine
Euro-Vorhersage: Die Eingangsgrößen sind zu grob, und eine ausgerechnete
Euro-Summe würde eine Sicherheit vortäuschen, die keine der vier Größen hat.

Ohne Erfahrungswerte aus der Wirkungsmessung startet jeder Befundtyp bei einer
neutralen Sicherheit. Das ist ehrlicher als ein geschätzter Erfolgswert — und
korrigiert sich von selbst, sobald die ersten Messungen vorliegen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .wirkung import URTEIL_VERBESSERT, bilanz

logger = logging.getLogger(__name__)

# Aufwand je Befundtyp, grob in drei Stufen. Der Wert ist ein Teiler:
# je größer, desto teurer die Maßnahme.
AUFWAND_KLEIN = 1.0  # Textfeld ändern
AUFWAND_MITTEL = 2.5  # Inhalt überarbeiten, Bild ersetzen
AUFWAND_GROSS = 5.0  # Seite neu bauen, Struktur ändern

AUFWAND_JE_TYP: Dict[str, float] = {
    "missing_title": AUFWAND_KLEIN,
    "short_title": AUFWAND_KLEIN,
    "long_title": AUFWAND_KLEIN,
    "missing_meta_description": AUFWAND_KLEIN,
    "short_meta_description": AUFWAND_KLEIN,
    "long_meta_description": AUFWAND_KLEIN,
    "missing_canonical": AUFWAND_KLEIN,
    "images_without_alt": AUFWAND_KLEIN,
    "missing_og_image": AUFWAND_MITTEL,
    "image_lcp_lazy_loaded": AUFWAND_KLEIN,
    "image_too_large": AUFWAND_MITTEL,
    "missing_organization_schema": AUFWAND_MITTEL,
    "thin_content": AUFWAND_GROSS,
    "near_duplicate": AUFWAND_GROSS,
    "orphan_page": AUFWAND_MITTEL,
    "unreachable_page": AUFWAND_MITTEL,
    "high_bounce_page": AUFWAND_GROSS,
    "slow_page": AUFWAND_GROSS,
}
AUFWAND_UNBEKANNT = AUFWAND_MITTEL

# Sicherheit ohne Erfahrungswerte. Bewusst in der Mitte: Wir wissen es nicht.
SICHERHEIT_NEUTRAL = 0.5

# Ab so vielen belastbaren Messungen wird die eigene Trefferquote verwendet.
MIN_MESSUNGEN_FUER_ERFAHRUNG = 3

# Höchstens so viele Befunde derselben Seite in der Vorschlagsliste.
MAX_JE_SEITE = 2

# Positionsfenster mit dem größten realistischen Hebel.
POTENZIAL_JE_POSITION = (
    (1.0, 3.0, 0.2),  # schon vorn — wenig Luft
    (3.0, 10.0, 1.0),  # Seite 1 unten: der größte Hebel
    (10.0, 20.0, 0.7),  # Seite 2: erreichbar
    (20.0, 50.0, 0.3),  # weit hinten: teuer
)
POTENZIAL_SEHR_WEIT_HINTEN = 0.1
POTENZIAL_OHNE_POSITION = 0.4

# Pflichtseiten ohne Verkaufsfunktion. Sie ranken oft gut (der Firmenname
# steht drauf), bringen aber keine Anfragen — wer dort optimiert, verbessert
# eine Kennzahl und nicht das Geschäft. Das ist keine Schätzung, sondern eine
# Aussage über den Zweck dieser Seiten. Sobald ein echter Geschäftswert
# hinterlegt ist (Phase 3), regelt der die Gewichtung ohnehin selbst.
PFLICHTSEITEN = (
    "/impressum",
    "/datenschutz",
    "/privacy",
    "/agb",
    "/nutzungsbedingungen",
    "/widerruf",
    "/cookie",
)
PFLICHTSEITEN_DAEMPFER = 0.1


def ist_pflichtseite(url: str) -> bool:
    """Rechtlich nötige Seite ohne Verkaufsfunktion?"""
    pfad = url.lower()
    if "://" in pfad:
        rest = pfad.split("://", 1)[1]
        schnitt = rest.find("/")
        pfad = rest[schnitt:] if schnitt >= 0 else "/"
    return any(pfad.startswith(m) for m in PFLICHTSEITEN)


@dataclass
class Chance:
    """Eine bewertete Maßnahme."""

    projekt: str
    url: str
    issue_type: str
    titel: str
    punkte: float
    geschaeftswert: Optional[float] = None
    besucher: int = 0
    position: Optional[float] = None
    sicherheit: float = SICHERHEIT_NEUTRAL
    sicherheit_belegt: bool = False
    aufwand: float = AUFWAND_UNBEKANNT
    nach_umsatz: bool = False
    begruendung: str = ""

    @property
    def aufwand_klartext(self) -> str:
        if self.aufwand <= AUFWAND_KLEIN:
            return "klein"
        if self.aufwand <= AUFWAND_MITTEL:
            return "mittel"
        return "groß"


def potenzial(position: Optional[float]) -> float:
    """Wie viel an dieser Position realistisch zu holen ist.

    Kein linearer Verlauf: Von Platz 8 auf Platz 3 ist ein großer Sprung in
    Klicks und gut erreichbar. Von Platz 2 auf 1 ist wenig Luft, von Platz 60
    nach vorn ist meist eine andere Baustelle als ein Titel.
    """
    if position is None or position <= 0:
        return POTENZIAL_OHNE_POSITION
    for von, bis, wert in POTENZIAL_JE_POSITION:
        if von <= position < bis:
            return wert
    return POTENZIAL_SEHR_WEIT_HINTEN


def sicherheit_je_typ(
    db_pfad: str,
    aktion_je_typ: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    """Erfahrungswerte aus der Wirkungsmessung — was hat bei uns gewirkt.

    Liefert je Änderungsart die Trefferquote. Typen mit zu wenigen belastbaren
    Messungen tauchen NICHT auf; der Aufrufer fällt dann auf
    `SICHERHEIT_NEUTRAL` zurück, statt eine Quote aus zwei Messungen zu
    verwenden.
    """
    werte: Dict[str, float] = {}
    try:
        zeilen = bilanz(db_pfad)
    except Exception as exc:  # pragma: no cover - defensiv
        logger.debug(f"[chancen] Bilanz nicht lesbar: {exc}")
        return werte

    for zeile in zeilen:
        belastbar = int(zeile.get("belastbar") or 0)
        quote = zeile.get("trefferquote")
        if belastbar >= MIN_MESSUNGEN_FUER_ERFAHRUNG and quote is not None:
            werte[str(zeile.get("aktion"))] = float(quote)
    return werte


def _wert_und_besucher(
    url: str, seitenwerte: Dict[str, Any]
) -> tuple[Optional[float], int]:
    eintrag = seitenwerte.get(url) or seitenwerte.get(url.rstrip("/")) or {}
    return eintrag.get("wert"), int(eintrag.get("besucher") or 0)


def bewerte_chancen(
    befunde: Iterable[Dict[str, Any]],
    projekt: str,
    db_pfad: Optional[str] = None,
    seitenwerte: Optional[Dict[str, Any]] = None,
    aktion_je_typ: Optional[Dict[str, str]] = None,
) -> List[Chance]:
    """Bewertet Befunde und gibt sie nach Lohnenswertigkeit sortiert zurück.

    `befunde` sind Audit-Befunde: `{"type", "title", "url", "position",
    "besucher"}`. `seitenwerte` ordnet einer Adresse ihren Geschäftswert zu
    (aus Phase 3); fehlt sie, wird nach Sichtbarkeit gewichtet und jede Chance
    als `nach_umsatz=False` gekennzeichnet.
    """
    from .changelog_book import AKTION_JE_ISSUE_TYPE

    aktion_je_typ = aktion_je_typ or AKTION_JE_ISSUE_TYPE
    seitenwerte = seitenwerte or {}
    erfahrung = sicherheit_je_typ(db_pfad) if db_pfad else {}

    chancen: List[Chance] = []
    for befund in befunde:
        typ = str(befund.get("type") or "")
        url = str(befund.get("url") or "")
        if not typ:
            continue

        position = befund.get("position")
        position = float(position) if position not in (None, "") else None

        wert, besucher = _wert_und_besucher(url, seitenwerte)
        if besucher == 0:
            besucher = int(befund.get("besucher") or 0)

        # Geschaeftswert wenn vorhanden, sonst Sichtbarkeit als Ersatzmassstab.
        nach_umsatz = wert is not None and wert > 0
        gewicht = wert if nach_umsatz else float(max(besucher, 1))

        aktion = aktion_je_typ.get(typ, "sonstiges")
        quote = erfahrung.get(aktion)
        sicherheit = quote if quote is not None else SICHERHEIT_NEUTRAL

        aufwand = AUFWAND_JE_TYP.get(typ, AUFWAND_UNBEKANNT)
        punkte = (gewicht * potenzial(position) * sicherheit) / aufwand

        # Ohne hinterlegten Geschäftswert würde eine gut rankende Pflichtseite
        # die Liste anführen — beim ersten Live-Lauf belegte das Impressum
        # fünf von sechs Plätzen. Mit echtem Geschäftswert ist der Dämpfer
        # überflüssig, weil die Seite dann von selbst bei null landet.
        pflichtseite = not nach_umsatz and ist_pflichtseite(url)
        if pflichtseite:
            punkte *= PFLICHTSEITEN_DAEMPFER

        massstab = "Umsatz" if nach_umsatz else f"{besucher} Einblendungen"
        if pflichtseite:
            massstab += ", Pflichtseite ohne Verkaufsfunktion"
        beleg = (
            f"Trefferquote {quote:.0%} aus eigenen Messungen"
            if quote is not None
            else "noch keine Erfahrungswerte"
        )
        chancen.append(
            Chance(
                projekt=projekt,
                url=url,
                issue_type=typ,
                titel=str(befund.get("title") or typ),
                punkte=round(punkte, 2),
                geschaeftswert=wert,
                besucher=besucher,
                position=position,
                sicherheit=sicherheit,
                sicherheit_belegt=quote is not None,
                aufwand=aufwand,
                nach_umsatz=nach_umsatz,
                begruendung=f"Maßstab: {massstab} · {beleg}",
            )
        )

    chancen.sort(key=lambda c: c.punkte, reverse=True)
    return chancen


def als_text(chancen: List[Chance], anzahl: int = 10) -> str:
    """Die lohnendsten Maßnahmen als lesbare Liste."""
    if not chancen:
        return "Keine bewertbaren Chancen gefunden."

    zeilen: List[str] = []
    ohne_umsatz = [c for c in chancen if not c.nach_umsatz]
    ohne_erfahrung = [c for c in chancen if not c.sicherheit_belegt]

    zeilen.append(f"Die {min(anzahl, len(chancen))} lohnendsten Maßnahmen")

    if len(ohne_umsatz) == len(chancen):
        zeilen.append(
            "⚠ Gewichtet nach Besucherzahlen, NICHT nach Umsatz — für keine\n"
            "  Seite ist ein Geschäftswert hinterlegt. Die Reihenfolge kann\n"
            "  sich deutlich ändern, sobald die Zahlen vorliegen."
        )
    if len(ohne_erfahrung) == len(chancen):
        zeilen.append(
            "⚠ Noch keine Erfahrungswerte aus der Wirkungsmessung — jeder\n"
            "  Eingriff wird gleich sicher bewertet. Das schärft sich mit den\n"
            "  ersten Messergebnissen von selbst."
        )
    zeilen.append("")

    # Eine Seite mit zwölf Befunden würde sonst die ganze Liste füllen und
    # neun andere Seiten verdrängen. Zwei Einträge je Seite reichen, um zu
    # zeigen, dass dort etwas zu tun ist.
    gefiltert: List[Chance] = []
    je_seite: Dict[str, int] = {}
    verdraengt = 0
    for c in chancen:
        anzahl_seite = je_seite.get(c.url, 0)
        if anzahl_seite >= MAX_JE_SEITE:
            verdraengt += 1
            continue
        je_seite[c.url] = anzahl_seite + 1
        gefiltert.append(c)

    for i, c in enumerate(gefiltert[:anzahl], 1):
        pos = "—" if c.position is None else f"Pos {c.position:.1f}"
        zeilen.append(
            f"{i:>2}. [{c.punkte:>8.2f}] {c.titel}  ({pos}, Aufwand {c.aufwand_klartext})"
        )
        zeilen.append(f"      {c.url}")
        zeilen.append(f"      {c.begruendung}")

    if verdraengt:
        zeilen.append("")
        zeilen.append(
            f"({verdraengt} weitere Befund(e) auf denselben Seiten — höchstens "
            f"{MAX_JE_SEITE} je Seite, damit eine Seite die Liste nicht füllt.)"
        )

    return "\n".join(zeilen)
