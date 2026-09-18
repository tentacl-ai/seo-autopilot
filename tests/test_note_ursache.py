"""Tests fuer die Note nach Ursache statt Menge (note.py, befund_arten.py).

Anlass (Rundumschlag 18.09.2026): coaching-beispiel verlor ~19 Punkte fuer 17x
unreachable_page aus EINER fehlenden Navigation; Faustregeln wogen so viel
wie echte Fehler.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from seo_autopilot.befund_arten import (
    BEFUND_ARTEN,
    EMPFEHLUNG,
    FEHLER,
    HINWEIS,
    art_von,
)
from seo_autopilot.core.audit_context import AuditContext
from seo_autopilot.note import (
    berechne_note,
    berechne_note_alt,
    menge,
    ohne_doppelte_ursachen,
)


def _b(typ, schwere="high", url="https://x.de/a"):
    return {"type": typ, "severity": schwere, "affected_url": url}


class TestBefundArten:
    def test_jeder_typ_im_code_ist_eingeordnet(self):
        """Neue Befundtypen duerfen nicht still als 'unbekannt' durchrutschen."""
        wurzel = Path(__file__).resolve().parents[1] / "seo_autopilot"
        muster = re.compile(
            r'"([a-z]+(?:_[a-z0-9]+)+)",\s*\n?\s*"(?:high|medium|low|critical|info)"'
        )
        gefunden = set()
        for datei in list((wurzel / "analyzers").glob("*.py")) + list(
            (wurzel / "agents").glob("*.py")
        ):
            gefunden |= set(muster.findall(datei.read_text()))
        fehlend = sorted(t for t in gefunden if t not in BEFUND_ARTEN)
        assert fehlend == []

    def test_faustregeln_sind_empfehlungen(self):
        for typ in (
            "thin_content",
            "short_title",
            "long_meta_description",
        ):
            assert art_von(typ) == EMPFEHLUNG
        # GEO-Faustregeln und generisches JSON-LD sind seit dem Google-Leitfaden
        # (Stand 10.07.2026) nur noch Hinweise, siehe test_google_ki_leitfaden.py
        for typ in ("geo_answer_first", "geo_fact_density", "no_jsonld"):
            assert art_von(typ) == HINWEIS

    def test_technische_fehler(self):
        for typ in (
            "broken_internal_link",
            "missing_title",
            "noindex_detected",
            "soft_404_catchall",
        ):
            assert art_von(typ) == FEHLER

    def test_unbekannt_und_http_status_sind_fehler(self):
        assert art_von("http_404") == FEHLER
        assert art_von(None) == FEHLER
        assert art_von("irgendwas_neues") == FEHLER


class TestAbnehmenderGrenznutzen:
    def test_menge(self):
        assert menge(0.5) == 0.5
        assert menge(1) == 1
        assert menge(16) == 5

    def test_siebzehn_gleiche_zaehlen_nicht_siebzehnfach(self):
        """coaching-beispiel: 17x unreachable_page (high) auf 15 Seiten."""
        befunde = [_b("unreachable_page", url=f"https://x.de/{i}") for i in range(17)]
        alt = berechne_note_alt(befunde, 15)
        neu = berechne_note(befunde, 15).note
        assert alt == 50.0  # Deckel erreicht
        assert neu > 80  # eine Ursache, ~15 Punkte
        # aber: verschiedene Ursachen zaehlen weiterhin voll
        verschieden = [_b(t) for t in ("missing_title", "noindex_detected", "no_https")]
        assert berechne_note(verschieden, 15).note == 91.0

    def test_mehr_betroffene_seiten_bleibt_schlechter(self):
        wenig = [_b("missing_title", url=f"https://x.de/{i}") for i in range(2)]
        viel = [_b("missing_title", url=f"https://x.de/{i}") for i in range(10)]
        assert berechne_note(viel, 15).note < berechne_note(wenig, 15).note


class TestDoppelteUrsache:
    def test_unreachable_und_orphan_an_derselben_adresse(self):
        befunde = [_b("unreachable_page", "high"), _b("orphan_page", "medium")]
        zaehlend, zusammen = ohne_doppelte_ursachen(befunde)
        assert [b["type"] for b in zaehlend] == ["unreachable_page"]
        assert zusammen == 1

    def test_verschiedene_adressen_bleiben_getrennt(self):
        befunde = [
            _b("unreachable_page", url="https://x.de/a"),
            _b("orphan_page", url="https://x.de/b"),
        ]
        assert len(ohne_doppelte_ursachen(befunde)[0]) == 2

    def test_doppelung_und_kannibalisierung(self):
        befunde = [
            _b("near_duplicate_content"),
            _b("keyword_cannibalization"),
            _b("cluster_cannibalization"),
        ]
        assert len(ohne_doppelte_ursachen(befunde)[0]) == 1

    def test_ki_crawler_aus_zwei_analyzern_domainweit(self):
        befunde = [
            _b("ai_crawler_blocked", url="https://x.de/robots.txt"),
            {
                "type": "geo_ai_crawler_blocked",
                "severity": "critical",
                "affected_url": None,
            },
        ]
        assert len(ohne_doppelte_ursachen(befunde)[0]) == 1

    def test_url_aus_affected_items(self):
        befunde = [
            {
                "type": "unreachable_page",
                "severity": "high",
                "affected_items": '{"url": "https://x.de/a/"}',
            },
            {
                "type": "orphan_page",
                "severity": "medium",
                "affected_items": '{"url": "https://x.de/a"}',
            },
        ]
        assert len(ohne_doppelte_ursachen(befunde)[0]) == 1


class TestEmpfehlungenGedeckelt:
    def test_viele_faustregeln_kosten_hoechstens_zehn(self):
        befunde = [
            _b(t, s, f"https://x.de/{i}")
            for i in range(40)
            for t, s in (
                ("short_title", "high"),
                ("long_meta_description", "medium"),
                ("thin_content", "medium"),
            )
        ]
        ergebnis = berechne_note(befunde, 15)
        assert ergebnis.note == 90.0
        assert ergebnis.abzuege["empfehlung"] == 10.0

    def test_ein_echter_fehler_wiegt_mehr_als_eine_faustregel(self):
        assert (
            berechne_note([_b("missing_title")], 15).note
            < berechne_note([_b("short_title")], 15).note
        )


class TestRueckwaertsvertraeglich:
    @pytest.mark.parametrize("seiten", [None, 15, 30, 40])
    def test_ohne_typen_wie_bisher(self, seiten):
        befunde = (
            [{"severity": "high"}] * 3
            + [{"severity": "medium"}] * 5
            + [{"severity": "low"}] * 7
        )
        assert berechne_note(befunde, seiten).note == berechne_note_alt(befunde, seiten)

    def test_info_zaehlt_nicht(self):
        assert (
            berechne_note([_b("schema_rich_result_opportunity", "info")], 15).note
            == 100.0
        )

    def test_critical_zaehlt_wie_high(self):
        """Bisher fiel critical komplett aus der Rechnung."""
        assert berechne_note([_b("fetch_error", "critical")], 15).note == 97.0


class TestAuditContext:
    def _ctx(self):
        return AuditContext(
            audit_id="a",
            project_id="p",
            project_config=SimpleNamespace(name="T", domain="https://x.de"),
        )

    def test_art_wird_an_jeden_befund_gehaengt(self):
        ctx = self._ctx()
        ctx.add_result(
            "analyzer",
            SimpleNamespace(
                issues=[_b("short_title"), _b("missing_title"), _b("geo_answer_first")]
            ),
        )
        assert [i["art"] for i in ctx.all_issues] == [EMPFEHLUNG, FEHLER, HINWEIS]

    def test_strategy_behaelt_art(self):
        ctx = self._ctx()
        ctx.add_result("strategy", SimpleNamespace(issues=[_b("thin_content")]))
        assert ctx.all_issues[0]["art"] == EMPFEHLUNG

    def test_score_details(self):
        ctx = self._ctx()
        ctx.all_issues = [_b("unreachable_page"), _b("orphan_page", "medium")]
        ctx.agent_results["analyzer"] = SimpleNamespace(metrics={"pages_crawled": 15})
        assert ctx.calculate_score() == 97.0
        assert ctx.score_details["zusammengefasst"] == 1
