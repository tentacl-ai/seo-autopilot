"""
Telegram notifier.

Sends a short audit summary to the configured Telegram chat.
Gracefully becomes a no-op when TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
are not set (e.g. in tests or local runs).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

from ..core.audit_context import AuditContext
from ..core.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE_LENGTH = 4096


async def send_audit_notification(ctx: AuditContext, report_path: Optional[Path] = None) -> bool:
    """Send a Telegram message summarising the audit. Returns True on success."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.info("Telegram not configured (no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) - skipping")
        return False

    text = _format_message(ctx, report_path)
    url = TELEGRAM_API.format(token=token, method="sendMessage")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data={
                "chat_id": chat_id,
                "text": text[:MAX_MESSAGE_LENGTH],
                "parse_mode": "Markdown",
                "disable_web_page_preview": "true",
            })
        if resp.status_code != 200:
            logger.warning(f"Telegram API error {resp.status_code}: {resp.text}")
            return False
        logger.info("Telegram notification sent")
        return True
    except Exception as exc:
        logger.warning(f"Telegram send failed: {exc}")
        return False


def _format_message(ctx: AuditContext, report_path: Optional[Path]) -> str:
    sev = ctx.issues_by_severity()
    summary = ctx.summary()

    score_emoji = "🟢" if (ctx.score or 0) >= 85 else "🟡" if (ctx.score or 0) >= 60 else "🔴"

    lines = [
        f"*SEO Audit · {summary['project_name']}*",
        f"{score_emoji} Score: *{ctx.score if ctx.score is not None else '–'}/100*",
        f"Domain: `{summary['domain']}`",
        "",
        f"Issues: *{summary['issues_total']}*",
        f"  🔴 High: {sev['high']}",
        f"  🟠 Medium: {sev['medium']}",
        f"  ⚪ Low: {sev['low']}",
    ]

    kw = ctx.agent_results.get("keyword")
    if kw and kw.metrics.get("gsc_available"):
        lines += [
            "",
            f"GSC 28d: *{kw.metrics.get('total_clicks', 0)} clicks*, "
            f"{kw.metrics.get('total_impressions', 0)} impressions, "
            f"CTR {kw.metrics.get('avg_ctr', 0)}%",
        ]

    # Top 3 high-priority actions
    strategy = ctx.agent_results.get("strategy")
    top_actions = (strategy.metrics.get("top_actions") if strategy else None) or []
    if top_actions:
        lines += ["", "*Top Actions:*"]
        for a in top_actions[:3]:
            lines.append(f"• {a.get('title', 'Issue')}")

    if report_path:
        lines += ["", f"Report: `{report_path}`"]

    return "\n".join(lines)
