from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.api.auth import create_access_token, get_current_user, hash_password, require_admin, verify_password
from src.api.db import db_healthcheck, get_db, init_db_schema_and_seed
from src.api.settings import get_settings

openapi_tags = [
    {"name": "Health", "description": "Service and database health endpoints."},
    {"name": "Auth", "description": "User registration and login (JWT bearer tokens)."},
    {"name": "Lessons", "description": "Lesson retrieval and learner progress."},
    {"name": "Achievements", "description": "Achievement listing and user achievements."},
    {"name": "Pronunciation", "description": "Pronunciation feedback (initial stub)."},
    {"name": "Admin", "description": "Admin-only endpoints (role gated)."},
    {"name": "Realtime", "description": "WebSocket endpoint for real-time notifications."},
]

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=(
        "Linguaspeak backend API.\n\n"
        "WebSocket usage:\n"
        "- Connect to `GET /ws`\n"
        "- Optional auth: pass `?token=<JWT>` query param\n"
        "- Server sends JSON messages like `{type: 'notification', payload: {...}}`."
    ),
    version=settings.app_version,
    openapi_tags=openapi_tags,
)

allow_origins = ["*"] if settings.cors_allow_origins.strip() == "*" else [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    """Initialize DB schema/seed on app startup (idempotent)."""
    init_db_schema_and_seed()


# -----------------------------
# Models
# -----------------------------
class RegisterRequest(BaseModel):
    email: str = Field(..., description="User email (unique).")
    password: str = Field(..., min_length=6, description="User password (min 6 chars).")
    role: Optional[Literal["learner", "admin"]] = Field(None, description="Optional role. Admin should only be used in dev.")


class AuthResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token.")
    token_type: str = Field("bearer", description="Token type for Authorization header.")
    user: Dict[str, Any] = Field(..., description="User payload (id/email/role).")


class LoginRequest(BaseModel):
    email: str = Field(..., description="User email.")
    password: str = Field(..., description="User password.")


class Lesson(BaseModel):
    id: str = Field(..., description="Lesson id.")
    title: str = Field(..., description="Lesson title.")
    description: str = Field(..., description="Lesson description.")
    difficulty: str = Field(..., description="Difficulty label.")


class ProgressResponse(BaseModel):
    lesson_id: str = Field(..., description="Lesson id.")
    completion_percent: int = Field(..., ge=0, le=100, description="Completion percent for the lesson.")


class UpdateProgressRequest(BaseModel):
    completion_percent: int = Field(..., ge=0, le=100, description="New completion percent.")


class Achievement(BaseModel):
    id: str = Field(..., description="Achievement id.")
    title: str = Field(..., description="Title.")
    description: str = Field(..., description="Description.")
    icon: str = Field(..., description="Icon name.")


class PronunciationRequest(BaseModel):
    text: str = Field(..., description="User-provided text or transcript to evaluate.")
    locale: Optional[str] = Field("en-US", description="Locale of the utterance.")


class PronunciationResponse(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0, description="Pronunciation score (0-1).")
    feedback: str = Field(..., description="Human-readable feedback.")


# -----------------------------
# Health
# -----------------------------
@app.get("/", tags=["Health"], summary="Health check", description="Basic service health check.", operation_id="health_check")
def health_check() -> Dict[str, str]:
    """Return basic health info."""
    return {"message": "Healthy"}


@app.get(
    "/health/db",
    tags=["Health"],
    summary="Database health check",
    description="Checks if the API can connect to PostgreSQL.",
    operation_id="db_health_check",
)
def health_check_db() -> Dict[str, Any]:
    """Return DB connectivity status."""
    return db_healthcheck()


@app.get(
    "/docs/ws",
    tags=["Realtime"],
    summary="WebSocket usage help",
    description="Human-readable guidance for using the /ws endpoint.",
    operation_id="websocket_usage_help",
)
def websocket_usage_help() -> Dict[str, Any]:
    """Return WS usage info for API consumers."""
    return {
        "path": "/ws",
        "connect": "ws(s)://<host>/ws?token=<JWT optional>",
        "messages": [
            {"type": "welcome", "payload": {"message": "connected"}},
            {"type": "notification", "payload": {"kind": "achievement_awarded", "data": {"achievement_id": "first-lesson"}}},
        ],
    }


# -----------------------------
# Auth
# -----------------------------
@app.post(
    "/auth/register",
    tags=["Auth"],
    summary="Register",
    description="Register a new user and return a JWT bearer token.",
    response_model=AuthResponse,
    operation_id="auth_register",
)
def register(req: RegisterRequest) -> AuthResponse:
    """Register a new user."""
    db = get_db()
    password_hash = hash_password(req.password)
    role = req.role or "learner"
    with db.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s) RETURNING id, email, role",
                (req.email.lower(), password_hash, role),
            )
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists or invalid payload")
        user = cur.fetchone()

    token = create_access_token(sub=str(user["id"]), role=str(user["role"]))
    return AuthResponse(access_token=token, user={"id": user["id"], "email": user["email"], "role": user["role"]})


