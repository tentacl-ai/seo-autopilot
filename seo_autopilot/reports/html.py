"""
HTML report renderer for an AuditContext.

Writes a self-contained HTML file with inline CSS into
<project_root>/reports/<project_id>/<audit_id>.html and also updates
<project_id>/latest.html as a convenience symlink.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..core.audit_context import AuditContext

logger = logging.getLogger(__name__)


REPORT_ROOT = Path(__file__).resolve().parent.parent.parent / "reports"
TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_html_report(ctx: AuditContext) -> Path:
    """Render the audit into a static HTML file and return its path."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html")

    html = template.render(
        ctx=ctx,
        summary=ctx.summary(),
        top_actions=_top_actions(ctx),
        fixes=ctx.all_fixes,
        severities=ctx.issues_by_severity(),
        categories=ctx.issues_by_category(),
        keyword_metrics=_keyword_metrics(ctx),
    )

    project_dir = REPORT_ROOT / ctx.project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    file_path = project_dir / f"{ctx.audit_id}.html"
    file_path.write_text(html, encoding="utf-8")

    latest = project_dir / "latest.html"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(file_path.name)
    except OSError:
        # symlink not supported, copy instead
        latest.write_text(html, encoding="utf-8")

    logger.info(f"Report saved: {file_path}")
    return file_path


def _top_actions(ctx: AuditContext):
    strategy = ctx.agent_results.get("strategy")
    if strategy and strategy.metrics.get("top_actions"):
        return strategy.metrics["top_actions"]
    return ctx.all_issues[:10]


def _keyword_metrics(ctx: AuditContext):
    kw = ctx.agent_results.get("keyword")
    if not kw:
        return None
    return kw.metrics
