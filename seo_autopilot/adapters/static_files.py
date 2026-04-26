"""StaticFilesAdapter: wendet SEO-Fixes auf statische HTML-Dateien an.

Konfig (project.adapter_config):
    {
      "root_path": "/opt/apps/skinmatch/frontend",  # Vite/React/etc Root
      "html_files": ["index.html"],                 # zu patchende HTMLs (default index.html)
      "robots_path": "public/robots.txt",
      "sitemap_path": "public/sitemap.xml",
      "git_branch_prefix": "seo-autofix",           # default
      "push_to_remote": false,                       # default false (sicher)
      "post_apply_command": "npm run build"         # optional, nach Fix laufen
    }

Der Adapter ist defensiv: kein Git-Repo? -> macht trotzdem die Edits, gibt fake commit-hash.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    """Ergebnis pro angewendetem Fix."""

    issue_id: Optional[str] = None
    success: bool = False
    commit_hash: Optional[str] = None
    diff: str = ""
    error: Optional[str] = None
    files_changed: List[str] = field(default_factory=list)


class StaticFilesAdapter:
    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.root = Path(self.config.get("root_path", "")).resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"root_path does not exist: {self.root}")
        self.html_files = self.config.get("html_files") or ["index.html"]
        self.robots_path = self.config.get("robots_path", "public/robots.txt")
        self.sitemap_path = self.config.get("sitemap_path", "public/sitemap.xml")
        self.branch_prefix = self.config.get("git_branch_prefix", "seo-autofix")
        self.push_to_remote = bool(self.config.get("push_to_remote", False))
        self.post_apply_command = self.config.get("post_apply_command", "")
        self._has_git = (self.root / ".git").exists()

    # ------------------------------- Helpers -----------------------------------

    _git_configured: bool = False

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        if self._has_git and not self._git_configured:
            # Make this directory safe even if owned by another UID (Docker mount case)
            subprocess.run(
                [
                    "git",
                    "config",
                    "--global",
                    "--add",
                    "safe.directory",
                    str(self.root),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            # Local git identity (only inside this repo, doesn't touch global)
            for k, v in [
                ("user.email", "seo-autopilot@tentacl.ai"),
                ("user.name", "seo-autopilot"),
            ]:
                subprocess.run(
                    ["git", "-C", str(self.root), "config", k, v],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            self._git_configured = True
        cmd = ["git", "-C", str(self.root), *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def _read(self, rel_path: str) -> str:
        path = self.root / rel_path
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _write(self, rel_path: str, content: str) -> None:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _index_html_files(self) -> List[Path]:
        """Findet die zu patchenden index.html-Dateien (root + dist/ + public/)."""
        candidates = []
        for name in self.html_files:
            for sub in ("", "dist", "public"):
                p = self.root / sub / name if sub else self.root / name
                if p.exists():
                    candidates.append(p)
        return candidates

    def _patch_html_head(
        self, content: str, snippet: str, replace_pattern: Optional[str] = None
    ) -> str:
        """Fuegt snippet vor </head> ein. Wenn replace_pattern matcht, wird das Match ersetzt."""
        if replace_pattern:
            r = re.compile(replace_pattern, re.IGNORECASE | re.DOTALL)
            if r.search(content):
                return r.sub(snippet, content, count=1)
        return re.sub(
            r"</head>", f"  {snippet}\n  </head>", content, count=1, flags=re.IGNORECASE
        )

    # ------------------------------- Apply Methods -----------------------------

    def apply_meta_description(self, suggestion: str) -> List[str]:
        snippet = f'<meta name="description" content="{escape(suggestion[:160])}" />'
        pattern = r'<meta\s+name="description"[^>]*/?>'
        return self._patch_all_html(snippet, pattern)

    def apply_meta_title(self, suggestion: str) -> List[str]:
        title_safe = escape(suggestion[:65])
        snippet = f"<title>{title_safe}</title>"
        return self._patch_all_html(snippet, r"<title>.*?</title>")

    def apply_canonical(self, url: str) -> List[str]:
        snippet = f'<link rel="canonical" href="{escape(url)}" />'
        return self._patch_all_html(snippet, r'<link\s+rel="canonical"[^>]*/?>')

    def apply_og_image(self, og_url: str) -> List[str]:
        snippet = f'<meta property="og:image" content="{escape(og_url)}" />'
        return self._patch_all_html(snippet, r'<meta\s+property="og:image"[^>]*/?>')

    def apply_schema_block(self, json_ld: Any) -> List[str]:
        """json_ld kann dict (echtes JSON-LD) oder str (bereits JSON-serialisiert) sein."""
        import json as _json

        if isinstance(json_ld, dict):
            body = _json.dumps(json_ld, ensure_ascii=False, indent=2)
            schema_type = json_ld.get("@type", "")
        elif isinstance(json_ld, str):
            body = json_ld.strip()
            try:
                schema_type = _json.loads(body).get("@type", "")
            except Exception:
                schema_type = ""
        else:
            return []
        snippet = f'<script type="application/ld+json">\n{body}\n</script>'
        files = []
        for path in self._index_html_files():
            content = path.read_text(encoding="utf-8")
            # Skip wenn bereits ein Block mit gleichem @type existiert
            if schema_type and f'"@type": "{schema_type}"' in content:
                continue
            if schema_type and f'"@type":"{schema_type}"' in content:
                continue
            new = self._patch_html_head(content, snippet)
            if new != content:
                path.write_text(new, encoding="utf-8")
                files.append(str(path.relative_to(self.root)))
        return files

    def apply_robots_txt(self, content: str) -> List[str]:
        existing = self._read(self.robots_path)
        if existing.strip() == content.strip():
            return []
        self._write(
            self.robots_path, content if content.endswith("\n") else content + "\n"
        )
        return [self.robots_path]

    def apply_sitemap_xml(self, content: str) -> List[str]:
        if self._read(self.sitemap_path).strip() == content.strip():
            return []
        self._write(
            self.sitemap_path, content if content.endswith("\n") else content + "\n"
        )
        return [self.sitemap_path]

    def _patch_all_html(
        self, snippet: str, replace_pattern: Optional[str]
    ) -> List[str]:
        files = []
        for path in self._index_html_files():
            content = path.read_text(encoding="utf-8")
            new = self._patch_html_head(content, snippet, replace_pattern)
            if new != content:
                path.write_text(new, encoding="utf-8")
                files.append(str(path.relative_to(self.root)))
        return files

    # ------------------------------- Apply One Fix -----------------------------

    SUPPORTED_TYPES = {
        "missing_title": "apply_meta_title",
        "short_title": "apply_meta_title",
        "long_title": "apply_meta_title",
        "missing_meta_description": "apply_meta_description",
        "short_meta_description": "apply_meta_description",
        "long_meta_description": "apply_meta_description",
        "missing_canonical": "apply_canonical",
        "canonical_missing": "apply_canonical",
        "missing_og_image": "apply_og_image",
        "missing_organization_schema": "apply_schema_block",
        "missing_robots_txt": "apply_robots_txt",
        "missing_sitemap_xml": "apply_sitemap_xml",
    }

    def can_apply(self, fix: Dict[str, Any]) -> bool:
        return fix.get("type") in self.SUPPORTED_TYPES

    def apply_fix(self, fix: Dict[str, Any], audit_id: str = "manual") -> ApplyResult:
        """Wendet einen einzelnen Fix an. Macht Git-Commit wenn Repo vorhanden."""
        result = ApplyResult(issue_id=fix.get("issue_id"))
        ftype = fix.get("type", "")
        method_name = self.SUPPORTED_TYPES.get(ftype)
        if not method_name:
            result.error = f"Adapter cannot apply type: {ftype}"
            return result

        try:
            files = self._dispatch(method_name, fix)
            if not files:
                # Bereits angewendet / nichts zu aendern - kein Fehler
                result.success = True
                result.commit_hash = "already-applied"
                result.diff = "(no changes — fix already in place)"
                return result
            result.files_changed = files

            # Git stage + commit
            if self._has_git:
                self._git("add", *files)
                msg = self._commit_message(fix, audit_id)
                self._git("commit", "-m", msg, "--no-verify")
                head = self._git("rev-parse", "HEAD").stdout.strip()
                result.commit_hash = head[:12]
                # Diff-Snapshot
                diff = self._git("show", "--unified=2", head, check=False)
                result.diff = (diff.stdout or "")[:4000]
                # Optional push
                if self.push_to_remote:
                    self._git("push", "origin", "HEAD", check=False)
            else:
                result.commit_hash = "no-git"
                result.diff = "(no git repo at root)"

            # Optional Post-Apply (Build / Restart)
            if self.post_apply_command:
                subprocess.run(
                    shlex.split(self.post_apply_command),
                    cwd=str(self.root),
                    capture_output=True,
                    timeout=300,
                    check=False,
                )

            result.success = True
            return result
        except subprocess.CalledProcessError as e:
            result.error = f"git error: {e.stderr or e.stdout}"
            return result
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
            return result

    def _dispatch(self, method_name: str, fix: Dict[str, Any]) -> List[str]:
        method = getattr(self, method_name)
        suggestion = fix.get("suggestion") or ""
        # Strukturierte Fixes (organization schema) liefern dict statt string
        if method_name == "apply_schema_block":
            return method(fix.get("snippet") or fix.get("schema") or suggestion)
        if method_name == "apply_canonical":
            return method(fix.get("url") or suggestion)
        if method_name == "apply_og_image":
            return method(fix.get("url") or suggestion)
        return method(suggestion)

    def _commit_message(self, fix: Dict[str, Any], audit_id: str) -> str:
        title = (fix.get("issue_title") or fix.get("type") or "fix").strip()
        return f"seo-autofix: {title}\n\nType: {fix.get('type')}\nAudit: {audit_id}\nSource: {fix.get('source','?')}\n"
