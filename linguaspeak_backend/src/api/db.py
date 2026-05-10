from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Dict, Iterator

import psycopg2
import psycopg2.extras

from src.api.settings import get_settings


@dataclass
class Db:
    """Small DB helper wrapping psycopg2 connections."""

    dsn: str

    @contextlib.contextmanager
    def conn(self) -> Iterator[psycopg2.extensions.connection]:
        """Context-managed connection."""
        connection = psycopg2.connect(self.dsn)
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def cursor(self) -> Iterator[psycopg2.extras.RealDictCursor]:
        """Context-managed cursor using RealDictCursor for dict row results."""
        with self.conn() as c:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                yield cur
                c.commit()


def _schema_sql() -> str:
    """
    Schema is intentionally minimal but aligned with plan + frontend needs.

    NOTE: Database container step already created tables; this keeps backend able to run on
    a fresh DB as well (idempotent).
    """
    return """
    CREATE TABLE IF NOT EXISTS users (
      id SERIAL PRIMARY KEY,
      email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'learner',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS lessons (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      difficulty TEXT NOT NULL DEFAULT 'Beginner',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS user_lesson_progress (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
      completion_percent INTEGER NOT NULL DEFAULT 0,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      PRIMARY KEY (user_id, lesson_id)
    );

    CREATE TABLE IF NOT EXISTS achievements (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      description TEXT NOT NULL,
      icon TEXT NOT NULL DEFAULT 'trophy'
    );

    CREATE TABLE IF NOT EXISTS user_achievements (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      achievement_id TEXT NOT NULL REFERENCES achievements(id) ON DELETE CASCADE,
      awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      PRIMARY KEY (user_id, achievement_id)
    );

    CREATE TABLE IF NOT EXISTS notifications (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      payload JSONB NOT NULL DEFAULT '{}'::jsonb,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      read_at TIMESTAMPTZ NULL
    );
    """


def _seed_sql() -> str:
    """Seed minimal content if DB is empty."""
    return """
    INSERT INTO lessons (id, title, description, difficulty)
    VALUES
      ('basics-1', 'Basics 1', 'Greetings, simple phrases.', 'Beginner'),
      ('food-1', 'Food 1', 'Common foods and ordering.', 'Beginner')
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO achievements (id, title, description, icon)
    VALUES
      ('first-lesson', 'First Lesson', 'Complete your first lesson.', 'sparkles'),
      ('streak-3', '3-day Streak', 'Practice 3 days in a row.', 'flame')
    ON CONFLICT (id) DO NOTHING;
    """


# PUBLIC_INTERFACE
def get_db() -> Db:
    """Create a Db instance from settings (DB URL derived from env or db_connection.txt)."""
    settings = get_settings()
    if not settings.postgres_url:
        raise RuntimeError(
            "POSTGRES_URL is not configured and could not be derived from db_connection.txt. "
            "Ensure database workspace exists and db_connection.txt is present."
        )
    return Db(dsn=settings.postgres_url)


# PUBLIC_INTERFACE
def init_db_schema_and_seed() -> None:
    """Initialize DB schema (idempotent) and apply minimal seed data."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(_schema_sql())
        cur.execute(_seed_sql())


# PUBLIC_INTERFACE
def db_healthcheck() -> Dict[str, Any]:
    """Perform a simple DB connectivity check."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT 1 AS ok;")
        row = cur.fetchone()
    return {"ok": bool(row and row.get("ok") == 1)}
