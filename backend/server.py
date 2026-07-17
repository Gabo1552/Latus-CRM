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
import ssl
import smtplib
import hashlib
from pathlib import Path
from dataclasses import replace
from email.message import EmailMessage
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
    send_template_message,
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
RESEND_API_KEY = (os.environ.get("RESEND_API_KEY") or "").strip()
RESEND_FROM_EMAIL = (os.environ.get("RESEND_FROM_EMAIL") or "").strip().lower()
RESEND_FROM_NAME = (os.environ.get("RESEND_FROM_NAME") or "Latus CRM").strip()
APP_BASE_URL = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")

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


DEFAULT_ROLE_PERMISSIONS = {
    "admin": ["manage_users", "configure_whatsapp", "configure_ai", "manage_settings", "write_catalog", "message_any", "trigger_bot_any", "write_crm"],
    "supervisor": ["write_catalog", "trigger_bot_any", "write_crm"],
    "agent": ["write_crm"],
    "viewer": []
}


async def get_role_permissions(role: str) -> list[str]:
    try:
        doc = await db.roles.find_one({"role_id": role})
        if doc and "permissions" in doc:
            return list(doc["permissions"])
    except Exception:
        pass
    return list(DEFAULT_ROLE_PERMISSIONS.get(role, []))


async def get_all_roles() -> set[str]:
    roles_set = set(ROLES)
    try:
        custom = await db.roles.find({}, {"_id": 0, "role_id": 1}).to_list(100)
        roles_set.update(r["role_id"] for r in custom)
    except Exception:
        pass
    return roles_set


def require_perm(permission: str):
    """Dependency factory: check if the current user has the required permission."""
    async def _dep(user: User = Depends(get_current_user)) -> User:
        perms = await get_role_permissions(user.role)
        if permission not in perms:
            raise HTTPException(status_code=403, detail="Permiso insuficiente")
        return user
    return _dep

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
    permissions: Optional[List[str]] = None
    work_areas: Optional[List[str]] = None
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

    # Meta Ads Referral Fields
    meta_ad_id: Optional[str] = None
    meta_ad_url: Optional[str] = None
    meta_source_type: Optional[str] = None
    meta_ad_title: Optional[str] = None
    meta_ad_body: Optional[str] = None
    meta_ad_media_type: Optional[str] = None
    meta_ad_image_url: Optional[str] = None
    meta_ctwa_clid: Optional[str] = None
    lead_source: Optional[str] = "Orgánico"
    first_message_from_ad: Optional[str] = None
    first_ad_message_at: Optional[str] = None
    raw_referral: Optional[dict] = None


class ContactCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    company: Optional[str] = None
    tags: List[str] = []
    notes: Optional[str] = None


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    lead_source: Optional[str] = None


class LeadProduct(BaseModel):
    id: Optional[str] = None
    name: str
    price: float
    quantity: int = 1
    currency: Optional[str] = None
    list_price: Optional[float] = None
    promotion_applied: bool = False


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
    products: List[LeadProduct] = []
    closed_at: Optional[str] = None
    closed_by: Optional[str] = None
    closed_value: Optional[float] = None
    sale_snapshot: Optional[dict] = None
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
    products: Optional[List[LeadProduct]] = []


class LeadUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    value: Optional[float] = None
    assigned_to: Optional[str] = None
    tags: Optional[List[str]] = None
    products: Optional[List[LeadProduct]] = None


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("conv"))
    contact_id: str
    lead_id: Optional[str] = None
    status: str = "open"
    priority: str = "medium"
    bot_enabled: bool = True
    assigned_to: Optional[str] = None
    assigned_work_area: Optional[str] = None
    last_message: str = ""
    last_message_at: str = Field(default_factory=now_iso)
    unread: int = 0
    created_at: str = Field(default_factory=now_iso)


class ConversationUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    bot_enabled: Optional[bool] = None
    assigned_to: Optional[str] = None
    assigned_work_area: Optional[str] = None


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
    status: str = "todo"
    priority: str = "medium"
    assigned_to: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    lead_id: Optional[str] = None
    due_date: Optional[str] = None
    status: str = "todo"
    priority: str = "medium"
    assigned_to: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None


class Appointment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("appt"))
    contact_id: Optional[str] = None
    lead_id: Optional[str] = None
    conversation_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    event_type: str = "appointment"  # appointment, event
    start_time: str
    end_time: str
    status: str = "scheduled"  # scheduled, completed, cancelled
    assigned_to: Optional[str] = None
    scheduling_mode: str = "people"  # people, business
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    reminder_enabled: bool = False
    reminder_minutes_before: Optional[int] = None
    reminder_template_id: Optional[str] = None
    reminder_due_at: Optional[str] = None
    reminder_status: Optional[str] = None
    reminder_sent_at: Optional[str] = None
    reminder_error: Optional[str] = None
    reminder_attempts: int = 0
    confirmation_status: Optional[str] = None
    created_by_bot: bool = False
    created_by: Optional[str] = None
    created_by_name: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class AppointmentCreate(BaseModel):
    contact_id: Optional[str] = None
    lead_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    event_type: Literal["appointment", "event"] = "appointment"
    start_time: str
    end_time: str
    status: Literal["scheduled", "completed", "cancelled"] = "scheduled"
    assigned_to: Optional[str] = None
    service_id: Optional[str] = None
    reminder_enabled: Optional[bool] = None
    reminder_minutes_before: Optional[int] = None
    reminder_template_id: Optional[str] = None


class AppointmentUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    event_type: Optional[Literal["appointment", "event"]] = None
    contact_id: Optional[str] = None
    lead_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    status: Optional[Literal["scheduled", "completed", "cancelled"]] = None
    assigned_to: Optional[str] = None
    service_id: Optional[str] = None
    reminder_enabled: Optional[bool] = None
    reminder_minutes_before: Optional[int] = None
    reminder_template_id: Optional[str] = None


class CalendarAvailabilityUpdate(BaseModel):
    enabled: Optional[bool] = None
    timezone: Optional[str] = None
    default_duration_minutes: Optional[int] = None
    buffer_minutes: Optional[int] = None
    weekly_schedule: Optional[dict] = None


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
    user = User(**user_doc)
    user.permissions = await get_role_permissions(user.role)
    return user


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
    perms = await get_role_permissions(user.role)
    if "write_crm" not in perms:
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
    user = User(**user_doc)
    user.permissions = await get_role_permissions(user.role)
    return user


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


class ForgotPasswordBody(BaseModel):
    email: str


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str


