"""
Abstract Agent Base Class

All agents:
1. Subscribe to events
2. Operate on a project
3. Emit analysis results as events
4. Store findings in database
5. Optionally generate auto-fixes
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from datetime import datetime
from enum import Enum
import logging

from ..core.project_manager import ProjectConfig
from ..core.event_bus import event_bus, EventType, Event

if TYPE_CHECKING:
    from ..core.audit_context import AuditContext

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    """Agent execution status"""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class AgentResult:
    """Result of agent execution"""

    status: AgentStatus
    agent_name: str
    project_id: str
    audit_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metrics: Dict[str, Any] = field(default_factory=dict)  # Findings, scores, etc
    issues: List[Dict[str, Any]] = field(default_factory=list)  # Auto-detected issues
    fixes: List[Dict[str, Any]] = field(default_factory=list)  # Suggested fixes
    log_output: str = ""
    errors: List[str] = field(default_factory=list)
    duration_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for events/storage"""
        return {
            "status": self.status.value,
            "agent_name": self.agent_name,
            "project_id": self.project_id,
            "audit_id": self.audit_id,
            "timestamp": self.timestamp.isoformat(),
            "metrics": self.metrics,
            "issues": self.issues,
            "fixes": self.fixes,
            "log_output": self.log_output,
            "errors": self.errors,
            "duration_seconds": self.duration_seconds,
        }


class AgentError(Exception):
    """Base exception for agent errors"""

    pass


class Agent(ABC):
    """
    Abstract base class for all SEO agents.

    Subclasses implement specific analysis tasks:
    - analyze(): Run the analysis
    - emit_events(): Report results
    - store_results(): Persist findings
    """

    def __init__(self, project_id: str, audit_id: str, project_config: ProjectConfig,
                 context: Optional["AuditContext"] = None):
        self.project_id = project_id
        self.audit_id = audit_id
        self.project_config = project_config
        self.context = context  # AuditContext, set by run_audit_for_project
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent name (e.g., 'analyzer', 'keyword', 'strategy')"""
        pass

    @property
    @abstractmethod
    def event_type(self) -> EventType:
        """Event type emitted when complete"""
        pass

    @abstractmethod
    async def run(self) -> AgentResult:
        """
        Execute agent analysis.

        Returns:
            AgentResult with metrics, issues, fixes
        """
        pass

    async def emit_result(self, result: AgentResult) -> None:
        """Emit result as event for real-time clients"""
        event = Event(
            type=self.event_type,
            project_id=self.project_id,
            tenant_id=getattr(self.project_config, "tenant_id", None),
            timestamp=datetime.utcnow(),
            data=result.to_dict(),
        )
        await event_bus.emit(event)
        self.logger.info(f"✅ {self.name} completed: {result.status.value}")

    async def emit_started(self) -> None:
        """Emit agent started event"""
        event = Event(
            type=EventType.ANALYZER_STARTED,  # Generic started type
            project_id=self.project_id,
            tenant_id=getattr(self.project_config, "tenant_id", None),
            timestamp=datetime.utcnow(),
            data={"agent": self.name, "audit_id": self.audit_id},
        )
        await event_bus.emit(event)

    async def emit_error(self, error: str) -> None:
        """Emit error event"""
        result = AgentResult(
            status=AgentStatus.FAILED,
            agent_name=self.name,
            project_id=self.project_id,
            audit_id=self.audit_id,
            errors=[error],
        )
        await self.emit_result(result)
