"""StaticFilesAdapter: wendet SEO-Fixes auf statische HTML-Dateien an.

Konfig (project.adapter_config):
    {
      "root_path": "/var/www/meine-website/frontend",  # Vite/React/etc Root
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

from ..handwerker import (
    bilder_der_seite,
    datei_fuer_seite,
    kontext_aus_html,
    lokales_bild,
)

logger = logging.getLogger(__name__)


class KeineZielDatei(Exception):
    """Zur Seite gibt es unter root_path keine Datei (z. B. anderer Webroot)."""


@dataclass
class ApplyResult:
    """Ergebnis pro angewendetem Fix."""

    issue_id: Optional[str] = None
    success: bool = False
    commit_hash: Optional[str] = None
    diff: str = ""
    error: Optional[str] = None
    files_changed: List[str] = field(default_factory=list)
    # Grund, warum der Adapter den Befund NICHT beheben kann (z. B. Bild ohne
    # lokale Datei, Seite liegt in einem anderen Webroot). Weder Erfolg noch
    # Fehler: der Befund bleibt offen, es wird nichts gebucht.
    nicht_behebbar: Optional[str] = None


def kuerzen(text: str, n: int) -> str:
    """Kuerzt an einer Wortgrenze und laesst keinen haengenden Trenner stehen
    (vorher: "Gebaeudemanagement – Monitoring, ... Protokolle | ")."""
    t = (text or "").strip()
    if len(t) <= n:
        return t
    t = t[:n]
    if " " in t:
        t = t[: t.rfind(" ")]
    return t.rstrip(" |–-—:,;·/").strip()


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
        self._nicht_behebbar: List[str] = []
        # Git-Wurzel darf oberhalb von root liegen (z. B. root = repo/frontend/dist)
        self._git_root = self._git_wurzel()
        self._has_git = self._git_root is not None

    # ------------------------------- Helpers -----------------------------------

    _git_configured: bool = False

    def _git_wurzel(self) -> Optional[Path]:
        try:
            r = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        if r.returncode != 0 or not r.stdout.strip():
            # Fremder Besitzer? Einmal als sicher markieren und nochmal fragen.
            subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", "*"],
                capture_output=True,
                text=True,
                check=False,
            )
            r = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
        return Path(r.stdout.strip())

    def _versionierbar(self, files: List[str]) -> List[str]:
        """Nur Dateien, die Git nicht ignoriert — sonst scheitert `git add`
        und die Aenderung gaelte faelschlich als fehlgeschlagen."""
        if not files:
            return []
        r = self._git("check-ignore", "--", *files, check=False)
        ignoriert = set((r.stdout or "").split())
        return [f for f in files if f not in ignoriert]

    def _pruefe_fremde_aenderungen(self, dateien: Optional[List[Path]]) -> None:
        """Nie in eine Datei schreiben, an der gerade jemand anderes arbeitet.

        tentacl.ai: `dist/` ist die Quelle selbst, von Hand und von anderen
        Sitzungen bearbeitet. Ein `git add` des Autopiloten wuerde deren halbe
        Arbeit mitcommitten (18.09.2026). Offene Aenderungen -> nicht_behebbar,
        naechster Lauf versucht es wieder.
        """
        if not self._has_git or not dateien:
            return
        for d in dateien:
            r = self._git("status", "--porcelain", "--", str(d), check=False)
            if (r.stdout or "").strip():
                raise KeineZielDatei(
                    f"offene fremde Aenderungen in {d.name} - diesmal nicht angefasst"
                )

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
                    str(self._git_root or self.root),
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

    def _ziel_dateien(self, fix: Dict[str, Any]) -> List[Path]:
        """Welche Dateien ein Fix anfassen darf.

        Trägt der Fix eine Seitenadresse (`seite`), wird GENAU deren Datei
        geändert — oder gar nichts, wenn sie nicht zu finden ist. Erst ohne
        jede Adresse gilt das alte Verhalten (konfigurierte index.html).
        Vorher landete jede Änderung in der Startseite, egal welche Seite
        der Befund meinte.
        """
        seite = fix.get("seite")
        if seite:
            datei = datei_fuer_seite(self.root, seite)
            return [datei] if datei else []
        return self._index_html_files()

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

    def apply_meta_description(
        self, suggestion: str, dateien: Optional[List[Path]] = None
    ) -> List[str]:
        snippet = f'<meta name="description" content="{escape(suggestion[:160])}" />'
        pattern = r'<meta\s+name="description"[^>]*/?>'
        return self._patch_all_html(snippet, pattern, dateien)

    def apply_meta_title(
        self, suggestion: str, dateien: Optional[List[Path]] = None
    ) -> List[str]:
        title_safe = escape(suggestion[:65])
        snippet = f"<title>{title_safe}</title>"
        return self._patch_all_html(snippet, r"<title>.*?</title>", dateien)

    def apply_canonical(
        self, url: str, dateien: Optional[List[Path]] = None
    ) -> List[str]:
        snippet = f'<link rel="canonical" href="{escape(url)}" />'
        return self._patch_all_html(
            snippet, r'<link\s+rel="canonical"[^>]*/?>', dateien
        )

    def apply_og_image(
        self, og_url: str, dateien: Optional[List[Path]] = None
    ) -> List[str]:
        snippet = f'<meta property="og:image" content="{escape(og_url)}" />'
        return self._patch_all_html(
            snippet, r'<meta\s+property="og:image"[^>]*/?>', dateien
        )

    def apply_og_title(
        self, suggestion: str, dateien: Optional[List[Path]] = None
    ) -> List[str]:
        snippet = f'<meta property="og:title" content="{escape(suggestion[:90])}" />'
        return self._patch_all_html(
            snippet, r'<meta\s+property="og:title"[^>]*/?>', dateien
        )

    def apply_twitter_card(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """twitter:card + Titel/Beschreibung/Bild aus den vorhandenen og:-Angaben.

        Es wird nur ergänzt, was fehlt — nie etwas überschrieben.
        """
        files = []
        for path in dateien if dateien is not None else self._index_html_files():
            content = path.read_text(encoding="utf-8")
            k = kontext_aus_html(content)
            zeilen = []
            if not k["twitter_card"]:
                zeilen.append(
                    '<meta name="twitter:card" content="summary_large_image" />'
                )
            if 'name="twitter:title"' not in content and (k["og_title"] or k["title"]):
                zeilen.append(
                    f'<meta name="twitter:title" content="{escape(kuerzen(k["og_title"] or k["title"], 70))}" />'
                )
            if 'name="twitter:description"' not in content and (
                k["og_description"] or k["description"]
            ):
                zeilen.append(
                    f'<meta name="twitter:description" content="{escape(kuerzen(k["og_description"] or k["description"], 200))}" />'
                )
            if 'name="twitter:image"' not in content and k["og_image"]:
                zeilen.append(
                    f'<meta name="twitter:image" content="{escape(k["og_image"])}" />'
                )
            if not zeilen:
                continue
            new = self._patch_html_head(content, "\n  ".join(zeilen))
            if new != content:
                path.write_text(new, encoding="utf-8")
                files.append(str(path.relative_to(self.root)))
        return files

    def apply_image_alt(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """Setzt Alt-Texte aus fix["bilder"] = {src: alt} an <img> ohne alt.

        Dekorative Bilder (alt="", role=presentation, aria-hidden) bleiben
        unangetastet — alt="" ist dort die richtige Auszeichnung (WCAG 1.1.1).
        """
        bilder: Dict[str, str] = fix.get("bilder") or {}
        if not bilder:
            return []
        files = []
        for path in dateien if dateien is not None else self._index_html_files():
            content = path.read_text(encoding="utf-8")
            new = content
            for b in bilder_der_seite(content):
                if b["alt"] is not None or b["dekorativ"]:
                    continue
                alt = bilder.get(b["src"])
                if not alt:
                    continue
                tag = b["tag"]
                neu = re.sub(
                    r"^<img\b",
                    f'<img alt="{escape(alt[:125])}"',
                    tag,
                    count=1,
                    flags=re.I,
                )
                new = new.replace(tag, neu, 1)
            if new != content:
                path.write_text(new, encoding="utf-8")
                files.append(str(path.relative_to(self.root)))
        return files

    def apply_image_dimensions(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """width/height in Originalpixeln an <img> ohne Maße (gegen Layout-Sprünge).

        Nur für lokale Bilddateien, deren Größe wir wirklich messen können.
        """
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover
            # Frueher still "return []" -> Befund galt als behoben (18.09.2026)
            self._nicht_behebbar.append("Pillow nicht installiert")
            return []
        files = []
        seite = fix.get("seite")
        for path in dateien if dateien is not None else self._index_html_files():
            content = path.read_text(encoding="utf-8")
            new = content
            for b in bilder_der_seite(content):
                if b["width"] or b["height"]:
                    continue
                datei = lokales_bild(self.root, seite, b["src"])
                if datei is None:
                    self._nicht_behebbar.append(f"keine lokale Datei: {b['src']}")
                    continue
                try:
                    with Image.open(datei) as im:
                        w, h = im.size
                except Exception:
                    self._nicht_behebbar.append(f"nicht lesbar: {b['src']}")
                    continue
                if not w or not h:
                    continue
                tag = b["tag"]
                neu = re.sub(
                    r"^<img\b",
                    f'<img width="{w}" height="{h}"',
                    tag,
                    count=1,
                    flags=re.I,
                )
                new = new.replace(tag, neu, 1)
            if new != content:
                path.write_text(new, encoding="utf-8")
                files.append(str(path.relative_to(self.root)))
        return files

    def apply_jsonld(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """WebPage-JSON-LD mit datePublished/dateModified für Seiten ohne Auszeichnung."""
        import json as _json
        from datetime import datetime, timezone

        files = []
        for path in dateien if dateien is not None else self._index_html_files():
            content = path.read_text(encoding="utf-8")
            if "application/ld+json" in content:
                continue
            k = kontext_aus_html(content)
            seite = fix.get("seite") or ""
            geaendert = (
                datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                .date()
                .isoformat()
            )
            # datePublished nur, wenn wirklich bekannt (Git-Historie) — ein
            # erfundenes "heute" waere fuer alte Seiten schlicht falsch.
            veroeffentlicht = fix.get("date_published") or self._erstes_commit_datum(
                path
            )
            block = {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": k["og_title"] or k["title"] or k["h1"],
                "description": k["description"] or k["og_description"],
                "url": seite,
                "inLanguage": k["lang"],
                "datePublished": veroeffentlicht,
                "dateModified": geaendert,
            }
            if fix.get("publisher"):
                block["publisher"] = {
                    "@type": "Organization",
                    "name": fix["publisher"],
                    "url": fix.get("domain", ""),
                }
            if k["og_image"]:
                block["primaryImageOfPage"] = {
                    "@type": "ImageObject",
                    "url": k["og_image"],
                }
            block = {kk: v for kk, v in block.items() if v}
            snippet = (
                '<script type="application/ld+json">\n'
                + _json.dumps(block, ensure_ascii=False, indent=2)
                + "\n</script>"
            )
            new = self._patch_html_head(content, snippet)
            if new != content:
                path.write_text(new, encoding="utf-8")
                files.append(str(path.relative_to(self.root)))
        return files

    def _erstes_commit_datum(self, path: Path) -> Optional[str]:
        if not self._has_git:
            return None
        r = self._git(
            "log",
            "--diff-filter=A",
            "--format=%cs",
            "--",
            str(path.relative_to(self.root)),
            check=False,
        )
        zeilen = [z for z in (r.stdout or "").splitlines() if z.strip()]
        return zeilen[-1].strip() if zeilen else None

    def apply_schema_block(
        self, json_ld: Any, dateien: Optional[List[Path]] = None
    ) -> List[str]:
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
        for path in dateien if dateien is not None else self._index_html_files():
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

    # ---------------- Sichtbarer Text (Empfehlungen, Stufe 3, 18.09.2026) -------
    #
    # Nur ueber empfehlungen_umsetzen.py erreichbar (nicht in der Whitelist des
    # ApplyAgent). Jede Methode ist idempotent (Marker data-seo-autopilot bzw.
    # Vergleich mit dem Zieltext) und raet nie: Ist die Einfuegestelle nicht
    # eindeutig oder der Ausgangstext nicht mehr genau so da, wird nichts
    # geschrieben und der Fix gilt als `nicht_behebbar`.

    @staticmethod
    def einfuegestelle(content: str) -> Optional[int]:
        """Index vor dem Ende des Hauptinhalts: genau ein <main> (sonst genau ein
        <article>); liegt ein <footer> darin, davor. Ohne beides: vor dem einzigen
        <footer> der Seite. Sonst None (nie raten)."""
        for tag in ("main", "article"):
            auf = [m.start() for m in re.finditer(rf"<{tag}\b", content, re.I)]
            zu = [m.start() for m in re.finditer(rf"</{tag}\s*>", content, re.I)]
            if len(auf) == 1 and len(zu) == 1 and auf[0] < zu[0]:
                fuss = re.search(r"<footer\b", content[auf[0] : zu[0]], re.I)
                return auf[0] + fuss.start() if fuss else zu[0]
            if auf or zu:
                return None  # mehrdeutig: nie raten
        fuesse = [m.start() for m in re.finditer(r"<footer\b", content, re.I)]
        return fuesse[0] if len(fuesse) == 1 else None

    @staticmethod
    def _norm_text(s: str) -> str:
        from html import unescape

        return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

    def apply_faq_block(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """Sichtbarer FAQ-Abschnitt + FAQPage-JSON-LD. Platzhalter gehen nie live."""
        import json as _json

        fragen = [
            f
            for f in fix.get("fragen") or []
            if f.get("antwort") and "[" not in f["antwort"] and "]" not in f["antwort"]
        ]
        if len(fragen) < 2:
            self._nicht_behebbar.append("weniger als 2 beantwortete Fragen")
            return []
        files = []
        for path in dateien or []:
            content = path.read_text(encoding="utf-8")
            if 'data-seo-autopilot="faq"' in content:
                continue  # schon da
            if "FAQPage" in content:
                self._nicht_behebbar.append(
                    "Seite hat bereits eine FAQ – Ergänzung von Hand"
                )
                continue
            stelle = self.einfuegestelle(content)
            if stelle is None:
                self._nicht_behebbar.append("Einfügestelle (<main>/<article>) unklar")
                continue
            block = fix.get("html") or ""
            if "[" in block or not block.strip():
                self._nicht_behebbar.append(
                    "FAQ-Baustein fehlt oder enthält Platzhalter"
                )
                continue
            new = content[:stelle] + block + "\n" + content[stelle:]
            jsonld = fix.get("jsonld")
            if jsonld:
                snippet = (
                    '<script type="application/ld+json">\n'
                    + _json.dumps(jsonld, ensure_ascii=False, indent=2)
                    + "\n</script>"
                )
                new = self._patch_html_head(new, snippet)
            path.write_text(new, encoding="utf-8")
            files.append(str(path.relative_to(self.root)))
        return files

    def apply_abschnitt(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """Neuer Abschnitt vor dem Ende des Hauptinhalts (vor einer Autopilot-FAQ)."""
        marker = fix.get("marker") or ""
        block = fix.get("html") or ""
        if not marker or not block.strip() or "[" in block:
            self._nicht_behebbar.append("Abschnitt ohne fertigen Text")
            return []
        files = []
        for path in dateien or []:
            content = path.read_text(encoding="utf-8")
            if f'data-seo-autopilot="{marker}"' in content:
                continue
            faq = content.find('<section class="seo-faq" data-seo-autopilot="faq"')
            stelle = faq if faq >= 0 else self.einfuegestelle(content)
            if stelle is None:
                self._nicht_behebbar.append("Einfügestelle (<main>/<article>) unklar")
                continue
            new = content[:stelle] + block + "\n" + content[stelle:]
            path.write_text(new, encoding="utf-8")
            files.append(str(path.relative_to(self.root)))
        return files

    def apply_ueberschrift(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """Ersetzt den Text der einzigen H1 — nur wenn sie reiner Text ist und noch
        genau dem erwarteten alten Wortlaut entspricht."""
        alt, neu = fix.get("alt") or "", fix.get("neu") or ""
        files = []
        for path in dateien or []:
            content = path.read_text(encoding="utf-8")
            treffer = list(
                re.finditer(r"(<h1\b[^>]*>)(.*?)(</h1\s*>)", content, re.I | re.S)
            )
            if len(treffer) != 1:
                self._nicht_behebbar.append(f"{len(treffer)} H1 auf der Seite")
                continue
            m = treffer[0]
            jetzt = self._norm_text(m.group(2))
            if jetzt == self._norm_text(neu):
                continue
            if "<" in m.group(2):
                self._nicht_behebbar.append("H1 enthält Auszeichnung – von Hand ändern")
                continue
            if jetzt != self._norm_text(alt):
                self._nicht_behebbar.append("H1 hat sich geändert – Vorschlag veraltet")
                continue
            new = content[: m.start(2)] + escape(neu, quote=False) + content[m.end(2) :]
            path.write_text(new, encoding="utf-8")
            files.append(str(path.relative_to(self.root)))
        return files

    def apply_erster_absatz(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """Ersetzt den ersten Absatz im Hauptinhalt, wenn er noch genau so dasteht."""
        alt, neu = self._norm_text(fix.get("alt") or ""), fix.get("neu") or ""
        files = []
        for path in dateien or []:
            content = path.read_text(encoding="utf-8")
            haupt = re.search(r"<main\b[^>]*>", content, re.I) or re.search(
                r"<article\b[^>]*>", content, re.I
            )
            start = haupt.end() if haupt else 0
            erster = None
            for m in re.finditer(
                r"(<p\b[^>]*>)(.*?)(</p\s*>)", content[start:], re.I | re.S
            ):
                if len(self._norm_text(m.group(2))) >= 40:
                    erster = m
                    break
            if erster is None:
                self._nicht_behebbar.append("kein erster Absatz gefunden")
                continue
            jetzt = self._norm_text(erster.group(2))
            if jetzt == self._norm_text(neu):
                continue
            if "<" in erster.group(2):
                self._nicht_behebbar.append(
                    "erster Absatz enthält Auszeichnung/Links – von Hand ändern"
                )
                continue
            if jetzt != alt:
                self._nicht_behebbar.append(
                    "erster Absatz hat sich geändert – Vorschlag veraltet"
                )
                continue
            a, b = start + erster.start(2), start + erster.end(2)
            new = content[:a] + escape(neu, quote=False) + content[b:]
            path.write_text(new, encoding="utf-8")
            files.append(str(path.relative_to(self.root)))
        return files

    def apply_interner_link(
        self, fix: Dict[str, Any], dateien: Optional[List[Path]] = None
    ) -> List[str]:
        """Verlinkt das erste woertliche Vorkommen des Begriffs in einem Absatz ohne
        Link (Hauptinhalt der Quellseite) auf die Zielseite."""
        from html import unescape

        ziel, muster = fix.get("ziel") or "", fix.get("muster") or ""
        if not ziel or not muster:
            self._nicht_behebbar.append("Ziel oder Linktext fehlt")
            return []
        files = []
        for path in dateien or []:
            content = path.read_text(encoding="utf-8")
            ziel_ohne = ziel.rstrip("/")
            if re.search(
                rf'href="(https?://[^/"]+)?{re.escape(ziel_ohne)}/?"', content, re.I
            ):
                continue  # verlinkt schon
            haupt = re.search(r"<main\b[^>]*>(.*?)</main\s*>", content, re.I | re.S)
            von, bis = (haupt.start(1), haupt.end(1)) if haupt else (0, len(content))
            gesetzt = False
            for p in re.finditer(
                r"<p\b[^>]*>(.*?)</p\s*>", content[von:bis], re.I | re.S
            ):
                inner = p.group(1)
                if "<a" in inner.lower():
                    continue
                for teil in re.finditer(r"[^<]+", inner):
                    t = re.search(muster, teil.group(0), re.I)
                    if not t or unescape(t.group(0)) != t.group(0):
                        continue
                    a = von + p.start(1) + teil.start() + t.start()
                    b = von + p.start(1) + teil.start() + t.end()
                    link = f'<a href="{escape(ziel)}">{content[a:b]}</a>'
                    content = content[:a] + link + content[b:]
                    gesetzt = True
                    break
                if gesetzt:
                    break
            if not gesetzt:
                self._nicht_behebbar.append(
                    "Linkwort nicht mehr wörtlich in einem Absatz"
                )
                continue
            path.write_text(content, encoding="utf-8")
            files.append(str(path.relative_to(self.root)))
        return files

    def _patch_all_html(
        self,
        snippet: str,
        replace_pattern: Optional[str],
        dateien: Optional[List[Path]] = None,
    ) -> List[str]:
        files = []
        for path in dateien if dateien is not None else self._index_html_files():
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
        # Handwerker (v1.13): je Seite, nur Meta-Ebene und Auszeichnung
        "missing_og_title": "apply_og_title",
        "missing_twitter_card": "apply_twitter_card",
        "images_without_alt": "apply_image_alt",
        "image_missing_dimensions": "apply_image_dimensions",
        "no_jsonld": "apply_jsonld",
        # Empfehlungen (v1.16): sichtbarer Text — NICHT in der ApplyAgent-Whitelist,
        # nur ueber empfehlungen_umsetzen.py (Autopilot bzw. nach Freigabe)
        "empfehlung_faq_ergaenzen": "apply_faq_block",
        "empfehlung_abschnitt_ergaenzen": "apply_abschnitt",
        "empfehlung_ueberschrift_verbessern": "apply_ueberschrift",
        "empfehlung_antwort_zuerst": "apply_erster_absatz",
        "empfehlung_interne_links": "apply_interner_link",
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

        self._nicht_behebbar = []
        try:
            try:
                files = self._dispatch(method_name, fix)
            except KeineZielDatei as exc:
                result.nicht_behebbar = str(exc)
                return result
            if not files and self._nicht_behebbar:
                # Nichts geaendert, weil es nicht ging — NICHT "bereits erledigt"
                # (vorher wurde der Befund dadurch faelschlich als behoben gefuehrt).
                n = len(self._nicht_behebbar)
                result.nicht_behebbar = (
                    f"{n} Stelle(n) nicht automatisch behebbar, z. B. "
                    + "; ".join(self._nicht_behebbar[:3])
                )
                return result
            if not files:
                # Bereits angewendet / nichts zu aendern - kein Fehler
                result.success = True
                result.commit_hash = "already-applied"
                result.diff = "(no changes — fix already in place)"
                return result
            result.files_changed = files

            # Git stage + commit (nur versionierte Dateien; ignorierte bleiben
            # geaendert, aber ohne Commit — das Aenderungsbuch haelt sie fest)
            versioniert = self._versionierbar(files) if self._has_git else []
            if self._has_git and not versioniert:
                result.commit_hash = "not-tracked"
                result.diff = "(Dateien sind per .gitignore ausgenommen — geaendert, nicht versioniert)"
            elif self._has_git:
                self._git("add", *versioniert)
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
        if method_name in ("apply_robots_txt", "apply_sitemap_xml"):
            return method(suggestion)
        dateien = self._ziel_dateien(fix)
        self._pruefe_fremde_aenderungen(dateien)
        if fix.get("seite") and not dateien:
            raise KeineZielDatei(
                f"keine Datei fuer Seite {fix.get('seite')} unter {self.root}"
            )
        # Strukturierte Fixes bekommen den ganzen Fix
        if method_name in (
            "apply_twitter_card",
            "apply_image_alt",
            "apply_image_dimensions",
            "apply_jsonld",
            "apply_faq_block",
            "apply_abschnitt",
            "apply_ueberschrift",
            "apply_erster_absatz",
            "apply_interner_link",
        ):
            return method(fix, dateien)
        if method_name == "apply_schema_block":
            return method(
                fix.get("snippet") or fix.get("schema") or suggestion, dateien
            )
        if method_name == "apply_canonical":
            return method(fix.get("url") or suggestion, dateien)
        if method_name == "apply_og_image":
            return method(fix.get("url") or suggestion, dateien)
        return method(suggestion, dateien)

    def _commit_message(self, fix: Dict[str, Any], audit_id: str) -> str:
        title = (fix.get("issue_title") or fix.get("type") or "fix").strip()
        return f"seo-autofix: {title}\n\nType: {fix.get('type')}\nAudit: {audit_id}\nSource: {fix.get('source', '?')}\n"
