"""
Configuration Management – Pydantic Settings + Environment Variables

All paths use sensible defaults relative to the project directory.
Override via environment variables or a .env file.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings

# Project root = directory containing this package
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Application Settings – Read from .env + Environment Variables"""

    # Application
    APP_NAME: str = "SEO Autopilot"
    APP_VERSION: str = "1.0.1"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8002
    API_SECRET_KEY: str = os.getenv("API_SECRET_KEY", "change-me-in-production")
    CORS_ORIGINS: list = ["http://localhost:3000", "http://localhost:8000"]

    # Database
    # Default: SQLite in project dir. For Postgres use: postgresql+asyncpg://user:pass@host/db
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{_PROJECT_ROOT / 'seo_autopilot.db'}"
    )
    DB_ECHO: bool = DEBUG

    # AI APIs
    # CLAUDE_API_KEY hat Vorrang; sonst der zentrale ANTHROPIC_API_KEY aus <eigene Zugangsdaten-Datei>
    CLAUDE_API_KEY: Optional[str] = os.getenv("CLAUDE_API_KEY") or os.getenv(
        "ANTHROPIC_API_KEY"
    )
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-opus-5")
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

    # Telegram Notifications
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")

    # Data Sources
    GSC_CREDENTIALS_PATH: str = os.getenv(
        "GSC_CREDENTIALS_PATH",
        str(_PROJECT_ROOT / "credentials" / "service-account.json"),
    )
    AHREFS_API_KEY: Optional[str] = os.getenv("AHREFS_API_KEY")
    SEMRUSH_API_KEY: Optional[str] = os.getenv("SEMRUSH_API_KEY")

    # Google PageSpeed Insights (Core Web Vitals).
    # OHNE Schluessel laeuft die Abfrage ueber ein gemeinsames Google-Projekt,
    # dessen Tageskontingent praktisch dauerhaft erschoepft ist — die API
    # antwortet dann mit HTTP 429 und der Autopilot bekommt NIE Core Web Vitals.
    # Genau das war am 2026-08-17 der Fall. Kostenloser Schluessel:
    # https://developers.google.com/speed/docs/insights/v5/get-started
    PAGESPEED_API_KEY: Optional[str] = os.getenv("PAGESPEED_API_KEY")

    # Google Places API (New) fuer Local SEO (Sterne, Bewertungen, Platz in Maps).
    # Eigener Schluessel im GCP-Projekt tentacl-seo, nur Places + nur Hub-IPs.
    # Muss hier stehen: die Settings lehnen unbekannte .env-Variablen ab, und dann
    # faellt die ganze .env weg (auch der PageSpeed-Schluessel).
    GOOGLE_PLACES_API_KEY: Optional[str] = os.getenv("GOOGLE_PLACES_API_KEY")

    # Einbindung in die eigene Umgebung (Werte gehoeren in die .env, nicht in den
    # Code — der Ordner ist oeffentlich auf GitHub). Leer = Funktion inaktiv.
    MAILER_PFAD: Optional[str] = os.getenv("SEO_MAILER_PFAD")
    MELDUNGS_EMPFAENGER: Optional[str] = os.getenv("SEO_MELDUNGS_EMPFAENGER")
    INDEXNOW_SITES: Optional[str] = os.getenv("SEO_INDEXNOW_SITES")
    BING_STATE: Optional[str] = os.getenv("SEO_BING_STATE")
    ENTSCHEIDUNGEN_ORDNER: Optional[str] = os.getenv("SEO_ENTSCHEIDUNGEN_ORDNER")
    CRON_ENV_DATEIEN: Optional[str] = os.getenv("SEO_CRON_ENV_DATEIEN")
    SECRETS_DATEI: Optional[str] = os.getenv("SEO_SECRETS_DATEI")

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = os.getenv("LOG_FILE")

    # Scheduler
    SCHEDULER_TIMEZONE: str = "UTC"
    SCHEDULER_MAX_WORKERS: int = 4

    # Sentry (optional)
    SENTRY_DSN: Optional[str] = os.getenv("SENTRY_DSN")

    # Project Config
    PROJECT_CONFIG_PATH: str = os.getenv(
        "PROJECT_CONFIG_PATH", str(_PROJECT_ROOT / "projects.yaml")
    )

    class Config:
        # Absolut: die Audit-Cronzeilen laufen ohne cd in den Projektordner —
        # mit ".env" relativ wurde die Datei dort nie gelesen (PageSpeed-Schluessel
        # fehlte deshalb in jedem Cronlauf, 18.09.2026).
        env_file = str(_PROJECT_ROOT / ".env")
        env_file_encoding = "utf-8"
        case_sensitive = True


# Singleton instance — graceful if .env is missing or unreadable
try:
    settings = Settings()
except Exception:
    settings = Settings(_env_file=None)
