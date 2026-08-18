"""
Tests der Projekt-Ladelogik.

Anlass: Ein einziges unbekanntes Feld in `projects.yaml` liess frueher ALLE
Projekte verschwinden. Der Autopilot lief danach scheinbar normal weiter und
tat nichts — sichtbar nur an einer Zeile im Log. Fuer ein unbeaufsichtigt
laufendes System ist das der gefaehrlichste Fehlertyp ueberhaupt.
"""

import pytest
import yaml

from seo_autopilot.core.project_manager import ProjectManager


def _schreibe(tmp_path, projekte):
    pfad = tmp_path / "projects.yaml"
    pfad.write_text(yaml.safe_dump({"projects": projekte}), encoding="utf-8")
    return str(pfad)


GESUND = {
    "domain": "https://example.com",
    "name": "Beispiel",
    "enabled": True,
}


class TestUnbekannteFelder:
    def test_unbekanntes_feld_kippt_nicht_alle_projekte(self, tmp_path):
        """Der eigentliche Fehler: ein Feld zu viel und nichts lief mehr."""
        pfad = _schreibe(
            tmp_path,
            {
                "a": {**GESUND, "voellig_neues_feld": "irgendwas"},
                "b": dict(GESUND),
            },
        )
        pm = ProjectManager(pfad)
        assert len(pm.list_projects()) == 2

    def test_bekannte_felder_bleiben_erhalten(self, tmp_path):
        pfad = _schreibe(
            tmp_path, {"a": {**GESUND, "betriebsart": "copilot", "quatsch": 1}}
        )
        pm = ProjectManager(pfad)
        projekt = pm.get_project("a")
        assert projekt.betriebsart == "copilot"
        assert projekt.domain == "https://example.com"

    def test_betriebsart_ist_ein_bekanntes_feld(self, tmp_path):
        """Sonst waere die Ausfuehrungssteuerung gar nicht konfigurierbar."""
        pfad = _schreibe(tmp_path, {"a": {**GESUND, "betriebsart": "autopilot"}})
        pm = ProjectManager(pfad)
        assert pm.get_project("a").betriebsart == "autopilot"


class TestFehlerIsolation:
    def test_kaputtes_projekt_reisst_die_anderen_nicht_mit(self, tmp_path):
        pfad = _schreibe(
            tmp_path,
            {
                "kaputt": {"domain": ["keine", "zeichenkette"], "enabled": "ja"},
                "gesund": dict(GESUND),
            },
        )
        pm = ProjectManager(pfad)
        assert pm.get_project("gesund") is not None

    def test_leere_projektliste_ist_kein_absturz(self, tmp_path):
        pfad = tmp_path / "projects.yaml"
        pfad.write_text("projects:\n", encoding="utf-8")
        pm = ProjectManager(str(pfad))
        assert pm.list_projects() == []

    def test_fehlende_datei_ist_kein_absturz(self, tmp_path):
        pm = ProjectManager(str(tmp_path / "gibtsnicht.yaml"))
        assert pm.list_projects() == []
