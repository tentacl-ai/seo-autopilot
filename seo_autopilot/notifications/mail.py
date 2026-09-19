"""Meldungen per Mail an Robert - Ersatz fuer Telegram (am 09.09.2026 abgeschafft).

Ohne diesen Weg liefen Waechter-Meldungen seit Wochen ins Leere: `send_plain_message`
sprang still ab ("Telegram not configured - skipping"), und niemand sah, was kaputt war.

Gemeldet wird nur, wenn sich der Inhalt gegenueber der letzten Meldung geaendert hat.
Ein Waechter, der jeden Tag dieselbe Warnung schickt, wird ungelesen weggeklickt.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
from pathlib import Path
from ..core.config import settings

logger = logging.getLogger(__name__)

MAILER = settings.MAILER_PFAD or ""
EMPFAENGER = (settings.MELDUNGS_EMPFAENGER or "").strip()
STAND = Path(__file__).resolve().parents[2] / "logs" / "gemeldet"

# Platzhalter- und reservierte Domains duerfen niemals echte Meldungen erhalten.
# `beispiel.de` nimmt Mail technisch an; genau deshalb reicht eine optisch
# offensichtliche Beispieladresse als Schutz nicht aus.
RESERVIERTE_DOMAINS = {
    "beispiel.de",
    "example.com",
    "example.net",
    "example.org",
}


def empfaenger_erlaubt(adresse: str) -> bool:
    adresse = (adresse or "").strip().lower()
    if adresse.count("@") != 1:
        return False
    lokal, domain = adresse.rsplit("@", 1)
    if not lokal or not domain or domain in RESERVIERTE_DOMAINS:
        return False
    return not domain.endswith((".example", ".invalid", ".localhost", ".test"))


def fingerabdruck(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def schon_gemeldet(schluessel: str, text: str, ordner: Path = STAND) -> bool:
    pfad = ordner / f"{schluessel}.txt"
    return pfad.exists() and pfad.read_text().strip() == fingerabdruck(text)


def merken(schluessel: str, text: str, ordner: Path = STAND) -> None:
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"{schluessel}.txt").write_text(fingerabdruck(text))


def an_robert(
    betreff: str, text: str, schluessel: str | None = None, ordner: Path = STAND
) -> bool:
    """Schickt eine Klartext-Mail. Mit `schluessel` nur bei geaendertem Inhalt."""
    if not MAILER:
        logger.warning("[Meldung] Versand gesperrt: MAILER_PFAD fehlt")
        return False
    if not empfaenger_erlaubt(EMPFAENGER):
        logger.warning(
            "[Meldung] Versand gesperrt: kein zulaessiger MELDUNGS_EMPFAENGER konfiguriert"
        )
        return False
    if schluessel and schon_gemeldet(schluessel, text, ordner):
        logger.info(f"[Meldung] {schluessel}: unveraendert, keine Mail")
        return False
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(text)
        datei = fh.name
    try:
        lauf = subprocess.run(
            [
                "python3",
                MAILER,
                "--to",
                EMPFAENGER,
                "--subject",
                betreff,
                "--body-file",
                datei,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    finally:
        Path(datei).unlink(missing_ok=True)
    if lauf.returncode != 0:
        logger.warning(f"[Meldung] Mail fehlgeschlagen: {lauf.stderr[-200:]}")
        return False
    if schluessel:
        merken(schluessel, text, ordner)
    return True
