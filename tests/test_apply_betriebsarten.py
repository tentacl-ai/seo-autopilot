"""
Tests der Ausführungssteuerung IM ApplyAgent.

`test_ausfuehrung.py` prüft die Entscheidungslogik für sich. Hier geht es um
die Frage, die zählt: Hält die Sperre auch im echten Agenten — dem einzigen
Ort, an dem tatsächlich Dateien verändert werden?
"""

import asyncio
from types import SimpleNamespace

import pytest

import seo_autopilot.ausfuehrung as ausfuehrung
from seo_autopilot.agents.apply import ApplyAgent
from seo_autopilot.ausfuehrung import freigaben, tabelle_anlegen


@pytest.fixture
def db(tmp_path, monkeypatch):
    pfad = str(tmp_path / "t.db")
    tabelle_anlegen(pfad)
    monkeypatch.setattr(ausfuehrung, "standard_db_pfad", lambda: pfad)
    return pfad


def _projekt(betriebsart=None, whitelist_extra=None, auto_fix=False):
    return SimpleNamespace(
        id="test",
        betriebsart=betriebsart,
        auto_fix_enabled=auto_fix,
        auto_fix_config={"whitelist_extra": whitelist_extra or []},
        adapter_type="static",
        adapter_config={"root_path": "/tmp/gibt-es-nicht"},
    )


def _fix(typ, url="https://x.de/a"):
    return {"type": typ, "title": f"Befund {typ}", "url": url, "priority": "high"}


def _lauf(projekt, fixes, db):
    ctx = SimpleNamespace(
        agent_results={"content": SimpleNamespace(fixes=fixes)},
        force_apply=False,
    )
    agent = ApplyAgent(
        project_id="test", audit_id="a1", project_config=projekt, context=ctx
    )
    return asyncio.run(agent.run())


class TestSperreImEchtenAgenten:
    def test_gesperrtes_landet_in_freigabe_statt_ausfuehrung(self, db):
        """Der Fall, der zaehlt: Autopilot AN, gesperrter Typ in der Whitelist."""
        projekt = _projekt(
            betriebsart="autopilot",
            whitelist_extra=["missing_canonical", "missing_robots_txt"],
        )

        _lauf(projekt, [_fix("missing_canonical"), _fix("missing_robots_txt")], db)

        offen = freigaben(db)
        assert len(offen) == 2
        assert all(f.ist_gesperrt for f in offen)

    def test_geloeschte_seite_wird_nie_automatisch_ausgefuehrt(self, db):
        projekt = _projekt(betriebsart="autopilot", whitelist_extra=["delete_page"])
        _lauf(projekt, [_fix("delete_page")], db)
        assert freigaben(db)[0].ist_gesperrt


class TestFreigegebenesWirdAusgefuehrt:
    """Die CLI verspricht 'wird beim naechsten Lauf ausgefuehrt'.

    Ohne diese Verknuepfung waere die Freigabe eine Sackgasse: Der Mensch
    stimmt zu, und nichts passiert.
    """

    def test_freigegebener_vorschlag_kommt_in_die_ausfuehrung(self, db):
        from seo_autopilot.agents.apply import _freigegebene_fixes
        from seo_autopilot.ausfuehrung import (
            STATUS_FREIGEGEBEN,
            entscheiden,
            zur_freigabe,
        )

        fix = _fix("missing_canonical")
        kennung = zur_freigabe(db, "test", fix, "gesperrt")
        entscheiden(db, kennung, STATUS_FREIGEGEBEN, von="robert")

        treffer = _freigegebene_fixes(db, "test", [fix])

        assert len(treffer) == 1
        assert treffer[0]["_freigabe_id"] == kennung

    def test_offener_vorschlag_wird_nicht_ausgefuehrt(self, db):
        from seo_autopilot.agents.apply import _freigegebene_fixes
        from seo_autopilot.ausfuehrung import zur_freigabe

        fix = _fix("missing_canonical")
        zur_freigabe(db, "test", fix, "gesperrt")

        assert _freigegebene_fixes(db, "test", [fix]) == []

    def test_abgelehnter_vorschlag_wird_nicht_ausgefuehrt(self, db):
        from seo_autopilot.agents.apply import _freigegebene_fixes
        from seo_autopilot.ausfuehrung import (
            STATUS_ABGELEHNT,
            entscheiden,
            zur_freigabe,
        )

        fix = _fix("missing_canonical")
        kennung = zur_freigabe(db, "test", fix, "gesperrt")
        entscheiden(db, kennung, STATUS_ABGELEHNT)

        assert _freigegebene_fixes(db, "test", [fix]) == []

    def test_alte_freigabe_ohne_aktuellen_befund_tut_nichts(self, db):
        """Eine Zustimmung von vor drei Wochen darf nichts Erledigtes anfassen."""
        from seo_autopilot.agents.apply import _freigegebene_fixes
        from seo_autopilot.ausfuehrung import (
            STATUS_FREIGEGEBEN,
            entscheiden,
            zur_freigabe,
        )

        kennung = zur_freigabe(db, "test", _fix("missing_canonical"), "gesperrt")
        entscheiden(db, kennung, STATUS_FREIGEGEBEN)

        # Der Befund besteht im aktuellen Lauf nicht mehr.
        assert _freigegebene_fixes(db, "test", []) == []


class TestBetriebsartenImAgenten:
    def test_beobachter_legt_nichts_an_und_aendert_nichts(self, db):
        ergebnis = _lauf(_projekt(betriebsart="beobachter"), [_fix("short_title")], db)
        assert "Beobachter" in ergebnis.log_output
        assert freigaben(db) == []

    def test_copilot_legt_auch_harmloses_vor(self, db):
        _lauf(_projekt(betriebsart="copilot"), [_fix("short_title")], db)
        offen = freigaben(db)
        assert len(offen) == 1
        assert not offen[0].ist_gesperrt
        assert "Copilot" in offen[0].begruendung

    def test_ohne_betriebsart_wird_nichts_geaendert(self, db):
        """Der sichere Standard gilt auch, wenn nichts konfiguriert ist."""
        ergebnis = _lauf(_projekt(), [_fix("short_title")], db)
        assert "Beobachter" in ergebnis.log_output

    def test_geringe_schwere_bleibt_unangetastet(self, db):
        projekt = _projekt(betriebsart="copilot")
        fix = _fix("short_title")
        fix["priority"] = "low"
        _lauf(projekt, [fix], db)
        assert freigaben(db) == []
