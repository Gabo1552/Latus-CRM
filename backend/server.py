from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, Query
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
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

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="Latus CRM API", openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url="/api/redoc")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


LEAD_STATUSES = ["new", "contacted", "qualified", "proposal", "won", "lost"]
CONV_STATUSES = ["open", "pending", "resolved"]
PRIORITIES = ["low", "medium", "high"]
ROLES = ["admin", "supervisor", "sales_agent"]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None
    role: str = "sales_agent"
    active: bool = True
    created_at: str = Field(default_factory=now_iso)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_dt(cls, v: Any):
        if isinstance(v, datetime):
            return v.isoformat()
        return v


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


@api_router.post("/auth/session")
async def process_session(request: Request, response: Response):
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")

    async with httpx.AsyncClient() as hc:
        r = await hc.get(
            "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
            headers={"X-Session-ID": session_id},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid session_id")
    data = r.json()

    email = data["email"]
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"name": data["name"], "picture": data.get("picture")}},
        )
        role = existing["role"]
    else:
        user_id = new_id("user")
        # First ever real user becomes admin
        real_users = await db.users.count_documents({"is_demo": {"$ne": True}})
        role = "admin" if real_users == 0 else "sales_agent"
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": data["name"],
            "picture": data.get("picture"),
            "role": role,
            "active": True,
            "is_demo": False,
            "created_at": now_iso(),
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
    await db.messages.insert_one(msg.model_dump())
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
    return msg


@api_router.post("/conversations/{conv_id}/simulate-inbound")
async def simulate_inbound(conv_id: str, user: User = Depends(get_current_user)):
    """Demo helper: simulate a customer (WhatsApp) message arriving."""
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    contact = await db.contacts.find_one({"id": conv["contact_id"]}, {"_id": 0})
    samples = [
        "Hola, te escribo para hacer seguimiento — ¿alguna novedad?",
        "¿Me podés pasar de nuevo los precios, por favor?",
        "¿Tenés disponibilidad para una llamada rápida hoy?",
        "¡Gracias! ¿Cuándo pueden enviar el pedido?",
        "Tengo una pregunta sobre la propuesta.",
    ]
    body = samples[len(conv.get("last_message", "")) % len(samples)]
    msg = Message(conversation_id=conv_id, sender_type="contact",
                  sender_name=contact["name"] if contact else "Cliente", body=body)
    await db.messages.insert_one(msg.model_dump())
    await db.conversations.update_one(
        {"id": conv_id}, {"$inc": {"unread": 1}, "$set": {"last_message": body, "last_message_at": now_iso()}})
    cname = contact["name"] if contact else "Cliente"
    await _notify_target(conv.get("assigned_to"), "new_message",
                         f"Nuevo mensaje de {cname}", body[:120], "conversation", conv_id, conv.get("priority", "medium"))
    return msg


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
# AI: summary & suggested reply (Claude Sonnet 4.6 via Emergent key)
# ---------------------------------------------------------------------------

async def _build_transcript(conv_id: str) -> str:
    msgs = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    lines = []
    for m in msgs:
        role = {"contact": "Cliente", "bot": "Bot", "agent": "Agente"}.get(m["sender_type"], m["sender_type"])
        lines.append(f"{role}: {m['body']}")
    return "\n".join(lines)


async def _llm(system: str, prompt: str) -> str:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
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
    flag = await db.settings.find_one({"key": "seeded"})
    if flag and not force:
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
    await _seed(force=False)
    await backfill_notifications()
    _start_scheduler()
    logger.info("Latus CRM started")


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

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
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
    client.close()
