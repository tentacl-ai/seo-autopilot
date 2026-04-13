"""
SEO Autopilot – Multi-Tenant SEO Automation Platform

Version: 0.5.0
Author: SEO Autopilot Contributors
License: MIT

Features:
- Multi-project configuration
- Plugin-based data sources (GSC, PageSpeed Insights)
- AI-powered SEO agents (Claude API)
- Event-driven architecture
- REST API + CLI
- Multi-tenant ready
"""

__version__ = "0.5.0"
__author__ = "SEO Autopilot Contributors"

from .core.config import settings
from .core.project_manager import ProjectManager

__all__ = ["settings", "ProjectManager"]
