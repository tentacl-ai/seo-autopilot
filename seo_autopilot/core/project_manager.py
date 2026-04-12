"""
Project Manager – Multi-Tenant Project Configuration & CRUD

Ermöglicht:
- Mehrere Projekte (Domains) in einer Instanz
- Verschiedene Adapter pro Projekt (Static HTML, WordPress, etc)
- Verschiedene Data Sources pro Projekt
- Tenant-Isolation

Daten-Persistierung: YAML Config + SQLite/PostgreSQL
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
    """Ein SEO-Projekt / eine Domain"""

    # Identifikatoren
    id: str  # z.B. "tentacl-ai", "myshop-de"
    domain: str  # z.B. "https://tentacl.ai"
    name: str  # Display name
    tenant_id: Optional[str] = None  # Multi-tenant: welcher Kunde?

    # Adapter-Typ (wie man auf die Site zugreift)
    adapter_type: str = "static"  # static | wordpress | fastapi | generic
    adapter_config: Dict[str, Any] = None  # z.B. {"root_path": "/opt/apps/..."}

    # Data Sources (welche Daten wir pullen)
    enabled_sources: List[str] = None  # z.B. ["gsc", "lighthouse"]
    source_config: Dict[str, Any] = None  # z.B. {"gsc": {"property_url": "..."}}

    # Scheduling
    enabled: bool = True
    schedule_cron: str = "0 7 * * 1"  # Montag 07:00
    last_run_at: Optional[datetime] = None
    run_interval_days: int = 7

    # Notifications
    notifications_enabled: bool = True
    notify_channels: List[str] = None  # z.B. ["telegram", "email"]
    notify_config: Dict[str, Any] = None  # z.B. {"email": "admin@..."}

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
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow()


class ProjectManager:
    """Verwalte alle SEO-Projekte"""

    def __init__(self, config_path: str = None):
        self.config_path = Path(config_path or "/opt/odoo/docs/seo-autopilot/projects.yaml")
        self.projects: Dict[str, ProjectConfig] = {}
        self._load_config()

    def _load_config(self):
        """Lade Projekte aus YAML"""
        if not self.config_path.exists():
            logger.warning(f"Config file nicht gefunden: {self.config_path}. Starte mit leerer Liste.")
            return

        try:
            with open(self.config_path) as f:
                data = yaml.safe_load(f) or {}

            for project_id, cfg in data.get("projects", {}).items():
                self.projects[project_id] = ProjectConfig(
                    id=project_id,
                    **cfg
                )
            logger.info(f"Geladen {len(self.projects)} Projekte aus {self.config_path}")
        except Exception as e:
            logger.error(f"Fehler beim Laden der Config: {e}")

    def _save_config(self):
        """Speichere Projekte in YAML"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "projects": {
                pid: {k: v for k, v in asdict(cfg).items() if k != "id"}
                for pid, cfg in self.projects.items()
            }
        }

        with open(self.config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Konfiguration gespeichert: {self.config_path}")

    def add_project(
        self,
        project_id: str,
        domain: str,
        name: str,
        adapter_type: str = "static",
        adapter_config: Dict = None,
        enabled_sources: List[str] = None,
        **kwargs
    ) -> ProjectConfig:
        """Füge ein neues Projekt hinzu"""

        if project_id in self.projects:
            raise ValueError(f"Projekt '{project_id}' existiert bereits")

        cfg = ProjectConfig(
            id=project_id,
            domain=domain,
            name=name,
            adapter_type=adapter_type,
            adapter_config=adapter_config or {},
            enabled_sources=enabled_sources or ["gsc"],
            **kwargs
        )

        self.projects[project_id] = cfg
        self._save_config()
        logger.info(f"Projekt hinzugefügt: {project_id} ({domain})")
        return cfg

    def get_project(self, project_id: str) -> Optional[ProjectConfig]:
        """Hole ein Projekt"""
        return self.projects.get(project_id)

    def list_projects(self, tenant_id: str = None, enabled_only: bool = False) -> List[ProjectConfig]:
        """Liste alle Projekte (optional gefiltert)"""
        projects = list(self.projects.values())

        if tenant_id:
            projects = [p for p in projects if p.tenant_id == tenant_id]

        if enabled_only:
            projects = [p for p in projects if p.enabled]

        return projects

    def update_project(self, project_id: str, **updates) -> ProjectConfig:
        """Update ein Projekt"""
        if project_id not in self.projects:
            raise ValueError(f"Projekt '{project_id}' nicht gefunden")

        cfg = self.projects[project_id]
        for key, value in updates.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)

        cfg.updated_at = datetime.utcnow()
        self._save_config()
        logger.info(f"Projekt aktualisiert: {project_id}")
        return cfg

    def delete_project(self, project_id: str) -> bool:
        """Lösche ein Projekt"""
        if project_id not in self.projects:
            raise ValueError(f"Projekt '{project_id}' nicht gefunden")

        del self.projects[project_id]
        self._save_config()
        logger.info(f"Projekt gelöscht: {project_id}")
        return True

    def get_enabled_projects(self) -> List[ProjectConfig]:
        """Hole alle aktivierten Projekte (für Scheduler)"""
        return [p for p in self.projects.values() if p.enabled]

    def export_config(self, format: str = "yaml") -> str:
        """Exportiere Config als String"""
        if format == "json":
            return json.dumps(
                {pid: asdict(cfg) for pid, cfg in self.projects.items()},
                indent=2,
                default=str
            )
        else:
            data = {
                "projects": {
                    pid: {k: v for k, v in asdict(cfg).items() if k != "id"}
                    for pid, cfg in self.projects.items()
                }
            }
            return yaml.dump(data, default_flow_style=False, sort_keys=False)
