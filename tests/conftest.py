import asyncio
import os
import sys
from pathlib import Path

# Ensure project root on sys.path when running pytest from any cwd
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Use an isolated in-memory SQLite + empty projects file before anything imports settings
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PROJECT_CONFIG_PATH", str(ROOT / "tests" / "test_projects.yaml"))

import pytest


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_page_data():
    from seo_autopilot.sources.crawler import PageData

    return PageData(
        url="https://example.com/",
        status_code=200,
        final_url="https://example.com/",
        title="Example Home – KI Business Systems",
        meta_description="Willkommen bei Example – wir bauen KI-Systeme für moderne Unternehmen mit ERP, CRM und Automation.",
        h1=["Example Home"],
        word_count=400,
        viewport="width=device-width, initial-scale=1",
        lang="de",
        canonical="https://example.com/",
        images_total=3,
        images_without_alt=1,
        schema_types=["Organization"],
        og_tags={"og:title": "Example", "og:image": "https://example.com/og.png"},
        twitter_tags={"twitter:card": "summary_large_image"},
        security_headers={
            "strict-transport-security": "max-age=31536000",
            "x-frame-options": "SAMEORIGIN",
            "x-content-type-options": "nosniff",
        },
        https=True,
        fetch_ms=300,
    )