@app.post(
    "/auth/login",
    tags=["Auth"],
    summary="Login",
    description="Login and receive a JWT bearer token.",
    response_model=AuthResponse,
    operation_id="auth_login",
)
def login(req: LoginRequest) -> AuthResponse:
    """Login an existing user."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id, email, role, password_hash FROM users WHERE email = %s", (req.email.lower(),))
        row = cur.fetchone()
    if not row or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = create_access_token(sub=str(row["id"]), role=str(row["role"]))
    return AuthResponse(access_token=token, user={"id": row["id"], "email": row["email"], "role": row["role"]})


@app.get(
    "/me",
    tags=["Auth"],
    summary="Current user",
    description="Return the current user from the bearer token.",
    operation_id="auth_me",
)
def me(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Return current user."""
    return user


# -----------------------------
# Lessons & Progress
# -----------------------------
@app.get(
    "/lessons",
    tags=["Lessons"],
    summary="List lessons",
    description="List available lessons.",
    response_model=List[Lesson],
    operation_id="list_lessons",
)
def list_lessons() -> List[Lesson]:
    """Return lessons."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id, title, description, difficulty FROM lessons ORDER BY created_at ASC")
        rows = cur.fetchall()
    return [Lesson(**r) for r in rows]


@app.get(
    "/lessons/{lesson_id}",
    tags=["Lessons"],
    summary="Get lesson",
    description="Get a single lesson by id.",
    response_model=Lesson,
    operation_id="get_lesson",
)
def get_lesson(lesson_id: str) -> Lesson:
    """Return a lesson by id."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id, title, description, difficulty FROM lessons WHERE id = %s", (lesson_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found")
    return Lesson(**row)


@app.get(
    "/progress/{lesson_id}",
    tags=["Lessons"],
    summary="Get progress",
    description="Get the current user's progress for a lesson.",
    response_model=ProgressResponse,
    operation_id="get_lesson_progress",
)
def get_progress(lesson_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> ProgressResponse:
    """Return learner progress for a lesson."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT completion_percent FROM user_lesson_progress WHERE user_id=%s AND lesson_id=%s",
            (user["id"], lesson_id),
        )
        row = cur.fetchone()
    return ProgressResponse(lesson_id=lesson_id, completion_percent=int(row["completion_percent"]) if row else 0)


def _award_first_lesson_if_needed(user_id: int) -> Optional[str]:
    """Award 'first-lesson' achievement if user has any lesson >= 100% completion."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM user_achievements WHERE user_id=%s AND achievement_id='first-lesson'",
            (user_id,),
        )
        if cur.fetchone():
            return None
        cur.execute(
            "SELECT 1 FROM user_lesson_progress WHERE user_id=%s AND completion_percent >= 100 LIMIT 1",
            (user_id,),
        )
        if not cur.fetchone():
            return None
        cur.execute(
            "INSERT INTO user_achievements (user_id, achievement_id) VALUES (%s, 'first-lesson') ON CONFLICT DO NOTHING",
            (user_id,),
        )
        cur.execute(
            "INSERT INTO notifications (user_id, kind, payload) VALUES (%s, %s, %s::jsonb)",
            (user_id, "achievement_awarded", '{"achievement_id":"first-lesson"}'),
        )
    return "first-lesson"


@app.put(
    "/progress/{lesson_id}",
    tags=["Lessons"],
    summary="Update progress",
    description="Update the current user's progress for a lesson.",
    response_model=ProgressResponse,
    operation_id="update_lesson_progress",
)
def update_progress(
    lesson_id: str, req: UpdateProgressRequest, user: Dict[str, Any] = Depends(get_current_user)
) -> ProgressResponse:
    """Update learner progress; may award achievements and emit notifications."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_lesson_progress (user_id, lesson_id, completion_percent)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, lesson_id) DO UPDATE
              SET completion_percent = EXCLUDED.completion_percent,
                  updated_at = NOW()
            RETURNING completion_percent
            """,
            (user["id"], lesson_id, req.completion_percent),
        )
        row = cur.fetchone()

        # Also drop a generic progress notification (consumable via /ws polling behavior).
        cur.execute(
            "INSERT INTO notifications (user_id, kind, payload) VALUES (%s, %s, %s::jsonb)",
            (user["id"], "progress_updated", f'{{"lesson_id":"{lesson_id}","completion_percent":{int(row["completion_percent"])}}}'),
        )

    _award_first_lesson_if_needed(int(user["id"]))
    return ProgressResponse(lesson_id=lesson_id, completion_percent=int(row["completion_percent"]))


