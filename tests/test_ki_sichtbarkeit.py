"""Tests fuer den KI-Sichtbarkeitsbericht und das Crawler-Radar (ohne Netz, ohne SSH)."""

import ipaddress
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from seo_autopilot import ki_sichtbarkeit as k

JETZT = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
AB = JETZT - timedelta(days=7)
ZEIT = "01/Nov/2026:10:00:00 +0000"
ALT = "20/Oct/2026:10:00:00 +0000"
OPENAI_NETZ = {"prefixes": [{"ipv4Prefix": "20.171.0.0/16"}]}
NETZE = {"OpenAI": k.netze_aus(OPENAI_NETZ)}

UA_SUCHE = "Mozilla/5.0; compatible; OAI-SearchBot/1.3; +https://openai.com/searchbot"
UA_USER = "Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)"
UA_GPT = "Mozilla/5.0; compatible; GPTBot/1.2; +https://openai.com/gptbot"
UA_CLAUDE_SUCHE = "Mozilla/5.0 (compatible; Claude-SearchBot/1.0)"


def kombiniert(ip, pfad, ua, zeit=ZEIT, referer="-"):
    return f'{ip} - - [{zeit}] "GET {pfad} HTTP/1.1" 200 512 "{referer}" "{ua}"'


def visitor(ip, host, pfad, ua, zeit=ZEIT):
    return f"{ip}|{zeit}|{host}|{pfad}|200|-|{ua}"


def ohne_dns(_ip):
    return ""


# ------------------------------------------------------------- Protokolle


def test_bot_reihenfolge_claude_searchbot_vor_claudebot():
    assert k.bot_von(UA_CLAUDE_SUCHE) == ("Claude-SearchBot", "Anthropic", k.SUCHE)
    assert k.bot_von("ClaudeBot/1.0")[0] == "ClaudeBot"
    assert k.bot_von("Mozilla/5.0 Firefox") is None


def test_oai_adsbot_ist_bekannt():
    assert k.bot_von("Mozilla/5.0 (compatible; OAI-AdsBot/1.0)")[0] == "OAI-AdsBot"


def test_zeile_lesen_beide_formate():
    z = k.zeile_lesen(kombiniert("20.171.1.1", "/preise/", UA_SUCHE), "combined")
    assert z["pfad"] == "/preise/" and z["ua"] == UA_SUCHE and z["host"] == ""
    v = k.zeile_lesen(visitor("1.2.3.4", "Tentacl.AI", "/", "x|y"), "visitor")
    assert v["host"] == "tentacl.ai" and v["ua"] == "x|y"
    assert k.zeile_lesen("Muell", "combined") is None
    assert k.zeile_lesen("a|b|c", "visitor") is None


def test_abrufe_sammeln_filtert_zeit_host_und_menschen():
    zeilen = [
        visitor("20.171.1.1", "tentacl.ai", "/a", UA_SUCHE),
        visitor("20.171.1.1", "octopus.tentacl.de", "/b", UA_SUCHE),
        visitor("20.171.1.1", "tentacl.ai", "/c", UA_SUCHE, zeit=ALT),
        visitor("9.9.9.9", "tentacl.ai", "/d", "Mozilla/5.0 Firefox"),
    ]
    abrufe = k.abrufe_sammeln(zeilen, "visitor", ["tentacl.ai"], AB)
    assert [a["pfad"] for a in abrufe] == ["/a"]


# ------------------------------------------------------------- Echtheit


def test_netze_aus_verschachtelt_und_kaputt():
    netze = k.netze_aus(
        {"a": [{"ipv6Prefix": "2001:db8::/32"}, "kein netz", "1.2.3/x"]}
    )
    assert netze == [ipaddress.ip_network("2001:db8::/32")]


