"""
Configuration Management – Pydantic Settings + Environment Variables

All settings can be overridden via environment variables or a .env file.
"""

import os
from pathlib import Path
from typing import Optional, List
from pydantic_settings import BaseSettings
from pydantic import field_validator


# Detect a sensible default data directory
_HOME = Path.home()
_DEFAULT_DB = str(_HOME / ".seo-autopilot" / "seo_autopilot.db")
_DEFAULT_PROJECTS = str(Path.cwd() / "projects.yaml")
_DEFAULT_LOG = None  # No log file by default – just stdout


class Settings(BaseSettings):
    """Application settings – loaded from environment variables or .env file."""

    # Application
    APP_NAME: str = "SEO Autopilot"
    APP_VERSION: str = "0.3.0"
    DEBUG: bool = False

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8002
    API_SECRET_KEY: str = "change-me-in-production"
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]

    # Database – defaults to SQLite in user home dir
    DATABASE_URL: str = f"sqlite+aiosqlite:///{_DEFAULT_DB}"
    DB_ECHO: bool = False

    # AI APIs (optional)
    CLAUDE_API_KEY: Optional[str] = None
    CLAUDE_MODEL: str = "claude-opus-4-5"
    GEMINI_API_KEY: Optional[str] = None

    # Telegram notifications (optional)
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Data sources (optional)
    GSC_CREDENTIALS_PATH: Optional[str] = None
    PAGESPEED_API_KEY: Optional[str] = None
    AHREFS_API_KEY: Optional[str] = None
    SEMRUSH_API_KEY: Optional[str] = None

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = _DEFAULT_LOG

    # Scheduler
    SCHEDULER_TIMEZONE: str = "UTC"
    SCHEDULER_MAX_WORKERS: int = 4

    # Sentry (optional)
    SENTRY_DSN: Optional[str] = None

    # Project config file path
    PROJECT_CONFIG_PATH: str = _DEFAULT_PROJECTS

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


def _load_settings() -> Settings:
    """Load settings, gracefully ignoring missing or unreadable .env file."""
    try:
        return Settings()
    except Exception:
        # Fallback: ignore .env if unreadable
        return Settings(_env_file=None)


# Singleton
settings = _load_settings()
