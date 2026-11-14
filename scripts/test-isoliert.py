"""Run tests without real credentials, CLI model calls or external connections."""

import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
for key in list(os.environ):
    if any(
        part in key
        for part in ["API_KEY", "API_SECRET", "TELEGRAM", "DATAFORSEO", "OAUTH_TOKEN"]
    ):
        os.environ.pop(key)
os.environ["CLAUDE_CLI"] = "/nonexistent/seo-test-cli"
os.environ["SEO_SECRETS_DATEI"] = "/nonexistent/seo-test-secrets"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["PROJECT_CONFIG_PATH"] = str(ROOT / "tests" / "test_projects.yaml")

original = socket.socket.connect


def local_only(sock, address):
    if isinstance(address, tuple) and address[0] not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise OSError("Externe Verbindung im Test gesperrt")
    return original(sock, address)


socket.socket.connect = local_only
from seo_autopilot import abo_ki  # noqa: E402

abo_ki.AUTOMATION_ENV = Path("/nonexistent/seo-test-automation")
import pytest  # noqa: E402

raise SystemExit(pytest.main(sys.argv[1:] or ["tests/", "-q"]))