def test_echtheit_ip_liste_scanner_und_ohne_liste():
    echt = {"ip": "20.171.5.5", "pfad": "/", "anbieter": "OpenAI", "bot": "GPTBot"}
    fremd = dict(echt, ip="45.1.1.1")
    scanner = dict(echt, pfad="/.env")
    ads = dict(fremd, bot="OAI-AdsBot")
    claude = {"ip": "1.1.1.1", "pfad": "/", "anbieter": "Anthropic", "bot": "ClaudeBot"}
    assert k.echtheit(echt, NETZE, ohne_dns) == k.ECHT
    assert k.echtheit(fremd, NETZE, ohne_dns) == k.FALSCH
    assert k.echtheit(scanner, NETZE, ohne_dns) == k.FALSCH
    assert k.echtheit(ads, NETZE, ohne_dns) == k.UNBESTAETIGT
    assert k.echtheit(claude, NETZE, ohne_dns) == k.UNBESTAETIGT


def test_echtheit_ueber_rueckwaerts_dns():
    apple = {"ip": "17.1.1.1", "pfad": "/", "anbieter": "Apple", "bot": "Applebot"}
    assert k.echtheit(apple, {}, lambda ip: "17-1.applebot.apple.com") == k.ECHT
    assert k.echtheit(apple, {}, lambda ip: "evil.example.com") == k.FALSCH


def test_ip_listen_laden_meldet_kaputte_adressen():
    def holen(url):
        if url.endswith("gptbot.json"):
            return json.dumps(OPENAI_NETZ)
        raise RuntimeError("weg")

    netze, kaputt = k.ip_listen_laden(holen)
    assert len(netze["OpenAI"]) == 1
    assert "https://openai.com/gptbot.json" not in kaputt
    assert "https://www.bing.com/toolbox/bingbot.json" in kaputt


# ------------------------------------------------------------- Auswertung


def test_auswerten_zaehlt_falsche_nicht_mit():
    zeilen = [
        kombiniert("20.171.1.1", "/preise/?x=1", UA_SUCHE),
        kombiniert("20.171.1.2", "/preise/", UA_USER),
        kombiniert("20.171.1.3", "/bild.webp", UA_SUCHE),
        kombiniert("66.66.66.66", "/.env", UA_GPT),
        kombiniert("66.66.66.66", "/", UA_GPT),
    ]
    a = k.auswerten(k.abrufe_sammeln(zeilen, "combined", [], AB), NETZE, ohne_dns)
    assert a["falsch"] == 2
    assert a["arten"] == {k.SUCHE: 2, k.NUTZER: 1}
    assert a["seiten"] == [("/preise/", 2)]
    assert a["bots"]["GPTBot"] == {k.FALSCH: 2}


def test_unbekannte_bots_ab_schwelle():
    neu = "Mozilla/5.0 (compatible; NovaAgent/1.0)"
    zeilen = [kombiniert("1.1.1.1", "/", neu)] * 3 + [
        kombiniert("1.1.1.1", "/", "AhrefsBot/7.0"),
        kombiniert("1.1.1.1", "/", UA_GPT),
    ] * 5
    assert k.unbekannte_bots(zeilen, "combined", mindestens=3) == Counter(
        {"NovaAgent": 3}
    )


def test_umami_sql_prueft_id_streng():
    sql = k.umami_sql("3f2d0e04-d3a1-423f-8f08-aa481e93e81e")
    assert "chatgpt.com" in sql and "interval '7 days'" in sql
    with pytest.raises(ValueError):
        k.umami_sql("x'; DROP TABLE website_event; --")


def test_robots_pruefen_findet_gesperrte_ki_bots():
    robots = (
        "User-agent: OAI-SearchBot\nDisallow: /\n\nUser-agent: GPTBot\nDisallow: /\n"
    )
    gesperrt = k.robots_pruefen(robots, "https://beispiel.de")
    assert "OAI-SearchBot" in gesperrt
    assert "GPTBot" not in gesperrt  # Training darf gesperrt sein
    assert k.robots_pruefen("User-agent: *\nAllow: /\n", "https://beispiel.de") == []


# ------------------------------------------------------------- Radar


def test_radar_erster_lauf_ist_grundstand_dann_nur_neues(monkeypatch):
    monkeypatch.setattr(k, "QUELLEN", {"OpenAI": "u"})
    con = k.db_oeffnen(":memory:")
    seite = {"text": "GPTBot and OAI-SearchBot. The robots.txt user-agent gptbot"}

    def holen(_url):
        return seite["text"]

    assert k.radar_quellen(con, holen, speichern=True) == ([], [])
    seite["text"] += " New: OAI-AdsBot checks ads. Gptbot."
    assert k.radar_quellen(con, holen, speichern=True) == (["OpenAI: OAI-AdsBot"], [])
    assert k.radar_quellen(con, holen, speichern=True) == ([], [])


