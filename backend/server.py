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

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="FlowDesk CRM API")
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
    msg = Message(
        conversation_id=conv_id,
        sender_type=payload.sender_type,
        sender_name=user.name if payload.sender_type == "agent" else "Bot",
        body=payload.body,
    )
    await db.messages.insert_one(msg.model_dump())
    await db.conversations.update_one(
        {"id": conv_id},
        {"$set": {"last_message": payload.body, "last_message_at": now_iso()}},
    )
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

    return {
        "total_leads": len(leads),
        "total_contacts": await db.contacts.count_documents({}),
        "pipeline_value": pipeline_value,
        "won_value": won_value,
        "conversion_rate": conv_rate,
        "open_conversations": open_convs,
        "pending_conversations": pending_convs,
        "human_handled": human_handled,
        "open_tasks": open_tasks,
        "leads_by_status": by_status,
        "value_by_status": value_by_status,
    }

# ---------------------------------------------------------------------------
# AI: summary & suggested reply (Claude Sonnet 4.6 via Emergent key)
# ---------------------------------------------------------------------------

async def _build_transcript(conv_id: str) -> str:
    msgs = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    lines = []
    for m in msgs:
        role = {"contact": "Customer", "bot": "Bot", "agent": "Agent"}.get(m["sender_type"], m["sender_type"])
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
        return {"summary": "No messages yet to summarize."}
    try:
        summary = await _llm(
            "You are a sales assistant for a WhatsApp CRM. Summarize conversations crisply for a busy sales agent.",
            f"Summarize this WhatsApp sales conversation in 3-4 short bullet points covering the customer's intent, "
            f"key needs, objections, and recommended next step. Be concise.\n\n{transcript}",
        )
        return {"summary": summary.strip()}
    except Exception as e:
        logger.error(f"AI summary error: {e}")
        raise HTTPException(status_code=502, detail="AI service unavailable")


