"""Google-Updates im Messzeitraum (Sperre 6 der Wirkungsmessung)."""

from datetime import date, datetime, timezone

import pytest

from seo_autopilot import google_updates as gu
from seo_autopilot.changelog_book import AKTION_META_TITLE, URHEBER_AUTOPILOT, aenderungen, notiere_aenderung
from seo_autopilot.wirkung import (
    URTEIL_NICHT_ZURECHENBAR,
    URTEIL_VERBESSERT,
    miss_eine,
    tabelle_anlegen,
)


class TestListe:
    def test_liste_geladen_nur_bestaetigte_ranking_updates(self):
        updates = gu.ranking_updates()
        assert len(updates) >= 15
        assert {u.art for u in updates} <= {"core", "core+spam", "spam"}
        assert all(u.quelle.startswith("https://") for u in updates)
        # unverified[] (z. B. "SAFE") darf nie auftauchen
        assert not any("SAFE" in u.name for u in updates)

    def test_core_update_mai_2026_im_zeitraum(self):
        treffer = gu.im_zeitraum(date(2026, 5, 25), date(2026, 6, 1))
        assert any(u.art == "core" and u.start == date(2026, 5, 21) for u in treffer)

    def test_ruhiger_zeitraum_ohne_update(self):
        # Zwischen Maerz- (bis 16.04.) und Back-Button-/Mai-Updates liegt nichts
        assert gu.im_zeitraum(date(2026, 4, 20), date(2026, 5, 6)) == []

    def test_stand_und_veraltet(self):
        assert gu.stand() == date(2026, 9, 28)
        assert not gu.ist_veraltet(date(2026, 10, 1))
        assert gu.ist_veraltet(date(2027, 3, 1))

    def test_kaputte_datei_bricht_nichts(self, tmp_path):
        p = tmp_path / "kaputt.json"
        p.write_text("{nicht json", encoding="utf-8")
        assert gu.ranking_updates(str(p)) == []
        assert gu.im_zeitraum(date(2026, 1, 1), date(2026, 12, 31), str(p)) == []


def _holer(vorher, nachher):
    aufrufe = {"n": 0}

    async def hole(url, von, bis):
        aufrufe["n"] += 1
        return vorher if aufrufe["n"] == 1 else nachher

    return hole


BESSER = (
    {"clicks": 10, "impressions": 400, "position": 12.0},
    {"clicks": 25, "impressions": 500, "position": 6.0},
)


async def _miss(db, am):
    cid = notiere_aenderung(
        db, "beratung-beispiel", AKTION_META_TITLE, ziel_url="https://example.com/",
        urheber=URHEBER_AUTOPILOT, vorher="Alt", nachher="Neu", zeitpunkt=am,
    )
    aenderung = next(a for a in aenderungen(db, tage=0) if a.id == cid)
    return await miss_eine(db, aenderung, 7, _holer(*BESSER), heute=date(2026, 9, 30))


class TestWirkungsmessung:
    @pytest.fixture
    def db(self, tmp_path):
        pfad = str(tmp_path / "t.db")
        tabelle_anlegen(pfad)
        return pfad

    @pytest.mark.asyncio
    async def test_core_update_macht_erfolg_unzurechenbar(self, db):
        # Titel geaendert am 25.05.2026, Core Update ab 21.05.2026 rollt noch aus
        m = await _miss(db, datetime(2026, 5, 25, 9, tzinfo=timezone.utc))
        assert m.urteil == URTEIL_NICHT_ZURECHENBAR
        assert "May 2026 Core Update" in m.notiz
        assert "„besser“" in m.notiz  # das eigentliche Ergebnis bleibt nachlesbar

    @pytest.mark.asyncio
    async def test_spam_update_nur_vermerkt(self, db):
        # 26.08.2026: August-2026-Spam-Update (ab 18.08.) laeuft noch
        m = await _miss(db, datetime(2026, 8, 26, 9, tzinfo=timezone.utc))
        assert m.urteil == URTEIL_VERBESSERT
        assert "Spam Update" in m.notiz

    @pytest.mark.asyncio
    async def test_ohne_update_unveraendert(self, db):
        m = await _miss(db, datetime(2026, 4, 28, 9, tzinfo=timezone.utc))
        assert m.urteil == URTEIL_VERBESSERT
        assert "Google-Update" not in m.notiz
