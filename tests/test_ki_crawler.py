"""KI-Crawler in robots.txt: Gruppen nach RFC 9309, Zweck Suche vs. Training."""

from seo_autopilot.analyzers.geo_audit import GEOAuditor
from seo_autopilot.analyzers.ki_crawler import erlaubt, gesperrte_ki_crawler, gruppen_lesen
from seo_autopilot.analyzers.robots_sitemap import RobotsResult, RobotsSitemapAuditor


def _robots_befunde(raw):
    auditor = RobotsSitemapAuditor()
    robots = RobotsResult(exists=True, status_code=200, raw=raw)
    auditor._parse_robots(robots)
    return robots, {i["type"]: i for i in auditor.detect_robots_issues(robots)}


class TestGruppen:
    def test_aufeinanderfolgende_user_agents_bilden_eine_gruppe(self):
        # Alter Parser: GPTBot wurde von der zweiten Zeile ueberschrieben
        gruppen = gruppen_lesen("User-agent: OAI-SearchBot\nUser-agent: GPTBot\nDisallow: /\n")
        assert len(gruppen) == 1
        assert gruppen[0].agents == ["OAI-SearchBot", "GPTBot"]

    def test_laengste_regel_gewinnt(self):
        assert erlaubt([("disallow", "/"), ("allow", "/")]) is True  # Gleichstand -> Allow
        assert erlaubt([("disallow", "/")]) is False
        assert erlaubt([("disallow", "")]) is True
        assert erlaubt([("disallow", "/admin/")]) is True

    def test_unicode_trenner_ist_kein_zeilenende(self):
        raw = "User-agent: OAI-SearchBot\nDisallow: /intern # Kommentar Disallow: /\n"
        assert gesperrte_ki_crawler(raw)["sichtbarkeit"] == []


class TestZweck:
    def test_gruppe_mit_zwei_agents_sperrt_beide(self):
        # Vorher: nur der letzte Agent der Gruppe galt als gesperrt
        robots, befunde = _robots_befunde(
            "User-agent: OAI-SearchBot\nUser-agent: PerplexityBot\nDisallow: /\n"
        )
        assert robots.blocked_ai_crawlers == ["OAI-SearchBot", "PerplexityBot"]
        assert "ai_crawler_blocked" in befunde

    def test_neue_such_crawler_werden_erkannt(self):
        _, befunde = _robots_befunde("User-agent: Claude-SearchBot\nDisallow: /\n")
        assert "Claude-SearchBot" in befunde["ai_crawler_blocked"]["title"]

    def test_nur_training_gesperrt_ist_hinweis_kein_fehler(self):
        # GPTBot/ClaudeBot = Training; ChatGPT-Suche und Claude-Suche laufen ueber eigene Crawler
        robots, befunde = _robots_befunde(
            "User-agent: GPTBot\nDisallow: /\nUser-agent: ClaudeBot\nDisallow: /\n"
            "User-agent: Google-Extended\nDisallow: /\nUser-agent: *\nDisallow:\n"
        )
        assert "ai_crawler_blocked" not in befunde
        assert befunde["ai_training_blocked"]["severity"] == "info"
        assert robots.blocked_ai_training == ["GPTBot", "ClaudeBot", "Google-Extended"]

    def test_teilsperre_ist_keine_sperre(self):
        _, befunde = _robots_befunde("User-agent: OAI-SearchBot\nDisallow: /admin/\n")
        assert "ai_crawler_blocked" not in befunde

    def test_eigene_erlaubnis_schlaegt_sternsperre(self):
        robots, befunde = _robots_befunde(
            "User-agent: *\nDisallow: /\nUser-agent: OAI-SearchBot\nAllow: /\n"
        )
        assert "OAI-SearchBot" not in robots.ai_crawlers_blocked_by_wildcard
        assert "ai_crawler_blocked" not in befunde

    def test_sternsperre_nennt_ki_crawler_ohne_doppelbefund(self):
        robots, befunde = _robots_befunde("User-agent: *\nDisallow: /\n")
        assert "wildcard_disallow" in befunde
        assert "ai_crawler_blocked" not in befunde  # gleiche Ursache, nicht doppelt zaehlen
        assert "OAI-SearchBot" in befunde["wildcard_disallow"]["description"]


class TestGeoGleicheQuelle:
    def test_geo_und_robots_melden_dasselbe(self):
        raw = "User-agent: GPTBot\nUser-agent: OAI-SearchBot\nDisallow: /\n"
        robots, _ = _robots_befunde(raw)
        assert GEOAuditor(robots_txt_content=raw).check_ai_crawler_access() == robots.blocked_ai_crawlers
        assert robots.blocked_ai_crawlers == ["OAI-SearchBot"]
