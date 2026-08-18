"""
Geschäftswert — was eine Seite dem Kunden tatsächlich einbringt.

Bis hierher priorisiert der Autopilot nach Besucherzahlen: Eine Seite mit 500
Besuchern gilt als wichtiger als eine mit 20. Für ein Unternehmen ist das
regelmäßig falsch herum. Zwanzig Besucher auf „Factoring für Speditionen"
können mehr wert sein als fünfhundert auf einem Ratgeberartikel, der nie zu
einer Anfrage führt.

Dieses Modul bringt den fehlenden Maßstab: **was ist eine Anfrage wert?**

Die wichtigste Regel steht am Anfang
------------------------------------

**Der Wert wird erfasst, nicht geschätzt.** Es gibt in diesem Modul keinen
Standardwert für eine Anfrage, keinen Branchendurchschnitt, keine Heuristik.
Fehlt die Zahl, sagt das Modul „unbekannt" und rechnet nicht. Eine erfundene
Zahl wäre schlimmer als keine: Sie sieht aus wie eine Tatsache, verteilt aber
den gesamten Arbeitsaufwand nach einem Bauchgefühl — und niemand könnte
hinterher nachvollziehen, warum das System eine Seite bevorzugt hat.

Woher die Zahl kommt
--------------------

Aus `projects.yaml`, je Projekt, unter `geschaeftswert`:

    geschaeftswert:
      waehrung: EUR
      ziele:
        - name: Kontaktanfrage
          seiten: ["/kontakt", "/finanzierung/factoring"]
          wert_pro_abschluss: 2500      # was ein gewonnener Kunde bringt
          abschlussquote: 0.15          # wie viele Anfragen zu Kunden werden

Aus beidem ergibt sich der Wert einer einzelnen Anfrage (hier 375 EUR). Beide
Angaben muss der Kunde liefern; die Abschlussquote kennt jeder Vertrieb aus
Erfahrung, und sie ist ehrlicher als ein geschätzter „Lead-Wert".

Was das Modul daraus macht
--------------------------

* **Wert je Seite** — Besucher × Anfragequote × Wert je Anfrage.
* **Verschenktes Geld** — Seiten mit vielen Besuchern, aber ohne Anfragen.
* **Unterschätzte Seiten** — wenige Besucher, aber hoher Wert je Besucher.

Diese drei Listen sind die Eingangsgröße für den Chancen-Motor (Phase 4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ohne so viele Besucher im Zeitraum ist jede Quote Zufall.
MIN_BESUCHER = 30

# Ab dieser Besucherzahl ohne eine einzige Anfrage lohnt der Blick.
MIN_BESUCHER_FUER_LEERLAUF = 50

# Anteil am besten Wert-je-Besucher, ab dem eine Seite als unterschaetzt gilt.
UNTERSCHAETZT_ANTEIL = 0.5


@dataclass
class Ziel:
    """Ein bezifferbares Ziel einer Website (Anfrage, Buchung, Kauf)."""

    name: str
    wert_pro_abschluss: Optional[float] = None
    abschlussquote: Optional[float] = None
    seiten: List[str] = field(default_factory=list)

    @property
    def vollstaendig(self) -> bool:
        """Reichen die Angaben, um zu rechnen?

        Beide Werte müssen da und positiv sein. Eine Abschlussquote von 0 wäre
        rechnerisch gültig, macht aber jede Seite wertlos — das ist fast immer
        ein Eingabefehler und keine Aussage.
        """
        return (
            self.wert_pro_abschluss is not None
            and self.abschlussquote is not None
            and self.wert_pro_abschluss > 0
            and 0 < self.abschlussquote <= 1
        )

    @property
    def wert_je_anfrage(self) -> Optional[float]:
        """Was eine einzelne Anfrage im Schnitt einbringt."""
        if not self.vollstaendig:
            return None
        return round(self.wert_pro_abschluss * self.abschlussquote, 2)

    def gilt_fuer(self, url_pfad: str) -> bool:
        """Zählt diese Seite auf dieses Ziel ein?

        Ohne Seitenliste gilt das Ziel für die ganze Website — der Normalfall
        bei kleinen Seiten, wo jede Seite auf dasselbe Kontaktformular führt.
        """
        if not self.seiten:
            return True
        return any(url_pfad.startswith(muster) for muster in self.seiten)


@dataclass
class SeitenWert:
    """Was eine einzelne Seite einbringt — oder einbringen könnte."""

    url: str
    besucher: int = 0
    anfragen: int = 0
    ziel_name: str = ""
    wert_je_anfrage: Optional[float] = None
    waehrung: str = "EUR"

    @property
    def anfragequote(self) -> Optional[float]:
        """Anteil der Besucher, der zu einer Anfrage wird."""
        if self.besucher < MIN_BESUCHER:
            return None
        return round(self.anfragen / self.besucher, 4)

    @property
    def wert(self) -> Optional[float]:
        """Was die Seite im Zeitraum eingebracht hat."""
        if self.wert_je_anfrage is None:
            return None
        return round(self.anfragen * self.wert_je_anfrage, 2)

    @property
    def wert_je_besucher(self) -> Optional[float]:
        """Der eigentliche Vergleichsmaßstab zwischen Seiten."""
        if self.wert is None or self.besucher < MIN_BESUCHER:
            return None
        return round(self.wert / self.besucher, 2)

    @property
    def bezifferbar(self) -> bool:
        return self.wert_je_anfrage is not None


# ---------------------------------------------------------------------------
# Konfiguration lesen
# ---------------------------------------------------------------------------


def lies_ziele(projekt: Dict[str, Any]) -> Tuple[List[Ziel], str]:
    """Liest die Ziele eines Projekts aus seiner Konfiguration.

    Rückgabe: (Ziele, Währung). Fehlt der Abschnitt komplett, kommt eine leere
    Liste zurück — das ist der Normalzustand, solange der Kunde die Zahlen
    nicht geliefert hat, und ausdrücklich kein Fehler.
    """
    abschnitt = projekt.get("geschaeftswert") or {}
    waehrung = str(abschnitt.get("waehrung") or "EUR")

    ziele: List[Ziel] = []
    for eintrag in abschnitt.get("ziele") or []:
        if not isinstance(eintrag, dict):
            continue
        try:
            wert = eintrag.get("wert_pro_abschluss")
            quote = eintrag.get("abschlussquote")
            ziele.append(
                Ziel(
                    name=str(eintrag.get("name") or "Anfrage"),
                    wert_pro_abschluss=float(wert) if wert is not None else None,
                    abschlussquote=float(quote) if quote is not None else None,
                    seiten=[str(s) for s in (eintrag.get("seiten") or [])],
                )
            )
        except (TypeError, ValueError) as exc:
            # Eine kaputte Zahl darf nicht als 0 durchrutschen: Das ergäbe
            # lautlos einen Wert von null statt eines sichtbaren Hinweises.
            logger.warning(
                f"[wert] Ziel {eintrag.get('name')!r} hat unlesbare Zahlen "
                f"({exc}) — wird als unvollständig behandelt."
            )
            ziele.append(Ziel(name=str(eintrag.get("name") or "Anfrage")))

    return ziele, waehrung


def fehlende_angaben(projekte: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    """Bei welchen Projekten fehlt der Geschäftswert noch.

    Das ist die Arbeitsliste für das Kundengespräch — und gleichzeitig die
    ehrliche Antwort darauf, warum der Chancen-Motor für ein Projekt noch nicht
    nach Umsatz priorisieren kann.
    """
    offen = []
    for pid, projekt in (projekte or {}).items():
        if not (projekt or {}).get("enabled", True):
            continue
        ziele, _ = lies_ziele(projekt or {})
        if not ziele:
            offen.append(
                {
                    "projekt": pid,
                    "grund": "kein Abschnitt 'geschaeftswert' in projects.yaml",
                    "fehlt": "Ziele, Wert je Abschluss, Abschlussquote",
                }
            )
            continue
        unvollstaendig = [z for z in ziele if not z.vollstaendig]
        if unvollstaendig:
            offen.append(
                {
                    "projekt": pid,
                    "grund": f"{len(unvollstaendig)} von {len(ziele)} Ziel(en) "
                    "ohne belastbare Zahlen",
                    "fehlt": ", ".join(_was_fehlt(z) for z in unvollstaendig),
                }
            )
    return offen


def _was_fehlt(ziel: Ziel) -> str:
    teile = []
    if ziel.wert_pro_abschluss is None or ziel.wert_pro_abschluss <= 0:
        teile.append("Wert je Abschluss")
    if ziel.abschlussquote is None or not (0 < ziel.abschlussquote <= 1):
        teile.append("Abschlussquote")
    return f"{ziel.name} ({', '.join(teile) or 'unklar'})"


# ---------------------------------------------------------------------------
# Bewerten
# ---------------------------------------------------------------------------


def _pfad_von(url: str) -> str:
    """Der Pfadanteil einer Adresse — Zielzuordnung arbeitet auf Pfaden."""
    if "://" not in url:
        return url if url.startswith("/") else "/" + url
    rest = url.split("://", 1)[1]
    schnitt = rest.find("/")
    return rest[schnitt:] if schnitt >= 0 else "/"


def bewerte_seiten(
    seiten: Iterable[Dict[str, Any]],
    ziele: List[Ziel],
    waehrung: str = "EUR",
) -> List[SeitenWert]:
    """Rechnet je Seite aus, was sie einbringt.

    `seiten` sind Rohdaten aus Analytics/Search Console:
    `{"url": ..., "besucher": int, "anfragen": int}`.

    Seiten, für die kein bezifferbares Ziel greift, kommen mit `wert = None`
    zurück statt mit 0. Der Unterschied ist wesentlich: „bringt nichts" und
    „wissen wir nicht" dürfen nie dasselbe Ergebnis liefern.
    """
    bewertet: List[SeitenWert] = []
    nutzbare = [z for z in ziele if z.vollstaendig]

    for eintrag in seiten:
        url = str(eintrag.get("url") or "")
        if not url:
            continue
        pfad = _pfad_von(url)
        treffer = next((z for z in nutzbare if z.gilt_fuer(pfad)), None)
        bewertet.append(
            SeitenWert(
                url=url,
                besucher=int(eintrag.get("besucher") or 0),
                anfragen=int(eintrag.get("anfragen") or 0),
                ziel_name=treffer.name if treffer else "",
                wert_je_anfrage=treffer.wert_je_anfrage if treffer else None,
                waehrung=waehrung,
            )
        )

    return bewertet


def verschenktes_geld(bewertet: Iterable[SeitenWert]) -> List[SeitenWert]:
    """Seiten mit vielen Besuchern, aber ohne eine einzige Anfrage.

    Der teuerste Zustand überhaupt: Die Sichtbarkeit ist schon da und bezahlt,
    sie führt nur zu nichts. Solche Seiten brauchen keinen SEO-Eingriff,
    sondern meist einen klaren nächsten Schritt auf der Seite selbst.
    """
    treffer = [
        s
        for s in bewertet
        if s.bezifferbar
        and s.anfragen == 0
        and s.besucher >= MIN_BESUCHER_FUER_LEERLAUF
    ]
    return sorted(treffer, key=lambda s: s.besucher, reverse=True)


def unterschaetzte_seiten(bewertet: Iterable[SeitenWert]) -> List[SeitenWert]:
    """Wenige Besucher, aber überdurchschnittlich wertvoll.

    Genau hier lohnt zusätzliche Sichtbarkeit am meisten — und genau diese
    Seiten übersieht jede Priorisierung nach Besucherzahl.
    """
    mit_wert = [
        s for s in bewertet if s.wert_je_besucher is not None and s.wert_je_besucher > 0
    ]
    if not mit_wert:
        return []

    bester = max(s.wert_je_besucher for s in mit_wert)
    schwelle = bester * UNTERSCHAETZT_ANTEIL
    schnitt_besucher = sum(s.besucher for s in mit_wert) / len(mit_wert)

    treffer = [
        s
        for s in mit_wert
        if s.wert_je_besucher >= schwelle and s.besucher < schnitt_besucher
    ]
    return sorted(treffer, key=lambda s: s.wert_je_besucher or 0, reverse=True)


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


def _geld(betrag: Optional[float], waehrung: str = "EUR") -> str:
    """Deutsche Schreibweise: Punkt als Tausender-, Komma als Dezimaltrenner.

    Der Zwischenschritt über den Platzhalter ist nötig, weil Python englisch
    formatiert (1,500.00) und ein direktes Ersetzen beide Zeichen zu Punkten
    machen würde — aus 1.500,00 EUR würde 1.500.00 EUR.
    """
    if betrag is None:
        return "—"
    roh = f"{betrag:,.2f}"
    return (
        f"{roh.replace(',', '\x00').replace('.', ',').replace('\x00', '.')} {waehrung}"
    )


def _prozent(anteil: Optional[float]) -> str:
    """Anteil in deutscher Schreibweise, mit Komma."""
    if anteil is None:
        return "—"
    return f"{anteil * 100:.1f}".replace(".", ",") + " %"


def als_text(
    bewertet: List[SeitenWert],
    offene_angaben: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Der Geschäftswert je Seite als lesbarer Bericht."""
    zeilen: List[str] = []

    bezifferbar = [s for s in bewertet if s.bezifferbar]

    if not bezifferbar:
        zeilen.append(
            "Geschäftswert: für keine Seite bezifferbar.\n"
            "\n"
            "Das ist kein Fehler, sondern eine fehlende Angabe: Solange nicht\n"
            "hinterlegt ist, was eine Anfrage wert ist, priorisiert der\n"
            "Autopilot nach Besucherzahlen statt nach Umsatz."
        )
        if offene_angaben:
            zeilen.append("\nOffen je Projekt:")
            for eintrag in offene_angaben:
                zeilen.append(f"  {eintrag['projekt']}: {eintrag['grund']}")
                zeilen.append(f"      fehlt: {eintrag['fehlt']}")
        zeilen.append(
            "\nEinzutragen in projects.yaml unter 'geschaeftswert' — die Zahlen\n"
            "kommen vom Kunden, sie werden bewusst nicht geschätzt."
        )
        return "\n".join(zeilen)

    waehrung = bezifferbar[0].waehrung
    gesamt = sum(s.wert or 0 for s in bezifferbar)

    # Kein einziger Abschluss auf ALLEN bezifferbaren Seiten heisst fast nie
    # "niemand fragt an", sondern "Anfragen werden nicht gezaehlt". Ohne
    # diesen Hinweis liest sich der Bericht wie ein vernichtendes Ergebnis,
    # obwohl schlicht die Messung fehlt — genau der stille Ausfall, der dieses
    # Projekt schon mehrfach Monate gekostet hat.
    if sum(s.anfragen for s in bezifferbar) == 0:
        zeilen.append(
            "⚠ Auf keiner Seite wurde eine Anfrage gezählt. Das bedeutet fast\n"
            "  immer, dass die Anfragen-Erfassung fehlt — nicht, dass niemand\n"
            "  anfragt. Solange sie fehlt, bleibt jeder Wert unten 0,00, und\n"
            "  nur die Besucherzahlen sind belastbar.\n"
        )

    zeilen.append(
        f"Geschäftswert — {len(bezifferbar)} bezifferbare Seite(n), "
        f"Summe {_geld(gesamt, waehrung)}."
    )
    zeilen.append("")

    nach_wert = sorted(bezifferbar, key=lambda s: s.wert or 0, reverse=True)
    for s in nach_wert[:15]:
        quote = _prozent(s.anfragequote)
        zeilen.append(
            f"  {_geld(s.wert, s.waehrung):>14}  "
            f"{s.besucher:>6} Besucher  {s.anfragen:>4} Anfragen  "
            f"Quote {quote:>6}  {s.url}"
        )

    leerlauf = verschenktes_geld(bezifferbar)
    if leerlauf:
        zeilen.append("")
        zeilen.append(
            f"Besucher ohne Anfragen ({len(leerlauf)} Seite(n)) — "
            "Sichtbarkeit ist da, sie führt nur zu nichts:"
        )
        for s in leerlauf[:5]:
            zeilen.append(f"  {s.besucher:>6} Besucher, 0 Anfragen  {s.url}")

    unterschaetzt = unterschaetzte_seiten(bezifferbar)
    if unterschaetzt:
        zeilen.append("")
        zeilen.append(
            f"Unterschätzt ({len(unterschaetzt)} Seite(n)) — wenig Besucher, "
            "hoher Wert je Besucher. Hier lohnt Sichtbarkeit am meisten:"
        )
        for s in unterschaetzt[:5]:
            zeilen.append(
                f"  {_geld(s.wert_je_besucher, s.waehrung):>12} je Besucher  "
                f"({s.besucher} Besucher)  {s.url}"
            )

    unbezifferbar = [s for s in bewertet if not s.bezifferbar]
    if unbezifferbar:
        zeilen.append("")
        zeilen.append(
            f"{len(unbezifferbar)} Seite(n) ohne zugeordnetes Ziel — "
            "nicht bewertet (nicht: bewertet mit null)."
        )

    return "\n".join(zeilen)
