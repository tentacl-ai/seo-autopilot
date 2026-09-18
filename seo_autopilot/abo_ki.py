"""Claude-Anfragen ueber Roberts Max-Abo - NICHT ueber die bezahlte API.

Einzige Stelle im SEO-Autopilot, die Claude ruft (Umstellung 16.09.2026, weil
die Fix-Vorschlaege mit Opus ueber ANTHROPIC_API_KEY Auto-Recharge-Rechnungen
ausloesten). Muster wie ki_bruecke/social-agent: Claude-CLI mit dem Abo-Token
aus automation.env; bezahlte Zugaenge werden aus der Umgebung entfernt und der
Aufruf bricht ab, falls die CLI trotzdem einen API-Key meldet.

Bewusst KEIN Rueckfall auf die API: scheitert das Abo, nehmen die Aufrufer ihre
Vorlage/Heuristik.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

AUTOMATION_ENV = Path("/home/claude-user/.claude/automation.env")
HEIMAT = "/home/claude-user"
CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "/home/claude-user/.npm-global/bin/claude")
BEZAHLTE_ZUGAENGE = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_API_KEY",
)
_URL = re.compile(r"https?://[^\s\"'<>)\]}\\]+")


class KeinZugang(RuntimeError):
    """Abo-Token oder CLI fehlt."""


def token() -> str:
    try:
        for zeile in AUTOMATION_ENV.read_text(encoding="utf-8").splitlines():
            zeile = zeile.strip().removeprefix("export ").strip()
            if zeile.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                return zeile.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def verfuegbar() -> bool:
    return bool(token()) and Path(CLAUDE_CLI).exists()


def modell_alias(name: Optional[str]) -> str:
    """claude-opus-5 -> opus usw.; Unbekanntes -> sonnet."""
    n = (name or "").lower()
    for alias in ("opus", "sonnet", "haiku"):
        if alias in n:
            return alias
    return "sonnet"


def _nachricht(inhalt: List[Dict]) -> str:
    return (
        json.dumps({"type": "user", "message": {"role": "user", "content": inhalt}})
        + "\n"
    )


def lauf(
    inhalt: List[Dict],
    system: Optional[str] = None,
    modell: str = "sonnet",
    websuche: bool = False,
    zeitlimit: float = 180,
) -> Dict:
    """Ein Aufruf. inhalt = Content-Bloecke (text/image/document).

    Rueckgabe {"text": ..., "urls": [...]} - urls aus Websuche-Ergebnissen.
    """
    tok = token()
    if not tok or not Path(CLAUDE_CLI).exists():
        raise KeinZugang("Abo-Token oder Claude-CLI fehlt")
    umfeld = {k: v for k, v in os.environ.items() if k not in BEZAHLTE_ZUGAENGE}
    umfeld["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    umfeld["HOME"] = HEIMAT

    befehl = [
        CLAUDE_CLI,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        modell_alias(modell),
        "--effort",
        "low",
    ]
    if websuche:
        befehl += ["--tools", "WebSearch", "--allowedTools", "WebSearch"]
    else:
        befehl += ["--tools", ""]
    if system:
        befehl += ["--system-prompt", system]

    p = subprocess.run(
        befehl,
        input=_nachricht(inhalt),
        capture_output=True,
        text=True,
        timeout=zeitlimit,
        cwd=HEIMAT,
        env=umfeld,
    )

    ergebnis, quelle, urls = None, None, []
    for zeile in p.stdout.splitlines():
        try:
            o = json.loads(zeile)
        except ValueError:
            continue
        if o.get("type") == "system" and o.get("subtype") == "init":
            quelle = o.get("apiKeySource")
        elif o.get("type") == "user":
            # Websuche-Ergebnisse kommen als tool_result zurueck
            urls += _URL.findall(json.dumps(o.get("message", {}), ensure_ascii=False))
        elif o.get("type") == "result":
            ergebnis = o
    if quelle not in (None, "none"):
        raise RuntimeError(f"CLI nutzt bezahlten Zugang ({quelle}) - abgebrochen")
    if not ergebnis:
        raise RuntimeError((p.stderr or p.stdout or "keine Antwort")[-300:])
    if ergebnis.get("is_error"):
        raise RuntimeError(str(ergebnis.get("result") or ergebnis.get("subtype"))[:300])
    text = (ergebnis.get("result") or "").strip()
    urls += _URL.findall(text)
    return {"text": text, "urls": list(dict.fromkeys(u.rstrip(".,;\\") for u in urls))}


def fragen(
    prompt: str,
    system: Optional[str] = None,
    modell: str = "sonnet",
    zeitlimit: float = 180,
) -> str:
    return lauf(
        [{"type": "text", "text": prompt}],
        system=system,
        modell=modell,
        zeitlimit=zeitlimit,
    )["text"]


# --- Ersatz fuer anthropic.Anthropic() in bestehendem Code ------------------


class _Block:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Antwort:
    stop_reason = "end_turn"

    def __init__(self, text: str):
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, zeitlimit: float):
        self._zeitlimit = zeitlimit

    def create(self, model=None, messages=None, system=None, **_ignoriert) -> _Antwort:
        """Nimmt nur die letzte User-Nachricht (alle Aufrufer schicken genau eine)."""
        inhalt = (messages or [{}])[-1].get("content", "")
        if isinstance(inhalt, str):
            inhalt = [{"type": "text", "text": inhalt}]
        return _Antwort(
            lauf(inhalt, system=system, modell=model, zeitlimit=self._zeitlimit)["text"]
        )


class AboClient:
    """Duck-Typing-Ersatz: client.messages.create(...) wie beim Anthropic-SDK."""

    def __init__(self, zeitlimit: float = 180):
        self.messages = _Messages(zeitlimit)