def test_radar_trockenlauf_speichert_nichts_und_meldet_kaputte_seiten(monkeypatch):
    monkeypatch.setattr(k, "QUELLEN", {"OpenAI": "u", "Apple": "v"})
    con = k.db_oeffnen(":memory:")

    def holen(url):
        if url == "v":
            raise RuntimeError("404")
        return "GPTBot"

    assert k.radar_quellen(con, holen, speichern=False) == ([], ["Apple"])
    assert con.execute("SELECT count(*) FROM ki_radar_namen").fetchone()[0] == 0


def test_radar_protokoll_meldet_jeden_namen_nur_einmal():
    con = k.db_oeffnen(":memory:")
    assert k.radar_logs_neu(con, Counter({"NovaAgent": 30}), True) == [
        "NovaAgent (30 Abrufe)"
    ]
    assert k.radar_logs_neu(con, Counter({"novaagent": 50}), True) == []


# ------------------------------------------------------------- Bericht


def test_projekt_block_mit_vorwoche_und_warnungen():
    w = {
        "auswertung": {
            "bots": {
                "OAI-SearchBot": {k.ECHT: 12},
                "Claude-SearchBot": {k.UNBESTAETIGT: 8},
            },
            "arten": {k.SUCHE: 20, k.TRAINING: 4},
            "seiten": [("/preise/", 9), ("/", 5)],
            "falsch": 3,
        },
        "besucher": {"chatgpt.com": 2},
        "gesperrt": ["PerplexityBot"],
    }
    vorher = {"auswertung": {"arten": {k.SUCHE: 10}}}
    text = k.projekt_block("beispiel.de", w, vorher)
    assert "KI-Suche liest: 20 (▲ 100 %) – ChatGPT 12, Claude 8" in text
    assert "Menschen fragen KI: 0" in text
    assert "Meistgelesen: /preise/, /" in text
    assert "Besucher aus KI: 2 – chatgpt.com 2" in text
    assert "robots.txt sperrt: PerplexityBot" in text


def test_projekt_block_ohne_protokoll_und_mit_fehler():
    assert "kein Protokoll angebunden" in k.projekt_block("a.de", {}, None)
    text = k.projekt_block("a.de", {"fehler": "Protokoll nicht lesbar"}, None)
    assert "kein Protokoll" not in text and "⚠️ Protokoll nicht lesbar" in text


def test_bericht_teilt_lange_nachrichten_und_zeigt_radar():
    bloecke = ["x" * 1500] * 4
    radar = {"quellen": ["OpenAI: OAI-AdsBot"], "quelle_kaputt": ["Apple"]}
    teile = k.bericht(bloecke, radar, 7, JETZT)
    assert len(teile) >= 2 and all(len(t) <= 3800 for t in teile)
    assert teile[0].startswith("🤖 KI-Sichtbarkeit · 7 Tage bis 02.11.2026")
    assert "🆕 Neu bei den Anbietern: OpenAI: OAI-AdsBot" in teile[-1]
    assert "Anbieterseite nicht lesbar: Apple" in teile[-1]
    assert "Aussortiert (Scanner mit falschem Bot-Namen): 7" in teile[-1]
    assert "keine neuen KI-Crawler" in k.bericht([], {}, 0, JETZT)[0]


def test_projekt_lauf_ohne_ssh(monkeypatch):
    zeilen = [kombiniert("20.171.1.1", "/", UA_SUCHE)]
    monkeypatch.setattr(k, "befehl_ausfuehren", lambda b: zeilen)
    p = {
        "domain": "https://beispiel.de",
        "ki_sichtbarkeit": {"logs": [{"befehl": "cat x", "format": "combined"}]},
    }
    w, unbekannt = k.projekt_lauf(
        "b", p, AB, NETZE, lambda url: "User-agent: *\nAllow: /\n", None
    )
    assert w["auswertung"]["arten"] == {k.SUCHE: 1}
    assert w["gesperrt"] == [] and not unbekannt

    def kaputt(_b):
        raise RuntimeError("ssh weg")

    monkeypatch.setattr(k, "befehl_ausfuehren", kaputt)
    w, _ = k.projekt_lauf("b", p, AB, NETZE, lambda url: "", None)
    assert w["fehler"] == "Protokoll nicht lesbar" and "auswertung" not in w


