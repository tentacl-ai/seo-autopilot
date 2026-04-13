"""Unit tests for the HTML parser inside crawler._parse_html_into."""

from seo_autopilot.sources.crawler import PageData, _parse_html_into

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
