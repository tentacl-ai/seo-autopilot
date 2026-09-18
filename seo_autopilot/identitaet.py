"""
Schutz gegen "falsche Website unter der Domain".

Anlass 18.09.2026: Unter einer Port-Adresse wurde statt camping-beispiel eine ganz
andere Kundenseite geprueft — 38 Befunde, 9 Freigaben und 14 Eintraege im
Aenderungsbuch fuer die falsche Website.

Ein Projekt kann deshalb in projects.yaml festlegen, woran es seine Website
erkennt::

    camping-beispiel:
      domain: https://...
      erwartet: "Campingplatz camping-beispiel"      # oder eine Liste von Texten

Mindestens einer der Texte muss auf der Startseite vorkommen — in Titel, H1,
og:site_name, einem Schema-Namen oder im sichtbaren Text (Gross/Klein,
Umlaute und Mehrfach-Leerzeichen egal). Sonst bricht der Audit VOR jeder
Analyse und vor jedem Auto-Fix ab (Status failed, die CLI gibt Exit 1).
Ist die Startseite nicht abrufbar, gilt die Identitaet als NICHT bestaetigt —
auf einer unbestaetigten Website wird nichts veraendert.

Ohne ``erwartet`` aendert sich nichts.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from typing import Any, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from .sources.crawler import USER_AGENT

logger = logging.getLogger(__name__)

TIMEOUT = 20.0


class IdentitaetNichtBestaetigt(RuntimeError):
    """Die Startseite gehoert nicht (nachweislich) zum Projekt."""


def _normal(text: str) -> str:
    text = (text or "").casefold()
    for alt, neu in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(alt, neu)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.split())


def erwartete_texte(erwartet: Any) -> List[str]:
    if not erwartet:
        return []
    werte = erwartet if isinstance(erwartet, (list, tuple)) else [erwartet]
    return [str(w).strip() for w in werte if str(w).strip()]


def _schema_namen(soup: BeautifulSoup) -> List[str]:
    namen = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            daten = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        stapel = [daten]
        while stapel:
            obj = stapel.pop()
            if isinstance(obj, dict):
                if isinstance(obj.get("name"), str):
                    namen.append(obj["name"])
                stapel.extend(obj.values())
            elif isinstance(obj, list):
                stapel.extend(obj)
    return namen


def kennzeichen(html: str) -> str:
    """Alles, woran man eine Website erkennt, als ein normalisierter Text."""
    soup = BeautifulSoup(html or "", "html.parser")
    teile = [soup.title.get_text(" ", strip=True) if soup.title else ""]
    teile += [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    site = soup.find("meta", attrs={"property": "og:site_name"})
    teile.append((site.get("content") or "") if site else "")
    teile += _schema_namen(soup)
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    teile.append(soup.get_text(" ", strip=True))
    return _normal(" ".join(teile))


def pruefe_html(html: str, erwartet: Any) -> Tuple[bool, str]:
    texte = erwartete_texte(erwartet)
    if not texte:
        return True, "keine Erwartung gesetzt"
    inhalt = kennzeichen(html)
    for text in texte:
        if _normal(text) in inhalt:
            return True, f"'{text}' auf der Startseite gefunden"
    return False, (
        "Startseite enthaelt keinen der erwarteten Texte "
        f"({', '.join(repr(t) for t in texte)}) — falsche Website unter der Domain?"
    )


async def pruefe_identitaet(
    domain: str, erwartet: Any, client: Optional[httpx.AsyncClient] = None
) -> Tuple[bool, str]:
    """(bestaetigt, Begruendung). Ohne Erwartung immer bestaetigt."""
    if not erwartete_texte(erwartet):
        return True, "keine Erwartung gesetzt"
    eigener = client is None
    if eigener:
        client = httpx.AsyncClient(
            follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
    try:
        antwort = await client.get(domain)
    except Exception as exc:
        return (
            False,
            f"Startseite nicht abrufbar ({type(exc).__name__}) — Identitaet unbestaetigt",
        )
    finally:
        if eigener:
            await client.aclose()
    if antwort.status_code != 200:
        return (
            False,
            f"Startseite antwortet mit HTTP {antwort.status_code} — Identitaet unbestaetigt",
        )
    return pruefe_html(antwort.text, erwartet)


async def sicherstellen(
    project: Any, client: Optional[httpx.AsyncClient] = None
) -> None:
    """Wirft ``IdentitaetNichtBestaetigt``, wenn ``project.erwartet`` nicht passt."""
    erwartet = getattr(project, "erwartet", None)
    if not erwartete_texte(erwartet):
        return
    ok, grund = await pruefe_identitaet(project.domain, erwartet, client=client)
    if not ok:
        raise IdentitaetNichtBestaetigt(f"Identitaetspruefung fehlgeschlagen: {grund}")
    logger.info(f"[identitaet] {project.id}: {grund}")
