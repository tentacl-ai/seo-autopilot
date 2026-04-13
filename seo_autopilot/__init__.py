"""
SEO Autopilot – Multi-Tenant SEO Automation Platform

Version: 1.0.2
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

__version__ = "1.0.2"
__author__ = "SEO Autopilot Contributors"

from .core.config import settings
from .core.project_manager import ProjectManager

__all__ = ["settings", "ProjectManager"]
