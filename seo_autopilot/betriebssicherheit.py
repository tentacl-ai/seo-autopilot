"""Small, shared checks for honest job and source completion states."""

from __future__ import annotations

from typing import Any, Mapping


def agenten_fehler(results: Mapping[str, Any]) -> list[str]:
    return [
        name
        for name, result in results.items()
        if getattr(result, "errors", None)
        or getattr(
            getattr(result, "status", None), "value", getattr(result, "status", None)
        )
        == "failed"
        or (getattr(result, "metrics", {}) or {}).get("had_error")
    ]


def ki_abdeckung(ergebnis: dict) -> dict:
    """Count only successfully answered questions; supports older report files."""
    providers = set(ergebnis.get("genannt_je_ki", {}))
    for answers in ergebnis.get("ergebnisse", {}).values():
        providers.update(answers)
    count = {
        name: {"erfolgreich": 0, "fehler": 0, "genannt": 0}
        for name in sorted(providers)
    }
    for answers in ergebnis.get("ergebnisse", {}).values():
        for name in providers:
            value = answers.get(name, {}).get("genannt")
            if value in {"nein", "verlinkt", "erwaehnt"}:
                count[name]["erfolgreich"] += 1
                count[name]["genannt"] += int(value != "nein")
            else:
                count[name]["fehler"] += 1
    return count


def cron_tage(expression, start, stop):
    """Days covered by a numeric five-field Vixie cron (no optional scheduler)."""
    from datetime import timedelta

    try:
        fields = expression.split()
        if len(fields) != 5:
            return None
        values = []
        for field, low, high in zip(fields, [0, 0, 1, 1, 0], [59, 23, 31, 12, 7]):
            selected = set()
            for part in field.split(","):
                base, *steps = part.split("/")
                step = int(steps[0]) if steps else 1
                if step <= 0 or len(steps) > 1:
                    return None
                if base == "*":
                    first, last = low, high
                elif "-" in base:
                    first, last = map(int, base.split("-"))
                else:
                    first = int(base)
                    last = high if steps else first
                if not low <= first <= last <= high:
                    return None
                selected.update(range(first, last + 1, step))
            values.append(selected)
    except (ValueError, TypeError, AttributeError):
        return None
    days = set()
    day = start
    while day < stop:
        dom = day.day in values[2]
        dow = ((day.weekday() + 1) % 7) in {n % 7 for n in values[4]}
        restricted = fields[2] != "*" and fields[4] != "*"
        if day.month in values[3] and ((dom or dow) if restricted else (dom and dow)):
            days.add(day.date().isoformat())
        day += timedelta(days=1)
    return days


def bild_delta(previous, current):
    """Compare observed occurrences, not image content or claimed visual meaning."""
    if previous is None:
        return {"grundstand": True, "neu": [], "geaendert": []}
    before = {(item.get("page"), item.get("src")): item for item in previous}
    added, changed = [], []
    for item in current:
        key = (item.get("page"), item.get("src"))
        if key not in before:
            added.append(item)
        elif any(
            item.get(field) != before[key].get(field)
            for field in ["alt", "width", "height", "decorative", "caption"]
        ):
            changed.append(item)
    return {
        "grundstand": False,
        "neu": added[:100],
        "geaendert": changed[:100],
        "gekuerzt": len(added) > 100 or len(changed) > 100,
    }


def cron_fuer_pruefung(text):
    """Expose the original command of managed cron jobs to legacy checks."""
    import re
    from .betriebsjob import JOBS

    pattern = re.compile(
        r"-m seo_autopilot\.betriebsjob ([a-z-]+)(?: --projekt ([A-Za-z0-9_-]+))?"
    )

    def replace(match):
        job, project = match.groups()
        if job == "audit" and project:
            parts = ["cli.main", "run", "--project-id", project]
        elif job in JOBS and not project:
            parts = JOBS[job]
        else:
            return match.group(0)
        return "-m seo_autopilot." + " ".join(parts)

    return "\n".join(
        line if line.lstrip().startswith("#") else pattern.sub(replace, line)
        for line in text.splitlines()
    )
