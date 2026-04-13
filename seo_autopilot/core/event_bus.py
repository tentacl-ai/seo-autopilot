"""
Event Bus – Pub/Sub System für Audit-Events

Ermöglicht:
- Agenten triggern Events ("audit_complete", "issues_found", etc)
- REST API Clients können auf Events warten (WebSocket)
- Multi-Tenant Isolation (Events sind pro Projekt)
"""

from typing import Callable, Dict, List, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import asyncio
import logging

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Verfügbare Event-Typen"""

    AUDIT_STARTED = "audit_started"
    AUDIT_COMPLETED = "audit_completed"
    AUDIT_FAILED = "audit_failed"
    ANALYZER_STARTED = "analyzer_started"
    ANALYZER_COMPLETED = "analyzer_completed"
    KEYWORD_RESEARCH_STARTED = "keyword_research_started"
    KEYWORD_RESEARCH_COMPLETED = "keyword_research_completed"
    STRATEGY_STARTED = "strategy_started"
    STRATEGY_COMPLETED = "strategy_completed"
    CONTENT_GENERATION_STARTED = "content_generation_started"
    CONTENT_GENERATION_COMPLETED = "content_generation_completed"
    ISSUES_FOUND = "issues_found"
    SUGGESTIONS_GENERATED = "suggestions_generated"


@dataclass
class Event:
    """Ein Event mit Metadaten"""

    type: EventType
    project_id: str
    timestamp: datetime
    data: Dict[str, Any]
    tenant_id: str = None

    def to_dict(self):
        return {
            "type": self.type,
            "project_id": self.project_id,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "tenant_id": self.tenant_id,
        }


class EventBus:
    """Event Pub/Sub System"""

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._event_history: List[Event] = []
        self._max_history = 1000

    def subscribe(self, event_type: EventType, callback: Callable):
        """Subscribiere zu einem Event-Typ"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []

        self._subscribers[event_type].append(callback)
        logger.debug(f"Subscriber hinzugefügt: {event_type}")

    def unsubscribe(self, event_type: EventType, callback: Callable):
        """Abmelden von Events"""
        if event_type in self._subscribers:
            self._subscribers[event_type].remove(callback)

    async def emit(self, event: Event):
        """Publishe ein Event"""
        logger.info(f"Event emitted: {event.type} for {event.project_id}")

        # Speichere in Historie
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)

        # Rufe alle Subscriber auf
        if event.type in self._subscribers:
            tasks = [
                callback(event)
                for callback in self._subscribers[event.type]
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    def get_history(
        self, project_id: str = None, event_type: EventType = None, limit: int = 100
    ) -> List[Event]:
        """Hole Event-Historie"""
        history = list(reversed(self._event_history))

        if project_id:
            history = [e for e in history if e.project_id == project_id]

        if event_type:
            history = [e for e in history if e.type == event_type]

        return history[:limit]


# Singleton instance
event_bus = EventBus()
