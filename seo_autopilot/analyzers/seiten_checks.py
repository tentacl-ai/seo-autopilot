"""
Seitenpruefungen ohne Netzabruf (Rundumschlag 18.09.2026, Stufe 2).

* ``utility_page_indexable``       — Werkzeugseiten (/dashboard, /login, /suche,
  /warenkorb ...) ohne noindex im Index. Anlass: tentacl.ai/seo-autopilot/dashboard.
* ``missing_local_business_schema`` — die Website zeigt Adresse oder
  Telefon-Link, das JSON-LD kennt aber keinen lokalen Organisationstyp
  (LocalBusiness, FinancialService, Campground ...). Einmal je Website.
* ``heading_level_skipped``        — Ueberschriften-Ebene uebersprungen (h1 -> h3).
* ``duplicate_title`` / ``duplicate_meta_description`` — gleicher Titel bzw.
  gleiche Beschreibung auf mehreren Seiten (Canonical-Paare ausgenommen).
* ``mixed_content``                — http-Ressourcen auf einer https-Seite.

``missing_html_lang`` und ``missing_viewport`` gibt es bereits im
AnalyzerAgent (``missing_html_lang``, ``missing_viewport``) — hier nicht doppelt.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .eeat import LEGAL_PATTERNS, ORGANIZATION_TYPES

# Pfadsegmente, die auf Werkzeug-/Kontoseiten hindeuten (exakter Segment-Vergleich)
UTILITY_SEGMENTE = {
    "dashboard", "admin", "administrator", "wp-admin", "backend",
    "login", "anmelden", "anmeldung", "signin", "sign-in", "logout", "abmelden",
    "register", "registrieren", "registrierung", "signup", "sign-up",
    "suche", "search", "warenkorb", "cart", "basket", "checkout", "kasse",
    "account", "konto", "mein-konto", "my-account", "passwort", "password",
}  # fmt: skip

# Organisationstypen, die KEINE lokale Praesenz ausdruecken
_NICHT_LOKAL = {
    "organization", "corporation", "ngo", "educationalorganization",
    "governmentorganization", "sportsorganization",
}  # fmt: skip
LOKALE_TYPEN = (ORGANIZATION_TYPES - _NICHT_LOKAL) | {
    "lodgingbusiness", "campground", "bedandbreakfast", "hostel", "motel",
    "resort", "vacationrental", "touristinformationcenter",
    "sportsactivitylocation", "tattooparlor", "animalshelter", "library",
    "recyclingcenter", "radiostation", "legalservice", "notary", "attorney",
}  # fmt: skip

_STRASSE = re.compile(
    r"\b[A-ZÄÖÜ][\wäöüß.\-]*(?:straße|strasse|str\.|gasse|weg|platz|allee|ring|"
    r"damm|ufer|steig|zeile|markt|gürtel|guertel)\s+\d{1,4}\s?[a-zA-Z]?\b",
)
_PLZ_ORT = re.compile(r"\b(?:D-|A-|CH-)?\d{4,5}\s+[A-ZÄÖÜ][a-zäöüß]{2,}")
_ALLE_UEBERSCHRIFTEN = re.compile(r"^h[1-6]$")
_AKTIV = {"script", "iframe", "object", "embed"}


def _befund(typ, schwere, url, titel, beschreibung, abhilfe, kategorie="meta"):
    return {
        "category": kategorie,
        "type": typ,
        "severity": schwere,
        "title": titel,
        "affected_url": url,
        "description": beschreibung,
        "fix_suggestion": abhilfe,
        "estimated_impact": "",
    }


def _norm(url: str) -> str:
    return (url or "").split("#")[0].split("?")[0].rstrip("/").lower()


def _noindex(seite: Dict[str, Any]) -> bool:
    werte = f"{seite.get('robots_meta') or ''} {seite.get('x_robots_tag') or ''}"
    return "noindex" in werte.lower()


def _ist_rechtsseite(url: str) -> bool:
    pfad = urlparse(url or "").path.lower()
    muster = LEGAL_PATTERNS["impressum"] + LEGAL_PATTERNS["datenschutz"] + ["agb"]
    return any(m in pfad for m in muster)


# --------------------------------------------------------------------------
# 1. Werkzeugseiten im Index
# --------------------------------------------------------------------------


def pruefe_werkzeugseiten(
    seiten: List[Dict[str, Any]], sitemap_urls: Optional[Iterable[str]] = None
) -> List[Dict[str, Any]]:
    in_sitemap = {_norm(u) for u in (sitemap_urls or [])}
    befunde = []
    for s in seiten:
        url = s.get("final_url") or s.get("url") or ""
        segmente = {t.lower() for t in urlparse(url).path.split("/") if t}
        treffer = sorted(segmente & UTILITY_SEGMENTE)
        if not treffer or _noindex(s):
            continue
        canonical = s.get("canonical")
        if canonical and _norm(canonical) != _norm(url):
            continue  # zeigt woanders hin -> wird nicht selbst indexiert
        schwere = "medium" if _norm(s.get("url")) in in_sitemap else "low"
        befunde.append(
            _befund(
                "utility_page_indexable",
                schwere,
                s.get("url"),
                f"Werkzeugseite indexierbar: /{'/'.join(treffer)}",
                "Die Seite ist eine Werkzeug-/Kontoseite, traegt aber kein noindex"
                + (" und steht in der Sitemap" if schwere == "medium" else "")
                + ". Solche Seiten haben im Suchindex nichts verloren.",
                '<meta name="robots" content="noindex"> setzen (bzw. X-Robots-Tag) '
                "und die Seite aus der Sitemap nehmen.",
                kategorie="indexierung",
            )
        )
    return befunde


# --------------------------------------------------------------------------
# 2. Lokales Unternehmen ohne passenden Schema-Typ
# --------------------------------------------------------------------------


def _typen(schemas: Iterable[Dict[str, Any]]) -> Set[str]:
    typen = set()
    for s in schemas:
        roh = s.get("@type") if isinstance(s, dict) else None
        for t in roh if isinstance(roh, list) else [roh]:
            if isinstance(t, str):
                typen.add(t.strip().lower())
    return typen


def _lokale_signale(seite: Dict[str, Any]) -> List[str]:
    html = seite.get("html") or ""
    signale = []
    if re.search(r'href\s*=\s*["\']tel:', html, re.I):
        signale.append("Telefon-Link")
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    if _STRASSE.search(text) and _PLZ_ORT.search(text):
        signale.append("Postadresse")
    return signale


_ORTS_PFADE = (
    "kontakt",
    "contact",
    "anfahrt",
    "standort",
    "lage",
    "location",
    "find-us",
)


def _ist_ortsseite(url: str, domain: str) -> bool:
    if _ist_rechtsseite(url):
        return False
    pfad = urlparse(url or "").path.lower().strip("/")
    return not pfad or any(m in pfad for m in _ORTS_PFADE)


def pruefe_lokales_schema(
    seiten: List[Dict[str, Any]], domain: str
) -> List[Dict[str, Any]]:
    """Einmal je Website: Adresse/Telefon sichtbar, aber kein lokaler Schema-Typ."""
    alle_typen = _typen(sch for s in seiten for sch in (s.get("schema_data") or []))
    alle_typen |= {
        str(t).lower() for s in seiten for t in (s.get("schema_types") or [])
    }
    if alle_typen & LOKALE_TYPEN:
        return []
    fundorte = []
    for s in seiten:
        # Nur Startseite und Kontakt/Anfahrt: Dort zeigt ein Betrieb mit
        # Ortsbezug Adresse und Telefon. Ein Impressum hat jede Website, eine
        # Firmenadresse auf "Ueber uns"/"Presse" auch jede Softwarefirma.
        if not _ist_ortsseite(s.get("url"), domain):
            continue
        signale = _lokale_signale(s)
        if signale:
            fundorte.append(
                f"{urlparse(s.get('url') or '').path or '/'} ({', '.join(signale)})"
            )
    if not fundorte:
        return []
    vorhanden = ", ".join(sorted(alle_typen)) or "kein JSON-LD"
    return [
        _befund(
            "missing_local_business_schema",
            "medium",
            domain.rstrip("/") + "/",
            "Adresse/Telefon sichtbar, aber kein LocalBusiness-Schema",
            f"Gefunden auf: {'; '.join(fundorte[:5])}. Vorhandene Schema-Typen: "
            f"{vorhanden}. Ohne passenden Untertyp (LocalBusiness, "
            "ProfessionalService, FinancialService, LodgingBusiness, Campground ...) "
            "fehlen Google die Branche und der Ortsbezug fuer lokale Suchen.",
            "Organization um den passenden LocalBusiness-Untertyp ergaenzen "
            '(z. B. "@type": ["Organization", "FinancialService"]) mit address, '
            "telephone, geo und openingHours.",
            kategorie="schema",
        )
    ]


# --------------------------------------------------------------------------
# 3. Ueberschriften-Hierarchie
# --------------------------------------------------------------------------


def erster_ebenensprung(html: str) -> Optional[str]:
    """z. B. 'h1 -> h3', oder None, wenn keine Ebene uebersprungen wird."""
    soup = BeautifulSoup(html or "", "html.parser")
    vorher = None
    for tag in soup.find_all(_ALLE_UEBERSCHRIFTEN):
        ebene = int(tag.name[1])
        if vorher is not None and ebene > vorher + 1:
            return f"h{vorher} -> h{ebene} ('{tag.get_text(' ', strip=True)[:50]}')"
        vorher = ebene
    return None


def pruefe_ueberschriften(seiten: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    befunde = []
    for s in seiten:
        sprung = erster_ebenensprung(s.get("html") or "")
        if sprung:
            befunde.append(
                _befund(
                    "heading_level_skipped",
                    "low",
                    s.get("url"),
                    "Ueberschriften-Ebene uebersprungen",
                    f"Erster Sprung: {sprung}. Screenreader und Suchmaschinen "
                    "lesen die Gliederung aus den Ebenen.",
                    "Ebenen lueckenlos verwenden (h1 -> h2 -> h3); Optik per CSS.",
                    kategorie="content",
                )
            )
    return befunde


def h1_nur_optisch(html: str) -> Optional[str]:
    """Findet ein Element, das wie eine H1 aussieht, aber keine ist (class="h1")."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(class_=re.compile(r"(^|\s)h1(\s|$)")):
        if tag.name != "h1":
            return f"<{tag.name} class=\"h1\">{tag.get_text(' ', strip=True)[:60]}"
    return None