def test_waechter_meldet_stillstand():
    from seo_autopilot import health

    con = k.db_oeffnen(":memory:")
    r = health.HealthReport(zeitpunkt=JETZT)
    health._pruefe_ki_sichtbarkeit(con, "x ki_sichtbarkeit", JETZT, r)
    assert any("laeuft nicht" in b.titel for b in r.befunde)
    con.execute("INSERT INTO ki_laeufe VALUES ('2026-10-26T06:30:00+00:00','voll')")
    r2 = health.HealthReport(zeitpunkt=JETZT)
    health._pruefe_ki_sichtbarkeit(con, "x ki_sichtbarkeit", JETZT, r2)
    assert r2.befunde == []
    r3 = health.HealthReport(zeitpunkt=JETZT + timedelta(days=2))
    health._pruefe_ki_sichtbarkeit(
        con, "x ki_sichtbarkeit", JETZT + timedelta(days=2), r3
    )
    assert any("laeuft nicht" in b.titel for b in r3.befunde)
    r4 = health.HealthReport(zeitpunkt=JETZT)
    health._pruefe_ki_sichtbarkeit(con, "kein eintrag", JETZT, r4)
    assert r4.befunde == []


def test_umami_zaehlt_keine_normale_bing_suche():
    sql = k.umami_sql("3f2d0e04-d3a1-423f-8f08-aa481e93e81e")
    assert "'bing.com'" not in sql and "%bing.com%" not in sql
    assert "referrer_domain LIKE '%.perplexity.ai'" in sql


def test_technik_dateien_sind_keine_gelesenen_seiten():
    zeilen = [
        kombiniert("20.171.1.1", p, UA_SUCHE)
        for p in (
            "/robots.txt",
            "/sitemap.xml",
            "/favicon.ico",
            "/llms.txt",
            "/preise/",
        )
    ]
    a = k.auswerten(k.abrufe_sammeln(zeilen, "combined", [], AB), NETZE, ohne_dns)
    assert dict(a["seiten"]) == {"/llms.txt": 1, "/preise/": 1}


def test_radar_ignoriert_eigenen_pruefer_und_allgemeine_woerter():
    uas = ["SEOAutopilotBot/1.0", "Mozilla/5.0 robot", "searchbot/2", "ExaSearchBot/1"]
    zeilen = [kombiniert("1.1.1.1", "/", ua) for ua in uas] * 25
    assert set(k.unbekannte_bots(zeilen, "combined")) == {"ExaSearchBot"}


def test_block_ueberlebt_das_speichern_der_projektliste(tmp_path):
    """Der Autopilot-Lauf speichert projects.yaml neu und verwarf unbekannte Felder (31.10.2026)."""
    from seo_autopilot.core.project_manager import ProjectManager

    pfad = tmp_path / "projects.yaml"
    pfad.write_text(
        "projects:\n  camp:\n    domain: https://x.de\n    name: Camp\n"
        "    ki_sichtbarkeit:\n      umami_id: abc\n"
        "      logs:\n        - befehl: cat x\n          format: combined\n"
    )
    pm = ProjectManager(str(pfad))
    pm.update_project("camp", name="Camp 2")
    import yaml

    gespeichert = yaml.safe_load(pfad.read_text())["projects"]["camp"]
    assert gespeichert["ki_sichtbarkeit"]["logs"][0]["befehl"] == "cat x"


def test_radar_ignoriert_doku_adressen():
    text = "see /docs/multi-agent and docs-agent; ChatGPT-User, meta-externalagent"
    assert set(k.namen_auf_seite(text)) == {"chatgpt-user", "meta-externalagent"}
