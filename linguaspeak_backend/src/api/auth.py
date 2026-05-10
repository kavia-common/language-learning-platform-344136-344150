from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from src.api.db import get_db
from src.api.settings import get_settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# PUBLIC_INTERFACE
def hash_password(password: str) -> str:
    """Hash a plaintext password."""
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against a stored hash."""
    return _pwd.verify(password, password_hash)


# PUBLIC_INTERFACE
def create_access_token(*, sub: str, role: str) -> str:
    """Create a signed JWT access token."""
    s = get_settings()
    exp = _now() + timedelta(minutes=s.jwt_access_token_minutes)
    payload: Dict[str, Any] = {"sub": sub, "role": role, "exp": exp}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT token."""
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])


# PUBLIC_INTERFACE
def get_current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Dict[str, Any]:
    """Return the current user (dict with id/email/role) from bearer token."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(creds.credentials)
        sub = str(payload.get("sub", ""))
        if not sub:
            raise ValueError("Missing sub")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id, email, role FROM users WHERE id = %s", (int(sub),))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return {"id": row["id"], "email": row["email"], "role": row["role"]}


# PUBLIC_INTERFACE
def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Ensure the current user is an admin."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
