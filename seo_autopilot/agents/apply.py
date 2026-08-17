"""ApplyAgent: wendet Fixes wirklich an (Welle 2 des Auto-Fix-Loops).

Position in der Pipeline: NACH ContentAgent (der die Fixes generiert).

Nur aktiv wenn:
- project.auto_fix_enabled == True (DB-Feld) ODER force_apply Flag im Aufruf
- adapter_type wird vom adapters.get_adapter() Factory unterstuetzt

Whitelist:
Nur Fixes mit type in DEFAULT_WHITELIST UND severity in ['high', 'medium'] werden
auto-applied. Erweiterbar via project.auto_fix_config['whitelist_extra'].
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import Agent, AgentResult, AgentStatus
from ..adapters import get_adapter, ApplyResult
from ..core.event_bus import EventType

if TYPE_CHECKING:
    from ..core.audit_context import AuditContext

logger = logging.getLogger(__name__)


DEFAULT_WHITELIST = {
    "missing_title",
    "short_title",
    "long_title",
    "missing_meta_description",
    "short_meta_description",
    "long_meta_description",
    "missing_canonical",
    "canonical_missing",
    "missing_og_image",
    "missing_organization_schema",
    "missing_robots_txt",
    "missing_sitemap_xml",
    # Welle 2.5: erweitertes Set
    "org_schema_no_sameas",
}


class ApplyAgent(Agent):
    @property
    def name(self) -> str:
        return "apply"

    @property
    def event_type(self) -> EventType:
        return EventType.APPLY_COMPLETED

    async def run(self) -> AgentResult:
        result = AgentResult(
            status=AgentStatus.RUNNING,
            agent_name=self.name,
            project_id=self.project_id,
            audit_id=self.audit_id,
        )

        ctx: Optional["AuditContext"] = self.context
        force = bool(getattr(ctx, "force_apply", False)) if ctx else False
        proj_enabled = bool(getattr(self.project_config, "auto_fix_enabled", False))
        if not (force or proj_enabled):
            result.status = AgentStatus.SKIPPED
            result.log_output = "auto_fix_enabled=False and no force flag — skipping"
            return result

        # Sammle alle Fixes aus dem ContentAgent-Result
        content_result = ctx.agent_results.get("content") if ctx else None
        fixes: List[Dict[str, Any]] = list(getattr(content_result, "fixes", []) or [])

        # Whitelist + Severity-Filter
        cfg_extra = (self.project_config.auto_fix_config or {}).get(
            "whitelist_extra", []
        )
        whitelist = DEFAULT_WHITELIST | set(cfg_extra)

        eligible = []
        for f in fixes:
            ftype = f.get("type", "")
            severity = (f.get("priority") or f.get("severity") or "low").lower()
            if ftype not in whitelist:
                continue
            if severity == "low":
                continue
            eligible.append(f)

        if not eligible:
            result.status = AgentStatus.COMPLETED
            result.log_output = f"0 of {len(fixes)} fixes match whitelist+severity filter — nothing to apply"
            return result

        # Adapter holen
        adapter_type = self.project_config.adapter_type or "static"
        try:
            adapter = get_adapter(
                adapter_type, self.project_config.adapter_config or {}
            )
        except Exception as e:
            result.status = AgentStatus.FAILED
            result.errors.append(f"adapter init failed: {e}")
            result.log_output = str(e)
            return result

        # Apply
        applied: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for f in eligible:
            if not adapter.can_apply(f):
                failed.append({**f, "fix_error": "adapter cannot apply this type"})
                continue
            ar: ApplyResult = adapter.apply_fix(f, audit_id=self.audit_id)
            entry = {
                **f,
                "applied_at": datetime.utcnow().isoformat() + "Z",
                "applied_by": (
                    "claude_auto" if f.get("source") == "claude" else "template_auto"
                ),
                "git_commit_hash": ar.commit_hash,
                "fix_diff": ar.diff,
                "fix_error": ar.error,
                "files_changed": ar.files_changed,
                "success": ar.success,
            }
            (applied if ar.success else failed).append(entry)
            self._ins_aenderungsbuch(f, ar)

        # In den Context schreiben fuer Persistence + Telegram
        if ctx is not None:
            existing = list(getattr(ctx, "applied_fixes", []) or [])
            ctx.applied_fixes = existing + applied + failed

        result.metrics = {
            "fixes_eligible": len(eligible),
            "fixes_applied": len(applied),
            "fixes_failed": len(failed),
            "adapter_type": adapter_type,
        }
        result.fixes = applied + failed
        result.status = AgentStatus.COMPLETED
        result.log_output = (
            f"Auto-Fix: {len(applied)} applied, {len(failed)} failed, "
            f"{len(fixes) - len(eligible)} skipped (not in whitelist)"
        )
        logger.info(result.log_output)
        return result

    # ------------------------------------------------------------------
    # Änderungsbuch
    # ------------------------------------------------------------------

    def _ins_aenderungsbuch(self, fix: Dict[str, Any], ar: "ApplyResult") -> None:
        """Protokolliert einen angewendeten Fix im Änderungsbuch.

        Hier — und nur hier — verändert der Autopilot tatsächlich eine Website.
        Ohne Eintrag an dieser Stelle ist später nicht mehr belegbar, dass eine
        Wirkung von uns kam; genau diese Lücke schliesst das Buch.

        Zwei bewusste Regeln:

        * Ein Fix ohne geänderte Datei ist keine Änderung (der Adapter meldet
          "already-applied", wenn der Wert bereits so dasteht). Solche Läufe
          würden das Buch jeden Tag mit Nicht-Ereignissen fluten.
        * Protokollieren darf den Fix nie kosten: alles in try/except, Fehler
          nur als Warnung.
        """
        try:
            from ..changelog_book import (
                STATUS_ANGEWENDET,
                STATUS_FEHLGESCHLAGEN,
                URHEBER_AUTOPILOT,
                aktion_fuer,
                notiere_aenderung,
                standard_db_pfad,
                vorher_nachher_aus_diff,
            )

            if ar.success and not ar.files_changed:
                return  # nichts geändert -> nichts zu buchen

            vorher, nachher = vorher_nachher_aus_diff(ar.diff)
            if not nachher:
                nachher = str(fix.get("suggestion") or fix.get("url") or "")

            begruendung = str(
                fix.get("issue_title") or fix.get("type") or "Auto-Fix"
            ).strip()
            quelle = fix.get("source")
            if quelle:
                begruendung = f"{begruendung} (Quelle: {quelle})"
            if not ar.success and ar.error:
                begruendung = f"{begruendung} — fehlgeschlagen: {ar.error}"

            commit = ar.commit_hash
            if commit in ("no-git", "already-applied"):
                commit = None

            notiere_aenderung(
                standard_db_pfad(),
                self.project_id,
                aktion_fuer(fix.get("type")),
                audit_id=self.audit_id,
                urheber=URHEBER_AUTOPILOT,
                ziel_url=fix.get("url"),
                datei_pfad=", ".join(ar.files_changed) or None,
                vorher=vorher,
                nachher=nachher,
                begruendung=begruendung,
                issue_type=fix.get("type"),
                git_commit=commit,
                # Rückgängig machbar ist nur, was einen Commit hinterlassen hat.
                rueckgaengig_moeglich=bool(commit),
                status=STATUS_ANGEWENDET if ar.success else STATUS_FEHLGESCHLAGEN,
            )
        except Exception as exc:  # pragma: no cover - defensiv
            logger.warning(
                f"[apply] Änderung nicht ins Buch geschrieben (non-fatal): {exc}"
            )
