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
    APP_VERSION: str = "0.5.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8002
    API_SECRET_KEY: str = os.getenv("API_SECRET_KEY", "change-me-in-production")
    CORS_ORIGINS: list = ["http://localhost:3000", "http://localhost:8000"]

    # Database
    # Default: SQLite in project dir. For Postgres use: postgresql+asyncpg://user:pass@host/db
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{_PROJECT_ROOT / 'seo_autopilot.db'}"
    )
    DB_ECHO: bool = DEBUG

    # AI APIs
    CLAUDE_API_KEY: Optional[str] = os.getenv("CLAUDE_API_KEY")
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

    # Telegram Notifications
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")

    # Data Sources
    GSC_CREDENTIALS_PATH: str = os.getenv(
        "GSC_CREDENTIALS_PATH",
        str(_PROJECT_ROOT / "credentials" / "service-account.json")
    )
    AHREFS_API_KEY: Optional[str] = os.getenv("AHREFS_API_KEY")
    SEMRUSH_API_KEY: Optional[str] = os.getenv("SEMRUSH_API_KEY")

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
        "PROJECT_CONFIG_PATH",
        str(_PROJECT_ROOT / "projects.yaml")
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# Singleton instance — graceful if .env is missing or unreadable
try:
    settings = Settings()
except Exception:
    settings = Settings(_env_file=None)
