"""
AuditContext: shared state passed between agents within a single audit run.

Each agent reads from the context (e.g. StrategyAgent reads all previous
issues) and writes its own result. After the full pipeline the context
is serialized into the database and used for reports + notifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .project_manager import ProjectConfig


@dataclass
class AuditContext:
    audit_id: str
    project_id: str
    project_config: ProjectConfig
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Per-agent results keyed by agent name ("analyzer", "keyword", ...)
    agent_results: Dict[str, Any] = field(default_factory=dict)

    # Accumulated issues with agent attribution
    all_issues: List[Dict[str, Any]] = field(default_factory=list)
    all_fixes: List[Dict[str, Any]] = field(default_factory=list)

    # Overall metrics
    score: Optional[float] = None
    status: str = "running"  # running | completed | failed
    error: Optional[str] = None

    # Auto-Fix-Loop (Welle 2)
    applied_fixes: List[Dict[str, Any]] = field(default_factory=list)
    force_apply: bool = False  # CLI --auto-fix oder API force=True

    # Intelligence-Feed (Welle 3) - TrendBundle
    intel_bundle: Optional[Any] = None

    def add_result(self, agent_name: str, result: Any) -> None:
        """
        Record the result of one agent.

        - "strategy" does not produce new issues, it re-prioritizes existing
          ones; its result.issues replaces all_issues in-place.
        - Every other agent appends its issues/fixes to the shared lists.
        """
        self.agent_results[agent_name] = result

        if agent_name == "strategy":
            ranked = getattr(result, "issues", []) or []
            if ranked:
                self.all_issues = [dict(i) for i in ranked]
        else:
            for issue in getattr(result, "issues", []) or []:
                annotated = dict(issue)
                annotated.setdefault("source_agent", agent_name)
                self.all_issues.append(annotated)

        for fix in getattr(result, "fixes", []) or []:
            annotated = dict(fix)
            annotated.setdefault("source_agent", agent_name)
            self.all_fixes.append(annotated)

    def issues_by_severity(self) -> Dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for issue in self.all_issues:
            sev = (issue.get("severity") or "low").lower()
            counts[sev] = counts.get(sev, 0) + 1
        return counts

    def issues_by_category(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for issue in self.all_issues:
            cat = issue.get("category") or "other"
            out[cat] = out.get(cat, 0) + 1
        return out

    def crawled_pages(self) -> Optional[int]:
        """Wie viele Seiten hat der Analyzer tatsächlich untersucht?

        Gibt `None` zurück, wenn die Zahl nicht ermittelbar ist. Dann wird
        NICHT normiert — eine unbekannte Seitenzahl darf die Bewertung nicht
        verzerren (ein Fallback auf 1 hätte die Abzüge verfünfzehnfacht).
        """
        analyzer = self.agent_results.get("analyzer")
        metrics = getattr(analyzer, "metrics", None) or {}
        for schluessel in ("pages_crawled", "total_pages", "pages_analyzed", "pages"):
            wert = metrics.get(schluessel)
            if isinstance(wert, (int, float)) and wert > 0:
                return int(wert)
        return None

    # Bezugsgröße der Normierung: eine Website dieser Größe gilt als "typisch".
    # Bei genau so vielen Seiten verhält sich der Score exakt wie früher.
    REFERENZ_SEITEN = 15

    def calculate_score(self) -> float:
        """Gewichteter Score mit gedeckelten Abzügen, normiert auf die Seitenzahl.

        Früher waren die Abzüge absolut: Wer 40 Seiten prüfen ließ, sammelte
        zwangsläufig mehr Befunde als bei 15 Seiten und bekam eine schlechtere
        Note — obwohl die Prüfung gründlicher und die Website unverändert war.
        Genau das ist am 2026-08-17 passiert, als die Crawl-Limits an die echte
        Seitenzahl angepasst wurden (tentacl.ai 8,9 -> 3,2 ohne jede Änderung
        an der Website, lovebianca 45,7 -> 14,0).

        Jetzt zählt die Befunddichte: Befunde pro Seite, hochgerechnet auf eine
        Referenzgröße von 15 Seiten. Damit sind Läufe über die Zeit und über
        verschieden große Websites hinweg vergleichbar. Bei genau 15 geprüften
        Seiten ist das Ergebnis identisch mit der bisherigen Berechnung.

        Seitenunabhängige Befunde (robots.txt, Sitemap, Domain-weite
        Vertrauenssignale) werden dadurch leicht abgeschwächt — das ist
        gewollt: Ein einzelner Domain-Befund darf eine 40-Seiten-Website nicht
        genauso hart treffen wie eine mit 4 Seiten.
        """
        sev = self.issues_by_severity()
        seiten = self.crawled_pages()
        # Ohne bekannte Seitenzahl bleibt es bei der ursprünglichen Rechnung.
        faktor = self.REFERENZ_SEITEN / seiten if seiten else 1.0

        high_pen = min(50.0, 3.0 * sev["high"] * faktor)
        med_pen = min(30.0, 1.0 * sev["medium"] * faktor)
        low_pen = min(20.0, 0.3 * sev["low"] * faktor)
        self.score = max(0.0, round(100.0 - high_pen - med_pen - low_pen, 1))
        return self.score

    def summary(self) -> Dict[str, Any]:
        """Concise summary usable for notifications + API responses."""
        return {
            "audit_id": self.audit_id,
            "project_id": self.project_id,
            "project_name": self.project_config.name,
            "domain": self.project_config.domain,
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "duration_seconds": (
                (self.completed_at - self.started_at).total_seconds()
                if self.completed_at
                else None
            ),
            "status": self.status,
            "score": self.score,
            "issues_total": len(self.all_issues),
            "issues_by_severity": self.issues_by_severity(),
            "issues_by_category": self.issues_by_category(),
            "fixes_total": len(self.all_fixes),
            "agents": {
                name: {
                    "status": (
                        getattr(res, "status", None).value
                        if getattr(res, "status", None)
                        else None
                    ),
                    "duration_seconds": getattr(res, "duration_seconds", None),
                    "issues": len(getattr(res, "issues", []) or []),
                    "log": getattr(res, "log_output", ""),
                }
                for name, res in self.agent_results.items()
            },
        }
