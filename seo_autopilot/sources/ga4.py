"""
Google Analytics 4 als Datenquelle ("Besucherdaten").

Die Search Console beantwortet nur die halbe Frage: sie zeigt, wie oft eine
Seite in Google **angezeigt** und angeklickt wurde. Was danach passiert —
ob die Besucher bleiben oder sofort wieder abspringen — steht dort nicht.
Genau diese zweite Hälfte liefert GA4:

- Nutzer, Sitzungen, Seitenaufrufe (gesamt und je Seite)
- Absprungrate und Interaktionsrate (je Seite und gesamt)
- Zugriffsquellen nach Kanalgruppe (organische Suche vs. Rest)

Wichtig: **Diese Quelle darf einen Audit niemals abbrechen.** Fehlt die
Bibliothek `google-analytics-data`, fehlt die Schlüsseldatei oder antwortet
die API nicht, meldet sich die Quelle als "nicht verfügbar" und liefert
`None` — der Lauf geht ohne Besucherdaten weiter.

Konfiguration je Projekt (projects.yaml):

    enabled_sources:
      - gsc
      - ga4
    source_config:
      ga4:
        property_id: "123456789"      # NICHT die Mess-ID G-XXXXXXX
        credentials_path: /opt/odoo/credentials/tentacl-seo-service-account.json
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import DataSource

logger = logging.getLogger(__name__)

# Hinweis, den der Anwender zu sehen bekommt, wenn die Bibliothek fehlt.
FEHLT_HINWEIS = (
    "Google-Analytics-Bibliothek nicht installiert — "
    "GA4-Quelle deaktiviert (pip install google-analytics-data)"
)

try:  # pragma: no cover - abhängig von der Installation
    from google.oauth2.service_account import Credentials
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )

    HAS_GA4_API = True
except ImportError:  # pragma: no cover - abhängig von der Installation
    HAS_GA4_API = False
    logger.info(FEHLT_HINWEIS)

# Kennzahlen, die wir immer abfragen. Reihenfolge egal — wir lesen die
# Antwort über die Kopfzeilen aus, nicht über Positionen.
KENNZAHLEN = [
    "activeUsers",
    "sessions",
    "screenPageViews",
    "bounceRate",
    "engagementRate",
]

# Wie viele Seiten wir maximal aus dem Seiten-Report holen.
SEITEN_LIMIT = 25

# Kanalgruppen, die als "organische Suche" zählen (GA4 antwortet je nach
# Spracheinstellung der Property englisch oder deutsch).
ORGANISCHE_KANAELE = ("organic search", "organische suche")


@dataclass
class GA4Analytics:
    """Besucherdaten eines Zeitraums in einer schlichten Struktur."""

    start_date: str = ""
    end_date: str = ""
    total_users: int = 0
    total_sessions: int = 0
    total_pageviews: int = 0
    bounce_rate: float = 0.0  # Prozent, z. B. 62.5
    engagement_rate: float = 0.0  # Prozent
    top_pages: List[Dict[str, Any]] = field(default_factory=list)
    by_channel: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    organic_sessions: int = 0
    organic_share: float = 0.0  # Anteil organischer Sitzungen in Prozent

    @property
    def is_empty(self) -> bool:
        """True, wenn die Property im Zeitraum gar keine Daten geliefert hat."""
        return (
            self.total_sessions == 0
            and self.total_users == 0
            and not self.top_pages
            and not self.by_channel
        )


# ---------------------------------------------------------------------------
# Reine Hilfsfunktionen (ohne Netz, damit gut testbar)
# ---------------------------------------------------------------------------


def _zahl(wert: Any) -> float:
    """GA4 liefert alle Werte als Zeichenkette — defensiv in Zahl wandeln."""
    try:
        return float(wert)
    except (TypeError, ValueError):
        return 0.0


def _prozent(wert: Any) -> float:
    """GA4 liefert Raten als Anteil (0.62) — wir zeigen Prozent (62.0)."""
    return round(_zahl(wert) * 100, 2)


def zeilen_zu_dicts(antwort: Any) -> List[Dict[str, Any]]:
    """Wandelt eine GA4-Antwort in einfache Wörterbücher um.

    Wir lesen die Spaltennamen aus den Kopfzeilen der Antwort. Dadurch ist die
    Auswertung unabhängig von der Reihenfolge der angefragten Kennzahlen und
    funktioniert genauso mit einer nachgebauten Antwort im Test.
    """
    if antwort is None:
        return []

    dim_namen = [h.name for h in (getattr(antwort, "dimension_headers", None) or [])]
    kennzahl_namen = [h.name for h in (getattr(antwort, "metric_headers", None) or [])]

    zeilen: List[Dict[str, Any]] = []
    for row in getattr(antwort, "rows", None) or []:
        eintrag: Dict[str, Any] = {}
        dim_werte = getattr(row, "dimension_values", None) or []
        kennzahl_werte = getattr(row, "metric_values", None) or []

        for i, name in enumerate(dim_namen):
            eintrag[name] = dim_werte[i].value if i < len(dim_werte) else ""
        for i, name in enumerate(kennzahl_namen):
            eintrag[name] = (
                _zahl(kennzahl_werte[i].value) if i < len(kennzahl_werte) else 0.0
            )
        zeilen.append(eintrag)
    return zeilen


def _ist_organisch(kanal: str) -> bool:
    return (kanal or "").strip().lower() in ORGANISCHE_KANAELE


def baue_analytics(
    seiten_zeilen: List[Dict[str, Any]],
    kanal_zeilen: List[Dict[str, Any]],
    start_date: str,
    end_date: str,
) -> GA4Analytics:
    """Setzt die beiden Teil-Reports zu einer Auswertung zusammen.

    Die Summen berechnen wir selbst aus den Zeilen, statt einen dritten
    Report abzufragen — das spart eine API-Runde und liefert dieselbe Zahl.
    Absprung- und Interaktionsrate werden nach Sitzungen gewichtet, sonst
    würde eine Seite mit drei Besuchern genauso schwer wiegen wie die
    Startseite.
    """
    ergebnis = GA4Analytics(start_date=start_date, end_date=end_date)

    seiten: List[Dict[str, Any]] = []
    gewicht_absprung = 0.0
    gewicht_interaktion = 0.0
    gewicht_summe = 0.0

    for zeile in seiten_zeilen:
        sitzungen = int(_zahl(zeile.get("sessions")))
        nutzer = int(_zahl(zeile.get("activeUsers")))
        aufrufe = int(_zahl(zeile.get("screenPageViews")))
        absprung = _prozent(zeile.get("bounceRate"))
        interaktion = _prozent(zeile.get("engagementRate"))

        ergebnis.total_users += nutzer
        ergebnis.total_sessions += sitzungen
        ergebnis.total_pageviews += aufrufe

        gewicht_absprung += absprung * sitzungen
        gewicht_interaktion += interaktion * sitzungen
        gewicht_summe += sitzungen

        seiten.append(
            {
                "page": zeile.get("pagePath", "") or "",
                "users": nutzer,
                "sessions": sitzungen,
                "pageviews": aufrufe,
                "bounce_rate": absprung,
                "engagement_rate": interaktion,
            }
        )

    ergebnis.top_pages = sorted(
        seiten, key=lambda s: s["pageviews"], reverse=True
    )  # meistgesehene Seite zuerst

    if gewicht_summe > 0:
        ergebnis.bounce_rate = round(gewicht_absprung / gewicht_summe, 2)
        ergebnis.engagement_rate = round(gewicht_interaktion / gewicht_summe, 2)

    kanaele: Dict[str, Dict[str, Any]] = {}
    kanal_sitzungen_gesamt = 0
    for zeile in kanal_zeilen:
        name = zeile.get("sessionDefaultChannelGroup", "") or "unbekannt"
        sitzungen = int(_zahl(zeile.get("sessions")))
        nutzer = int(_zahl(zeile.get("activeUsers")))
        eintrag = kanaele.setdefault(name, {"users": 0, "sessions": 0})
        eintrag["users"] += nutzer
        eintrag["sessions"] += sitzungen
        kanal_sitzungen_gesamt += sitzungen
        if _ist_organisch(name):
            ergebnis.organic_sessions += sitzungen

    ergebnis.by_channel = kanaele

    # Der Anteil bezieht sich auf die Kanal-Summe: nur dort sind wirklich
    # alle Sitzungen enthalten (der Seiten-Report ist auf Top-Seiten gekappt).
    bezug = kanal_sitzungen_gesamt or ergebnis.total_sessions
    if bezug > 0:
        ergebnis.organic_share = round((ergebnis.organic_sessions / bezug) * 100, 2)

    # Ohne Seiten-Report (z. B. nur Kanäle vorhanden) trotzdem Summen füllen.
    if not seiten_zeilen and kanal_sitzungen_gesamt:
        ergebnis.total_sessions = kanal_sitzungen_gesamt
        ergebnis.total_users = sum(k["users"] for k in kanaele.values())

    return ergebnis


# ---------------------------------------------------------------------------
# Die Quelle
# ---------------------------------------------------------------------------


class GA4DataSource(DataSource):
    """Google Analytics Data API v1beta — Besucherdaten je Property.

    Anders als die GSC-Quelle wirft diese Klasse im Konstruktor nichts: Eine
    fehlende Bibliothek oder Schlüsseldatei ist kein Grund, den Audit
    abzubrechen. Der Zustand steht in `available` und `unavailable_reason`.
    """

    SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

    def __init__(self, credentials_path: str, property_id: str):
        """
        Args:
            credentials_path: Pfad zum Dienstkonto-JSON
            property_id: Die GA4-Property-ID (nur Ziffern, z. B. "123456789"),
                NICHT die Mess-ID "G-XXXXXXX"
        """
        self.credentials_path = Path(credentials_path) if credentials_path else None
        self.property_id = str(property_id or "").strip()
        self.client = None
        self.authenticated = False
        self.unavailable_reason: Optional[str] = None

        if not HAS_GA4_API:
            self.unavailable_reason = FEHLT_HINWEIS
            logger.warning(f"[GA4] {FEHLT_HINWEIS}")
        elif not self.property_id:
            self.unavailable_reason = "Keine property_id konfiguriert"
        elif self.credentials_path is None or not self.credentials_path.exists():
            self.unavailable_reason = (
                f"Zugangsdaten nicht gefunden: {self.credentials_path}"
            )

    # -- Zustand -----------------------------------------------------------

    @property
    def available(self) -> bool:
        """True, wenn Bibliothek, Property-ID und Schlüsseldatei da sind."""
        return self.unavailable_reason is None

    def status_text(self) -> str:
        """Kurzer Klartext für Log und Bericht."""
        if self.available:
            return f"GA4 bereit (Property {self.property_id})"
        return f"GA4 nicht verfügbar: {self.unavailable_reason}"

    # -- Verbindung --------------------------------------------------------

    async def authenticate(self) -> bool:
        """Meldet sich mit dem Dienstkonto an.

        Gibt False zurück statt zu werfen — der Aufrufer entscheidet, ob er
        ohne Besucherdaten weitermacht (in der Praxis: immer).
        """
        if not self.available:
            logger.info(f"[GA4] {self.status_text()}")
            return False

        try:
            credentials = Credentials.from_service_account_file(
                str(self.credentials_path), scopes=self.SCOPES
            )
            self.client = BetaAnalyticsDataClient(credentials=credentials)
            self.authenticated = True
            logger.info(f"[GA4] Angemeldet für Property {self.property_id}")
            return True
        except Exception as exc:
            self.unavailable_reason = f"Anmeldung fehlgeschlagen: {exc}"
            self.authenticated = False
            logger.warning(f"[GA4] {self.unavailable_reason}")
            return False

    async def test_connection(self) -> bool:
        """Prüft mit einer minimalen Abfrage, ob die Property lesbar ist."""
        if not self.authenticated and not await self.authenticate():
            return False
        try:
            self._run_report(
                dimensions=[],
                metrics=["activeUsers"],
                start_date="7daysAgo",
                end_date="today",
                limit=1,
            )
            return True
        except Exception as exc:
            logger.warning(f"[GA4] Verbindungstest fehlgeschlagen: {exc}")
            return False

    # -- Abruf -------------------------------------------------------------

    async def fetch(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[GA4Analytics]:
        """Holt die Besucherdaten für einen Zeitraum.

        Args:
            start_date: "2026-08-01" oder GA4-Kurzform wie "28daysAgo"
            end_date: "2026-08-17" oder "today"

        Returns:
            GA4Analytics — oder None, wenn die Quelle nicht verfügbar ist
            oder die API nicht antwortet. Es wird nie eine Ausnahme geworfen.
        """
        start_date = start_date or "28daysAgo"
        end_date = end_date or "today"

        if not self.authenticated and not await self.authenticate():
            return None

        try:
            seiten_antwort = self._run_report(
                dimensions=["pagePath"],
                metrics=KENNZAHLEN,
                start_date=start_date,
                end_date=end_date,
                limit=SEITEN_LIMIT,
            )
            kanal_antwort = self._run_report(
                dimensions=["sessionDefaultChannelGroup"],
                metrics=["activeUsers", "sessions"],
                start_date=start_date,
                end_date=end_date,
                limit=SEITEN_LIMIT,
            )
        except Exception as exc:
            logger.warning(f"[GA4] Abruf fehlgeschlagen: {exc}")
            return None

        return baue_analytics(
            zeilen_zu_dicts(seiten_antwort),
            zeilen_zu_dicts(kanal_antwort),
            start_date=start_date,
            end_date=end_date,
        )

    def _run_report(
        self,
        dimensions: List[str],
        metrics: List[str],
        start_date: str,
        end_date: str,
        limit: int = SEITEN_LIMIT,
    ) -> Any:
        """Eine einzelne Abfrage an die Data API. Rohantwort zurück."""
        request = RunReportRequest(
            property=f"properties/{self.property_id}",
            dimensions=[Dimension(name=d) for d in dimensions],
            metrics=[Metric(name=m) for m in metrics],
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            limit=limit,
        )
        return self.client.run_report(request)

    # -- Schnittstelle der Basisklasse ------------------------------------

    async def pull_analytics(
        self, domain: str, days: int = 28
    ) -> Optional[GA4Analytics]:
        """Besucherdaten der letzten `days` Tage (Signatur wie bei GSC).

        GA4 liefert keine Suchdaten, deshalb kommt hier `GA4Analytics`
        zurück und nicht `SearchAnalytics` — die beiden ergänzen sich.
        `domain` wird nicht gebraucht, die Property-ID bestimmt die Quelle.
        """
        ende = datetime.utcnow().date()
        beginn = ende - timedelta(days=days)
        return await self.fetch(beginn.isoformat(), ende.isoformat())

    async def pull_backlinks(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """GA4 kennt keine Backlinks."""
        logger.debug("[GA4] Keine Backlink-Daten in GA4 vorhanden.")
        return None

    async def pull_keywords(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """GA4 kennt keine Suchbegriffe (die stehen in der Search Console)."""
        logger.debug("[GA4] Keine Keyword-Daten in GA4 — dafür ist GSC zuständig.")
        return None


def erstelle_quelle(source_config: Dict[str, Any]) -> Optional[GA4DataSource]:
    """Baut die GA4-Quelle aus `source_config` eines Projekts.

    Gibt None zurück, wenn kein `ga4`-Abschnitt konfiguriert ist. Ein
    unvollständiger Abschnitt liefert trotzdem eine Quelle — sie meldet sich
    dann selbst als nicht verfügbar (mit Begründung im Log).
    """
    cfg = (source_config or {}).get("ga4")
    if not cfg:
        return None
    return GA4DataSource(
        credentials_path=cfg.get("credentials_path", ""),
        property_id=cfg.get("property_id", ""),
    )