@api_router.post("/conversations/{conv_id}/ai-suggest")
async def ai_suggest(conv_id: str, user: User = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    transcript = await _build_transcript(conv_id)
    if not transcript.strip():
        return {"suggestion": "Hi! Thanks for reaching out. How can I help you today?"}
    try:
        suggestion = await _llm(
            "You are a friendly, professional WhatsApp sales agent. Write natural, concise replies that move the deal forward.",
            f"Based on this WhatsApp conversation, write the single best next reply the agent should send to the customer. "
            f"Return ONLY the message text, no quotes or preamble. Keep it warm and under 50 words.\n\n{transcript}",
        )
        return {"suggestion": suggestion.strip()}
    except Exception as e:
        logger.error(f"AI suggest error: {e}")
        raise HTTPException(status_code=502, detail="AI service unavailable")

# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

@api_router.post("/seed")
async def reseed(admin: User = Depends(require_admin)):
    await _seed(force=True)
    return {"ok": True}


async def _seed(force: bool = False):
    flag = await db.settings.find_one({"key": "seeded"})
    if flag and not force:
        return
    if force:
        for coll in ["contacts", "leads", "conversations", "messages", "tasks", "notes", "tags", "bot_events"]:
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
        {"id": "tag_hot", "name": "Hot Lead", "color": "#DC2626"},
        {"id": "tag_vip", "name": "VIP", "color": "#FF4500"},
        {"id": "tag_demo", "name": "Demo Booked", "color": "#064E3B"},
        {"id": "tag_followup", "name": "Follow Up", "color": "#EAB308"},
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
        ("open", "high", False, "qualified", 12000, "Sounds great, can you send the proposal?", [
            ("contact", "Hi, I saw your ad on Instagram. Do you ship wholesale to the US?"),
            ("bot", "Hello! Yes we do ship wholesale across the US. How many units are you looking for?"),
            ("contact", "Around 500 units to start, maybe more next quarter."),
            ("bot", "Great! For 500+ units we offer tiered pricing. Let me connect you with a specialist."),
            ("agent", "Hi Carlos, Leo here from sales. For 500 units our price is $24/unit with free freight."),
            ("contact", "Sounds great, can you send the proposal?"),
        ]),
        ("pending", "medium", True, "contacted", 4500, "Let me check with my team and revert.", [
            ("contact", "Do you have the spring collection in stock?"),
            ("bot", "Yes! The spring collection is in stock. Would you like a catalog?"),
            ("contact", "Yes please."),
            ("bot", "Sent to your email. Pricing starts at $18/unit for orders over 100."),
            ("contact", "Let me check with my team and revert."),
        ]),
        ("open", "high", False, "proposal", 28000, "The proposal looks good. Discount on annual?", [
            ("contact", "We're comparing 3 vendors. What makes you different?"),
            ("agent", "Hi Aisha, great question. We offer 48h dispatch and a dedicated account manager."),
            ("contact", "The proposal looks good. Discount on annual?"),
        ]),
        ("resolved", "low", True, "won", 9000, "Payment done, thank you!", [
            ("contact", "Ready to place the order."),
            ("bot", "Wonderful! Sending you the payment link now."),
            ("contact", "Payment done, thank you!"),
        ]),
        ("open", "medium", True, "new", 0, "Hi, what are your prices?", [
            ("contact", "Hi, what are your prices?"),
            ("bot", "Hi Elena! Our catalog starts at $15/unit. What product line interests you?"),
        ]),
        ("pending", "high", False, "qualified", 16500, "Can we hop on a quick call tomorrow?", [
            ("contact", "I need 1000 units urgently for an event."),
            ("bot", "I can help with bulk urgent orders. Connecting you to a specialist."),
            ("agent", "Hi Marcus, Priya here. We can expedite 1000 units within 5 days."),
            ("contact", "Can we hop on a quick call tomorrow?"),
        ]),
        ("open", "low", True, "contacted", 3200, "Thanks, I'll think about it.", [
            ("contact", "Do you offer samples?"),
            ("bot", "Yes, samples are $5 each, refundable on your first order."),
            ("contact", "Thanks, I'll think about it."),
        ]),
        ("open", "medium", False, "lost", 5000, "We went with another supplier, sorry.", [
            ("contact", "What's your MOQ?"),
            ("agent", "Hi Fatima, our MOQ is 200 units."),
            ("contact", "We went with another supplier, sorry."),
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
        lid = new_id("lead")
        await db.leads.insert_one({
            "id": lid, "contact_id": cid, "title": f"{company} wholesale order",
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
        base = datetime.now(timezone.utc) - timedelta(minutes=len(msgs) * 7)
        for j, (stype, body) in enumerate(msgs):
            sname = {"contact": cname, "bot": "Bot", "agent": "Sales Agent"}[stype]
            await db.messages.insert_one({
                "id": new_id("msg"), "conversation_id": conv_id, "sender_type": stype,
                "sender_name": sname, "body": body,
                "created_at": (base + timedelta(minutes=j * 7)).isoformat(),
            })
        if i % 2 == 0:
            await db.notes.insert_one({
                "id": new_id("note"), "lead_id": lid,
                "body": "Customer is price sensitive but high volume. Push annual contract.",
                "author_id": "user_demo_sup", "author_name": "Maya Sorensen", "created_at": now_iso(),
            })
        if lead_status in ("qualified", "proposal"):
            await db.tasks.insert_one({
                "id": new_id("task"), "title": f"Send proposal to {cname}",
                "description": "Prepare tiered pricing PDF and email it.", "lead_id": lid,
                "due_date": (datetime.now(timezone.utc) + timedelta(days=i % 3 + 1)).date().isoformat(),
                "status": "todo", "priority": prio, "assigned_to": assigned, "created_at": now_iso(),
            })

    await db.tasks.insert_one({
        "id": new_id("task"), "title": "Weekly pipeline review", "description": "Review all open deals with the team.",
        "lead_id": None, "due_date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
        "status": "todo", "priority": "medium", "assigned_to": "user_demo_sup", "created_at": now_iso(),
    })

    await db.settings.update_one({"key": "seeded"}, {"$set": {"key": "seeded", "at": now_iso()}}, upsert=True)


@app.on_event("startup")
async def on_startup():
    await _seed(force=False)
    logger.info("FlowDesk CRM started")


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
    client.close()
