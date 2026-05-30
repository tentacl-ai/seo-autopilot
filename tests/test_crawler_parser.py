"""Unit tests for the HTML parser inside crawler._parse_html_into."""

from seo_autopilot.sources.crawler import (
    PageData,
    _parse_html_into,
    _expand_jsonld_graph,
)

HTML_FULL = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Tentacl – KI Business Systeme</title>
  <meta name="description" content="Beste KI Business Plattform.">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="index,follow">
  <meta property="og:title" content="Tentacl">
  <meta property="og:image" content="https://tentacl.ai/og.png">
  <meta name="twitter:card" content="summary_large_image">
  <link rel="canonical" href="https://tentacl.ai/">
  <link rel="alternate" hreflang="en" href="https://tentacl.ai/en/">
  <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Organization","name":"Tentacl"}
  </script>
</head>
<body>
  <h1>Erste H1</h1>
  <h2>Section A</h2>
  <h2>Section B</h2>
  <p>Lorem ipsum dolor sit amet consectetur.</p>
  <a href="/about">About</a>
  <a href="https://external.com">External</a>
  <img src="a.png" alt="logo">
  <img src="b.png">
</body>
</html>
"""


def test_parse_full_html():
    page = PageData(url="https://tentacl.ai/", final_url="https://tentacl.ai/")
    _parse_html_into(page, HTML_FULL)
    assert page.title == "Tentacl – KI Business Systeme"
    assert page.meta_description == "Beste KI Business Plattform."
    assert page.lang == "de"
    assert page.viewport == "width=device-width, initial-scale=1"
    assert page.canonical == "https://tentacl.ai/"
    assert "Organization" in page.schema_types
    assert page.h1 == ["Erste H1"]
    assert len(page.h2) == 2
    assert page.internal_links == 1
    assert page.external_links == 1
    assert page.images_total == 2
    assert page.images_without_alt == 1
    assert page.og_tags["og:title"] == "Tentacl"
    assert page.og_tags["og:image"] == "https://tentacl.ai/og.png"
    assert page.twitter_tags["twitter:card"] == "summary_large_image"
    assert page.word_count > 3


def test_parse_empty_html():
    page = PageData(url="https://x.test/", final_url="https://x.test/")
    _parse_html_into(page, "<html><body>hi</body></html>")
    assert page.title is None
    assert page.meta_description is None
    assert page.h1 == []
    assert page.schema_types == []


HTML_GRAPH = """
<!doctype html>
<html lang="de">
<head>
  <title>Graph-Seite</title>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@graph": [
      {"@type": "Organization", "name": "BiancaAI"},
      {"@type": "WebSite", "name": "lovebianca.ai"},
      {"@type": "Person", "name": "Bianca"}
    ]
  }
  </script>
</head>
<body><h1>Hallo</h1></body>
</html>
"""


def test_parse_jsonld_graph_is_flattened():
    """@graph wrappers must be expanded so each entity's @type is visible.

    Regression: a single <script> with an @graph array previously surfaced as
    one block without @type, falsely flagged as "JSON-LD without @type".
    """
    page = PageData(url="https://x.test/", final_url="https://x.test/")
    _parse_html_into(page, HTML_GRAPH)
    types = {s.get("@type") for s in page.schema_data}
    assert types == {"Organization", "WebSite", "Person"}
    assert set(page.schema_types) == {"Organization", "WebSite", "Person"}
    # The bare @graph wrapper (no @type) must NOT leak through.
    assert all(s.get("@type") for s in page.schema_data)


def test_expand_graph_passthrough_without_graph():
    """Plain entity lists are returned unchanged."""
    entries = [{"@type": "Article", "headline": "x"}]
    assert _expand_jsonld_graph(entries) == entries


def test_expand_graph_keeps_outer_type():
    """A node carrying both @type and @graph yields the children AND itself."""
    entries = [{"@type": "WebPage", "@graph": [{"@type": "Organization"}]}]
    out = _expand_jsonld_graph(entries)
    types = [e.get("@type") for e in out]
    assert "Organization" in types
    assert "WebPage" in types
    # The outer node must no longer carry the @graph key.
    assert all("@graph" not in e for e in out)


def test_expand_graph_nested():
    """Nested @graph levels are flattened recursively."""
    entries = [{"@graph": [{"@graph": [{"@type": "Person", "name": "B"}]}]}]
    out = _expand_jsonld_graph(entries)
    assert out == [{"@type": "Person", "name": "B"}]


HTML_BOILERPLATE = """
<!doctype html>
<html lang="de">
<head><title>Seite A</title></head>
<body>
  <nav>Home Ueber Kontakt Impressum AGB Datenschutz Blog Shop Login Registrieren</nav>
  <main>
    <h1>Thema A</h1>
    <p>Ausfuehrlicher einzigartiger Hauptinhalt ueber das spezielle Thema A mit vielen besonderen Woertern.</p>
  </main>
  <footer>Impressum AGB Datenschutz Kontakt Copyright 2026 Alle Rechte vorbehalten</footer>
</body>
</html>
"""


def test_text_content_is_main_region_only():
    """text_content holds the <main> text but NOT nav/footer boilerplate."""
    page = PageData(url="https://x.test/a", final_url="https://x.test/a")
    _parse_html_into(page, HTML_BOILERPLATE)
    assert "Hauptinhalt ueber das spezielle Thema A" in page.text_content
    assert "Login" not in page.text_content  # nav stripped
    assert "Copyright" not in page.text_content  # footer stripped


def test_text_content_fallback_without_main():
    """Without <main>/<article>, fall back to body minus nav/header/footer/aside."""
    html = (
        "<html><body><nav>Menue Verlinkung</nav>"
        "<p>Reiner Textkoerper ohne eine Main Region direkt im Body.</p>"
        "<footer>Fusszeile Text</footer></body></html>"
    )
    page = PageData(url="https://x.test/b", final_url="https://x.test/b")
    _parse_html_into(page, html)
    assert "Reiner Textkoerper ohne eine Main Region" in page.text_content
    assert "Menue" not in page.text_content
    assert "Fusszeile" not in page.text_content
