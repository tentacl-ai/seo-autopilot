"""
Wettbewerbsvergleich — mit dem eigenen Crawler statt mit gekauften Daten.

Die naheliegende Antwort auf „wie steht der Kunde gegen den Wettbewerb da"
ist ein Datenanbieter wie DataForSEO. Für den eigentlichen Zweck braucht es
den aber nicht: Was ein Wettbewerber auf seinen Seiten tut — wie ausführlich
er schreibt, welche strukturierten Daten er auszeichnet, wie schnell die
Seiten laden, wie er Titel und Beschreibungen formuliert — steht öffentlich
im Quelltext und lässt sich mit dem vorhandenen Crawler ansehen.

Was hiermit NICHT geht, und warum das ehrlich benannt gehört:

* **Fremde Platzierungen.** Wer auf Platz 1 für einen Suchbegriff steht, sagt
  nur die Ergebnisseite von Google — und die abzugreifen verstößt gegen deren
  Nutzungsbedingungen. Für die *eigenen* Seiten liefert die Search Console
  ohnehin genauere Zahlen als jeder Drittanbieter.
* **Ein Backlink-Index.** Wer weltweit auf wen verlinkt, weiß nur, wer das
  halbe Web crawlt. Das ist ein eigenes Produkt, kein Modul.

Für beides bleibt ein Datenanbieter die richtige Wahl — nur eben als bewusste
Kaufentscheidung, nicht als Voraussetzung dafür, dass der Autopilot arbeitet.

Höflichkeit ist hier Pflicht
----------------------------

Auf eigenen Projekten crawlt der Autopilot Seiten, die dem Kunden gehören.
Hier sind es **fremde Server**. Deshalb gilt in diesem Modul, anders als im
normalen Audit:

* `robots.txt` wird gelesen und befolgt. Verbietet sie uns eine Adresse, wird
  sie nicht abgerufen — auch nicht „nur einmal zum Vergleich".
* Es werden wenige Seiten geholt (`MAX_SEITEN`), nicht die ganze Website.
* Der Bot nennt sich beim Namen (derselbe `USER_AGENT` wie sonst).

Verglichen wird bewusst nur Handwerk, das man sich ansehen darf: Umfang,
Auszeichnung, Tempo. Keine Inhalte übernehmen, keine Texte kopieren.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .sources.crawler import USER_AGENT, PageData, SEOCrawler

logger = logging.getLogger(__name__)

# Bewusst klein: Es geht um einen Eindruck vom Handwerk, nicht um eine
# vollständige Erfassung fremder Websites.
MAX_SEITEN = 10

# Ab dieser Wortzahl gilt eine Seite als inhaltlich ausgearbeitet.
WORTE_AUSGEARBEITET = 600

# Ab diesem Rückstand in Prozent ist ein Unterschied nennenswert.
DEUTLICH = 0.25


@dataclass
class Profil:
    """Was eine Website handwerklich zeigt — eigene oder fremde."""

    domain: str
    seiten: int = 0
    worte_schnitt: int = 0
    ausgearbeitete_seiten: int = 0
    mit_schema: int = 0
    schema_typen: List[str] = field(default_factory=list)
    mit_meta_description: int = 0
    titel_laenge_schnitt: int = 0
    erreichbar: bool = True
    hinweis: str = ""

    @property
    def anteil_ausgearbeitet(self) -> float:
        return self.ausgearbeitete_seiten / self.seiten if self.seiten else 0.0

    @property
    def anteil_schema(self) -> float:
        return self.mit_schema / self.seiten if self.seiten else 0.0

    @property
    def anteil_meta(self) -> float:
        return self.mit_meta_description / self.seiten if self.seiten else 0.0


@dataclass
class Vergleich:
    """Das Ergebnis: eigenes Profil gegen die Wettbewerber."""

    eigenes: Profil
    fremde: List[Profil] = field(default_factory=list)
    rueckstaende: List[str] = field(default_factory=list)
    vorspruenge: List[str] = field(default_factory=list)

    @property
    def belastbar(self) -> bool:
        """Reicht die Datenlage für eine Aussage?"""
        return self.eigenes.seiten > 0 and any(f.seiten > 0 for f in self.fremde)


# ---------------------------------------------------------------------------
# Höflichkeit
# ---------------------------------------------------------------------------


async def robots_erlaubt(domain: str, pfade: Iterable[str]) -> Tuple[List[str], int]:
    """Filtert Adressen anhand der robots.txt der fremden Domain.

    Rückgabe: (erlaubte Adressen, Anzahl der ausgeschlossenen).

    Ist die robots.txt nicht abrufbar, wird sie als „nichts verboten"
    behandelt — so verhalten sich Suchmaschinen auch. Ein Serverfehler auf
    fremder Seite darf den Vergleich nicht verhindern, ein ausdrückliches
    Verbot dagegen sehr wohl.
    """
    pfade = list(pfade)
    basis = f"{urlparse(domain).scheme or 'https'}://{urlparse(domain).netloc}"
    parser = RobotFileParser()

    try:
        async with httpx.AsyncClient(
            timeout=10.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            antwort = await client.get(urljoin(basis, "/robots.txt"))
        if antwort.status_code == 200:
            parser.parse(antwort.text.splitlines())
        else:
            logger.info(
                f"[wettbewerb] {basis}: robots.txt liefert "
                f"{antwort.status_code} — nichts verboten"
            )
            return (pfade, 0)
    except Exception as exc:
        logger.info(f"[wettbewerb] {basis}: robots.txt nicht abrufbar ({exc})")
        return (pfade, 0)

    erlaubt = [u for u in pfade if parser.can_fetch(USER_AGENT, u)]
    ausgeschlossen = len(pfade) - len(erlaubt)
    if ausgeschlossen:
        logger.info(
            f"[wettbewerb] {basis}: {ausgeschlossen} Adresse(n) laut robots.txt "
            "nicht erlaubt — werden nicht abgerufen"
        )
    return (erlaubt, ausgeschlossen)


# ---------------------------------------------------------------------------
# Profil erstellen
# ---------------------------------------------------------------------------


def _worte(seite: PageData) -> int:
    text = getattr(seite, "text_content", None) or ""
    if not text:
        return int(getattr(seite, "word_count", 0) or 0)
    return len(text.split())


def _schema_typen(seite: PageData) -> List[str]:
    roh = getattr(seite, "schema_types", None) or getattr(seite, "json_ld", None) or []
    typen: List[str] = []
    if isinstance(roh, list):
        for eintrag in roh:
            if isinstance(eintrag, str):
                typen.append(eintrag)
            elif isinstance(eintrag, dict):
                t = eintrag.get("@type")
                if isinstance(t, str):
                    typen.append(t)
                elif isinstance(t, list):
                    typen.extend(str(x) for x in t)
    return typen


def profil_aus_seiten(domain: str, seiten: List[PageData]) -> Profil:
    """Verdichtet gecrawlte Seiten zu einem Handwerks-Profil."""
    gute = [s for s in seiten if getattr(s, "status_code", 0) == 200]
    if not gute:
        return Profil(
            domain=domain,
            erreichbar=False,
            hinweis="Keine Seite abrufbar (Sperre, Fehler oder robots.txt).",
        )

    worte = [_worte(s) for s in gute]
    alle_typen: List[str] = []
    mit_schema = 0
    for s in gute:
        typen = _schema_typen(s)
        if typen:
            mit_schema += 1
            alle_typen.extend(typen)

    titel_laengen = [len(getattr(s, "title", "") or "") for s in gute]

    return Profil(
        domain=domain,
        seiten=len(gute),
        worte_schnitt=int(sum(worte) / len(worte)) if worte else 0,
        ausgearbeitete_seiten=sum(1 for w in worte if w >= WORTE_AUSGEARBEITET),
        mit_schema=mit_schema,
        schema_typen=sorted(set(alle_typen)),
        mit_meta_description=sum(
            1 for s in gute if (getattr(s, "meta_description", "") or "").strip()
        ),
        titel_laenge_schnitt=(
            int(sum(titel_laengen) / len(titel_laengen)) if titel_laengen else 0
        ),
    )


async def erfasse_domain(domain: str, max_seiten: int = MAX_SEITEN) -> Profil:
    """Crawlt wenige Seiten einer Domain — robots.txt wird befolgt."""
    try:
        async with SEOCrawler() as crawler:
            adressen = await crawler.discover_pages(domain, limit=max_seiten)
            if not adressen:
                return Profil(
                    domain=domain,
                    erreichbar=False,
                    hinweis="Keine Seiten gefunden (weder Sitemap noch Startseite).",
                )

            erlaubt, ausgeschlossen = await robots_erlaubt(domain, adressen)
            if not erlaubt:
                return Profil(
                    domain=domain,
                    erreichbar=False,
                    hinweis="robots.txt erlaubt keinen Abruf — wird respektiert.",
                )

            seiten = await crawler.crawl(erlaubt[:max_seiten])
    except Exception as exc:
        logger.warning(f"[wettbewerb] {domain} nicht erfassbar: {exc}")
        return Profil(domain=domain, erreichbar=False, hinweis=f"Fehler: {exc}")

    profil = profil_aus_seiten(domain, seiten)
    if ausgeschlossen:
        profil.hinweis = f"{ausgeschlossen} Adresse(n) laut robots.txt ausgelassen."
    return profil


# ---------------------------------------------------------------------------
# Vergleichen
# ---------------------------------------------------------------------------


def _bewerte(eigenes: Profil, fremde: List[Profil]) -> Tuple[List[str], List[str]]:
    """Wo liegt die eigene Seite zurück, wo vorn.

    Verglichen wird gegen den BESTEN Wettbewerber, nicht gegen den Schnitt:
    Der Kunde konkurriert um dieselben Plätze mit dem Stärksten, nicht mit dem
    Mittelmaß.
    """
    brauchbar = [f for f in fremde if f.seiten > 0]
    if not brauchbar or eigenes.seiten == 0:
        return ([], [])

    rueckstaende: List[str] = []
    vorspruenge: List[str] = []

    bester_umfang = max(f.worte_schnitt for f in brauchbar)
    if bester_umfang and eigenes.worte_schnitt < bester_umfang * (1 - DEUTLICH):
        fuehrend = max(brauchbar, key=lambda f: f.worte_schnitt)
        rueckstaende.append(
            f"Inhaltstiefe: {eigenes.worte_schnitt} Wörter je Seite gegen "
            f"{bester_umfang} bei {fuehrend.domain}."
        )
    elif bester_umfang and eigenes.worte_schnitt > bester_umfang * (1 + DEUTLICH):
        vorspruenge.append(
            f"Inhaltstiefe: {eigenes.worte_schnitt} Wörter je Seite — mehr als "
            f"jeder Wettbewerber ({bester_umfang})."
        )

    bester_schema = max(f.anteil_schema for f in brauchbar)
    if bester_schema > eigenes.anteil_schema + 0.2:
        fuehrend = max(brauchbar, key=lambda f: f.anteil_schema)
        rueckstaende.append(
            f"Strukturierte Daten: {eigenes.anteil_schema:.0%} der Seiten gegen "
            f"{bester_schema:.0%} bei {fuehrend.domain}."
        )
    elif eigenes.anteil_schema > bester_schema + 0.2:
        vorspruenge.append(
            f"Strukturierte Daten: {eigenes.anteil_schema:.0%} der Seiten — "
            f"mehr als jeder Wettbewerber ({bester_schema:.0%})."
        )

    fremde_typen = {t for f in brauchbar for t in f.schema_typen}
    fehlend = sorted(fremde_typen - set(eigenes.schema_typen))
    if fehlend:
        rueckstaende.append(
            "Auszeichnungen, die der Wettbewerb hat und wir nicht: "
            + ", ".join(fehlend[:6])
        )

    bester_meta = max(f.anteil_meta for f in brauchbar)
    if bester_meta > eigenes.anteil_meta + 0.2:
        rueckstaende.append(
            f"Meta-Beschreibungen: {eigenes.anteil_meta:.0%} der Seiten gegen "
            f"{bester_meta:.0%} beim Wettbewerb."
        )

    return (rueckstaende, vorspruenge)


async def vergleiche(
    eigene_domain: str,
    wettbewerber: List[str],
    max_seiten: int = MAX_SEITEN,
) -> Vergleich:
    """Stellt die eigene Website neben die genannten Wettbewerber."""
    if not wettbewerber:
        return Vergleich(eigenes=Profil(domain=eigene_domain))

    profile = await asyncio.gather(
        erfasse_domain(eigene_domain, max_seiten),
        *[erfasse_domain(w, max_seiten) for w in wettbewerber],
    )
    eigenes, fremde = profile[0], list(profile[1:])
    rueckstaende, vorspruenge = _bewerte(eigenes, fremde)
    return Vergleich(
        eigenes=eigenes,
        fremde=fremde,
        rueckstaende=rueckstaende,
        vorspruenge=vorspruenge,
    )


def wettbewerber_von(projekt: Dict[str, Any]) -> List[str]:
    """Die konfigurierten Wettbewerber eines Projekts.

    Wer die Wettbewerber sind, weiß der Kunde — das lässt sich ohne
    Ergebnisseiten-Daten nicht erraten, und geraten wäre es wertlos.
    """
    roh = (projekt or {}).get("wettbewerber") or []
    return [str(w).strip() for w in roh if str(w).strip()]


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


def als_text(vergleich: Vergleich) -> str:
    """Der Vergleich als lesbarer Bericht."""
    e = vergleich.eigenes

    if not vergleich.fremde:
        return (
            "Kein Wettbewerbsvergleich möglich: Für dieses Projekt sind keine\n"
            "Wettbewerber hinterlegt. In projects.yaml eintragen:\n"
            "\n"
            "  wettbewerber:\n"
            "    - https://beispiel-wettbewerber.de\n"
            "\n"
            "Wer die Wettbewerber sind, weiß der Kunde — raten wäre wertlos."
        )

    zeilen = [f"Wettbewerbsvergleich — {e.domain}", ""]

    if not e.erreichbar:
        zeilen.append(f"⚠ Eigene Seite nicht erfassbar: {e.hinweis}")
        return "\n".join(zeilen)

    zeilen.append(
        f"  {'eigene Seite':<34} {e.seiten:>3} Seiten  "
        f"{e.worte_schnitt:>5} Wörter Ø  "
        f"Schema {e.anteil_schema:>4.0%}  Meta {e.anteil_meta:>4.0%}"
    )
    for f in vergleich.fremde:
        if not f.erreichbar:
            zeilen.append(f"  {f.domain:<34} — {f.hinweis}")
            continue
        zeilen.append(
            f"  {f.domain:<34} {f.seiten:>3} Seiten  "
            f"{f.worte_schnitt:>5} Wörter Ø  "
            f"Schema {f.anteil_schema:>4.0%}  Meta {f.anteil_meta:>4.0%}"
        )

    if vergleich.rueckstaende:
        zeilen.append("")
        zeilen.append("Rückstand gegenüber dem stärksten Wettbewerber:")
        for r in vergleich.rueckstaende:
            zeilen.append(f"  • {r}")

    if vergleich.vorspruenge:
        zeilen.append("")
        zeilen.append("Vorsprung:")
        for v in vergleich.vorspruenge:
            zeilen.append(f"  • {v}")

    if not vergleich.rueckstaende and not vergleich.vorspruenge:
        zeilen.append("")
        zeilen.append("Kein deutlicher Unterschied im Handwerk erkennbar.")

    zeilen.append("")
    zeilen.append(
        "Hinweis: Verglichen wird, was öffentlich im Quelltext steht — Umfang,\n"
        "Auszeichnung, Formulierung. Platzierungen und Verlinkungen des\n"
        "Wettbewerbs sind damit NICHT messbar; dafür bräuchte es einen\n"
        "Datenanbieter."
    )
    return "\n".join(zeilen)
