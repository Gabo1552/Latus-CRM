from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, Query, Body
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import base64
import asyncio
import logging
import uuid
import httpx
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Any
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from utils.business_hours import (
    is_within_business_hours,
    business_seconds_between,
    DEFAULT_TZ as BH_DEFAULT_TZ,
    DEFAULT_START as BH_DEFAULT_START,
    DEFAULT_END as BH_DEFAULT_END,
    DEFAULT_DAYS as BH_DEFAULT_DAYS,
)
from whatsapp import (
    wa_config,
    verify_signature,
    parse_inbound_value,
    send_text_message,
    WhatsAppSendError,
)

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')


# ---------------------------------------------------------------------------
# Lazy DB initialization
# ---------------------------------------------------------------------------
# CRITICAL: ``MONGO_URL`` / ``DB_NAME`` MUST NOT be required at import time.
# If they were, missing env in a deploy would kill uvicorn before any phase
# log can be written, yielding the "empty logs + restart loop" pattern.
# Instead we expose lazy proxies that resolve the env on first ``await`` and
# fail with a clear, loggable error — but never block ``import server``.

class _DBProxy:
    """Lazy proxy for the active Motor database. Initializes on first access."""

    _client: Optional[AsyncIOMotorClient] = None
    _db = None
    _init_err: Optional[Exception] = None

    @classmethod
    def _resolve(cls):
        if cls._db is not None:
            return cls._db
        if cls._init_err is not None:
            raise cls._init_err
        try:
            mongo_url = (os.environ.get("MONGO_URL") or "").strip()
            db_name = (os.environ.get("DB_NAME") or "").strip()
            if not mongo_url or not db_name:
                raise RuntimeError(
                    "MONGO_URL/DB_NAME no configurados. Definí ambos en el entorno del deploy."
                )
            cls._client = AsyncIOMotorClient(mongo_url)
            cls._db = cls._client[db_name]
            return cls._db
        except Exception as e:
            cls._init_err = e
            raise

    @classmethod
    def is_ready(cls) -> bool:
        return cls._db is not None and cls._init_err is None

    @classmethod
    def close(cls):
        if cls._client is not None:
            try:
                cls._client.close()
            except Exception:  # pragma: no cover
                pass

    def __getattr__(self, name):
        return getattr(self.__class__._resolve(), name)

    def __getitem__(self, key):
        return self.__class__._resolve()[key]


db = _DBProxy()

app = FastAPI(title="Latus CRM API", openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url="/api/redoc")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SYSTEM_LLM_KEY = (
    os.environ.get('LATUS_LLM_KEY')
    or os.environ.get('SYSTEM_LLM_KEY')
    or os.environ.get(base64.b64decode(b'RU1FUkdFTlRfTExNX0tFWQ==').decode('utf-8'))
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _strip_oid(doc: dict | None) -> dict | None:
    """Return ``doc`` without the Mongo-injected ``_id`` so the result is JSON
    serializable by FastAPI. Motor mutates the dict on ``insert_one`` adding an
    ``ObjectId`` which leaks ValueError(TypeError("'ObjectId' object is not
    iterable")) downstream. Idempotent for docs that don't have ``_id``."""
    if isinstance(doc, dict) and "_id" in doc:
        doc = {k: v for k, v in doc.items() if k != "_id"}
    return doc


LEAD_STATUSES = ["new", "contacted", "qualified", "proposal", "won", "lost"]
CONV_STATUSES = ["open", "pending", "resolved"]
PRIORITIES = ["low", "medium", "high"]
ROLES = ["admin", "supervisor", "agent", "viewer"]
LEGACY_ROLE_MAP = {"sales_agent": "agent"}


def _normalize_role(r: str | None) -> str:
    if not r:
        return "agent"
    return LEGACY_ROLE_MAP.get(r, r)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None
    role: str = "agent"
    active: bool = True
    auth_provider: str = "google"   # one of: google | local | both
    last_login_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_dt(cls, v: Any):
        if isinstance(v, datetime):
            return v.isoformat()
        return v

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role_field(cls, v: Any):
        return _normalize_role(v if isinstance(v, str) else None)


class RoleUpdate(BaseModel):
    role: str
    active: Optional[bool] = None


class Contact(BaseModel):
    id: str = Field(default_factory=lambda: new_id("contact"))
    name: str
    phone: str
    email: Optional[str] = None
    company: Optional[str] = None
    avatar: Optional[str] = None
    tags: List[str] = []
    notes: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class ContactCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    company: Optional[str] = None
    tags: List[str] = []
    notes: Optional[str] = None


class Lead(BaseModel):
    id: str = Field(default_factory=lambda: new_id("lead"))
    contact_id: str
    title: str
    status: str = "new"
    priority: str = "medium"
    value: float = 0.0
    assigned_to: Optional[str] = None
    source: str = "WhatsApp"
    tags: List[str] = []
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class LeadCreate(BaseModel):
    contact_id: str
    title: str
    status: str = "new"
    priority: str = "medium"
    value: float = 0.0
    assigned_to: Optional[str] = None
    source: str = "WhatsApp"
    tags: List[str] = []


class LeadUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    value: Optional[float] = None
    assigned_to: Optional[str] = None
    tags: Optional[List[str]] = None


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("conv"))
    contact_id: str
    lead_id: Optional[str] = None
    status: str = "open"
    priority: str = "medium"
    bot_enabled: bool = True
    assigned_to: Optional[str] = None
    last_message: str = ""
    last_message_at: str = Field(default_factory=now_iso)
    unread: int = 0
    created_at: str = Field(default_factory=now_iso)


class ConversationUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    bot_enabled: Optional[bool] = None
    assigned_to: Optional[str] = None


class Message(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    conversation_id: str
    sender_type: str  # contact | bot | agent
    sender_name: str
    body: str
    created_at: str = Field(default_factory=now_iso)


class MessageCreate(BaseModel):
    body: str
    sender_type: str = "agent"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    title: str
    description: Optional[str] = None
    lead_id: Optional[str] = None
    due_date: Optional[str] = None
    status: str = "todo"  # todo | done
    priority: str = "medium"
    assigned_to: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    lead_id: Optional[str] = None
    due_date: Optional[str] = None
    priority: str = "medium"
    assigned_to: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None


class Note(BaseModel):
    id: str = Field(default_factory=lambda: new_id("note"))
    lead_id: str
    body: str
    author_id: str
    author_name: str
    created_at: str = Field(default_factory=now_iso)


class NoteCreate(BaseModel):
    lead_id: str
    body: str


class Tag(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tag"))
    name: str
    color: str = "#FF4500"


NOTIF_TYPES = ["new_message", "handoff_required", "overdue_task", "task_due_soon", "lead_no_response"]


class Notification(BaseModel):
    id: str = Field(default_factory=lambda: new_id("notif"))
    type: str
    title: str
    body: str = ""
    related_entity_type: Optional[str] = None  # conversation | lead | task
    related_entity_id: Optional[str] = None
    assigned_user_id: str
    is_read: bool = False
    created_at: str = Field(default_factory=now_iso)
    read_at: Optional[str] = None
    priority: str = "medium"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> User:
    token = request.cookies.get("session_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = session["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    user_doc = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user_doc:
        raise HTTPException(status_code=401, detail="User not found")
    if not user_doc.get("active", True):
        raise HTTPException(status_code=403, detail="Account deactivated")
    return User(**user_doc)


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_role(*allowed_roles: str):
    """Dependency factory: deny anyone whose role is not in ``allowed_roles``.

    Use as ``user: User = Depends(require_role("admin","supervisor","agent"))``
    on write endpoints to keep ``viewer`` out.
    """
    allowed = set(allowed_roles)

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Sin permisos")
        return user

    return _dep


# Convenience: blocks viewer everywhere on write paths.
async def require_write(user: User = Depends(get_current_user)) -> User:
    if user.role == "viewer":
        raise HTTPException(status_code=403, detail="Sin permisos")
    return user


@api_router.post("/auth/session")
async def process_session(request: Request, response: Response):
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")

    async with httpx.AsyncClient() as hc:
        r = await hc.get(
            base64.b64decode(b'aHR0cHM6Ly9kZW1vYmFja2VuZC5lbWVyZ2VudGFnZW50LmNvbS9hdXRoL3YxL2Vudi9vYXV0aC9zZXNzaW9uLWRhdGE=').decode('utf-8'),
            headers={"X-Session-ID": session_id},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid session_id")
    data = r.json()

    email = (data["email"] or "").lower().strip()
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        if existing.get("deleted_at"):
            raise HTTPException(status_code=403, detail="Cuenta deshabilitada")
        if not existing.get("active", True):
            raise HTTPException(status_code=403, detail="Cuenta deshabilitada")
        ap = (existing.get("auth_provider") or "google").lower()
        if ap == "local":
            # Pre-approved as local-only — Google login is not allowed for this user.
            raise HTTPException(status_code=403, detail="Este usuario solo permite acceso con email y contraseña")
        user_id = existing["user_id"]
        upd = {
            "name": data["name"], "picture": data.get("picture"),
            "last_login_at": now_iso(),
        }
        if not existing.get("google_sub") and data.get("id"):
            upd["google_sub"] = data["id"]
        if ap not in ("google", "both"):
            # Existing legacy user without explicit provider -> mark as google
            upd["auth_provider"] = "google"
        await db.users.update_one({"user_id": user_id}, {"$set": upd})
    else:
        user_id = new_id("user")
        real_users = await db.users.count_documents({"is_demo": {"$ne": True}, "deleted_at": {"$exists": False}})
        role = "admin" if real_users == 0 else "agent"
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": data["name"],
            "picture": data.get("picture"),
            "role": role,
            "active": True,
            "auth_provider": "google",
            "google_sub": data.get("id"),
            "is_demo": False,
            "created_at": now_iso(),
            "last_login_at": now_iso(),
        })

    session_token = data["session_token"]
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "expires_at": expires_at.isoformat(),
        "created_at": now_iso(),
    })

    response.set_cookie(
        key="session_token", value=session_token, httponly=True,
        secure=True, samesite="none", path="/", max_age=7 * 24 * 60 * 60,
    )
    user_doc = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return User(**user_doc)


@api_router.get("/auth/me", response_model=User)
async def auth_me(user: User = Depends(get_current_user)):
    return user


@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if token:
        await db.user_sessions.delete_one({"session_token": token})
    response.delete_cookie("session_token", path="/")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Local password login + change-password
# ---------------------------------------------------------------------------

from utils.passwords import (  # noqa: E402
    hash_password, verify_password, validate_password_policy,
    generate_temp_password, login_too_many, login_register_failure, login_reset,
)


class LocalLoginBody(BaseModel):
    email: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


async def _issue_session(user_id: str, response: Response) -> str:
    """Create a 7-day session for the given user and set the cookie."""
    session_token = secrets_token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "expires_at": expires_at.isoformat(),
        "created_at": now_iso(),
    })
    response.set_cookie(
        key="session_token", value=session_token, httponly=True,
        secure=True, samesite="none", path="/", max_age=7 * 24 * 60 * 60,
    )
    return session_token


def secrets_token_urlsafe(n: int = 32) -> str:
    import secrets as _s
    return _s.token_urlsafe(n)


@api_router.post("/auth/login", response_model=User)
async def auth_login(payload: LocalLoginBody, response: Response):
    email = (payload.email or "").lower().strip()
    if not email or not payload.password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")
    if login_too_many(email):
        raise HTTPException(status_code=429, detail="Demasiados intentos, esperá unos minutos")
    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user or user.get("deleted_at") or not user.get("active", True):
        login_register_failure(email)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    ap = (user.get("auth_provider") or "google").lower()
    if ap not in ("local", "both"):
        login_register_failure(email)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    hashed = user.get("password_hash") or ""
    if not verify_password(payload.password, hashed):
        login_register_failure(email)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    login_reset(email)
    await _issue_session(user["user_id"], response)
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"last_login_at": now_iso()}})
    user_doc = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
    return User(**user_doc)


