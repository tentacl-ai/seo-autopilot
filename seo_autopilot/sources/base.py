"""
Abstract Data Source – Base class for all data sources

All sources (GSC, Ahrefs, Semrush, Lighthouse) inherit from this.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SearchAnalytics:
    """Structure for search data"""

    total_clicks: int
    total_impressions: int
    avg_ctr: float
    avg_position: float
    top_queries: List[Dict[str, Any]]
    top_pages: List[Dict[str, Any]]
    by_device: Dict[str, Dict[str, int]]
    by_country: Dict[str, Dict[str, int]]


class DataSource(ABC):
    """Abstract base class for data sources"""

    @abstractmethod
    async def authenticate(self) -> bool:
        """Authenticate with the data source"""
        pass

    @abstractmethod
    async def pull_analytics(self, domain: str, days: int = 28) -> Optional[SearchAnalytics]:
        """Pull search analytics (e.g. GSC, Semrush)"""
        pass

    @abstractmethod
    async def pull_backlinks(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """Pull backlink data (if available)"""
        pass

    @abstractmethod
    async def pull_keywords(self, domain: str) -> Optional[List[Dict[str, Any]]]:
        """Pull keyword data (if available)"""
        pass

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test whether the connection works"""
        pass


class DataSourceError(Exception):
    """Base exception for data source errors"""

    pass