# -----------------------------
# Achievements
# -----------------------------
@app.get(
    "/achievements",
    tags=["Achievements"],
    summary="List achievements",
    description="List all achievements.",
    response_model=List[Achievement],
    operation_id="list_achievements",
)
def list_achievements() -> List[Achievement]:
    """Return achievement catalog."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id, title, description, icon FROM achievements ORDER BY id ASC")
        rows = cur.fetchall()
    return [Achievement(**r) for r in rows]


@app.get(
    "/me/achievements",
    tags=["Achievements"],
    summary="My achievements",
    description="List achievements awarded to the current user.",
    response_model=List[Achievement],
    operation_id="my_achievements",
)
def my_achievements(user: Dict[str, Any] = Depends(get_current_user)) -> List[Achievement]:
    """Return achievements for current user."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.title, a.description, a.icon
            FROM user_achievements ua
            JOIN achievements a ON a.id = ua.achievement_id
            WHERE ua.user_id = %s
            ORDER BY ua.awarded_at ASC
            """,
            (user["id"],),
        )
        rows = cur.fetchall()
    return [Achievement(**r) for r in rows]


# -----------------------------
# Pronunciation (stub)
# -----------------------------
@app.post(
    "/pronunciation/evaluate",
    tags=["Pronunciation"],
    summary="Evaluate pronunciation (stub)",
    description="Stub endpoint that returns a placeholder score and feedback.",
    response_model=PronunciationResponse,
    operation_id="pronunciation_evaluate",
)
def pronunciation_evaluate(req: PronunciationRequest, user: Dict[str, Any] = Depends(get_current_user)) -> PronunciationResponse:
    """Return placeholder pronunciation score/feedback; stores an analytics notification for demo."""
    # Simple heuristic stub: longer text => slightly higher score cap, but always stable.
    length = len(req.text.strip())
    score = min(1.0, max(0.2, 0.2 + (length / 120.0)))
    feedback = "Good clarity. Focus on vowel length and stress patterns." if score >= 0.6 else "Try speaking more slowly and clearly."
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO notifications (user_id, kind, payload) VALUES (%s, %s, %s::jsonb)",
            (user["id"], "pronunciation_feedback", f'{{"score":{score},"locale":"{req.locale}"}}'),
        )
    return PronunciationResponse(score=float(score), feedback=feedback)


# -----------------------------
# Admin (minimal)
# -----------------------------
@app.get(
    "/admin/lessons",
    tags=["Admin"],
    summary="Admin: list lessons",
    description="Admin-only list lessons (same as /lessons for now).",
    response_model=List[Lesson],
    operation_id="admin_list_lessons",
)
def admin_list_lessons(_admin: Dict[str, Any] = Depends(require_admin)) -> List[Lesson]:
    """Admin-only listing."""
    return list_lessons()


# -----------------------------
# Realtime WebSocket
# -----------------------------
class ConnectionManager:
    """Holds WebSocket connections per user id."""

    def __init__(self) -> None:
        self._connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        conns = self._connections.get(user_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns and user_id in self._connections:
            del self._connections[user_id]

    async def send(self, user_id: int, message: Dict[str, Any]) -> None:
        conns = list(self._connections.get(user_id, []))
        for ws in conns:
            await ws.send_json(message)


manager = ConnectionManager()


def _user_id_from_token(token: str) -> Optional[int]:
    """Best-effort token decode without raising."""
    try:
        from src.api.auth import decode_token

        payload = decode_token(token)
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except Exception:
        return None


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time notifications.

    Auth:
    - Optional query param `token=<JWT>`
    - If token is missing/invalid, the server accepts connection but only sends 'welcome' and rejects protected actions.
    """
    token = websocket.query_params.get("token", "")
    user_id = _user_id_from_token(token) if token else None

    # If not authed, accept connection but with user_id=0 bucket.
    bucket_user_id = user_id if user_id is not None else 0
    await manager.connect(bucket_user_id, websocket)
    await websocket.send_json({"type": "welcome", "payload": {"authenticated": user_id is not None}})

    try:
        while True:
            # Client may send pings or request to "pull" notifications.
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "pull_notifications":
                if user_id is None:
                    await websocket.send_json({"type": "error", "payload": {"detail": "Not authenticated"}})
                    continue
                db = get_db()
                with db.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, kind, payload, created_at
                        FROM notifications
                        WHERE user_id=%s AND read_at IS NULL
                        ORDER BY created_at ASC
                        LIMIT 25
                        """,
                        (user_id,),
                    )
                    rows = cur.fetchall()
                    # Mark as read
                    if rows:
                        ids = [int(r["id"]) for r in rows]
                        cur.execute("UPDATE notifications SET read_at = NOW() WHERE id = ANY(%s)", (ids,))
                await websocket.send_json({"type": "notification_batch", "payload": {"items": rows}})
                continue

            await websocket.send_json({"type": "error", "payload": {"detail": "Unknown message type"}})

    except WebSocketDisconnect:
        manager.disconnect(bucket_user_id, websocket)
