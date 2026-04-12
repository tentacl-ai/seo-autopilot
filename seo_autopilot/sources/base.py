"""
Abstract Data Source – Basis-Klasse für alle Datenquellen

Alle Sources (GSC, Ahrefs, Semrush, Lighthouse) erben davon.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SearchAnalytics:
    """Struktur für Search-Daten"""

    total_clicks: int
    total_impressions: int
    avg_ctr: float
    avg_position: float
    top_queries: List[Dict[str, Any]]
    top_pages: List[Dict[str, Any]]
    by_device: Dict[str, Dict[str, int]]
    by_country: Dict[str, Dict[str, int]]


class DataSource(ABC):
    """Abstract Base Class für Datenquellen"""

    @abstractmethod
    async def authenticate(self) -> bool:
        """Authentifiziere bei der Datenquelle"""
        pass

    @abstractmethod
    async def pull_analytics(self, domain: str, days: int = 28) -> Optional[SearchAnalytics]:
        """Ziehe Search-Analytics (z.B. GSC, Semrush)"""
        pass

    @abstractmethod
    async def pull_backlinks(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """Ziehe Backlink-Daten (falls verfügbar)"""
        pass

    @abstractmethod
    async def pull_keywords(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """Ziehe Keyword-Daten (falls verfügbar)"""
        pass

    @abstractmethod
    async def test_connection(self) -> bool:
        """Teste ob die Verbindung funktioniert"""
        pass


class DataSourceError(Exception):
    """Base exception für Data Source Fehler"""

    pass
