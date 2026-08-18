"""
Der Bot-Token darf nicht in den Logs landen.

Gefunden am 18.08.2026: httpx protokolliert jede Anfrage samt vollständiger
URL auf INFO-Ebene — und der Telegram-Token steht in der URL. In `cron.log`
standen dadurch 314 Zeilen mit dem Token im Klartext.
"""

import logging

import seo_autopilot.notifications.telegram  # noqa: F401  (setzt die Log-Level)


class TestTokenBleibtAusDenLogs:
    def test_httpx_protokolliert_keine_anfragen_mehr(self):
        """Auf INFO würde die komplette URL samt Token geschrieben."""
        assert logging.getLogger("httpx").level >= logging.WARNING

    def test_httpcore_ebenfalls_gedaempft(self):
        assert logging.getLogger("httpcore").level >= logging.WARNING

    def test_echte_fehler_bleiben_sichtbar(self):
        """Dämpfen heisst nicht stummschalten — Warnungen müssen durch."""
        assert logging.getLogger("httpx").level <= logging.WARNING
