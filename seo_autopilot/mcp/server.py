"""SEO Autopilot MCP Server – expose audit pipeline to Claude."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from seo_autopilot.core.config import Settings
from seo_autopilot.core.project_manager import ProjectManager
from seo_autopilot.core.audit_context import AuditContext
from seo_autopilot.agents.analyzer import AnalyzerAgent
from seo_autopilot.agents.keyword import KeywordAgent
from seo_autopilot.agents.strategy import StrategyAgent
from seo_autopilot.agents.content import ContentAgent

logger = logging.getLogger(__name__)


class SEOAutopilotMCPServer:
    """MCP Server that exposes SEO audit pipeline as Claude tools."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.project_manager = ProjectManager(self.settings.projects_file)

    async def list_projects(self) -> dict[str, Any]:
        """List all configured SEO projects.

        Returns:
            Dict with project list and metadata
        """
        projects = self.project_manager.list_projects()
        return {
            "count": len(projects),
            "projects": [
                {
                    "id": p["id"],
                    "name": p.get("name", p["id"]),
                    "domain": p.get("domain", ""),
                    "enabled": p.get("enabled", True),
                }
                for p in projects
            ],
        }

    async def run_audit(self, project_id: str, max_pages: int = 15) -> dict[str, Any]:
        """Run a complete SEO audit on a project.

        Args:
            project_id: Project identifier from config
            max_pages: Maximum pages to crawl (default: 15)

        Returns:
            Audit result with issues, score, recommendations
        """
        try:
            project = self.project_manager.get_project(project_id)
            if not project:
                return {"error": f"Project '{project_id}' not found"}

            # Create audit context and run pipeline
            context = AuditContext(
                project_id=project_id,
                tenant_id=project.get("tenant_id", project_id),
            )

            # Run analyzer
            analyzer = AnalyzerAgent(
                project_id=project_id,
                audit_id=context.audit_id,
                context=context,
            )
            analyzer_result = await analyzer.run()

            # Run keyword agent
            keyword = KeywordAgent(
                project_id=project_id,
                audit_id=context.audit_id,
                context=context,
            )
            keyword_result = await keyword.run()

            # Run strategy agent
            strategy = StrategyAgent(
                project_id=project_id,
                audit_id=context.audit_id,
                context=context,
            )
            strategy_result = await strategy.run()

            # Run content agent
            content = ContentAgent(
                project_id=project_id,
                audit_id=context.audit_id,
                context=context,
                settings=self.settings,
            )
            content_result = await content.run()

            # Calculate score
            all_issues = list(context.all_issues)
            high_count = sum(1 for i in all_issues if i.get("severity") == "high")
            medium_count = sum(1 for i in all_issues if i.get("severity") == "medium")
            score = max(0, 100 - (high_count * 10 + medium_count * 3))

            return {
                "project_id": project_id,
                "audit_id": context.audit_id,
                "score": score,
                "total_issues": len(all_issues),
                "high_severity": high_count,
                "medium_severity": medium_count,
                "pages_crawled": len(context.pages),
                "quick_wins": len(
                    [i for i in all_issues if i.get("priority") == "high"]
                ),
                "agents": {
                    "analyzer": {
                        "status": analyzer_result.status.value,
                        "issues": len(analyzer_result.issues),
                        "duration_seconds": analyzer_result.duration_seconds,
                    },
                    "keyword": {
                        "status": keyword_result.status.value,
                        "opportunities": len(keyword_result.metrics.get("opportunities", [])),
                    },
                    "strategy": {
                        "status": strategy_result.status.value,
                        "quick_wins": strategy_result.metrics.get("quick_wins", 0),
                        "this_week": strategy_result.metrics.get("this_week", 0),
                    },
                    "content": {
                        "status": content_result.status.value,
                        "fixes_generated": len(content_result.metrics.get("fixes", [])),
                    },
                },
                "top_actions": strategy_result.metrics.get("top_actions", [])[:5],
            }

        except Exception as e:
            logger.exception(f"Audit failed for project {project_id}")
            return {"error": str(e), "project_id": project_id}

    async def get_audit_results(self, audit_id: str) -> dict[str, Any]:
        """Retrieve results of a previous audit.

        Args:
            audit_id: Audit identifier

        Returns:
            Full audit data with issues, recommendations, generated fixes
        """
        # TODO: Implement database retrieval
        return {"error": "get_audit_results not yet implemented"}

    def get_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions for Claude.

        Returns:
            List of tool definitions in MCP format
        """
        return [
            {
                "name": "list_projects",
                "description": "List all configured SEO projects available for auditing",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "run_audit",
                "description": "Run a complete SEO audit on a project (crawls, analyzes, and provides recommendations)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project identifier (e.g., 'tentacl-ai')",
                        },
                        "max_pages": {
                            "type": "integer",
                            "description": "Maximum pages to crawl (default: 15)",
                            "default": 15,
                        },
                    },
                    "required": ["project_id"],
                },
            },
        ]

    async def handle_tool_call(self, tool_name: str, args: dict) -> dict[str, Any]:
        """Handle a tool call from Claude.

        Args:
            tool_name: Name of the tool to call
            args: Arguments for the tool

        Returns:
            Tool result
        """
        if tool_name == "list_projects":
            return await self.list_projects()
        elif tool_name == "run_audit":
            return await self.run_audit(
                args["project_id"],
                args.get("max_pages", 15),
            )
        else:
            return {"error": f"Unknown tool: {tool_name}"}