class SendTestEmailBody(BaseModel):
    to_email: str


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


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _build_password_email_html(*, title: str, intro: str, cta_label: str,
                               cta_url: str, footer: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;background:#f9f9f7;padding:24px;color:#0B1B26">
      <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #E9E6DC;padding:32px">
        <h1 style="margin:0 0 16px;font-size:24px">{title}</h1>
        <p style="margin:0 0 24px;line-height:1.6">{intro}</p>
        <p style="margin:0 0 24px">
          <a href="{cta_url}" style="display:inline-block;background:#0E8DDB;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:4px;font-weight:700">
            {cta_label}
          </a>
        </p>
        <p style="margin:0 0 16px;line-height:1.6">Si el botón no funciona, copiá este enlace en tu navegador:</p>
        <p style="margin:0 0 24px;word-break:break-all;color:#0E8DDB">{cta_url}</p>
        <p style="margin:0;color:#666666;line-height:1.6">{footer}</p>
      </div>
    </div>
    """.strip()


async def send_email_via_settings(*, to_email: str, subject: str, html_body: str,
                                  text_body: str) -> bool:
    settings = await get_app_settings()
    if not settings.get("smtp_enabled"):
        return False
    host = (settings.get("smtp_host") or "").strip()
    from_email = (settings.get("smtp_from_email") or "").strip()
    if not host or not from_email:
        logger.warning("smtp enabled but host/from_email missing")
        return False

    # If using Resend, prefer the HTTP REST API to bypass cloud SMTP port blocking (e.g. on Railway)
    password = settings.get("smtp_password") or ""
    if host == "smtp.resend.com" or (password and password.startswith("re_")):
        try:
            from_name = settings.get("smtp_from_name") or "Latus CRM"
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {password}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": f"{from_name} <{from_email}>",
                        "to": [to_email],
                        "subject": subject,
                        "html": html_body,
                        "text": text_body,
                    }
                )
                if res.status_code in (200, 201):
                    logger.info("Email sent successfully via Resend HTTP API")
                    return True
                else:
                    logger.error("Resend HTTP API failed with status %d: %s", res.status_code, res.text)
        except Exception as e:
            logger.exception("Failed sending email via Resend HTTP API: %s", e)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f'{settings.get("smtp_from_name") or "Latus CRM"} <{from_email}>'
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    def _send():
        use_ssl = bool(settings.get("smtp_use_ssl"))
        use_tls = bool(settings.get("smtp_use_tls"))
        port = int(settings.get("smtp_port") or (465 if use_ssl else 587))
        username = settings.get("smtp_username") or ""
        password = settings.get("smtp_password") or ""
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if use_tls and not use_ssl:
                server.starttls(context=ssl.create_default_context())
            if username:
                server.login(username, password)
            server.send_message(msg)

    try:
        await asyncio.to_thread(_send)
        return True
    except Exception:
        logger.exception("failed to send email to %s", to_email)
        return False


def _resolve_app_base_url(settings: dict, request: Request | None = None) -> str:
    configured = _normalize_optional_url(settings.get("app_base_url"))
    if configured:
        return configured
    origin = (request.headers.get("origin") if request else "") or ""
    if origin.startswith("http://") or origin.startswith("https://"):
        return origin.rstrip("/")
    return "http://localhost:3000"


async def create_password_reset_token(*, user_id: str, purpose: str = "reset_password",
                                      expires_minutes: int = 60) -> tuple[str, str]:
    token = secrets_token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    await db.password_reset_tokens.update_many(
        {"user_id": user_id, "purpose": purpose, "used_at": None},
        {"$set": {"revoked_at": now_iso()}},
    )
    await db.password_reset_tokens.insert_one({
        "id": new_id("prt"),
        "user_id": user_id,
        "purpose": purpose,
        "token_hash": _hash_reset_token(token),
        "created_at": now_iso(),
        "expires_at": expires_at.isoformat(),
        "used_at": None,
        "revoked_at": None,
    })
    return token, expires_at.isoformat()


async def send_password_setup_email(*, user_doc: dict, request: Request | None = None) -> bool:
    settings = await get_app_settings()
    app_base = _resolve_app_base_url(settings, request)
    token, _ = await create_password_reset_token(user_id=user_doc["user_id"], purpose="welcome_password")
    reset_url = f"{app_base}/?reset_token={token}"
    return await send_email_via_settings(
        to_email=user_doc["email"],
        subject="Tu acceso a Latus CRM",
        html_body=_build_password_email_html(
            title="Tu cuenta ya esta lista",
            intro=f"Hola {user_doc.get('name') or user_doc['email']}, tu usuario fue creado en Latus CRM. Usá este enlace para definir tu contraseña y entrar al sistema.",
            cta_label="Definir contraseña",
            cta_url=reset_url,
            footer="Si no esperabas este correo, podés ignorarlo. El enlace vence en 60 minutos.",
        ),
        text_body=(
            f"Hola {user_doc.get('name') or user_doc['email']}, tu cuenta en Latus CRM fue creada. "
            f"Definí tu contraseña desde este enlace: {reset_url} . El enlace vence en 60 minutos."
        ),
    )


async def send_password_recovery_email(*, user_doc: dict, request: Request | None = None) -> bool:
    settings = await get_app_settings()
    app_base = _resolve_app_base_url(settings, request)
    token, _ = await create_password_reset_token(user_id=user_doc["user_id"], purpose="reset_password")
    reset_url = f"{app_base}/?reset_token={token}"
    return await send_email_via_settings(
        to_email=user_doc["email"],
        subject="Recuperá tu contraseña de Latus CRM",
        html_body=_build_password_email_html(
            title="Recuperación de contraseña",
            intro=f"Hola {user_doc.get('name') or user_doc['email']}, recibimos un pedido para cambiar tu contraseña de Latus CRM.",
            cta_label="Crear nueva contraseña",
            cta_url=reset_url,
            footer="Si no pediste este cambio, podés ignorar este email. El enlace vence en 60 minutos.",
        ),
        text_body=(
            f"Recibimos un pedido para cambiar tu contraseña de Latus CRM. "
            f"Usá este enlace para crear una nueva: {reset_url} . Si no fuiste vos, ignorá este mensaje."
        ),
    )


async def send_welcome_email(*, user_doc: dict, auth_provider: str,
                             request: Request | None = None) -> bool:
    if auth_provider in ("local", "both"):
        return await send_password_setup_email(user_doc=user_doc, request=request)
    settings = await get_app_settings()
    app_base = _resolve_app_base_url(settings, request)
    login_url = f"{app_base}/"
    return await send_email_via_settings(
        to_email=user_doc["email"],
        subject="Tu usuario de Latus CRM fue creado",
        html_body=_build_password_email_html(
            title="Bienvenido a Latus CRM",
            intro=f"Hola {user_doc.get('name') or user_doc['email']}, tu cuenta fue creada correctamente. Ya podés ingresar con el método configurado por tu administrador.",
            cta_label="Ir al login",
            cta_url=login_url,
            footer="Si no esperabas este correo, comunicate con tu administrador.",
        ),
        text_body=(
            f"Hola {user_doc.get('name') or user_doc['email']}, tu cuenta en Latus CRM ya fue creada. "
            f"Podés ingresar desde {login_url}"
        ),
    )


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
    user_obj = User(**user_doc)
    user_obj.permissions = await get_role_permissions(user_obj.role)
    return user_obj


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


@api_router.post("/auth/password/forgot")
async def auth_forgot_password(payload: ForgotPasswordBody, request: Request):
    email = (payload.email or "").lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")
    user = await db.users.find_one({"email": email, "deleted_at": None, "active": True}, {"_id": 0})
    if user:
        ap = (user.get("auth_provider") or "google").lower()
        if ap in ("local", "both"):
            await send_password_recovery_email(user_doc=user, request=request)
    return {"ok": True, "message": "Si el email existe, te enviamos instrucciones para recuperar tu acceso."}


@api_router.post("/auth/password/reset")
async def auth_reset_password(payload: ResetPasswordBody):
    if not payload.token:
        raise HTTPException(status_code=400, detail="Token inválido")
    ok, msg = validate_password_policy(payload.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    doc = await db.password_reset_tokens.find_one({
        "token_hash": _hash_reset_token(payload.token),
        "used_at": None,
        "revoked_at": None,
    }, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=400, detail="El enlace de recuperación es inválido o ya fue usado")
    expires_at = doc.get("expires_at")
    expires_dt = datetime.fromisoformat(expires_at) if isinstance(expires_at, str) else expires_at
    if not expires_dt or expires_dt < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="El enlace de recuperación venció")
    user = await db.users.find_one({"user_id": doc["user_id"], "deleted_at": None, "active": True}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=400, detail="Usuario no disponible")
    ap = (user.get("auth_provider") or "google").lower()
    if ap not in ("local", "both"):
        raise HTTPException(status_code=400, detail="Este usuario no usa contraseña local")
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"password_hash": hash_password(payload.new_password), "updated_at": now_iso()}},
    )
    await db.password_reset_tokens.update_one({"id": doc["id"]}, {"$set": {"used_at": now_iso()}})
    await db.user_sessions.delete_many({"user_id": user["user_id"]})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin · Roles CRUD
# ---------------------------------------------------------------------------

class RoleCreate(BaseModel):
    role_id: str
    name: str
    permissions: List[str] = []

class RoleUpdatePayload(BaseModel):
    name: Optional[str] = None
    permissions: List[str]

@api_router.get("/roles")
async def list_roles(user: User = Depends(require_perm("manage_users"))):
    docs = await db.roles.find({}, {"_id": 0}).to_list(100)
    # If empty, return defaults
    if not docs:
        docs = [
            {"role_id": rid, "name": rid.capitalize(), "permissions": perms, "is_default": True}
            for rid, perms in DEFAULT_ROLE_PERMISSIONS.items()
        ]
    return docs

@api_router.post("/roles")
async def create_custom_role(payload: RoleCreate, user: User = Depends(require_perm("manage_users"))):
    rid = payload.role_id.strip().lower()
    if not rid or not payload.name.strip():
        raise HTTPException(status_code=400, detail="ID de rol y nombre son requeridos")
    if rid in DEFAULT_ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail="No se puede sobreescribir un rol del sistema")
    
    # Check if exists
    exist = await db.roles.find_one({"role_id": rid})
    if exist:
        raise HTTPException(status_code=400, detail="El rol ya existe")
        
    doc = {
        "role_id": rid,
        "name": payload.name.strip(),
        "permissions": payload.permissions,
        "is_default": False
    }
    await db.roles.insert_one(doc)
    # Add to global ROLES list so it's recognized as a valid role
    if rid not in ROLES:
        ROLES.append(rid)
    return {"ok": True, "role": doc}

@api_router.put("/roles/{role_id}")
async def update_role(role_id: str, payload: RoleUpdatePayload, user: User = Depends(require_perm("manage_users"))):
    rid = role_id.strip().lower()
    exist = await db.roles.find_one({"role_id": rid})
    if not exist:
        # If it's a default role, we can create/upsert it in DB
        if rid in DEFAULT_ROLE_PERMISSIONS:
            doc = {
                "role_id": rid,
                "name": rid.capitalize(),
                "permissions": payload.permissions,
                "is_default": True
            }
            await db.roles.insert_one(doc)
            return {"ok": True, "role": doc}
        raise HTTPException(status_code=404, detail="Rol no encontrado")
        
    update = {"permissions": payload.permissions}
    if payload.name:
        update["name"] = payload.name.strip()
        
    await db.roles.update_one({"role_id": rid}, {"$set": update})
    return {"ok": True}

@api_router.delete("/roles/{role_id}")
async def delete_custom_role(role_id: str, user: User = Depends(require_perm("manage_users"))):
    rid = role_id.strip().lower()
    if rid in DEFAULT_ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail="No se pueden borrar roles del sistema")
        
    exist = await db.roles.find_one({"role_id": rid})
    if not exist:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
        
    # Check if any user is currently using this role
    in_use = await db.users.find_one({"role": rid})
    if in_use:
        raise HTTPException(status_code=400, detail="No se puede borrar el rol porque está siendo usado por uno o más usuarios")
        
    await db.roles.delete_one({"role_id": rid})
    if rid in ROLES:
        try:
            ROLES.remove(rid)
        except ValueError:
            pass
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin · Work Areas CRUD
# ---------------------------------------------------------------------------

class WorkAreaCreate(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    routing_rules: Optional[str] = ""


@api_router.get("/admin/work-areas")
async def list_work_areas(admin: User = Depends(require_perm("manage_users"))):
    docs = await db.work_areas.find({}, {"_id": 0}).to_list(100)
    return docs


@api_router.post("/admin/work-areas")
async def create_work_area(payload: WorkAreaCreate, admin: User = Depends(require_perm("manage_users"))):
    import re
    wa_id = payload.id.strip().lower()
    name = payload.name.strip()
    if not wa_id or not name:
        raise HTTPException(status_code=400, detail="El ID y el nombre son requeridos")
    if not re.match(r"^[a-z0-9_-]+$", wa_id):
        raise HTTPException(status_code=400, detail="El ID solo puede contener letras minúsculas, números, guiones y guiones bajos")
    
    exist = await db.work_areas.find_one({"id": wa_id})
    if exist:
        raise HTTPException(status_code=400, detail="El área de trabajo ya existe")
        
    doc = {
        "id": wa_id,
        "name": name,
        "description": payload.description.strip(),
        "routing_rules": payload.routing_rules.strip(),
        "created_at": now_iso(),
    }
    await db.work_areas.insert_one(doc)
    return doc


@api_router.delete("/admin/work-areas/{wa_id}")
async def delete_work_area(wa_id: str, admin: User = Depends(require_perm("manage_users"))):
    wa_id = wa_id.strip().lower()
    exist = await db.work_areas.find_one({"id": wa_id})
    if not exist:
        raise HTTPException(status_code=404, detail="Área de trabajo no encontrada")
        
    await db.work_areas.delete_one({"id": wa_id})
    # Remove from all users
    await db.users.update_many({}, {"$pull": {"work_areas": wa_id}})
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
    work_areas: Optional[List[str]] = None


class AdminUserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    auth_provider: Optional[str] = None
    is_active: Optional[bool] = None
    work_areas: Optional[List[str]] = None


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
        "work_areas": d.get("work_areas") or [],
    }
    return out


@api_router.get("/admin/users")
async def admin_list_users(
    admin: User = Depends(require_perm("manage_users")),
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
async def admin_get_user(uid: str, admin: User = Depends(require_perm("manage_users"))):
    d = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return _public_user(d)


@api_router.post("/admin/users")
async def admin_create_user(payload: AdminUserCreate, request: Request,
                            admin: User = Depends(require_perm("manage_users"))):
    email = (payload.email or "").lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")
    valid_roles = await get_all_roles()
    if payload.role not in valid_roles:
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
        "work_areas": payload.work_areas or [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "created_by": admin.user_id,
    }
    if password_hash:
        doc["password_hash"] = password_hash
    await db.users.insert_one(doc)
    email_sent = await send_welcome_email(user_doc=doc, auth_provider=ap, request=request)
    return {**_public_user(doc), "email_sent": email_sent}


async def _count_active_admins() -> int:
    return await db.users.count_documents({
        "role": "admin", "active": True, "deleted_at": {"$exists": False},
    })


@api_router.patch("/admin/users/{uid}")
async def admin_update_user(uid: str, payload: AdminUserUpdate, admin: User = Depends(require_perm("manage_users"))):
    target = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    update: dict[str, Any] = {}
    if payload.name is not None:
        update["name"] = payload.name.strip()
    if payload.role is not None:
        valid_roles = await get_all_roles()
        if payload.role not in valid_roles:
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
    if payload.work_areas is not None:
        update["work_areas"] = payload.work_areas
    if not update:
        return _public_user(target)
    update["updated_at"] = now_iso()
    await db.users.update_one({"user_id": uid}, {"$set": update})
    d = await db.users.find_one({"user_id": uid}, {"_id": 0})
    return _public_user(d)


@api_router.post("/admin/users/{uid}/activate")
async def admin_activate(uid: str, admin: User = Depends(require_perm("manage_users"))):
    return await admin_update_user(uid, AdminUserUpdate(is_active=True), admin=admin)


@api_router.post("/admin/users/{uid}/deactivate")
async def admin_deactivate(uid: str, admin: User = Depends(require_perm("manage_users"))):
    return await admin_update_user(uid, AdminUserUpdate(is_active=False), admin=admin)


@api_router.post("/admin/users/{uid}/reset-password")
async def admin_reset_password(uid: str, request: Request,
                               admin: User = Depends(require_perm("manage_users"))):
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
    email_sent = await send_password_recovery_email(user_doc=target, request=request)
    return {"ok": True, "temporary_password": None if email_sent else temp, "email_sent": email_sent}


@api_router.delete("/admin/users/{uid}")
async def admin_delete_user(uid: str, admin: User = Depends(require_perm("manage_users"))):
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
async def admin_wa_config_get(request: Request, admin: User = Depends(require_perm("configure_whatsapp"))):
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
async def admin_wa_config_put(payload: WhatsAppConfigUpdate, admin: User = Depends(require_perm("configure_whatsapp"))):
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
async def admin_wa_test_connection(admin: User = Depends(require_perm("configure_whatsapp"))):
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
async def admin_wa_rotate_verify_token(admin: User = Depends(require_perm("configure_whatsapp"))):
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
async def admin_wa_test_webhook_verify(request: Request, admin: User = Depends(require_perm("configure_whatsapp"))):
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
async def update_user(user_id: str, payload: RoleUpdate, admin: User = Depends(require_perm("manage_users"))):
    valid_roles = await get_all_roles()
    if payload.role not in valid_roles:
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
    
    # Self-healing: ensure all returned contacts have a linked lead
    for d in docs:
        cid = d["id"]
        exist_lead = await db.leads.find_one({"contact_id": cid})
        if not exist_lead:
            lead = Lead(
                id=new_id("lead"),
                contact_id=cid,
                title=f"Lead de {d['name']}",
                status="new",
                priority="medium",
                value=0.0,
                assigned_to=user.user_id,
                tags=[],
                products=[],
            )
            await db.leads.insert_one(lead.model_dump())
            
    return [Contact(**d) for d in docs]


@api_router.post("/contacts", response_model=Contact)
async def create_contact(payload: ContactCreate, user: User = Depends(get_current_user)):
    contact = Contact(**payload.model_dump())
    await db.contacts.insert_one(contact.model_dump())
    
    # Automatically create a linked lead for the new contact
    lead = Lead(
        id=new_id("lead"),
        contact_id=contact.id,
        title=f"Lead de {contact.name}",
        status="new",
        priority="medium",
        value=0.0,
        assigned_to=user.user_id,
        tags=[],
        products=[],
    )
    await db.leads.insert_one(lead.model_dump())
    
    return contact


@api_router.get("/contacts/{contact_id}", response_model=Contact)
async def get_contact(contact_id: str, user: User = Depends(get_current_user)):
    doc = await db.contacts.find_one({"id": contact_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Contact not found")
    return Contact(**doc)


@api_router.patch("/contacts/{contact_id}", response_model=Contact)
async def update_contact(contact_id: str, payload: ContactUpdate, user: User = Depends(get_current_user)):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update:
        doc = await db.contacts.find_one({"id": contact_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Contact not found")
        return Contact(**doc)
    await db.contacts.update_one({"id": contact_id}, {"$set": update})
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
    role = _normalize_role(user.role)
    is_admin_or_supervisor = role in ("admin", "supervisor")
    if not is_admin_or_supervisor:
        q["assigned_to"] = user.user_id
    elif assigned_to:
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
    if lead.status == "won":
        from utils.sales import SaleError, close_sale
        try:
            closed = await close_sale(
                db,
                lead.model_dump(),
                [product.model_dump() for product in lead.products],
                user_id=user.user_id,
            )
            lead = Lead(**{**lead.model_dump(), **closed})
        except SaleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    existing = await db.leads.find_one({"id": lead_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Lead not found")
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    next_status = update.get("status", existing.get("status"))
    if existing.get("status") == "won" and next_status == "won" \
            and ({"products", "value"}.intersection(payload.model_fields_set)):
        raise HTTPException(
            status_code=409,
            detail="La venta ya está cerrada. Reabrila antes de modificar productos o importes",
        )
    if "assigned_to" in payload.model_fields_set:
        val = payload.assigned_to
        if val is None or (isinstance(val, str) and val.strip() == ""):
            update["assigned_to"] = None
        else:
            update["assigned_to"] = str(val).strip()
            
    if "products" in payload.model_fields_set:
        products_list = payload.products or []
        update["products"] = [p.model_dump() for p in products_list]
        update["value"] = sum(float(p.price) * int(p.quantity) for p in products_list)

    if existing.get("status") != "won" and next_status == "won":
        from utils.sales import SaleError, close_sale
        sold_products = update.get("products", existing.get("products") or [])
        try:
            update.update(await close_sale(
                db, {**existing, **update}, sold_products, user_id=user.user_id
            ))
        except SaleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    elif existing.get("status") == "won" and next_status != "won":
        from utils.sales import reverse_sale
        update["sale_snapshot"] = await reverse_sale(
            db, existing.get("sale_snapshot"), user_id=user.user_id
        )
        
    update["updated_at"] = now_iso()
    await db.leads.update_one({"id": lead_id}, {"$set": update})
    doc = await db.leads.find_one({"id": lead_id}, {"_id": 0})
    # Sync assigned_to to conversation
    if "assigned_to" in update:
        await db.conversations.update_one({"lead_id": lead_id}, {"$set": {"assigned_to": update["assigned_to"]}})
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

    # Send email notification if enabled and it is an unattended lead alert
    if ntype == "lead_no_response":
        settings = await get_app_settings()
        if settings.get("email_notif_unattended_enabled", True):
            user = await db.users.find_one({"user_id": user_id})
            if user and user.get("email"):
                to_email = user["email"].strip().lower()
                if "@" in to_email:
                    base_url = _resolve_app_base_url(settings)
                    link = f"{base_url}/chat" if entity_type == "conversation" else base_url
                    cname = title.replace("Lead sin respuesta: ", "")
                    html_content = _build_password_email_html(
                        title="⚠️ Lead sin atender",
                        intro=f"Hola {user.get('name', 'Usuario')}, el cliente <strong>{cname}</strong> requiere atención urgente. Lleva más de {settings.get('lead_no_response_threshold_hours', 2)} horas sin recibir respuesta.",
                        cta_label="Ver Conversación",
                        cta_url=link,
                        footer="Recibiste esta notificación porque estás a cargo de este lead o sos administrador del sistema."
                    )
                    await send_email_via_settings(
                        to_email=to_email,
                        subject=f"⚠️ {title}",
                        html_body=html_content,
                        text_body=f"Hola {user.get('name', 'Usuario')}, {body}. Podés verlo en {link}"
                    )


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
    "task_statuses": [
        {"key": "todo", "label": "Pendiente", "is_done": False},
        {"key": "in_progress", "label": "En progreso", "is_done": False},
        {"key": "done", "label": "Completada", "is_done": True},
    ],
    "catalog_categories": [],
    "catalog_category_colors": {},
    "smtp_enabled": bool(RESEND_API_KEY and RESEND_FROM_EMAIL),
    "smtp_host": "smtp.resend.com" if RESEND_API_KEY else "",
    "smtp_port": 465 if RESEND_API_KEY else 587,
    "smtp_username": "resend" if RESEND_API_KEY else "",
    "smtp_password": RESEND_API_KEY,
    "smtp_from_email": RESEND_FROM_EMAIL,
    "smtp_from_name": RESEND_FROM_NAME,
    "smtp_use_tls": False if RESEND_API_KEY else True,
    "smtp_use_ssl": True if RESEND_API_KEY else False,
    "app_base_url": APP_BASE_URL,
    "email_notif_unattended_enabled": True,
    "email_report_daily_enabled": True,
    "email_report_weekly_enabled": True,
    "email_report_monthly_enabled": True,
    "last_daily_report_at": "",
    "last_weekly_report_at": "",
    "last_monthly_report_at": "",
}

PUBLIC_SETTINGS_KEYS = {
    "lead_no_response_enabled",
    "lead_no_response_threshold_hours",
    "lead_no_response_business_hours_only",
    "business_hours_start",
    "business_hours_end",
    "business_days",
    "business_timezone",
    "task_statuses",
    "catalog_categories",
    "catalog_category_colors",
    "email_notif_unattended_enabled",
    "email_report_daily_enabled",
    "email_report_weekly_enabled",
    "email_report_monthly_enabled",
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
    task_statuses: Optional[List[dict[str, Any]]] = None
    catalog_categories: Optional[List[str]] = None
    catalog_category_colors: Optional[dict[str, str]] = None
    smtp_enabled: Optional[bool] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from_name: Optional[str] = None
    smtp_use_tls: Optional[bool] = None
    smtp_use_ssl: Optional[bool] = None
    app_base_url: Optional[str] = None
    email_notif_unattended_enabled: Optional[bool] = None
    email_report_daily_enabled: Optional[bool] = None
    email_report_weekly_enabled: Optional[bool] = None
    email_report_monthly_enabled: Optional[bool] = None
    last_daily_report_at: Optional[str] = None
    last_weekly_report_at: Optional[str] = None
    last_monthly_report_at: Optional[str] = None


def _slugify_config_key(value: str) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "_" for ch in (value or "").strip())
    parts = [p for p in raw.split("_") if p]
    return "_".join(parts)


def _normalize_task_statuses(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        label = str(item.get("label") or "").strip()
        key = _slugify_config_key(str(item.get("key") or label))
        if not label or not key or key in seen:
            continue
        status_dict = {
            "key": key,
            "label": label,
            "is_done": bool(item.get("is_done", False)),
        }
        if "color" in item:
            status_dict["color"] = str(item["color"] or "").strip()
        if "bg" in item:
            status_dict["bg"] = str(item["bg"] or "").strip()
        normalized.append(status_dict)
        seen.add(key)
    if not normalized:
        normalized = [dict(x) for x in DEFAULT_SETTINGS["task_statuses"]]
    if not any(item["is_done"] for item in normalized):
        normalized[-1]["is_done"] = True
    return normalized


def _normalize_catalog_categories(items: list[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for item in items or []:
        value = str(item or "").strip()
        lowered = value.lower()
        if not value or lowered in seen:
            continue
        normalized.append(value)
        seen.add(lowered)
    return sorted(normalized, key=lambda x: x.lower())


def _task_status_map(settings: dict) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in settings.get("task_statuses", [])}


def _normalize_optional_url(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if not (text.startswith("http://") or text.startswith("https://")):
        raise ValueError("La URL base debe empezar con http:// o https://")
    return text.rstrip("/")


async def validate_task_status(status: str) -> str:
    settings = await get_app_settings()
    allowed = _task_status_map(settings)
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Estado de tarea inválido")
    return status


async def get_task_done_statuses() -> set[str]:
    settings = await get_app_settings()
    return {item["key"] for item in settings.get("task_statuses", []) if item.get("is_done")}


def _public_settings_view(settings: dict) -> dict:
    return {key: settings.get(key) for key in PUBLIC_SETTINGS_KEYS}


def _admin_settings_view(settings: dict) -> dict:
    return {
        **settings,
        "smtp_password": "",
        "smtp_password_configured": bool(settings.get("smtp_password")),
    }


async def get_app_settings() -> dict:
    doc = await db.settings.find_one({"key": "app"}, {"_id": 0})
    s = dict(DEFAULT_SETTINGS)
    if doc:
        for k in DEFAULT_SETTINGS:
            if k in doc and doc[k] is not None:
                s[k] = doc[k]
    s["task_statuses"] = _normalize_task_statuses(s.get("task_statuses"))
    s["catalog_categories"] = _normalize_catalog_categories(s.get("catalog_categories"))
    return s


@api_router.get("/settings")
async def read_settings(user: User = Depends(get_current_user)):
    return _public_settings_view(await get_app_settings())


@api_router.get("/admin/settings")
async def read_admin_settings(admin: User = Depends(require_perm("manage_settings"))):
    return _admin_settings_view(await get_app_settings())


@api_router.patch("/admin/settings")
async def update_settings(payload: SettingsUpdate, admin: User = Depends(require_perm("manage_settings"))):
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
    if "task_statuses" in update:
        update["task_statuses"] = _normalize_task_statuses(update["task_statuses"])
    if "catalog_categories" in update:
        update["catalog_categories"] = _normalize_catalog_categories(update["catalog_categories"])
    if "smtp_port" in update:
        update["smtp_port"] = max(1, int(update["smtp_port"]))
    if "smtp_host" in update:
        update["smtp_host"] = (update["smtp_host"] or "").strip()
    if "smtp_username" in update:
        update["smtp_username"] = (update["smtp_username"] or "").strip()
    if "smtp_from_email" in update:
        update["smtp_from_email"] = (update["smtp_from_email"] or "").strip().lower()
    if "smtp_from_name" in update:
        update["smtp_from_name"] = (update["smtp_from_name"] or "").strip() or "Latus CRM"
    if "app_base_url" in update:
        try:
            update["app_base_url"] = _normalize_optional_url(update["app_base_url"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if "smtp_use_ssl" in update and update.get("smtp_use_ssl"):
        update["smtp_use_tls"] = False
    if "smtp_use_tls" in update and update.get("smtp_use_tls"):
        update["smtp_use_ssl"] = False
    if "smtp_password" in update and update["smtp_password"] == "":
        update.pop("smtp_password")
    await db.settings.update_one({"key": "app"}, {"$set": {"key": "app", **update}}, upsert=True)
    return _admin_settings_view(await get_app_settings())


@api_router.post("/admin/settings/email/test")
async def send_test_email(payload: SendTestEmailBody, admin: User = Depends(require_perm("manage_settings"))):
    to_email = (payload.to_email or "").strip().lower()
    if not to_email or "@" not in to_email:
        raise HTTPException(status_code=400, detail="Email de destino inválido")
    ok = await send_email_via_settings(
        to_email=to_email,
        subject="Prueba de email de Latus CRM",
        html_body=_build_password_email_html(
            title="SMTP configurado correctamente",
            intro="Este es un correo de prueba enviado desde la configuración de Latus CRM.",
            cta_label="Abrir CRM",
            cta_url=_resolve_app_base_url(await get_app_settings()),
            footer=f"Enviado por {admin.name or admin.email}. Si lo recibiste, Resend ya quedó conectado.",
        ),
        text_body="Este es un correo de prueba enviado desde la configuración de Latus CRM.",
    )
    if not ok:
        raise HTTPException(status_code=400, detail="No se pudo enviar el email de prueba. Revisá la clave, el remitente y el dominio verificado en Resend.")
    return {"ok": True}


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
        if not contact.get("lead_source"):
            upd["lead_source"] = "WhatsApp"
        await db.contacts.update_one({"id": contact["id"]}, {"$set": upd})
        contact = await db.contacts.find_one({"id": contact["id"]}, {"_id": 0})
        return contact
    # create new contact
    new_contact = Contact(
        name=profile_name or f"+{wa_id}",
        phone=f"+{wa_id}",
    ).model_dump()
    new_contact["whatsapp_id"] = wa_id
    new_contact["lead_source"] = "WhatsApp"
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

    # Check for Meta Ads click to WhatsApp referral payload
    referral = m.raw.get("referral") if m.raw else None
    if referral:
        has_existing_campaign = bool(contact.get("meta_ad_id"))
        upd: dict[str, Any] = {"raw_referral": referral}
        if not has_existing_campaign:
            upd["lead_source"] = "Meta Ads"
            upd["meta_ad_id"] = referral.get("source_id")
            upd["meta_ad_url"] = referral.get("source_url")
            upd["meta_source_type"] = referral.get("source_type")
            upd["meta_ad_title"] = referral.get("headline")
            upd["meta_ad_body"] = referral.get("body")
            upd["meta_ad_media_type"] = referral.get("media_type")
            upd["meta_ad_image_url"] = referral.get("image_url")
            upd["meta_ctwa_clid"] = referral.get("ctwa_clid")
            upd["first_message_from_ad"] = m.text or f"[{m.message_type}]"
            upd["first_ad_message_at"] = m.timestamp or now_iso()
        
        await db.contacts.update_one({"id": contact["id"]}, {"$set": upd})
        contact.update(upd)

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


class WhatsAppTemplateSend(BaseModel):
    template_id: str
    appointment_id: Optional[str] = None


WHATSAPP_CUSTOMER_SERVICE_WINDOW_HOURS = 24


def _whatsapp_window_state_from_messages(messages: list[dict]) -> dict:
    """Determine whether Meta still allows a free-form reply to the customer."""
    inbound = [
        message for message in messages
        if message.get("sender_type") == "contact" or message.get("direction") == "inbound"
    ]
    inbound.sort(key=lambda message: str(message.get("created_at") or ""), reverse=True)
    last_message_at = inbound[0].get("created_at") if inbound else None
    if not last_message_at:
        return {
            "whatsapp_free_text_allowed": False,
            "whatsapp_window_status": "no_customer_message",
            "whatsapp_window_expires_at": None,
            "last_customer_message_at": None,
        }
    try:
        last_message = datetime.fromisoformat(str(last_message_at).replace("Z", "+00:00"))
        if last_message.tzinfo is None:
            last_message = last_message.replace(tzinfo=timezone.utc)
        last_message = last_message.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return {
            "whatsapp_free_text_allowed": False,
            "whatsapp_window_status": "unknown",
            "whatsapp_window_expires_at": None,
            "last_customer_message_at": last_message_at,
        }
    expires_at = last_message + timedelta(hours=WHATSAPP_CUSTOMER_SERVICE_WINDOW_HOURS)
    allowed = datetime.now(timezone.utc) < expires_at
    return {
        "whatsapp_free_text_allowed": allowed,
        "whatsapp_window_status": "open" if allowed else "expired",
        "whatsapp_window_expires_at": expires_at.isoformat(),
        "last_customer_message_at": last_message.isoformat(),
    }


async def _whatsapp_window_state(conv_id: str) -> dict:
    messages = await db.messages.find(
        {"conversation_id": conv_id, "sender_type": "contact"}, {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    return _whatsapp_window_state_from_messages(messages)


def _wa_config_for_conversation(cfg, conv: dict):
    """Reply from the phone number that actually received this conversation."""
    external = str(conv.get("channel_external_id") or "")
    phone_number_id = external.split(":", 1)[0].strip() if ":" in external else ""
    return replace(cfg, phone_number_id=phone_number_id) if phone_number_id else cfg


def _wa_external_message_id(result: dict) -> str:
    try:
        return (result.get("messages") or [{}])[0].get("id") or ""
    except Exception:
        return ""


async def _template_context_for(
    contact: dict,
    appointment: dict | None,
    settings: dict,
) -> tuple[dict, dict]:
    from whatsapp.templates import build_template_context
    assigned_user = {}
    if appointment and appointment.get("assigned_to"):
        assigned_user = await db.users.find_one(
            {"user_id": appointment["assigned_to"]}, {"_id": 0, "name": 1}
        ) or {}
    context = build_template_context(
        contact=contact,
        appointment=appointment,
        assigned_user=assigned_user,
        timezone_name=settings.get("appointment_timezone") or "America/Argentina/Buenos_Aires",
    )
    return context, assigned_user


async def _send_configured_whatsapp_template(
    *,
    conv: dict,
    contact: dict,
    template: dict,
    settings: dict,
    appointment: dict | None,
    sender_type: str,
    sender_name: str,
) -> dict:
    from whatsapp.templates import render_template_preview, template_parameter_values
    cfg = _wa_config_for_conversation(await wa_config_effective(db), conv)
    if not cfg.is_configured:
        raise HTTPException(status_code=503, detail="WhatsApp no configurado")
    wa_id = contact.get("whatsapp_id") or "".join(
        character for character in (contact.get("phone") or "") if character.isdigit()
    )
    if not wa_id:
        raise HTTPException(status_code=400, detail="El cliente no tiene un WhatsApp asociado")
    context, _ = await _template_context_for(contact, appointment, settings)
    try:
        result = await send_template_message(
            cfg,
            wa_id,
            template_name=template["name"],
            language=template.get("language") or "es_AR",
            body_parameters=template_parameter_values(template, context),
        )
    except WhatsAppSendError as exc:
        await _wa_record_send_error(code=exc.error_code, message=exc.error_message or "")
        raise HTTPException(
            status_code=502,
            detail=f"Meta rechazó la plantilla: {exc.error_message or 'Error desconocido'}",
        ) from exc
    body = render_template_preview(template, context)
    msg_doc = {
        "id": new_id("msg"),
        "conversation_id": conv["id"],
        "sender_type": sender_type,
        "sender_name": sender_name,
        "body": body,
        "created_at": now_iso(),
        "direction": "outbound",
        "delivery_status": "sent",
        "external_message_id": _wa_external_message_id(result),
        "message_type": "template",
        "channel": "whatsapp",
        "template_id": template.get("id"),
        "template_name": template.get("name"),
        "template_language": template.get("language"),
        "appointment_id": appointment.get("id") if appointment else None,
    }
    await db.messages.insert_one(msg_doc)
    await db.conversations.update_one(
        {"id": conv["id"]},
        {"$set": {"last_message": body, "last_message_at": msg_doc["created_at"], "channel": "whatsapp"}},
    )
    return _strip_oid(msg_doc)


@api_router.get("/conversations/{conv_id}/whatsapp-templates")
async def list_conversation_whatsapp_templates(
    conv_id: str,
    user: User = Depends(get_current_user),
):
    from whatsapp.templates import build_template_context, render_template_preview
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    perms = await get_role_permissions(user.role)
    if "message_any" not in perms and conv.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="No podés enviar mensajes en esta conversación")
    contact = await db.contacts.find_one({"id": conv["contact_id"]}, {"_id": 0}) or {}
    settings = await _effective_bot_settings()
    context = build_template_context(
        contact=contact,
        timezone_name=settings.get("appointment_timezone") or "America/Argentina/Buenos_Aires",
    )
    templates = []
    for template in settings.get("whatsapp_recontact_templates") or []:
        if template.get("active") is False:
            continue
        templates.append({**template, "rendered_preview": render_template_preview(template, context)})
    return {"templates": templates}


@api_router.post("/conversations/{conv_id}/send-whatsapp-template")
async def send_conversation_whatsapp_template(
    conv_id: str,
    payload: WhatsAppTemplateSend,
    user: User = Depends(get_current_user),
):
    from whatsapp.templates import find_template
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    perms = await get_role_permissions(user.role)
    if "message_any" not in perms and conv.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="No podés enviar mensajes en esta conversación")
    settings = await _effective_bot_settings()
    template = find_template(settings, payload.template_id, purpose="recontact")
    if not template:
        raise HTTPException(status_code=404, detail="La plantilla de recontacto no existe o está inactiva")
    contact = await db.contacts.find_one({"id": conv["contact_id"]}, {"_id": 0}) or {}
    return await _send_configured_whatsapp_template(
        conv=conv,
        contact=contact,
        template=template,
        settings=settings,
        appointment=None,
        sender_type="agent",
        sender_name=user.name,
    )


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
    cfg = _wa_config_for_conversation(cfg, conv)
    perms = await get_role_permissions(user.role)
    if "message_any" not in perms and conv.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="Solo el operador asignado o un usuario con permisos pueden enviar mensajes a esta conversación")
    window = await _whatsapp_window_state(conv_id)
    if not window["whatsapp_free_text_allowed"]:
        raise HTTPException(
            status_code=409,
            detail="La ventana de respuesta de WhatsApp venció. Para evitar el Error #131047, enviá una plantilla de recontacto.",
        )
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
    external_id = _wa_external_message_id(result)
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
    perms = await get_role_permissions(user.role)
    if "configure_whatsapp" not in perms:
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
    assigned_work_area: Optional[str] = None,
):
    q = {}
    if status:
        q["status"] = status
    if priority:
        q["priority"] = priority
    role = _normalize_role(user.role)
    is_admin_or_supervisor = role in ("admin", "supervisor")
    if not is_admin_or_supervisor:
        q["assigned_to"] = user.user_id
    elif assigned_to:
        q["assigned_to"] = assigned_to
        
    if assigned_work_area:
        if assigned_work_area == "unassigned":
            q["assigned_work_area"] = {"$in": [None, ""]}
        else:
            q["assigned_work_area"] = assigned_work_area

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
    if doc.get("channel") == "whatsapp":
        doc.update(_whatsapp_window_state_from_messages(doc["messages"]))
    await db.conversations.update_one({"id": conv_id}, {"$set": {"unread": 0}})
    return doc


@api_router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: str, payload: MessageCreate, user: User = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    perms = await get_role_permissions(user.role)
    if "message_any" not in perms and conv.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="Solo el operador asignado o un usuario con permisos pueden enviar mensajes a esta conversación")
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
        set_fields["bot_enabled"] = True
        set_fields["bot_status"] = "bot_activo"
        set_fields["human_required_reason"] = None
        await _log_system_message(db, conv["id"], "Bot reactivado - Control de bot encendido (Reapertura de chat)")
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
    cfg = _wa_config_for_conversation(await wa_config_effective(db), conv)
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
    default_handoff_user_id: Optional[str] = None
    company_context: Optional[str] = None
    response_instructions: Optional[str] = None
    catalog_reading_enabled: Optional[bool] = None
    api_keys: Optional[dict] = None
    bot_inactive_close_hours: Optional[int] = None
    appointment_scheduling_enabled: Optional[bool] = None
    appointment_available_days: Optional[List[int]] = None
    appointment_business_hours: Optional[str] = None
    appointment_duration_minutes: Optional[int] = None
    appointment_mode: Optional[Literal["people", "business"]] = None
    appointment_timezone: Optional[str] = None
    appointment_services: Optional[List[dict]] = None
    whatsapp_recontact_templates: Optional[List[dict]] = None
    appointment_reminders_enabled: Optional[bool] = None
    appointment_reminder_minutes_before: Optional[int] = None
    appointment_reminder_templates: Optional[List[dict]] = None
    appointment_reminder_template_id: Optional[str] = None
    appointment_rescheduling_enabled: Optional[bool] = None


_ALLOWED_BOT_MODELS = {
    "gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet-20241022",
    "gemini-2.0-flash", "gemini-1.5-flash", "claude-sonnet-4-6"
}


async def _log_system_message(db, conv_id: str, text: str):
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    msg_doc = {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "conversation_id": conv_id,
        "sender_type": "system",
        "sender_name": "Sistema",
        "body": text,
        "created_at": now,
        "direction": "outbound",
        "delivery_status": "sent",
        "message_type": "text",
        "channel": "whatsapp",
    }
    await db.messages.insert_one(msg_doc)


@api_router.get("/admin/bot-settings")
async def admin_get_bot_settings(admin: User = Depends(require_perm("configure_ai"))):
    from ai.pipeline import DEFAULT_BOT_SETTINGS
    from ai import providers as ai_providers
    doc = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
    # Build keys_status for the bot's keys
    keys_status = {}
    for prov in ai_providers.KEY_REQUIRED_PROVIDERS:
        raw = await ai_providers._resolve_bot_api_key(db, prov)
        keys_status[prov] = {
            "configured": bool(raw),
            "masked": ai_providers.mask_key(raw)
        }
    return {
        **DEFAULT_BOT_SETTINGS,
        **doc,
        "keys_status": keys_status
    }


@api_router.patch("/admin/bot-settings")
async def admin_patch_bot_settings(payload: BotSettingsUpdate,
                                   admin: User = Depends(require_perm("configure_ai"))):
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
    if "default_handoff_user_id" in update:
        uid = update["default_handoff_user_id"]
        if uid:
            uid = uid.strip()
            user_doc = await db.users.find_one({"user_id": uid})
            if not user_doc:
                raise HTTPException(400, "El usuario asignado para derivación no existe")
            update["default_handoff_user_id"] = uid
        else:
            update["default_handoff_user_id"] = None
    if "company_context" in update:
        update["company_context"] = str(update["company_context"])
        update["business_instructions"] = update["company_context"]
    if "response_instructions" in update:
        update["response_instructions"] = str(update["response_instructions"])
    if "catalog_reading_enabled" in update:
        update["catalog_reading_enabled"] = bool(update["catalog_reading_enabled"])
    if "bot_inactive_close_hours" in update:
        val = update["bot_inactive_close_hours"]
        if val is not None:
            try:
                val = int(val)
                if not (1 <= val <= 168):
                    raise ValueError()
                update["bot_inactive_close_hours"] = val
            except Exception:
                raise HTTPException(400, "El cierre automático debe ser entre 1 y 168 horas")
    if "appointment_timezone" in update or "appointment_services" in update:
        from utils.scheduling import SchedulingError, normalize_services, validate_timezone
        current = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
        try:
            timezone_name = validate_timezone(
                update.get("appointment_timezone") or current.get("appointment_timezone")
            )
            update["appointment_timezone"] = timezone_name
            if "appointment_services" in update:
                update["appointment_services"] = normalize_services(
                    update["appointment_services"], timezone_name
                )
        except SchedulingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "appointment_duration_minutes" in update:
        duration = int(update["appointment_duration_minutes"])
        if not (5 <= duration <= 480):
            raise HTTPException(400, "La duración debe ser de 5 a 480 minutos")
        update["appointment_duration_minutes"] = duration
    if "whatsapp_recontact_templates" in update or "appointment_reminder_templates" in update:
        from whatsapp.templates import normalize_templates
        try:
            if "whatsapp_recontact_templates" in update:
                update["whatsapp_recontact_templates"] = normalize_templates(
                    update["whatsapp_recontact_templates"], purpose="recontact"
                )
            if "appointment_reminder_templates" in update:
                update["appointment_reminder_templates"] = normalize_templates(
                    update["appointment_reminder_templates"], purpose="appointment_reminder"
                )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "appointment_reminder_minutes_before" in update:
        try:
            reminder_minutes = int(update["appointment_reminder_minutes_before"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "La anticipación del recordatorio debe ser un número") from exc
        if not (5 <= reminder_minutes <= 43200):
            raise HTTPException(400, "La anticipación debe estar entre 5 minutos y 30 días")
        update["appointment_reminder_minutes_before"] = reminder_minutes
    if "appointment_reminder_template_id" in update:
        update["appointment_reminder_template_id"] = (
            str(update["appointment_reminder_template_id"] or "").strip() or None
        )
    if {"appointment_reminders_enabled", "appointment_reminder_templates",
        "appointment_reminder_template_id"}.intersection(update):
        from ai.pipeline import DEFAULT_BOT_SETTINGS
        current = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
        effective = {**DEFAULT_BOT_SETTINGS, **current, **update}
        if effective.get("appointment_reminders_enabled"):
            active_templates = [
                template for template in effective.get("appointment_reminder_templates") or []
                if template.get("active") is not False
            ]
            if not active_templates:
                raise HTTPException(400, "Agregá al menos una plantilla de recordatorio activa")
            selected_id = effective.get("appointment_reminder_template_id")
            if not selected_id or not any(template.get("id") == selected_id for template in active_templates):
                raise HTTPException(400, "Seleccioná la plantilla predeterminada para recordatorios")
    if {"appointment_scheduling_enabled", "appointment_mode", "appointment_services"}.intersection(update):
        from utils.scheduling import SchedulingError, normalize_services
        current = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
        effective = {**current, **update}
        if effective.get("appointment_scheduling_enabled") and effective.get("appointment_mode") == "business":
            try:
                services = normalize_services(
                    effective.get("appointment_services") or [],
                    effective.get("appointment_timezone"),
                )
            except SchedulingError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not any(service["active"] for service in services):
                raise HTTPException(
                    status_code=400,
                    detail="Agregá al menos un servicio activo antes de habilitar las citas en el local",
                )
    if "api_keys" in update:
        api_keys = update.pop("api_keys")
        if isinstance(api_keys, dict):
            from ai import providers as ai_providers
            clean_keys = {}
            for k, v in api_keys.items():
                if k in ai_providers.KEY_REQUIRED_PROVIDERS:
                    clean_keys[k] = str(v) if v is not None else None
            if clean_keys:
                await ai_providers.save_bot_api_keys(db, clean_keys, user_id=admin.user_id)
    update["updated_at"] = now_iso()
    update["updated_by"] = admin.user_id
    await db.bot_settings.update_one({"_id": "default"},
                                     {"$set": {"_id": "default", **update}}, upsert=True)
    return await admin_get_bot_settings(admin)


# ---------------------------------------------------------------------------
# AI provider settings (multi-provider configuration)
# ---------------------------------------------------------------------------


@api_router.get("/admin/ai-provider")
async def admin_get_ai_provider(admin: User = Depends(require_perm("configure_ai"))):
    from ai import providers as ai_providers
    s = await ai_providers.load_settings(db)
    # Build keys_status for all key-required providers
    keys_status = {}
    for prov in ai_providers.KEY_REQUIRED_PROVIDERS:
        raw = await ai_providers._resolve_api_key(db, prov)
        keys_status[prov] = {
            "configured": bool(raw),
            "masked": ai_providers.mask_key(raw)
        }
    provider = s.get("provider", "built_in")
    masked = keys_status.get(provider, {}).get("masked", "")
    return {
        **{k: s[k] for k in ai_providers.DEFAULTS.keys()},
        "api_key_configured": s.get("api_key_configured", False),
        "api_key_masked": masked,
        "keys_status": keys_status,
        "model_suggestions": ai_providers.MODEL_SUGGESTIONS,
        "supported_providers": list(ai_providers.SUPPORTED_PROVIDERS),
        "updated_at": s.get("updated_at"),
        "updated_by": s.get("updated_by"),
    }


@api_router.put("/admin/ai-provider")
async def admin_put_ai_provider(payload: dict = Body(...),
                                admin: User = Depends(require_perm("configure_ai"))):
    from ai import providers as ai_providers
    current = await ai_providers.load_settings(db)
    try:
        clean = ai_providers.validate_patch(payload, current)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await ai_providers.save_settings(db, clean, user_id=admin.user_id)
    return await admin_get_ai_provider(admin)


@api_router.post("/admin/ai-provider/test")
async def admin_test_ai_provider(admin: User = Depends(require_perm("configure_ai"))):
    from ai import providers as ai_providers
    return await ai_providers.test_provider_connectivity(db)


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
                       status: str | None, conversation_id: str | None = None,
                       provider: str | None = None) -> dict:
    q: dict = {"created_at": {"$gte": from_iso, "$lte": to_iso}}
    if model:           q["model"] = model
    if status:          q["status"] = status
    if conversation_id: q["conversation_id"] = conversation_id
    if provider:        q["provider"] = provider
    return q


@api_router.get("/admin/ai-usage/summary")
async def admin_ai_usage_summary(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    model: str | None = None,
    status: str | None = None,
    provider: str | None = None,
    admin: User = Depends(require_perm("configure_ai")),
):
    f, t = _date_bounds(from_, to)
    q = _build_usage_filter(f, t, model, status, provider=provider)
    logs = await db.ai_usage_logs.find(q, {"_id": 0}).to_list(50_000)
    total_calls = len(logs)
    success_calls = sum(1 for l in logs if l.get("status") == "success")
    error_calls = total_calls - success_calls
    total_tokens = sum(int(l.get("total_tokens") or 0) for l in logs)
    total_cost = round(sum(float(l.get("estimated_cost_usd") or 0.0) for l in logs), 6)
    provider_cost = round(sum(float(l.get("provider_cost_usd") or 0.0) for l in logs), 6)
    provider_cost_calls = sum(1 for l in logs if l.get("provider_cost_usd") is not None)
    token_measured_calls = sum(1 for l in logs if l.get("status") == "success" and int(l.get("total_tokens") or 0) > 0)

    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_conv: dict[str, dict] = {}
    for l in logs:
        m = l.get("model") or "unknown"
        bm = by_model.setdefault(m, {"model": m, "calls": 0, "tokens": 0, "cost_usd": 0.0, "provider_cost_usd": 0.0})
        bm["calls"] += 1
        bm["tokens"] += int(l.get("total_tokens") or 0)
        bm["cost_usd"] = round(bm["cost_usd"] + float(l.get("estimated_cost_usd") or 0.0), 6)
        bm["provider_cost_usd"] = round(bm["provider_cost_usd"] + float(l.get("provider_cost_usd") or 0.0), 6)

        d = (l.get("created_at") or "")[:10]
        bd = by_day.setdefault(d, {"date": d, "calls": 0, "tokens": 0, "cost_usd": 0.0, "provider_cost_usd": 0.0})
        bd["calls"] += 1
        bd["tokens"] += int(l.get("total_tokens") or 0)
        bd["cost_usd"] = round(bd["cost_usd"] + float(l.get("estimated_cost_usd") or 0.0), 6)
        bd["provider_cost_usd"] = round(bd["provider_cost_usd"] + float(l.get("provider_cost_usd") or 0.0), 6)

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
        "estimated_cost_usd": total_cost,
        "provider_cost_usd": provider_cost,
        "provider_cost_calls": provider_cost_calls,
        "token_measured_calls": token_measured_calls,
        "measurement": {
            "tokens": "provider_response",
            "cost": "mixed" if provider_cost_calls else "estimated",
            "token_coverage_pct": round(token_measured_calls * 100.0 / success_calls, 1) if success_calls else 0.0,
            "provider_cost_coverage_pct": round(provider_cost_calls * 100.0 / success_calls, 1) if success_calls else 0.0,
        },
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
    provider: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_perm("configure_ai")),
):
    f, t = _date_bounds(from_, to)
    q = _build_usage_filter(f, t, model, status, conversation_id, provider)
    total = await db.ai_usage_logs.count_documents(q)
    items = await db.ai_usage_logs.find(q, {"_id": 0}) \
        .sort("created_at", -1).to_list(offset + limit)
    return {"items": items[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset}


@api_router.get("/admin/ai-usage/quick")
async def admin_ai_usage_quick(admin: User = Depends(require_perm("configure_ai"))):
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
            "provider_cost_usd": round(sum(float(i.get("provider_cost_usd") or 0.0) for i in items), 6),
            "provider_cost_calls": sum(1 for i in items if i.get("provider_cost_usd") is not None),
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


class AIUsageReportingKeyBody(BaseModel):
    key: Optional[str] = None


@api_router.get("/admin/ai-usage/provider-reporting")
async def admin_ai_usage_provider_reporting(
    admin: User = Depends(require_perm("configure_ai")),
):
    from ai import provider_usage
    return await provider_usage.reporting_status(db)


@api_router.put("/admin/ai-usage/provider-reporting/{provider}")
async def admin_ai_usage_provider_reporting_put(
    provider: str,
    payload: AIUsageReportingKeyBody,
    admin: User = Depends(require_perm("configure_ai")),
):
    from ai import provider_usage
    try:
        await provider_usage.save_reporting_key(db, provider, payload.key, admin.user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return await provider_usage.reporting_status(db)


@api_router.post("/admin/ai-usage/provider-report")
async def admin_ai_usage_provider_report(
    provider: str,
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    admin: User = Depends(require_perm("configure_ai")),
):
    from ai import provider_usage
    try:
        return await provider_usage.fetch_provider_report(db, provider, from_, to)
    except provider_usage.ProviderUsageError as exc:
        raise HTTPException(400, str(exc))


@api_router.get("/admin/ai-pricing")
async def admin_ai_pricing_get(admin: User = Depends(require_perm("configure_ai"))):
    from ai import usage as ai_usage
    pricing = await ai_usage.load_pricing(db)
    return {"models": pricing, "defaults": ai_usage.DEFAULT_PRICING}


class AIPriceItem(BaseModel):
    model: str
    input_per_million: float
    output_per_million: float


@api_router.put("/admin/ai-pricing")
async def admin_ai_pricing_put(item: AIPriceItem,
                               admin: User = Depends(require_perm("configure_ai"))):
    from ai import usage as ai_usage
    try:
        result = await ai_usage.save_pricing(db, item.model, item.input_per_million,
                                             item.output_per_million,
                                             user_id=admin.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"models": result, "defaults": ai_usage.DEFAULT_PRICING}


@api_router.post("/admin/ai-pricing/reset")
async def admin_ai_pricing_reset(admin: User = Depends(require_perm("configure_ai"))):
    from ai import usage as ai_usage
    result = await ai_usage.reset_pricing(db, user_id=admin.user_id)
    return {"models": result, "defaults": ai_usage.DEFAULT_PRICING}


# ---------------------------------------------------------------------------
# Catalog (products) — Phase 3
# ---------------------------------------------------------------------------


async def require_catalog_writer(user: User = Depends(get_current_user)) -> User:
    perms = await get_role_permissions(user.role)
    if "write_catalog" not in perms:
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
    from catalog import build_listing_query, product_view
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
    return {"items": [product_view(item) for item in items[offset:offset + limit]], "total": total,
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
    from catalog import product_view
    p = await db.products.find_one(
        {"product_id": product_id, "deleted_at": None},
        {"_id": 0, "deleted_at": 0})
    if not p:
        raise HTTPException(404, "Producto no encontrado")
    return product_view(p)


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
    settings = await get_app_settings()
    configured = settings.get("catalog_categories", [])
    cats = await db.products.distinct("category", {"deleted_at": None,
                                                   "category": {"$ne": None}})
    merged = _normalize_catalog_categories([*configured, *[c for c in cats if c]])
    return {"categories": merged}


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
    perms = await get_role_permissions(user.role)
    if "write_crm" not in perms:
        return False
    if "trigger_bot_any" in perms:
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
    await _log_system_message(db, conv_id, f"Bot reactivado - Control de bot encendido (Agente: {user.name})")
    return await db.conversations.find_one({"id": conv_id}, {"_id": 0})


@api_router.post("/conversations/{conv_id}/bot/deactivate")
async def bot_deactivate(conv_id: str, user: User = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if not await _can_use_bot_for_conv(conv, user):
        raise HTTPException(403, "Sin permisos")
    await db.conversations.update_one({"id": conv_id}, {"$set": {
        "bot_enabled": False, "bot_status": "en_atencion_humana",
        "human_required_reason": "Desactivado por agente",
    }})
    await _log_system_message(db, conv_id, f"Control humano activado - Bot apagado (Agente: {user.name})")
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
    if "assigned_to" in payload.model_fields_set:
        val = payload.assigned_to
        if val is None or (isinstance(val, str) and val.strip() == ""):
            update["assigned_to"] = None
        else:
            update["assigned_to"] = str(val).strip()
    if "assigned_work_area" in payload.model_fields_set:
        val = payload.assigned_work_area
        if val is None or (isinstance(val, str) and val.strip() == ""):
            update["assigned_work_area"] = None
        else:
            update["assigned_work_area"] = str(val).strip()

    await db.conversations.update_one({"id": conv_id}, {"$set": update})
    
    # Sync assigned_to to lead
    if "assigned_to" in update:
        conv = await db.conversations.find_one({"id": conv_id})
        if conv and conv.get("lead_id"):
            await db.leads.update_one({"id": conv["lead_id"]}, {"$set": {"assigned_to": update["assigned_to"]}})
    # log bot handoff event
    if "bot_enabled" in update:
        conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
        was_enabled = conv.get("bot_enabled", True) if conv else True
        is_enabled = bool(update["bot_enabled"])
        if was_enabled != is_enabled:
            if is_enabled:
                await _log_system_message(db, conv_id, f"Bot reactivado - Control de bot encendido (Agente: {user.name})")
            else:
                await _log_system_message(db, conv_id, f"Control humano activado - Bot apagado (Agente: {user.name})")
        await db.bot_events.insert_one({
            "id": new_id("evt"),
            "conversation_id": conv_id,
            "type": "bot_enabled" if update["bot_enabled"] else "human_handoff",
            "actor": user.name,
            "created_at": now_iso(),
        })
        if not update["bot_enabled"]:
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
    role = _normalize_role(user.role)
    is_admin_or_supervisor = role in ("admin", "supervisor")
    if not is_admin_or_supervisor:
        q["assigned_to"] = user.user_id
    elif assigned_to:
        q["assigned_to"] = assigned_to
    docs = await db.tasks.find(q, {"_id": 0}).sort("due_date", 1).to_list(1000)
    leads = {l["id"]: l for l in await db.leads.find({}, {"_id": 0}).to_list(1000)}
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(1000)}
    for d in docs:
        lead = leads.get(d.get("lead_id"))
        if lead:
            lead["contact"] = contacts.get(lead.get("contact_id"))
            d["lead"] = lead
    return docs


@api_router.post("/tasks", response_model=Task)
async def create_task(payload: TaskCreate, user: User = Depends(get_current_user)):
    data = payload.model_dump()
    if not data.get("assigned_to"):
        data["assigned_to"] = user.user_id
    if data.get("status"):
        data["status"] = await validate_task_status(data["status"])
    task = Task(**data)
    await db.tasks.insert_one(task.model_dump())
    return task


@api_router.patch("/tasks/{task_id}", response_model=Task)
async def update_task(task_id: str, payload: TaskUpdate, user: User = Depends(get_current_user)):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "status" in update:
        update["status"] = await validate_task_status(update["status"])
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
# Appointments
# ---------------------------------------------------------------------------

async def _effective_bot_settings() -> dict:
    from ai.pipeline import DEFAULT_BOT_SETTINGS
    doc = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
    return {**DEFAULT_BOT_SETTINGS, **doc}


def _is_calendar_manager(user: User) -> bool:
    return _normalize_role(user.role) in ("admin", "supervisor")


def _parse_appointment_time(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field_name} debe ser una fecha ISO-8601 válida")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_appointment_times(start_time: str, end_time: str) -> None:
    start_dt = _parse_appointment_time(start_time, "start_time")
    end_dt = _parse_appointment_time(end_time, "end_time")
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="La hora de fin debe ser posterior a la hora de inicio")


async def _validate_appointment_assignee(user_id: str | None) -> str:
    target = (user_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="La cita o evento debe estar asignado a un usuario")
    member = await db.users.find_one(
        {"user_id": target, "active": {"$ne": False}},
        {"_id": 0, "user_id": 1},
    )
    if not member:
        raise HTTPException(status_code=400, detail="El usuario asignado no existe o está inactivo")
    return target


async def _get_editable_appointment(appt_id: str, user: User) -> dict:
    doc = await db.appointments.find_one({"id": appt_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Cita o evento no encontrado")
    if not _is_calendar_manager(user) and doc.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="No podés modificar el calendario de otro usuario")
    return doc


async def _calendar_availability_for(user_id: str, settings: dict) -> tuple[dict, dict]:
    from utils.scheduling import SchedulingError, normalize_person_availability
    user_doc = await db.users.find_one(
        {"user_id": user_id, "active": {"$ne": False}}, {"_id": 0}
    )
    if not user_doc:
        raise HTTPException(status_code=404, detail="Usuario no encontrado o inactivo")
    try:
        availability = normalize_person_availability(user_doc.get("calendar_settings"), settings)
    except SchedulingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user_doc, availability


async def _save_calendar_availability(user_id: str, payload: CalendarAvailabilityUpdate) -> dict:
    from utils.scheduling import SchedulingError, normalize_person_availability
    settings = await _effective_bot_settings()
    user_doc, current = await _calendar_availability_for(user_id, settings)
    merged = {**current, **payload.model_dump(exclude_unset=True)}
    try:
        availability = normalize_person_availability(merged, settings)
    except (SchedulingError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.users.update_one({"user_id": user_id}, {"$set": {"calendar_settings": availability}})
    return {
        "user_id": user_id,
        "name": user_doc.get("name") or user_doc.get("email") or "Usuario",
        **availability,
    }


@api_router.get("/calendar/scheduling-config")
async def get_calendar_scheduling_config(user: User = Depends(get_current_user)):
    from utils.scheduling import normalize_services
    settings = await _effective_bot_settings()
    _, availability = await _calendar_availability_for(user.user_id, settings)
    return {
        "enabled": bool(settings.get("appointment_scheduling_enabled")),
        "mode": settings.get("appointment_mode") or "people",
        "timezone": settings.get("appointment_timezone"),
        "reminders_enabled": bool(settings.get("appointment_reminders_enabled")),
        "reminder_minutes_before": int(settings.get("appointment_reminder_minutes_before") or 1440),
        "reminder_template_id": settings.get("appointment_reminder_template_id"),
        "services": normalize_services(
            settings.get("appointment_services") or [], settings.get("appointment_timezone")
        ),
        "availability": availability,
    }


@api_router.get("/calendar/availability")
async def get_calendar_availability(user: User = Depends(get_current_user)):
    settings = await _effective_bot_settings()
    user_doc, availability = await _calendar_availability_for(user.user_id, settings)
    return {"user_id": user.user_id, "name": user_doc.get("name"), **availability}


@api_router.patch("/calendar/availability")
async def patch_calendar_availability(
    payload: CalendarAvailabilityUpdate,
    user: User = Depends(get_current_user),
):
    return await _save_calendar_availability(user.user_id, payload)


@api_router.get("/calendar/team-availability")
async def get_team_calendar_availability(user: User = Depends(get_current_user)):
    if not _is_calendar_manager(user):
        raise HTTPException(status_code=403, detail="Solo administradores y supervisores pueden ver la disponibilidad del equipo")
    settings = await _effective_bot_settings()
    members = await db.users.find({"active": {"$ne": False}}, {"_id": 0}).sort("name", 1).to_list(500)
    result = []
    for member in members:
        from utils.scheduling import normalize_person_availability
        availability = normalize_person_availability(member.get("calendar_settings"), settings)
        result.append({
            "user_id": member.get("user_id"),
            "name": member.get("name") or member.get("email") or "Usuario",
            "role": _normalize_role(member.get("role")),
            **availability,
        })
    return result


@api_router.patch("/calendar/team-availability/{user_id}")
async def patch_team_calendar_availability(
    user_id: str,
    payload: CalendarAvailabilityUpdate,
    user: User = Depends(get_current_user),
):
    if not _is_calendar_manager(user):
        raise HTTPException(status_code=403, detail="Solo administradores y supervisores pueden configurar al equipo")
    return await _save_calendar_availability(user_id, payload)


async def _apply_scheduling_rules(
    data: dict,
    *,
    exclude_appointment_id: str | None = None,
) -> dict:
    settings = await _effective_bot_settings()
    mode = settings.get("appointment_mode") or "people"
    if data.get("event_type") != "appointment":
        data["scheduling_mode"] = mode
        data["service_id"] = None
        data["service_name"] = None
        return data
    if data.get("status") != "scheduled":
        return data
    data["scheduling_mode"] = mode
    if not settings.get("appointment_scheduling_enabled"):
        if mode == "people":
            data["service_id"] = None
            data["service_name"] = None
        return data
    from utils.scheduling import (
        SchedulingError,
        get_business_service,
        parse_datetime,
        validate_appointment_slot,
    )
    try:
        if mode == "business":
            service = get_business_service(settings, data.get("service_id"))
            normalized_start = parse_datetime(data["start_time"], service["timezone"])
            data["end_time"] = (
                normalized_start + timedelta(minutes=int(service["duration_minutes"]))
            ).isoformat()
        slot = await validate_appointment_slot(
            db,
            settings,
            start_time=data["start_time"],
            end_time=data["end_time"],
            mode=mode,
            assigned_to=data.get("assigned_to"),
            service_id=data.get("service_id"),
            exclude_appointment_id=exclude_appointment_id,
        )
    except SchedulingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    data["start_time"] = slot["start_time"]
    data["end_time"] = slot["end_time"]
    data["service_id"] = slot["resource_id"] if mode == "business" else None
    data["service_name"] = slot["resource_name"] if mode == "business" else None
    return data


async def _attach_appointment_recipient(data: dict) -> dict:
    """Persist who the reminder must contact and through which conversation."""
    if not data.get("contact_id") and data.get("lead_id"):
        lead = await db.leads.find_one({"id": data["lead_id"]}, {"_id": 0, "contact_id": 1})
        if lead:
            data["contact_id"] = lead.get("contact_id")
    if data.get("contact_id") and not data.get("conversation_id"):
        conv = await db.conversations.find_one(
            {"contact_id": data["contact_id"], "channel": "whatsapp"},
            {"_id": 0},
            sort=[("last_message_at", -1)],
        )
        if conv:
            data["conversation_id"] = conv.get("id")
    return data


async def _apply_appointment_reminder_settings(
    data: dict,
    *,
    reset_status: bool,
) -> dict:
    from utils.appointment_reminders import reminder_fields
    settings = await _effective_bot_settings()
    data = await _attach_appointment_recipient(data)
    data.update(reminder_fields(data, settings, reset_status=reset_status))
    return data

@api_router.get("/appointments")
async def list_appointments(
    user: User = Depends(get_current_user),
    start: Optional[str] = None,
    end: Optional[str] = None,
    assigned_to: Optional[str] = None,
):
    q = {}
    if start or end:
        date_q = {}
        if start:
            date_q["$gte"] = start
        if end:
            date_q["$lte"] = end
        q["start_time"] = date_q

    if not _is_calendar_manager(user):
        q["assigned_to"] = user.user_id
    elif assigned_to:
        q["assigned_to"] = assigned_to

    docs = await db.appointments.find(q, {"_id": 0}).sort("start_time", 1).to_list(1000)
    
    # Expand lead/contact
    leads = {l["id"]: l for l in await db.leads.find({}, {"_id": 0}).to_list(1000)}
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(1000)}
    members = {
        member["user_id"]: {
            "user_id": member["user_id"],
            "name": member.get("name") or member.get("email") or "Usuario",
            "picture": member.get("picture"),
            "role": _normalize_role(member.get("role")),
        }
        for member in await db.users.find({}, {"_id": 0}).to_list(500)
        if member.get("user_id")
    }
    for d in docs:
        d["assigned_user"] = members.get(d.get("assigned_to"))
        d["created_by_user"] = members.get(d.get("created_by"))
        if d.get("lead_id"):
            lead = leads.get(d.get("lead_id"))
            if lead:
                lead["contact"] = contacts.get(lead.get("contact_id"))
                d["lead"] = lead
        elif d.get("contact_id"):
            d["contact"] = contacts.get(d.get("contact_id"))
            
    return docs

@api_router.post("/appointments", response_model=Appointment)
async def create_appointment(payload: AppointmentCreate, user: User = Depends(get_current_user)):
    data = payload.model_dump()
    data["title"] = data["title"].strip()
    if not data["title"]:
        raise HTTPException(status_code=400, detail="El título es obligatorio")
    requested_assignee = data.get("assigned_to")
    if not _is_calendar_manager(user) and requested_assignee not in (None, "", user.user_id):
        raise HTTPException(status_code=403, detail="No podés crear eventos para otro usuario")
    data["assigned_to"] = await _validate_appointment_assignee(
        requested_assignee if _is_calendar_manager(user) else user.user_id
    )
    _validate_appointment_times(data["start_time"], data["end_time"])
    data = await _apply_scheduling_rules(data)
    data = await _apply_appointment_reminder_settings(data, reset_status=True)
    data["created_by_bot"] = False
    data["created_by"] = user.user_id
    data["created_by_name"] = user.name
    appt = Appointment(**data)
    await db.appointments.insert_one(appt.model_dump())
    return appt

@api_router.patch("/appointments/{appt_id}", response_model=Appointment)
async def update_appointment(appt_id: str, payload: AppointmentUpdate, user: User = Depends(get_current_user)):
    existing = await _get_editable_appointment(appt_id, user)
    update = payload.model_dump(exclude_unset=True)
    if not update:
        return Appointment(**existing)
    if "title" in update:
        update["title"] = (update["title"] or "").strip()
        if not update["title"]:
            raise HTTPException(status_code=400, detail="El título es obligatorio")
    if "assigned_to" in update:
        if not _is_calendar_manager(user) and update["assigned_to"] != user.user_id:
            raise HTTPException(status_code=403, detail="No podés reasignar eventos a otro usuario")
        update["assigned_to"] = await _validate_appointment_assignee(update["assigned_to"])
    start_time = update.get("start_time", existing.get("start_time"))
    end_time = update.get("end_time", existing.get("end_time"))
    _validate_appointment_times(start_time, end_time)
    scheduling_fields = {"start_time", "end_time", "assigned_to", "service_id", "event_type"}
    should_revalidate = bool(scheduling_fields.intersection(update)) or update.get("status") == "scheduled"
    if should_revalidate:
        candidate = {**existing, **update, "start_time": start_time, "end_time": end_time}
        candidate = await _apply_scheduling_rules(candidate, exclude_appointment_id=appt_id)
        for key in ("start_time", "end_time", "scheduling_mode", "service_id", "service_name"):
            update[key] = candidate.get(key)
    reminder_fields_changed = {
        "start_time", "status", "event_type", "contact_id", "lead_id",
        "reminder_enabled", "reminder_minutes_before", "reminder_template_id",
    }.intersection(update)
    if reminder_fields_changed:
        reminder_candidate = await _apply_appointment_reminder_settings(
            {**existing, **update}, reset_status=True
        )
        for key in (
            "contact_id", "conversation_id", "reminder_enabled", "reminder_minutes_before",
            "reminder_template_id", "reminder_due_at", "reminder_status", "reminder_sent_at",
            "reminder_error", "reminder_attempts", "confirmation_status",
        ):
            update[key] = reminder_candidate.get(key)
    update["updated_by"] = user.user_id
    update["updated_at"] = now_iso()
    await db.appointments.update_one({"id": appt_id}, {"$set": update})
    doc = await db.appointments.find_one({"id": appt_id}, {"_id": 0})
    return Appointment(**doc)


async def _send_appointment_reminder(
    appointment: dict,
    *,
    sender_type: str = "bot",
    sender_name: str | None = None,
) -> dict:
    from whatsapp.templates import find_template
    settings = await _effective_bot_settings()
    template_id = (
        appointment.get("reminder_template_id")
        or settings.get("appointment_reminder_template_id")
    )
    template = find_template(settings, template_id or "", purpose="appointment_reminder")
    if not template:
        raise HTTPException(
            status_code=409,
            detail="No hay una plantilla de recordatorio activa configurada para este turno",
        )
    enriched = await _attach_appointment_recipient(dict(appointment))
    if not enriched.get("contact_id"):
        raise HTTPException(status_code=409, detail="El turno no tiene un cliente asociado")
    contact = await db.contacts.find_one({"id": enriched["contact_id"]}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="No se encontró el cliente del turno")
    conv = None
    if enriched.get("conversation_id"):
        conv = await db.conversations.find_one({"id": enriched["conversation_id"]}, {"_id": 0})
    if not conv:
        conv = await db.conversations.find_one(
            {"contact_id": enriched["contact_id"], "channel": "whatsapp"},
            {"_id": 0},
            sort=[("last_message_at", -1)],
        )
    if not conv:
        raise HTTPException(status_code=409, detail="El cliente no tiene una conversación de WhatsApp")
    message = await _send_configured_whatsapp_template(
        conv=conv,
        contact=contact,
        template=template,
        settings=settings,
        appointment=enriched,
        sender_type=sender_type,
        sender_name=sender_name or settings.get("bot_name") or "Bot",
    )
    sent_at = now_iso()
    await db.appointments.update_one(
        {"id": enriched["id"]},
        {"$set": {
            "contact_id": enriched.get("contact_id"),
            "conversation_id": conv.get("id"),
            "reminder_template_id": template.get("id"),
            "reminder_status": "sent",
            "reminder_sent_at": sent_at,
            "reminder_error": None,
            "confirmation_status": "awaiting_response",
        }},
    )
    return message


@api_router.post("/appointments/{appt_id}/send-reminder")
async def send_appointment_reminder_now(
    appt_id: str,
    user: User = Depends(get_current_user),
):
    appointment = await _get_editable_appointment(appt_id, user)
    if appointment.get("event_type") != "appointment" or appointment.get("status") != "scheduled":
        raise HTTPException(status_code=409, detail="Sólo se pueden recordar citas agendadas")
    try:
        appointment_start = datetime.fromisoformat(
            str(appointment.get("start_time") or "").replace("Z", "+00:00")
        )
        if appointment_start.tzinfo is None:
            appointment_start = appointment_start.replace(tzinfo=timezone.utc)
        if appointment_start.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="No se puede recordar un turno que ya comenzó")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="La fecha del turno no es válida") from exc
    return await _send_appointment_reminder(
        appointment,
        sender_type="agent",
        sender_name=user.name,
    )

@api_router.delete("/appointments/{appt_id}")
async def delete_appointment(appt_id: str, user: User = Depends(get_current_user)):
    await _get_editable_appointment(appt_id, user)
    await db.appointments.delete_one({"id": appt_id})
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

def _compute_dashboard_stats(leads, convs, tasks, contacts, done_statuses, is_admin_or_supervisor, user_id, start_dt=None, end_dt=None):
    # Filter leads by date range
    filtered_leads = leads
    if start_dt or end_dt:
        filtered_leads = []
        for l in leads:
            created = l.get("created_at")
            if not created:
                continue
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if start_dt and dt < start_dt:
                    continue
                if end_dt and dt > end_dt:
                    continue
                filtered_leads.append(l)
            except Exception:
                pass

    by_status = {s: 0 for s in LEAD_STATUSES}
    value_by_status = {s: 0.0 for s in LEAD_STATUSES}
    pipeline_value = 0.0
    won_value = 0.0
    for l in filtered_leads:
        st = l.get("status", "new")
        by_status[st] = by_status.get(st, 0) + 1
        value_by_status[st] = value_by_status.get(st, 0) + float(l.get("value", 0.0))
        if st == "won":
            won_value += float(l.get("value", 0.0))
        elif st != "lost":
            pipeline_value += float(l.get("value", 0.0))

    won = by_status.get("won", 0)
    lost = by_status.get("lost", 0)
    closed = won + lost
    conv_rate = round((won / closed) * 100, 1) if closed else 0.0

    # Calculate post-sales metrics
    from collections import Counter
    won_leads = [l for l in filtered_leads if l.get("status") == "won"]
    won_contact_ids = {l["contact_id"] for l in won_leads if l.get("contact_id")}
    total_customers = len(won_contact_ids)
    average_ticket = won_value / len(won_leads) if won_leads else 0.0
    
    won_contact_counts = Counter(l["contact_id"] for l in won_leads if l.get("contact_id"))
    recurring_customers = sum(1 for cid, count in won_contact_counts.items() if count > 1)
    
    contact_won_value = {}
    for l in won_leads:
        cid = l.get("contact_id")
        if cid:
            contact_won_value[cid] = contact_won_value.get(cid, 0.0) + float(l.get("value") or 0.0)
            
    top_customers_sorted = sorted(contact_won_value.items(), key=lambda x: x[1], reverse=True)[:5]
    top_customers = []
    for cid, val in top_customers_sorted:
        ct = contacts.get(cid, {})
        top_customers.append({
            "contact_id": cid,
            "name": ct.get("name") or "Cliente desconocido",
            "company": ct.get("company") or "Particular",
            "avatar": ct.get("avatar"),
            "total_value": val
        })
        
    product_revenue = {}
    product_quantity = {}
    for l in won_leads:
        sold_products = (l.get("sale_snapshot") or {}).get("products") or l.get("products") or []
        for p in sold_products:
            pname = p.get("name")
            if pname:
                price = float(p.get("unit_price", p.get("price")) or 0.0)
                qty = int(p.get("quantity") or 1)
                product_revenue[pname] = product_revenue.get(pname, 0.0) + (price * qty)
                product_quantity[pname] = product_quantity.get(pname, 0) + qty
                
    top_products_sorted = sorted(product_revenue.items(), key=lambda x: x[1], reverse=True)[:5]
    top_products = []
    for pname, rev in top_products_sorted:
        top_products.append({
            "name": pname,
            "revenue": rev,
            "quantity": product_quantity.get(pname, 0)
        })
        
    sales_by_month = {}
    for l in won_leads:
        created = l.get("created_at") or l.get("updated_at")
        if created:
            month = created[:7]  # YYYY-MM
            sales_by_month[month] = sales_by_month.get(month, 0.0) + float(l.get("value") or 0.0)
            
    sorted_months = sorted(sales_by_month.keys())
    sales_trend = [{"month": m, "value": sales_by_month[m]} for m in sorted_months]

    # Leads per day for this period
    leads_by_day = {}
    for l in filtered_leads:
        created = l.get("created_at")
        if created:
            day = created[:10]  # YYYY-MM-DD
            leads_by_day[day] = leads_by_day.get(day, 0) + 1
    sorted_days = sorted(leads_by_day.keys())
    leads_trend = [{"date": d, "value": leads_by_day[d]} for d in sorted_days]

    # Leads per source for this period
    source_counts = {}
    for l in filtered_leads:
        cid = l.get("contact_id")
        ct = contacts.get(cid, {})
        ls = ct.get("lead_source") or l.get("source") or "Orgánico"
        source_counts[ls] = source_counts.get(ls, 0) + 1
    leads_by_source = [{"source": k, "count": v} for k, v in source_counts.items()]

    return {
        "total_leads": len(filtered_leads),
        "pipeline_value": pipeline_value,
        "won_value": won_value,
        "conversion_rate": conv_rate,
        "leads_by_status": by_status,
        "value_by_status": value_by_status,
        "leads_trend": leads_trend,
        "leads_by_source": leads_by_source,
        "sales": {
            "total_customers": total_customers,
            "average_ticket": average_ticket,
            "recurring_customers": recurring_customers,
            "top_customers": top_customers,
            "top_products": top_products,
            "sales_trend": sales_trend
        }
    }


@api_router.get("/dashboard/metrics")
async def dashboard_metrics(
    user: User = Depends(get_current_user),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    compare_start_date: Optional[str] = None,
    compare_end_date: Optional[str] = None,
):
    leads = await db.leads.find({}, {"_id": 0}).to_list(2000)
    convs = await db.conversations.find({}, {"_id": 0}).to_list(2000)
    tasks = await db.tasks.find({}, {"_id": 0}).to_list(2000)
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(2000)}

    role = _normalize_role(user.role)
    is_admin_or_supervisor = role in ("admin", "supervisor")
    if not is_admin_or_supervisor:
        leads = [l for l in leads if l.get("assigned_to") == user.user_id]
        convs = [c for c in convs if c.get("assigned_to") == user.user_id]
        tasks = [t for t in tasks if t.get("assigned_to") == user.user_id]

    open_convs = len([c for c in convs if c.get("status") == "open"])
    pending_convs = len([c for c in convs if c.get("status") == "pending"])
    human_handled = len([c for c in convs if not c.get("bot_enabled", True)])
    done_statuses = await get_task_done_statuses()
    open_tasks = len([t for t in tasks if t.get("status") not in done_statuses])

    # --- Detect overdue / due-soon tasks and generate notifications ---
    now = datetime.now(timezone.utc)
    soon = now + timedelta(hours=24)
    overdue_tasks = []
    for t in tasks:
        if t.get("status") in done_statuses or not t.get("due_date"):
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
    if not is_admin_or_supervisor:
        no_response_convs = [c for c in no_response_convs if c.get("assigned_to") == user.user_id]
    no_response = [conv_brief(c) for c in no_response_convs]

    # Parse range dates
    start_dt = None
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date + "T00:00:00+00:00")
        except Exception:
            pass
    end_dt = None
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date + "T23:59:59+00:00")
        except Exception:
            pass

    current = _compute_dashboard_stats(leads, convs, tasks, contacts, done_statuses, is_admin_or_supervisor, user.user_id, start_dt, end_dt)

    comparison = None
    if compare_start_date and compare_end_date:
        compare_start_dt = None
        try:
            compare_start_dt = datetime.fromisoformat(compare_start_date + "T00:00:00+00:00")
        except Exception:
            pass
        compare_end_dt = None
        try:
            compare_end_dt = datetime.fromisoformat(compare_end_date + "T23:59:59+00:00")
        except Exception:
            pass
        comparison = _compute_dashboard_stats(leads, convs, tasks, contacts, done_statuses, is_admin_or_supervisor, user.user_id, compare_start_dt, compare_end_dt)

    return {
        "total_contacts": len(contacts),
        "open_conversations": open_convs,
        "pending_conversations": pending_convs,
        "human_handled": human_handled,
        "open_tasks": open_tasks,
        "requires_attention": {
            "open_handoffs": open_handoffs,
            "unread_conversations": unread_conversations,
            "overdue_tasks": overdue_brief,
            "no_response": no_response,
        },
        **current,
        "comparison": comparison
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
    from ai import providers as ai_providers
    try:
        provider_obj = await ai_providers.get_provider(db, for_bot=False)
        res = await provider_obj.chat(system_prompt=system, user_block=prompt, json_mode=False)
        return res.content
    except Exception as e:
        logger.exception("Error calling LLM in _llm helper")
        raise HTTPException(500, f"Servicio de IA no disponible: {e}")


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
async def reseed(admin: User = Depends(require_perm("manage_settings"))):
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
    scenario_id = "aura_estetica_argentina_v1"
    flag = await db.settings.find_one({"key": "seeded"})
    seed_enabled = (os.environ.get("LATUS_SEED_DEMO", "").strip().lower()
                    in ("1", "true", "yes", "on"))
    if flag and not force:
        if flag.get("scenario") == scenario_id:
            return
        # Sólo migramos automáticamente una demo anterior cuando el entorno
        # sigue declarado como demo. Una base real nunca se reemplaza aquí.
        previous_demo_users = await db.users.count_documents({"is_demo": True})
        if not (seed_enabled and previous_demo_users > 0):
            return
        force = True
    if not force:
        admin_count = await db.users.count_documents({"role": "admin", "active": True, "deleted_at": None})
        if admin_count == 0:
            logger.info("No active admin users found. Bootstrapping default local admin user (admin@latus.test).")
            await db.users.update_one(
                {"user_id": "user_local_admin"},
                {"$set": {
                    "user_id": "user_local_admin", "email": "admin@latus.test",
                    "name": "Administrador Local", "role": "admin", "active": True,
                    "auth_provider": "local", "password_hash": hash_password("Latus1234"),
                    "is_demo": False, "created_at": now_iso(), "updated_at": now_iso(),
                }}, upsert=True,
            )
        if not seed_enabled:
            logger.info("_seed skipped: LATUS_SEED_DEMO not set")
            return

    from demo_data import build_demo_dataset

    if force:
        # El restablecimiento demo ya reemplazaba los datos operativos. Incluimos
        # ahora catálogo, agenda, áreas y métricas para que todas las pantallas
        # pertenezcan al mismo escenario.
        for coll in [
            "contacts", "leads", "conversations", "messages", "tasks", "notes",
            "tags", "appointments", "products", "work_areas", "ai_usage_logs",
            "bot_events", "notifications",
        ]:
            await db.__getattr__(coll).delete_many({})
        await db.users.delete_many({"is_demo": True})

    dataset = build_demo_dataset()
    for user_doc in dataset["users"]:
        await db.users.update_one(
            {"user_id": user_doc["user_id"]}, {"$set": user_doc}, upsert=True,
        )
    for collection_name in [
        "work_areas", "tags", "products", "contacts", "leads", "conversations",
        "messages", "notes", "tasks", "appointments", "ai_usage_logs",
    ]:
        collection = db.__getattr__(collection_name)
        for document in dataset[collection_name]:
            await collection.insert_one(document)

    await db.bot_settings.update_one(
        {"_id": "default"}, {"$set": dataset["bot_settings"]}, upsert=True,
    )
    await db.settings.update_one(
        {"key": "app"}, {"$set": dataset["app_settings"]}, upsert=True,
    )
    await db.settings.update_one(
        {"key": "seeded"},
        {"$set": {"key": "seeded", "at": now_iso(), "scenario": scenario_id}},
        upsert=True,
    )


async def _seed_roles():
    """Seed the default roles in db.roles if they do not exist."""
    try:
        for rid, perms in DEFAULT_ROLE_PERMISSIONS.items():
            exist = await db.roles.find_one({"role_id": rid})
            if not exist:
                await db.roles.update_one(
                    {"role_id": rid},
                    {"$set": {
                        "role_id": rid,
                        "name": rid.capitalize(),
                        "permissions": perms,
                        "is_default": True
                    }},
                    upsert=True
                )
    except Exception as e:
        logger.exception("Error seeding roles: %s", str(e))


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
    asyncio.create_task(_bg_step("_seed_roles", _seed_roles()))
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
        await db.password_reset_tokens.create_index("token_hash", unique=True, name="ux_password_reset_token_hash")
        await db.password_reset_tokens.create_index("expires_at", name="ix_password_reset_expires")
        await db.appointments.create_index(
            [("assigned_to", 1), ("start_time", 1)],
            name="ix_appointments_assignee_start",
        )
        await db.appointments.create_index("start_time", name="ix_appointments_start")
        await db.appointments.create_index(
            [("reminder_status", 1), ("reminder_due_at", 1)],
            name="ix_appointments_reminder_due",
        )
    except Exception as e:  # pragma: no cover - best-effort
        logger.warning("ensure_indexes failed: %s", e)


def _build_report_email_html(report_type: str, stats: dict, base_url: str) -> str:
    title = f"Resumen {report_type.capitalize()} de Leads"
    def fmt_curr(val: float) -> str:
        return f"${val:,.2f}"
    return f"""
    <div style="font-family: Arial, sans-serif; background-color: #f9f9f7; padding: 24px; color: #0b1b26;">
      <div style="max-width: 500px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #e5e7eb; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
        <div style="background-color: #0b1b26; padding: 24px; text-align: center; color: #ffffff;">
          <h2 style="margin: 0; font-size: 20px; font-weight: bold; letter-spacing: 0.5px;">Latus CRM</h2>
          <p style="margin: 4px 0 0 0; font-size: 13px; color: #94a3b8;">{title}</p>
        </div>
        <div style="padding: 24px;">
          <p style="font-size: 15px; margin: 0 0 20px 0; color: #334155; line-height: 1.5;">
            Hola, te compartimos el resumen de rendimiento y estado de los leads de tu CRM correspondiente al periodo de este reporte.
          </p>
          <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
            <thead>
              <tr style="border-bottom: 2px solid #e2e8f0;">
                <th style="text-align: left; padding: 8px 0; font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase;">Métrica</th>
                <th style="text-align: right; padding: 8px 0; font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase;">Resultado</th>
              </tr>
            </thead>
            <tbody>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 12px 0; font-size: 14px; color: #0b1b26; font-weight: 500;">Nuevos Leads Creados</td>
                <td style="padding: 12px 0; font-size: 14px; color: #0b1b26; font-weight: bold; text-align: right;">{stats['new_count']} <span style="font-size: 12px; color: #64748b; font-weight: normal;">({fmt_curr(stats['new_value'])})</span></td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 12px 0; font-size: 14px; color: #16a34a; font-weight: 500;">Leads Ganados</td>
                <td style="padding: 12px 0; font-size: 14px; color: #16a34a; font-weight: bold; text-align: right;">{stats['won_count']} <span style="font-size: 12px; color: #16a34a; font-weight: normal;">({fmt_curr(stats['won_value'])})</span></td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 12px 0; font-size: 14px; color: #dc2626; font-weight: 500;">Leads Perdidos</td>
                <td style="padding: 12px 0; font-size: 14px; color: #dc2626; font-weight: bold; text-align: right;">{stats['lost_count']}</td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 12px 0; font-size: 14px; color: #ea580c; font-weight: 500;">Clientes Sin Atender</td>
                <td style="padding: 12px 0; font-size: 14px; color: #ea580c; font-weight: bold; text-align: right;">{stats['unattended_count']}</td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9; background-color: #f8fafc;">
                <td style="padding: 12px 6px; font-size: 14px; color: #0b1b26; font-weight: 600;">Valor del Pipeline Activo</td>
                <td style="padding: 12px 6px; font-size: 14px; color: #0b1b26; font-weight: bold; text-align: right;">{fmt_curr(stats['active_value'])}</td>
              </tr>
            </tbody>
          </table>
          <div style="text-align: center; margin-top: 30px; margin-bottom: 10px;">
            <a href="{base_url}" style="background-color: #0b1b26; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-size: 14px; font-weight: bold; display: inline-block;">Abrir Latus CRM</a>
          </div>
        </div>
        <div style="background-color: #f8fafc; padding: 16px; text-align: center; border-top: 1px solid #e2e8f0;">
          <p style="margin: 0; font-size: 11px; color: #64748b; line-height: 1.4;">
            Este reporte fue auto-generado por Latus CRM.<br/>Podés desactivar estos resúmenes desde el panel de Configuración en cualquier momento.
          </p>
        </div>
      </div>
    </div>
    """.strip()


async def generate_leads_summary_report(days_back: int) -> dict:
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days_back)
    leads = await db.leads.find({}, {"_id": 0}).to_list(10000)
    new_leads_count = 0
    new_leads_value = 0.0
    won_leads_count = 0
    won_leads_value = 0.0
    lost_leads_count = 0
    total_active_value = 0.0
    for l in leads:
        try:
            created_at = datetime.fromisoformat(l.get("created_at").replace("Z", "+00:00"))
        except Exception:
            created_at = None
        try:
            updated_at = datetime.fromisoformat(l.get("updated_at").replace("Z", "+00:00"))
        except Exception:
            updated_at = None
        status = l.get("status", "new")
        val = float(l.get("value") or 0.0)
        if created_at and created_at >= cutoff:
            new_leads_count += 1
            new_leads_value += val
        if status == "won":
            if updated_at and updated_at >= cutoff:
                won_leads_count += 1
                won_leads_value += val
        elif status == "lost":
            if updated_at and updated_at >= cutoff:
                lost_leads_count += 1
        else:
            total_active_value += val
    unattended_count = await db.notifications.count_documents({
        "type": "lead_no_response",
        "is_read": False
    })
    return {
        "new_count": new_leads_count,
        "new_value": new_leads_value,
        "won_count": won_leads_count,
        "won_value": won_leads_value,
        "lost_count": lost_leads_count,
        "active_value": total_active_value,
        "unattended_count": unattended_count
    }


async def check_and_send_scheduled_reports():
    settings = await get_app_settings()
    now_utc = datetime.now(timezone.utc)
    daily_enabled = bool(settings.get("email_report_daily_enabled", True))
    weekly_enabled = bool(settings.get("email_report_weekly_enabled", True))
    monthly_enabled = bool(settings.get("email_report_monthly_enabled", True))
    last_daily = settings.get("last_daily_report_at")
    last_weekly = settings.get("last_weekly_report_at")
    last_monthly = settings.get("last_monthly_report_at")
    run_daily = False
    run_weekly = False
    run_monthly = False
    updates = {}
    if daily_enabled:
        if not last_daily:
            run_daily = True
        else:
            try:
                dt_daily = datetime.fromisoformat(last_daily.replace("Z", "+00:00"))
                if now_utc - dt_daily >= timedelta(hours=24):
                    run_daily = True
            except Exception:
                run_daily = True
    if weekly_enabled:
        if not last_weekly:
            run_weekly = True
        else:
            try:
                dt_weekly = datetime.fromisoformat(last_weekly.replace("Z", "+00:00"))
                if now_utc - dt_weekly >= timedelta(days=7):
                    run_weekly = True
            except Exception:
                run_weekly = True
    if monthly_enabled:
        if not last_monthly:
            run_monthly = True
        else:
            try:
                dt_monthly = datetime.fromisoformat(last_monthly.replace("Z", "+00:00"))
                if now_utc - dt_monthly >= timedelta(days=30):
                    run_monthly = True
            except Exception:
                run_monthly = True
    if run_daily or run_weekly or run_monthly:
        leaders = await db.users.find({"role": {"$in": ["admin", "supervisor"]}}, {"_id": 0}).to_list(100)
        recipients = [u["email"].strip().lower() for u in leaders if u.get("email") and "@" in u["email"]]
        if recipients:
            base_url = _resolve_app_base_url(settings)
            if run_daily:
                stats = await generate_leads_summary_report(1)
                html_body = _build_report_email_html("diario", stats, base_url)
                for email in recipients:
                    await send_email_via_settings(
                        to_email=email,
                        subject="📊 Resumen Diario de Leads - Latus CRM",
                        html_body=html_body,
                        text_body=f"Resumen diario de Latus CRM. Nuevos leads: {stats['new_count']}. Ganados: {stats['won_count']}. Valor activo: ${stats['active_value']}."
                    )
                updates["last_daily_report_at"] = now_utc.isoformat()
            if run_weekly:
                stats = await generate_leads_summary_report(7)
                html_body = _build_report_email_html("semanal", stats, base_url)
                for email in recipients:
                    await send_email_via_settings(
                        to_email=email,
                        subject="📊 Resumen Semanal de Leads - Latus CRM",
                        html_body=html_body,
                        text_body=f"Resumen semanal de Latus CRM. Nuevos leads: {stats['new_count']}. Ganados: {stats['won_count']}. Valor activo: ${stats['active_value']}."
                    )
                updates["last_weekly_report_at"] = now_utc.isoformat()
            if run_monthly:
                stats = await generate_leads_summary_report(30)
                html_body = _build_report_email_html("mensual", stats, base_url)
                for email in recipients:
                    await send_email_via_settings(
                        to_email=email,
                        subject="📊 Resumen Mensual de Leads - Latus CRM",
                        html_body=html_body,
                        text_body=f"Resumen mensual de Latus CRM. Nuevos leads: {stats['new_count']}. Ganados: {stats['won_count']}. Valor activo: ${stats['active_value']}."
                    )
                updates["last_monthly_report_at"] = now_utc.isoformat()
    if updates:
        await db.settings.update_one({"key": "app"}, {"$set": updates}, upsert=True)


# ---------------------------------------------------------------------------
# Scheduler: lead-no-response scan every 5 minutes
# ---------------------------------------------------------------------------


async def close_inactive_conversations(db):
    """Close inactive chats and re-arm disabled bots after the configured delay."""
    bot_cfg_doc = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
    try:
        inactive_hours = min(168, max(1, int(bot_cfg_doc.get("bot_inactive_close_hours") or 48)))
    except (TypeError, ValueError):
        inactive_hours = 48
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=inactive_hours)
    convs = await db.conversations.find({}, {"_id": 0}).to_list(1000)
    closed = reactivated = 0
    for c in convs:
        last_at_str = c.get("last_message_at") or c.get("updated_at")
        if not last_at_str:
            continue
        try:
            last_at = datetime.fromisoformat(last_at_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        if last_at.astimezone(timezone.utc) >= cutoff:
            continue
        should_close = c.get("status") != "resolved"
        should_reactivate = not c.get("bot_enabled", True)
        if not should_close and not should_reactivate:
            continue
        conv_id = c["id"]
        update = {"inactivity_processed_at": now_utc.isoformat()}
        if should_close:
            update.update({"status": "resolved", "bot_status": "cerrada"})
            closed += 1
        if should_reactivate:
            update.update({
                "bot_enabled": True,
                "human_required_reason": None,
                "bot_reactivated_at": now_utc.isoformat(),
                "bot_reactivated_reason": "inactivity_timeout",
            })
            reactivated += 1
        await db.conversations.update_one({"id": conv_id}, {"$set": update})
        if should_close:
            await _log_system_message(db, conv_id, f"Conversación cerrada automáticamente por inactividad de {inactive_hours} hs")
        if should_reactivate:
            await _log_system_message(db, conv_id, "Bot reactivado - Control de bot encendido")
        logger.info(
            "Processed inactive conversation %s after %s hours close=%s reactivate=%s",
            conv_id, inactive_hours, should_close, should_reactivate,
        )
    return {"closed": closed, "reactivated": reactivated}


async def send_due_appointment_reminders() -> dict:
    """Claim and send reminders once; safe to run repeatedly from the scheduler."""
    settings = await _effective_bot_settings()
    if not settings.get("appointment_reminders_enabled"):
        return {"sent": 0, "failed": 0}
    current = now_iso()
    due = await db.appointments.find({
        "event_type": "appointment",
        "status": "scheduled",
        "reminder_enabled": True,
        "reminder_due_at": {"$lte": current},
        "start_time": {"$gt": current},
        "reminder_status": {"$in": ["pending", "error"]},
        "$or": [
            {"reminder_attempts": {"$lt": 3}},
            {"reminder_attempts": {"$exists": False}},
        ],
    }, {"_id": 0}).sort("reminder_due_at", 1).to_list(200)
    sent = failed = 0
    for appointment in due:
        claim = await db.appointments.update_one(
            {
                "id": appointment["id"],
                "reminder_status": {"$in": ["pending", "error"]},
            },
            {"$set": {"reminder_status": "sending", "reminder_error": None},
             "$inc": {"reminder_attempts": 1}},
        )
        if claim is not None and getattr(claim, "modified_count", 0) != 1:
            continue
        try:
            await _send_appointment_reminder(appointment)
            sent += 1
        except Exception as exc:
            failed += 1
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            await db.appointments.update_one(
                {"id": appointment["id"]},
                {"$set": {"reminder_status": "error", "reminder_error": str(detail)[:500]}},
            )
            logger.warning("Appointment reminder failed appointment=%s: %s", appointment.get("id"), detail)
    return {"sent": sent, "failed": failed}


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
        try:
            await close_inactive_conversations(db)
        except Exception:
            logger.exception("scheduled close_inactive_conversations failed")
        try:
            await check_and_send_scheduled_reports()
        except Exception:  # pragma: no cover - log only
            logger.exception("scheduled check_and_send_scheduled_reports failed")
        try:
            await send_due_appointment_reminders()
        except Exception:  # pragma: no cover - log only
            logger.exception("scheduled send_due_appointment_reminders failed")

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
    "/api/auth/password/forgot", "/api/auth/password/reset",
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
                        perms = await get_role_permissions(role)
                        if "write_crm" not in perms:
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
    allow_origin_regex=r"https://.*\.vercel\.app|https://.*\.somoslatus\.com|https://somoslatus\.com",
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
