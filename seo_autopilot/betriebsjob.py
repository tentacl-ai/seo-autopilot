"""Run existing scheduled commands under a shared lock and record their outcome.

No scheduler is installed and no job retries external effects. Cron supplies the
existing environment. All managed jobs sharing the DB use the same lock.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = {
    "selfcheck": ["cli.main", "selfcheck", "--no-notify"],
    "wirkung": ["cli.main", "wirkung", "--messen"],
    "chancen": ["cli.main", "chancen", "--anzahl", "10"],
    "historie": ["cli.main", "historie", "--importieren"],
    "marktradar": ["cli.main", "marktradar", "--sammeln", "--tage", "1"],
    "kundenbericht": ["cli.main", "kundenbericht", "--senden"],
    "empfehlungen": ["cli.main", "empfehlungen", "--erzeugen", "--umsetzen"],
    "rangliste": ["rangliste", "--nachholen", "12"],
    "maps": ["maps"],
    "backlinks": ["backlinks"],
    "lernschleife": ["cli.main", "lernschleife", "--senden"],
    "pulse": ["pulse_export"],
    "gsc": ["gsc_ereignisse"],
    "gsc-mails": ["gsc_ereignisse", "--nur-mails"],
    "ki-radar": ["ki_sichtbarkeit"],
    "fachquellen": ["fachquellen"],
}


def jetzt():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomar(path: Path, data: dict):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".job-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ausfuehren(command, name, state_dir: Path, timeout=1800, wait=1800):
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_dir / (name + ".json")
    prior = {}
    try:
        prior = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    with (state_dir / "datenbank.lock").open("a") as lock:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    # This job did not start. Do not overwrite a running same job.
                    atomar(
                        state_dir / (name + ".wartet.json"),
                        {
                            "job": name,
                            "status": "nicht_gestartet",
                            "am": jetzt(),
                            "grund": "Gemeinsame Jobsperre nicht rechtzeitig frei",
                        },
                    )
                    return 75
                time.sleep(0.1)
        waiting = state_dir / (name + ".wartet.json")
        if waiting.exists():
            waiting.unlink()
        data = {
            "job": name,
            "start": jetzt(),
            "status": "running",
            "last_success": prior.get("last_success"),
        }
        atomar(path, data)
        try:
            process = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
        except OSError:
            data.update(status="startfehler", ende=jetzt(), exit_code=127)
            atomar(path, data)
            return 127

        def stop_child():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()

        def interrupted(signum, frame):
            raise InterruptedError("SEO-Job wurde unterbrochen")

        previous_handlers = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[sig] = signal.signal(sig, interrupted)
        try:
            try:
                code = process.wait(timeout=timeout)
                status = "ok" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                stop_child()
                code, status = 124, "timeout"
            except BaseException:
                stop_child()
                data.update(status="interrupted", ende=jetzt())
                atomar(path, data)
                raise
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
        data.update(ende=jetzt(), status=status, exit_code=code)
        if code == 0:
            data["last_success"] = data["ende"]
        atomar(path, data)
        # Unique bounded metadata only, no customer content or credentials.
        with (state_dir / (name + ".jsonl")).open("a") as history:
            history.write(json.dumps(data, ensure_ascii=False) + "\n")
        history_path = state_dir / (name + ".jsonl")
        lines = history_path.read_text().splitlines()
        if len(lines) > 180:
            history_path.write_text("\n".join(lines[-180:]) + "\n")
        return code


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("job", choices=["audit", *JOBS])
    parser.add_argument("--projekt")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "logs" / "betrieb")
    args = parser.parse_args(argv)
    name = args.job
    if name == "audit":
        from .core.project_manager import ProjectManager
        from .core.config import settings

        project = ProjectManager(settings.PROJECT_CONFIG_PATH).get_project(args.projekt)
        if not project or not project.enabled:
            parser.error("Aktives Projekt erforderlich")
        name += "-" + project.id
        parts = ["cli.main", "run", "--project-id", project.id]
    else:
        if args.projekt:
            parser.error("Projekt nur für audit zulässig")
        parts = JOBS[name]
    if any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for c in name
    ):
        parser.error("Ungültige Jobkennung")
    return ausfuehren(
        [sys.executable, "-m", "seo_autopilot." + parts[0], *parts[1:]],
        name,
        args.state_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