# --------------------------------------------------------------------------
# 4. Doppelte Titel / Beschreibungen
# --------------------------------------------------------------------------


def _doppelte(seiten, feld) -> List[List[Dict[str, Any]]]:
    gruppen: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in seiten:
        wert = " ".join((s.get(feld) or "").split()).casefold()
        if wert and not _noindex(s):
            gruppen[wert].append(s)
    ergebnis = []
    for gruppe in gruppen.values():
        # Canonical-Paare: zeigen alle auf dieselbe Adresse, ist das gewollt
        ziele = {
            _norm(s.get("canonical") or s.get("final_url") or s.get("url"))
            for s in gruppe
        }
        if len(gruppe) > 1 and len(ziele) > 1 and not _sprachfassungen(gruppe):
            ergebnis.append(gruppe)
    return ergebnis


def _sprachfassungen(gruppe: List[Dict[str, Any]]) -> bool:
    """Alle Seiten sind per hreflang Sprachfassungen voneinander ("FAQ" = "FAQ")."""
    verbund = {
        _norm(h.get("href"))
        for s in gruppe
        for h in (s.get("hreflang") or [])
        if h.get("href")
    }
    return bool(verbund) and all(
        _norm(s.get("final_url") or s.get("url")) in verbund for s in gruppe
    )


