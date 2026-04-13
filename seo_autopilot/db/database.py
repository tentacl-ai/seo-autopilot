"""
Database connection and session management

Supports:
- PostgreSQL (production)
- SQLite (development)
- Async sessions via SQLAlchemy 2.0
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator

from ..core.config import settings
from .models import Base

logger = logging.getLogger(__name__)


class Database:
    """Database connection manager"""

    def __init__(self):
        self.engine = None
        self.async_engine = None
        self.SessionLocal = None
        self.AsyncSessionLocal = None
        self._initialized = False

    async def initialize(self):
        """Initialize database connection and create tables"""
        if self._initialized:
            return

        logger.info(f"Initializing database: {settings.DATABASE_URL}")

        # Create async engine. SQLite/aiosqlite does not support pool_size/max_overflow.
        engine_kwargs = {"echo": settings.DB_ECHO, "future": True}
        if not settings.DATABASE_URL.startswith("sqlite"):
            engine_kwargs.update({
                "pool_size": 10,
                "max_overflow": 20,
                "pool_pre_ping": True,
            })
        self.async_engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

        self.AsyncSessionLocal = async_sessionmaker(
            self.async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        # Create tables if not exist
        async with self.async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self._initialized = True
        logger.info("✅ Database initialized")

    async def close(self):
        """Close database connections"""
        if self.async_engine:
            await self.async_engine.dispose()
            logger.info("Database closed")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get async database session (context manager)"""
        if not self._initialized:
            await self.initialize()

        async with self.AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Database session error: {e}")
                raise
            finally:
                await session.close()

    async def session(self) -> AsyncSession:
        """Get async database session (direct)"""
        if not self._initialized:
            await self.initialize()

        return self.AsyncSessionLocal()


# Global database instance
db = Database()
