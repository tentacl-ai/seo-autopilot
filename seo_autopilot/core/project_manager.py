"""
Project Manager – Multi-Tenant Project Configuration & CRUD

Provides:
- Multiple projects (domains) in a single instance
- Different adapters per project (Static HTML, WordPress, etc)
- Different data sources per project
- Tenant isolation

Data persistence: YAML config + SQLite/PostgreSQL
"""

import yaml
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class ProjectConfig:
    """A single SEO project / domain"""

    # Identifiers
    id: str  # e.g. "tentacl-ai", "myshop-de"
    domain: str  # e.g. "https://tentacl.ai"
    name: str  # Display name
    tenant_id: Optional[str] = None  # Multi-tenant: which customer?

    # Adapter type (how to access the site)
    adapter_type: str = "static"  # static | wordpress | fastapi | generic
    adapter_config: Dict[str, Any] = None  # e.g. {"root_path": "/opt/apps/..."}

    # Data sources (which data we pull)
    enabled_sources: List[str] = None  # e.g. ["gsc", "lighthouse"]
    source_config: Dict[str, Any] = None  # e.g. {"gsc": {"property_url": "..."}}

    # Scheduling
    enabled: bool = True
    schedule_cron: str = "0 7 * * 1"  # Monday 07:00
    last_run_at: Optional[datetime] = None
    run_interval_days: int = 7

    # Notifications
    notifications_enabled: bool = True
    notify_channels: List[str] = None  # e.g. ["telegram", "email"]
    notify_config: Dict[str, Any] = None  # e.g. {"email": "admin@..."}

    # Auto-Fix-Loop (Welle 2)
    auto_fix_enabled: bool = False
    auto_fix_config: Dict[str, Any] = (
        None  # {whitelist_extra: [...], push_to_remote, ...}
    )

    # Metadata
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.adapter_config is None:
            self.adapter_config = {}
        if self.source_config is None:
            self.source_config = {}
        if self.enabled_sources is None:
            self.enabled_sources = ["gsc"]
        if self.notify_channels is None:
            self.notify_channels = ["telegram"]
        if self.notify_config is None:
            self.notify_config = {}
        if self.auto_fix_config is None:
            self.auto_fix_config = {}
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow()


class ProjectManager:
    """Manage all SEO projects"""

    def __init__(self, config_path: str = None):
        self.config_path = Path(
            config_path
            or Path(__file__).resolve().parent.parent.parent / "projects.yaml"
        )
        self.projects: Dict[str, ProjectConfig] = {}
        self._load_config()

    def _load_config(self):
        """Load projects from YAML"""
        if not self.config_path.exists():
            logger.warning(
                f"Config file not found: {self.config_path}. Starting with empty list."
            )
            return

        try:
            with open(self.config_path) as f:
                data = yaml.safe_load(f) or {}

            for project_id, cfg in data.get("projects", {}).items():
                self.projects[project_id] = ProjectConfig(id=project_id, **cfg)
            logger.info(f"Loaded {len(self.projects)} projects from {self.config_path}")
        except Exception as e:
            logger.error(f"Error loading config: {e}")

    def _save_config(self):
        """Save projects to YAML"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "projects": {
                pid: {k: v for k, v in asdict(cfg).items() if k != "id"}
                for pid, cfg in self.projects.items()
            }
        }

        with open(self.config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Configuration saved: {self.config_path}")

    def add_project(
        self,
        project_id: str,
        domain: str,
        name: str,
        adapter_type: str = "static",
        adapter_config: Dict = None,
        enabled_sources: List[str] = None,
        **kwargs,
    ) -> ProjectConfig:
        """Add a new project"""

        if project_id in self.projects:
            raise ValueError(f"Project '{project_id}' already exists")

        cfg = ProjectConfig(
            id=project_id,
            domain=domain,
            name=name,
            adapter_type=adapter_type,
            adapter_config=adapter_config or {},
            enabled_sources=enabled_sources or ["gsc"],
            **kwargs,
        )

        self.projects[project_id] = cfg
        self._save_config()
        logger.info(f"Project added: {project_id} ({domain})")
        return cfg

    def get_project(self, project_id: str) -> Optional[ProjectConfig]:
        """Get a project"""
        return self.projects.get(project_id)

    def list_projects(
        self, tenant_id: str = None, enabled_only: bool = False
    ) -> List[ProjectConfig]:
        """List all projects (optionally filtered)"""
        projects = list(self.projects.values())

        if tenant_id:
            projects = [p for p in projects if p.tenant_id == tenant_id]

        if enabled_only:
            projects = [p for p in projects if p.enabled]

        return projects

    def update_project(self, project_id: str, **updates) -> ProjectConfig:
        """Update a project"""
        if project_id not in self.projects:
            raise ValueError(f"Project '{project_id}' not found")

        cfg = self.projects[project_id]
        for key, value in updates.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)

        cfg.updated_at = datetime.utcnow()
        self._save_config()
        logger.info(f"Project updated: {project_id}")
        return cfg

    def delete_project(self, project_id: str) -> bool:
        """Delete a project"""
        if project_id not in self.projects:
            raise ValueError(f"Project '{project_id}' not found")

        del self.projects[project_id]
        self._save_config()
        logger.info(f"Project deleted: {project_id}")
        return True

    def get_enabled_projects(self) -> List[ProjectConfig]:
        """Get all enabled projects (for scheduler)"""
        return [p for p in self.projects.values() if p.enabled]

    def export_config(self, format: str = "yaml") -> str:
        """Exportiere Config als String"""
        if format == "json":
            return json.dumps(
                {pid: asdict(cfg) for pid, cfg in self.projects.items()},
                indent=2,
                default=str,
            )
        else:
            data = {
                "projects": {
                    pid: {k: v for k, v in asdict(cfg).items() if k != "id"}
                    for pid, cfg in self.projects.items()
                }
            }
            return yaml.dump(data, default_flow_style=False, sort_keys=False)
