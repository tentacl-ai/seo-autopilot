"""Places-Schluessel in der .env darf die Einstellungen nicht kippen (unbekannte Variablen = Fehler)."""

from seo_autopilot.core.config import Settings


def test_places_schluessel_in_env_wird_gelesen(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PAGESPEED_API_KEY=a\nGOOGLE_PLACES_API_KEY=b\n", encoding="utf-8")
    s = Settings(_env_file=str(env))
    assert s.PAGESPEED_API_KEY == "a" and s.GOOGLE_PLACES_API_KEY == "b"
