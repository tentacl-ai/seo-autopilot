"""Prepare, never install, a cron migration for existing SEO jobs."""

from __future__ import annotations
import re
import shlex
from .betriebsjob import JOBS

ROOT = "/opt/odoo/docs/seo-autopilot"
PATTERN = re.compile(
    r"(?P<python>(?:/opt/odoo/docs/seo-autopilot/)?venv/bin/python3?) -m seo_autopilot\.(?P<module>[\w.]+)(?P<args>.*?)(?=\s*>>|\s*#|$)"
)


def vorbereiten(text, nacht=True, fachquellen=True):
    changed, lines, schedules = [], [], {}
    for line in text.splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            lines.append(line)
            continue
        m = PATTERN.search(line)
        if not m:
            lines.append(line)
            continue
        command = [m["module"], *shlex.split(m["args"].strip())]
        project = None
        if len(command) == 4 and command[:3] == ["cli.main", "run", "--project-id"]:
            project = command[3]
            job = "audit"
            name = job + "-" + project
        else:
            keys = [key for key, parts in JOBS.items() if parts == command]
            if len(keys) != 1:
                raise ValueError(
                    "Unbekannter SEO-Aufruf; keine automatische Cron-Übernahme"
                )
            job = keys[0]
            name = job
        replacement = m["python"] + " -m seo_autopilot.betriebsjob " + job
        if project:
            replacement += " --projekt " + shlex.quote(project)
        new = line[: m.start()] + replacement + line[m.end() :]
        if nacht and project == "tentacl-ai":
            fields = new.split(None, 5)
            if len(fields) != 6:
                raise ValueError("Ungültige Cron-Zeile")
            new = "15 4 * * * " + fields[5]
        schedule = " ".join(new.split()[:5])
        schedules.setdefault(name, []).append(schedule)
        changed.append({"job": name, "cron": schedule})
        lines.append(new)
    if not changed:
        raise ValueError("Keine kanonischen SEO-Jobs erkannt")
    if fachquellen and "fachquellen" not in schedules:
        lines.append(
            f"5 6 * * 1 cd {ROOT} && {ROOT}/venv/bin/python3 -m seo_autopilot.betriebsjob fachquellen >> {ROOT}/logs/fachquellen.log 2>&1 # SEO Originalquellen woechentlich, kein Versand"
        )
        schedules["fachquellen"] = ["5 6 * * 1"]
        changed.append({"job": "fachquellen", "cron": "5 6 * * 1"})
    return "\n".join(lines) + "\n", changed, schedules
