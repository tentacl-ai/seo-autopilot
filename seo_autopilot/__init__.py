"""
SEO Autopilot – Multi-Tenant SEO Automation Platform

Version: 0.1.0
Author: Tentacl
License: Proprietary

Features:
- Multi-project configuration
- Plugin-based data sources (GSC, Ahrefs, Semrush, Lighthouse)
- AI-powered SEO agents (Claude/Gemini)
- Event-driven architecture
- REST API + CLI
- Multi-tenant ready
"""

__version__ = "0.1.0"
__author__ = "Tentacl"

from .core.config import settings
from .core.project_manager import ProjectManager

__all__ = ["settings", "ProjectManager"]
