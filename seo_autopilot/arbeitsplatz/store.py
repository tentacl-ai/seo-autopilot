"""Versioned project documents and conservative, concurrent spending reservations."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class WorkspaceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", value
    ):
        raise WorkspaceError("Ungültige Kennung.")
    return value


def encoded(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(data.encode()) > 250_000:
        raise WorkspaceError("Inhalt zu groß.")
    return data


def fingerprint(value) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Store:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def initialize(self):
        """Explicit setup, never called by a read request."""
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS workspace_documents (
                  project TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
                  revision INTEGER NOT NULL, payload TEXT NOT NULL,
                  author TEXT NOT NULL, created_at TEXT NOT NULL,
                  PRIMARY KEY(project,kind,key,revision));
                CREATE TABLE IF NOT EXISTS workspace_calls (
                  project TEXT NOT NULL, request_id TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, month TEXT NOT NULL,
                  reserved INTEGER NOT NULL, status TEXT NOT NULL,
                  payload TEXT, created_at TEXT NOT NULL,
                  PRIMARY KEY(project,request_id));
                """)

    @staticmethod
    def document(row):
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def get(self, project: str, kind: str, key: str, revision: int | None = None):
        with self.connect() as con:
            query = (
                "SELECT * FROM workspace_documents WHERE project=? AND kind=? AND key=?"
            )
            args = [project, kind, key]
            if revision is not None:
                query += " AND revision=?"
                args.append(revision)
            row = con.execute(
                query + " ORDER BY revision DESC LIMIT 1", args
            ).fetchone()
            return self.document(row)

    def list(self, project: str, kind: str):
        with self.connect() as con:
            rows = con.execute(
                """SELECT d.* FROM workspace_documents d JOIN
                (SELECT key,MAX(revision) revision FROM workspace_documents
                 WHERE project=? AND kind=? GROUP BY key) latest
                ON d.key=latest.key AND d.revision=latest.revision
                WHERE d.project=? AND d.kind=? ORDER BY d.created_at DESC LIMIT 1000""",
                (project, kind, project, kind),
            ).fetchall()
            return [self.document(row) for row in rows]

    def put(self, project, kind, key, payload, author, expected_revision):
        identifier(key)
        data = encoded(payload)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = (
                con.execute(
                    "SELECT MAX(revision) FROM workspace_documents WHERE project=? AND kind=? AND key=?",
                    (project, kind, key),
                ).fetchone()[0]
                or 0
            )
            if current != expected_revision:
                raise WorkspaceError("Zwischenzeitlich geändert. Bitte neu laden.", 409)
            con.execute(
                "INSERT INTO workspace_documents VALUES (?,?,?,?,?,?,?)",
                (project, kind, key, current + 1, data, author, now()),
            )
        return self.get(project, kind, key)

    def cached(self, project, digest, max_age_seconds=86400):
        with self.connect() as con:
            row = con.execute(
                """SELECT * FROM workspace_calls WHERE project=? AND fingerprint=?
                AND status='completed' ORDER BY created_at DESC LIMIT 1""",
                (project, digest),
            ).fetchone()
        if (
            row
            and (
                datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])
            ).total_seconds()
            <= max_age_seconds
        ):
            return json.loads(row["payload"])
        return None

    def reserve(self, project, request_id, digest, ceiling, monthly_limit):
        """Reservations remain charged on failure/timeout; no automatic retry."""
        identifier(request_id)
        if ceiling <= 0 or monthly_limit <= 0:
            raise WorkspaceError("Bezahlte Daten sind nicht freigegeben.", 403)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT fingerprint,status FROM workspace_calls WHERE project=? AND request_id=?",
                (project, request_id),
            ).fetchone()
            if existing:
                raise WorkspaceError(
                    "Auftrag bereits erfasst; keine erneute Abrechnung.", 409
                )
            active = con.execute(
                "SELECT 1 FROM workspace_calls WHERE project=? AND fingerprint=? AND status IN ('pending','queued') LIMIT 1",
                (project, digest),
            ).fetchone()
            if active:
                raise WorkspaceError("Gleiche Recherche läuft bereits.", 409)
            month = now()[:7]
            used = con.execute(
                "SELECT COALESCE(SUM(reserved),0) FROM workspace_calls WHERE project=? AND month=?",
                (project, month),
            ).fetchone()[0]
            if used + ceiling > monthly_limit:
                raise WorkspaceError("Monatsbudget ausgeschöpft.", 409)
            con.execute(
                "INSERT INTO workspace_calls VALUES (?,?,?,?,?,'pending',NULL,?)",
                (project, request_id, digest, month, ceiling, now()),
            )

    def finish(self, project, request_id, payload, status="completed"):
        with self.connect() as con:
            con.execute(
                "UPDATE workspace_calls SET payload=?,status=? WHERE project=? AND request_id=? AND status='pending'",
                (encoded(payload), status, project, request_id),
            )

    def spending(self, project):
        with self.connect() as con:
            rows = con.execute(
                "SELECT month,SUM(reserved) reserved,COUNT(*) calls FROM workspace_calls WHERE project=? GROUP BY month ORDER BY month DESC LIMIT 12",
                (project,),
            ).fetchall()
            return [dict(row) for row in rows]

    def research_log(self, project):
        with self.connect() as con:
            rows = con.execute(
                "SELECT request_id,status,payload,created_at,reserved FROM workspace_calls WHERE project=? ORDER BY created_at DESC LIMIT 100",
                (project,),
            ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload"]) if row["payload"] else None,
            }
            for row in rows
        ]

    def queued_result(self, project, request_id, payload):
        with self.connect() as con:
            con.execute(
                "UPDATE workspace_calls SET payload=?,status='completed' WHERE project=? AND request_id=? AND status='queued'",
                (encoded(payload), project, request_id),
            )

    def claim_assistant(self, project, key, author):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute(
                "SELECT 1 FROM workspace_documents WHERE project=? AND kind='assistant' AND key=?",
                (project, key),
            ).fetchone():
                raise WorkspaceError("Diese Frage wird bereits bearbeitet.", 409)
            count = con.execute(
                "SELECT COUNT(*) FROM workspace_documents WHERE project=? AND kind='assistant' AND revision=1 AND created_at>=?",
                (project, now()[:10]),
            ).fetchone()[0]
            if count >= 20:
                raise WorkspaceError("Gesprächslimit für heute erreicht.", 429)
            con.execute(
                "INSERT INTO workspace_documents VALUES (?,'assistant',?,1,?,?,?)",
                (project, key, encoded({"status": "pending"}), author, now()),
            )
