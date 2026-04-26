"""Apply-Adapters: setzen Fixes wirklich um (file write, git commit, REST call, ...).

Pro Project ein Adapter. Auswahl ueber `project.adapter_type`.
"""

from .static_files import StaticFilesAdapter, ApplyResult

__all__ = ["StaticFilesAdapter", "ApplyResult", "get_adapter"]


def get_adapter(adapter_type: str, config: dict):
    """Factory: liefert den passenden Adapter pro project.adapter_type."""
    if adapter_type == "static":
        return StaticFilesAdapter(config or {})
    raise ValueError(f"Unsupported adapter_type for auto-fix: {adapter_type!r}")
