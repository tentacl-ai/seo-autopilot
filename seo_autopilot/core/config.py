"""
Configuration Management – Pydantic Settings + Environment Variables

Multi-tenant ready: Jeder Tenant kann seine eigenen Credentials haben.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application Settings – Read from .env + Environment Variables"""

    # Application
    APP_NAME: str = "SEO Autopilot"
    APP_VERSION: str = "0.1.0"
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
        "sqlite+aiosqlite:////opt/odoo/docs/seo-autopilot/seo_autopilot.db"
    )
    DB_ECHO: bool = DEBUG  # Log SQL queries if debug

    # AI APIs
    CLAUDE_API_KEY: Optional[str] = os.getenv("CLAUDE_API_KEY")
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

    # Telegram Notifications
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")

    # Data Sources
    GSC_CREDENTIALS_PATH: str = "/opt/odoo/credentials/tentacl-seo-service-account.json"
    AHREFS_API_KEY: Optional[str] = os.getenv("AHREFS_API_KEY")
    SEMRUSH_API_KEY: Optional[str] = os.getenv("SEMRUSH_API_KEY")

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = os.getenv("LOG_FILE", "/var/log/seo-autopilot.log")

    # Scheduler
    SCHEDULER_TIMEZONE: str = "UTC"
    SCHEDULER_MAX_WORKERS: int = 4

    # Sentry (optional)
    SENTRY_DSN: Optional[str] = os.getenv("SENTRY_DSN")

    # Project Config
    PROJECT_CONFIG_PATH: str = os.getenv(
        "PROJECT_CONFIG_PATH",
        "/opt/odoo/docs/seo-autopilot/projects.yaml"
    )

    class Config:
        env_file = ".env"
        case_sensitive = True


# Singleton instance
settings = Settings()
