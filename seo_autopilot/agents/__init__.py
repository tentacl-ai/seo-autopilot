"""Agent system for seo-autopilot

Agents orchestrate SEO analysis:
- AnalyzerAgent: Meta tags, performance, schema, security
- KeywordAgent: Keyword research, clustering, difficulty
- StrategyAgent: Issue prioritization, impact estimation
- ContentAgent: Auto-generate meta tag fixes
- MonitorAgent: Track metrics over time
"""

from .base import Agent, AgentError, AgentResult
from .analyzer import AnalyzerAgent

__all__ = ["Agent", "AgentError", "AgentResult", "AnalyzerAgent"]
