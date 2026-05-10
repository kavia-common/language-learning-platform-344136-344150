import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _read_db_url_from_db_connection_txt() -> Optional[str]:
    """
    Read the PostgreSQL URL from the repo's db_connection.txt (database container).

    The plan requires DB connectivity to use the connection info from db_connection.txt
    and to avoid hardcoding ports.
    """
    # The DB connection file lives in the sibling workspace: language-learning-platform-344136-344151
    # Backend workspace root: language-learning-platform-344136-344150/linguaspeak_backend
    # We resolve relative to this file's location to be stable across cwd changes.
    here = Path(__file__).resolve()
    backend_root = here.parents[2]  # .../linguaspeak_backend/src
    workspace_root = backend_root.parents[0]  # .../linguaspeak_backend
    # Go up to .../code-generation then into the database workspace.
    # This path is consistent in this mono-workspace layout.
    codegen_root = workspace_root.parents[1]

    candidate = codegen_root / "language-learning-platform-344136-344151" / "linguaspeak_database" / "db_connection.txt"
    if not candidate.exists():
        return None

    raw = candidate.read_text(encoding="utf-8").strip()
    # Expected format: `psql postgresql://user:pass@host:port/db`
    if "postgresql://" in raw:
        return raw.split("postgresql://", 1)[1].strip().join(["postgresql://"])  # type: ignore[attr-defined]
    return None


def _read_db_url_from_db_connection_txt_safe() -> Optional[str]:
    """Small wrapper to avoid any unexpected exceptions at import time."""
    try:
        # The join trick above is too clever; keep robust parsing here.
        here = Path(__file__).resolve()
        backend_root = here.parents[2]
        workspace_root = backend_root.parents[0]
        codegen_root = workspace_root.parents[1]
        candidate = codegen_root / "language-learning-platform-344136-344151" / "linguaspeak_database" / "db_connection.txt"
        if not candidate.exists():
            return None
        raw = candidate.read_text(encoding="utf-8").strip()
        idx = raw.find("postgresql://")
        if idx == -1:
            return None
        return raw[idx:].strip()
    except Exception:
        return None


@dataclass(frozen=True)
class Settings:
    """Application settings derived from environment variables (and db_connection.txt fallback)."""

    app_name: str = "Linguaspeak API"
    app_version: str = "0.1.0"

    # Auth
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-insecure-change-me")  # NOTE: set in .env for real deployments
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_access_token_minutes: int = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "240"))

    # Database
    postgres_url: str = os.getenv("POSTGRES_URL") or _read_db_url_from_db_connection_txt_safe() or ""

    # CORS
    cors_allow_origins: str = os.getenv("CORS_ALLOW_ORIGINS", "*")


# PUBLIC_INTERFACE
def get_settings() -> Settings:
    """Return a singleton-like settings object for the app runtime."""
    return Settings()
