"""
DataForSEO als Datenquelle (Fahrplan-Schritt 7).

Die Search Console zeigt nur die **eigenen** Zahlen: eigene Klicks, eigene
Positionen. Sie beantwortet nie die entscheidende Frage — *wer steht bei
diesem Suchbegriff vor uns, und womit?* Genau diese drei Dinge kaufen wir
bei DataForSEO ein:

  * ``serp_ergebnisse``    – wer auf den vorderen Plätzen steht
  * ``suchvolumen``        – wie oft ein Begriff im Monat gesucht wird
  * ``backlink_uebersicht`` – wie viele fremde Seiten auf eine Domain zeigen

Zwei Dinge sind hier wichtiger als Funktionsumfang:

**1. Geld.** DataForSEO rechnet pro Abfrage ab. Ein Programmierfehler in einer
Schleife ist hier keine Log-Zeile, sondern eine Rechnung. Deshalb hat diese
Quelle eine harte Obergrenze pro Lauf (Standard 25). Wird sie erreicht, läuft
nichts "einfach weiter" — es bricht mit :class:`KostenbremseError` ab und wird
protokolliert.

**2. Zugangsdaten.** Login und Passwort kommen ausschließlich aus
Umgebungsvariablen (``DATAFORSEO_LOGIN`` / ``DATAFORSEO_PASSWORD``) oder aus
einer Datei, deren Pfad in der Projektkonfiguration steht. Sie stehen nirgends
im Code — und sie landen in **keiner** Log-Zeile und in **keiner**
Fehlermeldung, auch nicht gekürzt. Jede Meldung, die nach außen geht, läuft
vorher durch :meth:`DataForSEODataSource._ohne_geheimnisse`.

Fehlen die Zugangsdaten, meldet sich die Quelle sauber als *nicht
konfiguriert* und liefert leere Ergebnisse zurück. Sie wirft dann keine
Ausnahme — ein Audit darf niemals daran scheitern, dass eine Zusatzquelle
nicht eingerichtet ist.

Eintrag in ``projects.yaml``::

    enabled_sources:
      - gsc
      - dataforseo
    source_config:
      dataforseo:
        credentials_path: /opt/odoo/credentials/dataforseo.json  # optional
        max_abfragen_pro_lauf: 25      # Kostenbremse
        location_code: 2276            # 2276 = Deutschland
        language_code: de

Ausführliche Anleitung: ``docs/dataforseo-setup.md``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import DataSource, DataSourceError, SearchAnalytics

logger = logging.getLogger(__name__)

BASIS_URL = "https://api.dataforseo.com/v3/"

# Sparsame Standardwerte: Deutschland, Deutsch, zehn Treffer.
# 2276 ist der DataForSEO-Ländercode für Deutschland.
STANDARD_LOCATION_CODE = 2276
STANDARD_LANGUAGE_CODE = "de"
STANDARD_SERP_LIMIT = 10

# Kostenbremse: so viele bezahlte Abfragen darf EIN Lauf höchstens auslösen.
STANDARD_MAX_ABFRAGEN = 25

# DataForSEO nimmt höchstens 1000 Begriffe pro Suchvolumen-Abfrage.
MAX_KEYWORDS_PRO_ABFRAGE = 1000

# DataForSEO liefert HTTP 200 und packt den echten Status in den Rumpf.
ERFOLG_STATUS = 20000

STANDARD_TIMEOUT = 60.0

# Genau dieser Text signalisiert dem Aufrufer: hier fehlen nur die Zugangsdaten,
# es ist nichts kaputt.
NICHT_KONFIGURIERT = (
    "DataForSEO ist nicht konfiguriert — "
    "DATAFORSEO_LOGIN und DATAFORSEO_PASSWORD fehlen"
)

# Endpunkte (v3, relativ zu BASIS_URL)
PFAD_SERP = "serp/google/organic/live/advanced"
PFAD_SUCHVOLUMEN = "keywords_data/google_ads/search_volume/live"
PFAD_BACKLINKS = "backlinks/summary/live"
PFAD_KONTO = "appendix/user_data"  # kostenlos, zählt nicht gegen die Bremse


class KostenbremseError(DataSourceError):
    """Die Obergrenze für bezahlte Abfragen pro Lauf ist erreicht.

    Bewusst eine Ausnahme und kein stiller Rückgabewert: Wer weiterfragt,
    zahlt weiter. Hier soll etwas kaputtgehen, nicht die Rechnung wachsen.
    """


# ---------------------------------------------------------------------------
# Ergebnis-Strukturen (deutsche Feldnamen, wie im übrigen neuen Code)
# ---------------------------------------------------------------------------


@dataclass
class SerpTreffer:
    """Ein einzelner organischer Treffer auf der Google-Ergebnisseite."""

    position: int
    domain: str
    titel: str
    url: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position": self.position,
            "domain": self.domain,
            "titel": self.titel,
            "url": self.url,
        }


@dataclass
class SerpErgebnis:
    """Antwort auf :meth:`DataForSEODataSource.serp_ergebnisse`."""

    keyword: str
    treffer: List[SerpTreffer] = field(default_factory=list)
    konfiguriert: bool = True
    fehler: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.fehler is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "keyword": self.keyword,
            "treffer": [t.to_dict() for t in self.treffer],
            "konfiguriert": self.konfiguriert,
            "fehler": self.fehler,
        }


@dataclass
class SuchvolumenEintrag:
    """Monatliches Suchvolumen für einen einzelnen Begriff."""

    keyword: str
    suchvolumen: Optional[int] = None
    wettbewerb: Optional[str] = None
    cpc: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "keyword": self.keyword,
            "suchvolumen": self.suchvolumen,
            "wettbewerb": self.wettbewerb,
            "cpc": self.cpc,
        }


@dataclass
class SuchvolumenErgebnis:
    """Antwort auf :meth:`DataForSEODataSource.suchvolumen`."""

    eintraege: List[SuchvolumenEintrag] = field(default_factory=list)
    konfiguriert: bool = True
    fehler: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.fehler is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eintraege": [e.to_dict() for e in self.eintraege],
            "konfiguriert": self.konfiguriert,
            "fehler": self.fehler,
        }


@dataclass
class BacklinkUebersicht:
    """Antwort auf :meth:`DataForSEODataSource.backlink_uebersicht`.

    ``vertrauenswert`` ist der DataForSEO-Domain-Rank (0–1000). Er ist kein
    Google-Wert, sondern eine Schätzung — als Vergleichsgröße zwischen zwei
    Domains taugt er, als absolute Note nicht.
    """

    domain: str
    verweisende_domains: Optional[int] = None
    backlinks_gesamt: Optional[int] = None
    vertrauenswert: Optional[float] = None
    konfiguriert: bool = True
    fehler: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.fehler is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "verweisende_domains": self.verweisende_domains,
            "backlinks_gesamt": self.backlinks_gesamt,
            "vertrauenswert": self.vertrauenswert,
            "konfiguriert": self.konfiguriert,
            "fehler": self.fehler,
        }


# ---------------------------------------------------------------------------
# Die Quelle
# ---------------------------------------------------------------------------


class DataForSEODataSource(DataSource):
    """DataForSEO v3 — SERP, Suchvolumen und Backlinks.

    Args:
        source_config: der Abschnitt ``source_config.dataforseo`` aus
            ``projects.yaml``. Erlaubte Schlüssel: ``credentials_path``,
            ``max_abfragen_pro_lauf``, ``location_code``, ``language_code``,
            ``timeout``.
        client: fertiger ``httpx.AsyncClient``. Nur für Tests gedacht —
            im Betrieb baut die Quelle sich pro Abfrage einen eigenen.
        umgebung: Ersatz für ``os.environ``. Ebenfalls nur für Tests.
    """

    BASIS_URL = BASIS_URL

    def __init__(
        self,
        source_config: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
        umgebung: Optional[Dict[str, str]] = None,
    ):
        cfg = source_config or {}
        self.config = cfg
        self._client = client
        self._umgebung = dict(os.environ) if umgebung is None else dict(umgebung)

        self.location_code = cfg.get("location_code", STANDARD_LOCATION_CODE)
        self.language_code = cfg.get("language_code", STANDARD_LANGUAGE_CODE)
        self.timeout = float(cfg.get("timeout", STANDARD_TIMEOUT))

        # Kostenbremse
        self.max_abfragen = int(cfg.get("max_abfragen_pro_lauf", STANDARD_MAX_ABFRAGEN))
        self.abfragen_verbraucht = 0

        self._login, self._passwort, self._quelle = self._zugangsdaten_lesen(cfg)
        self.authenticated = bool(self._login and self._passwort)

        if self.authenticated:
            logger.info(
                f"[DataForSEO] Zugangsdaten geladen (Quelle: {self._quelle}), "
                f"Obergrenze {self.max_abfragen} Abfragen pro Lauf"
            )
        else:
            logger.info(
                "[DataForSEO] Keine Zugangsdaten gefunden — Quelle bleibt still. "
                "Der Audit läuft ohne SERP-, Suchvolumen- und Backlink-Daten weiter."
            )

    # -- Zugangsdaten -------------------------------------------------------

    def _zugangsdaten_lesen(
        self, cfg: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[str], str]:
        """Holt Login und Passwort. Reihenfolge: Umgebung, dann Datei.

        Gibt ``(login, passwort, herkunft)`` zurück. Die Herkunft ist nur ein
        Wort fürs Log ("Umgebungsvariablen" / Dateiname) — nie ein Wert.
        """
        login = (self._umgebung.get("DATAFORSEO_LOGIN") or "").strip()
        passwort = (self._umgebung.get("DATAFORSEO_PASSWORD") or "").strip()
        if login and passwort:
            return login, passwort, "Umgebungsvariablen"

        pfad = cfg.get("credentials_path")
        if pfad:
            aus_datei = self._zugangsdaten_aus_datei(Path(pfad))
            if aus_datei:
                return aus_datei[0], aus_datei[1], f"Datei {Path(pfad).name}"

        return None, None, "keine"

    @staticmethod
    def _zugangsdaten_aus_datei(pfad: Path) -> Optional[Tuple[str, str]]:
        """Liest Zugangsdaten aus einer Datei.

        Drei Formate werden akzeptiert, damit niemand raten muss:
          * JSON: ``{"login": "...", "password": "..."}``
          * eine Zeile ``login:passwort``
          * Zeilen ``DATAFORSEO_LOGIN=...`` / ``DATAFORSEO_PASSWORD=...``

        Bei jedem Problem: ``None`` und eine Log-Zeile **ohne** Inhalt der
        Datei — ein kaputtes Zugangsdaten-File darf nichts durchsickern lassen.
        """
        try:
            if not pfad.exists():
                logger.warning(f"[DataForSEO] Zugangsdaten-Datei fehlt: {pfad}")
                return None
            roh = pfad.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning(
                f"[DataForSEO] Zugangsdaten-Datei nicht lesbar: {pfad} "
                f"({type(exc).__name__})"
            )
            return None

        if not roh:
            logger.warning(f"[DataForSEO] Zugangsdaten-Datei ist leer: {pfad}")
            return None

        # 1. JSON
        try:
            daten = json.loads(roh)
            if isinstance(daten, dict):
                login = str(daten.get("login") or daten.get("DATAFORSEO_LOGIN") or "")
                passwort = str(
                    daten.get("password")
                    or daten.get("passwort")
                    or daten.get("DATAFORSEO_PASSWORD")
                    or ""
                )
                if login and passwort:
                    return login.strip(), passwort.strip()
        except ValueError:
            pass

        # 2./3. Zeilenformate
        werte: Dict[str, str] = {}
        for zeile in roh.splitlines():
            zeile = zeile.strip()
            if not zeile or zeile.startswith("#"):
                continue
            if "=" in zeile:
                schluessel, _, wert = zeile.partition("=")
                werte[schluessel.strip().upper()] = wert.strip().strip("\"'")
            elif ":" in zeile and not werte:
                login, _, passwort = zeile.partition(":")
                if login.strip() and passwort.strip():
                    return login.strip(), passwort.strip()

        login = werte.get("DATAFORSEO_LOGIN", "")
        passwort = werte.get("DATAFORSEO_PASSWORD", "")
        if login and passwort:
            return login, passwort

        logger.warning(
            f"[DataForSEO] Zugangsdaten-Datei hat kein erkanntes Format: {pfad}"
        )
        return None

    @property
    def ist_konfiguriert(self) -> bool:
        """True, wenn Login **und** Passwort vorliegen."""
        return bool(self._login and self._passwort)

    def status(self) -> Dict[str, Any]:
        """Kurzer Betriebszustand — enthält niemals Zugangsdaten."""
        return {
            "quelle": "dataforseo",
            "konfiguriert": self.ist_konfiguriert,
            "herkunft": self._quelle,
            "abfragen_verbraucht": self.abfragen_verbraucht,
            "max_abfragen_pro_lauf": self.max_abfragen,
        }

    # -- Geheimhaltung ------------------------------------------------------

    def _geheimnisse(self) -> List[str]:
        """Alle Zeichenketten, die niemals nach außen dürfen."""
        geheim: List[str] = []
        for wert in (self._login, self._passwort):
            if wert:
                geheim.append(wert)
        if self._login and self._passwort:
            roh = f"{self._login}:{self._passwort}"
            geheim.append(roh)
            geheim.append(base64.b64encode(roh.encode("utf-8")).decode("ascii"))
        # Lange Werte zuerst ersetzen, sonst bleiben Reste stehen.
        return sorted(set(geheim), key=len, reverse=True)

    def _ohne_geheimnisse(self, text: Any) -> str:
        """Ersetzt Zugangsdaten durch ``***``.

        Jede Meldung, die in ein Log oder in eine Fehlermeldung geht, läuft
        hier durch. Auch dann, wenn wir "sicher wissen", dass nichts drinsteht:
        Fremde Antworten (z. B. ein Server, der den Auth-Header zurückspiegelt)
        sind nicht unser Code und nicht unsere Kontrolle.
        """
        sauber = str(text)
        for geheim in self._geheimnisse():
            if geheim and geheim in sauber:
                sauber = sauber.replace(geheim, "***")
        return sauber

    # -- Kostenbremse -------------------------------------------------------

    def _budget_verbrauchen(self, wofuer: str) -> None:
        """Zählt eine bezahlte Abfrage — und bricht ab, wenn das Budget alle ist."""
        if self.abfragen_verbraucht >= self.max_abfragen:
            meldung = (
                f"Kostenbremse: Obergrenze von {self.max_abfragen} DataForSEO-"
                f"Abfragen pro Lauf erreicht — '{wofuer}' wird NICHT ausgeführt. "
                "Obergrenze bei Bedarf über source_config.dataforseo."
                "max_abfragen_pro_lauf anheben."
            )
            logger.error(f"[DataForSEO] {meldung}")
            raise KostenbremseError(meldung)
        self.abfragen_verbraucht += 1
        logger.debug(
            f"[DataForSEO] Abfrage {self.abfragen_verbraucht}/{self.max_abfragen} "
            f"({wofuer})"
        )

    def budget_zuruecksetzen(self) -> None:
        """Setzt den Zähler auf 0 — beim Start eines neuen Laufs."""
        self.abfragen_verbraucht = 0

    # -- HTTP ---------------------------------------------------------------

    async def _anfrage(
        self, pfad: str, nutzlast: Any, wofuer: str, kostenpflichtig: bool = True
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """Eine POST-Abfrage gegen DataForSEO.

        Rückgabe: ``(ergebnisliste, fehlertext)`` — genau eines von beiden ist
        gesetzt. Der Fehlertext ist immer bereits von Geheimnissen befreit.
        """
        if kostenpflichtig:
            self._budget_verbrauchen(wofuer)

        eigener_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            antwort = await client.post(
                self.BASIS_URL + pfad,
                json=nutzlast,
                auth=httpx.BasicAuth(self._login or "", self._passwort or ""),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except httpx.TimeoutException:
            fehler = f"Zeitüberschreitung nach {self.timeout:.0f}s"
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler
        except Exception as exc:
            fehler = self._ohne_geheimnisse(
                f"Netzwerkfehler ({type(exc).__name__}): {exc}"
            )
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler
        finally:
            if eigener_client:
                await client.aclose()

        return self._antwort_auswerten(antwort, wofuer)

    def _antwort_auswerten(
        self, antwort: httpx.Response, wofuer: str
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """Übersetzt HTTP- und DataForSEO-Statuscodes in klare deutsche Sätze."""
        code = antwort.status_code

        if code in (401, 403):
            fehler = (
                f"Zugangsdaten wurden abgelehnt (HTTP {code}) — "
                "DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD prüfen"
            )
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler

        if code == 429:
            fehler = (
                "Zu viele Abfragen (HTTP 429) — DataForSEO drosselt. "
                "Später erneut versuchen."
            )
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler

        if code >= 500:
            fehler = f"DataForSEO nicht erreichbar (HTTP {code})"
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler

        if code != 200:
            fehler = self._ohne_geheimnisse(f"Unerwartete Antwort (HTTP {code})")
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler

        try:
            daten = antwort.json()
        except Exception:
            fehler = "Antwort war kein gültiges JSON"
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler

        if not isinstance(daten, dict):
            return None, "Antwort hatte ein unerwartetes Format"

        # DataForSEO antwortet mit HTTP 200 und meldet Fehler im Rumpf.
        status = daten.get("status_code")
        if status is not None and status != ERFOLG_STATUS:
            fehler = self._ohne_geheimnisse(
                f"DataForSEO meldet Fehler {status}: "
                f"{daten.get('status_message', 'ohne Begründung')}"
            )
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler

        aufgaben = daten.get("tasks") or []
        if not aufgaben:
            return None, "DataForSEO hat keine Aufgabe zurückgeliefert"

        aufgabe = aufgaben[0] or {}
        aufgaben_status = aufgabe.get("status_code")
        if aufgaben_status is not None and aufgaben_status != ERFOLG_STATUS:
            fehler = self._ohne_geheimnisse(
                f"DataForSEO-Aufgabe fehlgeschlagen ({aufgaben_status}): "
                f"{aufgabe.get('status_message', 'ohne Begründung')}"
            )
            logger.warning(f"[DataForSEO] {wofuer}: {fehler}")
            return None, fehler

        ergebnis = aufgabe.get("result")
        if not ergebnis:
            # Kein Fehler — es gibt schlicht nichts zu berichten.
            return [], None
        return ergebnis, None

    # -- Öffentliche Abfragen ----------------------------------------------

    async def serp_ergebnisse(
        self, keyword: str, limit: int = STANDARD_SERP_LIMIT
    ) -> SerpErgebnis:
        """Wer steht bei ``keyword`` auf den vorderen Plätzen?

        Args:
            keyword: der Suchbegriff, so wie ihn ein Mensch eintippen würde.
            limit: wie viele organische Treffer zurückkommen sollen.

        Returns:
            :class:`SerpErgebnis` mit Position, Domain, Titel und Adresse.
            Bei fehlenden Zugangsdaten: leere Trefferliste, ``konfiguriert``
            ist False — **keine** Ausnahme.

        Raises:
            KostenbremseError: wenn das Abfragebudget des Laufs erschöpft ist.
        """
        if not self.ist_konfiguriert:
            return SerpErgebnis(
                keyword=keyword,
                konfiguriert=False,
                fehler=NICHT_KONFIGURIERT,
            )

        # DataForSEO rechnet die Tiefe in Zehnerschritten ab — weniger als 10
        # kostet nicht weniger, mehr als nötig kostet mehr.
        tiefe = max(10, ((max(1, limit) + 9) // 10) * 10)
        nutzlast = [
            {
                "keyword": keyword,
                "location_code": self.location_code,
                "language_code": self.language_code,
                "depth": tiefe,
                "device": "desktop",
            }
        ]

        ergebnis, fehler = await self._anfrage(PFAD_SERP, nutzlast, f"SERP '{keyword}'")
        if fehler:
            return SerpErgebnis(keyword=keyword, fehler=fehler)

        treffer = self._serp_parsen(ergebnis or [], limit)
        logger.info(f"[DataForSEO] SERP '{keyword}': {len(treffer)} organische Treffer")
        return SerpErgebnis(keyword=keyword, treffer=treffer)

    @staticmethod
    def _serp_parsen(ergebnis: List[Dict[str, Any]], limit: int) -> List[SerpTreffer]:
        """Filtert aus der SERP-Antwort die organischen Treffer heraus.

        Anzeigen, "Ähnliche Fragen" und Karten stehen in derselben Liste —
        uns interessiert nur ``type == "organic"``.
        """
        treffer: List[SerpTreffer] = []
        for block in ergebnis:
            for eintrag in (block or {}).get("items") or []:
                if not isinstance(eintrag, dict):
                    continue
                if eintrag.get("type") != "organic":
                    continue
                position = eintrag.get("rank_absolute") or eintrag.get("rank_group")
                treffer.append(
                    SerpTreffer(
                        position=int(position) if position else len(treffer) + 1,
                        domain=eintrag.get("domain") or "",
                        titel=eintrag.get("title") or "",
                        url=eintrag.get("url") or "",
                    )
                )
                if len(treffer) >= limit:
                    return treffer
        return treffer

    async def suchvolumen(self, keywords: List[str]) -> SuchvolumenErgebnis:
        """Monatliches Suchvolumen je Begriff.

        Alle Begriffe gehen in **eine** Abfrage — das ist der günstige Weg.

        Args:
            keywords: Liste von Suchbegriffen (höchstens 1000; mehr schneidet
                DataForSEO ohnehin ab, wir kürzen vorher und sagen es im Log).

        Raises:
            KostenbremseError: wenn das Abfragebudget des Laufs erschöpft ist.
        """
        sauber = [k.strip() for k in (keywords or []) if k and k.strip()]
        if not self.ist_konfiguriert:
            return SuchvolumenErgebnis(
                konfiguriert=False,
                fehler=NICHT_KONFIGURIERT,
            )
        if not sauber:
            return SuchvolumenErgebnis(fehler="Keine Suchbegriffe übergeben")

        if len(sauber) > MAX_KEYWORDS_PRO_ABFRAGE:
            logger.warning(
                f"[DataForSEO] {len(sauber)} Begriffe übergeben — auf "
                f"{MAX_KEYWORDS_PRO_ABFRAGE} gekürzt (API-Grenze)"
            )
            sauber = sauber[:MAX_KEYWORDS_PRO_ABFRAGE]

        nutzlast = [
            {
                "keywords": sauber,
                "location_code": self.location_code,
                "language_code": self.language_code,
            }
        ]
        ergebnis, fehler = await self._anfrage(
            PFAD_SUCHVOLUMEN, nutzlast, f"Suchvolumen ({len(sauber)} Begriffe)"
        )
        if fehler:
            return SuchvolumenErgebnis(fehler=fehler)

        eintraege = [
            SuchvolumenEintrag(
                keyword=e.get("keyword") or "",
                suchvolumen=e.get("search_volume"),
                wettbewerb=e.get("competition"),
                cpc=e.get("cpc"),
            )
            for e in (ergebnis or [])
            if isinstance(e, dict)
        ]
        logger.info(f"[DataForSEO] Suchvolumen für {len(eintraege)} Begriffe geholt")
        return SuchvolumenErgebnis(eintraege=eintraege)

    async def backlink_uebersicht(self, domain: str) -> BacklinkUebersicht:
        """Wie viele fremde Seiten zeigen auf diese Domain?

        Args:
            domain: mit oder ohne ``https://`` — wird selbst aufgeräumt.

        Returns:
            :class:`BacklinkUebersicht` mit verweisenden Domains, Gesamtzahl
            der Backlinks und — falls DataForSEO ihn liefert — dem
            Vertrauenswert (Domain-Rank 0–1000).

        Raises:
            KostenbremseError: wenn das Abfragebudget des Laufs erschöpft ist.
        """
        ziel = self._domain_normalisieren(domain)
        if not self.ist_konfiguriert:
            return BacklinkUebersicht(
                domain=ziel,
                konfiguriert=False,
                fehler=NICHT_KONFIGURIERT,
            )
        if not ziel:
            return BacklinkUebersicht(domain="", fehler="Keine Domain übergeben")

        nutzlast = [
            {
                "target": ziel,
                "internal_list_limit": 10,
                "backlinks_status_type": "live",
                "include_subdomains": True,
            }
        ]
        ergebnis, fehler = await self._anfrage(
            PFAD_BACKLINKS, nutzlast, f"Backlinks {ziel}"
        )
        if fehler:
            return BacklinkUebersicht(domain=ziel, fehler=fehler)

        erster = (ergebnis or [{}])[0] if ergebnis else {}
        if not isinstance(erster, dict):
            erster = {}

        uebersicht = BacklinkUebersicht(
            domain=ziel,
            verweisende_domains=erster.get("referring_domains"),
            backlinks_gesamt=erster.get("backlinks"),
            vertrauenswert=erster.get("rank"),
        )
        logger.info(
            f"[DataForSEO] Backlinks {ziel}: "
            f"{uebersicht.verweisende_domains} verweisende Domains, "
            f"{uebersicht.backlinks_gesamt} Backlinks"
        )
        return uebersicht

    @staticmethod
    def _domain_normalisieren(domain: str) -> str:
        """``https://www.example.com/pfad`` -> ``www.example.com``."""
        if not domain:
            return ""
        ziel = domain.strip()
        for praefix in ("https://", "http://"):
            if ziel.lower().startswith(praefix):
                ziel = ziel[len(praefix) :]
                break
        return ziel.split("/")[0].strip().rstrip(".")

    # -- Schnittstelle der Basisklasse -------------------------------------

    async def authenticate(self) -> bool:
        """Prüft nur, ob Zugangsdaten vorliegen — kostet nichts.

        Anders als bei der Search Console wird hier **nicht** geworfen: eine
        fehlende Zusatzquelle darf keinen Audit umbringen.
        """
        self.authenticated = self.ist_konfiguriert
        return self.authenticated

    async def test_connection(self) -> bool:
        """Fragt die kostenlose Kontoauskunft ab.

        Zählt bewusst nicht gegen die Kostenbremse — ``appendix/user_data``
        ist bei DataForSEO gratis.
        """
        if not self.ist_konfiguriert:
            return False
        _, fehler = await self._anfrage(
            PFAD_KONTO, [{}], "Verbindungstest", kostenpflichtig=False
        )
        return fehler is None

    async def pull_analytics(
        self, domain: str, days: int = 28
    ) -> Optional[SearchAnalytics]:
        """Nicht vorhanden: DataForSEO kennt unsere eigenen Klicks nicht.

        Eigene Klicks/Impressionen kommen aus der Search Console (``gsc``).
        """
        logger.debug(
            "[DataForSEO] pull_analytics gibt es hier nicht — "
            "eigene Klickdaten liefert die Search Console."
        )
        return None

    async def pull_backlinks(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """Backlink-Übersicht im Listenformat der Basisklasse."""
        uebersicht = await self.backlink_uebersicht(domain)
        if not uebersicht.ok:
            return None
        return [uebersicht.to_dict()]

    async def pull_keywords(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """DataForSEO braucht konkrete Begriffe — siehe :meth:`suchvolumen`.

        Eine Keyword-Liste aus der Domain abzuleiten wäre eine zusätzliche
        (kostenpflichtige) Abfrage, die niemand bestellt hat.
        """
        logger.debug(
            "[DataForSEO] pull_keywords braucht eine Begriffsliste — "
            "suchvolumen(keywords) benutzen."
        )
        return None


# ---------------------------------------------------------------------------
# Registrierung als Quelle "dataforseo"
# ---------------------------------------------------------------------------


def ist_aktiviert(project_config: Any) -> bool:
    """Steht ``dataforseo`` in ``enabled_sources`` des Projekts?"""
    quellen = getattr(project_config, "enabled_sources", None)
    if quellen is None and isinstance(project_config, dict):
        quellen = project_config.get("enabled_sources")
    return "dataforseo" in (quellen or [])


def quelle_aus_projekt(project_config: Any) -> Optional[DataForSEODataSource]:
    """Baut die Quelle aus einer Projektkonfiguration — analog zu ``gsc``.

    Liest ``source_config.dataforseo`` und gibt ``None`` zurück, wenn die
    Quelle für dieses Projekt gar nicht aktiviert ist. Ist sie aktiviert, aber
    unkonfiguriert, kommt trotzdem ein Objekt zurück: Es meldet dann sauber
    "nicht konfiguriert", statt den Aufrufer mit ``None`` raten zu lassen.
    """
    if not ist_aktiviert(project_config):
        return None

    cfg = getattr(project_config, "source_config", None)
    if cfg is None and isinstance(project_config, dict):
        cfg = project_config.get("source_config")
    return DataForSEODataSource((cfg or {}).get("dataforseo", {}))