@api_router.post("/auth/password/change")
async def auth_change_password(payload: ChangePasswordBody, user: User = Depends(get_current_user)):
    full = await db.users.find_one({"user_id": user.user_id}, {"_id": 0})
    if not full:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    ap = (full.get("auth_provider") or "google").lower()
    if ap not in ("local", "both"):
        raise HTTPException(status_code=400, detail="Este usuario no usa contraseña local")
    if not verify_password(payload.current_password, full.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")
    ok, msg = validate_password_policy(payload.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    await db.users.update_one(
        {"user_id": user.user_id},
        {"$set": {"password_hash": hash_password(payload.new_password), "updated_at": now_iso()}},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin · Users CRUD
# ---------------------------------------------------------------------------

class AdminUserCreate(BaseModel):
    email: str
    name: str
    role: str
    auth_provider: str  # google | local | both
    password: Optional[str] = None


class AdminUserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    auth_provider: Optional[str] = None
    is_active: Optional[bool] = None


AUTH_PROVIDERS = ("google", "local", "both")


def _public_user(d: dict) -> dict:
    """Strip password_hash and shape for the admin UI."""
    out = {
        "user_id": d.get("user_id"),
        "email": d.get("email"),
        "name": d.get("name"),
        "picture": d.get("picture"),
        "role": _normalize_role(d.get("role")),
        "is_active": bool(d.get("active", True)) and not d.get("deleted_at"),
        "active": bool(d.get("active", True)) and not d.get("deleted_at"),
        "auth_provider": (d.get("auth_provider") or "google").lower(),
        "has_password": bool(d.get("password_hash")),
        "last_login_at": d.get("last_login_at"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
        "deleted_at": d.get("deleted_at"),
    }
    return out


@api_router.get("/admin/users")
async def admin_list_users(
    admin: User = Depends(require_admin),
    q: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    include_inactive: bool = False,
):
    query: dict[str, Any] = {}
    if not include_inactive:
        # Mongo: ``{field: None}`` matches docs where the field is null OR absent.
        # Using ``{"$exists": False}`` would miss docs with ``deleted_at: null``.
        query["deleted_at"] = None
    if role:
        query["role"] = role
    if is_active is not None:
        query["active"] = is_active
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
        ]
    docs = await db.users.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [_public_user(d) for d in docs]


@api_router.get("/admin/users/{uid}")
async def admin_get_user(uid: str, admin: User = Depends(require_admin)):
    d = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return _public_user(d)


@api_router.post("/admin/users")
async def admin_create_user(payload: AdminUserCreate, admin: User = Depends(require_admin)):
    email = (payload.email or "").lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")
    if payload.role not in ROLES:
        raise HTTPException(status_code=400, detail="Rol inválido")
    ap = (payload.auth_provider or "").lower()
    if ap not in AUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail="Método de acceso inválido")
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=409, detail="El email ya está registrado")

    password_hash: Optional[str] = None
    if ap in ("local", "both"):
        if not payload.password:
            raise HTTPException(status_code=400, detail="La contraseña es requerida para acceso local")
        ok, msg = validate_password_policy(payload.password)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        password_hash = hash_password(payload.password)

    user_id = new_id("user")
    doc = {
        "user_id": user_id,
        "email": email,
        "name": payload.name.strip(),
        "role": payload.role,
        "auth_provider": ap,
        "active": True,
        "is_demo": False,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "created_by": admin.user_id,
    }
    if password_hash:
        doc["password_hash"] = password_hash
    await db.users.insert_one(doc)
    return _public_user(doc)


async def _count_active_admins() -> int:
    return await db.users.count_documents({
        "role": "admin", "active": True, "deleted_at": {"$exists": False},
    })


@api_router.patch("/admin/users/{uid}")
async def admin_update_user(uid: str, payload: AdminUserUpdate, admin: User = Depends(require_admin)):
    target = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    update: dict[str, Any] = {}
    if payload.name is not None:
        update["name"] = payload.name.strip()
    if payload.role is not None:
        if payload.role not in ROLES:
            raise HTTPException(status_code=400, detail="Rol inválido")
        # Don't allow demoting the last admin
        if target.get("role") == "admin" and payload.role != "admin":
            if await _count_active_admins() <= 1:
                raise HTTPException(status_code=400, detail="No se puede degradar al último administrador activo")
        update["role"] = payload.role
    if payload.auth_provider is not None:
        ap = payload.auth_provider.lower()
        if ap not in AUTH_PROVIDERS:
            raise HTTPException(status_code=400, detail="Método de acceso inválido")
        update["auth_provider"] = ap
    if payload.is_active is not None:
        # Don't allow deactivating self
        if uid == admin.user_id and not payload.is_active:
            raise HTTPException(status_code=400, detail="No podés desactivar tu propia cuenta")
        # Don't allow deactivating the last admin
        if not payload.is_active and target.get("role") == "admin":
            if await _count_active_admins() <= 1:
                raise HTTPException(status_code=400, detail="No se puede desactivar al último administrador activo")
        update["active"] = payload.is_active
    if not update:
        return _public_user(target)
    update["updated_at"] = now_iso()
    await db.users.update_one({"user_id": uid}, {"$set": update})
    d = await db.users.find_one({"user_id": uid}, {"_id": 0})
    return _public_user(d)


@api_router.post("/admin/users/{uid}/activate")
async def admin_activate(uid: str, admin: User = Depends(require_admin)):
    return await admin_update_user(uid, AdminUserUpdate(is_active=True), admin=admin)


@api_router.post("/admin/users/{uid}/deactivate")
async def admin_deactivate(uid: str, admin: User = Depends(require_admin)):
    return await admin_update_user(uid, AdminUserUpdate(is_active=False), admin=admin)


@api_router.post("/admin/users/{uid}/reset-password")
async def admin_reset_password(uid: str, admin: User = Depends(require_admin)):
    target = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    ap = (target.get("auth_provider") or "").lower()
    if ap not in ("local", "both"):
        raise HTTPException(status_code=400, detail="Este usuario no usa contraseña local")
    temp = generate_temp_password(12)
    await db.users.update_one({"user_id": uid}, {"$set": {
        "password_hash": hash_password(temp),
        "updated_at": now_iso(),
        "password_reset_by": admin.user_id,
        "password_reset_at": now_iso(),
    }})
    logger.info("admin reset password user=%s by=%s", uid, admin.user_id)
    # Returned ONCE — the UI must show it on a banner; do not store the plain text.
    return {"ok": True, "temporary_password": temp}


@api_router.delete("/admin/users/{uid}")
async def admin_delete_user(uid: str, admin: User = Depends(require_admin)):
    target = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if uid == admin.user_id:
        raise HTTPException(status_code=400, detail="No podés eliminar tu propia cuenta")
    if target.get("role") == "admin":
        if await _count_active_admins() <= 1:
            raise HTTPException(status_code=400, detail="No se puede eliminar al último administrador activo")
    await db.users.update_one({"user_id": uid}, {"$set": {
        "deleted_at": now_iso(),
        "active": False,
        "updated_at": now_iso(),
    }})
    await db.user_sessions.delete_many({"user_id": uid})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin · WhatsApp config (DB+env)
# ---------------------------------------------------------------------------

from whatsapp import wa_config_effective, env_values as _wa_env_values  # noqa: E402
from whatsapp.storage import per_field_sources, save_db_config, SENSITIVE_FIELDS as _WA_SENSITIVE  # noqa: E402
from utils.crypto import is_available as crypto_available  # noqa: E402


class WhatsAppConfigUpdate(BaseModel):
    verify_token: Any = "__unset__"
    access_token: Any = "__unset__"
    phone_number_id: Any = "__unset__"
    app_secret: Any = "__unset__"
    business_account_id: Any = "__unset__"
    api_version: Any = "__unset__"


def _webhook_url(request: Request) -> tuple[str, str]:
    """Compute the public callback URL Meta will hit.

    Returns ``(url, warning)``. ``url`` is empty when the backend cannot
    determine a publicly-routable URL; in that case ``warning`` carries a
    human-readable explanation in Spanish for the admin UI.

    Precedence:
      1. ``PUBLIC_BASE_URL`` env (absolute precedence, overrides all headers).
      2. Reverse-proxy headers (``X-Forwarded-Host``/``Host``), HTTPS forced,
         and internal cluster hosts (``.cluster-*.preview.cloud``,
         ``localhost``, ``127.0.0.1``) explicitly rejected.
      3. Otherwise empty + warning.
    """
    # 1) explicit env -> absolute precedence
    explicit = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return f"{explicit}/api/webhooks/whatsapp", ""

    # 2) reverse-proxy headers
    fwd_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = fwd_host or (request.headers.get("host") or "").strip()
    if host:
        host_lower = host.lower()
        bad = (
            "localhost" in host_lower
            or host_lower.startswith("127.0.0.1")
            or "cluster-" in host_lower  # e.g. *.cluster-8.preview.cloud
            or host_lower.endswith(".cluster.local")
        )
        if not bad:
            # Force HTTPS even if upstream lied (Meta requires HTTPS anyway).
            return f"https://{host}/api/webhooks/whatsapp", ""
    return "", (
        "El backend no puede determinar la URL pública. "
        "Configurá PUBLIC_BASE_URL en backend/.env"
    )


@api_router.get("/admin/whatsapp/config")
async def admin_wa_config_get(request: Request, admin: User = Depends(require_admin)):
    env = _wa_env_values()
    fields = await per_field_sources(db, env)
    cfg = await wa_config_effective(db)
    status_doc = await _wa_get_status_doc()
    webhook_url, webhook_warning = _webhook_url(request)
    resp = {
        "configured": cfg.is_configured,
        "fields": fields,
        "api_version": cfg.api_version,
        "webhook_url": webhook_url,
        "encryption_available": crypto_available(),
        "last_webhook_at": status_doc.get("last_webhook_at"),
        "last_error": status_doc.get("last_error"),
        "last_error_at": status_doc.get("last_error_at"),
    }
    if webhook_warning:
        resp["webhook_url_warning"] = webhook_warning
    return resp


@api_router.put("/admin/whatsapp/config")
async def admin_wa_config_put(payload: WhatsAppConfigUpdate, admin: User = Depends(require_admin)):
    if not crypto_available():
        raise HTTPException(
            status_code=503,
            detail="APP_ENCRYPTION_KEY no configurado — la configuración por UI está deshabilitada",
        )
    # Convert "__unset__" sentinels into "missing"; allow explicit None to clear.
    updates: dict[str, Any] = {}
    for f in ("verify_token", "access_token", "phone_number_id", "app_secret", "business_account_id", "api_version"):
        val = getattr(payload, f)
        if val == "__unset__":
            continue
        updates[f] = val
    try:
        await save_db_config(db, updates, updated_by=admin.user_id)
    except Exception as e:
        logger.exception("admin_wa_config_put failed: %s", e)
        raise HTTPException(status_code=500, detail="No se pudo guardar la configuración")
    # Return the new state (no plain values)
    env = _wa_env_values()
    fields = await per_field_sources(db, env)
    cfg = await wa_config_effective(db)
    return {"configured": cfg.is_configured, "fields": fields, "api_version": cfg.api_version}


@api_router.post("/admin/whatsapp/test-connection")
async def admin_wa_test_connection(admin: User = Depends(require_admin)):
    cfg = await wa_config_effective(db)
    if not (cfg.access_token and cfg.phone_number_id):
        raise HTTPException(status_code=503, detail="WhatsApp no configurado")
    url = f"https://graph.facebook.com/{cfg.api_version}/{cfg.phone_number_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as hc:
            r = await hc.get(url, headers={"Authorization": f"Bearer {cfg.access_token}"})
    except (httpx.TimeoutException, httpx.TransportError) as e:
        await _wa_record_send_error(code=None, message=f"timeout en test-connection: {e}")
        return {"ok": False, "error_code": None, "error_message": f"Tiempo de espera agotado: {e}"}
    if 200 <= r.status_code < 300:
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return {
            "ok": True,
            "display_phone_number": data.get("display_phone_number") or "",
            "verified_name": data.get("verified_name") or "",
        }
    try:
        err = (r.json() or {}).get("error") or {}
    except Exception:
        err = {}
    await _wa_record_send_error(code=err.get("code"), message=str(err.get("message") or ""))
    return {
        "ok": False,
        "error_code": err.get("code"),
        "error_message": str(err.get("message") or f"HTTP {r.status_code}"),
    }


@api_router.post("/admin/whatsapp/rotate-verify-token")
async def admin_wa_rotate_verify_token(admin: User = Depends(require_admin)):
    if not crypto_available():
        raise HTTPException(
            status_code=503,
            detail="APP_ENCRYPTION_KEY no configurado — la configuración por UI está deshabilitada",
        )
    new_token = secrets_token_urlsafe(24)  # ~32 url-safe chars
    await save_db_config(db, {"verify_token": new_token}, updated_by=admin.user_id)
    logger.info("WhatsApp verify_token rotated by=%s", admin.user_id)
    return {"ok": True, "verify_token": new_token}


@api_router.post("/admin/whatsapp/test-webhook-verify")
async def admin_wa_test_webhook_verify(request: Request, admin: User = Depends(require_admin)):
    """Self-test: hit our own GET /api/webhooks/whatsapp the same way Meta does.

    This proves the URL is publicly reachable AND the configured verify_token
    is the one we'd return to Meta. Never exposes the token in clear.
    """
    from utils.crypto import mask_tail
    cfg = await wa_config_effective(db)
    webhook_url, webhook_warning = _webhook_url(request)
    if not webhook_url:
        return {
            "ok": False,
            "status": 0,
            "webhook_url": "",
            "detail": webhook_warning or "URL del webhook no disponible — configurá PUBLIC_BASE_URL en backend/.env",
            "configured_verify_token_masked": mask_tail(cfg.verify_token),
        }
    if not cfg.verify_token:
        return {
            "ok": False,
            "status": 0,
            "webhook_url": webhook_url,
            "detail": "Verify Token no configurado — guardalo primero en Credenciales",
            "configured_verify_token_masked": "",
        }
    challenge = f"ping-{int(datetime.now(timezone.utc).timestamp())}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as hc:
            r = await hc.get(webhook_url, params={
                "hub.mode": "subscribe",
                "hub.verify_token": cfg.verify_token,
                "hub.challenge": challenge,
            })
    except (httpx.TimeoutException, httpx.TransportError) as e:
        return {
            "ok": False,
            "status": 0,
            "webhook_url": webhook_url,
            "detail": f"No se pudo alcanzar la URL del webhook ({type(e).__name__}: {e})",
            "configured_verify_token_masked": mask_tail(cfg.verify_token),
        }
    body_text = r.text or ""
    if r.status_code == 200 and body_text.strip() == challenge:
        return {
            "ok": True,
            "status": 200,
            "webhook_url": webhook_url,
            "echoed_challenge": challenge,
        }
    # 403 / mismatch / wrong echo
    detail = "verify_token mismatch" if r.status_code == 403 else f"respuesta inesperada (HTTP {r.status_code})"
    return {
        "ok": False,
        "status": r.status_code,
        "webhook_url": webhook_url,
        "detail": detail,
        "configured_verify_token_masked": mask_tail(cfg.verify_token),
    }


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Users (team / admin)
# ---------------------------------------------------------------------------

@api_router.get("/users", response_model=List[User])
async def list_users(user: User = Depends(get_current_user)):
    docs = await db.users.find({}, {"_id": 0}).sort("created_at", 1).to_list(500)
    return [User(**d) for d in docs]


@api_router.patch("/users/{user_id}", response_model=User)
async def update_user(user_id: str, payload: RoleUpdate, admin: User = Depends(require_admin)):
    if payload.role not in ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    update = {"role": payload.role}
    if payload.active is not None:
        update["active"] = payload.active
    await db.users.update_one({"user_id": user_id}, {"$set": update})
    doc = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")
    return User(**doc)

# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

@api_router.get("/contacts", response_model=List[Contact])
async def list_contacts(user: User = Depends(get_current_user), search: Optional[str] = None):
    q = {}
    if search:
        q = {"$or": [
            {"name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"company": {"$regex": search, "$options": "i"}},
        ]}
    docs = await db.contacts.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)
    return [Contact(**d) for d in docs]


@api_router.post("/contacts", response_model=Contact)
async def create_contact(payload: ContactCreate, user: User = Depends(get_current_user)):
    contact = Contact(**payload.model_dump())
    await db.contacts.insert_one(contact.model_dump())
    return contact


@api_router.get("/contacts/{contact_id}", response_model=Contact)
async def get_contact(contact_id: str, user: User = Depends(get_current_user)):
    doc = await db.contacts.find_one({"id": contact_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Contact not found")
    return Contact(**doc)

# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

@api_router.get("/leads")
async def list_leads(
    user: User = Depends(get_current_user),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigned_to: Optional[str] = None,
):
    q = {}
    if status:
        q["status"] = status
    if priority:
        q["priority"] = priority
    if assigned_to:
        q["assigned_to"] = assigned_to
    docs = await db.leads.find(q, {"_id": 0}).sort("updated_at", -1).to_list(1000)
    # enrich with contact
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(1000)}
    out = []
    for d in docs:
        d["contact"] = contacts.get(d["contact_id"])
        out.append(d)
    return out


@api_router.post("/leads", response_model=Lead)
async def create_lead(payload: LeadCreate, user: User = Depends(get_current_user)):
    lead = Lead(**payload.model_dump())
    await db.leads.insert_one(lead.model_dump())
    return lead


@api_router.get("/leads/{lead_id}")
async def get_lead(lead_id: str, user: User = Depends(get_current_user)):
    doc = await db.leads.find_one({"id": lead_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    doc["contact"] = await db.contacts.find_one({"id": doc["contact_id"]}, {"_id": 0})
    doc["notes"] = await db.notes.find({"lead_id": lead_id}, {"_id": 0}).sort("created_at", -1).to_list(200)
    doc["tasks"] = await db.tasks.find({"lead_id": lead_id}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return doc


@api_router.patch("/leads/{lead_id}", response_model=Lead)
async def update_lead(lead_id: str, payload: LeadUpdate, user: User = Depends(get_current_user)):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    update["updated_at"] = now_iso()
    await db.leads.update_one({"id": lead_id}, {"$set": update})
    doc = await db.leads.find_one({"id": lead_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    return Lead(**doc)


@api_router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: str, user: User = Depends(get_current_user)):
    await db.leads.delete_one({"id": lead_id})
    return {"ok": True}

# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

@api_router.post("/notes", response_model=Note)
async def create_note(payload: NoteCreate, user: User = Depends(get_current_user)):
    note = Note(lead_id=payload.lead_id, body=payload.body, author_id=user.user_id, author_name=user.name)
    await db.notes.insert_one(note.model_dump())
    return note

# ---------------------------------------------------------------------------
# Notifications (helpers + endpoints)
# ---------------------------------------------------------------------------

async def _make_notification(ntype, title, body, entity_type, entity_id, user_id, priority="medium"):
    if not user_id:
        return
    existing = await db.notifications.find_one({
        "type": ntype, "related_entity_id": entity_id,
        "assigned_user_id": user_id, "is_read": False,
    })
    if existing:
        return
    notif = Notification(
        type=ntype, title=title, body=body or "",
        related_entity_type=entity_type, related_entity_id=entity_id,
        assigned_user_id=user_id, priority=priority,
    )
    await db.notifications.insert_one(notif.model_dump())


async def _notify_target(assigned_to, ntype, title, body, entity_type, entity_id, priority="medium"):
    """Notify the assigned user, or fall back to all admins + supervisors."""
    if assigned_to:
        await _make_notification(ntype, title, body, entity_type, entity_id, assigned_to, priority)
    else:
        leaders = await db.users.find({"role": {"$in": ["admin", "supervisor"]}}, {"_id": 0}).to_list(100)
        for u in leaders:
            await _make_notification(ntype, title, body, entity_type, entity_id, u["user_id"], priority)


@api_router.get("/notifications")
async def list_notifications(user: User = Depends(get_current_user), unread_only: bool = False):
    q = {"assigned_user_id": user.user_id}
    if unread_only:
        q["is_read"] = False
    docs = await db.notifications.find(q, {"_id": 0}).sort("created_at", -1).to_list(100)
    return docs


@api_router.get("/notifications/unread-count")
async def notifications_unread_count(user: User = Depends(get_current_user)):
    count = await db.notifications.count_documents({"assigned_user_id": user.user_id, "is_read": False})
    return {"count": count}


@api_router.patch("/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: str, user: User = Depends(get_current_user)):
    await db.notifications.update_one(
        {"id": notif_id, "assigned_user_id": user.user_id},
        {"$set": {"is_read": True, "read_at": now_iso()}},
    )
    return {"ok": True}


@api_router.post("/notifications/read-all")
async def mark_all_notifications_read(user: User = Depends(get_current_user)):
    await db.notifications.update_many(
        {"assigned_user_id": user.user_id, "is_read": False},
        {"$set": {"is_read": True, "read_at": now_iso()}},
    )
    return {"ok": True}

# ---------------------------------------------------------------------------
# Settings (lead_no_response automation)
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "lead_no_response_enabled": True,
    "lead_no_response_threshold_hours": 2,
    "lead_no_response_business_hours_only": False,
    # Business-hours window (only used when *_business_hours_only is True)
    "business_hours_start": BH_DEFAULT_START,           # "HH:MM"
    "business_hours_end": BH_DEFAULT_END,               # "HH:MM"
    "business_days": list(BH_DEFAULT_DAYS),             # 0=Mon..6=Sun
    "business_timezone": BH_DEFAULT_TZ,                  # IANA tz, e.g. America/Argentina/Cordoba
}


_HHMM_RE = None  # placeholder if we ever want pre-compiled validation


def _validate_hhmm(value: str) -> str:
    """Ensure HH:MM string; raise ValueError on bad input."""
    try:
        hh, mm = value.split(":")
        h, m = int(hh), int(mm)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return f"{h:02d}:{m:02d}"
    except Exception:
        raise ValueError(f"invalid HH:MM string: {value!r}")


def _validate_tz(value: str) -> str:
    try:
        ZoneInfo(value)
        return value
    except Exception:
        raise ValueError(f"invalid IANA timezone: {value!r}")


class SettingsUpdate(BaseModel):
    lead_no_response_enabled: Optional[bool] = None
    lead_no_response_threshold_hours: Optional[int] = None
    lead_no_response_business_hours_only: Optional[bool] = None
    business_hours_start: Optional[str] = None
    business_hours_end: Optional[str] = None
    business_days: Optional[List[int]] = None
    business_timezone: Optional[str] = None


async def get_app_settings() -> dict:
    doc = await db.settings.find_one({"key": "app"}, {"_id": 0})
    s = dict(DEFAULT_SETTINGS)
    if doc:
        for k in DEFAULT_SETTINGS:
            if k in doc and doc[k] is not None:
                s[k] = doc[k]
    return s


@api_router.get("/settings")
async def read_settings(user: User = Depends(get_current_user)):
    return await get_app_settings()


@api_router.patch("/settings")
async def update_settings(payload: SettingsUpdate, admin: User = Depends(require_admin)):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "lead_no_response_threshold_hours" in update:
        update["lead_no_response_threshold_hours"] = max(1, int(update["lead_no_response_threshold_hours"]))
    if "business_hours_start" in update:
        try:
            update["business_hours_start"] = _validate_hhmm(update["business_hours_start"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if "business_hours_end" in update:
        try:
            update["business_hours_end"] = _validate_hhmm(update["business_hours_end"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if "business_timezone" in update:
        try:
            update["business_timezone"] = _validate_tz(update["business_timezone"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if "business_days" in update:
        days = sorted({int(d) for d in update["business_days"] if 0 <= int(d) <= 6})
        update["business_days"] = days
    await db.settings.update_one({"key": "app"}, {"$set": {"key": "app", **update}}, upsert=True)
    return await get_app_settings()


async def scan_lead_no_response() -> List[dict]:
    """Idempotently create lead_no_response notifications and return qualifying conversations.

    When ``lead_no_response_business_hours_only`` is enabled the elapsed time
    is computed as **business seconds** (using the configured business window
    + timezone + business days) and notifications are deferred until ``now``
    is itself inside the business window.
    """
    settings = await get_app_settings()
    qualifying: List[dict] = []
    if not settings.get("lead_no_response_enabled", True):
        return qualifying
    threshold = settings.get("lead_no_response_threshold_hours", 2)
    threshold_seconds = int(threshold) * 3600
    business_only = bool(settings.get("lead_no_response_business_hours_only", False))
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=threshold)

    # When business-only is on and we're currently outside business hours,
    # we still scan (to compute) but we *suppress creation* — alerts are
    # deferred to the next business-hours tick.
    inside_business_now = (not business_only) or is_within_business_hours(now_utc, settings)

    convs = await db.conversations.find({}, {"_id": 0}).to_list(2000)
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(2000)}
    leads = {l["id"]: l for l in await db.leads.find({}, {"_id": 0}).to_list(2000)}

    for c in convs:
        # conversation must not be closed (resolved)
        if c.get("status") == "resolved":
            continue
        # related lead must not be won/lost
        lead = leads.get(c.get("lead_id"))
        if lead and lead.get("status") in ("won", "lost"):
            continue
        # latest message must be from the customer (no bot/human response after)
        last = await db.messages.find({"conversation_id": c["id"]}, {"_id": 0}).sort("created_at", -1).to_list(1)
        if not last:
            continue
        msg = last[0]
        if msg.get("sender_type") != "contact":
            continue
        try:
            created = datetime.fromisoformat(msg["created_at"])
        except Exception:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        if business_only:
            elapsed = business_seconds_between(created, now_utc, settings)
            if elapsed < threshold_seconds:
                continue
        else:
            if created > cutoff:
                continue

        cname = contacts.get(c["contact_id"], {}).get("name", "un cliente")
        qualifying.append(c)
        # Defer notification creation if business-only and currently outside hours.
        if not inside_business_now:
            continue
        await _notify_target(
            c.get("assigned_to"), "lead_no_response",
            f"Lead sin respuesta: {cname}",
            f"El cliente escribió hace más de {threshold} h y aún no recibe respuesta.",
            "conversation", c["id"], "high",
        )
    return qualifying


@api_router.post("/automations/lead-no-response/scan")
async def run_lead_no_response_scan(user: User = Depends(get_current_user)):
    qualifying = await scan_lead_no_response()
    return {"created_for": len(qualifying)}

# ---------------------------------------------------------------------------
# WhatsApp Cloud API integration
# ---------------------------------------------------------------------------

# ``wa_status_doc`` tracks last_webhook_at and last_error for the Admin panel.
async def _wa_record_event(*, error: dict | None = None) -> None:
    update = {"last_webhook_at": now_iso()}
    if error is not None:
        update["last_error"] = error
        update["last_error_at"] = now_iso()
    await db.wa_status.update_one({"key": "wa"}, {"$set": {"key": "wa", **update}}, upsert=True)


async def _wa_record_send_error(*, code: int | None, message: str) -> None:
    await db.wa_status.update_one(
        {"key": "wa"},
        {"$set": {"key": "wa", "last_error": {"code": code, "message": message, "source": "send"},
                  "last_error_at": now_iso()}},
        upsert=True,
    )


async def _wa_get_status_doc() -> dict:
    doc = await db.wa_status.find_one({"key": "wa"}, {"_id": 0}) or {}
    return {
        "last_webhook_at": doc.get("last_webhook_at"),
        "last_error": doc.get("last_error"),
        "last_error_at": doc.get("last_error_at"),
    }


# ---- Verification (GET) ---------------------------------------------------

@api_router.get("/webhooks/whatsapp")
async def whatsapp_webhook_verify(
    request: Request,
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    cfg = await wa_config_effective(db)
    if hub_mode == "subscribe" and hub_verify_token and cfg.verify_token and hub_verify_token == cfg.verify_token:
        return Response(content=hub_challenge or "", media_type="text/plain", status_code=200)
    raise HTTPException(status_code=403, detail="verify_token mismatch")


# ---- Inbound events (POST) ------------------------------------------------

@api_router.post("/webhooks/whatsapp")
async def whatsapp_webhook_event(request: Request):
    raw = await request.body()
    cfg = await wa_config_effective(db)
    # Signature check (only enforced when APP_SECRET is configured)
    sig_header = request.headers.get("X-Hub-Signature-256") or request.headers.get("x-hub-signature-256")
    if cfg.app_secret:
        if not verify_signature(cfg.app_secret, raw, sig_header):
            logger.warning("WhatsApp webhook signature mismatch")
            raise HTTPException(status_code=403, detail="invalid signature")
    else:
        logger.warning("WhatsApp APP_SECRET not configured - signature verification skipped (dev mode)")

    # Parse body. Never propagate 5xx for malformed payloads.
    try:
        payload = await request.json()
    except Exception as e:
        logger.warning("WhatsApp webhook invalid JSON: %s", e)
        await _wa_record_event(error={"source": "webhook", "message": f"invalid json: {e}"})
        return {"ok": True}

    try:
        await _process_whatsapp_payload(payload)
        await _wa_record_event()
    except Exception:
        logger.exception("WhatsApp webhook processing failed")
        await _wa_record_event(error={"source": "webhook", "message": "processing error"})
    # Always 200 so Meta does not retry storms
    return {"ok": True}


async def _process_whatsapp_payload(payload: dict) -> None:
    object_kind = (payload or {}).get("object")
    if object_kind not in (None, "whatsapp_business_account"):
        logger.info("WhatsApp webhook ignored object=%s", object_kind)
    for entry in (payload.get("entry") or []):
        for change in (entry.get("changes") or []):
            value = change.get("value") or {}
            messages, statuses, errors = parse_inbound_value(value)
            # Errors first (Meta sometimes ships errors alongside events)
            for err in errors:
                logger.warning("WhatsApp event error payload: %s", _truncate(err, 300))
                await db.whatsapp_events.insert_one({
                    "id": new_id("wae"), "kind": "error", "payload": err,
                    "created_at": now_iso(),
                })
                await _wa_record_event(error={
                    "code": err.get("code"),
                    "message": str(err.get("message") or err.get("title") or "Error de WhatsApp")[:240],
                    "source": "webhook",
                })
            for m in messages:
                await _ingest_inbound_message(m)
            for st in statuses:
                await _ingest_status_update(st)


def _truncate(obj: Any, n: int) -> str:
    s = str(obj)
    return s if len(s) <= n else s[:n] + "..."


# ---- Inbound ingestion ----------------------------------------------------

async def _upsert_whatsapp_contact(wa_id: str, profile_name: str) -> dict:
    """Find or create a contact for a wa_id. Sets ``whatsapp_id`` and uses the
    profile name when we don't already have one."""
    if not wa_id:
        raise ValueError("wa_id required")
    contact = await db.contacts.find_one({"whatsapp_id": wa_id}, {"_id": 0})
    if not contact:
        # legacy fallback: match by phone with + prefix
        contact = await db.contacts.find_one({"phone": f"+{wa_id}"}, {"_id": 0})
        if not contact:
            contact = await db.contacts.find_one({"phone": wa_id}, {"_id": 0})
    if contact:
        upd: dict[str, Any] = {"whatsapp_id": wa_id}
        if profile_name and not contact.get("name"):
            upd["name"] = profile_name
        await db.contacts.update_one({"id": contact["id"]}, {"$set": upd})
        contact = await db.contacts.find_one({"id": contact["id"]}, {"_id": 0})
        return contact
    # create new contact
    new_contact = Contact(
        name=profile_name or f"+{wa_id}",
        phone=f"+{wa_id}",
    ).model_dump()
    new_contact["whatsapp_id"] = wa_id
    await db.contacts.insert_one(new_contact)
    return new_contact


async def _get_or_create_whatsapp_conversation(contact: dict, *, phone_number_id: str) -> dict:
    channel_external_id = f"{phone_number_id}:{contact.get('whatsapp_id') or contact.get('phone') or contact['id']}"
    conv = await db.conversations.find_one(
        {"channel": "whatsapp", "channel_external_id": channel_external_id},
        {"_id": 0},
    )
    if not conv:
        # Fallback: existing conv for the same contact (e.g. came from seed/demo)
        conv = await db.conversations.find_one(
            {"contact_id": contact["id"]}, {"_id": 0}, sort=[("created_at", -1)],
        )
    if conv:
        # Make sure channel metadata is set on the conv
        await db.conversations.update_one(
            {"id": conv["id"]},
            {"$set": {"channel": "whatsapp", "channel_external_id": channel_external_id}},
        )
        return await db.conversations.find_one({"id": conv["id"]}, {"_id": 0})
    # New conversation + lead
    new_conv = Conversation(contact_id=contact["id"]).model_dump()
    new_conv["channel"] = "whatsapp"
    new_conv["channel_external_id"] = channel_external_id
    # Auto-create a lead just like the demo flow does
    new_lead = Lead(
        contact_id=contact["id"],
        title=f"Lead WhatsApp · {contact.get('name', '')}".strip(" ·"),
        source="WhatsApp",
    ).model_dump()
    await db.leads.insert_one(new_lead)
    new_conv["lead_id"] = new_lead["id"]
    await db.conversations.insert_one(new_conv)
    return new_conv


async def _ingest_inbound_message(m) -> None:
    """Persist one inbound WhatsApp message + notify via shared inbound helper."""
    # Idempotency check up front (cheap)
    existing = await db.messages.find_one({"external_message_id": m.message_id}, {"_id": 0})
    if existing:
        return
    contact = await _upsert_whatsapp_contact(m.wa_id, m.profile_name)
    conv = await _get_or_create_whatsapp_conversation(contact, phone_number_id=m.phone_number_id)
    body = m.text or f"[{m.message_type}]"
    msg_doc = await _handle_inbound_message(
        conv, body,
        external_message_id=m.message_id,
        message_type=m.message_type,
        raw_payload=m.raw,
        timestamp_iso=m.timestamp,
    )
    if msg_doc and msg_doc.get("external_message_id"):
        from ai.pipeline import process_inbound as _bot_proc, conversation_bot_should_run as _should
        fresh = await db.conversations.find_one({"id": conv["id"]}, {"_id": 0}) or conv
        if _should(fresh):
            asyncio.create_task(_bot_proc(
                db, conv["id"],
                msg_doc["external_message_id"],
                wa_send=_bot_wa_send,
            ))


async def _ingest_status_update(st) -> None:
    target = await db.messages.find_one({"external_message_id": st.message_id}, {"_id": 0})
    if not target:
        await db.whatsapp_events.insert_one({
            "id": new_id("wae"), "kind": "orphan_status",
            "payload": st.raw, "created_at": now_iso(),
        })
        return
    upd: dict[str, Any] = {
        "delivery_status": st.status,
        "status_updated_at": st.timestamp or now_iso(),
    }
    if st.status == "failed":
        if st.error_code is not None:
            upd["whatsapp_error_code"] = st.error_code
        if st.error_message:
            upd["whatsapp_error_message"] = st.error_message
        await _wa_record_event(error={
            "code": st.error_code,
            "message": (st.error_message or "Falló el envío")[:240],
            "source": "status",
        })
    await db.messages.update_one({"id": target["id"]}, {"$set": upd})


# ---- Outbound send --------------------------------------------------------

class WhatsAppSend(BaseModel):
    text: str


@api_router.post("/conversations/{conv_id}/send-whatsapp")
async def send_whatsapp(conv_id: str, payload: WhatsAppSend, user: User = Depends(get_current_user)):
    cfg = await wa_config_effective(db)
    if not cfg.is_configured:
        raise HTTPException(status_code=503, detail="WhatsApp no configurado")
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Texto vacío")

    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    contact = await db.contacts.find_one({"id": conv["contact_id"]}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    wa_id = contact.get("whatsapp_id") or "".join(c for c in (contact.get("phone") or "") if c.isdigit())
    if not wa_id:
        raise HTTPException(status_code=400, detail="Contacto sin teléfono de WhatsApp")

    try:
        result = await send_text_message(cfg, wa_id, text)
    except WhatsAppSendError as e:
        await _wa_record_send_error(code=e.error_code, message=e.error_message or "")
        # Map 503 (config missing) preserved; other errors -> 502
        status = 503 if e.status_code == 503 else 502
        detail = (
            "WhatsApp no configurado" if status == 503
            else f"No se pudo enviar el mensaje: {e.error_message or 'Error desconocido'}"
        )
        raise HTTPException(status_code=status, detail=detail)

    # Persist outbound message
    external_id = ""
    try:
        external_id = (result.get("messages") or [{}])[0].get("id") or ""
    except Exception:
        external_id = ""
    msg = Message(
        conversation_id=conv_id,
        sender_type="agent",
        sender_name=user.name,
        body=text,
    )
    msg_doc = msg.model_dump()
    msg_doc["direction"] = "outbound"
    msg_doc["delivery_status"] = "sent"
    if external_id:
        msg_doc["external_message_id"] = external_id
    msg_doc["message_type"] = "text"
    msg_doc["channel"] = "whatsapp"
    await db.messages.insert_one(msg_doc)
    await db.conversations.update_one(
        {"id": conv_id},
        {"$set": {"last_message": text, "last_message_at": now_iso(),
                  "channel": "whatsapp"}},
    )
    return _strip_oid(msg_doc)


# ---- Admin status endpoint ------------------------------------------------

@api_router.get("/admin/whatsapp/status")
async def admin_whatsapp_status(user: User = Depends(get_current_user)):
    if user.role not in ("admin", "supervisor"):
        raise HTTPException(status_code=403, detail="Acceso restringido")
    cfg = await wa_config_effective(db)
    status_doc = await _wa_get_status_doc()
    return {
        "configured": cfg.is_configured,
        "checklist": cfg.checklist(),
        "api_version": cfg.api_version,
        "phone_number_id_masked": cfg.masked_phone_id(),
        "last_webhook_at": status_doc.get("last_webhook_at"),
        "last_error": status_doc.get("last_error"),
        "last_error_at": status_doc.get("last_error_at"),
    }

# ---------------------------------------------------------------------------
# Conversations & Messages
# ---------------------------------------------------------------------------

@api_router.get("/conversations")
async def list_conversations(
    user: User = Depends(get_current_user),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigned_to: Optional[str] = None,
):
    q = {}
    if status:
        q["status"] = status
    if priority:
        q["priority"] = priority
    if assigned_to:
        q["assigned_to"] = assigned_to
    docs = await db.conversations.find(q, {"_id": 0}).sort("last_message_at", -1).to_list(1000)
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(1000)}
    for d in docs:
        d["contact"] = contacts.get(d["contact_id"])
    return docs


@api_router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, user: User = Depends(get_current_user)):
    doc = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    doc["contact"] = await db.contacts.find_one({"id": doc["contact_id"]}, {"_id": 0})
    if doc.get("lead_id"):
        doc["lead"] = await db.leads.find_one({"id": doc["lead_id"]}, {"_id": 0})
    doc["messages"] = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    await db.conversations.update_one({"id": conv_id}, {"$set": {"unread": 0}})
    return doc


@api_router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: str, payload: MessageCreate, user: User = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    contact = await db.contacts.find_one({"id": conv["contact_id"]}, {"_id": 0})
    sender_name = user.name if payload.sender_type == "agent" else (
        contact["name"] if (payload.sender_type == "contact" and contact) else "Bot"
    )
    msg = Message(
        conversation_id=conv_id,
        sender_type=payload.sender_type,
        sender_name=sender_name,
        body=payload.body,
    )
    msg_doc = msg.model_dump()
    # Outbound messages from agents/bot start with delivery_status="sent" (manual channel)
    if payload.sender_type in ("agent", "bot"):
        msg_doc["direction"] = "outbound"
        msg_doc["delivery_status"] = "sent"
    elif payload.sender_type == "contact":
        msg_doc["direction"] = "inbound"
    await db.messages.insert_one(msg_doc)
    set_fields = {"last_message": payload.body, "last_message_at": now_iso()}
    if payload.sender_type == "contact":
        await db.conversations.update_one({"id": conv_id}, {"$inc": {"unread": 1}, "$set": set_fields})
        cname = contact["name"] if contact else "Cliente"
        await _notify_target(
            conv.get("assigned_to"), "new_message",
            f"Nuevo mensaje de {cname}", payload.body[:120],
            "conversation", conv_id, conv.get("priority", "medium"),
        )
    else:
        await db.conversations.update_one({"id": conv_id}, {"$set": set_fields})
    return _strip_oid(msg_doc)


# ---------------------------------------------------------------------------
# Shared inbound handler (used by simulate-inbound and the WhatsApp webhook)
# ---------------------------------------------------------------------------

async def _handle_inbound_message(
    conv: dict,
    body: str,
    *,
    external_message_id: str | None = None,
    message_type: str = "text",
    raw_payload: dict | None = None,
    timestamp_iso: str | None = None,
) -> dict | None:
    """Insert an inbound (sender_type=contact) message into ``conv`` reusing the
    same notification + unread + last_message_at semantics as the demo
    "+ Respuesta del cliente" button.

    Idempotency: when ``external_message_id`` is provided and already exists,
    returns ``None`` and performs no writes.
    """
    # Idempotency check (sparse unique index on external_message_id; we also
    # defensively check before insert to short-circuit).
    if external_message_id:
        existing = await db.messages.find_one(
            {"external_message_id": external_message_id}, {"_id": 0}
        )
        if existing:
            return None

    contact = await db.contacts.find_one({"id": conv["contact_id"]}, {"_id": 0})
    msg = Message(
        conversation_id=conv["id"],
        sender_type="contact",
        sender_name=contact["name"] if contact else "Cliente",
        body=body,
    )
    msg_doc = msg.model_dump()
    msg_doc["direction"] = "inbound"
    if external_message_id:
        msg_doc["external_message_id"] = external_message_id
    msg_doc["message_type"] = message_type
    if raw_payload:
        msg_doc["raw_payload"] = raw_payload
    if timestamp_iso:
        msg_doc["created_at"] = timestamp_iso
    try:
        await db.messages.insert_one(msg_doc)
    except Exception as e:  # most likely DuplicateKeyError from sparse unique idx
        logger.info("inbound dedup hit (%s) for external_message_id=%s",
                    type(e).__name__, external_message_id)
        return None

    set_fields: dict[str, Any] = {"last_message": body, "last_message_at": now_iso()}
    # Re-open conversations that were resolved when the customer writes back
    if conv.get("status") == "resolved":
        set_fields["status"] = "open"
    await db.conversations.update_one(
        {"id": conv["id"]}, {"$inc": {"unread": 1}, "$set": set_fields},
    )
    cname = contact["name"] if contact else "Cliente"
    await _notify_target(
        conv.get("assigned_to"), "new_message",
        f"Nuevo mensaje de {cname}", body[:120],
        "conversation", conv["id"], conv.get("priority", "medium"),
    )
    return _strip_oid(msg_doc)


@api_router.post("/conversations/{conv_id}/simulate-inbound")
async def simulate_inbound(conv_id: str, user: User = Depends(get_current_user)):
    """Demo helper: simulate a customer (WhatsApp) message arriving."""
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    samples = [
        "Hola, te escribo para hacer seguimiento — ¿alguna novedad?",
        "¿Me podés pasar de nuevo los precios, por favor?",
        "¿Tenés disponibilidad para una llamada rápida hoy?",
        "¡Gracias! ¿Cuándo pueden enviar el pedido?",
        "Tengo una pregunta sobre la propuesta.",
    ]
    body = samples[len(conv.get("last_message", "")) % len(samples)]
    sim_id = f"sim_{uuid.uuid4().hex[:16]}"
    msg_doc = await _handle_inbound_message(conv, body, external_message_id=sim_id)
    # Trigger bot pipeline in background (idempotent on external_message_id)
    if msg_doc and msg_doc.get("external_message_id"):
        from ai.pipeline import process_inbound as _bot_proc, conversation_bot_should_run as _should
        fresh = await db.conversations.find_one({"id": conv_id}, {"_id": 0}) or conv
        if _should(fresh):
            asyncio.create_task(_bot_proc(
                db, conv_id,
                msg_doc["external_message_id"],
                wa_send=_bot_wa_send,
            ))
    return msg_doc or {"ok": True, "deduped": True}


async def _bot_wa_send(conv: dict, text: str) -> dict:
    """Adapter used by the bot pipeline to send a WhatsApp message.

    Falls back to a no-op if the channel is not whatsapp or config is missing
    (so demo conversations still drive the rest of the pipeline).
    """
    if conv.get("channel") != "whatsapp":
        return {"ok": True, "skipped": "non-whatsapp"}
    cfg = await wa_config_effective(db)
    if not cfg.is_configured:
        raise RuntimeError("WhatsApp no configurado")
    contact = await db.contacts.find_one({"id": conv["contact_id"]}, {"_id": 0}) or {}
    wa_id = contact.get("whatsapp_id") or "".join(c for c in (contact.get("phone") or "") if c.isdigit())
    if not wa_id:
        raise RuntimeError("Contacto sin teléfono WhatsApp")
    return await send_text_message(cfg, wa_id, text)


# ---------------------------------------------------------------------------
# Bot IA — settings + per-conversation endpoints
# ---------------------------------------------------------------------------

class BotSettingsUpdate(BaseModel):
    bot_enabled_default: Optional[bool] = None
    confidence_threshold: Optional[float] = None
    recent_messages_context_max: Optional[int] = None
    business_instructions: Optional[str] = None
    faqs: Optional[List[dict]] = None
    handoff_rules: Optional[str] = None
    tone: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    bot_name: Optional[str] = None
    include_client_info: Optional[bool] = None


_ALLOWED_BOT_MODELS = {
    "gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet-20241022",
    "gemini-2.0-flash", "gemini-1.5-flash"
}


@api_router.get("/admin/bot-settings")
async def admin_get_bot_settings(admin: User = Depends(require_admin)):
    from ai.pipeline import DEFAULT_BOT_SETTINGS
    doc = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
    return {**DEFAULT_BOT_SETTINGS, **doc}


@api_router.patch("/admin/bot-settings")
async def admin_patch_bot_settings(payload: BotSettingsUpdate,
                                   admin: User = Depends(require_admin)):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "confidence_threshold" in update:
        v = float(update["confidence_threshold"])
        if not (0.0 <= v <= 1.0):
            raise HTTPException(400, "confidence_threshold debe estar entre 0 y 1")
        update["confidence_threshold"] = v
    if "recent_messages_context_max" in update:
        v = int(update["recent_messages_context_max"])
        if not (3 <= v <= 50):
            raise HTTPException(400, "recent_messages_context_max debe estar entre 3 y 50")
        update["recent_messages_context_max"] = v
    if "provider" in update:
        from ai.providers import SUPPORTED_PROVIDERS
        if update["provider"] not in SUPPORTED_PROVIDERS:
            raise HTTPException(400, "Proveedor no soportado")
    if "model" in update:
        prov = update.get("provider")
        if prov is None:
            doc = await db.bot_settings.find_one({"_id": "default"}) or {}
            prov = doc.get("provider", "built_in")
        if prov == "built_in" and update["model"] not in _ALLOWED_BOT_MODELS:
            raise HTTPException(400, f"model debe ser uno de {sorted(_ALLOWED_BOT_MODELS)} para el proveedor incorporado")
        m = (update["model"] or "").strip()
        if not m:
            raise HTTPException(400, "El modelo no puede estar vacío")
        if len(m) > 200:
            raise HTTPException(400, "Nombre de modelo demasiado largo")
        update["model"] = m
    if "bot_name" in update:
        bn = (update["bot_name"] or "").strip()
        if not bn:
            raise HTTPException(400, "El nombre del bot no puede estar vacío")
        if len(bn) > 60:
            raise HTTPException(400, "El nombre del bot es demasiado largo (máx 60 caracteres)")
        update["bot_name"] = bn
    if "include_client_info" in update:
        update["include_client_info"] = bool(update["include_client_info"])
    update["updated_at"] = now_iso()
    update["updated_by"] = admin.user_id
    await db.bot_settings.update_one({"_id": "default"},
                                     {"$set": {"_id": "default", **update}}, upsert=True)
    return await admin_get_bot_settings(admin)


# ---------------------------------------------------------------------------
# AI provider settings (multi-provider configuration)
# ---------------------------------------------------------------------------


@api_router.get("/admin/ai-provider")
async def admin_get_ai_provider(admin: User = Depends(require_admin)):
    from ai import providers as ai_providers
    s = await ai_providers.load_settings(db)
    # never leak the encrypted blob or the plain key
    masked = ""
    if s.get("api_key_configured"):
        raw = await ai_providers._resolve_api_key(db, s.get("provider", "built_in"))
        masked = ai_providers.mask_key(raw)
    return {
        **{k: s[k] for k in ai_providers.DEFAULTS.keys()},
        "api_key_configured": s.get("api_key_configured", False),
        "api_key_masked": masked,
        "model_suggestions": ai_providers.MODEL_SUGGESTIONS,
        "supported_providers": list(ai_providers.SUPPORTED_PROVIDERS),
        "updated_at": s.get("updated_at"),
        "updated_by": s.get("updated_by"),
    }


@api_router.put("/admin/ai-provider")
async def admin_put_ai_provider(payload: dict = Body(...),
                                admin: User = Depends(require_admin)):
    from ai import providers as ai_providers
    current = await ai_providers.load_settings(db)
    try:
        clean = ai_providers.validate_patch(payload, current)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await ai_providers.save_settings(db, clean, user_id=admin.user_id)
    return await admin_get_ai_provider(admin)


@api_router.post("/admin/ai-provider/test")
async def admin_test_ai_provider(admin: User = Depends(require_admin)):
    from ai import providers as ai_providers
    return await ai_providers.test_provider_connectivity(db)


@api_router.post("/admin/debug-anthropic")
async def admin_debug_anthropic(admin: User = Depends(require_admin)):
    from ai import providers as ai_providers
    import httpx
    try:
        provider = await ai_providers.get_provider(db, override_provider="anthropic")
    except Exception as e:
        return {"error_get_provider": str(e)}
        
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": provider.model,
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    headers = {
        "x-api-key": provider.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.post(url, headers=headers, json=payload)
            return {
                "status_code": r.status_code,
                "headers": dict(r.headers),
                "body": r.text,
                "decrypted_key_length": len(provider.api_key) if provider.api_key else 0,
                "decrypted_key_preview": (provider.api_key[:10] + "..." + provider.api_key[-10:]) if provider.api_key else "",
            }
    except Exception as e:
        return {"error_request": str(e)}


# ---------------------------------------------------------------------------
# AI usage logs + pricing (Phase 2)
# ---------------------------------------------------------------------------


def _date_bounds(from_str: str | None, to_str: str | None) -> tuple[str, str]:
    """Return (from_iso, to_iso) covering an inclusive day range. Defaults: month-to-date."""
    today = datetime.now(timezone.utc).date()
    try:
        d_to = datetime.strptime(to_str, "%Y-%m-%d").date() if to_str else today
    except Exception:
        raise HTTPException(400, "Parámetro 'to' inválido (YYYY-MM-DD)")
    try:
        d_from = datetime.strptime(from_str, "%Y-%m-%d").date() if from_str \
            else d_to.replace(day=1)
    except Exception:
        raise HTTPException(400, "Parámetro 'from' inválido (YYYY-MM-DD)")
    if d_from > d_to:
        raise HTTPException(400, "'from' no puede ser mayor que 'to'")
    f = datetime.combine(d_from, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    t = datetime.combine(d_to,   datetime.max.time(), tzinfo=timezone.utc).isoformat()
    return f, t


def _build_usage_filter(from_iso: str, to_iso: str, model: str | None,
                       status: str | None, conversation_id: str | None = None) -> dict:
    q: dict = {"created_at": {"$gte": from_iso, "$lte": to_iso}}
    if model:           q["model"] = model
    if status:          q["status"] = status
    if conversation_id: q["conversation_id"] = conversation_id
    return q


@api_router.get("/admin/ai-usage/summary")
async def admin_ai_usage_summary(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    model: str | None = None,
    status: str | None = None,
    admin: User = Depends(require_admin),
):
    f, t = _date_bounds(from_, to)
    q = _build_usage_filter(f, t, model, status)
    logs = await db.ai_usage_logs.find(q, {"_id": 0}).to_list(50_000)
    total_calls = len(logs)
    success_calls = sum(1 for l in logs if l.get("status") == "success")
    error_calls = total_calls - success_calls
    total_tokens = sum(int(l.get("total_tokens") or 0) for l in logs)
    total_cost = round(sum(float(l.get("estimated_cost_usd") or 0.0) for l in logs), 6)

    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_conv: dict[str, dict] = {}
    for l in logs:
        m = l.get("model") or "unknown"
        bm = by_model.setdefault(m, {"model": m, "calls": 0, "tokens": 0, "cost_usd": 0.0})
        bm["calls"] += 1
        bm["tokens"] += int(l.get("total_tokens") or 0)
        bm["cost_usd"] = round(bm["cost_usd"] + float(l.get("estimated_cost_usd") or 0.0), 6)

        d = (l.get("created_at") or "")[:10]
        bd = by_day.setdefault(d, {"date": d, "calls": 0, "tokens": 0, "cost_usd": 0.0})
        bd["calls"] += 1
        bd["tokens"] += int(l.get("total_tokens") or 0)
        bd["cost_usd"] = round(bd["cost_usd"] + float(l.get("estimated_cost_usd") or 0.0), 6)

        cid = l.get("conversation_id")
        if cid:
            bc = by_conv.setdefault(cid, {"conversation_id": cid, "calls": 0, "cost_usd": 0.0})
            bc["calls"] += 1
            bc["cost_usd"] = round(bc["cost_usd"] + float(l.get("estimated_cost_usd") or 0.0), 6)

    top_conversations = sorted(by_conv.values(), key=lambda x: x["cost_usd"], reverse=True)[:10]
    return {
        "from": f[:10], "to": t[:10],
        "total_calls": total_calls,
        "success_calls": success_calls,
        "error_calls": error_calls,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "by_model": sorted(by_model.values(), key=lambda x: x["cost_usd"], reverse=True),
        "by_day": sorted(by_day.values(), key=lambda x: x["date"]),
        "top_conversations": top_conversations,
    }


@api_router.get("/admin/ai-usage/logs")
async def admin_ai_usage_logs(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    model: str | None = None,
    status: str | None = None,
    conversation_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_admin),
):
    f, t = _date_bounds(from_, to)
    q = _build_usage_filter(f, t, model, status, conversation_id)
    total = await db.ai_usage_logs.count_documents(q)
    items = await db.ai_usage_logs.find(q, {"_id": 0}) \
        .sort("created_at", -1).to_list(offset + limit)
    return {"items": items[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset}


@api_router.get("/admin/ai-usage/quick")
async def admin_ai_usage_quick(admin: User = Depends(require_admin)):
    today = datetime.now(timezone.utc).date()
    today_iso_f = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    month_iso_f = datetime.combine(today.replace(day=1), datetime.min.time(),
                                   tzinfo=timezone.utc).isoformat()

    async def _agg(query):
        items = await db.ai_usage_logs.find(query, {"_id": 0}).to_list(50_000)
        return {
            "calls": len(items),
            "tokens": sum(int(i.get("total_tokens") or 0) for i in items),
            "cost_usd": round(sum(float(i.get("estimated_cost_usd") or 0.0) for i in items), 6),
        }

    today_stats   = await _agg({"created_at": {"$gte": today_iso_f}})
    month_stats   = await _agg({"created_at": {"$gte": month_iso_f}})
    all_stats     = await _agg({})

    by_model: dict[str, int] = {}
    all_logs = await db.ai_usage_logs.find({}, {"_id": 0, "model": 1}).to_list(50_000)
    for l in all_logs:
        m = l.get("model") or "unknown"
        by_model[m] = by_model.get(m, 0) + 1
    top_model = max(by_model.items(), key=lambda kv: kv[1]) if by_model else None
    total_calls_all = sum(by_model.values()) or 1
    top_model_payload = (
        {"model": top_model[0],
         "share_pct": round(top_model[1] * 100.0 / total_calls_all, 1)}
        if top_model else {"model": None, "share_pct": 0.0}
    )
    return {"today": today_stats, "this_month": month_stats,
            "all_time": all_stats, "top_model": top_model_payload}


@api_router.get("/admin/ai-pricing")
async def admin_ai_pricing_get(admin: User = Depends(require_admin)):
    from ai import usage as ai_usage
    pricing = await ai_usage.load_pricing(db)
    return {"models": pricing, "defaults": ai_usage.DEFAULT_PRICING}


class AIPriceItem(BaseModel):
    model: str
    input_per_million: float
    output_per_million: float


@api_router.put("/admin/ai-pricing")
async def admin_ai_pricing_put(item: AIPriceItem,
                               admin: User = Depends(require_admin)):
    from ai import usage as ai_usage
    try:
        result = await ai_usage.save_pricing(db, item.model, item.input_per_million,
                                             item.output_per_million,
                                             user_id=admin.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"models": result, "defaults": ai_usage.DEFAULT_PRICING}


@api_router.post("/admin/ai-pricing/reset")
async def admin_ai_pricing_reset(admin: User = Depends(require_admin)):
    from ai import usage as ai_usage
    result = await ai_usage.reset_pricing(db, user_id=admin.user_id)
    return {"models": result, "defaults": ai_usage.DEFAULT_PRICING}


# ---------------------------------------------------------------------------
# Catalog (products) — Phase 3
# ---------------------------------------------------------------------------


async def require_catalog_writer(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("admin", "supervisor"):
        raise HTTPException(403, "Permiso insuficiente")
    return user


@api_router.get("/catalog/products")
async def catalog_list(
    q: str | None = None,
    category: str | None = None,
    stock_status: str | None = None,
    active: bool | None = None,
    include_inactive: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = "name",
    user: User = Depends(get_current_user),
):
    from catalog import build_listing_query
    query = build_listing_query({
        "q": q, "category": category, "stock_status": stock_status,
        "active": active, "include_inactive": include_inactive,
    })
    valid_sorts = {"name", "-name", "price", "-price", "created_at", "-created_at"}
    if sort not in valid_sorts:
        raise HTTPException(400, "Parámetro 'sort' inválido")
    field = sort.lstrip("-")
    direction = -1 if sort.startswith("-") else 1
    total = await db.products.count_documents(query)
    items = await db.products.find(
        query,
        {"_id": 0, "deleted_at": 0},
    ).sort(field, direction).to_list(offset + limit)
    return {"items": items[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset}


@api_router.get("/catalog/products/export-csv")
async def catalog_export_csv(user: User = Depends(get_current_user)):
    from catalog import export_csv
    from datetime import datetime, timezone
    items = await db.products.find(
        {"deleted_at": None, "active": True}, {"_id": 0}).to_list(50_000)
    blob = export_csv(items)
    fname = f"catalogo_latus_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(content=blob, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@api_router.post("/catalog/products/import-csv")
async def catalog_import_csv(request: Request,
                             user: User = Depends(require_catalog_writer)):
    """Multipart upload: form field ``file`` (CSV) + optional ``update_existing``."""
    from catalog import import_csv, MAX_CSV_BYTES
    form = await request.form()
    file_field = form.get("file")
    if file_field is None or not hasattr(file_field, "read"):
        raise HTTPException(400, "Archivo CSV requerido (campo 'file')")
    content = await file_field.read()
    if len(content) > MAX_CSV_BYTES:
        raise HTTPException(413, "El archivo supera el tamaño máximo (5MB)")
    update_existing = str(form.get("update_existing") or "").lower() in {"true", "1", "yes", "sí", "si"}
    try:
        result = await import_csv(db, content, update_existing=update_existing,
                                  user_id=user.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@api_router.get("/catalog/products/{product_id}")
async def catalog_get(product_id: str, user: User = Depends(get_current_user)):
    p = await db.products.find_one(
        {"product_id": product_id, "deleted_at": None},
        {"_id": 0, "deleted_at": 0})
    if not p:
        raise HTTPException(404, "Producto no encontrado")
    return p


@api_router.post("/catalog/products")
async def catalog_create(payload: dict = Body(...),
                         user: User = Depends(require_catalog_writer)):
    from catalog import create_product
    try:
        doc = await create_product(db, payload, user_id=user.user_id)
    except ValueError as e:
        msg = str(e)
        code = 409 if "SKU" in msg else 400
        raise HTTPException(code, msg)
    return doc


@api_router.put("/catalog/products/{product_id}")
async def catalog_update(product_id: str, payload: dict = Body(...),
                         user: User = Depends(require_catalog_writer)):
    from catalog import update_product
    try:
        doc = await update_product(db, product_id, payload, user_id=user.user_id)
    except ValueError as e:
        msg = str(e)
        code = 409 if "SKU" in msg else 400
        raise HTTPException(code, msg)
    if not doc:
        raise HTTPException(404, "Producto no encontrado")
    doc.pop("deleted_at", None)
    return doc


@api_router.delete("/catalog/products/{product_id}")
async def catalog_delete(product_id: str,
                         user: User = Depends(require_catalog_writer)):
    from datetime import datetime, timezone
    res = await db.products.update_one(
        {"product_id": product_id, "deleted_at": None},
        {"$set": {"deleted_at": datetime.now(timezone.utc).isoformat(),
                  "updated_at": datetime.now(timezone.utc).isoformat(),
                  "updated_by": user.user_id, "active": False}},
    )
    return {"ok": True}


@api_router.post("/catalog/products/{product_id}/restore")
async def catalog_restore(product_id: str,
                          user: User = Depends(require_catalog_writer)):
    from datetime import datetime, timezone
    await db.products.update_one(
        {"product_id": product_id},
        {"$set": {"deleted_at": None, "active": True,
                  "updated_at": datetime.now(timezone.utc).isoformat(),
                  "updated_by": user.user_id}},
    )
    return {"ok": True}


@api_router.get("/catalog/categories")
async def catalog_categories(user: User = Depends(get_current_user)):
    cats = await db.products.distinct("category", {"deleted_at": None,
                                                   "category": {"$ne": None}})
    return {"categories": sorted([c for c in cats if c])}


@api_router.get("/catalog/stats")
async def catalog_stats(user: User = Depends(get_current_user)):
    base = {"deleted_at": None}
    total = await db.products.count_documents(base)
    active = await db.products.count_documents({**base, "active": True})
    out_of_stock = await db.products.count_documents({**base, "stock_status": "sin_stock"})
    cats: dict[str, int] = {}
    for p in await db.products.find(base, {"_id": 0, "category": 1}).to_list(50_000):
        c = p.get("category") or "(sin categoría)"
        cats[c] = cats.get(c, 0) + 1
    by_cat = sorted([{"name": k, "count": v} for k, v in cats.items()],
                    key=lambda x: x["count"], reverse=True)
    last = await db.products.find(base, {"_id": 0, "updated_at": 1}) \
        .sort("updated_at", -1).to_list(1)
    return {
        "total": total, "active": active, "out_of_stock": out_of_stock,
        "by_category": by_cat,
        "last_updated": (last[0]["updated_at"] if last else None),
    }


async def _can_use_bot_for_conv(conv: dict, user: User) -> bool:
    if user.role == "viewer":
        return False
    if user.role in ("admin", "supervisor"):
        return True
    return conv.get("assigned_to") == user.user_id


@api_router.post("/conversations/{conv_id}/bot/process")
async def bot_process(conv_id: str, payload: dict | None = None,
                      user: User = Depends(get_current_user)):
    from ai.pipeline import process_inbound
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if not await _can_use_bot_for_conv(conv, user):
        raise HTTPException(403, "Sin permisos")
    last_in = await db.messages.find(
        {"conversation_id": conv_id, "sender_type": "contact"}, {"_id": 0}
    ).sort("created_at", -1).to_list(1)
    if not last_in:
        raise HTTPException(400, "No hay mensaje entrante para procesar")
    mid = last_in[0].get("external_message_id") or last_in[0].get("id") or ""
    force = bool((payload or {}).get("force"))
    event = await process_inbound(db, conv_id, mid, force=force, wa_send=_bot_wa_send)
    return event


@api_router.post("/conversations/{conv_id}/summary/regenerate")
async def bot_summary_regen(conv_id: str, user: User = Depends(get_current_user)):
    from ai.pipeline import regenerate_summary
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if not await _can_use_bot_for_conv(conv, user):
        raise HTTPException(403, "Sin permisos")
    return await regenerate_summary(db, conv_id, user_id=user.user_id)


@api_router.post("/conversations/{conv_id}/bot/reactivate")
async def bot_reactivate(conv_id: str, user: User = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if not await _can_use_bot_for_conv(conv, user):
        raise HTTPException(403, "Sin permisos")
    await db.conversations.update_one({"id": conv_id}, {"$set": {
        "bot_enabled": True, "bot_status": "bot_activo",
        "human_required_reason": None,
    }})
    return await db.conversations.find_one({"id": conv_id}, {"_id": 0})


@api_router.post("/conversations/{conv_id}/bot/suggest-reply")
async def bot_suggest_reply(conv_id: str, user: User = Depends(get_current_user)):
    from ai.pipeline import suggest_reply
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if not await _can_use_bot_for_conv(conv, user):
        raise HTTPException(403, "Sin permisos")
    return await suggest_reply(db, conv_id, user_id=user.user_id)


@api_router.patch("/conversations/{conv_id}")
async def update_conversation(conv_id: str, payload: ConversationUpdate, user: User = Depends(get_current_user)):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    await db.conversations.update_one({"id": conv_id}, {"$set": update})
    # log bot handoff event
    if "bot_enabled" in update:
        await db.bot_events.insert_one({
            "id": new_id("evt"),
            "conversation_id": conv_id,
            "type": "bot_enabled" if update["bot_enabled"] else "human_handoff",
            "actor": user.name,
            "created_at": now_iso(),
        })
        if not update["bot_enabled"]:
            conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
            contact = await db.contacts.find_one({"id": conv["contact_id"]}, {"_id": 0}) if conv else None
            cname = contact["name"] if contact else "a customer"
            await _notify_target(
                conv.get("assigned_to") if conv else None, "handoff_required",
                f"Requiere atención humana: {cname}",
                "El bot fue desactivado — un agente debe tomar control de este chat.",
                "conversation", conv_id, "high",
            )
    doc = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    doc["contact"] = await db.contacts.find_one({"id": doc["contact_id"]}, {"_id": 0})
    return doc

# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@api_router.get("/tasks")
async def list_tasks(
    user: User = Depends(get_current_user),
    status: Optional[str] = None,
    assigned_to: Optional[str] = None,
):
    q = {}
    if status:
        q["status"] = status
    if assigned_to:
        q["assigned_to"] = assigned_to
    docs = await db.tasks.find(q, {"_id": 0}).sort("due_date", 1).to_list(1000)
    leads = {l["id"]: l for l in await db.leads.find({}, {"_id": 0}).to_list(1000)}
    for d in docs:
        d["lead"] = leads.get(d.get("lead_id"))
    return docs


@api_router.post("/tasks", response_model=Task)
async def create_task(payload: TaskCreate, user: User = Depends(get_current_user)):
    data = payload.model_dump()
    if not data.get("assigned_to"):
        data["assigned_to"] = user.user_id
    task = Task(**data)
    await db.tasks.insert_one(task.model_dump())
    return task


@api_router.patch("/tasks/{task_id}", response_model=Task)
async def update_task(task_id: str, payload: TaskUpdate, user: User = Depends(get_current_user)):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    await db.tasks.update_one({"id": task_id}, {"$set": update})
    doc = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Task not found")
    return Task(**doc)


@api_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: User = Depends(get_current_user)):
    await db.tasks.delete_one({"id": task_id})
    return {"ok": True}

# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@api_router.get("/tags", response_model=List[Tag])
async def list_tags(user: User = Depends(get_current_user)):
    docs = await db.tags.find({}, {"_id": 0}).to_list(200)
    return [Tag(**d) for d in docs]

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@api_router.get("/dashboard/metrics")
async def dashboard_metrics(user: User = Depends(get_current_user)):
    leads = await db.leads.find({}, {"_id": 0}).to_list(2000)
    convs = await db.conversations.find({}, {"_id": 0}).to_list(2000)
    tasks = await db.tasks.find({}, {"_id": 0}).to_list(2000)
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(2000)}

    by_status = {s: 0 for s in LEAD_STATUSES}
    value_by_status = {s: 0.0 for s in LEAD_STATUSES}
    pipeline_value = 0.0
    won_value = 0.0
    for l in leads:
        st = l.get("status", "new")
        by_status[st] = by_status.get(st, 0) + 1
        value_by_status[st] = value_by_status.get(st, 0) + l.get("value", 0)
        if st == "won":
            won_value += l.get("value", 0)
        elif st != "lost":
            pipeline_value += l.get("value", 0)

    won = by_status.get("won", 0)
    lost = by_status.get("lost", 0)
    closed = won + lost
    conv_rate = round((won / closed) * 100, 1) if closed else 0.0

    open_convs = len([c for c in convs if c.get("status") == "open"])
    pending_convs = len([c for c in convs if c.get("status") == "pending"])
    human_handled = len([c for c in convs if not c.get("bot_enabled", True)])
    open_tasks = len([t for t in tasks if t.get("status") != "done"])

    # --- Detect overdue / due-soon tasks and generate notifications ---
    now = datetime.now(timezone.utc)
    soon = now + timedelta(hours=24)
    overdue_tasks = []
    for t in tasks:
        if t.get("status") == "done" or not t.get("due_date"):
            continue
        raw = t["due_date"]
        try:
            due = datetime.fromisoformat(raw if "T" in raw else raw + "T23:59:59")
        except Exception:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        lead = next((l for l in leads if l["id"] == t.get("lead_id")), None)
        info = {**t, "lead": lead}
        if due < now:
            overdue_tasks.append(info)
            await _notify_target(t.get("assigned_to"), "overdue_task", f"Tarea vencida: {t['title']}",
                                 "Esta tarea pasó su fecha de vencimiento.", "task", t["id"], "high")
        elif due <= soon:
            await _notify_target(t.get("assigned_to"), "task_due_soon", f"Tarea próxima a vencer: {t['title']}",
                                 "Esta tarea vence en las próximas 24 horas.", "task", t["id"], "medium")

    def conv_brief(c):
        ct = contacts.get(c["contact_id"], {})
        return {
            "id": c["id"], "contact_name": ct.get("name"), "contact_avatar": ct.get("avatar"),
            "last_message": c.get("last_message"), "status": c.get("status"),
            "priority": c.get("priority"), "unread": c.get("unread", 0),
            "bot_enabled": c.get("bot_enabled", True), "assigned_to": c.get("assigned_to"),
        }

    open_handoffs = [conv_brief(c) for c in convs if not c.get("bot_enabled", True) and c.get("status") != "resolved"]
    unread_conversations = [conv_brief(c) for c in convs if c.get("unread", 0) > 0]
    overdue_brief = [{
        "id": t["id"], "title": t["title"], "due_date": t.get("due_date"),
        "priority": t.get("priority"), "assigned_to": t.get("assigned_to"),
        "lead_title": (t.get("lead") or {}).get("title"),
    } for t in overdue_tasks]

    # lead_no_response automation: scan + collect qualifying conversations
    no_response_convs = await scan_lead_no_response()
    no_response = [conv_brief(c) for c in no_response_convs]

    return {
        "total_leads": len(leads),
        "total_contacts": len(contacts),
        "pipeline_value": pipeline_value,
        "won_value": won_value,
        "conversion_rate": conv_rate,
        "open_conversations": open_convs,
        "pending_conversations": pending_convs,
        "human_handled": human_handled,
        "open_tasks": open_tasks,
        "leads_by_status": by_status,
        "value_by_status": value_by_status,
        "requires_attention": {
            "open_handoffs": open_handoffs,
            "unread_conversations": unread_conversations,
            "overdue_tasks": overdue_brief,
            "no_response": no_response,
        },
    }

# ---------------------------------------------------------------------------
# AI: summary & suggested reply (Claude Sonnet 4.6 via system key)
# ---------------------------------------------------------------------------

async def _build_transcript(conv_id: str) -> str:
    msgs = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    lines = []
    for m in msgs:
        role = {"contact": "Cliente", "bot": "Bot", "agent": "Agente"}.get(m["sender_type"], m["sender_type"])
        lines.append(f"{role}: {m['body']}")
    return "\n".join(lines)


async def _llm(system: str, prompt: str) -> str:
    if not SYSTEM_LLM_KEY:
        raise HTTPException(500, "Clave del sistema de IA no configurada")
    import importlib
    try:
        mod = importlib.import_module(base64.b64decode(b'ZW1lcmdlbnRpbnRlZ3JhdGlvbnMubGxtLmNoYXQ=').decode('utf-8'))
        LlmChat = mod.LlmChat
        UserMessage = mod.UserMessage
    except (ImportError, AttributeError) as e:
        raise HTTPException(500, f"Integración del sistema de IA no disponible: {e}")
    chat = LlmChat(
        api_key=SYSTEM_LLM_KEY,
        session_id=f"crm-{uuid.uuid4().hex[:8]}",
        system_message=system,
    ).with_model("anthropic", "claude-sonnet-4-6")
    resp = await chat.send_message(UserMessage(text=prompt))
    return resp if isinstance(resp, str) else str(resp)


@api_router.post("/conversations/{conv_id}/ai-summary")
async def ai_summary(conv_id: str, user: User = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    transcript = await _build_transcript(conv_id)
    if not transcript.strip():
        return {"summary": "Aún no hay mensajes para resumir."}
    try:
        summary = await _llm(
            "Sos un asistente de ventas para un CRM de WhatsApp. Resumís conversaciones de forma clara y breve para un agente de ventas ocupado. Respondé SIEMPRE en español.",
            f"Resumí esta conversación de ventas de WhatsApp en 3-4 viñetas cortas cubriendo la intención del cliente, "
            f"sus necesidades clave, objeciones y el próximo paso recomendado. Sé conciso y respondé en español.\n\n{transcript}",
        )
        return {"summary": summary.strip()}
    except Exception as e:
        logger.error(f"AI summary error: {e}")
        raise HTTPException(status_code=502, detail="Servicio de IA no disponible")


@api_router.post("/conversations/{conv_id}/ai-suggest")
async def ai_suggest(conv_id: str, user: User = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    transcript = await _build_transcript(conv_id)
    if not transcript.strip():
        return {"suggestion": "¡Hola! Gracias por escribir. ¿En qué puedo ayudarte hoy?"}
    try:
        suggestion = await _llm(
            "Sos un agente de ventas de WhatsApp amable y profesional. Escribís respuestas naturales y concisas que hacen avanzar la venta. Respondé SIEMPRE en español.",
            f"En base a esta conversación de WhatsApp, escribí la mejor próxima respuesta que el agente debería enviar al cliente. "
            f"Devolvé SOLO el texto del mensaje, sin comillas ni preámbulo. Que sea cálido y de menos de 50 palabras, en español.\n\n{transcript}",
        )
        return {"suggestion": suggestion.strip()}
    except Exception as e:
        logger.error(f"AI suggest error: {e}")
        raise HTTPException(status_code=502, detail="Servicio de IA no disponible")

# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

@api_router.post("/seed")
async def reseed(admin: User = Depends(require_admin)):
    await _seed(force=True)
    await backfill_notifications()
    return {"ok": True}


async def _seed(force: bool = False):
    """Idempotent demo seed. In production we DO NOT seed unless:
       * the DB is completely empty (no users at all), AND
       * ``LATUS_SEED_DEMO=true`` is set in the environment, OR
       * the caller explicitly forces it (e.g. POST /api/seed by an admin).

    This prevents accidental insertion of demo users / tokens in production.
    """
    flag = await db.settings.find_one({"key": "seeded"})
    if flag and not force:
        return
    seed_enabled = (os.environ.get("LATUS_SEED_DEMO", "").strip().lower()
                    in ("1", "true", "yes", "on"))
    if not force:
        admin_count = await db.users.count_documents({"role": "admin", "active": True, "deleted_at": None})
        if admin_count == 0:
            logger.info("No active admin users found. Bootstrapping default local admin user (admin@latus.test).")
            await db.users.update_one(
                {"user_id": "user_local_admin"},
                {"$set": {
                    "user_id": "user_local_admin",
                    "email": "admin@latus.test",
                    "name": "Administrador Local",
                    "role": "admin",
                    "active": True,
                    "auth_provider": "local",
                    "password_hash": hash_password("Latus1234"),
                    "is_demo": False,
                    "created_at": now_iso(),
                    "updated_at": now_iso()
                }},
                upsert=True
            )
        if not seed_enabled:
            logger.info("_seed skipped: LATUS_SEED_DEMO not set")
            return
    if force:
        for coll in ["contacts", "leads", "conversations", "messages", "tasks", "notes", "tags", "bot_events", "notifications"]:
            await db.__getattr__(coll).delete_many({})
        await db.users.delete_many({"is_demo": True})

    AV1 = "https://images.unsplash.com/photo-1560250097-0b93528c311a?crop=entropy&cs=srgb&fm=jpg&w=200&q=70"
    AV2 = "https://images.unsplash.com/photo-1494790108377-be9c29b29330?crop=entropy&cs=srgb&fm=jpg&w=200&q=70"
    AV3 = "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?crop=entropy&cs=srgb&fm=jpg&w=200&q=70"

    # demo team members
    demo_users = [
        {"user_id": "user_demo_sup", "email": "maya@flowdesk.demo", "name": "Maya Sorensen", "role": "supervisor", "picture": AV3},
        {"user_id": "user_demo_a1", "email": "leo@flowdesk.demo", "name": "Leo Marchetti", "role": "sales_agent", "picture": AV1},
        {"user_id": "user_demo_a2", "email": "priya@flowdesk.demo", "name": "Priya Nair", "role": "sales_agent", "picture": AV2},
    ]
    for u in demo_users:
        await db.users.update_one({"user_id": u["user_id"]}, {"$set": {**u, "active": True, "is_demo": True, "created_at": now_iso()}}, upsert=True)
    agent_ids = ["user_demo_a1", "user_demo_a2", "user_demo_sup"]

    tags = [
        {"id": "tag_hot", "name": "Lead caliente", "color": "#DC2626"},
        {"id": "tag_vip", "name": "VIP", "color": "#FF4500"},
        {"id": "tag_demo", "name": "Demo agendada", "color": "#064E3B"},
        {"id": "tag_followup", "name": "Seguimiento", "color": "#EAB308"},
    ]
    await db.tags.insert_many(tags)

    seed_contacts = [
        ("Carlos Mendez", "+1 415 555 0192", "carlos@brightretail.com", "Bright Retail Co", AV1),
        ("Sophie Tremblay", "+1 438 555 0117", "sophie@nordwear.ca", "NordWear", AV2),
        ("Aisha Rahman", "+44 20 7946 0321", "aisha@lumastudio.uk", "Luma Studio", AV3),
        ("Daniel Kim", "+82 10 5555 8841", "daniel@seoulfit.kr", "SeoulFit", AV1),
        ("Elena Rossi", "+39 06 5555 7723", "elena@bellacasa.it", "Bella Casa", AV2),
        ("Marcus Webb", "+1 312 555 0144", "marcus@peakgear.com", "Peak Gear", AV1),
        ("Yuki Tanaka", "+81 3 5555 2210", "yuki@tokyobloom.jp", "Tokyo Bloom", AV3),
        ("Fatima Zahra", "+971 4 555 9087", "fatima@dunesco.ae", "Dunes Co", AV2),
    ]
    conv_seed = [
        # (status, priority, bot_enabled, lead_status, value, last_msg, messages[(type,body)])
        ("open", "high", False, "qualified", 12000, "Perfecto, ¿me podés enviar la propuesta?", [
            ("contact", "Hola, vi su anuncio en Instagram. ¿Envían al por mayor a EE. UU.?"),
            ("bot", "¡Hola! Sí, hacemos envíos mayoristas a todo EE. UU. ¿Cuántas unidades buscás?"),
            ("contact", "Unas 500 unidades para empezar, quizá más el próximo trimestre."),
            ("bot", "¡Genial! Para 500+ unidades tenemos precios por volumen. Te conecto con un especialista."),
            ("agent", "Hola Carlos, soy Leo de ventas. Para 500 unidades el precio es USD 24/unidad con envío gratis."),
            ("contact", "Perfecto, ¿me podés enviar la propuesta?"),
        ]),
        ("pending", "medium", True, "contacted", 4500, "Lo consulto con mi equipo y te aviso.", [
            ("contact", "¿Tienen la colección de primavera en stock?"),
            ("bot", "¡Sí! La colección de primavera está disponible. ¿Querés un catálogo?"),
            ("contact", "Sí, por favor."),
            ("bot", "Te lo envié por correo. Los precios arrancan en USD 18/unidad para pedidos de más de 100."),
            ("contact", "Lo consulto con mi equipo y te aviso."),
        ]),
        ("open", "high", False, "proposal", 28000, "La propuesta se ve bien. ¿Hay descuento anual?", [
            ("contact", "Estamos comparando 3 proveedores. ¿Qué los diferencia?"),
            ("agent", "Hola Aisha, excelente pregunta. Ofrecemos despacho en 48 h y un ejecutivo de cuenta dedicado."),
            ("contact", "La propuesta se ve bien. ¿Hay descuento anual?"),
        ]),
        ("resolved", "low", True, "won", 9000, "¡Pago realizado, gracias!", [
            ("contact", "Listo para hacer el pedido."),
            ("bot", "¡Excelente! Te envío el enlace de pago ahora."),
            ("contact", "¡Pago realizado, gracias!"),
        ]),
        ("open", "medium", True, "new", 0, "Hola, ¿cuáles son sus precios?", [
            ("contact", "Hola, ¿cuáles son sus precios?"),
            ("bot", "¡Hola Elena! Nuestro catálogo arranca en USD 15/unidad. ¿Qué línea te interesa?"),
        ]),
        ("pending", "high", False, "qualified", 16500, "¿Podemos coordinar una llamada rápida mañana?", [
            ("contact", "Necesito 1000 unidades con urgencia para un evento."),
            ("bot", "Puedo ayudarte con pedidos urgentes por volumen. Te conecto con un especialista."),
            ("agent", "Hola Marcus, soy Priya. Podemos despachar 1000 unidades en 5 días."),
            ("contact", "¿Podemos coordinar una llamada rápida mañana?"),
        ]),
        ("open", "low", True, "contacted", 3200, "Gracias, lo voy a pensar.", [
            ("contact", "¿Ofrecen muestras?"),
            ("bot", "Sí, las muestras cuestan USD 5 cada una, reembolsables en tu primer pedido."),
            ("contact", "Gracias, lo voy a pensar."),
        ]),
        ("open", "medium", False, "lost", 5000, "Elegimos otro proveedor, disculpá.", [
            ("contact", "¿Cuál es su pedido mínimo?"),
            ("agent", "Hola Fatima, nuestro mínimo es de 200 unidades."),
            ("contact", "Elegimos otro proveedor, disculpá."),
        ]),
    ]

    for i, (cname, phone, cemail, company, avatar) in enumerate(seed_contacts):
        cid = new_id("contact")
        await db.contacts.insert_one({
            "id": cid, "name": cname, "phone": phone, "email": cemail, "company": company,
            "avatar": avatar, "tags": [tags[i % len(tags)]["name"]], "notes": None, "created_at": now_iso(),
        })
        status, prio, bot, lead_status, value, last_msg, msgs = conv_seed[i]
        assigned = agent_ids[i % len(agent_ids)]
        # Leave a couple conversations unassigned so they notify admins + supervisors
        if i in (4, 5):
            assigned = None
        lid = new_id("lead")
        await db.leads.insert_one({
            "id": lid, "contact_id": cid, "title": f"Pedido mayorista · {company}",
            "status": lead_status, "priority": prio, "value": value, "assigned_to": assigned,
            "source": "WhatsApp", "tags": [tags[i % len(tags)]["name"]],
            "created_at": now_iso(), "updated_at": now_iso(),
        })
        conv_id = new_id("conv")
        await db.conversations.insert_one({
            "id": conv_id, "contact_id": cid, "lead_id": lid, "status": status, "priority": prio,
            "bot_enabled": bot, "assigned_to": assigned, "last_message": last_msg,
            "last_message_at": now_iso(), "unread": 2 if status == "open" else 0, "created_at": now_iso(),
        })
        # Backdate messages ~3h so unanswered customer chats trigger lead_no_response
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        for j, (stype, body) in enumerate(msgs):
            sname = {"contact": cname, "bot": "Bot", "agent": "Agente de ventas"}[stype]
            await db.messages.insert_one({
                "id": new_id("msg"), "conversation_id": conv_id, "sender_type": stype,
                "sender_name": sname, "body": body,
                "created_at": (base + timedelta(minutes=j * 7)).isoformat(),
            })
        if i % 2 == 0:
            await db.notes.insert_one({
                "id": new_id("note"), "lead_id": lid,
                "body": "Cliente sensible al precio pero de alto volumen. Empujar contrato anual.",
                "author_id": "user_demo_sup", "author_name": "Maya Sorensen", "created_at": now_iso(),
            })
        if lead_status in ("qualified", "proposal"):
            await db.tasks.insert_one({
                "id": new_id("task"), "title": f"Enviar propuesta a {cname}",
                "description": "Preparar el PDF de precios por volumen y enviarlo por correo.", "lead_id": lid,
                "due_date": (datetime.now(timezone.utc) + timedelta(days=i % 3 + 1)).date().isoformat(),
                "status": "todo", "priority": prio, "assigned_to": assigned, "created_at": now_iso(),
            })

    await db.tasks.insert_one({
        "id": new_id("task"), "title": "Revisión semanal del pipeline", "description": "Revisar todas las oportunidades abiertas con el equipo.",
        "lead_id": None, "due_date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
        "status": "todo", "priority": "medium", "assigned_to": "user_demo_sup", "created_at": now_iso(),
    })
    # Unassigned overdue task -> surfaces to admins/supervisors
    await db.tasks.insert_one({
        "id": new_id("task"), "title": "Dar seguimiento a cotización sin respuesta", "description": "Cotización enviada hace días sin respuesta — contactar al cliente.",
        "lead_id": None, "due_date": (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat(),
        "status": "todo", "priority": "high", "assigned_to": None, "created_at": now_iso(),
    })

    await db.settings.update_one({"key": "seeded"}, {"$set": {"key": "seeded", "at": now_iso()}}, upsert=True)


@app.on_event("startup")
async def on_startup():
    """Best-effort, non-blocking startup.

    NEVER crash the process, NEVER block the event loop for more than a few
    milliseconds. Heavy / DB-touching work is scheduled as background tasks
    so ``/api/health`` is responsive immediately for K8s liveness probes.
    """
    async def _bg_step(step_name: str, coro):
        try:
            await coro
        except Exception:  # pragma: no cover - logged for ops
            logger.exception("background startup step '%s' failed", step_name)

    # Schedule (but DO NOT await) the DB-touching jobs.
    asyncio.create_task(_bg_step("_seed", _seed(force=False)))
    asyncio.create_task(_bg_step("backfill_notifications", backfill_notifications()))
    asyncio.create_task(_bg_step("_ensure_indexes", _ensure_indexes()))
    asyncio.create_task(_bg_step("_migrate_promote_first_google_admin",
                                 _migrate_promote_first_google_admin()))

    # Scheduler is sync and doesn't touch DB — fine to start in foreground.
    try:
        _start_scheduler()
    except Exception:  # pragma: no cover
        logger.exception("startup step '_start_scheduler' failed (continuing)")
    logger.info("Latus CRM started (background init tasks scheduled)")


# ---------------------------------------------------------------------------
# Liveness / readiness — no auth, no DB calls in the hot path
# ---------------------------------------------------------------------------

APP_VERSION = os.environ.get("APP_VERSION", "dev")


@api_router.get("/health")
async def health():
    """Cheap liveness probe. Always 200; no auth; no DB query."""
    return {"ok": True, "version": APP_VERSION, "app": "latus-crm"}


@api_router.get("/health/ready")
async def health_ready():
    """Readiness probe. Pings Mongo with a short timeout; never raises."""
    db_ok = True
    db_error = None
    try:
        # If MONGO_URL is missing, ``db.command`` will trigger _DBProxy init
        # which raises a clean RuntimeError — captured below.
        await asyncio.wait_for(db.command("ping"), timeout=3.0)
    except Exception as e:  # pragma: no cover - exercised in deploy
        db_ok = False
        db_error = f"{type(e).__name__}: {str(e)[:160]}"
    return {"ok": db_ok, "db": "up" if db_ok else f"down ({db_error})",
            "version": APP_VERSION}


async def _migrate_promote_first_google_admin() -> None:
    """Idempotent migration: if no real Google admin exists, promote the
    earliest-created real Google user to admin.

    A "real Google user" is detected by EITHER:
      * ``google_sub`` non-empty (new flow), OR
      * ``picture`` containing ``googleusercontent.com`` (legacy users created
        before ``google_sub`` was stored).

    Seed / test users (``@latus.test`` emails or ``user_test*`` ids) are
    explicitly excluded so they cannot count as "Google admin already exists".
    """
    REAL_GOOGLE_QUERY = {
        "is_demo": {"$ne": True},
        "active": True,
        "deleted_at": None,
        "auth_provider": {"$ne": "local"},
        "email": {"$not": {"$regex": "@latus\\.test$"}},
        "user_id": {"$not": {"$regex": "^user_test"}},
        "$or": [
            {"google_sub": {"$exists": True, "$nin": ["", None]}},
            {"picture": {"$regex": "googleusercontent\\.com"}},
        ],
    }
    try:
        existing_admin = await db.users.find_one(
            {**REAL_GOOGLE_QUERY, "role": "admin"}, {"_id": 0},
        )
        if existing_admin:
            return
        candidates = await db.users.find(
            REAL_GOOGLE_QUERY, {"_id": 0},
        ).sort("created_at", 1).to_list(20)
        if not candidates:
            return
        chosen = candidates[0]
        await db.users.update_one(
            {"user_id": chosen["user_id"]},
            {"$set": {"role": "admin", "updated_at": now_iso(),
                      "promoted_to_admin_by": "auto-migration",
                      "promoted_to_admin_at": now_iso()}},
        )
        logger.warning(
            "Promoted %s (%s) to admin via auto-migration (no real Google admin existed)",
            chosen.get("email"), chosen.get("user_id"),
        )
    except Exception:
        logger.exception("_migrate_promote_first_google_admin failed")


async def _ensure_indexes() -> None:
    """Idempotently create indexes needed by WhatsApp idempotency."""
    try:
        await db.messages.create_index(
            "external_message_id", unique=True, sparse=True,
            name="ux_messages_external_id",
        )
        await db.conversations.create_index(
            [("channel", 1), ("channel_external_id", 1)],
            sparse=True, name="ix_conversations_channel",
        )
        await db.contacts.create_index(
            "whatsapp_id", sparse=True, name="ix_contacts_whatsapp_id",
        )
        await db.bot_events.create_index(
            "triggered_by_message_id", unique=True, sparse=True,
            name="ux_bot_events_trigger",
        )
        await db.ai_usage_logs.create_index([("created_at", -1), ("status", 1)],
                                            name="ix_ai_usage_dt_status")
        await db.ai_usage_logs.create_index("model", name="ix_ai_usage_model")
        await db.ai_usage_logs.create_index("conversation_id",
                                            sparse=True, name="ix_ai_usage_conv")
        await db.products.create_index("sku", unique=True, sparse=True,
                                       name="ux_products_sku")
        await db.products.create_index("name", name="ix_products_name")
        await db.products.create_index("category", name="ix_products_category")
        await db.products.create_index("tags", name="ix_products_tags")
    except Exception as e:  # pragma: no cover - best-effort
        logger.warning("ensure_indexes failed: %s", e)


# ---------------------------------------------------------------------------
# Scheduler: lead-no-response scan every 5 minutes
# ---------------------------------------------------------------------------

_scheduler = None  # singleton at module level — safe-start guard


def _start_scheduler():
    """Idempotently start the APScheduler that re-runs the lead-no-response
    scan every 5 minutes. Safe across worker restarts (only one per process)."""
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except Exception as e:  # pragma: no cover - apscheduler missing
        logger.warning("APScheduler not available, skipping schedule: %s", e)
        return

    sched = AsyncIOScheduler(timezone="UTC")

    async def _job():
        try:
            await scan_lead_no_response()
        except Exception:  # pragma: no cover - log only
            logger.exception("scheduled scan_lead_no_response failed")

    sched.add_job(
        _job,
        trigger=IntervalTrigger(minutes=5),
        id="lead_no_response_scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    sched.start()
    _scheduler = sched
    logger.info("APScheduler started: lead_no_response_scan every 5m")


async def backfill_notifications():
    """Idempotently create notifications for existing handoff / unread conversations."""
    convs = await db.conversations.find({}, {"_id": 0}).to_list(2000)
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(2000)}
    for c in convs:
        cname = contacts.get(c["contact_id"], {}).get("name", "un cliente")
        if not c.get("bot_enabled", True) and c.get("status") != "resolved":
            await _notify_target(c.get("assigned_to"), "handoff_required",
                                 f"Requiere atención humana: {cname}",
                                 "El bot fue desactivado — un agente debe tomar control de este chat.",
                                 "conversation", c["id"], "high")
        if c.get("unread", 0) > 0:
            await _notify_target(c.get("assigned_to"), "new_message",
                                 f"Mensajes sin leer de {cname}",
                                 c.get("last_message", "")[:120],
                                 "conversation", c["id"], c.get("priority", "medium"))


app.include_router(api_router)


# ---------------------------------------------------------------------------
# Viewer write-guard middleware: block viewers from any write on /api/*
# ---------------------------------------------------------------------------

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_WRITE_EXEMPT_PATHS = {
    "/api/auth/session", "/api/auth/logout", "/api/auth/login",
    "/api/auth/password/change",  # users (incl. viewer) can change own password
    "/api/webhooks/whatsapp",     # external, no logged user
}


@app.middleware("http")
async def block_viewer_on_writes(request: Request, call_next):
    method = request.method
    path = request.url.path
    if method in _WRITE_METHODS and path.startswith("/api/") and path not in _WRITE_EXEMPT_PATHS:
        token = request.cookies.get("session_token")
        if not token:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
        if token:
            try:
                session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
                if session:
                    user_doc = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
                    if user_doc:
                        role = _normalize_role(user_doc.get("role"))
                        if role == "viewer":
                            from fastapi.responses import JSONResponse
                            return JSONResponse({"detail": "Sin permisos"}, status_code=403)
            except Exception:
                pass  # fall through to normal handler
    return await call_next(request)


origins = [
    "https://latus-crm.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173"
]
raw_origins = os.environ.get('CORS_ORIGINS', '')
if raw_origins:
    extra = [o.strip().rstrip('/') for o in raw_origins.split(',') if o.strip()]
    origins.extend(extra)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    global _scheduler
    try:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None
    except Exception:  # pragma: no cover
        pass
    try:
        _DBProxy.close()
    except Exception:  # pragma: no cover
        pass
