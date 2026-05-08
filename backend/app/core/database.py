"""
database.py — Async database engine and session management.

CONCEPT: SQLModel + SQLAlchemy async
--------------------------------------
SQLModel is a library built on top of SQLAlchemy (the most popular Python ORM)
and Pydantic (Python's data validation library). It lets you define ONE class
that works as both a database table AND an API schema — no duplication.

We use the *async* version of SQLAlchemy because FastAPI is async-native.
Async means the server doesn't "block" (freeze) while waiting for a DB query —
it handles other requests in the meantime. Critical for performance when we're
also waiting on 3 Swiggy MCP calls simultaneously.

HOW A REQUEST FLOWS THROUGH THIS FILE:
  1. FastAPI starts → lifespan() in main.py calls init_db()
  2. init_db() creates all tables in Postgres if they don't exist
  3. Every API endpoint that needs DB access gets a session injected
     via FastAPI's Depends(get_session) system
  4. The session is automatically closed after each request
"""

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from typing import AsyncGenerator
from app.core.config import settings


# create_async_engine: creates a connection pool to Postgres.
# A "pool" means we keep N connections open and reuse them across requests
# instead of opening a new connection for every single API call (expensive).
#
# echo=True in development prints every SQL query to the terminal —
# extremely useful for learning what SQLModel generates under the hood.
# Turned off in production to avoid log spam.
#
# pool_pre_ping=True: before using a connection from the pool, send a
# lightweight "ping" to check it's still alive. Prevents "connection closed"
# errors after the DB has been idle.
# Railway provides postgresql:// but asyncpg requires postgresql+asyncpg://
# Auto-convert to ensure the correct async driver is always used
db_url = settings.DATABASE_URL
if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    db_url,
    echo=settings.APP_ENV == "development",
    pool_pre_ping=True,
)


# sessionmaker: a factory that produces Session objects.
# Think of a Session as a "unit of work" — it tracks all the DB operations
# you do (insert, update, delete) and lets you commit or rollback together.
#
# class_=AsyncSession: use the async version of Session
# expire_on_commit=False: by default SQLAlchemy "expires" (clears) all
# object attributes after a commit, forcing a re-fetch. We turn this off
# because in async code that causes unexpected "MissingGreenlet" errors.
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """
    Import models so SQLModel.metadata is populated.

    We no longer call create_all() here — Alembic owns all schema
    management via migration files in alembic/versions/.

    To create or modify tables:
      alembic revision --autogenerate -m "description"
      alembic upgrade head

    We still import models here so that any code referencing
    SQLModel.metadata (e.g. tests) has the full schema available.
    """
    # Models must be imported so SQLModel.metadata knows about them
    from app.models.user import User  # noqa: F401
    from app.models.event import Event  # noqa: F401
    from app.models.plan import Plan  # noqa: F401


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a database session per request.

    CONCEPT: FastAPI Dependency Injection
    --------------------------------------
    FastAPI has a built-in DI system. Any function parameter annotated with
    Depends(get_session) will automatically receive a fresh session:

        @router.get("/events")
        async def list_events(session: AsyncSession = Depends(get_session)):
            ...

    The `async with` here is a context manager — it guarantees the session
    is always closed after the request, even if an exception is raised.
    This prevents connection leaks.

    The `yield` turns this into a "generator dependency" — FastAPI runs
    everything before yield to set up, injects the value, then runs
    everything after yield for cleanup (like a try/finally).
    """
    async with AsyncSessionLocal() as session:
        yield session