def pruefe_doppelte_titel(seiten: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    befunde = []
    for feld, typ, schwere, name in (
        ("title", "duplicate_title", "medium", "Titel"),
        ("meta_description", "duplicate_meta_description", "low", "Beschreibung"),
    ):
        for gruppe in _doppelte(seiten, feld):
            pfade = [urlparse(s.get("url") or "").path or "/" for s in gruppe]
            befunde.append(
                _befund(
                    typ,
                    schwere,
                    gruppe[0].get("url"),
                    f"{len(gruppe)} Seiten mit gleichem {name}",
                    f"„{(gruppe[0].get(feld) or '')[:90]}“ auf: {', '.join(pfade[:6])}"
                    + (f" (+{len(pfade) - 6})" if len(pfade) > 6 else ""),
                    f"Je Seite einen eigenen {name} schreiben, der den Inhalt "
                    "dieser Seite beschreibt.",
                )
            )
    return befunde


# --------------------------------------------------------------------------
# 5. Gemischte Inhalte
# --------------------------------------------------------------------------


def _http_ressourcen(html: str) -> Dict[str, List[str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    aktiv, passiv = [], []
    for tag in soup.find_all(
        [
            "script",
            "iframe",
            "img",
            "source",
            "video",
            "audio",
            "embed",
            "object",
            "link",
        ]
    ):
        if tag.name == "link":
            rel = [r.lower() for r in (tag.get("rel") or [])]
            if "stylesheet" not in rel:
                continue
            wert = tag.get("href") or ""
        else:
            wert = tag.get("src") or tag.get("data") or ""
        if wert.strip().lower().startswith("http://") and "localhost" not in wert:
            (aktiv if tag.name in _AKTIV or tag.name == "link" else passiv).append(
                wert.strip()
            )
    return {"aktiv": aktiv, "passiv": passiv}


def pruefe_mixed_content(seiten: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    befunde = []
    for s in seiten:
        if not (s.get("final_url") or s.get("url") or "").startswith("https://"):
            continue
        res = _http_ressourcen(s.get("html") or "")
        alle = res["aktiv"] + res["passiv"]
        if not alle:
            continue
        schwere = "medium" if res["aktiv"] else "low"
        befunde.append(
            _befund(
                "mixed_content",
                schwere,
                s.get("url"),
                f"{len(alle)} unverschluesselte Ressource(n) auf https-Seite",
                (
                    "Skripte/Stylesheets/iframes per http werden vom Browser blockiert. "
                    if res["aktiv"]
                    else "Bilder/Medien per http loesen Warnungen aus. "
                )
                + "Beispiele: "
                + ", ".join(alle[:3]),
                "Alle Ressourcen per https:// (oder relativ) einbinden.",
                kategorie="security",
            )
        )
    return befunde
