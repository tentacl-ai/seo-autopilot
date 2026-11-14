"""Compatibility entrypoint for the project-scoped workspace tools.

No audit pipeline is duplicated here. Launch the stdio server with
`python -m seo_autopilot.arbeitsplatz --config <path> --stdio`.
"""

from __future__ import annotations

import os

from ..arbeitsplatz.__main__ import from_config
from ..arbeitsplatz.tools import call, list_tools


class SEOAutopilotMCPServer:
    def __init__(self, settings=None, workspace=None, principal=None):
        if workspace is None:
            path = os.environ.get("SEO_WORKSPACE_CONFIG")
            if not path:
                raise ValueError("SEO_WORKSPACE_CONFIG is required")
            workspace, access, _ = from_config(path)
            principal = access.authenticate(os.environ.get("SEO_WORKSPACE_TOKEN", ""))
        if principal is None:
            raise ValueError("A project-scoped principal is required")
        self.workspace = workspace
        self.principal = principal

    def get_tools(self):
        return list_tools()

    async def handle_tool_call(self, tool_name, args):
        return await call(self.workspace, self.principal, tool_name, args)
