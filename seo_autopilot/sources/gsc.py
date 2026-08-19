"""
Google Search Console Data Source

Pulls weekly data from GSC:
- Top keywords
- Top pages
- Device distribution
- Geographic distribution
"""

from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List
import logging

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    HAS_GOOGLE_API = True
except ImportError:
    HAS_GOOGLE_API = False

from .base import DataSource, SearchAnalytics, DataSourceError

logger = logging.getLogger(__name__)


class GSCDataSource(DataSource):
    """Google Search Console API Integration"""

    SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

    def __init__(self, credentials_path: str):
        """
        Initialize GSC data source

        Args:
            credentials_path: Path to service account JSON
        """
        if not HAS_GOOGLE_API:
            raise DataSourceError(
                "Google API libraries not installed. Install: google-auth-oauthlib"
            )

        self.credentials_path = Path(credentials_path)
        self.service = None
        self.authenticated = False

    async def authenticate(self) -> bool:
        """Authenticate with service account"""
        try:
            if not self.credentials_path.exists():
                raise FileNotFoundError(
                    f"Credentials not found: {self.credentials_path}"
                )

            credentials = Credentials.from_service_account_file(
                self.credentials_path, scopes=self.SCOPES
            )
            self.service = build("webmasters", "v3", credentials=credentials)
            self.authenticated = True
            logger.info("GSC authenticated successfully")
            return True

        except Exception as e:
            logger.error(f"GSC authentication failed: {e}")
            self.authenticated = False
            raise DataSourceError(f"GSC Auth Error: {e}")

    async def test_connection(self) -> bool:
        """Test connection to GSC"""
        try:
            if not self.authenticated:
                await self.authenticate()

            # Try the simplest query
            self.service.webmasters().sites().list().execute()
            return True
        except Exception as e:
            logger.error(f"GSC connection test failed: {e}")
            return False

    async def pull_analytics(
        self, domain: str, days: int = 28
    ) -> Optional[SearchAnalytics]:
        """
        Pull GSC analytics for a domain

        Args:
            domain: e.g. "https://tentacl.ai"
            days: How many days to look back

        Returns:
            SearchAnalytics or None on error
        """
        try:
            if not self.authenticated:
                await self.authenticate()

            end_date = datetime.utcnow().date()
            start_date = end_date - timedelta(days=days)

            # GSC API Request
            request = {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": ["query", "page", "device", "country"],
                "rowLimit": 25000,
            }

            response = (
                self.service.searchanalytics()
                .query(siteUrl=domain, body=request)
                .execute()
            )

            rows = response.get("rows", [])
            return self._parse_analytics(rows)

        except Exception as e:
            logger.error(f"GSC pull_analytics failed for {domain}: {e}")
            raise DataSourceError(f"GSC API Error: {e}")

    def _parse_analytics(self, rows: List[Dict]) -> SearchAnalytics:
        """Parse GSC rows in SearchAnalytics structure"""

        stats = {
            "total_clicks": 0,
            "total_impressions": 0,
            "avg_position": 0,
            "avg_ctr": 0,
            "top_queries": [],
            "top_pages": [],
            "by_device": {},
            "by_country": {},
        }

        if not rows:
            return SearchAnalytics(**stats)

        queries = {}
        pages = {}
        devices = {}
        countries = {}

        for row in rows:
            clicks = row.get("clicks", 0)
            impressions = row.get("impressions", 0)
            position = row.get("position", 0)

            stats["total_clicks"] += clicks
            stats["total_impressions"] += impressions

            # Top Queries
            keys = row.get("keys", [None, None, None, None])
            query = keys[0] if len(keys) > 0 else "unknown"

            if query not in queries:
                queries[query] = {
                    "clicks": 0,
                    "impressions": 0,
                    "position": 0,
                    "count": 0,
                }
            queries[query]["clicks"] += clicks
            queries[query]["impressions"] += impressions
            queries[query]["position"] += position
            queries[query]["count"] += 1

            # Top Pages
            page = keys[1] if len(keys) > 1 else "unknown"
            if page not in pages:
                pages[page] = {"clicks": 0, "impressions": 0}
            pages[page]["clicks"] += clicks
            pages[page]["impressions"] += impressions

            # By Device
            device = keys[2] if len(keys) > 2 else "unknown"
            if device not in devices:
                devices[device] = {"clicks": 0, "impressions": 0}
            devices[device]["clicks"] += clicks
            devices[device]["impressions"] += impressions

            # By Country
            country = keys[3] if len(keys) > 3 else "unknown"
            if country not in countries:
                countries[country] = {"clicks": 0, "impressions": 0}
            countries[country]["clicks"] += clicks
            countries[country]["impressions"] += impressions

        # Calculate averages
        for query in queries:
            if queries[query]["count"] > 0:
                queries[query]["position"] = round(
                    queries[query]["position"] / queries[query]["count"], 2
                )
            del queries[query]["count"]

        # Top 10
        stats["top_queries"] = sorted(
            [{"query": q, **data} for q, data in queries.items()],
            key=lambda x: x["clicks"],
            reverse=True,
        )[:10]

        stats["top_pages"] = sorted(
            [{"page": p, **data} for p, data in pages.items()],
            key=lambda x: x["clicks"],
            reverse=True,
        )[:10]

        stats["by_device"] = devices
        stats["by_country"] = countries

        if stats["total_impressions"] > 0:
            stats["avg_ctr"] = round(
                (stats["total_clicks"] / stats["total_impressions"]) * 100, 2
            )

        if rows:
            stats["avg_position"] = round(
                sum(r.get("position", 0) for r in rows) / len(rows), 2
            )

        return SearchAnalytics(**stats)

    async def pull_url_window(
        self,
        property_url: str,
        page_url: str,
        start_date: "date",
        end_date: "date",
    ) -> Optional[Dict[str, Any]]:
        """Kennzahlen EINER Adresse in einem festen Zeitfenster.

        Grundlage der Wirkungsmessung: `pull_analytics` liefert immer nur die
        letzten N Tage ab heute und aggregiert ueber die ganze Property. Fuer
        die Frage "hat diese Aenderung an dieser Seite gewirkt" braucht es
        beides genauer — eine Adresse, zwei frei waehlbare Fenster.

        `start_date`/`end_date` sind inklusiv, so wie die GSC-API sie versteht.

        Rueckgabe: dict mit `clicks`, `impressions`, `ctr`, `position`,
        `hat_daten`. Kein Treffer heisst nicht "Fehler", sondern schlicht: Die
        Seite hatte in diesem Zeitraum keine Sichtbarkeit — dann stehen Nullen
        und `hat_daten=False` drin. `None` kommt nur bei einem echten Fehler
        zurueck, damit die Wirkungsmessung "keine Daten" nicht mit "Abfrage
        kaputt" verwechselt und daraus ein Urteil bastelt.
        """
        try:
            if not self.authenticated:
                await self.authenticate()

            request = {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": ["page"],
                "dimensionFilterGroups": [
                    {
                        "filters": [
                            {
                                "dimension": "page",
                                "operator": "equals",
                                "expression": page_url,
                            }
                        ]
                    }
                ],
                "rowLimit": 1,
            }

            response = (
                self.service.searchanalytics()
                .query(siteUrl=property_url, body=request)
                .execute()
            )

            rows = response.get("rows", [])
            if not rows:
                return {
                    "clicks": 0,
                    "impressions": 0,
                    "ctr": 0.0,
                    "position": 0.0,
                    "hat_daten": False,
                }

            row = rows[0]
            return {
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr": round(float(row.get("ctr", 0.0)) * 100, 2),
                "position": round(float(row.get("position", 0.0)), 2),
                "hat_daten": True,
            }

        except Exception as e:
            logger.error(
                f"GSC pull_url_window failed for {page_url} "
                f"({start_date}..{end_date}): {e}"
            )
            return None

    async def pull_range(
        self,
        property_url: str,
        start_date: "date",
        end_date: "date",
        dimensions: Optional[List[str]] = None,
        row_limit: int = 5000,
    ) -> Optional[List[Dict[str, Any]]]:
        """Rohzeilen fuer einen frei waehlbaren Zeitraum.

        Grundlage des Historien-Imports. `pull_analytics` kann nur "die letzten
        N Tage ab heute" und aggregiert dabei ueber vier Dimensionen
        gleichzeitig; `pull_url_window` kann nur genau eine Adresse. Fuer eine
        Zeitreihe braucht es beides nicht: einen festen Monat, frei gewaehlte
        Dimensionen (oder gar keine, dann liefert Google die Gesamtsumme).

        `start_date`/`end_date` sind inklusiv (so versteht die GSC-API sie).
        `dimensions=None` bzw. `[]` heisst: eine einzige Zeile mit den
        Gesamtwerten des Zeitraums.

        Rueckgabe: Liste der Rohzeilen (`keys`, `clicks`, `impressions`,
        `ctr`, `position`). Leere Liste heisst "in diesem Zeitraum keine
        Sichtbarkeit" — eine Tatsache. `None` heisst "Abfrage fehlgeschlagen"
        — ein Fehler. Diese Unterscheidung ist der ganze Sinn des
        Rueckgabewerts: Wer einen API-Fehler als "null Klicks" in eine
        Zeitreihe schreibt, erzeugt einen Einbruch, den es nie gab.
        """
        try:
            if not self.authenticated:
                await self.authenticate()

            request: Dict[str, Any] = {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "rowLimit": row_limit,
            }
            if dimensions:
                request["dimensions"] = list(dimensions)

            response = (
                self.service.searchanalytics()
                .query(siteUrl=property_url, body=request)
                .execute()
            )
            return response.get("rows", [])

        except Exception as e:
            logger.error(
                f"GSC pull_range failed for {property_url} "
                f"({start_date}..{end_date}, dims={dimensions}): {e}"
            )
            return None

    async def pull_backlinks(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """GSC has no backlink API – not implemented"""
        logger.warning(
            "GSC does not provide backlink data. Use Ahrefs/Semrush instead."
        )
        return None

    async def pull_keywords(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """Pull keywords from top queries"""
        try:
            analytics = await self.pull_analytics(domain)
            if not analytics:
                return None

            return [
                {
                    "keyword": q["query"],
                    "clicks": q["clicks"],
                    "impressions": q["impressions"],
                    "position": q["position"],
                    "ctr": round((q["clicks"] / max(q["impressions"], 1)) * 100, 2),
                }
                for q in analytics.top_queries
            ]
        except Exception as e:
            logger.error(f"GSC pull_keywords failed: {e}")
            return None
