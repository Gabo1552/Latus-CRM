from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, Query, Body
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
import os
import base64
import asyncio
import csv
import io
import logging
import uuid
import httpx
import html
import ssl
import smtplib
import hashlib
import hmac
import json
import math
import re
from pathlib import Path
from dataclasses import replace
from email.message import EmailMessage
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Any
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

from ai.usage import PLAN_MONTHLY_AI_TOKENS

from utils.tenancy import (
    COMPOSITE_ID_COLLECTIONS,
    TENANT_SCOPED_COLLECTIONS,
    get_organization_id,
    reset_organization_id,
    set_organization_id,
    tenant_collection,
)

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

VALID_ENVIRONMENTS = {"development", "staging", "production"}


def _environment_name() -> str:
    return (
        os.environ.get("ENVIRONMENT")
        or os.environ.get("NODE_ENV")
        or "development"
    ).strip().lower()


def _split_cors_origins(raw_value: Optional[str] = None) -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "") if raw_value is None else raw_value
    return list(dict.fromkeys(
        origin.strip().rstrip("/")
        for origin in raw.split(",")
        if origin.strip()
    ))


def _is_secure_public_origin(value: str) -> bool:
    parsed = urlparse(value)
    return bool(
        parsed.scheme == "https"
        and parsed.netloc
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and "*" not in parsed.netloc
    )


def validate_environment_guardrails() -> None:
    """Fail fast when staging and production configuration can overlap."""
    env = _environment_name()
    db_name = (os.environ.get("DB_NAME") or "").strip().lower()
    mp_token = (os.environ.get("MERCADOPAGO_ACCESS_TOKEN") or "").strip()
    mp_mode = (os.environ.get("MERCADOPAGO_MODE") or "").strip().lower()
    app_base_url = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    public_base_url = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    cors_origins = _split_cors_origins()

    if env not in VALID_ENVIRONMENTS:
        raise RuntimeError(
            "ENVIRONMENT debe ser development, staging o production"
        )

    if env == "development":
        return

    missing = [
        name for name, value in {
            "MONGO_URL": (os.environ.get("MONGO_URL") or "").strip(),
            "DB_NAME": db_name,
            "APP_BASE_URL": app_base_url,
            "PUBLIC_BASE_URL": public_base_url,
            "CORS_ORIGINS": ",".join(cors_origins),
        }.items() if not value
    ]
    if missing:
        raise RuntimeError(
            "[GUARDRAIL DE SEGURIDAD] Faltan variables obligatorias para "
            f"{env}: {', '.join(missing)}"
        )

    if any("*" in origin for origin in cors_origins):
        raise RuntimeError(
            "[GUARDRAIL DE SEGURIDAD] CORS_ORIGINS no puede contener comodines fuera de desarrollo"
        )

    urls = {
        "APP_BASE_URL": app_base_url,
        "PUBLIC_BASE_URL": public_base_url,
        **{f"CORS_ORIGINS[{index}]": origin for index, origin in enumerate(cors_origins)},
    }
    invalid_urls = [
        name for name, value in urls.items()
        if not _is_secure_public_origin(value)
    ]
    if invalid_urls:
        raise RuntimeError(
            "[GUARDRAIL DE SEGURIDAD] Staging y producción requieren HTTPS en: "
            + ", ".join(invalid_urls)
        )
    if app_base_url not in cors_origins:
        raise RuntimeError(
            "[GUARDRAIL DE SEGURIDAD] APP_BASE_URL debe estar incluido exactamente en CORS_ORIGINS"
        )

    if env == "production":
        if "staging" in db_name or "test" in db_name:
            raise RuntimeError(
                f"[GUARDRAIL DE SEGURIDAD] Producción no puede iniciarse usando la base de datos de pruebas '{db_name}'. "
                "Definí DB_NAME=latus-crm-production en las variables de Railway de Producción."
            )
        if mp_token.startswith("TEST-"):
            raise RuntimeError(
                "[GUARDRAIL DE SEGURIDAD] Producción no puede iniciarse usando credenciales de prueba de Mercado Pago ('TEST-...'). "
                "Definí tu MERCADOPAGO_ACCESS_TOKEN de producción ('APP_USR-...') en las variables de Railway de Producción."
            )
        if mp_token and mp_mode != "production":
            raise RuntimeError(
                "[GUARDRAIL DE SEGURIDAD] Producción requiere MERCADOPAGO_MODE=production"
            )
        if "staging" in app_base_url or "staging" in public_base_url:
            raise RuntimeError(
                "[GUARDRAIL DE SEGURIDAD] Producción no puede usar URLs de Staging"
            )
    elif env == "staging":
        if db_name and "production" in db_name:
            raise RuntimeError(
                f"[GUARDRAIL DE SEGURIDAD] Staging no puede iniciarse usando la base de datos de producción '{db_name}'. "
                "Definí DB_NAME=latus-crm-staging en las variables de Railway de Staging."
            )
        if mp_token and mp_mode != "test":
            raise RuntimeError(
                "[GUARDRAIL DE SEGURIDAD] Staging requiere MERCADOPAGO_MODE=test"
            )


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
            validate_environment_guardrails()
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
        raw = getattr(self.__class__._resolve(), name)
        return tenant_collection(raw, name)

    def __getitem__(self, key):
        return tenant_collection(self.__class__._resolve()[key], key)

    @classmethod
    def raw_collection(cls, name: str):
        """Bypass tenant scoping for bootstrap and identity operations only."""
        return cls._resolve()[name]


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


_INTERNAL_ORGANIZATION_FIELDS = {
    "internal_notes", "billing_updated_by", "provider_customer_id",
    "provider_subscription_id", "provider_preapproval_id",
    "provider_plan_code", "provider_status", "provider_last_synced_at",
    "provider_last_payment_status", "provider_last_payment_at",
    "provider_last_invoice_modified", "provider_checkout_created_at",
    "billing_manual_override", "migration_origin",
}


def _public_organization(doc: dict | None) -> dict | None:
    clean = _strip_oid(doc)
    if not clean:
        return clean
    return {key: value for key, value in clean.items() if key not in _INTERNAL_ORGANIZATION_FIELDS}


LEAD_STATUSES = ["new", "contacted", "qualified", "proposal", "won", "lost"]
CONV_STATUSES = ["open", "pending", "resolved"]
PRIORITIES = ["low", "medium", "high"]
ROLES = ["admin", "supervisor", "agent", "viewer"]
LEGACY_ROLE_MAP = {"sales_agent": "agent"}


def _normalize_role(r: str | None) -> str:
    if not r:
        return "agent"
    return LEGACY_ROLE_MAP.get(r, r)


PERMISSION_MODULES = (
    "crm", "inbox", "calendar", "catalog", "ai", "users", "whatsapp", "settings",
)
PERMISSION_LEVELS = ("view", "use", "admin")
KNOWN_PERMISSIONS = {
    f"{module}_{level}" for module in PERMISSION_MODULES for level in PERMISSION_LEVELS
}

# Compatibilidad con roles guardados antes de incorporar permisos por nivel.
LEGACY_PERMISSION_MAP = {
    "write_crm": {"crm_use", "inbox_use", "calendar_use"},
    "write_catalog": {"catalog_admin"},
    "message_any": {"inbox_admin"},
    "trigger_bot_any": {"ai_use"},
    "manage_users": {"users_admin"},
    "configure_whatsapp": {"whatsapp_admin"},
    "configure_ai": {"ai_admin", "calendar_admin"},
    "manage_settings": {"settings_admin"},
}
KNOWN_PERMISSIONS.update(LEGACY_PERMISSION_MAP)

DEFAULT_ROLE_PERMISSIONS = {
    "admin": [f"{module}_admin" for module in PERMISSION_MODULES],
    "supervisor": [
        "crm_admin", "inbox_admin", "calendar_admin", "catalog_admin",
        "ai_use", "users_view", "whatsapp_view", "settings_view",
    ],
    "agent": ["crm_use", "inbox_use", "calendar_use", "catalog_view", "ai_use"],
    "viewer": ["crm_view", "inbox_view", "calendar_view", "catalog_view", "ai_view"],
}

LEGACY_DEFAULT_ROLE_PERMISSIONS = {
    "admin": {"manage_users", "configure_whatsapp", "configure_ai", "manage_settings", "write_catalog", "message_any", "trigger_bot_any", "write_crm"},
    "supervisor": {"write_catalog", "trigger_bot_any", "write_crm"},
    "agent": {"write_crm"},
    "viewer": set(),
}


def expand_permissions(permissions: list[str] | set[str] | tuple[str, ...]) -> list[str]:
    """Expand legacy aliases and the admin > use > view hierarchy."""
    expanded = {str(permission) for permission in permissions or []}
    for legacy, replacements in LEGACY_PERMISSION_MAP.items():
        if legacy in expanded:
            expanded.update(replacements)
    for module in PERMISSION_MODULES:
        if f"{module}_admin" in expanded:
            expanded.update({f"{module}_use", f"{module}_view"})
        elif f"{module}_use" in expanded:
            expanded.add(f"{module}_view")
    # Old frontend/tests continue to work while every screen migrates to the
    # explicit module permissions.
    if "crm_use" in expanded:
        expanded.add("write_crm")
    if "catalog_admin" in expanded:
        expanded.add("write_catalog")
    if "inbox_admin" in expanded:
        expanded.add("message_any")
    if "ai_use" in expanded:
        expanded.add("trigger_bot_any")
    if "users_admin" in expanded:
        expanded.add("manage_users")
    if "whatsapp_admin" in expanded:
        expanded.add("configure_whatsapp")
    if "settings_admin" in expanded:
        expanded.add("manage_settings")
    return sorted(expanded)


def permission_granted(permissions: list[str] | set[str], permission: str) -> bool:
    return permission in expand_permissions(permissions)


def normalize_role_permissions(permissions: list[str]) -> list[str]:
    selected = {str(permission).strip() for permission in permissions or []}
    unknown = sorted(selected - KNOWN_PERMISSIONS)
    if unknown:
        raise ValueError(f"Permisos desconocidos: {', '.join(unknown)}")
    expanded = set(expand_permissions(selected))
    # Guardamos un único nivel por módulo; los permisos anteriores se convierten
    # a la matriz nueva al guardar el rol.
    normalized: set[str] = set()
    for module in PERMISSION_MODULES:
        for level in reversed(PERMISSION_LEVELS):
            key = f"{module}_{level}"
            if key in expanded:
                normalized.add(key)
                break
    return sorted(normalized)


async def get_role_permissions(role: str) -> list[str]:
    try:
        doc = await db.roles.find_one({"role_id": role})
        if doc and "permissions" in doc:
            stored = set(doc["permissions"] or [])
            if role == "admin" or stored == LEGACY_DEFAULT_ROLE_PERMISSIONS.get(role):
                return expand_permissions(list(DEFAULT_ROLE_PERMISSIONS.get(role, [])))
            return expand_permissions(list(stored))
    except Exception:
        pass
    return expand_permissions(list(DEFAULT_ROLE_PERMISSIONS.get(role, [])))


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
        if not permission_granted(perms, permission):
            raise HTTPException(status_code=403, detail="Permiso insuficiente")
        return user
    return _dep


def require_any_perm(*permissions: str):
    """Allow access when at least one of the requested module permissions exists."""
    async def _dep(user: User = Depends(get_current_user)) -> User:
        perms = await get_role_permissions(user.role)
        if not any(permission_granted(perms, permission) for permission in permissions):
            raise HTTPException(status_code=403, detail="Permiso insuficiente")
        return user
    return _dep

# ---------------------------------------------------------------------------
# Billing and license foundation
# ---------------------------------------------------------------------------

DEFAULT_PLAN_CODE = "starter"
SUBSCRIPTION_STATUSES = {
    "not_configured", "trialing", "active", "past_due", "canceled", "suspended",
}
LICENSE_STATUSES = {"not_configured", "active", "grace_period", "suspended", "expired"}
AI_VARIABLE_BILLING_STATES = {"disabled", "simulation", "pilot", "active"}


def _default_organization_ai_variable_billing() -> dict[str, Any]:
    return {
        "state": "disabled",
        "billing_start_date": None,
        "fx_buffer_percent": None,
        "ai_fee_percent": None,
        "min_net_margin_percent": None,
        "min_ai_margin_percent": None,
        "profitability_enforcement": None,
    }


def _organization_ai_variable_billing(organization: dict) -> dict[str, Any]:
    """Return a backwards-compatible, safe tenant billing configuration."""
    raw = organization.get("ai_variable_billing")
    raw = raw if isinstance(raw, dict) else {}
    state = str(raw.get("state") or "disabled").lower()
    if state not in AI_VARIABLE_BILLING_STATES:
        state = "disabled"
    fee_override = organization.get("ai_fee_percent")
    if fee_override is None:
        fee_override = raw.get("ai_fee_percent")
    return {
        **_default_organization_ai_variable_billing(),
        **raw,
        "state": state,
        "ai_fee_percent": fee_override,
    }


def _effective_ai_settlement_policy(organization: dict, policy: dict) -> dict:
    """Overlay the tenant's explicit economic guardrails on the global policy."""
    effective = dict(policy)
    organization_billing = _organization_ai_variable_billing(organization)
    overrides = {
        "fx_buffer_percent": organization_billing.get("fx_buffer_percent"),
        "min_net_margin_percent": organization_billing.get("min_net_margin_percent"),
        "min_ai_margin_percent": organization_billing.get("min_ai_margin_percent"),
        "profitability_enforcement": organization_billing.get("profitability_enforcement"),
    }
    for field, value in overrides.items():
        if value is not None and value != "":
            effective[field] = value
    return effective

PLAN_CATALOG: dict[str, dict[str, Any]] = {
    "base": {
        "code": "base", "name": "Base heredado", "monthly_price_ars": 0,
        "description": "Acceso conservado para empresas creadas antes de habilitar la facturación.",
        "limits": {"users": 5, "contacts": 5000, "monthly_ai_tokens": PLAN_MONTHLY_AI_TOKENS["base"]},
        "features": ["CRM completo", "Agenda", "Catálogo", "Bandeja y automatizaciones"],
        "is_public": False,
    },
    "starter": {
        "code": "starter", "name": "Inicial", "monthly_price_ars": 45000,
        "description": "Para equipos pequeños que empiezan a ordenar ventas y atención.",
        "limits": {"users": 3, "contacts": 1500, "monthly_ai_tokens": PLAN_MONTHLY_AI_TOKENS["starter"]},
        "features": ["CRM y pipeline", "Agenda y recordatorios", "Catálogo", "1 canal de WhatsApp"],
        "is_public": True,
    },
    "growth": {
        "code": "growth", "name": "Crecimiento", "monthly_price_ars": 95000,
        "description": "Para negocios con varios agentes, más automatización y seguimiento.",
        "limits": {"users": 10, "contacts": 10000, "monthly_ai_tokens": PLAN_MONTHLY_AI_TOKENS["growth"]},
        "features": ["Todo Inicial", "Roles personalizados", "Consumo de IA ampliado", "Hasta 2 canales"],
        "is_public": True,
        "highlighted": True,
    },
    "scale": {
        "code": "scale", "name": "Escala", "monthly_price_ars": 185000,
        "description": "Para operaciones con múltiples equipos y necesidades avanzadas.",
        "limits": {"users": 30, "contacts": 50000, "monthly_ai_tokens": PLAN_MONTHLY_AI_TOKENS["scale"]},
        "features": ["Todo Crecimiento", "Soporte prioritario", "Mayor capacidad", "Hasta 5 canales"],
        "is_public": True,
    },
}


def _parse_billing_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            text_value = str(value).strip()
            if len(text_value) == 10:
                text_value = f"{text_value}T23:59:59-03:00"
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def subscription_access_state(organization: dict, *, at: Optional[datetime] = None) -> dict:
    """Return the effective access decision for an organization.

    ``not_configured`` remains enabled so existing installations are not locked
    when this billing foundation is deployed. Platform admins can later move an
    organization to a managed status explicitly.
    """
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if organization.get("status", "active") != "active":
        return {"allowed": False, "mode": "blocked", "reason": "organization_inactive"}

    license_status = organization.get("license_status") or "not_configured"
    if license_status in {"suspended", "expired"}:
        return {"allowed": False, "mode": "blocked", "reason": f"license_{license_status}"}

    subscription_status = organization.get("subscription_status") or "not_configured"
    if subscription_status == "suspended":
        return {"allowed": False, "mode": "blocked", "reason": "subscription_suspended"}

    if subscription_status == "past_due" or license_status == "grace_period":
        grace_end = _parse_billing_datetime(organization.get("grace_ends_at"))
        allowed = grace_end is not None and grace_end >= now
        return {
            "allowed": allowed,
            "mode": "grace" if allowed else "blocked",
            "reason": None if allowed else "payment_overdue",
            "expires_at": grace_end.isoformat() if grace_end else None,
        }

    if subscription_status in {"not_configured", "active"}:
        return {"allowed": True, "mode": subscription_status, "reason": None}

    if subscription_status == "trialing":
        trial_end = _parse_billing_datetime(organization.get("trial_ends_at"))
        allowed = trial_end is None or trial_end >= now
        return {
            "allowed": allowed,
            "mode": "trial" if allowed else "blocked",
            "reason": None if allowed else "trial_expired",
            "expires_at": trial_end.isoformat() if trial_end else None,
        }

    if subscription_status == "canceled":
        period_end = _parse_billing_datetime(organization.get("current_period_end"))
        allowed = period_end is not None and period_end >= now
        return {
            "allowed": allowed,
            "mode": "canceled_pending" if allowed else "blocked",
            "reason": None if allowed else "subscription_canceled",
            "expires_at": period_end.isoformat() if period_end else None,
        }

    return {"allowed": False, "mode": "blocked", "reason": "subscription_suspended"}


def _platform_admin_emails() -> set[str]:
    return {
        email.strip().lower()
        for email in (os.environ.get("PLATFORM_ADMIN_EMAILS") or "").split(",")
        if email.strip()
    }


def is_platform_admin_email(email: str | None) -> bool:
    return bool(email and email.strip().lower() in _platform_admin_emails())


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
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    plan_code: Optional[str] = None
    subscription_status: Optional[str] = None
    license_status: Optional[str] = None
    subscription_access: bool = True
    is_platform_admin: bool = False
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


class Organization(BaseModel):
    organization_id: str = Field(default_factory=lambda: new_id("org"))
    name: str
    slug: Optional[str] = None
    status: str = "active"
    plan_code: str = DEFAULT_PLAN_CODE
    subscription_status: str = "trialing"
    license_status: str = "active"
    trial_started_at: Optional[str] = Field(default_factory=now_iso)
    trial_ends_at: Optional[str] = Field(
        default_factory=lambda: (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    )
    current_period_end: Optional[str] = None
    grace_ends_at: Optional[str] = None
    billing_email: Optional[str] = None
    billing_cycle: str = "monthly"
    ai_fee_percent: Optional[float] = None
    ai_variable_billing: dict[str, Any] = Field(
        default_factory=_default_organization_ai_variable_billing
    )
    requested_plan_code: Optional[str] = None
    billing_request_status: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: Optional[str] = None


class OrganizationCreate(BaseModel):
    name: str


class OrganizationUpdate(BaseModel):
    name: str


class BillingPlanRequest(BaseModel):
    plan_code: str
    notes: Optional[str] = None


class BillingCheckoutRequest(BaseModel):
    plan_code: str
    billing_email: Optional[str] = None


class PlatformSubscriptionUpdate(BaseModel):
    plan_code: Optional[str] = None
    subscription_status: Optional[str] = None
    license_status: Optional[str] = None
    trial_ends_at: Optional[str] = None
    current_period_end: Optional[str] = None
    grace_ends_at: Optional[str] = None
    billing_email: Optional[str] = None
    internal_notes: Optional[str] = None
    ai_fee_percent: Optional[float] = None


class PlatformOrganizationCreate(BaseModel):
    name: str
    billing_email: Optional[str] = None
    plan_code: str = "starter"
    subscription_status: str = "active"
    license_status: str = "active"
    trial_days: Optional[int] = 0
    duration_months: Optional[int] = 12
    admin_name: Optional[str] = None
    admin_email: Optional[str] = None
    admin_password: Optional[str] = None
    internal_notes: Optional[str] = None
    ai_fee_percent: Optional[float] = None


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

def _raw_collection(name: str):
    """Return an unscoped collection for global identity/bootstrap records."""
    if isinstance(db, _DBProxy):
        return _DBProxy.raw_collection(name)
    return getattr(db, name)


def _request_session_token(request: Request) -> Optional[str]:
    token = request.cookies.get("session_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token


async def _membership_for_user(user_id: str, organization_id: Optional[str] = None) -> Optional[dict]:
    memberships = _raw_collection("memberships")
    query: dict[str, Any] = {"user_id": user_id, "status": "active"}
    if organization_id:
        query["organization_id"] = organization_id
    return await memberships.find_one(query, {"_id": 0})


async def _resolve_session_organization(session: dict, user_doc: Optional[dict] = None) -> Optional[str]:
    organization_id = session.get("organization_id")
    membership = await _membership_for_user(session["user_id"], organization_id) if organization_id else None
    if membership:
        return organization_id
    if user_doc and user_doc.get("default_organization_id"):
        membership = await _membership_for_user(session["user_id"], user_doc["default_organization_id"])
    if not membership:
        membership = await _membership_for_user(session["user_id"])
    if not membership:
        if not user_doc:
            return None
        organization_id = await _ensure_existing_user_organization(user_doc)
        membership = await _membership_for_user(session["user_id"], organization_id)
        if not membership:
            return None
    organization_id = membership["organization_id"]
    await _raw_collection("user_sessions").update_one(
        {"session_token": session["session_token"]},
        {"$set": {"organization_id": organization_id}},
    )
    return organization_id


async def _decorate_user_for_organization(user_doc: dict, organization_id: str) -> User:
    membership = await _membership_for_user(user_doc["user_id"], organization_id)
    if not membership:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": organization_id, "status": "active"}, {"_id": 0}
    )
    if not organization:
        raise HTTPException(status_code=403, detail="La empresa no está activa")
    access = subscription_access_state(organization)
    merged = {
        **user_doc,
        "name": membership.get("display_name") or user_doc.get("name"),
        "role": membership.get("role", user_doc.get("role", "agent")),
        "work_areas": membership.get("work_areas", user_doc.get("work_areas") or []),
        "organization_id": organization_id,
        "organization_name": organization.get("name"),
        "plan_code": organization.get("plan_code") or "base",
        "subscription_status": organization.get("subscription_status") or "not_configured",
        "license_status": organization.get("license_status") or "not_configured",
        "subscription_access": bool(access["allowed"]),
        "is_platform_admin": is_platform_admin_email(user_doc.get("email")),
    }
    user = User(**merged)
    user.permissions = await get_role_permissions(user.role)
    return user


def _subscription_route_is_exempt(request: Request) -> bool:
    path = request.url.path
    if path.startswith("/api/platform/") or path.startswith("/api/billing/"):
        return True
    if path in {
        "/api/auth/me", "/api/auth/logout", "/api/auth/password/change",
        "/api/organizations/current",
    }:
        return True
    if request.method == "GET" and path == "/api/organizations":
        return True
    if path.startswith("/api/organizations/") and path.endswith("/switch"):
        return True
    return False

async def get_current_user(request: Request) -> User:
    token = _request_session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await _raw_collection("user_sessions").find_one({"session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = session["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    user_doc = await _raw_collection("users").find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user_doc:
        raise HTTPException(status_code=401, detail="User not found")
    if not user_doc.get("active", True):
        raise HTTPException(status_code=403, detail="Account deactivated")
    organization_id = await _resolve_session_organization(session, user_doc)
    if not organization_id:
        raise HTTPException(status_code=403, detail="El usuario no pertenece a una empresa activa")
    set_organization_id(organization_id)
    request.state.organization_id = organization_id
    user = await _decorate_user_for_organization(user_doc, organization_id)
    if (
        not user.subscription_access
        and not user.is_platform_admin
        and not _subscription_route_is_exempt(request)
    ):
        raise HTTPException(status_code=402, detail={
            "code": "subscription_required",
            "message": "La suscripción de esta empresa requiere atención para continuar.",
            "subscription_status": user.subscription_status,
            "license_status": user.license_status,
        })
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
    if not permission_granted(perms, "crm_use"):
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
        organization_id = await _ensure_existing_user_organization({**existing, **upd})
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
        organization_id = await _create_owner_organization(user_id, data["name"])

    session_token = data["session_token"]
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "organization_id": organization_id,
        "expires_at": expires_at.isoformat(),
        "created_at": now_iso(),
    })

    response.set_cookie(
        key="session_token", value=session_token, httponly=True,
        secure=True, samesite="none", path="/", max_age=7 * 24 * 60 * 60,
    )
    set_organization_id(organization_id)
    user_doc = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return await _decorate_user_for_organization(user_doc, organization_id)


@api_router.get("/auth/me", response_model=User)
async def auth_me(user: User = Depends(get_current_user)):
    return user


async def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Acceso reservado al administrador de la plataforma")
    return user


LEGACY_MULTIEMPRESA_MIGRATION_ID = "legacy_single_tenant_to_multiempresa_v1"
LEGACY_DEFAULT_ORGANIZATION_ID = "org_legacy_default"
LEGACY_DEFAULT_ORGANIZATION_MONGO_ID = "migration:legacy_default_organization"


async def _get_or_create_legacy_default_organization() -> dict:
    """Return one stable organization for all records from the legacy app.

    The deterministic Mongo ``_id`` makes concurrent first starts converge on
    the same document. Existing organizations always win so reruns never move
    an already-migrated installation to a different tenant.
    """
    organizations = _raw_collection("organizations")
    existing = await organizations.find_one(
        {"status": "active"}, {"_id": 0}, sort=[("created_at", 1)]
    )
    if existing:
        return existing

    legacy = Organization(
        organization_id=LEGACY_DEFAULT_ORGANIZATION_ID,
        name=(os.environ.get("DEFAULT_ORGANIZATION_NAME") or "Latus CRM").strip(),
        plan_code="base",
        subscription_status="not_configured",
        license_status="not_configured",
        trial_started_at=None,
        trial_ends_at=None,
    ).model_dump()
    legacy.update({
        "_id": LEGACY_DEFAULT_ORGANIZATION_MONGO_ID,
        "migration_origin": "legacy_single_tenant",
    })
    try:
        await organizations.update_one(
            {"_id": LEGACY_DEFAULT_ORGANIZATION_MONGO_ID},
            {"$setOnInsert": legacy},
            upsert=True,
        )
    except DuplicateKeyError:
        # Another worker won the deterministic upsert.
        pass
    organization = await organizations.find_one(
        {"_id": LEGACY_DEFAULT_ORGANIZATION_MONGO_ID}, {"_id": 0}
    )
    if organization and not organization.get("organization_id") and not isinstance(db, _DBProxy):
        # Some old adapters upsert only the filter and silently ignore
        # ``$setOnInsert``. Complete that partial document in place.
        await organizations.update_one(
            {"_id": LEGACY_DEFAULT_ORGANIZATION_MONGO_ID},
            {"$set": {key: value for key, value in legacy.items() if key != "_id"}},
        )
        organization = await organizations.find_one(
            {"_id": LEGACY_DEFAULT_ORGANIZATION_MONGO_ID}, {"_id": 0}
        )
    if not organization:
        organization = await organizations.find_one(
            {"status": "active"}, {"_id": 0}, sort=[("created_at", 1)]
        )
    if not organization and not isinstance(db, _DBProxy):
        # Compatibility for the lightweight in-memory adapters used by old
        # tests, which do not implement Mongo's ``$setOnInsert`` semantics.
        await organizations.insert_one(dict(legacy))
        organization = await organizations.find_one(
            {"organization_id": LEGACY_DEFAULT_ORGANIZATION_ID}, {"_id": 0}
        ) or legacy
    if not organization:
        raise RuntimeError("No se pudo crear la empresa heredada de forma atómica")
    return organization


async def _ensure_legacy_membership(organization_id: str, user_doc: dict) -> dict:
    memberships = _raw_collection("memberships")
    existing = await memberships.find_one({"user_id": user_doc["user_id"]}, {"_id": 0})
    if existing:
        return existing
    membership_id = f"migration:{organization_id}:{user_doc['user_id']}"
    membership = {
        "_id": membership_id,
        "organization_id": organization_id,
        "user_id": user_doc["user_id"],
        "role": _normalize_role(user_doc.get("role")),
        "status": "active" if user_doc.get("active", True) else "inactive",
        "work_areas": user_doc.get("work_areas") or [],
        "calendar_settings": user_doc.get("calendar_settings"),
        "display_name": user_doc.get("name"),
        "created_at": user_doc.get("created_at") or now_iso(),
        "migrated_from_legacy": True,
    }
    try:
        await memberships.update_one(
            {"_id": membership_id}, {"$setOnInsert": membership}, upsert=True
        )
    except DuplicateKeyError:
        pass
    result = await memberships.find_one({"_id": membership_id}, {"_id": 0})
    if result and not result.get("user_id") and not isinstance(db, _DBProxy):
        await memberships.update_one(
            {"_id": membership_id},
            {"$set": {key: value for key, value in membership.items() if key != "_id"}},
        )
        result = await memberships.find_one({"_id": membership_id}, {"_id": 0})
    if not result:
        result = await memberships.find_one({"user_id": user_doc["user_id"]}, {"_id": 0})
    if not result and not isinstance(db, _DBProxy):
        # See the adapter compatibility note in the organization helper.
        await memberships.insert_one(dict(membership))
        result = await memberships.find_one({"user_id": user_doc["user_id"]}, {"_id": 0}) \
            or membership
    if not result:
        raise RuntimeError(f"No se pudo crear la membresía heredada de {user_doc['user_id']}")
    return result


async def _create_owner_organization(user_id: str, user_name: str) -> str:
    organization = Organization(name=f"Empresa de {user_name.strip() or 'nuevo usuario'}").model_dump()
    await _raw_collection("organizations").insert_one(organization)
    await _raw_collection("memberships").insert_one({
        "organization_id": organization["organization_id"],
        "user_id": user_id,
        "role": "admin",
        "status": "active",
        "work_areas": [],
        "display_name": user_name.strip() or "Nuevo usuario",
        "created_at": now_iso(),
    })
    await _raw_collection("users").update_one(
        {"user_id": user_id},
        {"$set": {"default_organization_id": organization["organization_id"]}},
    )
    return organization["organization_id"]


async def _backfill_legacy_adapter_memberships(organization_id: str) -> None:
    """Keep older in-memory DB adapters usable while they migrate to tenancy."""
    if isinstance(db, _DBProxy):
        return
    users = await _raw_collection("users").find({}, {"_id": 0}).to_list(1000)
    memberships = _raw_collection("memberships")
    for legacy_user in users:
        user_id = legacy_user.get("user_id")
        if not user_id:
            continue
        existing = await memberships.find_one({
            "organization_id": organization_id, "user_id": user_id,
        })
        if not existing:
            await memberships.insert_one({
                "organization_id": organization_id,
                "user_id": user_id,
                "role": _normalize_role(legacy_user.get("role")),
                "status": "active" if legacy_user.get("active", True) else "inactive",
                "work_areas": legacy_user.get("work_areas") or [],
                "calendar_settings": legacy_user.get("calendar_settings"),
                "display_name": legacy_user.get("name"),
                "created_at": legacy_user.get("created_at") or now_iso(),
            })


async def _ensure_existing_user_organization(user_doc: dict) -> str:
    membership = await _membership_for_user(
        user_doc["user_id"], user_doc.get("default_organization_id")
    )
    if not membership:
        membership = await _membership_for_user(user_doc["user_id"])
    if membership:
        await _backfill_legacy_adapter_memberships(membership["organization_id"])
        return membership["organization_id"]
    organization = await _get_or_create_legacy_default_organization()
    organization_id = organization["organization_id"]
    await _ensure_legacy_membership(organization_id, user_doc)
    await _raw_collection("users").update_one(
        {"user_id": user_doc["user_id"]},
        {"$set": {"default_organization_id": organization_id}},
    )
    await _backfill_legacy_adapter_memberships(organization_id)
    return organization_id


@api_router.get("/organizations")
async def list_organizations(user: User = Depends(get_current_user)):
    memberships = await _raw_collection("memberships").find(
        {"user_id": user.user_id, "status": "active"}, {"_id": 0}
    ).to_list(100)
    ids = [membership["organization_id"] for membership in memberships]
    organizations = await _raw_collection("organizations").find(
        {"organization_id": {"$in": ids}, "status": "active"}, {"_id": 0}
    ).sort("name", 1).to_list(100)
    membership_by_org = {item["organization_id"]: item for item in memberships}
    return [{
        **_public_organization(organization),
        "role": membership_by_org[organization["organization_id"]].get("role", "agent"),
        "is_current": organization["organization_id"] == user.organization_id,
    } for organization in organizations]


@api_router.get("/organizations/current")
async def get_current_organization(user: User = Depends(get_current_user)):
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": user.organization_id}, {"_id": 0}
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return _public_organization(organization)


@api_router.post("/organizations")
async def create_organization(payload: OrganizationCreate, request: Request,
                              user: User = Depends(get_current_user)):
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Ingresá un nombre válido para la empresa")
    organization = Organization(name=name).model_dump()
    await _raw_collection("organizations").insert_one(organization)
    await _raw_collection("memberships").insert_one({
        "organization_id": organization["organization_id"],
        "user_id": user.user_id,
        "role": "admin",
        "status": "active",
        "work_areas": [],
        "display_name": user.name,
        "created_at": now_iso(),
    })
    token = _request_session_token(request)
    if token:
        await _raw_collection("user_sessions").update_one(
            {"session_token": token}, {"$set": {"organization_id": organization["organization_id"]}}
        )
    set_organization_id(organization["organization_id"])
    await _seed_roles()
    return {**_strip_oid(organization), "role": "admin", "is_current": True}


@api_router.post("/organizations/{organization_id}/switch", response_model=User)
async def switch_organization(organization_id: str, request: Request,
                              user: User = Depends(get_current_user)):
    membership = await _membership_for_user(user.user_id, organization_id)
    if not membership:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": organization_id, "status": "active"}, {"_id": 0}
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Empresa no encontrada o inactiva")
    token = _request_session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Sesión no encontrada")
    await _raw_collection("user_sessions").update_one(
        {"session_token": token, "user_id": user.user_id},
        {"$set": {"organization_id": organization_id, "updated_at": now_iso()}},
    )
    set_organization_id(organization_id)
    user_doc = await _raw_collection("users").find_one({"user_id": user.user_id}, {"_id": 0})
    return await _decorate_user_for_organization(user_doc, organization_id)


@api_router.patch("/organizations/current")
async def update_current_organization(payload: OrganizationUpdate,
                                      user: User = Depends(require_perm("settings_admin"))):
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Ingresá un nombre válido para la empresa")
    await _raw_collection("organizations").update_one(
        {"organization_id": user.organization_id},
        {"$set": {"name": name, "updated_at": now_iso()}},
    )
    return _public_organization(await _raw_collection("organizations").find_one(
        {"organization_id": user.organization_id}, {"_id": 0}
    ))


# ---------------------------------------------------------------------------
# Plans, subscriptions and platform licenses
# ---------------------------------------------------------------------------

MERCADOPAGO_API_BASE_URL = "https://api.mercadopago.com"


class MercadoPagoAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _mercadopago_settings() -> dict:
    token = (os.environ.get("MERCADOPAGO_ACCESS_TOKEN") or "").strip()
    webhook_secret = (os.environ.get("MERCADOPAGO_WEBHOOK_SECRET") or "").strip()
    return {
        "access_token": token,
        "webhook_secret": webhook_secret,
        "checkout_ready": bool(token and webhook_secret and APP_BASE_URL),
        "webhook_ready": bool(token and webhook_secret),
    }


def _mercadopago_external_reference(organization_id: str, plan_code: str) -> str:
    return f"latus:{organization_id}:{plan_code}"


def _parse_mercadopago_external_reference(value: Any) -> tuple[Optional[str], Optional[str]]:
    parts = str(value or "").split(":")
    if len(parts) != 3 or parts[0] != "latus":
        return None, None
    return parts[1] or None, parts[2] or None


def _mercadopago_signature_is_valid(
    signature: str | None,
    request_id: str | None,
    data_id: str | None,
    secret: str,
) -> bool:
    """Validate Mercado Pago's HMAC-SHA256 Webhook signature.

    The manifest order and omission rules follow the official Webhooks guide.
    Replay safety is handled separately through the persisted request id.
    """
    if not signature or not secret:
        return False
    values: dict[str, str] = {}
    for part in signature.split(","):
        key, separator, value = part.strip().partition("=")
        if separator and key in {"ts", "v1"}:
            values[key] = value.strip()
    timestamp = values.get("ts")
    supplied_hash = values.get("v1")
    if not timestamp or not supplied_hash:
        return False
    manifest = ""
    if data_id:
        manifest += f"id:{str(data_id).lower()};"
    if request_id:
        manifest += f"request-id:{request_id};"
    manifest += f"ts:{timestamp};"
    expected_hash = hmac.new(
        secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_hash, supplied_hash)


async def _mercadopago_request(
    method: str,
    path: str,
    *,
    payload: Optional[dict] = None,
) -> dict:
    settings = _mercadopago_settings()
    if not settings["access_token"]:
        raise MercadoPagoAPIError("Mercado Pago no está configurado")
    headers = {
        "Authorization": f"Bearer {settings['access_token']}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.request(
                method,
                f"{MERCADOPAGO_API_BASE_URL}{path}",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise MercadoPagoAPIError("No se pudo conectar con Mercado Pago") from exc
    if response.status_code >= 400:
        try:
            provider_detail = response.json()
        except ValueError:
            provider_detail = {}
        message = (
            provider_detail.get("message")
            or provider_detail.get("error")
            or "Mercado Pago rechazó la operación"
        )
        raise MercadoPagoAPIError(str(message), status_code=response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise MercadoPagoAPIError("Mercado Pago devolvió una respuesta inválida") from exc


def _billing_grace_end() -> str:
    try:
        days = max(1, min(30, int(os.environ.get("BILLING_GRACE_DAYS") or "7")))
    except ValueError:
        days = 7
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def _find_organization_for_provider_resource(resource: dict) -> Optional[dict]:
    preapproval_id = str(resource.get("preapproval_id") or resource.get("id") or "")
    if preapproval_id:
        organization = await _raw_collection("organizations").find_one(
            {"provider_preapproval_id": preapproval_id}, {"_id": 0}
        )
        if organization:
            return organization
    organization_id, _ = _parse_mercadopago_external_reference(
        resource.get("external_reference")
    )
    if not organization_id:
        return None
    return await _raw_collection("organizations").find_one(
        {"organization_id": organization_id}, {"_id": 0}
    )


async def _apply_mercadopago_preapproval(resource: dict) -> Optional[str]:
    organization = await _find_organization_for_provider_resource(resource)
    if not organization:
        return None
    provider_id = str(resource.get("id") or "")
    current_provider_id = str(organization.get("provider_preapproval_id") or "")
    if current_provider_id and provider_id and current_provider_id != provider_id:
        return organization["organization_id"]
    _, referenced_plan = _parse_mercadopago_external_reference(
        resource.get("external_reference")
    )
    plan_code = referenced_plan if referenced_plan in PLAN_CATALOG else organization.get("plan_code")
    provider_status = str(resource.get("status") or "").lower()
    manual_override = bool(organization.get("billing_manual_override")) or bool(
        organization.get("billing_updated_by")
        and organization.get("license_status") in {"suspended", "expired"}
    )
    update: dict[str, Any] = {
        "provider_preapproval_id": provider_id or current_provider_id or None,
        "provider_subscription_id": provider_id or current_provider_id or None,
        "provider_plan_code": plan_code,
        "provider_status": provider_status or None,
        "provider_last_synced_at": now_iso(),
        "updated_at": now_iso(),
    }
    next_payment_date = resource.get("next_payment_date")
    if provider_status == "authorized" and not manual_override:
        update.update({
            "plan_code": plan_code,
            "subscription_status": "active",
            "license_status": "active",
            "current_period_end": next_payment_date or organization.get("current_period_end"),
            "grace_ends_at": None,
            "billing_request_status": "approved",
            "requested_plan_code": None,
        })
    elif provider_status == "paused" and not manual_override:
        update.update({
            "subscription_status": "past_due",
            "license_status": "grace_period",
            "grace_ends_at": organization.get("grace_ends_at") or _billing_grace_end(),
        })
    elif provider_status == "canceled" and not manual_override:
        update.update({
            "subscription_status": "canceled",
            "license_status": "active",
            "current_period_end": organization.get("current_period_end") or now_iso(),
        })
    await _raw_collection("organizations").update_one(
        {"organization_id": organization["organization_id"]}, {"$set": update}
    )
    if provider_status == "authorized" and not manual_override:
        await _raw_collection("billing_requests").update_many(
            {"organization_id": organization["organization_id"], "status": "pending"},
            {"$set": {
                "status": "approved",
                "resolved_at": now_iso(),
                "resolved_by": "mercadopago",
            }},
        )
    return organization["organization_id"]


async def _reconcile_ai_statement_payment(organization_id: str, resource: dict,
                                          payment_status: str) -> None:
    payment = resource.get("payment") if isinstance(resource.get("payment"), dict) else resource
    payment_id = str(payment.get("id") or resource.get("id") or "")
    try:
        paid_amount = float(payment.get("transaction_amount") or resource.get("transaction_amount") or 0)
    except (TypeError, ValueError):
        paid_amount = 0.0
    candidates = await _raw_collection("ai_billing_statements").find({
        "organization_id": organization_id,
        "status": {"$in": ["applied", "payment_failed"]},
    }, {"_id": 0}).sort("applied_at", -1).to_list(10)
    statement = next(
        (item for item in candidates
         if paid_amount > 0 and abs(float(item.get("total_amount_ars") or 0) - paid_amount) < 0.01),
        None,
    )
    if not statement:
        return
    status = "paid" if payment_status == "approved" else "payment_failed"
    update = {"status": status, "provider_payment_id": payment_id or None,
              "provider_payment_status": payment_status, "updated_at": now_iso()}
    if status == "paid":
        update["paid_at"] = payment.get("date_approved") or now_iso()
    await _raw_collection("ai_billing_statements").update_one(
        {"statement_id": statement["statement_id"]}, {"$set": update}
    )


async def _apply_mercadopago_payment(resource: dict) -> Optional[str]:
    organization = await _find_organization_for_provider_resource(resource)
    if not organization:
        return None
    resource_preapproval_id = str(resource.get("preapproval_id") or "")
    current_preapproval_id = str(organization.get("provider_preapproval_id") or "")
    if (
        resource_preapproval_id and current_preapproval_id
        and resource_preapproval_id != current_preapproval_id
    ):
        return organization["organization_id"]
    payment = resource.get("payment") if isinstance(resource.get("payment"), dict) else resource
    payment_status = str(payment.get("status") or resource.get("status") or "").lower()
    provider_status = str(organization.get("provider_status") or "").lower()
    manual_override = bool(organization.get("billing_manual_override")) or bool(
        organization.get("billing_updated_by")
        and organization.get("license_status") in {"suspended", "expired"}
    )
    modified_at = resource.get("last_modified") or resource.get("date_last_updated") or now_iso()
    previous_modified = _parse_billing_datetime(organization.get("provider_last_invoice_modified"))
    incoming_modified = _parse_billing_datetime(modified_at)
    if previous_modified and incoming_modified and incoming_modified < previous_modified:
        return organization["organization_id"]
    update: dict[str, Any] = {
        "provider_last_payment_status": payment_status or None,
        "provider_last_payment_at": payment.get("date_approved") or modified_at,
        "provider_last_invoice_modified": modified_at,
        "provider_last_synced_at": now_iso(),
        "updated_at": now_iso(),
    }
    if (
        payment_status == "approved" and not manual_override
        and provider_status not in {"paused", "canceled"}
    ):
        update.update({
            "subscription_status": "active",
            "license_status": "active",
            "grace_ends_at": None,
        })
    elif (
        payment_status in {"rejected", "cancelled", "cancelled_by_collector"}
        and not manual_override and provider_status not in {"paused", "canceled"}
    ):
        update.update({
            "subscription_status": "past_due",
            "license_status": "grace_period",
            "grace_ends_at": organization.get("grace_ends_at") or _billing_grace_end(),
        })
    await _raw_collection("organizations").update_one(
        {"organization_id": organization["organization_id"]}, {"$set": update}
    )
    if payment_status in {"approved", "rejected", "cancelled", "cancelled_by_collector"}:
        await _reconcile_ai_statement_payment(
            organization["organization_id"], resource, payment_status
        )
    if payment_status == "approved":
        payment = resource.get("payment") if isinstance(resource.get("payment"), dict) else resource
        payment_id = str(payment.get("id") or resource.get("id") or "")
        paid_statement = await _raw_collection("ai_billing_statements").find_one({
            "organization_id": organization["organization_id"],
            "status": "paid", "provider_payment_id": payment_id,
        }, {"_id": 0})
        if paid_statement and current_preapproval_id and organization.get("cancel_at_period_end"):
            try:
                canceled = await _mercadopago_request(
                    "PUT", f"/preapproval/{current_preapproval_id}",
                    payload={"status": "canceled"},
                )
                canceled.setdefault("id", current_preapproval_id)
                canceled.setdefault("status", "canceled")
                await _apply_mercadopago_preapproval(canceled)
                await _raw_collection("organizations").update_one(
                    {"organization_id": organization["organization_id"]},
                    {"$set": {"cancel_at_period_end": False,
                              "cancellation_completed_at": now_iso(),
                              "current_period_end": paid_statement.get("charge_scheduled_at") or now_iso(),
                              "updated_at": now_iso()}},
                )
            except MercadoPagoAPIError:
                logger.exception("Could not finalize scheduled cancellation org=%s",
                                 organization["organization_id"])
        elif (
            paid_statement and current_preapproval_id
            and not paid_statement.get("base_amount_restored_at")
        ):
            plan_code = paid_statement.get("plan_code") or organization.get("plan_code") or "base"
            plan = PLAN_CATALOG.get(plan_code) or PLAN_CATALOG["base"]
            base_amount = float(plan["monthly_price_ars"])
            try:
                await _mercadopago_request(
                    "PUT", f"/preapproval/{current_preapproval_id}",
                    payload={
                        "reason": f"Latus CRM - Plan {plan['name']}",
                        "auto_recurring": {
                            "transaction_amount": base_amount,
                            "currency_id": "ARS",
                        },
                    },
                )
                restored_at = now_iso()
                await _raw_collection("ai_billing_statements").update_one(
                    {"statement_id": paid_statement["statement_id"]},
                    {"$set": {"base_amount_restored_at": restored_at,
                              "base_amount_restored_ars": base_amount,
                              "updated_at": restored_at},
                     "$unset": {"base_amount_restore_error": ""}},
                )
                await _raw_collection("organizations").update_one(
                    {"organization_id": organization["organization_id"]},
                    {"$set": {"next_billing_amount_ars": base_amount,
                              "next_ai_amount_ars": 0.0,
                              "updated_at": restored_at}},
                )
                await _raw_collection("billing_events").insert_one({
                    "event_id": new_id("billevt"),
                    "organization_id": organization["organization_id"],
                    "type": "ai_settlement_base_amount_restored",
                    "provider": "mercadopago",
                    "provider_resource_id": current_preapproval_id,
                    "statement_id": paid_statement["statement_id"],
                    "amount_ars": base_amount,
                    "created_at": restored_at,
                })
            except MercadoPagoAPIError as exc:
                await _raw_collection("ai_billing_statements").update_one(
                    {"statement_id": paid_statement["statement_id"]},
                    {"$set": {"base_amount_restore_error": str(exc)[:500],
                              "updated_at": now_iso()}},
                )
                logger.exception("Could not restore base subscription amount org=%s",
                                 organization["organization_id"])
    return organization["organization_id"]

def _public_plan_catalog(*, include_internal: bool = False) -> list[dict]:
    return [
        dict(plan)
        for plan in PLAN_CATALOG.values()
        if include_internal or plan.get("is_public", True)
    ]


async def _organization_ai_month_usage(organization_id: str) -> dict:
    from ai import usage as ai_usage
    now = datetime.now(timezone.utc)
    month_start = datetime.combine(now.date().replace(day=1), datetime.min.time(),
                                   tzinfo=timezone.utc)
    logs = await _raw_collection("ai_usage_logs").find(
        {"organization_id": organization_id, "created_at": {"$gte": month_start.isoformat()}},
        {"_id": 0},
    ).to_list(100_000)
    total = {"calls": 0, "tokens": 0, "base_cost_usd": 0.0,
             "ai_fee_usd": 0.0, "billable_cost_usd": 0.0,
             "period_start": month_start.isoformat(), "period_end": now.isoformat()}
    for log in logs:
        billing = ai_usage.billing_breakdown(log)
        total["calls"] += 1
        total["tokens"] += int(log.get("total_tokens") or 0)
        for field in ("base_cost_usd", "ai_fee_usd", "billable_cost_usd"):
            total[field] = round(total[field] + float(billing[field]), 6)
    return total


async def _latest_ai_statement(organization_id: str) -> Optional[dict]:
    return await _raw_collection("ai_billing_statements").find_one(
        {"organization_id": organization_id}, {"_id": 0},
        sort=[("created_at", -1)],
    )


async def _build_ai_settlement_statement(organization: dict, charge_at: datetime,
                                         policy: dict, *, at: Optional[datetime] = None) -> dict:
    from billing import ai_settlement
    at = at or datetime.now(timezone.utc)
    organization_id = organization["organization_id"]
    period_start = ai_settlement.parse_datetime(organization.get("last_ai_settlement_end")) \
        or ai_settlement.parse_datetime(organization.get("provider_last_payment_at")) \
        or ai_settlement.previous_cycle_start(charge_at)
    organization_billing = _organization_ai_variable_billing(organization)
    billing_start = ai_settlement.parse_datetime(organization_billing.get("billing_start_date"))
    if billing_start and billing_start > period_start:
        period_start = billing_start
    period_end = min(at, charge_at)
    if period_start >= period_end:
        period_start = ai_settlement.previous_cycle_start(charge_at)
    logs = await _raw_collection("ai_usage_logs").find({
        "organization_id": organization_id,
        "created_at": {"$gte": period_start.isoformat(), "$lt": period_end.isoformat()},
        "status": "success",
    }, {"_id": 0}).to_list(100_000)
    usage = ai_settlement.summarize_logs(logs)
    plan_code = organization.get("plan_code") or "base"
    plan = PLAN_CATALOG.get(plan_code) or PLAN_CATALOG["base"]
    final_cancellation = bool(organization.get("cancel_at_period_end"))
    amounts = ai_settlement.calculate_amounts(
        plan_amount_ars=0 if final_cancellation else plan["monthly_price_ars"],
        billable_cost_usd=usage["billable_cost_usd"],
        usd_to_ars_rate=policy["usd_to_ars_rate"],
        fx_buffer_percent=policy["fx_buffer_percent"],
    )
    profitability = ai_settlement.calculate_profitability_breakdown(
        plan_amount_ars=amounts["plan_amount_ars"],
        ai_amount_ars=amounts["ai_amount_ars"],
        base_cost_usd=usage["base_cost_usd"],
        usd_to_ars_rate=policy["usd_to_ars_rate"],
        mp_fee_percent=policy["mp_fee_percent"],
        tax_percent=policy["tax_percent"],
        min_margin_percent=policy["min_net_margin_percent"],
        min_ai_margin_percent=policy["min_ai_margin_percent"],
    )
    cycle = charge_at.date().isoformat()
    return {
        "statement_id": f"aistmt_{hashlib.sha256(f'{organization_id}:{cycle}'.encode()).hexdigest()[:20]}",
        "settlement_key": f"{organization_id}:{cycle}",
        "organization_id": organization_id,
        "plan_code": plan_code,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "charge_scheduled_at": charge_at.isoformat(),
        **usage,
        **amounts,
        "usd_to_ars_rate": policy["usd_to_ars_rate"],
        "exchange_rate_source": policy["exchange_rate_source"],
        "exchange_rate_observed_at": policy.get("exchange_rate_observed_at"),
        "fx_buffer_percent": policy["fx_buffer_percent"],
        "mp_fee_percent": policy["mp_fee_percent"],
        "tax_percent": policy["tax_percent"],
        "min_net_margin_percent": policy["min_net_margin_percent"],
        "min_ai_margin_percent": policy["min_ai_margin_percent"],
        "profitability_enforcement": policy["profitability_enforcement"],
        "profitability": profitability,
        "provider": "mercadopago",
        "provider_preapproval_id": organization.get("provider_preapproval_id"),
        "organization_billing_state": organization_billing["state"],
        "organization_billing_start_date": organization_billing.get("billing_start_date"),
        "organization_fee_percent": organization_billing.get("ai_fee_percent"),
        "final_cancellation": final_cancellation,
        "status": "pending",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


async def _build_pilot_settlement_preview(organization: dict, policy: dict) -> dict:
    """Build the exact pilot proposal and its blockers without side effects."""
    from billing import ai_settlement
    now = datetime.now(timezone.utc)
    organization_id = organization["organization_id"]
    organization_billing = _organization_ai_variable_billing(organization)
    blockers: list[dict[str, str]] = []

    def block(code: str, message: str) -> None:
        blockers.append({"code": code, "message": message})

    if organization_billing["state"] != "pilot":
        block("not_pilot", "La empresa debe estar en modo Piloto")
    if not policy.get("enabled"):
        block("global_policy_disabled", "El cobro automático global está desactivado")
    if str(organization.get("subscription_status") or "").lower() != "active":
        block("subscription_not_active", "La suscripción de la empresa no está activa")
    charge_at = _parse_billing_datetime(organization.get("current_period_end"))
    if not charge_at:
        block("missing_charge_date", "La suscripción no tiene una próxima fecha de cobro")
    billing_start = ai_settlement.parse_datetime(organization_billing.get("billing_start_date"))
    if charge_at and billing_start and billing_start >= min(now, charge_at):
        block("billing_not_started", "La fecha de inicio del cobro de IA todavía no comenzó")

    effective_policy = _effective_ai_settlement_policy(organization, policy)
    if not ai_settlement.rate_is_fresh(effective_policy, at=now):
        block("stale_exchange_rate", "La cotización USD/ARS está vencida o no configurada")
    if (
        not organization.get("provider_preapproval_id")
        or str(organization.get("provider_status") or "").lower() != "authorized"
    ):
        block("subscription_not_authorized", "Mercado Pago no tiene una suscripción autorizada")

    statement = None
    fingerprint = None
    if charge_at and (not billing_start or billing_start < min(now, charge_at)):
        statement = await _build_ai_settlement_statement(
            organization, charge_at, effective_policy, at=now,
        )
        fingerprint = ai_settlement.settlement_preview_fingerprint(statement)
        if float(statement.get("total_amount_ars") or 0) <= 0:
            block("no_charge", "No hay un importe pendiente para aplicar")
        profitability = statement.get("profitability") or {}
        if (
            not profitability.get("is_profitable", False)
            and effective_policy.get("profitability_enforcement") == "block"
        ):
            block(
                "insufficient_margin",
                profitability.get("warning") or "La liquidación no alcanza el margen mínimo",
            )

    return {
        "ready": not blockers and statement is not None,
        "organization_id": organization_id,
        "organization_name": organization.get("name") or organization_id,
        "blockers": blockers,
        "statement": statement,
        "preview_fingerprint": fingerprint,
        "side_effects": {
            "database_writes": False,
            "provider_calls": False,
            "mercadopago_changes": False,
        },
        "generated_at": now.isoformat(),
    }


async def _record_profitability_alert(organization: dict, statement: dict, *, blocked: bool) -> dict:
    """Create at most one open profitability alert per organization."""
    existing = await _raw_collection("system_alerts").find_one({
        "organization_id": organization["organization_id"],
        "alert_type": "insufficient_margin",
        "status": {"$in": ["unread", "read"]},
    }, {"_id": 0})
    if existing:
        return existing
    from utils.alerts import create_system_alert
    profitability = statement.get("profitability") or {}
    return await create_system_alert(
        db,
        alert_type="insufficient_margin",
        organization_id=organization["organization_id"],
        severity="critical" if blocked else "warning",
        title=(
            "Liquidación bloqueada por baja rentabilidad"
            if blocked else "Liquidación aplicada con margen en riesgo"
        ),
        message=profitability.get("warning") or "La liquidación no alcanza el margen configurado",
        metadata={
            "settlement_key": statement.get("settlement_key"),
            "total_margin_percent": profitability.get("net_margin_percent"),
            "ai_margin_percent": profitability.get("ai_net_margin_percent"),
            "min_total_margin_percent": profitability.get("min_margin_percent"),
            "min_ai_margin_percent": profitability.get("min_ai_margin_percent"),
            "total_amount_ars": statement.get("total_amount_ars"),
            "enforcement": statement.get("profitability_enforcement"),
        },
    )


async def _record_mp_update_error_alert(organization: dict, statement: dict, error: str) -> dict:
    open_alerts = await _raw_collection("system_alerts").find({
        "organization_id": organization["organization_id"],
        "alert_type": "mp_update_error",
        "status": {"$in": ["unread", "read"]},
    }, {"_id": 0}).to_list(100)
    existing = next(
        (alert for alert in open_alerts
         if (alert.get("metadata") or {}).get("statement_id") == statement.get("statement_id")),
        None,
    )
    if existing:
        return existing
    from utils.alerts import create_system_alert
    return await create_system_alert(
        db,
        alert_type="mp_update_error",
        organization_id=organization["organization_id"],
        severity="error",
        title="Mercado Pago rechazó la actualización de una liquidación",
        message=error[:500],
        metadata={
            "statement_id": statement.get("statement_id"),
            "settlement_key": statement.get("settlement_key"),
            "amount_ars": statement.get("total_amount_ars"),
        },
    )


async def _resolve_mp_update_error_alerts(organization_id: str, statement_id: str,
                                          user_id: str) -> None:
    alerts = await _raw_collection("system_alerts").find({
        "organization_id": organization_id,
        "alert_type": "mp_update_error",
        "status": {"$in": ["unread", "read"]},
    }, {"_id": 0}).to_list(100)
    resolved_at = now_iso()
    for alert in alerts:
        if (alert.get("metadata") or {}).get("statement_id") != statement_id:
            continue
        await _raw_collection("system_alerts").update_one(
            {"alert_id": alert["alert_id"]},
            {"$set": {
                "status": "resolved", "resolved_at": resolved_at,
                "resolved_by": user_id,
            }},
        )


async def _retry_failed_ai_statement(statement: dict, organization: dict,
                                     policy: dict, actor: User) -> dict:
    """Retry the frozen Mercado Pago update without rebuilding the cycle."""
    now = datetime.now(timezone.utc)
    status = str(statement.get("status") or "")
    retry_count = int(statement.get("retry_count") or 0)
    max_attempts = int(policy.get("max_retry_attempts") or 3)
    cooldown_minutes = int(policy.get("retry_cooldown_minutes") or 0)

    if status == "retrying":
        claimed_at = _parse_billing_datetime(statement.get("retry_claimed_at"))
        if claimed_at and now - claimed_at < timedelta(minutes=15):
            raise HTTPException(409, "La liquidación ya tiene un reintento en curso")
    elif status != "failed":
        messages = {
            "payment_failed": "El pago fue rechazado por el cliente; no corresponde reenviar el importe",
            "applied": "La liquidación ya fue aplicada al próximo cobro",
            "paid": "La liquidación ya fue cobrada",
            "blocked_margin": "La liquidación está bloqueada por rentabilidad, no por un error técnico",
            "retry_exhausted": "La liquidación agotó sus reintentos manuales",
        }
        raise HTTPException(409, messages.get(status, "Esta liquidación no admite reintentos"))

    if retry_count >= max_attempts:
        raise HTTPException(409, "La liquidación agotó sus reintentos manuales")
    last_retry = _parse_billing_datetime(statement.get("last_retry_at"))
    if last_retry and cooldown_minutes > 0:
        available_at = last_retry + timedelta(minutes=cooldown_minutes)
        if now < available_at:
            remaining = max(1, math.ceil((available_at - now).total_seconds() / 60))
            raise HTTPException(429, f"Esperá {remaining} minuto(s) antes de volver a intentar")
    if str(organization.get("subscription_status") or "").lower() != "active":
        raise HTTPException(409, "La suscripción de la empresa ya no está activa")
    provider_id = str(organization.get("provider_preapproval_id") or "")
    if not provider_id or str(organization.get("provider_status") or "").lower() != "authorized":
        raise HTTPException(409, "Mercado Pago no tiene una suscripción autorizada")
    frozen_provider_id = str(statement.get("provider_preapproval_id") or "")
    if frozen_provider_id and frozen_provider_id != provider_id:
        raise HTTPException(409, "La empresa cambió de suscripción en Mercado Pago; revisá el caso manualmente")

    attempt = retry_count + 1
    claim_id = new_id("retry")
    claimed_at = now_iso()
    await _raw_collection("ai_billing_statements").update_one(
        {
            "statement_id": statement["statement_id"],
            "status": status,
        },
        {"$set": {
            "status": "retrying", "retry_claim_id": claim_id,
            "retry_claimed_at": claimed_at, "updated_at": claimed_at,
        }},
    )
    claimed = await _raw_collection("ai_billing_statements").find_one(
        {"statement_id": statement["statement_id"]}, {"_id": 0},
    )
    if not claimed or claimed.get("retry_claim_id") != claim_id:
        raise HTTPException(409, "Otro administrador inició el reintento")

    try:
        provider_result = await _mercadopago_request(
            "PUT", f"/preapproval/{provider_id}",
            payload={
                "reason": (
                    "Latus CRM - Liquidación final de consumo de IA"
                    if statement.get("final_cancellation") else
                    f"Latus CRM - Plan {PLAN_CATALOG.get(statement.get('plan_code'), PLAN_CATALOG['base'])['name']} + consumo de IA"
                ),
                "auto_recurring": {
                    "transaction_amount": statement["total_amount_ars"],
                    "currency_id": "ARS",
                },
            },
        )
    except MercadoPagoAPIError as exc:
        failed_at = now_iso()
        exhausted = attempt >= max_attempts
        error_message = str(exc)[:500]
        await _raw_collection("ai_billing_statements").update_one(
            {"statement_id": statement["statement_id"], "retry_claim_id": claim_id},
            {"$set": {
                "status": "retry_exhausted" if exhausted else "failed",
                "retry_count": attempt, "last_retry_at": failed_at,
                "last_retry_status": "failed", "error": error_message,
                "provider_error_status": exc.status_code,
                "updated_at": failed_at,
            }, "$unset": {"retry_claim_id": "", "retry_claimed_at": ""}},
        )
        await _record_mp_update_error_alert(organization, statement, error_message)
        await _raw_collection("billing_events").insert_one({
            "event_id": new_id("billevt"), "organization_id": organization["organization_id"],
            "type": "ai_settlement_retry_failed", "provider": "mercadopago",
            "statement_id": statement["statement_id"], "retry_attempt": attempt,
            "provider_error_status": exc.status_code, "error": error_message,
            "actor_user_id": actor.user_id, "actor_email": actor.email,
            "created_at": failed_at,
        })
        return {
            **statement,
            "status": "retry_exhausted" if exhausted else "failed",
            "retry_count": attempt, "last_retry_at": failed_at,
            "error": error_message,
        }

    applied_at = now_iso()
    await _raw_collection("ai_billing_statements").update_one(
        {"statement_id": statement["statement_id"], "retry_claim_id": claim_id},
        {"$set": {
            "status": "applied", "retry_count": attempt,
            "last_retry_at": applied_at, "last_retry_status": "succeeded",
            "retried_by_user_id": actor.user_id, "retried_by_email": actor.email,
            "applied_at": applied_at,
            "provider_response_status": provider_result.get("status"),
            "updated_at": applied_at,
        }, "$unset": {
            "error": "", "provider_error_status": "",
            "retry_claim_id": "", "retry_claimed_at": "",
        }},
    )
    await _raw_collection("organizations").update_one(
        {"organization_id": organization["organization_id"]},
        {"$set": {
            "last_ai_settlement_end": statement["period_end"],
            "next_billing_amount_ars": statement["total_amount_ars"],
            "next_ai_amount_ars": statement["ai_amount_ars"],
            "last_ai_statement_id": statement["statement_id"],
            "updated_at": applied_at,
        }},
    )
    await _raw_collection("billing_events").insert_one({
        "event_id": new_id("billevt"), "organization_id": organization["organization_id"],
        "type": "ai_settlement_retry_succeeded", "provider": "mercadopago",
        "provider_resource_id": provider_id, "statement_id": statement["statement_id"],
        "retry_attempt": attempt, "amount_ars": statement["total_amount_ars"],
        "actor_user_id": actor.user_id, "actor_email": actor.email,
        "created_at": applied_at,
    })
    await _resolve_mp_update_error_alerts(
        organization["organization_id"], statement["statement_id"], actor.user_id,
    )
    return {
        **statement, "status": "applied", "retry_count": attempt,
        "last_retry_at": applied_at, "last_retry_status": "succeeded",
        "retried_by_user_id": actor.user_id, "retried_by_email": actor.email,
        "applied_at": applied_at,
    }


async def _apply_ai_settlement(organization: dict, policy: dict, *,
                               force: bool = False, manual: bool = False,
                               expected_preview_fingerprint: Optional[str] = None,
                               approved_by: Optional[User] = None) -> dict:
    from billing import ai_settlement
    now = datetime.now(timezone.utc)
    organization_id = organization["organization_id"]
    organization_billing = _organization_ai_variable_billing(organization)
    billing_state = organization_billing["state"]
    if billing_state == "disabled":
        return {"organization_id": organization_id, "status": "skipped", "reason": "organization_billing_disabled"}
    if billing_state == "simulation":
        return {"organization_id": organization_id, "status": "skipped", "reason": "organization_simulation_only"}
    if billing_state == "pilot" and not manual:
        return {"organization_id": organization_id, "status": "skipped", "reason": "pilot_requires_manual_run"}
    if str(organization.get("subscription_status") or "").lower() != "active":
        return {"organization_id": organization_id, "status": "skipped", "reason": "subscription_not_active"}
    charge_at = _parse_billing_datetime(organization.get("current_period_end"))
    if not charge_at:
        return {"organization_id": organization_id, "status": "skipped", "reason": "missing_charge_date"}
    billing_start = ai_settlement.parse_datetime(organization_billing.get("billing_start_date"))
    if billing_start and billing_start >= min(now, charge_at):
        return {"organization_id": organization_id, "status": "skipped", "reason": "billing_not_started"}
    if not force:
        seconds_until_charge = (charge_at - now).total_seconds()
        if seconds_until_charge <= 15 * 60:
            return {"organization_id": organization_id, "status": "skipped", "reason": "charge_too_close"}
        if seconds_until_charge > int(policy["settlement_lead_hours"]) * 3600:
            return {"organization_id": organization_id, "status": "skipped", "reason": "not_due"}
    effective_policy = _effective_ai_settlement_policy(organization, policy)
    if not ai_settlement.rate_is_fresh(effective_policy, at=now):
        return {"organization_id": organization_id, "status": "skipped", "reason": "stale_exchange_rate"}
    provider_id = str(organization.get("provider_preapproval_id") or "")
    if not provider_id or str(organization.get("provider_status") or "").lower() != "authorized":
        return {"organization_id": organization_id, "status": "skipped", "reason": "subscription_not_authorized"}

    draft = await _build_ai_settlement_statement(
        organization, charge_at, effective_policy, at=now
    )
    current_fingerprint = ai_settlement.settlement_preview_fingerprint(draft)
    if (
        expected_preview_fingerprint
        and current_fingerprint != expected_preview_fingerprint
    ):
        return {
            "organization_id": organization_id,
            "status": "preview_changed",
            "reason": "La liquidación cambió desde la vista previa",
            "preview_fingerprint": current_fingerprint,
        }
    profitability = draft.get("profitability") or {}
    if not profitability.get("is_profitable", False):
        enforcement = str(effective_policy.get("profitability_enforcement") or "block")
        blocked = enforcement == "block"
        await _record_profitability_alert(organization, draft, blocked=blocked)
        if blocked:
            existing_block = await _raw_collection("ai_billing_statements").find_one(
                {"settlement_key": draft["settlement_key"]}, {"_id": 0},
            )
            if (
                existing_block
                and existing_block.get("status") == "blocked_margin"
                and existing_block.get("profitability_fingerprint") == current_fingerprint
            ):
                return existing_block
            blocked_at = now_iso()
            blocked_statement = {
                **draft,
                "status": "blocked_margin",
                "blocked_at": blocked_at,
                "profitability_fingerprint": current_fingerprint,
                "updated_at": blocked_at,
            }
            if existing_block:
                await _raw_collection("ai_billing_statements").update_one(
                    {"settlement_key": draft["settlement_key"]},
                    {"$set": blocked_statement},
                )
            else:
                await _raw_collection("ai_billing_statements").insert_one(dict(blocked_statement))
            await _raw_collection("billing_events").insert_one({
                "event_id": new_id("billevt"),
                "organization_id": organization_id,
                "type": "ai_settlement_profitability_blocked",
                "statement_id": draft["statement_id"],
                "amount_ars": draft["total_amount_ars"],
                "total_margin_percent": profitability.get("net_margin_percent"),
                "ai_margin_percent": profitability.get("ai_net_margin_percent"),
                "created_at": blocked_at,
            })
            return blocked_statement
    if float(draft.get("total_amount_ars") or 0) <= 0 and draft.get("final_cancellation"):
        existing = await _raw_collection("ai_billing_statements").find_one(
            {"settlement_key": draft["settlement_key"]}, {"_id": 0}
        )
        if existing and existing.get("status") == "closed_no_charge":
            return existing
        statement = existing or draft
        if not existing:
            try:
                await _raw_collection("ai_billing_statements").insert_one(dict(statement))
            except DuplicateKeyError:
                statement = await _raw_collection("ai_billing_statements").find_one(
                    {"settlement_key": draft["settlement_key"]}, {"_id": 0}
                ) or draft
        canceled = await _mercadopago_request(
            "PUT", f"/preapproval/{provider_id}", payload={"status": "canceled"}
        )
        canceled.setdefault("id", provider_id)
        canceled.setdefault("status", "canceled")
        await _apply_mercadopago_preapproval(canceled)
        closed_at = now_iso()
        await _raw_collection("ai_billing_statements").update_one(
            {"statement_id": statement["statement_id"]},
            {"$set": {"status": "closed_no_charge", "paid_at": closed_at,
                      "updated_at": closed_at}},
        )
        await _raw_collection("organizations").update_one(
            {"organization_id": organization_id},
            {"$set": {"cancel_at_period_end": False,
                      "cancellation_completed_at": closed_at,
                      "current_period_end": charge_at.isoformat(), "updated_at": closed_at}},
        )
        return {**statement, "status": "closed_no_charge", "paid_at": closed_at}
    if float(draft.get("total_amount_ars") or 0) <= 0:
        return {"organization_id": organization_id, "status": "skipped", "reason": "no_charge"}
    existing = await _raw_collection("ai_billing_statements").find_one(
        {"settlement_key": draft["settlement_key"]}, {"_id": 0}
    )
    if existing and existing.get("status") in {
        "applying", "applied", "paid", "payment_failed", "failed", "retrying",
        "retry_exhausted",
    }:
        return existing
    statement = draft if existing and existing.get("status") == "blocked_margin" else (existing or draft)
    if existing and existing.get("status") == "blocked_margin":
        await _raw_collection("ai_billing_statements").update_one(
            {"settlement_key": draft["settlement_key"]}, {"$set": draft},
        )
    if not existing:
        try:
            await _raw_collection("ai_billing_statements").insert_one(dict(statement))
        except DuplicateKeyError:
            statement = await _raw_collection("ai_billing_statements").find_one(
                {"settlement_key": draft["settlement_key"]}, {"_id": 0}
            ) or draft
    approval_fields = {}
    if approved_by:
        approval_fields = {
            "approved_at": now_iso(),
            "approved_by_user_id": approved_by.user_id,
            "approved_by_email": approved_by.email,
            "approved_preview_fingerprint": expected_preview_fingerprint,
        }
    await _raw_collection("ai_billing_statements").update_one(
        {"statement_id": statement["statement_id"]},
        {"$set": {
            "status": "applying", "apply_started_at": now_iso(),
            "updated_at": now_iso(), **approval_fields,
        }},
    )
    try:
        provider_result = await _mercadopago_request(
            "PUT", f"/preapproval/{provider_id}",
            payload={
                "reason": (
                    "Latus CRM - Liquidación final de consumo de IA"
                    if statement.get("final_cancellation") else
                    f"Latus CRM - Plan {PLAN_CATALOG.get(statement['plan_code'], PLAN_CATALOG['base'])['name']} + consumo de IA"
                ),
                "auto_recurring": {
                    "transaction_amount": statement["total_amount_ars"],
                    "currency_id": "ARS",
                },
            },
        )
    except MercadoPagoAPIError as exc:
        failed_at = now_iso()
        error_message = str(exc)[:500]
        await _raw_collection("ai_billing_statements").update_one(
            {"statement_id": statement["statement_id"]},
            {"$set": {
                "status": "failed", "error": error_message,
                "provider_error_status": exc.status_code,
                "failed_at": failed_at,
                "retry_count": int(statement.get("retry_count") or 0),
                "updated_at": failed_at,
            }},
        )
        await _record_mp_update_error_alert(organization, statement, error_message)
        await _raw_collection("billing_events").insert_one({
            "event_id": new_id("billevt"), "organization_id": organization_id,
            "type": "ai_settlement_apply_failed", "provider": "mercadopago",
            "statement_id": statement["statement_id"],
            "provider_error_status": exc.status_code,
            "error": error_message, "created_at": failed_at,
        })
        return {
            **statement, "status": "failed", "error": error_message,
            "provider_error_status": exc.status_code,
            "failed_at": failed_at,
            "retry_count": int(statement.get("retry_count") or 0),
        }
    applied_at = now_iso()
    await _raw_collection("ai_billing_statements").update_one(
        {"statement_id": statement["statement_id"]},
        {"$set": {"status": "applied", "applied_at": applied_at,
                  "provider_response_status": provider_result.get("status"),
                  "updated_at": applied_at}, "$unset": {"error": ""}},
    )
    await _raw_collection("organizations").update_one(
        {"organization_id": organization_id},
        {"$set": {"last_ai_settlement_end": statement["period_end"],
                  "next_billing_amount_ars": statement["total_amount_ars"],
                  "next_ai_amount_ars": statement["ai_amount_ars"],
                  "last_ai_statement_id": statement["statement_id"],
                  "updated_at": applied_at}},
    )
    await _raw_collection("billing_events").insert_one({
        "event_id": new_id("billevt"), "organization_id": organization_id,
        "type": "ai_settlement_applied", "provider": "mercadopago",
        "provider_resource_id": provider_id, "statement_id": statement["statement_id"],
        "amount_ars": statement["total_amount_ars"], "created_at": applied_at,
        "approved_by_user_id": approved_by.user_id if approved_by else None,
        "approved_by_email": approved_by.email if approved_by else None,
        "approved_preview_fingerprint": expected_preview_fingerprint,
    })
    return {
        **statement, **approval_fields,
        "status": "applied", "applied_at": applied_at,
    }


async def process_due_ai_settlements(*, force_organization_id: Optional[str] = None,
                                     force: bool = False) -> dict:
    from billing import ai_settlement
    policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    if not policy["enabled"] and not force:
        return {"enabled": False, "processed": 0, "applied": 0, "items": []}
    updated = ai_settlement.parse_datetime(policy.get("exchange_rate_updated_at"))
    if policy.get("exchange_rate_source") == "bcra" and (
        not updated or datetime.now(timezone.utc) - updated > timedelta(hours=12)
    ):
        try:
            policy = await ai_settlement.refresh_bcra_rate(_raw_collection("pricing_config"))
        except ValueError:
            logger.exception("No se pudo actualizar la cotización BCRA para liquidaciones de IA")
    query: dict[str, Any] = {
        "subscription_status": "active", "provider_status": "authorized",
        "current_period_end": {"$ne": None},
    }
    if force_organization_id:
        query = {"organization_id": force_organization_id}
    organizations = await _raw_collection("organizations").find(query, {"_id": 0}).to_list(1000)
    if not force_organization_id:
        organizations = [
            organization for organization in organizations
            if _organization_ai_variable_billing(organization)["state"] == "active"
        ]
    items = []
    for organization in organizations:
        try:
            items.append(await _apply_ai_settlement(
                organization, policy, force=force,
                manual=bool(force_organization_id),
            ))
        except Exception as exc:
            logger.exception("AI settlement failed org=%s", organization.get("organization_id"))
            items.append({"organization_id": organization.get("organization_id"),
                          "status": "failed", "error": str(exc)[:500]})
    return {"enabled": policy["enabled"], "processed": len(items),
            "applied": sum(1 for item in items if item.get("status") == "applied"),
            "items": items}


async def _subscription_payload(organization_id: str, *, include_internal: bool = False) -> dict:
    from ai import usage as ai_usage
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": organization_id}, {"_id": 0}
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    plan_code = organization.get("plan_code") or "base"
    plan = PLAN_CATALOG.get(plan_code) or PLAN_CATALOG["base"]
    active_members = await _raw_collection("memberships").count_documents({
        "organization_id": organization_id, "status": "active",
    })
    contacts = await _raw_collection("contacts").count_documents({
        "organization_id": organization_id,
    })
    latest_request = await _raw_collection("billing_requests").find_one(
        {"organization_id": organization_id}, {"_id": 0}, sort=[("created_at", -1)]
    )
    provider_settings = _mercadopago_settings()
    return {
        "organization": organization if include_internal else _public_organization(organization),
        "plan": dict(plan),
        "access": subscription_access_state(organization),
        "usage": {"users": active_members, "contacts": contacts},
        "ai_billing": {
            "fee_percent": await ai_usage.effective_fee_percent(db, organization_id),
            "has_custom_fee": organization.get("ai_fee_percent") is not None,
            "variable_billing": _organization_ai_variable_billing(organization),
            "this_month": await _organization_ai_month_usage(organization_id),
            "latest_statement": await _latest_ai_statement(organization_id),
        },
        "latest_request": latest_request,
        "payment_provider": {
            "name": "Mercado Pago",
            "checkout_ready": provider_settings["checkout_ready"],
            "webhook_ready": provider_settings["webhook_ready"],
            "status": organization.get("provider_status"),
            "plan_code": organization.get("provider_plan_code"),
            "last_synced_at": organization.get("provider_last_synced_at"),
            "last_payment_status": organization.get("provider_last_payment_status"),
        },
    }


@api_router.get("/billing/plans")
async def list_billing_plans(user: User = Depends(get_current_user)):
    return _public_plan_catalog(include_internal=user.is_platform_admin)


@api_router.get("/billing/subscription")
async def get_billing_subscription(user: User = Depends(get_current_user)):
    return await _subscription_payload(user.organization_id)


@api_router.post("/billing/plan-requests")
async def create_billing_plan_request(
    payload: BillingPlanRequest,
    user: User = Depends(require_perm("settings_admin")),
):
    plan = PLAN_CATALOG.get(payload.plan_code)
    if not plan or not plan.get("is_public", True):
        raise HTTPException(status_code=400, detail="El plan seleccionado no está disponible")
    request_doc = {
        "request_id": new_id("billreq"),
        "organization_id": user.organization_id,
        "requested_by": user.user_id,
        "requested_by_email": user.email,
        "plan_code": payload.plan_code,
        "notes": (payload.notes or "").strip()[:1000],
        "status": "pending",
        "created_at": now_iso(),
    }
    await _raw_collection("billing_requests").insert_one(request_doc)
    await _raw_collection("organizations").update_one(
        {"organization_id": user.organization_id},
        {"$set": {
            "requested_plan_code": payload.plan_code,
            "billing_request_status": "pending",
            "updated_at": now_iso(),
        }},
    )
    return _strip_oid(request_doc)


@api_router.post("/billing/checkout")
async def create_billing_checkout(
    payload: BillingCheckoutRequest,
    user: User = Depends(require_perm("settings_admin")),
):
    plan = PLAN_CATALOG.get(payload.plan_code)
    if not plan or not plan.get("is_public", True) or plan["monthly_price_ars"] <= 0:
        raise HTTPException(status_code=400, detail="El plan seleccionado no está disponible")
    settings = _mercadopago_settings()
    if not settings["checkout_ready"]:
        raise HTTPException(
            status_code=503,
            detail="Mercado Pago todavía no está configurado para cobrar en línea",
        )
    billing_email = (payload.billing_email or user.email or "").strip().lower()
    if "@" not in billing_email:
        raise HTTPException(status_code=400, detail="Ingresá un email de facturación válido")
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": user.organization_id}, {"_id": 0}
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if bool(organization.get("billing_manual_override")) or bool(
        organization.get("billing_updated_by")
        and organization.get("license_status") in {"suspended", "expired"}
    ):
        raise HTTPException(
            status_code=403,
            detail="La licencia fue bloqueada por la administración de la plataforma",
        )

    current_provider_id = organization.get("provider_preapproval_id")
    current_provider_status = str(organization.get("provider_status") or "").lower()
    current_provider_plan = organization.get("provider_plan_code")

    async def change_active_plan(provider_id: str, *, reactivate: bool = False) -> dict:
        open_statement = await _raw_collection("ai_billing_statements").find_one(
            {"organization_id": user.organization_id, "status": "applied"},
            {"_id": 0}, sort=[("applied_at", -1)],
        )
        carried_ai_amount = 0.0
        carry_statement = False
        if open_statement:
            scheduled = _parse_billing_datetime(open_statement.get("charge_scheduled_at"))
            if scheduled and scheduled > datetime.now(timezone.utc):
                carry_statement = True
                carried_ai_amount = float(open_statement.get("ai_amount_ars") or 0)
        next_total = round(float(plan["monthly_price_ars"]) + carried_ai_amount, 2)
        provider_update = {
            "reason": f"Latus CRM - Plan {plan['name']}",
            "external_reference": _mercadopago_external_reference(
                user.organization_id, payload.plan_code
            ),
            "auto_recurring": {
                "transaction_amount": next_total,
                "currency_id": "ARS",
            },
        }
        if reactivate:
            provider_update["status"] = "authorized"
        try:
            provider_result = await _mercadopago_request(
                "PUT",
                f"/preapproval/{provider_id}",
                payload=provider_update,
            )
        except MercadoPagoAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        provider_status = str(provider_result.get("status") or "").lower()
        if provider_status != "authorized":
            await _apply_mercadopago_preapproval(provider_result)
            raise HTTPException(
                status_code=409,
                detail="Mercado Pago no dejó la suscripción activa; actualizá el estado e intentá nuevamente",
            )
        update = {
            "plan_code": payload.plan_code,
            "provider_plan_code": payload.plan_code,
            "provider_status": provider_status,
            "subscription_status": "active",
            "license_status": "active",
            "billing_email": billing_email,
            "billing_request_status": "approved",
            "requested_plan_code": None,
            "cancel_at_period_end": False,
            "provider_last_synced_at": now_iso(),
            "updated_at": now_iso(),
        }
        await _raw_collection("organizations").update_one(
            {"organization_id": user.organization_id}, {"$set": update}
        )
        if open_statement and carry_statement:
            await _raw_collection("ai_billing_statements").update_one(
                {"statement_id": open_statement["statement_id"]},
                {"$set": {
                    "original_plan_code": open_statement.get("original_plan_code") or open_statement.get("plan_code"),
                    "plan_code": payload.plan_code,
                    "plan_amount_ars": float(plan["monthly_price_ars"]),
                    "total_amount_ars": next_total,
                    "final_cancellation": False,
                    "plan_changed_before_payment_at": now_iso(),
                    "updated_at": now_iso(),
                }},
            )
        await _raw_collection("billing_requests").update_many(
            {"organization_id": user.organization_id, "status": "pending"},
            {"$set": {
                "status": "approved",
                "resolved_at": now_iso(),
                "resolved_by": user.user_id,
            }},
        )
        await _raw_collection("billing_events").insert_one({
            "event_id": new_id("billevt"),
            "organization_id": user.organization_id,
            "type": "subscription_plan_changed",
            "provider": "mercadopago",
            "provider_resource_id": str(provider_id),
            "plan_code": payload.plan_code,
            "actor_user_id": user.user_id,
            "actor_email": user.email,
            "created_at": now_iso(),
        })
        return {
            "checkout_url": None,
            "status": update["provider_status"],
            "plan_updated": True,
        }

    if current_provider_id and current_provider_status == "authorized":
        if current_provider_plan == payload.plan_code:
            return {"checkout_url": None, "status": "authorized", "plan_updated": False}
        try:
            return await change_active_plan(str(current_provider_id))
        except HTTPException as exc:
            if "not valid for callerId" in str(exc.detail) or "not_found" in str(exc.detail).lower():
                logger.warning("Clearing invalid Mercado Pago preapproval ID %s for org %s: %s", current_provider_id, user.organization_id, exc.detail)
                await _raw_collection("organizations").update_one(
                    {"organization_id": user.organization_id},
                    {"$set": {"provider_preapproval_id": None, "provider_subscription_id": None, "provider_status": None}}
                )
                current_provider_id = None
            else:
                raise
    if current_provider_id and current_provider_status == "paused":
        try:
            return await change_active_plan(str(current_provider_id), reactivate=True)
        except HTTPException as exc:
            if "not valid for callerId" in str(exc.detail) or "not_found" in str(exc.detail).lower():
                logger.warning("Clearing invalid Mercado Pago preapproval ID %s for org %s: %s", current_provider_id, user.organization_id, exc.detail)
                await _raw_collection("organizations").update_one(
                    {"organization_id": user.organization_id},
                    {"$set": {"provider_preapproval_id": None, "provider_subscription_id": None, "provider_status": None}}
                )
                current_provider_id = None
            else:
                raise
    if current_provider_id and current_provider_status == "pending":
        try:
            existing = await _mercadopago_request("GET", f"/preapproval/{current_provider_id}")
            existing_status = str(existing.get("status") or "").lower()
            if existing_status == "authorized":
                await _apply_mercadopago_preapproval(existing)
                if current_provider_plan == payload.plan_code:
                    return {"checkout_url": None, "status": "authorized", "plan_updated": False}
                return await change_active_plan(str(current_provider_id))
            if existing_status == "pending" and current_provider_plan == payload.plan_code and existing.get("init_point"):
                return {
                    "checkout_url": existing["init_point"],
                    "status": existing.get("status"),
                    "reused": True,
                }
            if existing_status == "pending":
                pending_update = {
                    "reason": f"Latus CRM - Plan {plan['name']}",
                    "external_reference": _mercadopago_external_reference(
                        user.organization_id, payload.plan_code
                    ),
                    "back_url": f"{APP_BASE_URL}/suscripcion?checkout=retorno",
                    "auto_recurring": {
                        "transaction_amount": plan["monthly_price_ars"],
                        "currency_id": "ARS",
                    },
                    "status": "pending",
                }
                updated_pending = await _mercadopago_request(
                    "PUT", f"/preapproval/{current_provider_id}", payload=pending_update
                )
                checkout_url = updated_pending.get("init_point") or existing.get("init_point")
                if not checkout_url:
                    raise HTTPException(
                        status_code=502,
                        detail="Mercado Pago no devolvió el enlace de pago actualizado",
                    )
                update = {
                    "billing_email": billing_email,
                    "requested_plan_code": payload.plan_code,
                    "billing_request_status": "checkout_created",
                    "provider_plan_code": payload.plan_code,
                    "provider_status": "pending",
                    "provider_last_synced_at": now_iso(),
                    "updated_at": now_iso(),
                }
                await _raw_collection("organizations").update_one(
                    {"organization_id": user.organization_id}, {"$set": update}
                )
                await _raw_collection("billing_events").insert_one({
                    "event_id": new_id("billevt"),
                    "organization_id": user.organization_id,
                    "type": "checkout_plan_changed",
                    "provider": "mercadopago",
                    "provider_resource_id": str(current_provider_id),
                    "plan_code": payload.plan_code,
                    "actor_user_id": user.user_id,
                    "actor_email": user.email,
                    "created_at": now_iso(),
                })
                return {
                    "checkout_url": checkout_url,
                    "status": "pending",
                    "reused": True,
                    "plan_updated": True,
                }
            await _mercadopago_request(
                "PUT", f"/preapproval/{current_provider_id}", payload={"status": "canceled"}
            )
        except MercadoPagoAPIError as exc:
            if "not valid for callerId" in str(exc) or "not_found" in str(exc).lower() or getattr(exc, "status_code", None) in {400, 404}:
                logger.warning("Clearing invalid Mercado Pago preapproval ID %s for org %s: %s", current_provider_id, user.organization_id, exc)
                await _raw_collection("organizations").update_one(
                    {"organization_id": user.organization_id},
                    {"$set": {"provider_preapproval_id": None, "provider_subscription_id": None, "provider_status": None}}
                )
            else:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

    payer_email_to_send = billing_email
    if settings.get("access_token", "").startswith("TEST-") and "@testuser.com" not in billing_email and "test_user" not in billing_email:
        try:
            logger.info("Creating Mercado Pago test buyer user for test mode...")
            test_user = await _mercadopago_request("POST", "/users/test_user", payload={"site_id": "MLA"})
            if test_user.get("email"):
                payer_email_to_send = test_user["email"]
                logger.info("Using generated test buyer email: %s", payer_email_to_send)
        except Exception as exc:
            logger.warning("Could not auto-create test user via MP API: %s", exc)

    provider_payload = {
        "reason": f"Latus CRM - Plan {plan['name']}",
        "external_reference": _mercadopago_external_reference(
            user.organization_id, payload.plan_code
        ),
        "payer_email": payer_email_to_send,
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": plan["monthly_price_ars"],
            "currency_id": "ARS",
        },
        "back_url": f"{APP_BASE_URL}/suscripcion?checkout=retorno",
        "status": "pending",
    }
    try:
        preapproval = await _mercadopago_request(
            "POST", "/preapproval", payload=provider_payload
        )
    except MercadoPagoAPIError as exc:
        exc_str = str(exc).lower()
        if ("must be real or test users" in exc_str or "payer_email" in exc_str) and payer_email_to_send == billing_email:
            try:
                logger.info("Retrying preapproval creation with fresh test buyer email...")
                test_user = await _mercadopago_request("POST", "/users/test_user", payload={"site_id": "MLA"})
                test_email = test_user.get("email")
                if test_email:
                    provider_payload["payer_email"] = test_email
                    preapproval = await _mercadopago_request("POST", "/preapproval", payload=provider_payload)
                else:
                    raise exc
            except Exception as test_exc:
                logger.warning("Could not auto-create test user for MP preapproval: %s", test_exc)
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        else:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not preapproval.get("id") or not preapproval.get("init_point"):
        raise HTTPException(status_code=502, detail="Mercado Pago no devolvió el enlace de pago")
    update = {
        "billing_email": billing_email,
        "requested_plan_code": payload.plan_code,
        "billing_request_status": "checkout_created",
        "provider_preapproval_id": str(preapproval["id"]),
        "provider_subscription_id": str(preapproval["id"]),
        "provider_plan_code": payload.plan_code,
        "provider_status": str(preapproval.get("status") or "pending").lower(),
        "provider_checkout_created_at": now_iso(),
        "provider_last_synced_at": now_iso(),
        "updated_at": now_iso(),
    }
    await _raw_collection("organizations").update_one(
        {"organization_id": user.organization_id}, {"$set": update}
    )
    await _raw_collection("billing_events").insert_one({
        "event_id": new_id("billevt"),
        "organization_id": user.organization_id,
        "type": "checkout_created",
        "provider": "mercadopago",
        "provider_resource_id": str(preapproval["id"]),
        "plan_code": payload.plan_code,
        "actor_user_id": user.user_id,
        "actor_email": user.email,
        "created_at": now_iso(),
    })
    return {
        "checkout_url": preapproval["init_point"],
        "status": preapproval.get("status"),
        "reused": False,
    }


@api_router.post("/billing/reconcile")
async def reconcile_billing_subscription(
    user: User = Depends(require_perm("settings_admin")),
):
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": user.organization_id}, {"_id": 0}
    )
    provider_id = organization.get("provider_preapproval_id") if organization else None
    if not provider_id:
        raise HTTPException(status_code=400, detail="Todavía no hay una suscripción para actualizar")
    try:
        preapproval = await _mercadopago_request("GET", f"/preapproval/{provider_id}")
    except MercadoPagoAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await _apply_mercadopago_preapproval(preapproval)
    await _raw_collection("billing_events").insert_one({
        "event_id": new_id("billevt"),
        "organization_id": user.organization_id,
        "type": "subscription_reconciled",
        "provider": "mercadopago",
        "provider_resource_id": str(provider_id),
        "actor_user_id": user.user_id,
        "created_at": now_iso(),
    })
    return await _subscription_payload(user.organization_id)


@api_router.post("/billing/cancel")
async def cancel_billing_subscription(
    user: User = Depends(require_perm("settings_admin")),
):
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": user.organization_id}, {"_id": 0}
    )
    provider_id = organization.get("provider_preapproval_id") if organization else None
    if not provider_id:
        raise HTTPException(status_code=400, detail="No hay una suscripción para cancelar")
    if str(organization.get("provider_status") or "").lower() == "canceled":
        return await _subscription_payload(user.organization_id)
    from billing import ai_settlement
    variable_policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    organization_billing = _organization_ai_variable_billing(organization)
    if (
        variable_policy["enabled"]
        and organization_billing["state"] in {"pilot", "active"}
        and str(organization.get("provider_status") or "").lower() == "authorized"
    ):
        requested_at = now_iso()
        await _raw_collection("organizations").update_one(
            {"organization_id": user.organization_id},
            {"$set": {"cancel_at_period_end": True,
                      "cancellation_requested_at": requested_at,
                      "cancellation_requested_by": user.user_id,
                      "updated_at": requested_at}},
        )
        await _raw_collection("billing_events").insert_one({
            "event_id": new_id("billevt"), "organization_id": user.organization_id,
            "type": "subscription_cancellation_scheduled", "provider": "mercadopago",
            "provider_resource_id": str(provider_id), "actor_user_id": user.user_id,
            "actor_email": user.email, "created_at": requested_at,
        })
        return await _subscription_payload(user.organization_id)
    try:
        provider_result = await _mercadopago_request(
            "PUT", f"/preapproval/{provider_id}", payload={"status": "canceled"}
        )
    except MercadoPagoAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    provider_result.setdefault("id", str(provider_id))
    provider_result.setdefault("status", "canceled")
    if str(provider_result.get("status") or "").lower() != "canceled":
        raise HTTPException(
            status_code=409,
            detail="Mercado Pago todavía no confirmó la cancelación",
        )
    await _apply_mercadopago_preapproval(provider_result)
    await _raw_collection("billing_events").insert_one({
        "event_id": new_id("billevt"),
        "organization_id": user.organization_id,
        "type": "subscription_canceled_by_customer",
        "provider": "mercadopago",
        "provider_resource_id": str(provider_id),
        "actor_user_id": user.user_id,
        "actor_email": user.email,
        "created_at": now_iso(),
    })
    return await _subscription_payload(user.organization_id)


@api_router.post("/webhooks/mercadopago")
async def mercadopago_webhook(request: Request):
    settings = _mercadopago_settings()
    if not settings["webhook_ready"]:
        raise HTTPException(status_code=503, detail="Webhook de Mercado Pago no configurado")
    raw_body = await request.body()
    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Notificación inválida")
    query_data_id = request.query_params.get("data.id")
    request_id = request.headers.get("x-request-id")
    if not _mercadopago_signature_is_valid(
        request.headers.get("x-signature"),
        request_id,
        query_data_id,
        settings["webhook_secret"],
    ):
        raise HTTPException(status_code=401, detail="Firma de Mercado Pago inválida")
    event_type = str(request.query_params.get("type") or body.get("type") or "")
    body_data = body.get("data") if isinstance(body.get("data"), dict) else {}
    data_id = str(query_data_id or body_data.get("id") or "")
    if not data_id:
        raise HTTPException(status_code=400, detail="La notificación no incluye un recurso")
    event_key = request_id or hashlib.sha256(
        request.headers.get("x-signature", "").encode("utf-8") + raw_body
    ).hexdigest()
    existing = await _raw_collection("billing_events").find_one(
        {"provider_event_id": event_key}, {"_id": 0}
    )
    if existing:
        return {"ok": True, "duplicate": True}

    try:
        organization_id: Optional[str] = None
        if event_type == "subscription_preapproval":
            resource = await _mercadopago_request("GET", f"/preapproval/{data_id}")
            organization_id = await _apply_mercadopago_preapproval(resource)
        elif event_type == "subscription_authorized_payment":
            resource = await _mercadopago_request("GET", f"/authorized_payments/{data_id}")
            preapproval_id = resource.get("preapproval_id")
            if preapproval_id:
                preapproval = await _mercadopago_request(
                    "GET", f"/preapproval/{preapproval_id}"
                )
                organization_id = await _apply_mercadopago_preapproval(preapproval)
            organization_id = await _apply_mercadopago_payment(resource) or organization_id
        elif event_type == "payment":
            resource = await _mercadopago_request("GET", f"/v1/payments/{data_id}")
            transaction_data = (
                resource.get("point_of_interaction", {}).get("transaction_data", {})
                if isinstance(resource.get("point_of_interaction"), dict)
                else {}
            )
            preapproval_id = transaction_data.get("subscription_id")
            if preapproval_id:
                resource["preapproval_id"] = preapproval_id
            organization_id = await _apply_mercadopago_payment(resource)
        else:
            return {"ok": True, "ignored": True}
    except MercadoPagoAPIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        await _raw_collection("billing_events").insert_one({
            "event_id": new_id("billevt"),
            "organization_id": organization_id,
            "type": "provider_webhook_processed",
            "provider": "mercadopago",
            "provider_event_id": event_key,
            "provider_event_type": event_type,
            "provider_resource_id": data_id,
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        return {"ok": True, "duplicate": True}
    return {"ok": True, "matched": bool(organization_id)}


@api_router.get("/platform/organizations")
async def platform_list_organizations(platform_admin: User = Depends(require_platform_admin)):
    from ai import usage as ai_usage
    from billing import ai_settlement
    organizations = await _raw_collection("organizations").find({}, {"_id": 0}).sort(
        "created_at", -1
    ).to_list(500)
    organizations = [item for item in organizations if item.get("organization_id")]
    month_start = datetime.combine(
        datetime.now(timezone.utc).date().replace(day=1),
        datetime.min.time(), tzinfo=timezone.utc,
    ).isoformat()
    usage_logs = await _raw_collection("ai_usage_logs").find(
        {"created_at": {"$gte": month_start}}, {"_id": 0}
    ).to_list(100_000)
    usage_by_organization: dict[str, dict] = {}
    for log in usage_logs:
        organization_id = log.get("organization_id")
        if not organization_id:
            continue
        billing = ai_usage.billing_breakdown(log)
        item = usage_by_organization.setdefault(organization_id, {
            "calls": 0, "tokens": 0, "base_cost_usd": 0.0,
            "ai_fee_usd": 0.0, "billable_cost_usd": 0.0,
        })
        item["calls"] += 1
        item["tokens"] += int(log.get("total_tokens") or 0)
        for field in ("base_cost_usd", "ai_fee_usd", "billable_cost_usd"):
            item[field] = round(item[field] + float(billing[field]), 6)
    billing_policy = await ai_usage.load_billing_policy(db)
    settlement_policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    rows = []
    for organization in organizations:
        organization_id = organization["organization_id"]
        fee_override = organization.get("ai_fee_percent")
        usage = usage_by_organization.get(organization_id, {
            "calls": 0, "tokens": 0, "base_cost_usd": 0.0,
            "ai_fee_usd": 0.0, "billable_cost_usd": 0.0,
        })
        effective_policy = _effective_ai_settlement_policy(organization, settlement_policy)
        rate = float(effective_policy.get("usd_to_ars_rate") or 0.0)
        plan = PLAN_CATALOG.get(organization.get("plan_code") or "base") or PLAN_CATALOG["base"]
        if rate > 0:
            amounts = ai_settlement.calculate_amounts(
                plan_amount_ars=float(plan.get("monthly_price_ars") or 0.0),
                billable_cost_usd=float(usage["billable_cost_usd"]),
                usd_to_ars_rate=rate,
                fx_buffer_percent=float(effective_policy["fx_buffer_percent"]),
            )
            profitability = ai_settlement.calculate_profitability_breakdown(
                plan_amount_ars=amounts["plan_amount_ars"],
                ai_amount_ars=amounts["ai_amount_ars"],
                base_cost_usd=float(usage["base_cost_usd"]),
                usd_to_ars_rate=rate,
                mp_fee_percent=float(effective_policy["mp_fee_percent"]),
                tax_percent=float(effective_policy["tax_percent"]),
                min_margin_percent=float(effective_policy["min_net_margin_percent"]),
                min_ai_margin_percent=float(effective_policy["min_ai_margin_percent"]),
            )
        else:
            profitability = {
                "status": "not_configured", "is_profitable": False,
                "warning": "Configurá la cotización USD/ARS para calcular rentabilidad",
                "net_margin_percent": None, "ai_net_margin_percent": None,
            }
        rows.append({
            **organization,
            "ai_variable_billing": _organization_ai_variable_billing(organization),
            "access": subscription_access_state(organization),
            "active_users": await _raw_collection("memberships").count_documents({
                "organization_id": organization_id, "status": "active",
            }),
            "contacts": await _raw_collection("contacts").count_documents({
                "organization_id": organization_id,
            }),
            "latest_request": await _raw_collection("billing_requests").find_one(
                {"organization_id": organization_id}, {"_id": 0}, sort=[("created_at", -1)]
            ),
            "ai_billing": {
                "fee_percent": (
                    ai_usage.validate_fee_percent(fee_override)
                    if fee_override is not None
                    else billing_policy["default_fee_percent"]
                ),
                "has_custom_fee": fee_override is not None,
                "this_month": usage,
                "profitability": profitability,
                "profitability_enforcement": effective_policy["profitability_enforcement"],
                "latest_statement": await _latest_ai_statement(organization_id),
            },
        })
    return rows


@api_router.post("/platform/organizations")
async def platform_create_organization(
    payload: PlatformOrganizationCreate,
    platform_admin: User = Depends(require_platform_admin),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre de la empresa es obligatorio")
    if payload.plan_code not in PLAN_CATALOG:
        raise HTTPException(status_code=400, detail="Plan inválido")
    if payload.subscription_status not in SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=400, detail="Estado de suscripción inválido")
    if payload.license_status not in LICENSE_STATUSES:
        raise HTTPException(status_code=400, detail="Estado de licencia inválido")

    now = datetime.now(timezone.utc)
    trial_ends_at = None
    if payload.trial_days and payload.trial_days > 0:
        trial_ends_at = (now + timedelta(days=payload.trial_days)).isoformat()

    current_period_end = None
    if payload.duration_months and payload.duration_months > 0:
        current_period_end = (now + timedelta(days=payload.duration_months * 30)).isoformat()

    ai_fee = None
    if payload.ai_fee_percent is not None:
        from ai import usage as ai_usage
        try:
            ai_fee = ai_usage.validate_fee_percent(payload.ai_fee_percent)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    org_doc = Organization(
        name=name,
        plan_code=payload.plan_code,
        subscription_status=payload.subscription_status,
        license_status=payload.license_status,
        trial_ends_at=trial_ends_at,
        current_period_end=current_period_end,
        billing_email=(payload.billing_email or "").strip().lower() or None,
        internal_notes=(payload.internal_notes or "").strip() or None,
        ai_fee_percent=ai_fee,
        billing_manual_override=True,
    ).model_dump()

    await _raw_collection("organizations").insert_one(org_doc)

    admin_user_info = None
    if payload.admin_email and payload.admin_email.strip():
        admin_email = payload.admin_email.strip().lower()
        existing_user = await _raw_collection("users").find_one({"email": admin_email}, {"_id": 0})

        if existing_user:
            user_id = existing_user["user_id"]
            existing_mem = await _raw_collection("memberships").find_one(
                {"user_id": user_id, "organization_id": org_doc["organization_id"]}
            )
            if not existing_mem:
                await _raw_collection("memberships").insert_one({
                    "organization_id": org_doc["organization_id"],
                    "user_id": user_id,
                    "role": "admin",
                    "status": "active",
                    "work_areas": [],
                    "display_name": payload.admin_name or existing_user.get("name") or "Admin",
                    "created_at": now.isoformat(),
                })
            admin_user_info = {"user_id": user_id, "email": admin_email, "created": False}
        else:
            raw_pwd = payload.admin_password or "Latus12345!"
            user_id = new_id("user")
            hashed_pwd = hash_password(raw_pwd)
            user_doc = {
                "user_id": user_id,
                "email": admin_email,
                "name": (payload.admin_name or "").strip() or name,
                "hashed_password": hashed_pwd,
                "role": "admin",
                "default_organization_id": org_doc["organization_id"],
                "created_at": now.isoformat(),
            }
            await _raw_collection("users").insert_one(user_doc)
            await _raw_collection("memberships").insert_one({
                "organization_id": org_doc["organization_id"],
                "user_id": user_id,
                "role": "admin",
                "status": "active",
                "work_areas": [],
                "display_name": user_doc["name"],
                "created_at": now.isoformat(),
            })
            admin_user_info = {"user_id": user_id, "email": admin_email, "temp_password": raw_pwd, "created": True}

    return {
        "ok": True,
        "organization": org_doc,
        "admin_user": admin_user_info,
    }


@api_router.patch("/platform/organizations/{organization_id}/subscription")
async def platform_update_subscription(
    organization_id: str,
    payload: PlatformSubscriptionUpdate,
    platform_admin: User = Depends(require_platform_admin),
):
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": organization_id}, {"_id": 0}
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    update = payload.model_dump(exclude_unset=True)
    if "plan_code" in update and update["plan_code"] not in PLAN_CATALOG:
        raise HTTPException(status_code=400, detail="Plan inválido")
    if "subscription_status" in update and update["subscription_status"] not in SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=400, detail="Estado de suscripción inválido")
    if "license_status" in update and update["license_status"] not in LICENSE_STATUSES:
        raise HTTPException(status_code=400, detail="Estado de licencia inválido")
    for field in ("trial_ends_at", "current_period_end", "grace_ends_at"):
        if field not in update or update[field] in (None, ""):
            if field in update:
                update[field] = None
            continue
        parsed = _parse_billing_datetime(update[field])
        if not parsed:
            raise HTTPException(status_code=400, detail=f"Fecha inválida en {field}")
        update[field] = parsed.isoformat()
    if "billing_email" in update:
        billing_email = (update["billing_email"] or "").strip().lower()
        if billing_email and "@" not in billing_email:
            raise HTTPException(status_code=400, detail="Email de facturación inválido")
        update["billing_email"] = billing_email or None
    if "internal_notes" in update:
        update["internal_notes"] = (update["internal_notes"] or "").strip()[:2000]
    if "ai_fee_percent" in update and update["ai_fee_percent"] is not None:
        from ai import usage as ai_usage
        try:
            update["ai_fee_percent"] = ai_usage.validate_fee_percent(
                update["ai_fee_percent"]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "ai_fee_percent" in update:
        organization_billing = _organization_ai_variable_billing(organization)
        organization_billing.update({
            "ai_fee_percent": update["ai_fee_percent"],
            "updated_at": now_iso(),
            "updated_by": platform_admin.user_id,
        })
        update["ai_variable_billing"] = organization_billing
    if update.get("subscription_status") == "active":
        update["billing_request_status"] = "approved"
        update["requested_plan_code"] = None
    if (
        update.get("subscription_status") == "suspended"
        or update.get("license_status") in {"suspended", "expired"}
    ):
        update["billing_manual_override"] = True
    elif (
        update.get("subscription_status") == "active"
        and update.get("license_status") == "active"
    ):
        update["billing_manual_override"] = False
    update.update({
        "updated_at": now_iso(),
        "billing_updated_by": platform_admin.user_id,
    })
    await _raw_collection("organizations").update_one(
        {"organization_id": organization_id}, {"$set": update}
    )
    if update.get("subscription_status") == "active":
        await _raw_collection("billing_requests").update_many(
            {"organization_id": organization_id, "status": "pending"},
            {"$set": {
                "status": "approved",
                "resolved_at": now_iso(),
                "resolved_by": platform_admin.user_id,
            }},
        )
    await _raw_collection("billing_events").insert_one({
        "event_id": new_id("billevt"),
        "organization_id": organization_id,
        "type": "subscription_updated",
        "changes": update,
        "actor_user_id": platform_admin.user_id,
        "actor_email": platform_admin.email,
        "created_at": now_iso(),
    })
    return await _subscription_payload(organization_id, include_internal=True)


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
    user_doc = await _raw_collection("users").find_one({"user_id": user_id}, {"_id": 0})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    organization_id = await _ensure_existing_user_organization(user_doc)
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "organization_id": organization_id,
        "expires_at": expires_at.isoformat(),
        "created_at": now_iso(),
    })
    response.set_cookie(
        key="session_token", value=session_token, httponly=True,
        secure=True, samesite="none", path="/", max_age=7 * 24 * 60 * 60,
    )
    set_organization_id(organization_id)
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
    return await _decorate_user_for_organization(user_doc, get_organization_id())


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
            organization_id = await _ensure_existing_user_organization(user)
            set_organization_id(organization_id)
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
async def list_roles(user: User = Depends(require_perm("users_admin"))):
    docs = await db.roles.find({}, {"_id": 0}).to_list(100)
    # If empty, return defaults
    if not docs:
        docs = [
            {"role_id": rid, "name": {"admin": "Administrador", "supervisor": "Supervisor", "agent": "Agente", "viewer": "Sólo lectura"}.get(rid, rid.capitalize()), "permissions": perms, "is_default": True}
            for rid, perms in DEFAULT_ROLE_PERMISSIONS.items()
        ]
    for doc in docs:
        role_id = doc.get("role_id")
        stored = set(doc.get("permissions") or [])
        if role_id == "admin" or stored == LEGACY_DEFAULT_ROLE_PERMISSIONS.get(role_id):
            doc["permissions"] = DEFAULT_ROLE_PERMISSIONS.get(role_id, [])
    return docs

@api_router.post("/roles")
async def create_custom_role(payload: RoleCreate, user: User = Depends(require_perm("users_admin"))):
    rid = payload.role_id.strip().lower()
    if not rid or not payload.name.strip():
        raise HTTPException(status_code=400, detail="ID de rol y nombre son requeridos")
    if rid in DEFAULT_ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail="No se puede sobreescribir un rol del sistema")
    
    # Check if exists
    exist = await db.roles.find_one({"role_id": rid})
    if exist:
        raise HTTPException(status_code=400, detail="El rol ya existe")
        
    try:
        permissions = normalize_role_permissions(payload.permissions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc = {
        "role_id": rid,
        "name": payload.name.strip(),
        "permissions": permissions,
        "is_default": False
    }
    await db.roles.insert_one(doc)
    return {"ok": True, "role": doc}

@api_router.put("/roles/{role_id}")
async def update_role(role_id: str, payload: RoleUpdatePayload, user: User = Depends(require_perm("users_admin"))):
    rid = role_id.strip().lower()
    if rid == "admin":
        raise HTTPException(status_code=400, detail="El rol Administrador siempre conserva acceso total")
    try:
        permissions = normalize_role_permissions(payload.permissions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    exist = await db.roles.find_one({"role_id": rid})
    if not exist:
        # If it's a default role, we can create/upsert it in DB
        if rid in DEFAULT_ROLE_PERMISSIONS:
            doc = {
                "role_id": rid,
                "name": rid.capitalize(),
                "permissions": permissions,
                "is_default": True
            }
            await db.roles.insert_one(doc)
            return {"ok": True, "role": doc}
        raise HTTPException(status_code=404, detail="Rol no encontrado")
        
    update = {"permissions": permissions}
    if payload.name:
        update["name"] = payload.name.strip()
        
    await db.roles.update_one({"role_id": rid}, {"$set": update})
    return {"ok": True}

@api_router.delete("/roles/{role_id}")
async def delete_custom_role(role_id: str, user: User = Depends(require_perm("users_admin"))):
    rid = role_id.strip().lower()
    if rid in DEFAULT_ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail="No se pueden borrar roles del sistema")
        
    exist = await db.roles.find_one({"role_id": rid})
    if not exist:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
        
    # Check if any user is currently using this role
    in_use = await _raw_collection("memberships").find_one({
        "organization_id": user.organization_id, "role": rid, "status": "active",
    })
    if in_use:
        raise HTTPException(status_code=400, detail="No se puede borrar el rol porque está siendo usado por uno o más usuarios")
        
    await db.roles.delete_one({"role_id": rid})
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
async def list_work_areas(admin: User = Depends(require_perm("users_view"))):
    docs = await db.work_areas.find({}, {"_id": 0}).to_list(100)
    return docs


@api_router.post("/admin/work-areas")
async def create_work_area(payload: WorkAreaCreate, admin: User = Depends(require_perm("users_admin"))):
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
async def delete_work_area(wa_id: str, admin: User = Depends(require_perm("users_admin"))):
    wa_id = wa_id.strip().lower()
    exist = await db.work_areas.find_one({"id": wa_id})
    if not exist:
        raise HTTPException(status_code=404, detail="Área de trabajo no encontrada")
        
    await db.work_areas.delete_one({"id": wa_id})
    # Remove from all users
    await _raw_collection("memberships").update_many(
        {"organization_id": admin.organization_id}, {"$pull": {"work_areas": wa_id}}
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
        "role": _normalize_role(d.get("membership_role", d.get("role"))),
        "is_active": bool(d.get("membership_active", d.get("active", True))) and not d.get("deleted_at"),
        "active": bool(d.get("membership_active", d.get("active", True))) and not d.get("deleted_at"),
        "auth_provider": (d.get("auth_provider") or "google").lower(),
        "has_password": bool(d.get("password_hash")),
        "last_login_at": d.get("last_login_at"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
        "deleted_at": d.get("deleted_at"),
        "work_areas": d.get("membership_work_areas", d.get("work_areas") or []),
    }
    return out


async def _team_user_docs(organization_id: str, *, include_inactive: bool = False) -> list[dict]:
    membership_query: dict[str, Any] = {"organization_id": organization_id}
    if not include_inactive:
        membership_query["status"] = "active"
    memberships = await _raw_collection("memberships").find(
        membership_query, {"_id": 0}
    ).to_list(1000)
    if not memberships:
        return []
    member_by_user = {item["user_id"]: item for item in memberships}
    users = await _raw_collection("users").find(
        {"user_id": {"$in": list(member_by_user)}, "deleted_at": None}, {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    return [{
        **user,
        "membership_role": member_by_user[user["user_id"]].get("role", "agent"),
        "membership_active": member_by_user[user["user_id"]].get("status") == "active",
        "membership_work_areas": member_by_user[user["user_id"]].get("work_areas") or [],
        "name": member_by_user[user["user_id"]].get("display_name") or user.get("name"),
        "calendar_settings": member_by_user[user["user_id"]].get(
            "calendar_settings", user.get("calendar_settings")
        ),
    } for user in users]


@api_router.get("/admin/users")
async def admin_list_users(
    admin: User = Depends(require_perm("users_view")),
    q: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    include_inactive: bool = False,
):
    docs = await _team_user_docs(admin.organization_id, include_inactive=include_inactive)
    if role:
        docs = [doc for doc in docs if doc.get("membership_role") == role]
    if is_active is not None:
        docs = [doc for doc in docs if bool(doc.get("membership_active")) is is_active]
    if q:
        needle = q.casefold()
        docs = [doc for doc in docs if needle in (doc.get("name") or "").casefold()
                or needle in (doc.get("email") or "").casefold()]
    return [_public_user(d) for d in docs]


@api_router.get("/admin/users/{uid}")
async def admin_get_user(uid: str, admin: User = Depends(require_perm("users_view"))):
    docs = await _team_user_docs(admin.organization_id, include_inactive=True)
    d = next((item for item in docs if item.get("user_id") == uid), None)
    if not d:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return _public_user(d)


@api_router.post("/admin/users")
async def admin_create_user(payload: AdminUserCreate, request: Request,
                            admin: User = Depends(require_perm("users_admin"))):
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
        already_member = await _raw_collection("memberships").find_one({
            "organization_id": admin.organization_id, "user_id": existing["user_id"],
        })
        if already_member:
            raise HTTPException(status_code=409, detail="El usuario ya pertenece a esta empresa")
        await _raw_collection("memberships").insert_one({
            "organization_id": admin.organization_id,
            "user_id": existing["user_id"],
            "role": payload.role,
            "status": "active",
            "work_areas": payload.work_areas or [],
            "display_name": payload.name.strip() or existing.get("name"),
            "created_at": now_iso(),
            "created_by": admin.user_id,
        })
        return {**_public_user({
            **existing,
            "name": payload.name.strip() or existing.get("name"),
            "membership_role": payload.role,
            "membership_active": True,
            "membership_work_areas": payload.work_areas or [],
        }), "email_sent": False, "existing_identity": True}

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
        "default_organization_id": admin.organization_id,
    }
    if password_hash:
        doc["password_hash"] = password_hash
    await db.users.insert_one(doc)
    await _raw_collection("memberships").insert_one({
        "organization_id": admin.organization_id,
        "user_id": user_id,
        "role": payload.role,
        "status": "active",
        "work_areas": payload.work_areas or [],
        "display_name": payload.name.strip(),
        "created_at": now_iso(),
        "created_by": admin.user_id,
    })
    email_sent = await send_welcome_email(user_doc=doc, auth_provider=ap, request=request)
    return {**_public_user(doc), "email_sent": email_sent}


async def _count_active_admins() -> int:
    return await _raw_collection("memberships").count_documents({
        "organization_id": get_organization_id(), "role": "admin", "status": "active",
    })


@api_router.patch("/admin/users/{uid}")
async def admin_update_user(uid: str, payload: AdminUserUpdate, admin: User = Depends(require_perm("users_use"))):
    target = await db.users.find_one({"user_id": uid}, {"_id": 0})
    membership = await _raw_collection("memberships").find_one({
        "organization_id": admin.organization_id, "user_id": uid,
    }, {"_id": 0})
    if not target or not membership:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    target_role = membership.get("role", "agent")
    permissions = await get_role_permissions(admin.role)
    can_admin_users = permission_granted(permissions, "users_admin")
    if not can_admin_users and (payload.role is not None or payload.auth_provider is not None):
        raise HTTPException(status_code=403, detail="Sólo quien administra Usuarios puede cambiar roles o métodos de acceso")
    if not can_admin_users and target_role == "admin":
        raise HTTPException(status_code=403, detail="No podés modificar una cuenta administradora")
    identity_update: dict[str, Any] = {}
    membership_update: dict[str, Any] = {}
    if payload.name is not None:
        membership_update["display_name"] = payload.name.strip()
    if payload.role is not None:
        valid_roles = await get_all_roles()
        if payload.role not in valid_roles:
            raise HTTPException(status_code=400, detail="Rol inválido")
        # Don't allow demoting the last admin
        if target_role == "admin" and payload.role != "admin":
            if await _count_active_admins() <= 1:
                raise HTTPException(status_code=400, detail="No se puede degradar al último administrador activo")
        membership_update["role"] = payload.role
    if payload.auth_provider is not None:
        membership_count = await _raw_collection("memberships").count_documents({
            "user_id": uid, "status": "active",
        })
        if membership_count > 1:
            raise HTTPException(
                status_code=409,
                detail="El usuario pertenece a más de una empresa y debe administrar su acceso desde su cuenta",
            )
        ap = payload.auth_provider.lower()
        if ap not in AUTH_PROVIDERS:
            raise HTTPException(status_code=400, detail="Método de acceso inválido")
        identity_update["auth_provider"] = ap
    if payload.is_active is not None:
        # Don't allow deactivating self
        if uid == admin.user_id and not payload.is_active:
            raise HTTPException(status_code=400, detail="No podés desactivar tu propia cuenta")
        # Don't allow deactivating the last admin
        if not payload.is_active and target_role == "admin":
            if await _count_active_admins() <= 1:
                raise HTTPException(status_code=400, detail="No se puede desactivar al último administrador activo")
        membership_update["status"] = "active" if payload.is_active else "inactive"
    if payload.work_areas is not None:
        membership_update["work_areas"] = payload.work_areas
    if not identity_update and not membership_update:
        return _public_user({**target, "membership_role": target_role,
                             "membership_active": membership.get("status") == "active",
                             "membership_work_areas": membership.get("work_areas") or []})
    if identity_update:
        identity_update["updated_at"] = now_iso()
        await db.users.update_one({"user_id": uid}, {"$set": identity_update})
    if membership_update:
        membership_update["updated_at"] = now_iso()
        await _raw_collection("memberships").update_one(
            {"organization_id": admin.organization_id, "user_id": uid},
            {"$set": membership_update},
        )
    d = await db.users.find_one({"user_id": uid}, {"_id": 0})
    updated_membership = {**membership, **membership_update}
    return _public_user({**d, "membership_role": updated_membership.get("role", "agent"),
                         "membership_active": updated_membership.get("status") == "active",
                         "membership_work_areas": updated_membership.get("work_areas") or []})


@api_router.post("/admin/users/{uid}/activate")
async def admin_activate(uid: str, admin: User = Depends(require_perm("users_use"))):
    return await admin_update_user(uid, AdminUserUpdate(is_active=True), admin=admin)


@api_router.post("/admin/users/{uid}/deactivate")
async def admin_deactivate(uid: str, admin: User = Depends(require_perm("users_use"))):
    return await admin_update_user(uid, AdminUserUpdate(is_active=False), admin=admin)


@api_router.post("/admin/users/{uid}/reset-password")
async def admin_reset_password(uid: str, request: Request,
                               admin: User = Depends(require_perm("users_use"))):
    target = await db.users.find_one({"user_id": uid}, {"_id": 0})
    membership = await _raw_collection("memberships").find_one({
        "organization_id": admin.organization_id, "user_id": uid, "status": "active",
    })
    if not target or not membership:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    ap = (target.get("auth_provider") or "").lower()
    if ap not in ("local", "both"):
        raise HTTPException(status_code=400, detail="Este usuario no usa contraseña local")
    membership_count = await _raw_collection("memberships").count_documents({
        "user_id": uid, "status": "active",
    })
    if membership_count > 1:
        email_sent = await send_password_recovery_email(user_doc=target, request=request)
        return {"ok": True, "temporary_password": None, "email_sent": email_sent}
    temp = generate_temp_password(12)
    await db.users.update_one({"user_id": uid}, {"$set": {
        "password_hash": hash_password(temp),
        "updated_at": now_iso(),
        "password_reset_by": admin.user_id,
        "password_reset_at": now_iso(),
    }})
    logger.info("admin reset password user=%s by=%s", uid, admin.user_id)
    email_sent = await send_password_recovery_email(user_doc=target, request=request)
    # The generated password is only available in this response, so the admin
    # can hand it to the user even when the recovery email was delivered.
    return {"ok": True, "temporary_password": temp, "email_sent": email_sent}


@api_router.delete("/admin/users/{uid}")
async def admin_delete_user(uid: str, admin: User = Depends(require_perm("users_admin"))):
    target = await db.users.find_one({"user_id": uid}, {"_id": 0})
    membership = await _raw_collection("memberships").find_one({
        "organization_id": admin.organization_id, "user_id": uid,
    }, {"_id": 0})
    if not target or not membership:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if uid == admin.user_id:
        raise HTTPException(status_code=400, detail="No podés eliminar tu propia cuenta")
    if membership.get("role") == "admin":
        if await _count_active_admins() <= 1:
            raise HTTPException(status_code=400, detail="No se puede eliminar al último administrador activo")
    await _raw_collection("memberships").update_one(
        {"organization_id": admin.organization_id, "user_id": uid},
        {"$set": {"status": "removed", "removed_at": now_iso(),
                  "removed_by": admin.user_id}},
    )
    await db.user_sessions.delete_many({"user_id": uid, "organization_id": admin.organization_id})
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
    organization_id = get_organization_id()
    suffix = f"?organization_id={organization_id}" if organization_id else ""
    if explicit:
        return f"{explicit}/api/webhooks/whatsapp{suffix}", ""

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
            return f"https://{host}/api/webhooks/whatsapp{suffix}", ""
    return "", (
        "El backend no puede determinar la URL pública. "
        "Configurá PUBLIC_BASE_URL en backend/.env"
    )


@api_router.get("/admin/whatsapp/config")
async def admin_wa_config_get(request: Request, admin: User = Depends(require_perm("whatsapp_view"))):
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
async def admin_wa_config_put(payload: WhatsAppConfigUpdate, admin: User = Depends(require_perm("whatsapp_admin"))):
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
        if "phone_number_id" in updates:
            await _raw_collection("whatsapp_routes").delete_many({
                "organization_id": admin.organization_id,
            })
            phone_number_id = str(updates.get("phone_number_id") or "").strip()
            if phone_number_id:
                await _raw_collection("whatsapp_routes").update_one(
                    {"phone_number_id": phone_number_id},
                    {"$set": {"phone_number_id": phone_number_id,
                              "organization_id": admin.organization_id,
                              "updated_at": now_iso()}},
                    upsert=True,
                )
    except Exception as e:
        logger.exception("admin_wa_config_put failed: %s", e)
        raise HTTPException(status_code=500, detail="No se pudo guardar la configuración")
    # Return the new state (no plain values)
    env = _wa_env_values()
    fields = await per_field_sources(db, env)
    cfg = await wa_config_effective(db)
    return {"configured": cfg.is_configured, "fields": fields, "api_version": cfg.api_version}


@api_router.post("/admin/whatsapp/test-connection")
async def admin_wa_test_connection(admin: User = Depends(require_perm("whatsapp_use"))):
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
async def admin_wa_rotate_verify_token(admin: User = Depends(require_perm("whatsapp_admin"))):
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
async def admin_wa_test_webhook_verify(request: Request, admin: User = Depends(require_perm("whatsapp_use"))):
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
    docs = await _team_user_docs(user.organization_id)
    return [User(**{
        **doc,
        "role": doc.get("membership_role", "agent"),
        "work_areas": doc.get("membership_work_areas") or [],
        "organization_id": user.organization_id,
        "organization_name": user.organization_name,
    }) for doc in reversed(docs)]


@api_router.patch("/users/{user_id}", response_model=User)
async def update_user(user_id: str, payload: RoleUpdate, admin: User = Depends(require_perm("users_admin"))):
    valid_roles = await get_all_roles()
    if payload.role not in valid_roles:
        raise HTTPException(status_code=400, detail="Invalid role")
    update = {"role": payload.role, "updated_at": now_iso()}
    if payload.active is not None:
        update["status"] = "active" if payload.active else "inactive"
    result = await _raw_collection("memberships").update_one(
        {"organization_id": admin.organization_id, "user_id": user_id}, {"$set": update}
    )
    doc = await _raw_collection("users").find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")
    if getattr(result, "matched_count", 1) == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return User(**{**doc, "role": payload.role, "organization_id": admin.organization_id,
                   "organization_name": admin.organization_name})

# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

@api_router.get("/contacts", response_model=List[Contact])
async def list_contacts(user: User = Depends(require_perm("crm_view")), search: Optional[str] = None):
    q = {}
    if search:
        q = {"$or": [
            {"name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"company": {"$regex": search, "$options": "i"}},
        ]}
    docs = await db.contacts.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)
    # Repara contactos históricos que quedaron sin su lead asociado.
    contact_ids = [doc.get("id") for doc in docs if doc.get("id")]
    linked_leads = await db.leads.find(
        {"contact_id": {"$in": contact_ids}}, {"_id": 0, "contact_id": 1}
    ).to_list(1000)
    linked_ids = {lead.get("contact_id") for lead in linked_leads}
    for contact in docs:
        if contact.get("id") in linked_ids:
            continue
        lead = Lead(
            contact_id=contact["id"],
            title=f"Lead de {contact.get('name') or 'Contacto'}",
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
async def create_contact(payload: ContactCreate, user: User = Depends(require_perm("crm_use"))):
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
async def get_contact(contact_id: str, user: User = Depends(require_perm("crm_view"))):
    doc = await db.contacts.find_one({"id": contact_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Contact not found")
    return Contact(**doc)


@api_router.patch("/contacts/{contact_id}", response_model=Contact)
async def update_contact(contact_id: str, payload: ContactUpdate, user: User = Depends(require_perm("crm_use"))):
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
    user: User = Depends(require_perm("crm_view")),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigned_to: Optional[str] = None,
):
    q = {}
    if status:
        q["status"] = status
    if priority:
        q["priority"] = priority
    permissions = await get_role_permissions(user.role)
    can_manage_all = permission_granted(permissions, "crm_admin")
    if not can_manage_all:
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
async def create_lead(payload: LeadCreate, user: User = Depends(require_perm("crm_use"))):
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
async def get_lead(lead_id: str, user: User = Depends(require_perm("crm_view"))):
    doc = await db.leads.find_one({"id": lead_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    permissions = await get_role_permissions(user.role)
    if not permission_granted(permissions, "crm_admin") and doc.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="No podés ver un lead asignado a otra persona")
    doc["contact"] = await db.contacts.find_one({"id": doc["contact_id"]}, {"_id": 0})
    doc["notes"] = await db.notes.find({"lead_id": lead_id}, {"_id": 0}).sort("created_at", -1).to_list(200)
    doc["tasks"] = await db.tasks.find({"lead_id": lead_id}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return doc


@api_router.patch("/leads/{lead_id}", response_model=Lead)
async def update_lead(lead_id: str, payload: LeadUpdate, user: User = Depends(require_perm("crm_use"))):
    current = await db.leads.find_one({"id": lead_id}, {"_id": 0})
    if not current:
        raise HTTPException(status_code=404, detail="Lead not found")
    permissions = await get_role_permissions(user.role)
    if not permission_granted(permissions, "crm_admin") and current.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="No podés modificar un lead asignado a otra persona")
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
async def delete_lead(lead_id: str, user: User = Depends(require_perm("crm_admin"))):
    await db.leads.delete_one({"id": lead_id})
    return {"ok": True}

# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

@api_router.post("/notes", response_model=Note)
async def create_note(payload: NoteCreate, user: User = Depends(require_perm("crm_use"))):
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
        memberships = await _raw_collection("memberships").find({
            "organization_id": get_organization_id(),
            "role": {"$in": ["admin", "supervisor"]},
            "status": "active",
        }, {"_id": 0, "user_id": 1}).to_list(100)
        if not memberships and not isinstance(db, _DBProxy):
            memberships = await db.users.find(
                {"role": {"$in": ["admin", "supervisor"]}},
                {"_id": 0, "user_id": 1},
            ).to_list(100)
        for membership in memberships:
            await _make_notification(ntype, title, body, entity_type, entity_id,
                                     membership["user_id"], priority)


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
async def read_admin_settings(admin: User = Depends(require_perm("settings_view"))):
    return _admin_settings_view(await get_app_settings())


@api_router.patch("/admin/settings")
async def update_settings(payload: SettingsUpdate, admin: User = Depends(require_perm("settings_admin"))):
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
async def send_test_email(payload: SendTestEmailBody, admin: User = Depends(require_perm("settings_use"))):
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
    organization_id: Optional[str] = Query(default=None),
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    if not organization_id:
        organizations = await _raw_collection("organizations").find(
            {"status": "active"}, {"organization_id": 1, "_id": 0}
        ).to_list(2)
        if len(organizations) == 1:
            organization_id = organizations[0]["organization_id"]
        elif not organizations and not isinstance(db, _DBProxy):
            organization_id = "legacy-test"
        else:
            raise HTTPException(status_code=400, detail="organization_id required")
    if isinstance(db, _DBProxy):
        organization = await _raw_collection("organizations").find_one(
            {"organization_id": organization_id, "status": "active"}, {"_id": 0}
        )
        if not organization:
            raise HTTPException(status_code=404, detail="organization not found")
    set_organization_id(organization_id)
    cfg = await wa_config_effective(db)
    if hub_mode == "subscribe" and hub_verify_token and cfg.verify_token and hub_verify_token == cfg.verify_token:
        return Response(content=hub_challenge or "", media_type="text/plain", status_code=200)
    raise HTTPException(status_code=403, detail="verify_token mismatch")


# ---- Inbound events (POST) ------------------------------------------------

@api_router.post("/webhooks/whatsapp")
async def whatsapp_webhook_event(request: Request):
    raw = await request.body()
    # Route the event from Meta's destination phone to exactly one company.
    try:
        payload = await request.json()
    except Exception as e:
        logger.warning("WhatsApp webhook invalid JSON: %s", e)
        return {"ok": True}
    phone_number_ids = {
        str(((change.get("value") or {}).get("metadata") or {}).get("phone_number_id") or "").strip()
        for entry in (payload.get("entry") or [])
        for change in (entry.get("changes") or [])
    } - {""}
    route = None
    for phone_number_id in phone_number_ids:
        route = await _raw_collection("whatsapp_routes").find_one(
            {"phone_number_id": phone_number_id}, {"_id": 0}
        )
        if route:
            break
    if not route:
        requested_org = request.query_params.get("organization_id")
        if requested_org:
            organization = await _raw_collection("organizations").find_one(
                {"organization_id": requested_org, "status": "active"}, {"_id": 0}
            )
            if organization:
                route = {"organization_id": requested_org}
    if not route:
        organizations = await _raw_collection("organizations").find(
            {"status": "active"}, {"organization_id": 1, "_id": 0}
        ).to_list(2)
        if len(organizations) == 1:
            route = organizations[0]
        elif not organizations and not isinstance(db, _DBProxy):
            route = {"organization_id": "legacy-test"}
    if not route or not route.get("organization_id"):
        logger.error("WhatsApp webhook without a configured organization route phones=%s", phone_number_ids)
        return {"ok": True}
    set_organization_id(route["organization_id"])
    cfg = await wa_config_effective(db)
    # Signature check (only enforced when APP_SECRET is configured)
    sig_header = request.headers.get("X-Hub-Signature-256") or request.headers.get("x-hub-signature-256")
    if cfg.app_secret:
        if not verify_signature(cfg.app_secret, raw, sig_header):
            logger.warning("WhatsApp webhook signature mismatch")
            raise HTTPException(status_code=403, detail="invalid signature")
    else:
        logger.warning("WhatsApp APP_SECRET not configured - signature verification skipped (dev mode)")

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
    user: User = Depends(require_perm("inbox_use")),
):
    from whatsapp.templates import build_template_context, render_template_preview
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    perms = await get_role_permissions(user.role)
    if not permission_granted(perms, "inbox_admin") and conv.get("assigned_to") != user.user_id:
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
    user: User = Depends(require_perm("inbox_use")),
):
    from whatsapp.templates import find_template
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    perms = await get_role_permissions(user.role)
    if not permission_granted(perms, "inbox_admin") and conv.get("assigned_to") != user.user_id:
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
async def send_whatsapp(conv_id: str, payload: WhatsAppSend, user: User = Depends(require_perm("inbox_use"))):
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
    if not permission_granted(perms, "inbox_admin") and conv.get("assigned_to") != user.user_id:
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
async def admin_whatsapp_status(user: User = Depends(require_perm("whatsapp_view"))):
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
    user: User = Depends(require_perm("inbox_view")),
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
    perms = await get_role_permissions(user.role)
    can_manage_all = permission_granted(perms, "inbox_admin")
    if not can_manage_all:
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
async def get_conversation(conv_id: str, user: User = Depends(require_perm("inbox_view"))):
    doc = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    perms = await get_role_permissions(user.role)
    if not permission_granted(perms, "inbox_admin") and doc.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="No podés ver una conversación asignada a otra persona")
    doc["contact"] = await db.contacts.find_one({"id": doc["contact_id"]}, {"_id": 0})
    if doc.get("lead_id"):
        doc["lead"] = await db.leads.find_one({"id": doc["lead_id"]}, {"_id": 0})
    doc["messages"] = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    if doc.get("channel") == "whatsapp":
        doc.update(_whatsapp_window_state_from_messages(doc["messages"]))
    await db.conversations.update_one({"id": conv_id}, {"$set": {"unread": 0}})
    return doc


@api_router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: str, payload: MessageCreate, user: User = Depends(require_perm("inbox_use"))):
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    perms = await get_role_permissions(user.role)
    if not permission_granted(perms, "inbox_admin") and conv.get("assigned_to") != user.user_id:
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
    tone_dialect: Optional[str] = None
    response_length_limit: Optional[str] = None
    writing_rules: Optional[dict] = None
    company_workflow_steps: Optional[List[str]] = None
    custom_client_fields: Optional[List[dict]] = None
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
    webchat_enabled: Optional[bool] = None
    webchat_auto_invite_whatsapp: Optional[bool] = None
    webchat_title: Optional[str] = None
    webchat_welcome_message: Optional[str] = None
    webchat_primary_color: Optional[str] = None
    webchat_avatar_url: Optional[str] = None
    webchat_bg_color: Optional[str] = None
    webchat_user_bubble_color: Optional[str] = None
    webchat_position: Optional[str] = None


async def _ensure_webchat_public_key(organization_id: str) -> str:
    organizations = _raw_collection("organizations")
    organization = await organizations.find_one(
        {"organization_id": organization_id}, {"_id": 0, "webchat_public_key": 1},
    ) or {}
    existing = str(organization.get("webchat_public_key") or "").strip()
    if existing:
        return existing
    public_key = f"wpk_{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"
    await organizations.update_one(
        {"organization_id": organization_id},
        {"$set": {"webchat_public_key": public_key, "webchat_public_key_created_at": now_iso()}},
    )
    return public_key


async def _log_system_message(db, conv_id: str, text: str, *, channel: str = "whatsapp"):
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
        "channel": channel,
    }
    await db.messages.insert_one(msg_doc)


@api_router.get("/admin/bot-settings")
async def admin_get_bot_settings(admin: User = Depends(require_any_perm("ai_view", "calendar_view"))):
    from ai.pipeline import DEFAULT_BOT_SETTINGS
    from ai import providers as ai_providers
    doc = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
    platform_ai = await ai_providers.load_settings(db)
    webchat_public_key = await _ensure_webchat_public_key(admin.organization_id)
    return {
        **DEFAULT_BOT_SETTINGS,
        **doc,
        "provider": platform_ai.get("provider", "built_in"),
        "model": platform_ai.get("model", "gpt-4o-mini"),
        "provider_managed_by_platform": True,
        "webchat_public_key": webchat_public_key,
    }


@api_router.patch("/admin/bot-settings")
async def admin_patch_bot_settings(payload: BotSettingsUpdate,
                                   admin: User = Depends(get_current_user)):
    platform_only_fields = {"provider", "model", "api_keys"}
    requested_platform_fields = platform_only_fields.intersection(payload.model_fields_set)
    if requested_platform_fields:
        raise HTTPException(
            status_code=403,
            detail=(
                "Proveedor, modelo y credenciales de IA son administrados "
                "exclusivamente desde Plataforma"
            ),
        )
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    appointment_fields = {
        "appointment_scheduling_enabled", "appointment_available_days",
        "appointment_business_hours", "appointment_duration_minutes",
        "appointment_mode", "appointment_timezone", "appointment_services",
        "whatsapp_recontact_templates", "appointment_reminders_enabled",
        "appointment_reminder_minutes_before", "appointment_reminder_templates",
        "appointment_reminder_template_id", "appointment_rescheduling_enabled",
    }
    perms = await get_role_permissions(admin.role)
    if appointment_fields.intersection(update) and not permission_granted(perms, "calendar_admin"):
        raise HTTPException(status_code=403, detail="Se requiere administrar Agenda para cambiar esta configuración")
    if (set(update) - appointment_fields) and not permission_granted(perms, "ai_admin"):
        raise HTTPException(status_code=403, detail="Se requiere administrar IA para cambiar esta configuración")
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
            membership = await _raw_collection("memberships").find_one({
                "organization_id": admin.organization_id,
                "user_id": uid,
                "status": "active",
            })
            if not membership:
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
    for color_field in ("webchat_primary_color", "webchat_bg_color", "webchat_user_bubble_color"):
        if color_field in update:
            color = str(update[color_field] or "").strip()
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                raise HTTPException(400, f"{color_field} debe ser un color hexadecimal válido")
            update[color_field] = color.upper()
    for text_field, max_length in (
        ("webchat_title", 80), ("webchat_welcome_message", 500),
        ("webchat_avatar_url", 1000),
    ):
        if text_field in update:
            value = str(update[text_field] or "").strip()
            if len(value) > max_length:
                raise HTTPException(400, f"{text_field} supera el máximo de {max_length} caracteres")
            update[text_field] = value
    if update.get("webchat_avatar_url"):
        parsed_avatar = urlparse(update["webchat_avatar_url"])
        if parsed_avatar.scheme != "https" or not parsed_avatar.netloc:
            raise HTTPException(400, "La imagen del bot debe usar una URL HTTPS válida")
    if "webchat_position" in update:
        position = str(update["webchat_position"] or "").strip().lower()
        if position not in {"left", "right"}:
            raise HTTPException(400, "La posición del chat debe ser izquierda o derecha")
        update["webchat_position"] = position
    if "webchat_enabled" in update:
        update["webchat_enabled"] = bool(update["webchat_enabled"])
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
    update["updated_at"] = now_iso()
    update["updated_by"] = admin.user_id
    await db.bot_settings.update_one({"_id": "default"},
                                     {"$set": {"_id": "default", **update}}, upsert=True)
    return await admin_get_bot_settings(admin)


# ---------------------------------------------------------------------------
# Public Web Chat Endpoints (Unauthenticated / Token Driven)
# ---------------------------------------------------------------------------

class PublicWebChatSessionRequest(BaseModel):
    session_token: Optional[str] = Field(default=None, max_length=160)
    organization_key: Optional[str] = Field(default=None, max_length=100)
    name: Optional[str] = Field(default=None, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=30)


class PublicWebChatSendRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    sender_name: Optional[str] = Field(default=None, max_length=80)
    client_message_id: Optional[str] = Field(default=None, max_length=100)


_WEBCHAT_RATE_BUCKETS: dict[str, list[float]] = {}


def _enforce_webchat_rate_limit(request: Request, scope: str, *, limit: int, window_seconds: int) -> None:
    now = datetime.now(timezone.utc).timestamp()
    forwarded_for = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    client_host = forwarded_for or (request.client.host if request.client else "unknown")
    bucket_key = hashlib.sha256(f"{scope}:{client_host}".encode()).hexdigest()
    recent = [ts for ts in _WEBCHAT_RATE_BUCKETS.get(bucket_key, []) if now - ts < window_seconds]
    if len(recent) >= limit:
        raise HTTPException(429, "Demasiadas solicitudes. Esperá un momento y volvé a intentar")
    recent.append(now)
    _WEBCHAT_RATE_BUCKETS[bucket_key] = recent
    if len(_WEBCHAT_RATE_BUCKETS) > 10_000:
        stale_before = now - max(window_seconds, 600)
        for key, values in list(_WEBCHAT_RATE_BUCKETS.items())[:2_000]:
            if not values or values[-1] < stale_before:
                _WEBCHAT_RATE_BUCKETS.pop(key, None)


async def _activate_webchat_tenant(*, session_token: Optional[str] = None,
                                   organization_key: Optional[str] = None) -> str:
    current = get_organization_id()
    if current:
        if session_token:
            existing = await _raw_collection("conversations").find_one(
                {"webchat_session_token": session_token}, {"_id": 0, "organization_id": 1},
            )
            if existing and existing.get("organization_id") != current:
                raise HTTPException(404, "Enlace de chat inválido o vencido")
        return current
    organization_id = None
    if session_token:
        conversation = await _raw_collection("conversations").find_one(
            {"webchat_session_token": session_token},
            {"_id": 0, "organization_id": 1},
        )
        organization_id = (conversation or {}).get("organization_id")
    if not organization_id and organization_key:
        organization = await _raw_collection("organizations").find_one(
            {"webchat_public_key": organization_key}, {"_id": 0},
        )
        if (organization and organization.get("status") != "deleted"
                and subscription_access_state(organization).get("allowed")):
            organization_id = organization.get("organization_id")
    if not organization_id:
        raise HTTPException(404, "Enlace de chat inválido o vencido")
    set_organization_id(organization_id)
    return organization_id


async def _load_enabled_webchat_settings() -> dict:
    from ai.pipeline import _load_bot_settings
    settings = await _load_bot_settings(db)
    if not settings.get("webchat_enabled", True):
        raise HTTPException(404, "El chat web no está disponible")
    return settings


@api_router.post("/public/webchat/session")
async def public_webchat_session(payload: PublicWebChatSessionRequest, request: Request):
    """Initialize or resume a tenant-bound web chat session."""
    session_token = (payload.session_token or "").strip()
    organization_key = (payload.organization_key or "").strip()
    await _activate_webchat_tenant(
        session_token=session_token or None, organization_key=organization_key or None,
    )
    _enforce_webchat_rate_limit(
        request, f"session:{session_token or organization_key}", limit=12, window_seconds=600,
    )
    bot_settings = await _load_enabled_webchat_settings()
    conv_doc = None

    if session_token:
        conv_doc = await db.conversations.find_one({
            "webchat_session_token": session_token,
        })

    linked_source = None
    if conv_doc and conv_doc.get("channel") != "webchat":
        linked_source = conv_doc
        await db.conversations.update_one(
            {"id": linked_source["id"]},
            {"$unset": {"webchat_session_token": ""}, "$set": {"webchat_linked_at": now_iso()}},
        )
        conv_doc = None

    if not conv_doc:
        # Only an authenticated tenant administrator or a valid public
        # installation key may create a session. Unknown shared tokens never
        # create data implicitly.
        if session_token and not linked_source and not organization_key and not _request_session_token(request):
            raise HTTPException(404, "Enlace de chat inválido o vencido")
        now = datetime.now(timezone.utc).isoformat()
        if linked_source:
            contact_id = linked_source.get("contact_id")
        else:
            clean_name = (payload.name or "Visitante Web").strip() or "Visitante Web"
            clean_phone = (payload.phone or "").strip()
            contact_id = f"cnt_{uuid.uuid4().hex[:12]}"
            contact_doc = {
                "id": contact_id,
                "organization_id": get_organization_id(),
                "name": clean_name,
                "phone": clean_phone or None,
                "company": "Web Chat",
                "lead_source": "Web Chat",
                "custom_fields": {},
                "created_at": now,
            }
            await db.contacts.insert_one(contact_doc)

        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        session_token = (
            session_token if session_token.startswith("cw_") and len(session_token) >= 20
            else f"cw_{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"
        )
        conv_doc = {
            "id": conv_id,
            "organization_id": get_organization_id(),
            "contact_id": contact_id,
            "lead_id": linked_source.get("lead_id") if linked_source else None,
            "channel": "webchat",
            "status": "abierta",
            "priority": "media",
            "bot_enabled": bot_settings.get("bot_enabled_default", True),
            "bot_status": "bot_activo" if bot_settings.get("bot_enabled_default", True) else "en_atencion_humana",
            "last_message": "Sesión de Chat Web iniciada",
            "last_message_at": now,
            "unread": 0,
            "webchat_session_token": session_token,
            "source_conversation_id": linked_source.get("id") if linked_source else None,
            "created_at": now,
        }
        await db.conversations.insert_one(conv_doc)
    else:
        session_token = conv_doc["webchat_session_token"]

    msgs = await db.messages.find({"conversation_id": conv_doc["id"]}, {"_id": 0}).sort("created_at", 1).to_list(200)
    contact = await db.contacts.find_one({"id": conv_doc["contact_id"]}, {"_id": 0}) or {}
    
    return {
        "session_token": session_token,
        "conversation_id": conv_doc["id"],
        "bot_enabled": bool(conv_doc.get("bot_enabled", True)),
        "bot_status": conv_doc.get("bot_status", "bot_activo"),
        "contact_name": contact.get("name", "Cliente"),
        "bot_name": bot_settings.get("bot_name", "Bot"),
        "webchat_title": bot_settings.get("webchat_title", "Asistente Latus"),
        "webchat_welcome_message": bot_settings.get("webchat_welcome_message", "¡Hola! ¿En qué puedo ayudarte hoy?"),
        "webchat_primary_color": bot_settings.get("webchat_primary_color", "#0E8DDB"),
        "webchat_avatar_url": bot_settings.get("webchat_avatar_url") or None,
        "webchat_bg_color": bot_settings.get("webchat_bg_color") or "#F0F2F5",
        "webchat_user_bubble_color": bot_settings.get("webchat_user_bubble_color") or None,
        "finished": conv_doc.get("status") == "cerrada" or conv_doc.get("bot_status") == "cerrada",
        "messages": msgs,
    }


@api_router.get("/public/webchat/{session_token}/messages")
async def public_webchat_get_messages(session_token: str, request: Request):
    """Retrieves message history for a web chat session."""
    await _activate_webchat_tenant(session_token=session_token)
    _enforce_webchat_rate_limit(request, f"poll:{session_token}", limit=90, window_seconds=60)
    await _load_enabled_webchat_settings()
    conv = await db.conversations.find_one({
        "webchat_session_token": session_token,
        "channel": "webchat",
    }, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada")
    
    msgs = await db.messages.find({"conversation_id": conv["id"]}, {"_id": 0}).sort("created_at", 1).to_list(200)
    return {
        "conversation_id": conv["id"],
        "bot_enabled": bool(conv.get("bot_enabled", True)),
        "bot_status": conv.get("bot_status", "bot_activo"),
        "finished": conv.get("status") == "cerrada" or conv.get("bot_status") == "cerrada",
        "messages": msgs
    }


@api_router.post("/public/webchat/{session_token}/messages")
async def public_webchat_send_message(session_token: str, payload: PublicWebChatSendRequest,
                                      request: Request):
    """Receives an inbound message from the web chat visitor and triggers the AI bot pipeline."""
    from ai import pipeline as bot_pipeline
    await _activate_webchat_tenant(session_token=session_token)
    _enforce_webchat_rate_limit(request, f"send:{session_token}", limit=20, window_seconds=60)
    await _load_enabled_webchat_settings()
    conv = await db.conversations.find_one({
        "webchat_session_token": session_token,
        "channel": "webchat",
    }, {"_id": 0})
    if not conv:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada")
    
    now = datetime.now(timezone.utc).isoformat()
    client_message_id = (payload.client_message_id or uuid.uuid4().hex).strip()
    msg_id = "msg_web_" + hashlib.sha256(
        f"{conv['id']}:{client_message_id}".encode(),
    ).hexdigest()[:24]
    clean_body = payload.body.strip()
    if not clean_body:
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío")
    
    existing_message = await db.messages.find_one({"id": msg_id, "conversation_id": conv["id"]}, {"_id": 0})
    if existing_message:
        event_res = await bot_pipeline.process_inbound(db, conv["id"], msg_id, wa_send=None)
        updated_msgs = await db.messages.find(
            {"conversation_id": conv["id"]}, {"_id": 0},
        ).sort("created_at", 1).to_list(200)
        updated_conv = await db.conversations.find_one({"id": conv["id"]}, {"_id": 0}) or {}
        return {
            "status": "ok", "deduplicated": True, "event": event_res,
            "bot_enabled": bool(updated_conv.get("bot_enabled", True)),
            "bot_status": updated_conv.get("bot_status", "bot_activo"),
            "messages": updated_msgs,
        }

    msg_doc = {
        "id": msg_id,
        "organization_id": get_organization_id(),
        "conversation_id": conv["id"],
        "sender_type": "contact",
        "sender_name": payload.sender_name or "Visitante Web",
        "body": clean_body,
        "created_at": now,
        "direction": "inbound",
        "delivery_status": "delivered",
        "message_type": "text",
        "channel": "webchat",
        "client_message_id": client_message_id,
    }
    await db.messages.insert_one(msg_doc)
    
    update_fields = {"last_message": clean_body, "last_message_at": now}
    if conv.get("status") == "cerrada":
        bot_settings = await _load_enabled_webchat_settings()
        bot_default = bot_settings.get("bot_enabled_default", True)
        update_fields["status"] = "abierta"
        update_fields["bot_enabled"] = bot_default
        update_fields["bot_status"] = "bot_activo" if bot_default else "en_atencion_humana"
        update_fields["human_required_reason"] = None
        await _log_system_message(db, conv["id"], "Conversación de Chat Web reabierta por nuevo mensaje del cliente")

    await db.conversations.update_one(
        {"id": conv["id"]}, {"$set": update_fields, "$inc": {"unread": 1}},
    )
    
    event_res = await bot_pipeline.process_inbound(db, conv["id"], msg_id, wa_send=None)
    
    updated_msgs = await db.messages.find({"conversation_id": conv["id"]}, {"_id": 0}).sort("created_at", 1).to_list(200)
    updated_conv = await db.conversations.find_one({"id": conv["id"]}, {"_id": 0}) or {}
    
    return {
        "status": "ok",
        "event": event_res,
        "bot_enabled": bool(updated_conv.get("bot_enabled", True)),
        "bot_status": updated_conv.get("bot_status", "bot_activo"),
        "messages": updated_msgs
    }


async def send_webchat_whatsapp_summary_internal(db, conv_id: str) -> dict:
    conv = await db.conversations.find_one({"id": conv_id, "channel": "webchat"}, {"_id": 0})
    if not conv:
        return {"sent": False, "reason": "conversation_not_found"}
    contact = await db.contacts.find_one({"id": conv.get("contact_id")}, {"_id": 0}) or {}
    wa_id = "".join(ch for ch in str(contact.get("whatsapp_id") or "") if ch.isdigit())
    if len(wa_id) < 8:
        return {"sent": False, "reason": "whatsapp_not_verified"}
    cfg = _wa_config_for_conversation(await wa_config_effective(db), conv)
    if not cfg.is_configured:
        return {"sent": False, "reason": "whatsapp_not_configured"}
    summary = str(conv.get("summary") or "").strip()
    if not summary:
        recent = await db.messages.find(
            {"conversation_id": conv_id}, {"_id": 0, "sender_type": 1, "body": 1},
        ).sort("created_at", -1).to_list(6)
        recent.reverse()
        summary = " · ".join(
            str(item.get("body") or "").strip()[:180] for item in recent if item.get("body")
        )[:700]
    text = f"Resumen de tu consulta en {contact.get('name') or 'nuestro chat'}: {summary or 'Consulta finalizada.'}"
    try:
        result = await send_text_message(cfg, wa_id, text)
    except Exception as exc:
        logger.warning("webchat summary could not be sent conv=%s: %s", conv_id, exc)
        return {"sent": False, "reason": "send_failed"}
    return {"sent": True, "provider_result": result}


@api_router.post("/public/webchat/{session_token}/finish")
async def public_webchat_finish(session_token: str, request: Request):
    await _activate_webchat_tenant(session_token=session_token)
    _enforce_webchat_rate_limit(request, f"finish:{session_token}", limit=6, window_seconds=60)
    conv = await db.conversations.find_one(
        {"webchat_session_token": session_token, "channel": "webchat"}, {"_id": 0},
    )
    if not conv:
        raise HTTPException(404, "Sesión de chat no encontrada")
    if conv.get("status") != "cerrada":
        closed_at = now_iso()
        await db.conversations.update_one(
            {"id": conv["id"]},
            {"$set": {
                "status": "cerrada", "bot_enabled": False, "bot_status": "cerrada",
                "closed_at": closed_at, "updated_at": closed_at,
            }},
        )
        await _log_system_message(db, conv["id"], "Consulta de Chat Web finalizada", channel="webchat")
    summary_result = await send_webchat_whatsapp_summary_internal(db, conv["id"])
    return {"ok": True, "finished": True, "summary_sent": bool(summary_result.get("sent"))}


# ---------------------------------------------------------------------------
# AI provider settings (multi-provider configuration)
# ---------------------------------------------------------------------------


async def _ai_provider_payload(*, include_secret_status: bool) -> dict:
    from ai import providers as ai_providers
    s = await ai_providers.load_settings(db)
    keys_status = {}
    for prov in ai_providers.KEY_REQUIRED_PROVIDERS:
        raw = await ai_providers._resolve_api_key(db, prov)
        keys_status[prov] = {
            "configured": bool(raw) if include_secret_status else False,
            "masked": ai_providers.mask_key(raw) if include_secret_status else "",
        }
    provider = s.get("provider", "built_in")
    masked = keys_status.get(provider, {}).get("masked", "")
    return {
        **{k: s[k] for k in ai_providers.DEFAULTS.keys()},
        "api_key_configured": (
            s.get("api_key_configured", False) if include_secret_status else False
        ),
        "api_key_masked": masked,
        "keys_status": keys_status,
        "model_suggestions": ai_providers.MODEL_SUGGESTIONS,
        "supported_providers": list(ai_providers.SUPPORTED_PROVIDERS),
        "updated_at": s.get("updated_at"),
        "updated_by": s.get("updated_by"),
        "managed_by_platform": True,
        "can_manage": include_secret_status,
        "encryption_available": crypto_available(),
    }


@api_router.get("/admin/ai-provider")
async def admin_get_ai_provider(admin: User = Depends(require_perm("ai_view"))):
    return await _ai_provider_payload(include_secret_status=admin.is_platform_admin)


@api_router.get("/platform/ai-settings")
async def platform_get_ai_settings(platform_admin: User = Depends(require_platform_admin)):
    return await _ai_provider_payload(include_secret_status=True)


@api_router.put("/admin/ai-provider")
async def admin_put_ai_provider(payload: dict = Body(...),
                                admin: User = Depends(require_platform_admin)):
    from ai import providers as ai_providers
    from ai import usage as ai_usage
    current = await ai_providers.load_settings(db)
    try:
        clean = ai_providers.validate_patch(payload, current)
    except ValueError as e:
        raise HTTPException(400, str(e))
    api_keys = clean.get("api_keys") or {}
    single_key_present = "api_key" in clean
    single_key = clean.get("api_key")
    has_any_new_key = (
        isinstance(single_key, str) and bool(single_key.strip())
    ) or any(isinstance(value, str) and value.strip() for value in api_keys.values())
    if has_any_new_key:
        if not crypto_available():
            raise HTTPException(
                status_code=503,
                detail="APP_ENCRYPTION_KEY no configurado; no se pueden guardar credenciales de IA",
            )
    next_provider = clean.get("provider", current.get("provider", "built_in"))
    next_model = clean.get("model", current.get("model", ""))
    next_ai_enabled = clean.get("ai_enabled", current.get("ai_enabled", True))
    if next_ai_enabled and not await ai_usage.pricing_is_configured(db, next_model):
        raise HTTPException(
            400,
            "Configurá el precio de entrada y salida del modelo antes de activarlo. "
            "Así evitás registrar consumo con costo cero.",
        )
    if next_provider in ai_providers.KEY_REQUIRED_PROVIDERS:
        pending_key = api_keys.get(next_provider) if next_provider in api_keys else None
        explicitly_cleared = (
            (single_key_present and single_key is None)
            or (next_provider in api_keys and pending_key is None)
        )
        has_new_key = (
            isinstance(single_key, str) and bool(single_key.strip())
        ) or (isinstance(pending_key, str) and bool(pending_key.strip()))
        has_existing_key = bool(await ai_providers._resolve_api_key(db, next_provider))
        if explicitly_cleared or (not has_new_key and not has_existing_key):
            raise HTTPException(400, "API Key requerida para el proveedor activo")
    await ai_providers.save_settings(db, clean, user_id=admin.user_id)
    return await _ai_provider_payload(include_secret_status=True)


@api_router.put("/platform/ai-settings")
async def platform_put_ai_settings(payload: dict = Body(...),
                                   platform_admin: User = Depends(require_platform_admin)):
    return await admin_put_ai_provider(payload, platform_admin)


@api_router.post("/admin/ai-provider/test")
async def admin_test_ai_provider(admin: User = Depends(require_platform_admin)):
    from ai import providers as ai_providers
    return await ai_providers.test_provider_connectivity(db)


@api_router.post("/platform/ai-settings/test")
async def platform_test_ai_settings(platform_admin: User = Depends(require_platform_admin)):
    return await admin_test_ai_provider(platform_admin)


@api_router.get("/platform/ai-models/{provider}")
async def platform_get_ai_models(
    provider: str, platform_admin: User = Depends(require_platform_admin),
):
    from ai import model_catalog, providers as ai_providers
    if provider not in ai_providers.SUPPORTED_PROVIDERS:
        raise HTTPException(400, "Proveedor no soportado")
    return await model_catalog.catalog_with_pricing(db, provider)


@api_router.post("/platform/ai-models/{provider}/sync")
async def platform_sync_ai_models(
    provider: str, payload: dict = Body(default={}),
    platform_admin: User = Depends(require_platform_admin),
):
    from ai import model_catalog, providers as ai_providers
    if provider not in ai_providers.SUPPORTED_PROVIDERS:
        raise HTTPException(400, "Proveedor no soportado")
    settings = await ai_providers.load_settings(db)
    base_url = str(payload.get("base_url") or settings.get("base_url") or "").strip()
    try:
        await model_catalog.sync_catalog(
            db, provider, base_url=base_url, user_id=platform_admin.user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"No se pudo consultar el catálogo del proveedor: {exc}") from exc
    return await model_catalog.catalog_with_pricing(db, provider)


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
                       provider: str | None = None, organization_id: str | None = None) -> dict:
    q: dict = {"created_at": {"$gte": from_iso, "$lte": to_iso}}
    if model:           q["model"] = model
    if status:          q["status"] = status
    if conversation_id: q["conversation_id"] = conversation_id
    if provider:        q["provider"] = provider
    if organization_id and organization_id != "__all__":
        q["organization_id"] = organization_id
    return q


@api_router.get("/admin/ai-usage/summary")
async def admin_ai_usage_summary(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    model: str | None = None,
    status: str | None = None,
    provider: str | None = None,
    organization_id: str | None = None,
    admin: User = Depends(require_perm("ai_view")),
):
    from ai import usage as ai_usage
    f, t = _date_bounds(from_, to)
    target_org = organization_id if admin.is_platform_admin else admin.organization_id
    q = _build_usage_filter(f, t, model, status, provider=provider, organization_id=target_org)
    logs = await db.ai_usage_logs.find(q, {"_id": 0}).to_list(50_000)
    total_calls = len(logs)
    success_calls = sum(1 for l in logs if l.get("status") == "success")
    error_calls = total_calls - success_calls
    total_tokens = sum(int(l.get("total_tokens") or 0) for l in logs)
    total_cost = round(sum(float(l.get("estimated_cost_usd") or 0.0) for l in logs), 6)
    provider_cost = round(sum(float(l.get("provider_cost_usd") or 0.0) for l in logs), 6)
    provider_cost_calls = sum(1 for l in logs if l.get("provider_cost_usd") is not None)
    token_measured_calls = sum(1 for l in logs if l.get("status") == "success" and int(l.get("total_tokens") or 0) > 0)
    breakdowns = [ai_usage.billing_breakdown(log) for log in logs]
    base_cost = round(sum(float(item["base_cost_usd"]) for item in breakdowns), 6)
    ai_fee = round(sum(float(item["ai_fee_usd"]) for item in breakdowns), 6)
    billable_cost = round(sum(float(item["billable_cost_usd"]) for item in breakdowns), 6)

    bot_logs = [l for l in logs if l.get("purpose") == "bot_pipeline"]
    latencies = sorted(int(l.get("latency_ms") or 0) for l in bot_logs if int(l.get("latency_ms") or 0) > 0)
    p95_latency = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else 0
    bot_event_query: dict = {"created_at": {"$gte": f, "$lte": t}}
    if target_org and target_org != "__all__":
        bot_event_query["organization_id"] = target_org
    bot_events = await db.bot_events.find(bot_event_query, {"_id": 0}).to_list(50_000)
    decisions: dict[str, int] = {}
    channels: dict[str, int] = {}
    confidence_values: list[float] = []
    context_sizes: list[int] = []
    for item in bot_events:
        decision_name = item.get("decision") or "sin_decision"
        decisions[decision_name] = decisions.get(decision_name, 0) + 1
        channel_name = item.get("channel") or "sin_canal"
        channels[channel_name] = channels.get(channel_name, 0) + 1
        if item.get("confidence") is not None:
            confidence_values.append(float(item.get("confidence") or 0.0))
        if item.get("context_characters") is not None:
            context_sizes.append(int(item.get("context_characters") or 0))
    event_total = len(bot_events)
    bot_errors = sum(1 for item in bot_events if item.get("status") == "error")
    handoffs = decisions.get("require_human", 0)
    replies = decisions.get("reply_with_bot", 0)
    quality_alerts = []
    if event_total and bot_errors * 100 / event_total > 5:
        quality_alerts.append("La tasa de errores del bot supera el 5%.")
    if event_total and handoffs * 100 / event_total > 40:
        quality_alerts.append("Más del 40% de las consultas se derivan a una persona.")
    if p95_latency > 8000:
        quality_alerts.append("El 5% más lento de las respuestas tarda más de 8 segundos.")
    avg_prompt_tokens = round(
        sum(int(item.get("prompt_tokens") or 0) for item in bot_logs) / len(bot_logs), 1
    ) if bot_logs else 0.0
    if avg_prompt_tokens > 6000:
        quality_alerts.append("El contexto promedio supera 6.000 tokens de entrada.")
    bot_performance = {
        "events": event_total,
        "replies": replies,
        "handoffs": handoffs,
        "errors": bot_errors,
        "reply_rate_pct": round(replies * 100.0 / event_total, 1) if event_total else 0.0,
        "handoff_rate_pct": round(handoffs * 100.0 / event_total, 1) if event_total else 0.0,
        "error_rate_pct": round(bot_errors * 100.0 / event_total, 1) if event_total else 0.0,
        "average_confidence_pct": round(sum(confidence_values) * 100.0 / len(confidence_values), 1) if confidence_values else 0.0,
        "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "p95_latency_ms": p95_latency,
        "average_prompt_tokens": avg_prompt_tokens,
        "average_completion_tokens": round(sum(int(item.get("completion_tokens") or 0) for item in bot_logs) / len(bot_logs), 1) if bot_logs else 0.0,
        "average_context_characters": round(sum(context_sizes) / len(context_sizes)) if context_sizes else 0,
        "decisions": decisions,
        "channels": channels,
        "alerts": quality_alerts,
    }

    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_conv: dict[str, dict] = {}
    for l, billing in zip(logs, breakdowns):
        m = l.get("model") or "unknown"
        bm = by_model.setdefault(m, {"model": m, "calls": 0, "tokens": 0, "cost_usd": 0.0, "provider_cost_usd": 0.0, "base_cost_usd": 0.0, "ai_fee_usd": 0.0, "billable_cost_usd": 0.0})
        bm["calls"] += 1
        bm["tokens"] += int(l.get("total_tokens") or 0)
        bm["cost_usd"] = round(bm["cost_usd"] + float(l.get("estimated_cost_usd") or 0.0), 6)
        bm["provider_cost_usd"] = round(bm["provider_cost_usd"] + float(l.get("provider_cost_usd") or 0.0), 6)
        bm["base_cost_usd"] = round(bm["base_cost_usd"] + float(billing["base_cost_usd"]), 6)
        bm["ai_fee_usd"] = round(bm["ai_fee_usd"] + float(billing["ai_fee_usd"]), 6)
        bm["billable_cost_usd"] = round(bm["billable_cost_usd"] + float(billing["billable_cost_usd"]), 6)

        d = (l.get("created_at") or "")[:10]
        bd = by_day.setdefault(d, {"date": d, "calls": 0, "tokens": 0, "cost_usd": 0.0, "provider_cost_usd": 0.0, "base_cost_usd": 0.0, "ai_fee_usd": 0.0, "billable_cost_usd": 0.0})
        bd["calls"] += 1
        bd["tokens"] += int(l.get("total_tokens") or 0)
        bd["cost_usd"] = round(bd["cost_usd"] + float(l.get("estimated_cost_usd") or 0.0), 6)
        bd["provider_cost_usd"] = round(bd["provider_cost_usd"] + float(l.get("provider_cost_usd") or 0.0), 6)
        bd["base_cost_usd"] = round(bd["base_cost_usd"] + float(billing["base_cost_usd"]), 6)
        bd["ai_fee_usd"] = round(bd["ai_fee_usd"] + float(billing["ai_fee_usd"]), 6)
        bd["billable_cost_usd"] = round(bd["billable_cost_usd"] + float(billing["billable_cost_usd"]), 6)

        cid = l.get("conversation_id")
        if cid:
            bc = by_conv.setdefault(cid, {"conversation_id": cid, "calls": 0, "cost_usd": 0.0, "base_cost_usd": 0.0, "ai_fee_usd": 0.0, "billable_cost_usd": 0.0})
            bc["calls"] += 1
            bc["cost_usd"] = round(bc["cost_usd"] + float(l.get("estimated_cost_usd") or 0.0), 6)
            bc["base_cost_usd"] = round(bc["base_cost_usd"] + float(billing["base_cost_usd"]), 6)
            bc["ai_fee_usd"] = round(bc["ai_fee_usd"] + float(billing["ai_fee_usd"]), 6)
            bc["billable_cost_usd"] = round(bc["billable_cost_usd"] + float(billing["billable_cost_usd"]), 6)

    top_conversations = sorted(
        by_conv.values(), key=lambda x: x["billable_cost_usd"], reverse=True
    )[:10]
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
        "base_cost_usd": base_cost,
        "ai_fee_usd": ai_fee,
        "billable_cost_usd": billable_cost,
        "bot_performance": bot_performance,
        "token_measured_calls": token_measured_calls,
        "measurement": {
            "tokens": "provider_response",
            "cost": "mixed" if provider_cost_calls else "estimated",
            "token_coverage_pct": round(token_measured_calls * 100.0 / success_calls, 1) if success_calls else 0.0,
            "provider_cost_coverage_pct": round(provider_cost_calls * 100.0 / success_calls, 1) if success_calls else 0.0,
        },
        "by_model": sorted(
            by_model.values(), key=lambda x: x["billable_cost_usd"], reverse=True
        ),
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
    organization_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_perm("ai_view")),
):
    from ai import usage as ai_usage
    f, t = _date_bounds(from_, to)
    target_org = organization_id if admin.is_platform_admin else admin.organization_id
    q = _build_usage_filter(f, t, model, status, conversation_id, provider, organization_id=target_org)
    total = await db.ai_usage_logs.count_documents(q)
    items = await db.ai_usage_logs.find(q, {"_id": 0}) \
        .sort("created_at", -1).to_list(offset + limit)
    page = [
        {**item, **ai_usage.billing_breakdown(item)}
        for item in items[offset:offset + limit]
    ]
    return {"items": page, "total": total,
            "limit": limit, "offset": offset}


@api_router.get("/admin/ai-usage/quick")
async def admin_ai_usage_quick(
    organization_id: str | None = None,
    admin: User = Depends(require_perm("ai_view")),
):
    from ai import usage as ai_usage
    today = datetime.now(timezone.utc).date()
    today_iso_f = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    month_iso_f = datetime.combine(today.replace(day=1), datetime.min.time(),
                                   tzinfo=timezone.utc).isoformat()
    target_org = organization_id if admin.is_platform_admin else admin.organization_id

    async def _agg(base_query):
        query = dict(base_query)
        if target_org and target_org != "__all__":
            query["organization_id"] = target_org
        items = await db.ai_usage_logs.find(query, {"_id": 0}).to_list(50_000)
        billing = [ai_usage.billing_breakdown(item) for item in items]
        return {
            "calls": len(items),
            "tokens": sum(int(i.get("total_tokens") or 0) for i in items),
            "cost_usd": round(sum(float(i.get("estimated_cost_usd") or 0.0) for i in items), 6),
            "provider_cost_usd": round(sum(float(i.get("provider_cost_usd") or 0.0) for i in items), 6),
            "provider_cost_calls": sum(1 for i in items if i.get("provider_cost_usd") is not None),
            "base_cost_usd": round(sum(float(i["base_cost_usd"]) for i in billing), 6),
            "ai_fee_usd": round(sum(float(i["ai_fee_usd"]) for i in billing), 6),
            "billable_cost_usd": round(sum(float(i["billable_cost_usd"]) for i in billing), 6),
        }

    today_stats   = await _agg({"created_at": {"$gte": today_iso_f}})
    month_stats   = await _agg({"created_at": {"$gte": month_iso_f}})
    all_stats     = await _agg({})

    by_model: dict[str, int] = {}
    query_all = {}
    if target_org and target_org != "__all__":
        query_all["organization_id"] = target_org
    all_logs = await db.ai_usage_logs.find(query_all, {"_id": 0, "model": 1}).to_list(50_000)
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
    admin: User = Depends(require_perm("ai_view")),
):
    from ai import provider_usage
    return await provider_usage.reporting_status(
        db, include_credentials=admin.is_platform_admin
    )


@api_router.put("/admin/ai-usage/provider-reporting/{provider}")
async def admin_ai_usage_provider_reporting_put(
    provider: str,
    payload: AIUsageReportingKeyBody,
    admin: User = Depends(require_platform_admin),
):
    from ai import provider_usage
    try:
        await provider_usage.save_reporting_key(db, provider, payload.key, admin.user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return await provider_usage.reporting_status(db, include_credentials=True)


@api_router.post("/admin/ai-usage/provider-report")
async def admin_ai_usage_provider_report(
    provider: str,
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    admin: User = Depends(require_platform_admin),
):
    from ai import provider_usage
    try:
        return await provider_usage.fetch_provider_report(db, provider, from_, to)
    except provider_usage.ProviderUsageError as exc:
        raise HTTPException(400, str(exc))


@api_router.get("/admin/ai-pricing")
async def admin_ai_pricing_get(admin: User = Depends(require_perm("ai_view"))):
    from ai import usage as ai_usage
    pricing = await ai_usage.load_pricing(db)
    return {"models": pricing, "defaults": ai_usage.DEFAULT_PRICING,
            "metadata": await ai_usage.load_pricing_metadata(db)}


class AIPriceItem(BaseModel):
    model: str
    input_per_million: float
    output_per_million: float
    fee_percent: Optional[float] = None


class AIBillingPolicyUpdate(BaseModel):
    default_fee_percent: float


class AIVariableBillingPolicyUpdate(BaseModel):
    enabled: Optional[bool] = None
    usd_to_ars_rate: Optional[float] = None
    fx_buffer_percent: Optional[float] = None
    settlement_lead_hours: Optional[int] = None
    max_rate_age_hours: Optional[int] = None
    mp_fee_percent: Optional[float] = None
    tax_percent: Optional[float] = None
    min_net_margin_percent: Optional[float] = None
    min_ai_margin_percent: Optional[float] = None
    profitability_enforcement: Optional[Literal["block", "warn"]] = None
    max_retry_attempts: Optional[int] = None
    retry_cooldown_minutes: Optional[int] = None


@api_router.get("/platform/ai-billing")
async def platform_get_ai_billing(
    platform_admin: User = Depends(require_platform_admin),
):
    from ai import usage as ai_usage
    return await ai_usage.load_billing_policy(db)


@api_router.put("/platform/ai-billing")
async def platform_put_ai_billing(
    payload: AIBillingPolicyUpdate,
    platform_admin: User = Depends(require_platform_admin),
):
    from ai import usage as ai_usage
    try:
        return await ai_usage.save_billing_policy(
            db, payload.default_fee_percent, platform_admin.user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.get("/platform/ai-settlement-policy")
async def platform_get_ai_settlement_policy(
    platform_admin: User = Depends(require_platform_admin),
):
    from billing import ai_settlement
    policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    return {**policy, "rate_is_fresh": ai_settlement.rate_is_fresh(policy)}


@api_router.put("/platform/ai-settlement-policy")
async def platform_put_ai_settlement_policy(
    payload: AIVariableBillingPolicyUpdate,
    platform_admin: User = Depends(require_platform_admin),
):
    from billing import ai_settlement
    try:
        policy = await ai_settlement.save_policy(
            _raw_collection("pricing_config"),
            payload.model_dump(exclude_unset=True), platform_admin.user_id,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**policy, "rate_is_fresh": ai_settlement.rate_is_fresh(policy)}


@api_router.post("/platform/ai-settlement-policy/refresh-rate")
async def platform_refresh_ai_exchange_rate(
    platform_admin: User = Depends(require_platform_admin),
):
    from billing import ai_settlement
    try:
        policy = await ai_settlement.refresh_bcra_rate(
            _raw_collection("pricing_config"), platform_admin.user_id,
        )
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {**policy, "rate_is_fresh": ai_settlement.rate_is_fresh(policy)}


@api_router.post("/platform/ai-settlements/run")
async def platform_run_ai_settlements(
    organization_id: Optional[str] = None,
    platform_admin: User = Depends(require_platform_admin),
):
    if organization_id:
        organization = await _raw_collection("organizations").find_one(
            {"organization_id": organization_id}, {"_id": 0},
        )
        if not organization:
            raise HTTPException(404, "Empresa no encontrada")
        if _organization_ai_variable_billing(organization)["state"] == "pilot":
            raise HTTPException(
                409,
                "Las empresas piloto deben revisarse y aprobarse desde la vista previa",
            )
    return await process_due_ai_settlements(force_organization_id=organization_id)


@api_router.get("/platform/ai-settlements")
async def platform_list_ai_settlements(
    organization_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    platform_admin: User = Depends(require_platform_admin),
):
    query = {"organization_id": organization_id} if organization_id else {}
    items = await _billing_statement_items(query, limit)
    from billing import ai_settlement
    policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    return {
        "items": items,
        "count": len(items),
        "retry_policy": {
            "max_attempts": policy["max_retry_attempts"],
            "cooldown_minutes": policy["retry_cooldown_minutes"],
        },
    }


class OrganizationAIBillingUpdate(BaseModel):
    state: Optional[Literal["disabled", "simulation", "pilot", "active"]] = None
    ai_fee_percent: Optional[float] = None
    fx_buffer_percent: Optional[float] = None
    billing_start_date: Optional[str] = None
    max_monthly_ai_cost_usd: Optional[float] = None
    limit_action: Optional[Literal["block", "warn", "request_expansion"]] = None
    min_net_margin_percent: Optional[float] = None
    min_ai_margin_percent: Optional[float] = None
    profitability_enforcement: Optional[Literal["block", "warn"]] = None


class AISimulationRequest(BaseModel):
    organization_id: str
    period_start: Optional[str] = None
    period_end: Optional[str] = None


class AIPilotPreviewRequest(BaseModel):
    organization_id: str


class AIPilotApprovalRequest(BaseModel):
    organization_id: str
    preview_fingerprint: str
    confirmation: str


class AISettlementRetryRequest(BaseModel):
    confirmation: str


class AIKeyCredentialPayload(BaseModel):
    provider: str
    api_key: str
    label: Optional[str] = None
    assigned_organization_ids: Optional[list[str]] = None
    is_active: bool = True


@api_router.patch("/platform/organizations/{organization_id}/ai-variable-billing")
async def platform_update_org_ai_variable_billing(
    organization_id: str,
    payload: OrganizationAIBillingUpdate,
    platform_admin: User = Depends(require_platform_admin),
):
    """Update the tenant rollout mode and its variable-AI billing overrides."""
    org = await _raw_collection("organizations").find_one({"organization_id": organization_id}, {"_id": 0})
    if not org:
        raise HTTPException(404, "Empresa no encontrada")
    patch = payload.model_dump(exclude_unset=True)
    current_billing = _organization_ai_variable_billing(org)

    if "ai_fee_percent" in patch and patch["ai_fee_percent"] is not None:
        from ai import usage as ai_usage
        try:
            patch["ai_fee_percent"] = ai_usage.validate_fee_percent(patch["ai_fee_percent"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "fx_buffer_percent" in patch and patch["fx_buffer_percent"] is not None:
        buffer = float(patch["fx_buffer_percent"])
        if not math.isfinite(buffer) or buffer < 0 or buffer > 100:
            raise HTTPException(400, "El colchón cambiario debe estar entre 0% y 100%")
        patch["fx_buffer_percent"] = round(buffer, 4)
    if "max_monthly_ai_cost_usd" in patch and patch["max_monthly_ai_cost_usd"] is not None:
        maximum = float(patch["max_monthly_ai_cost_usd"])
        if not math.isfinite(maximum) or maximum <= 0:
            raise HTTPException(400, "El límite mensual debe ser mayor a cero")
        patch["max_monthly_ai_cost_usd"] = round(maximum, 4)
    for field, label in (
        ("min_net_margin_percent", "El margen neto mínimo"),
        ("min_ai_margin_percent", "El margen mínimo de IA"),
    ):
        if field in patch and patch[field] is not None:
            margin = float(patch[field])
            if not math.isfinite(margin) or margin < 0 or margin > 100:
                raise HTTPException(400, f"{label} debe estar entre 0% y 100%")
            patch[field] = round(margin, 4)
    if "billing_start_date" in patch:
        if patch["billing_start_date"] in (None, ""):
            patch["billing_start_date"] = None
        else:
            parsed_start = _parse_billing_datetime(patch["billing_start_date"])
            if not parsed_start:
                raise HTTPException(400, "Fecha de inicio de facturación inválida")
            patch["billing_start_date"] = parsed_start.isoformat()

    next_state = patch.get("state", current_billing["state"])
    if (
        next_state in {"disabled", "simulation"}
        and current_billing["state"] in {"pilot", "active"}
        and next_state != current_billing["state"]
    ):
        open_statement = await _raw_collection("ai_billing_statements").find_one({
            "organization_id": organization_id,
            "status": {"$in": ["applying", "applied", "payment_failed"]},
        }, {"_id": 0})
        if open_statement:
            raise HTTPException(
                409,
                "No se puede desactivar mientras exista una liquidación aplicada o pendiente de pago",
            )
    if (
        next_state in {"pilot", "active"}
        and not patch.get("billing_start_date")
        and not current_billing.get("billing_start_date")
    ):
        patch["billing_start_date"] = now_iso()

    changed_at = now_iso()
    updated_billing = {
        **current_billing,
        **patch,
        "state": next_state,
        "updated_at": changed_at,
        "updated_by": platform_admin.user_id,
    }
    db_update: dict[str, Any] = {
        "$set": {"ai_variable_billing": updated_billing, "updated_at": changed_at}
    }
    if "ai_fee_percent" in patch:
        if patch["ai_fee_percent"] is None:
            db_update["$unset"] = {"ai_fee_percent": ""}
        else:
            db_update["$set"]["ai_fee_percent"] = patch["ai_fee_percent"]
    await _raw_collection("organizations").update_one(
        {"organization_id": organization_id},
        db_update,
    )
    await _raw_collection("billing_events").insert_one({
        "event_id": new_id("billevt"),
        "organization_id": organization_id,
        "type": "organization_ai_variable_billing_updated",
        "previous_state": current_billing["state"],
        "new_state": next_state,
        "changes": patch,
        "actor_user_id": platform_admin.user_id,
        "actor_email": platform_admin.email,
        "created_at": changed_at,
    })
    return {"organization_id": organization_id, "ai_variable_billing": updated_billing}


@api_router.post("/platform/ai-billing/simulate")
async def platform_simulate_ai_billing(
    payload: AISimulationRequest,
    platform_admin: User = Depends(require_platform_admin),
):
    """Project the next AI settlement without writing data or calling providers."""
    from billing import ai_settlement
    org = await _raw_collection("organizations").find_one({"organization_id": payload.organization_id}, {"_id": 0})
    if not org:
        raise HTTPException(404, "Empresa no encontrada")

    policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    plan_code = org.get("plan_code") or "base"
    plan = PLAN_CATALOG.get(plan_code) or PLAN_CATALOG["base"]

    org_billing = _organization_ai_variable_billing(org)
    organization_buffer = org_billing.get("fx_buffer_percent")
    effective_policy = _effective_ai_settlement_policy(org, policy)
    fx_buffer = float(effective_policy.get("fx_buffer_percent") or 0.0)
    rate = float(effective_policy.get("usd_to_ars_rate") or 0.0)
    if rate <= 0:
        raise HTTPException(400, "Configurá una cotización USD/ARS antes de simular")

    now = datetime.now(timezone.utc)

    def parse_period_boundary(value: Optional[str], *, end: bool = False) -> Optional[datetime]:
        if not value:
            return None
        parsed = ai_settlement.parse_datetime(value)
        if not parsed:
            raise HTTPException(400, "El período indicado no tiene una fecha válida")
        if end and len(str(value).strip()) == 10:
            parsed += timedelta(days=1)
        return parsed

    explicit_start = parse_period_boundary(payload.period_start)
    explicit_end = parse_period_boundary(payload.period_end, end=True)
    charge_at = _parse_billing_datetime(org.get("current_period_end"))
    period_end = explicit_end or (min(now, charge_at) if charge_at else now)
    period_start = explicit_start \
        or ai_settlement.parse_datetime(org.get("last_ai_settlement_end")) \
        or ai_settlement.parse_datetime(org.get("provider_last_payment_at")) \
        or ai_settlement.previous_cycle_start(charge_at or period_end)
    billing_start = ai_settlement.parse_datetime(org_billing.get("billing_start_date"))
    if billing_start and billing_start > period_start:
        period_start = billing_start
    if period_start >= period_end:
        raise HTTPException(400, "La fecha desde debe ser anterior a la fecha hasta")
    if period_end - period_start > timedelta(days=366):
        raise HTTPException(400, "La simulación admite un período máximo de 366 días")

    query: dict[str, Any] = {
        "organization_id": payload.organization_id,
        "status": "success",
        "created_at": {"$gte": period_start.isoformat(), "$lt": period_end.isoformat()},
    }

    logs = await _raw_collection("ai_usage_logs").find(query, {"_id": 0}).to_list(100_000)
    from ai import usage as ai_usage
    from ai.usage import PLAN_MONTHLY_AI_TOKENS
    included_tokens = PLAN_MONTHLY_AI_TOKENS.get(plan_code, 250_000)
    fee_policy = await ai_usage.load_billing_policy(db)
    fee_override = org_billing.get("ai_fee_percent")
    configured_fee = float(
        fee_override if fee_override is not None else fee_policy["default_fee_percent"]
    )

    simulation = ai_settlement.simulate_settlement(
        organization_id=payload.organization_id,
        plan_name=plan["name"],
        plan_amount_ars=float(plan["monthly_price_ars"]),
        logs=logs,
        usd_to_ars_rate=rate,
        fx_buffer_percent=fx_buffer,
        included_tokens=included_tokens,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        exchange_rate_source=str(policy.get("exchange_rate_source") or "not_configured"),
        exchange_rate_observed_at=policy.get("exchange_rate_observed_at"),
        configured_fee_percent=configured_fee,
        fee_source="organization" if fee_override is not None else "global",
        buffer_source="organization" if organization_buffer is not None else "global",
        mp_fee_percent=float(effective_policy["mp_fee_percent"]),
        tax_percent=float(effective_policy["tax_percent"]),
        min_margin_percent=float(effective_policy["min_net_margin_percent"]),
        min_ai_margin_percent=float(effective_policy["min_ai_margin_percent"]),
        profitability_enforcement=str(effective_policy["profitability_enforcement"]),
    )
    return {
        **simulation,
        "organization_name": org.get("name") or payload.organization_id,
        "organization_billing_state": org_billing["state"],
        "organization_billing_start_date": org_billing.get("billing_start_date"),
        "organization_fx_buffer_override": organization_buffer,
    }


@api_router.post("/platform/ai-billing/pilot-preview")
async def platform_preview_pilot_ai_settlement(
    payload: AIPilotPreviewRequest,
    platform_admin: User = Depends(require_platform_admin),
):
    """Return the exact proposal that must be approved for a pilot company."""
    org = await _raw_collection("organizations").find_one(
        {"organization_id": payload.organization_id}, {"_id": 0},
    )
    if not org:
        raise HTTPException(404, "Empresa no encontrada")
    from billing import ai_settlement
    policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    return await _build_pilot_settlement_preview(org, policy)


@api_router.post("/platform/ai-billing/pilot-apply")
async def platform_apply_pilot_ai_settlement(
    payload: AIPilotApprovalRequest,
    platform_admin: User = Depends(require_platform_admin),
):
    """Apply a reviewed pilot only if its commercial values are unchanged."""
    if payload.confirmation != "APLICAR":
        raise HTTPException(400, "La aprobación explícita es obligatoria")
    if len(payload.preview_fingerprint) != 64:
        raise HTTPException(400, "La vista previa indicada no es válida")
    org = await _raw_collection("organizations").find_one(
        {"organization_id": payload.organization_id}, {"_id": 0},
    )
    if not org:
        raise HTTPException(404, "Empresa no encontrada")
    from billing import ai_settlement
    policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    preview = await _build_pilot_settlement_preview(org, policy)
    if not preview["ready"]:
        raise HTTPException(status_code=409, detail={
            "message": "La liquidación piloto no está lista para aplicarse",
            "blockers": preview["blockers"],
        })
    if preview["preview_fingerprint"] != payload.preview_fingerprint:
        raise HTTPException(status_code=409, detail={
            "message": "La liquidación cambió. Revisá la nueva vista previa antes de aprobar",
            "code": "preview_changed",
        })
    result = await _apply_ai_settlement(
        org, policy, force=True, manual=True,
        expected_preview_fingerprint=payload.preview_fingerprint,
        approved_by=platform_admin,
    )
    if result.get("status") == "preview_changed":
        raise HTTPException(status_code=409, detail={
            "message": "La liquidación cambió. Revisá la nueva vista previa antes de aprobar",
            "code": "preview_changed",
        })
    return result


@api_router.post("/platform/ai-billing/statements/{statement_id}/retry")
async def platform_retry_ai_settlement(
    statement_id: str,
    payload: AISettlementRetryRequest,
    platform_admin: User = Depends(require_platform_admin),
):
    """Retry one frozen failed statement with an audited, idempotent claim."""
    if payload.confirmation != "REINTENTAR":
        raise HTTPException(400, "La confirmación explícita es obligatoria")
    stmt = await _raw_collection("ai_billing_statements").find_one({"statement_id": statement_id}, {"_id": 0})
    if not stmt:
        raise HTTPException(404, "Liquidación no encontrada")
    org = await _raw_collection("organizations").find_one({"organization_id": stmt["organization_id"]}, {"_id": 0})
    if not org:
        raise HTTPException(404, "Empresa no encontrada")

    from billing import ai_settlement
    policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    return await _retry_failed_ai_statement(stmt, org, policy, platform_admin)


AI_STATEMENT_STATUSES = {
    "pending", "applying", "applied", "paid", "payment_failed", "failed",
    "retrying", "retry_exhausted", "blocked_margin", "closed_no_charge",
}

AI_STATEMENT_STATUS_LABELS = {
    "pending": "Pendiente", "applying": "Aplicando",
    "applied": "Incluida en la próxima renovación", "paid": "Cobrada",
    "payment_failed": "Pago rechazado", "failed": "Error técnico",
    "retrying": "Reintentando", "retry_exhausted": "Reintentos agotados",
    "blocked_margin": "Bloqueada por rentabilidad",
    "closed_no_charge": "Cerrada sin saldo",
}


def _billing_statement_query(*, organization_id: str, status: Optional[str] = None,
                             start_date: Optional[str] = None,
                             end_date: Optional[str] = None) -> dict:
    query: dict[str, Any] = {"organization_id": organization_id}
    if status:
        if status not in AI_STATEMENT_STATUSES:
            raise HTTPException(400, "Estado de liquidación inválido")
        query["status"] = status
    created_at: dict[str, str] = {}
    if start_date:
        if len(start_date.strip()) == 10:
            try:
                start = datetime.fromisoformat(start_date.strip()).replace(
                    tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"),
                ).astimezone(timezone.utc)
            except ValueError:
                start = None
        else:
            start = _parse_billing_datetime(start_date)
        if not start:
            raise HTTPException(400, "Fecha desde inválida")
        created_at["$gte"] = start.isoformat()
    if end_date:
        if len(end_date.strip()) == 10:
            try:
                end = (
                    datetime.fromisoformat(end_date.strip()).replace(
                        tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"),
                    ) + timedelta(days=1)
                ).astimezone(timezone.utc)
            except ValueError:
                end = None
        else:
            end = _parse_billing_datetime(end_date)
        if not end:
            raise HTTPException(400, "Fecha hasta inválida")
        created_at["$lt"] = end.isoformat()
    if created_at:
        if created_at.get("$gte") and created_at.get("$lt") \
                and created_at["$gte"] >= created_at["$lt"]:
            raise HTTPException(400, "La fecha desde debe ser anterior a la fecha hasta")
        query["created_at"] = created_at
    return query


async def _billing_statement_items(query: dict, limit: int) -> list[dict]:
    statements = await _raw_collection("ai_billing_statements").find(
        query, {"_id": 0},
    ).sort("created_at", -1).to_list(limit)
    organization_ids = list({
        statement.get("organization_id") for statement in statements
        if statement.get("organization_id")
    })
    organizations = await _raw_collection("organizations").find(
        {"organization_id": {"$in": organization_ids}},
        {"_id": 0},
    ).to_list(len(organization_ids) or 1)
    names = {item["organization_id"]: item.get("name") for item in organizations}
    return [
        {**statement, "organization_name": names.get(statement.get("organization_id"))}
        for statement in statements
    ]


def _safe_csv_cell(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _billing_statements_csv(statements: list[dict]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "liquidacion_id", "empresa_id", "empresa", "plan", "periodo_desde",
        "periodo_hasta", "llamadas", "tokens", "costo_proveedor_usd",
        "fee_latus_usd", "total_ia_usd", "cotizacion_usd_ars",
        "colchon_cambiario_pct", "plan_ars", "ia_ars", "total_ars", "estado",
        "intentos", "creada", "aplicada", "cobrada", "pago_id",
    ])
    for statement in statements:
        writer.writerow([_safe_csv_cell(value) for value in [
            statement.get("statement_id"), statement.get("organization_id"),
            statement.get("organization_name"), statement.get("plan_code"),
            statement.get("period_start"), statement.get("period_end"),
            statement.get("calls", 0), statement.get("tokens", 0),
            statement.get("base_cost_usd", 0), statement.get("ai_fee_usd", 0),
            statement.get("billable_cost_usd", 0), statement.get("usd_to_ars_rate", 0),
            statement.get("fx_buffer_percent", 0), statement.get("plan_amount_ars", 0),
            statement.get("ai_amount_ars", 0), statement.get("total_amount_ars", 0),
            AI_STATEMENT_STATUS_LABELS.get(statement.get("status"), statement.get("status")),
            statement.get("retry_count", 0), statement.get("created_at"),
            statement.get("applied_at"), statement.get("paid_at"),
            statement.get("provider_payment_id"),
        ]])
    return "\ufeff" + output.getvalue()


@api_router.get("/billing/statements")
async def list_billing_statements(
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
):
    """Tenant-scoped billing history; platform access never widens this route."""
    query = _billing_statement_query(
        organization_id=user.organization_id, status=status,
        start_date=start_date, end_date=end_date,
    )
    items = await _billing_statement_items(query, limit)
    return {"items": items, "count": len(items)}


@api_router.get("/billing/statements/export")
async def export_billing_statements(
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: User = Depends(get_current_user),
):
    """Export only the current tenant's frozen billing statements."""
    query = _billing_statement_query(
        organization_id=user.organization_id, status=status,
        start_date=start_date, end_date=end_date,
    )
    statements = await _billing_statement_items(query, 5000)
    safe_org = "".join(c for c in str(user.organization_id or "empresa") if c.isalnum() or c in "-_")
    return Response(
        content=_billing_statements_csv(statements),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="liquidaciones-{safe_org}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@api_router.get("/platform/ai-settlements/export")
async def platform_export_ai_settlements(
    organization_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    platform_admin: User = Depends(require_platform_admin),
):
    query: dict[str, Any] = {}
    if organization_id and organization_id != "__all__":
        query = _billing_statement_query(
            organization_id=organization_id, status=status,
            start_date=start_date, end_date=end_date,
        )
    else:
        if status:
            if status not in AI_STATEMENT_STATUSES:
                raise HTTPException(400, "Estado de liquidación inválido")
            query["status"] = status
        date_query = _billing_statement_query(
            organization_id="__all__", start_date=start_date, end_date=end_date,
        ).get("created_at")
        if date_query:
            query["created_at"] = date_query
    statements = await _billing_statement_items(query, 10_000)
    suffix = organization_id if organization_id and organization_id != "__all__" else "global"
    safe_suffix = "".join(c for c in suffix if c.isalnum() or c in "-_")
    return Response(
        content=_billing_statements_csv(statements),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="liquidaciones-{safe_suffix}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@api_router.get("/billing/statements/{statement_id}/receipt")
async def get_statement_receipt_html(
    statement_id: str,
    user: User = Depends(get_current_user),
):
    """Printable, non-fiscal statement detail with strict tenant isolation."""
    stmt = await _raw_collection("ai_billing_statements").find_one({"statement_id": statement_id}, {"_id": 0})
    if not stmt:
        raise HTTPException(404, "Liquidación no encontrada")
    if not user.is_platform_admin and stmt.get("organization_id") != user.organization_id:
        raise HTTPException(403, "No tenés permiso para ver esta liquidación")
    organization = await _raw_collection("organizations").find_one(
        {"organization_id": stmt.get("organization_id")}, {"_id": 0},
    ) or {}

    def esc(value: Any) -> str:
        return html.escape(str(value or "—"), quote=True)

    def amount(value: Any, currency: str = "ARS") -> str:
        try:
            raw = f"{float(value or 0):,.2f}"
        except (TypeError, ValueError):
            raw = "0.00"
        localized = raw.replace(",", "_").replace(".", ",").replace("_", ".")
        return f"{currency} {localized}"

    status_label = AI_STATEMENT_STATUS_LABELS.get(
        stmt.get("status"), str(stmt.get("status") or "Pendiente"),
    )
    receipt_html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>Detalle de liquidación {esc(stmt.get('statement_id'))}</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#f7f4ee;color:#102733;font-family:Inter,Segoe UI,Arial,sans-serif;padding:32px}}
.sheet{{max-width:780px;margin:auto;background:#fff;border:1px solid #e7dfd3;border-radius:22px;overflow:hidden;box-shadow:0 18px 45px rgba(16,39,51,.1)}}
header{{background:#102733;color:#fff;padding:30px}} h1{{font-size:24px;margin:8px 0}} .eyebrow{{color:#79b9ee;font-size:12px;font-weight:800;letter-spacing:.13em;text-transform:uppercase}}
.notice{{margin:22px 28px 0;padding:12px 14px;border:1px solid #f1d49b;background:#fff8e8;border-radius:12px;color:#785515;font-size:12px}}
.content{{padding:28px}} .meta{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:24px}} .card{{border:1px solid #eee7dc;border-radius:14px;padding:14px}}
.label{{color:#687b84;font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}} .value{{font-size:14px;font-weight:750;margin-top:5px;word-break:break-word}}
.row{{display:flex;justify-content:space-between;gap:20px;padding:12px 2px;border-bottom:1px solid #eee7dc;font-size:14px}} .row span:last-child{{font-weight:750;text-align:right}}
.total{{margin-top:18px;padding:18px;border-radius:14px;background:#eaf5ff;color:#145b91;font-size:18px;font-weight:900;display:flex;justify-content:space-between}}
.status{{display:inline-block;margin-top:20px;padding:8px 12px;border-radius:999px;background:#eef1f2;font-size:12px;font-weight:800}}
footer{{padding:18px 28px;border-top:1px solid #eee7dc;color:#687b84;font-size:11px}} @media(max-width:600px){{body{{padding:12px}}.meta{{grid-template-columns:1fr}}.content{{padding:20px}}}}
@media print{{body{{background:#fff;padding:0}}.sheet{{box-shadow:none;border:0;max-width:none}}}}
</style></head><body><main class="sheet">
<header><div class="eyebrow">Latus CRM · Suscripciones</div><h1>Detalle de liquidación</h1><div>{esc(status_label)}</div></header>
<div class="notice"><strong>Documento informativo no fiscal.</strong> Resume cómo se calculó la liquidación y no reemplaza una factura emitida conforme a ARCA.</div>
<section class="content"><div class="meta">
<div class="card"><div class="label">Empresa</div><div class="value">{esc(organization.get('name') or stmt.get('organization_id'))}</div></div>
<div class="card"><div class="label">Liquidación</div><div class="value">{esc(stmt.get('statement_id'))}</div></div>
<div class="card"><div class="label">Período de consumo</div><div class="value">{esc(str(stmt.get('period_start') or '')[:10])} al {esc(str(stmt.get('period_end') or '')[:10])}</div></div>
<div class="card"><div class="label">Fecha de creación</div><div class="value">{esc(stmt.get('created_at'))}</div></div>
</div>
<div class="row"><span>Plan ({esc(stmt.get('plan_code'))})</span><span>{amount(stmt.get('plan_amount_ars'))}</span></div>
<div class="row"><span>Costo del proveedor de IA</span><span>{amount(stmt.get('base_cost_usd'), 'USD')}</span></div>
<div class="row"><span>Fee de Latus</span><span>{amount(stmt.get('ai_fee_usd'), 'USD')}</span></div>
<div class="row"><span>Consumo facturable de IA</span><span>{amount(stmt.get('billable_cost_usd'), 'USD')}</span></div>
<div class="row"><span>Cotización aplicada</span><span>{amount(stmt.get('usd_to_ars_rate'), 'ARS/USD')}</span></div>
<div class="row"><span>Colchón cambiario</span><span>{esc(stmt.get('fx_buffer_percent') or 0)}%</span></div>
<div class="row"><span>Consumo de IA convertido</span><span>{amount(stmt.get('ai_amount_ars'))}</span></div>
<div class="total"><span>Total de la liquidación</span><span>{amount(stmt.get('total_amount_ars'))}</span></div>
<div class="status">Estado: {esc(status_label)}</div></section>
<footer>Identificador de empresa: {esc(stmt.get('organization_id'))} · Intentos técnicos: {esc(stmt.get('retry_count') or 0)} · Pago: {esc(stmt.get('provider_payment_id'))}</footer>
</main></body></html>"""
    from fastapi.responses import HTMLResponse
    safe_statement = "".join(c for c in statement_id if c.isalnum() or c in "-_")
    return HTMLResponse(
        content=receipt_html,
        headers={
            "Content-Disposition": f'inline; filename="liquidacion-{safe_statement}.html"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'self'",
        },
    )


def _resolve_dashboard_date_range(period: Optional[str], start_date: Optional[str],
                                  end_date: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return an inclusive-start/exclusive-end range using Argentina local dates."""
    selected = str(period or "this_month")
    if selected not in {"this_month", "prev_month", "last_30", "custom", "all"}:
        raise HTTPException(400, "Período financiero inválido")
    argentina = ZoneInfo("America/Argentina/Buenos_Aires")
    now_local = datetime.now(argentina)
    first_this_month = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if selected == "all":
        return None, None
    if selected == "this_month":
        start_local, end_local = first_this_month, now_local
    elif selected == "prev_month":
        previous_day = first_this_month - timedelta(days=1)
        start_local = previous_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = first_this_month
    elif selected == "last_30":
        start_local, end_local = now_local - timedelta(days=30), now_local
    else:
        if not start_date or not end_date:
            raise HTTPException(400, "Indicá fecha desde y fecha hasta")
        try:
            start_day = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
            end_day = datetime.strptime(end_date.strip(), "%Y-%m-%d").date()
            start_local = datetime.combine(start_day, datetime.min.time(), tzinfo=argentina)
            end_local = datetime.combine(
                end_day + timedelta(days=1), datetime.min.time(), tzinfo=argentina,
            )
        except (TypeError, ValueError):
            raise HTTPException(400, "El rango de fechas es inválido")
        if start_local >= end_local:
            raise HTTPException(400, "La fecha desde debe ser anterior o igual a la fecha hasta")

    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
    )


def _paid_statement_financials(statement: dict) -> dict[str, float]:
    """Read frozen economics from one paid statement without current-rate revaluation."""
    profitability = statement.get("profitability") or {}
    plan_revenue = float(statement.get("plan_amount_ars") or 0)
    ai_revenue = float(statement.get("ai_amount_ars") or 0)
    total_revenue = float(
        statement.get("total_amount_ars")
        if statement.get("total_amount_ars") is not None
        else plan_revenue + ai_revenue
    )
    stored_rate = float(statement.get("usd_to_ars_rate") or 0)
    provider_cost = float(
        profitability.get("provider_cost_ars")
        if profitability.get("provider_cost_ars") is not None
        else float(statement.get("base_cost_usd") or 0) * stored_rate
    )
    mp_fee = float(
        profitability.get("mp_fee_ars")
        if profitability.get("mp_fee_ars") is not None
        else total_revenue * float(statement.get("mp_fee_percent") or 0) / 100.0
    )
    tax = float(
        profitability.get("tax_ars")
        if profitability.get("tax_ars") is not None
        else total_revenue * float(statement.get("tax_percent") or 0) / 100.0
    )
    net_profit = float(
        profitability.get("net_profit_ars")
        if profitability.get("net_profit_ars") is not None
        else total_revenue - provider_cost - mp_fee - tax
    )
    return {
        "statements": 1,
        "plan_revenue_ars": round(plan_revenue, 2),
        "ai_revenue_ars": round(ai_revenue, 2),
        "total_revenue_ars": round(total_revenue, 2),
        "provider_cost_ars": round(provider_cost, 2),
        "mp_fee_ars": round(mp_fee, 2),
        "tax_ars": round(tax, 2),
        "net_profit_ars": round(net_profit, 2),
        "base_cost_usd": round(float(statement.get("base_cost_usd") or 0), 6),
        "ai_fee_usd": round(float(statement.get("ai_fee_usd") or 0), 6),
        "billable_cost_usd": round(float(statement.get("billable_cost_usd") or 0), 6),
    }


def _empty_realized_financials() -> dict[str, float]:
    return {
        "statements": 0, "plan_revenue_ars": 0.0, "ai_revenue_ars": 0.0,
        "total_revenue_ars": 0.0, "provider_cost_ars": 0.0,
        "mp_fee_ars": 0.0, "tax_ars": 0.0, "net_profit_ars": 0.0,
        "base_cost_usd": 0.0, "ai_fee_usd": 0.0, "billable_cost_usd": 0.0,
    }


def _add_realized_financials(target: dict, values: dict) -> None:
    for field, value in values.items():
        target[field] = round(float(target.get(field) or 0) + float(value or 0), 6)


@api_router.get("/platform/financial-dashboard")
async def platform_financial_dashboard(
    organization_id: Optional[str] = None,
    period: Optional[str] = "this_month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    platform_admin: User = Depends(require_platform_admin),
):
    """Superadmin Executive Billing & Operations Dashboard with company & period filtering."""
    from ai import usage as ai_usage
    from billing import ai_settlement
    from utils.alerts import list_system_alerts

    policy = await ai_settlement.load_policy(_raw_collection("pricing_config"))
    rate = float(policy.get("usd_to_ars_rate") or 1250.0)
    fx_buffer_default = float(policy.get("fx_buffer_percent") or 10.0)

    org_filter = {}
    if organization_id and organization_id not in ("__all__", ""):
        org_filter["organization_id"] = organization_id

    organizations = await _raw_collection("organizations").find(org_filter, {"_id": 0}).sort("created_at", -1).to_list(500)

    p_start, p_end = _resolve_dashboard_date_range(period, start_date, end_date)

    logs_query: dict[str, Any] = {}
    if organization_id and organization_id not in ("__all__", ""):
        logs_query["organization_id"] = organization_id
    if p_start or p_end:
        logs_query["created_at"] = {}
        if p_start:
            logs_query["created_at"]["$gte"] = p_start
        if p_end:
            logs_query["created_at"]["$lt"] = p_end

    usage_logs = await _raw_collection("ai_usage_logs").find(logs_query, {"_id": 0}).to_list(100_000)

    usage_by_org: dict[str, dict] = {}
    for log in usage_logs:
        org_id = log.get("organization_id")
        if not org_id:
            continue
        b = ai_usage.billing_breakdown(log)
        item = usage_by_org.setdefault(org_id, {
            "calls": 0, "tokens": 0, "base_cost_usd": 0.0, "ai_fee_usd": 0.0, "billable_cost_usd": 0.0,
        })
        item["calls"] += 1
        item["tokens"] += int(log.get("total_tokens") or 0)
        item["base_cost_usd"] = round(item["base_cost_usd"] + float(b["base_cost_usd"]), 6)
        item["ai_fee_usd"] = round(item["ai_fee_usd"] + float(b["ai_fee_usd"]), 6)
        item["billable_cost_usd"] = round(item["billable_cost_usd"] + float(b["billable_cost_usd"]), 6)

    paid_query: dict[str, Any] = {"status": "paid"}
    if organization_id and organization_id not in ("__all__", ""):
        paid_query["organization_id"] = organization_id
    paid_statements = await _raw_collection("ai_billing_statements").find(
        paid_query, {"_id": 0},
    ).sort("paid_at", -1).to_list(10_000)
    range_start = _parse_billing_datetime(p_start)
    range_end = _parse_billing_datetime(p_end)
    realized_by_org: dict[str, dict] = {}
    for statement in paid_statements:
        occurred_at = _parse_billing_datetime(
            statement.get("paid_at") or statement.get("updated_at") or statement.get("created_at")
        )
        if not occurred_at:
            continue
        if range_start and occurred_at < range_start:
            continue
        if range_end and occurred_at >= range_end:
            continue
        org_id = statement.get("organization_id")
        if not org_id:
            continue
        realized = realized_by_org.setdefault(org_id, _empty_realized_financials())
        _add_realized_financials(realized, _paid_statement_financials(statement))

    org_matrix = []
    total_subscriptions_ars = 0.0
    total_ai_billable_usd = 0.0
    total_ai_provider_cost_usd = 0.0
    total_ai_fee_usd = 0.0
    total_ai_billable_ars = 0.0
    total_ai_provider_cost_ars = 0.0
    total_mp_fee_ars = 0.0
    total_tax_ars = 0.0
    realized_totals = _empty_realized_financials()

    for org in organizations:
        org_id = org["organization_id"]
        plan_code = org.get("plan_code") or "base"
        plan = PLAN_CATALOG.get(plan_code) or PLAN_CATALOG["base"]
        plan_price = float(plan.get("monthly_price_ars") or 0.0)

        access = subscription_access_state(org)
        allowed = access.get("allowed", False)
        subscription_status = org.get("subscription_status") or "not_configured"
        license_status = org.get("license_status") or "not_configured"
        mrr_active = subscription_status == "active" and license_status not in {"suspended", "expired"}
        projected_plan_amount_ars = plan_price if mrr_active else 0.0

        u = usage_by_org.get(org_id, {
            "calls": 0, "tokens": 0, "base_cost_usd": 0.0, "ai_fee_usd": 0.0, "billable_cost_usd": 0.0,
        })
        realized = realized_by_org.get(org_id, _empty_realized_financials())
        _add_realized_financials(realized_totals, realized)

        if mrr_active:
            total_subscriptions_ars += projected_plan_amount_ars

        total_ai_billable_usd += u["billable_cost_usd"]
        total_ai_provider_cost_usd += u["base_cost_usd"]
        total_ai_fee_usd += u["ai_fee_usd"]

        org_var_billing = _organization_ai_variable_billing(org)
        effective_policy = _effective_ai_settlement_policy(org, policy)
        org_buffer = float(effective_policy["fx_buffer_percent"])
        ai_billable_ars = round(u["billable_cost_usd"] * rate * (1 + org_buffer / 100.0), 2)
        total_monthly_ars = round(projected_plan_amount_ars + ai_billable_ars, 2)
        profitability = ai_settlement.calculate_profitability_breakdown(
            plan_amount_ars=projected_plan_amount_ars,
            ai_amount_ars=ai_billable_ars,
            base_cost_usd=u["base_cost_usd"],
            usd_to_ars_rate=rate,
            mp_fee_percent=float(effective_policy["mp_fee_percent"]),
            tax_percent=float(effective_policy["tax_percent"]),
            min_margin_percent=float(effective_policy["min_net_margin_percent"]),
            min_ai_margin_percent=float(effective_policy["min_ai_margin_percent"]),
        )
        total_ai_billable_ars += ai_billable_ars
        total_ai_provider_cost_ars += profitability["provider_cost_ars"]
        summary_revenue_ars = projected_plan_amount_ars + ai_billable_ars
        total_mp_fee_ars += summary_revenue_ars * float(effective_policy["mp_fee_percent"]) / 100.0
        total_tax_ars += summary_revenue_ars * float(effective_policy["tax_percent"]) / 100.0

        org_matrix.append({
            "organization_id": org_id,
            "name": org.get("name") or "Sin nombre",
            "plan_code": plan_code,
            "plan_name": plan.get("name") or plan_code,
            "plan_price_ars": plan_price,
            "subscription_status": subscription_status,
            "license_status": license_status,
            "access_allowed": allowed,
            "mrr_active": mrr_active,
            "ai_state": org_var_billing.get("state") or ("active" if allowed else "disabled"),
            "billing_email": org.get("billing_email"),
            "current_period_end": org.get("current_period_end"),
            "internal_notes": org.get("internal_notes"),
            "ai_usage": {
                **u,
                "billable_cost_ars": ai_billable_ars,
            },
            "profitability": profitability,
            "profitability_enforcement": effective_policy["profitability_enforcement"],
            "total_monthly_ars": total_monthly_ars,
            "realized": {
                **realized,
                "net_margin_percent": round(
                    realized["net_profit_ars"] / realized["total_revenue_ars"] * 100.0, 1,
                ) if realized["total_revenue_ars"] > 0 else 0.0,
            },
        })

    total_ai_billable_ars = round(total_ai_billable_ars, 2)
    total_ai_provider_cost_ars = round(total_ai_provider_cost_ars, 2)
    total_ai_fee_ars = round(total_ai_fee_usd * rate, 2)

    total_revenue_ars = round(total_subscriptions_ars + total_ai_billable_ars, 2)
    estimated_net_profit_ars = round(
        total_revenue_ars - total_ai_provider_cost_ars - total_mp_fee_ars - total_tax_ars, 2,
    )
    net_margin_percent = round((estimated_net_profit_ars / total_revenue_ars * 100.0), 1) if total_revenue_ars > 0 else 0.0
    realized_margin_percent = round(
        realized_totals["net_profit_ars"] / realized_totals["total_revenue_ars"] * 100.0, 1,
    ) if realized_totals["total_revenue_ars"] > 0 else 0.0

    stmt_query = {}
    if organization_id and organization_id not in ("__all__", ""):
        stmt_query["organization_id"] = organization_id
    latest_statements = await _raw_collection("ai_billing_statements").find(stmt_query, {"_id": 0}).sort("created_at", -1).to_list(20)
    alerts = await list_system_alerts(db, organization_id=organization_id if organization_id and organization_id != "__all__" else None, limit=10)

    return {
        "summary": {
            "total_organizations": len(organizations),
            "active_licenses": sum(1 for o in org_matrix if o["access_allowed"]),
            "mrr_active_organizations": sum(1 for o in org_matrix if o["mrr_active"]),
            "usd_to_ars_rate": rate,
            "fx_buffer_percent": fx_buffer_default,
            "period": period,
            "period_start": p_start,
            "period_end": p_end,
            "period_end_exclusive": True,
            "realized_scope": "paid_ai_statements",
            "realized_statements": int(realized_totals["statements"]),
            "realized_plan_revenue_ars": round(realized_totals["plan_revenue_ars"], 2),
            "realized_ai_revenue_ars": round(realized_totals["ai_revenue_ars"], 2),
            "realized_total_revenue_ars": round(realized_totals["total_revenue_ars"], 2),
            "realized_provider_cost_ars": round(realized_totals["provider_cost_ars"], 2),
            "realized_mp_fee_ars": round(realized_totals["mp_fee_ars"], 2),
            "realized_tax_ars": round(realized_totals["tax_ars"], 2),
            "realized_net_profit_ars": round(realized_totals["net_profit_ars"], 2),
            "realized_net_margin_percent": realized_margin_percent,
            "realized_provider_cost_usd": round(realized_totals["base_cost_usd"], 4),
            "realized_ai_fee_usd": round(realized_totals["ai_fee_usd"], 4),
            "realized_ai_billable_usd": round(realized_totals["billable_cost_usd"], 4),
            "current_active_mrr_ars": round(total_subscriptions_ars, 2),
            "projected_ai_revenue_ars": total_ai_billable_ars,
            "projected_ai_provider_cost_ars": total_ai_provider_cost_ars,
            "projected_total_revenue_ars": total_revenue_ars,
            "projected_net_profit_ars": estimated_net_profit_ars,
            "projected_net_margin_percent": net_margin_percent,
            "monthly_subscriptions_ars": total_subscriptions_ars,
            "monthly_ai_billable_usd": round(total_ai_billable_usd, 4),
            "monthly_ai_billable_ars": total_ai_billable_ars,
            "monthly_ai_provider_cost_usd": round(total_ai_provider_cost_usd, 4),
            "monthly_ai_provider_cost_ars": total_ai_provider_cost_ars,
            "monthly_ai_fee_gross_profit_usd": round(total_ai_fee_usd, 4),
            "monthly_ai_fee_gross_profit_ars": total_ai_fee_ars,
            "estimated_mp_fee_ars": round(total_mp_fee_ars, 2),
            "estimated_tax_ars": round(total_tax_ars, 2),
            "total_revenue_ars": total_revenue_ars,
            "estimated_net_profit_ars": estimated_net_profit_ars,
            "net_margin_percent": net_margin_percent,
            "healthy_organizations": sum(1 for o in org_matrix if o["profitability"]["status"] == "healthy"),
            "at_risk_organizations": sum(1 for o in org_matrix if o["profitability"]["status"] == "at_risk"),
            "blocked_organizations": sum(1 for o in org_matrix if o["profitability"]["status"] == "blocked"),
        },
        "organizations": org_matrix,
        "latest_statements": latest_statements,
        "alerts": alerts,
    }


@api_router.get("/platform/financial-dashboard/export")
async def platform_financial_dashboard_export(
    organization_id: Optional[str] = None,
    period: Optional[str] = "this_month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    platform_admin: User = Depends(require_platform_admin),
):
    """Export filtered Financial Dashboard matrix as CSV."""
    dashboard_data = await platform_financial_dashboard(
        organization_id=organization_id,
        period=period,
        start_date=start_date,
        end_date=end_date,
        platform_admin=platform_admin,
    )

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "empresa_id", "empresa", "plan", "suscripcion", "licencia", "acceso",
        "mrr_actual_ars", "llamadas_periodo", "tokens_periodo",
        "ia_proveedor_proyectada_usd", "fee_ia_proyectado_usd",
        "ia_facturable_proyectada_usd", "ia_proyectada_ars",
        "margen_proyectado_pct", "liquidaciones_cobradas",
        "planes_realizados_ars", "ia_realizada_ars", "total_realizado_ars",
        "costo_proveedor_realizado_ars", "comision_mp_realizada_ars",
        "impuestos_realizados_ars", "ganancia_realizada_ars",
        "margen_realizado_pct", "email_facturacion",
    ])
    for o in dashboard_data["organizations"]:
        u = o["ai_usage"]
        realized = o.get("realized") or {}
        writer.writerow([_safe_csv_cell(value) for value in [
            o["organization_id"], o["name"], o["plan_name"],
            o["subscription_status"], o["license_status"], o["access_allowed"],
            o["plan_price_ars"] if o.get("mrr_active") else 0,
            u.get("calls", 0), u.get("tokens", 0), u.get("base_cost_usd", 0.0),
            u.get("ai_fee_usd", 0.0), u.get("billable_cost_usd", 0.0),
            u.get("billable_cost_ars", 0.0),
            (o.get("profitability") or {}).get("net_margin_percent", 0),
            realized.get("statements", 0), realized.get("plan_revenue_ars", 0),
            realized.get("ai_revenue_ars", 0), realized.get("total_revenue_ars", 0),
            realized.get("provider_cost_ars", 0), realized.get("mp_fee_ars", 0),
            realized.get("tax_ars", 0), realized.get("net_profit_ars", 0),
            realized.get("net_margin_percent", 0), o.get("billing_email") or "",
        ]])

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="dashboard-financiero-latus.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@api_router.get("/platform/ai-keys")
async def platform_get_ai_keys(
    platform_admin: User = Depends(require_platform_admin),
):
    """Point 8: Centralized AI Credentials Management (Superadmin Only)."""
    keys = await _raw_collection("system_ai_credentials").find({}, {"_id": 0, "api_key_masked": 1, "key_id": 1, "provider": 1, "label": 1, "assigned_organization_ids": 1, "is_active": 1, "created_at": 1}).to_list(100)
    return {"keys": keys}


@api_router.post("/platform/ai-keys")
async def platform_save_ai_key(
    payload: AIKeyCredentialPayload,
    platform_admin: User = Depends(require_platform_admin),
):
    """Point 8: Register or rotate centralized AI Provider Credential."""
    raw_key = payload.api_key.strip()
    masked = f"{raw_key[:6]}...{raw_key[-4:]}" if len(raw_key) > 10 else "••••••••"
    key_id = f"key_{uuid.uuid4().hex[:10]}"
    doc = {
        "key_id": key_id,
        "provider": payload.provider.lower(),
        "api_key_masked": masked,
        "label": payload.label or f"Clave {payload.provider}",
        "assigned_organization_ids": payload.assigned_organization_ids,
        "is_active": payload.is_active,
        "created_at": now_iso(),
        "created_by": platform_admin.user_id,
    }
    await _raw_collection("system_ai_credentials").insert_one(doc)
    return {"key_id": key_id, "status": "created", "masked": masked}


@api_router.delete("/platform/ai-keys/{key_id}")
async def platform_delete_ai_key(
    key_id: str,
    platform_admin: User = Depends(require_platform_admin),
):
    """Point 8: Revoke/Delete AI Provider Credential."""
    result = await _raw_collection("system_ai_credentials").delete_one({"key_id": key_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Credencial no encontrada")
    return {"key_id": key_id, "status": "deleted"}


@api_router.get("/platform/alerts")
async def platform_list_alerts(
    organization_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    platform_admin: User = Depends(require_platform_admin),
):
    """Point 9: Operational Alerts List."""
    from utils.alerts import list_system_alerts
    alerts = await list_system_alerts(db, organization_id=organization_id, status=status, limit=limit)
    return {"alerts": alerts, "count": len(alerts)}


@api_router.post("/platform/alerts/{alert_id}/resolve")
async def platform_resolve_alert(
    alert_id: str,
    platform_admin: User = Depends(require_platform_admin),
):
    """Point 9: Resolve Operational Alert."""
    from utils.alerts import resolve_system_alert
    success = await resolve_system_alert(db, alert_id, user_id=platform_admin.user_id)
    if not success:
        raise HTTPException(404, "Alerta no encontrada o ya resuelta")
    return {"alert_id": alert_id, "status": "resolved"}


@api_router.put("/admin/ai-pricing")
async def admin_ai_pricing_put(item: AIPriceItem,
                               admin: User = Depends(require_platform_admin)):
    from ai import usage as ai_usage
    try:
        result = await ai_usage.save_pricing(db, item.model, item.input_per_million,
                                             item.output_per_million,
                                             user_id=admin.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"models": result, "defaults": ai_usage.DEFAULT_PRICING}


@api_router.post("/admin/ai-pricing/reset")
async def admin_ai_pricing_reset(admin: User = Depends(require_platform_admin)):
    from ai import usage as ai_usage
    result = await ai_usage.reset_pricing(db, user_id=admin.user_id)
    return {"models": result, "defaults": ai_usage.DEFAULT_PRICING}


# ---------------------------------------------------------------------------
# Catalog (products) — Phase 3
# ---------------------------------------------------------------------------


async def require_catalog_writer(user: User = Depends(get_current_user)) -> User:
    perms = await get_role_permissions(user.role)
    if not permission_granted(perms, "catalog_admin"):
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
    user: User = Depends(require_perm("catalog_view")),
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
async def catalog_export_csv(user: User = Depends(require_perm("catalog_view"))):
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
async def catalog_get(product_id: str, user: User = Depends(require_perm("catalog_view"))):
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
async def catalog_categories(user: User = Depends(require_perm("catalog_view"))):
    settings = await get_app_settings()
    configured = settings.get("catalog_categories", [])
    cats = await db.products.distinct("category", {"deleted_at": None,
                                                   "category": {"$ne": None}})
    merged = _normalize_catalog_categories([*configured, *[c for c in cats if c]])
    return {"categories": merged}


@api_router.get("/catalog/stats")
async def catalog_stats(user: User = Depends(require_perm("catalog_view"))):
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
    if not permission_granted(perms, "ai_use") or not permission_granted(perms, "inbox_use"):
        return False
    if permission_granted(perms, "inbox_admin"):
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
async def update_conversation(conv_id: str, payload: ConversationUpdate, user: User = Depends(require_perm("inbox_use"))):
    current = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not current:
        raise HTTPException(status_code=404, detail="Conversation not found")
    perms = await get_role_permissions(user.role)
    is_manager = permission_granted(perms, "inbox_admin")
    if not is_manager and current.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="Sólo podés modificar conversaciones asignadas a vos")
    if not is_manager and ({"assigned_to", "assigned_work_area"} & payload.model_fields_set):
        raise HTTPException(status_code=403, detail="Se requiere administrar la bandeja para reasignar conversaciones")
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

    if update.get("status") == "cerrada" and current.get("channel") == "webchat":
        await send_webchat_whatsapp_summary_internal(db, conv_id)

    doc = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    doc["contact"] = await db.contacts.find_one({"id": doc["contact_id"]}, {"_id": 0})
    return doc

# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@api_router.get("/tasks")
async def list_tasks(
    user: User = Depends(require_perm("crm_view")),
    status: Optional[str] = None,
    assigned_to: Optional[str] = None,
):
    q = {}
    if status:
        q["status"] = status
    perms = await get_role_permissions(user.role)
    can_manage_all = permission_granted(perms, "crm_admin")
    if not can_manage_all:
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
async def create_task(payload: TaskCreate, user: User = Depends(require_perm("crm_use"))):
    data = payload.model_dump()
    if not data.get("assigned_to"):
        data["assigned_to"] = user.user_id
    if data.get("status"):
        data["status"] = await validate_task_status(data["status"])
    task = Task(**data)
    await db.tasks.insert_one(task.model_dump())
    return task


@api_router.patch("/tasks/{task_id}", response_model=Task)
async def update_task(task_id: str, payload: TaskUpdate, user: User = Depends(require_perm("crm_use"))):
    current = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    if not current:
        raise HTTPException(status_code=404, detail="Task not found")
    permissions = await get_role_permissions(user.role)
    if not permission_granted(permissions, "crm_admin") and current.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="No podés modificar una tarea asignada a otra persona")
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "status" in update:
        update["status"] = await validate_task_status(update["status"])
    await db.tasks.update_one({"id": task_id}, {"$set": update})
    doc = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Task not found")
    return Task(**doc)


@api_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: User = Depends(require_perm("crm_use"))):
    current = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    if not current:
        raise HTTPException(status_code=404, detail="Task not found")
    permissions = await get_role_permissions(user.role)
    if not permission_granted(permissions, "crm_admin") and current.get("assigned_to") != user.user_id:
        raise HTTPException(status_code=403, detail="No podés eliminar una tarea asignada a otra persona")
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
    permissions = user.permissions or DEFAULT_ROLE_PERMISSIONS.get(_normalize_role(user.role), [])
    return permission_granted(permissions, "calendar_admin")


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
    member = await _raw_collection("memberships").find_one({
        "organization_id": get_organization_id(), "user_id": target, "status": "active",
    })
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
    user_doc = await db.users.find_one({"user_id": user_id, "active": {"$ne": False}}, {"_id": 0})
    membership = await _raw_collection("memberships").find_one({
        "organization_id": get_organization_id(), "user_id": user_id, "status": "active",
    }, {"_id": 0})
    if not user_doc or not membership:
        raise HTTPException(status_code=404, detail="Usuario no encontrado o inactivo")
    try:
        availability = normalize_person_availability(
            membership.get("calendar_settings", user_doc.get("calendar_settings")), settings
        )
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
    await _raw_collection("memberships").update_one(
        {"organization_id": get_organization_id(), "user_id": user_id},
        {"$set": {"calendar_settings": availability, "updated_at": now_iso()}},
    )
    return {
        "user_id": user_id,
        "name": user_doc.get("name") or user_doc.get("email") or "Usuario",
        **availability,
    }


@api_router.get("/calendar/scheduling-config")
async def get_calendar_scheduling_config(user: User = Depends(require_perm("calendar_view"))):
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
async def get_calendar_availability(user: User = Depends(require_perm("calendar_view"))):
    settings = await _effective_bot_settings()
    user_doc, availability = await _calendar_availability_for(user.user_id, settings)
    return {"user_id": user.user_id, "name": user_doc.get("name"), **availability}


@api_router.patch("/calendar/availability")
async def patch_calendar_availability(
    payload: CalendarAvailabilityUpdate,
    user: User = Depends(require_perm("calendar_use")),
):
    return await _save_calendar_availability(user.user_id, payload)


@api_router.get("/calendar/team-availability")
async def get_team_calendar_availability(user: User = Depends(require_perm("calendar_admin"))):
    if not _is_calendar_manager(user):
        raise HTTPException(status_code=403, detail="Solo administradores y supervisores pueden ver la disponibilidad del equipo")
    settings = await _effective_bot_settings()
    members = sorted(await _team_user_docs(user.organization_id), key=lambda item: item.get("name") or "")
    result = []
    for member in members:
        from utils.scheduling import normalize_person_availability
        availability = normalize_person_availability(member.get("calendar_settings"), settings)
        result.append({
            "user_id": member.get("user_id"),
            "name": member.get("name") or member.get("email") or "Usuario",
            "role": _normalize_role(member.get("membership_role")),
            **availability,
        })
    return result


@api_router.patch("/calendar/team-availability/{user_id}")
async def patch_team_calendar_availability(
    user_id: str,
    payload: CalendarAvailabilityUpdate,
    user: User = Depends(require_perm("calendar_admin")),
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
    user: User = Depends(require_perm("calendar_view")),
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
            "role": _normalize_role(member.get("membership_role", member.get("role"))),
        }
        for member in await _team_user_docs(user.organization_id, include_inactive=True)
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
async def create_appointment(payload: AppointmentCreate, user: User = Depends(require_perm("calendar_use"))):
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
async def update_appointment(appt_id: str, payload: AppointmentUpdate, user: User = Depends(require_perm("calendar_use"))):
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
    user: User = Depends(require_perm("calendar_use")),
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
async def delete_appointment(appt_id: str, user: User = Depends(require_perm("calendar_use"))):
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
    user: User = Depends(require_perm("crm_view")),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    compare_start_date: Optional[str] = None,
    compare_end_date: Optional[str] = None,
):
    leads = await db.leads.find({}, {"_id": 0}).to_list(2000)
    convs = await db.conversations.find({}, {"_id": 0}).to_list(2000)
    tasks = await db.tasks.find({}, {"_id": 0}).to_list(2000)
    contacts = {c["id"]: c for c in await db.contacts.find({}, {"_id": 0}).to_list(2000)}

    permissions = await get_role_permissions(user.role)
    is_admin_or_supervisor = permission_granted(permissions, "crm_admin")
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
    if not await _can_use_bot_for_conv(conv, user):
        raise HTTPException(status_code=403, detail="Sin permisos")
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
    if not await _can_use_bot_for_conv(conv, user):
        raise HTTPException(status_code=403, detail="Sin permisos")
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
async def reseed(admin: User = Depends(require_perm("settings_admin"))):
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
            "ai_billing_statements", "bot_events", "notifications",
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


async def _ensure_default_organization() -> str:
    """Idempotently attach legacy data and users to the first organization."""
    organizations = _raw_collection("organizations")
    memberships = _raw_collection("memberships")
    organization = await _get_or_create_legacy_default_organization()
    organization_id = organization["organization_id"]
    await organizations.update_one(
        {"organization_id": organization_id},
        {"$set": {
            "plan_code": organization.get("plan_code") or "base",
            "subscription_status": organization.get("subscription_status") or "not_configured",
            "license_status": organization.get("license_status") or "not_configured",
        }},
    )

    users = _raw_collection("users")
    async for user_doc in users.find({"deleted_at": None}, {"_id": 0}):
        user_id = user_doc.get("user_id")
        if not user_id:
            continue
        existing = await _ensure_legacy_membership(organization_id, user_doc)
        user_organization_id = existing.get("organization_id") or organization_id
        await users.update_one(
            {"user_id": user_id, "default_organization_id": {"$exists": False}},
            {"$set": {"default_organization_id": user_organization_id}},
        )

    sessions = _raw_collection("user_sessions")
    async for session in sessions.find({"organization_id": {"$exists": False}}, {"_id": 0}):
        session_membership = await memberships.find_one({
            "user_id": session.get("user_id"), "status": "active",
        }, {"_id": 0})
        await sessions.update_one(
            {"session_token": session.get("session_token")},
            {"$set": {"organization_id": (
                session_membership.get("organization_id") if session_membership else organization_id
            )}},
        )

    for collection_name in TENANT_SCOPED_COLLECTIONS - COMPOSITE_ID_COLLECTIONS:
        await _raw_collection(collection_name).update_many(
            {"organization_id": {"$exists": False}},
            {"$set": {"organization_id": organization_id}},
        )

    # Preserve legacy logical IDs while making Mongo's physical _id unique per tenant.
    for collection_name in COMPOSITE_ID_COLLECTIONS:
        collection = _raw_collection(collection_name)
        legacy_docs = await collection.find({
            "$or": [
                {"organization_id": {"$exists": False}},
                {"organization_id": organization_id,
                 "_id": {"$not": {"$regex": f"^{organization_id}:"}}},
            ]
        }).to_list(1000)
        for legacy in legacy_docs:
            if legacy.get("multiempresa_shadowed_at"):
                continue
            old_id = str(legacy.get("_id", "default"))
            public_id = old_id.split(":", 1)[-1] if ":" in old_id else old_id
            new_id_value = f"{organization_id}:{public_id}"
            replacement = {**legacy, "_id": new_id_value, "organization_id": organization_id}
            replacement.pop("multiempresa_shadowed_at", None)
            await collection.replace_one({"_id": new_id_value}, replacement, upsert=True)
            if old_id != new_id_value:
                # Keep the legacy document as a rollback shadow. The old
                # single-tenant release still reads `_id=default` while the
                # new release reads the organization-prefixed copy.
                await collection.update_one(
                    {"_id": legacy["_id"]},
                    {"$set": {
                        "multiempresa_shadowed_at": now_iso(),
                        "multiempresa_shadow_id": new_id_value,
                    }},
                )
    return organization_id


async def _run_legacy_multiempresa_migration() -> str:
    """Run the legacy migration synchronously and persist its recovery state."""
    migrations = _raw_collection("system_migrations")
    previous = await migrations.find_one(
        {"_id": LEGACY_MULTIEMPRESA_MIGRATION_ID}, {"_id": 0}
    )
    if previous and previous.get("status") == "completed" and previous.get("organization_id"):
        return previous["organization_id"]

    confirmed = (os.environ.get("LATUS_CONFIRM_PRODUCTION_MIGRATION") or "").strip().lower()
    if _environment_name() == "production" and confirmed not in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "[MIGRACIÓN BLOQUEADA] Realizá un backup de MongoDB y definí "
            "LATUS_CONFIRM_PRODUCTION_MIGRATION=true para autorizar la primera migración multiempresa"
        )

    run_id = new_id("migration")
    started_at = now_iso()
    await migrations.update_one(
        {"_id": LEGACY_MULTIEMPRESA_MIGRATION_ID},
        {"$set": {
            "status": "running",
            "run_id": run_id,
            "started_at": started_at,
            "updated_at": started_at,
            "last_error": None,
        }, "$setOnInsert": {"created_at": started_at}},
        upsert=True,
    )
    try:
        organization_id = await _ensure_default_organization()
    except Exception as exc:
        await migrations.update_one(
            {"_id": LEGACY_MULTIEMPRESA_MIGRATION_ID},
            {"$set": {
                "status": "failed",
                "run_id": run_id,
                "failed_at": now_iso(),
                "updated_at": now_iso(),
                "last_error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }},
            upsert=True,
        )
        raise
    completed_at = now_iso()
    await migrations.update_one(
        {"_id": LEGACY_MULTIEMPRESA_MIGRATION_ID},
        {"$set": {
            "status": "completed",
            "run_id": run_id,
            "organization_id": organization_id,
            "completed_at": completed_at,
            "updated_at": completed_at,
            "last_error": None,
            "rollback_shadows_preserved": True,
        }},
        upsert=True,
    )
    return organization_id


async def _migrate_legacy_ai_credentials_to_platform(organization_id: str) -> None:
    """Copy legacy tenant AI secrets into platform-owned storage once.

    Source documents are intentionally preserved for rollback. ``$setOnInsert``
    prevents a later restart or another tenant from overwriting credentials
    already managed by the platform owner.
    """
    platform_secrets = _raw_collection("platform_secrets")
    existing = await platform_secrets.find_one({"_id": "ai_provider"}, {"_id": 1})
    if not existing:
        provider_doc = await db.app_secrets.find_one({"_id": "ai_provider"}) or {}
        bot_doc = await db.app_secrets.find_one({"_id": "bot_provider"}) or {}
        migrated = {
            key: value for key, value in provider_doc.items()
            if key not in {
                "_id", "organization_id", "multiempresa_shadowed_at",
                "multiempresa_shadow_id",
            }
        }
        # The bot-specific key had precedence in the previous implementation.
        for key, value in bot_doc.items():
            if key.startswith("api_key_") and key.endswith("_enc"):
                migrated[key] = value
        if migrated:
            migrated.update({
                "migrated_from_organization_id": organization_id,
                "migrated_at": now_iso(),
            })
            await platform_secrets.update_one(
                {"_id": "ai_provider"}, {"$setOnInsert": migrated}, upsert=True
            )

    reporting_exists = await platform_secrets.find_one(
        {"_id": "ai_usage_reporting"}, {"_id": 1}
    )
    if not reporting_exists:
        reporting_doc = await db.app_secrets.find_one({"_id": "ai_usage_reporting"}) or {}
        migrated_reporting = {
            key: value for key, value in reporting_doc.items()
            if key not in {
                "_id", "organization_id", "multiempresa_shadowed_at",
                "multiempresa_shadow_id",
            }
        }
        if migrated_reporting:
            migrated_reporting.update({
                "migrated_from_organization_id": organization_id,
                "migrated_at": now_iso(),
            })
            await platform_secrets.update_one(
                {"_id": "ai_usage_reporting"},
                {"$setOnInsert": migrated_reporting},
                upsert=True,
            )


async def _bootstrap_tenant_data() -> None:
    """Run tenant migration and existing seeds in a safe, ordered context."""
    organization_id = await _run_legacy_multiempresa_migration()
    token = set_organization_id(organization_id)
    try:
        await _migrate_legacy_ai_credentials_to_platform(organization_id)
        await _seed_roles()
        await _seed(force=False)
        # Demo seed can create global user identities; attach those too.
        await _ensure_default_organization()
        await backfill_notifications()
        await _ensure_indexes()
        await _migrate_promote_first_google_admin()
    finally:
        reset_organization_id(token)


@app.on_event("startup")
async def on_startup():
    """Validate and migrate synchronously before accepting traffic.

    Environment guardrails or migration failures intentionally stop an unsafe
    deployment instead of exposing a partially migrated application.
    """
    validate_environment_guardrails()

    # Finish migration and indexes before accepting sessions. Any failure is
    # intentional and keeps the unsafe release out of service.
    await _bootstrap_tenant_data()

    # Scheduler is sync and doesn't touch DB — fine to start in foreground.
    try:
        _start_scheduler()
    except Exception:  # pragma: no cover
        logger.exception("startup step '_start_scheduler' failed (continuing)")
    logger.info("Latus CRM started (multiempresa migration ready)")


# ---------------------------------------------------------------------------
# Liveness / readiness — no auth, no DB calls in the hot path
# ---------------------------------------------------------------------------

APP_VERSION = os.environ.get("APP_VERSION", "dev")


@api_router.get("/health")
async def health():
    """Cheap liveness probe. Always 200; no auth; no DB query."""
    return {
        "ok": True,
        "version": APP_VERSION,
        "app": "latus-crm",
        "environment": _environment_name(),
    }


@api_router.get("/health/ready")
async def health_ready():
    """Readiness probe. Pings Mongo with a short timeout; never raises."""
    db_ok = True
    db_error = None
    migration_status = "not_required" if _environment_name() == "development" else "unknown"
    try:
        # If MONGO_URL is missing, ``db.command`` will trigger _DBProxy init
        # which raises a clean RuntimeError — captured below.
        await asyncio.wait_for(db.command("ping"), timeout=3.0)
        if _environment_name() != "development":
            migration = await _raw_collection("system_migrations").find_one(
                {"_id": LEGACY_MULTIEMPRESA_MIGRATION_ID}, {"_id": 0, "status": 1}
            )
            migration_status = (migration or {}).get("status") or "missing"
            if migration_status != "completed":
                db_ok = False
                db_error = f"migración multiempresa: {migration_status}"
    except Exception as e:  # pragma: no cover - exercised in deploy
        db_ok = False
        db_error = f"{type(e).__name__}: {str(e)[:160]}"
    return {"ok": db_ok, "db": "up" if db_ok else f"down ({db_error})",
            "version": APP_VERSION, "migration": migration_status}


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
        if get_organization_id():
            await _raw_collection("memberships").update_one(
                {"organization_id": get_organization_id(), "user_id": chosen["user_id"]},
                {"$set": {"role": "admin", "updated_at": now_iso()}},
            )
        logger.warning(
            "Promoted %s (%s) to admin via auto-migration (no real Google admin existed)",
            chosen.get("email"), chosen.get("user_id"),
        )
    except Exception:
        logger.exception("_migrate_promote_first_google_admin failed")


async def _ensure_indexes() -> None:
    """Idempotently create indexes needed by integrations and tenancy."""
    try:
        # Replace legacy global uniqueness with tenant-local uniqueness.
        for collection_name, index_name in (
            ("messages", "ux_messages_external_id"),
            ("messages", "ux_org_messages_external_id"),
            ("bot_events", "ux_bot_events_trigger"),
            ("bot_events", "ux_org_bot_events_trigger"),
            ("products", "ux_products_sku"),
            ("products", "ux_org_products_sku"),
        ):
            try:
                await _raw_collection(collection_name).drop_index(index_name)
            except Exception:
                pass
        await _raw_collection("memberships").create_index(
            [("organization_id", 1), ("user_id", 1)], unique=True,
            name="ux_membership_org_user",
        )
        await _raw_collection("memberships").create_index(
            [("user_id", 1), ("status", 1)], name="ix_membership_user_status",
        )
        await _raw_collection("organizations").create_index(
            "organization_id", unique=True, name="ux_organizations_id",
        )
        await _raw_collection("organizations").create_index(
            "webchat_public_key", unique=True, sparse=True,
            name="ux_organizations_webchat_public_key",
        )
        await _raw_collection("conversations").create_index(
            "webchat_session_token", unique=True, sparse=True,
            name="ux_conversations_webchat_session_token",
        )
        await _raw_collection("billing_requests").create_index(
            [("organization_id", 1), ("created_at", -1)],
            name="ix_billing_requests_org_created",
        )
        await _raw_collection("billing_events").create_index(
            [("organization_id", 1), ("created_at", -1)],
            name="ix_billing_events_org_created",
        )
        await _raw_collection("billing_events").create_index(
            "provider_event_id", unique=True, sparse=True,
            name="ux_billing_provider_event",
        )
        await _raw_collection("ai_billing_statements").create_index(
            "settlement_key", unique=True, name="ux_ai_statement_settlement_key",
        )
        await _raw_collection("ai_billing_statements").create_index(
            [("organization_id", 1), ("created_at", -1)],
            name="ix_ai_statement_org_created",
        )
        await _raw_collection("ai_billing_statements").create_index(
            "provider_payment_id", unique=True, sparse=True,
            name="ux_ai_statement_provider_payment",
        )
        await _raw_collection("organizations").create_index(
            "provider_preapproval_id", unique=True, sparse=True,
            name="ux_organizations_provider_preapproval",
        )
        await _raw_collection("user_sessions").create_index(
            [("user_id", 1), ("organization_id", 1)], name="ix_sessions_user_org",
        )
        await _raw_collection("whatsapp_routes").create_index(
            "phone_number_id", unique=True, name="ux_whatsapp_route_phone",
        )
        await db.messages.create_index(
            [("organization_id", 1), ("external_message_id", 1)],
            unique=True, name="ux_org_messages_external_id",
            partialFilterExpression={"external_message_id": {"$type": "string"}},
        )
        await db.conversations.create_index(
            [("organization_id", 1), ("channel", 1), ("channel_external_id", 1)],
            sparse=True, name="ix_org_conversations_channel",
        )
        await db.contacts.create_index(
            [("organization_id", 1), ("whatsapp_id", 1)],
            sparse=True, name="ix_org_contacts_whatsapp_id",
        )
        await db.bot_events.create_index(
            [("organization_id", 1), ("triggered_by_message_id", 1)],
            unique=True, name="ux_org_bot_events_trigger",
            partialFilterExpression={"triggered_by_message_id": {"$type": "string"}},
        )
        await db.ai_usage_logs.create_index(
            [("organization_id", 1), ("created_at", -1), ("status", 1)],
            name="ix_org_ai_usage_dt_status")
        await db.ai_usage_logs.create_index(
            [("organization_id", 1), ("model", 1)], name="ix_org_ai_usage_model")
        await db.ai_usage_logs.create_index(
            [("organization_id", 1), ("conversation_id", 1)],
            sparse=True, name="ix_org_ai_usage_conv")
        await db.products.create_index(
            [("organization_id", 1), ("sku", 1)], unique=True,
            name="ux_org_products_sku",
            partialFilterExpression={"sku": {"$type": "string"}})
        await db.products.create_index([("organization_id", 1), ("name", 1)], name="ix_org_products_name")
        await db.products.create_index([("organization_id", 1), ("category", 1)], name="ix_org_products_category")
        await db.products.create_index([("organization_id", 1), ("tags", 1)], name="ix_org_products_tags")
        await db.password_reset_tokens.create_index("token_hash", unique=True, name="ux_password_reset_token_hash")
        await db.password_reset_tokens.create_index("expires_at", name="ix_password_reset_expires")
        await db.appointments.create_index(
            [("organization_id", 1), ("assigned_to", 1), ("start_time", 1)],
            name="ix_org_appointments_assignee_start",
        )
        await db.appointments.create_index(
            [("organization_id", 1), ("start_time", 1)], name="ix_org_appointments_start")
        await db.appointments.create_index(
            [("organization_id", 1), ("reminder_status", 1), ("reminder_due_at", 1)],
            name="ix_org_appointments_reminder_due",
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
        leader_memberships = await _raw_collection("memberships").find({
            "organization_id": get_organization_id(),
            "role": {"$in": ["admin", "supervisor"]},
            "status": "active",
        }, {"_id": 0, "user_id": 1}).to_list(100)
        leader_ids = [membership["user_id"] for membership in leader_memberships]
        leaders = await db.users.find({"user_id": {"$in": leader_ids}}, {"_id": 0}).to_list(100)
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
        organizations = await _raw_collection("organizations").find(
            {"status": "active"}, {"organization_id": 1, "_id": 0}
        ).to_list(10000)
        for organization in organizations:
            organization_id = organization.get("organization_id")
            if not organization_id:
                continue
            token = set_organization_id(organization_id)
            try:
                try:
                    await scan_lead_no_response()
                except Exception:  # pragma: no cover - log only
                    logger.exception("scheduled scan_lead_no_response failed org=%s", organization_id)
                try:
                    await close_inactive_conversations(db)
                except Exception:
                    logger.exception("scheduled close_inactive_conversations failed org=%s", organization_id)
                try:
                    await check_and_send_scheduled_reports()
                except Exception:  # pragma: no cover - log only
                    logger.exception("scheduled check_and_send_scheduled_reports failed org=%s", organization_id)
                try:
                    await send_due_appointment_reminders()
                except Exception:  # pragma: no cover - log only
                    logger.exception("scheduled send_due_appointment_reminders failed org=%s", organization_id)
            finally:
                reset_organization_id(token)
        try:
            await process_due_ai_settlements()
        except Exception:  # pragma: no cover - log only
            logger.exception("scheduled AI billing settlement scan failed")

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
    "/api/webhooks/mercadopago",  # external, HMAC-authenticated
}


@app.middleware("http")
async def block_viewer_on_writes(request: Request, call_next):
    method = request.method
    path = request.url.path
    is_exempt = path in _WRITE_EXEMPT_PATHS or (
        path.startswith("/api/organizations/") and path.endswith("/switch")
    )
    if method in _WRITE_METHODS and path.startswith("/api/") and not is_exempt:
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
                        membership = await _raw_collection("memberships").find_one({
                            "organization_id": session.get("organization_id"),
                            "user_id": session["user_id"],
                            "status": "active",
                        }, {"_id": 0})
                        role = _normalize_role(
                            membership.get("role") if membership else user_doc.get("role")
                        )
                        perms = await get_role_permissions(role)
                        if not any(
                            permission_granted(perms, f"{module}_use")
                            for module in PERMISSION_MODULES
                        ):
                            from fastapi.responses import JSONResponse
                            return JSONResponse({"detail": "Sin permisos"}, status_code=403)
            except Exception:
                pass  # fall through to normal handler
    return await call_next(request)


development_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173"
]
default_staging_origins = [
    "https://latus-crm-staging.vercel.app",
    "https://latus-crm.vercel.app",
    "https://latus-crm-staging.up.railway.app",
]
configured_origins = _split_cors_origins()
origins = list(dict.fromkeys(
    development_origins
    + default_staging_origins
    + configured_origins
))

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def organization_context_middleware(request: Request, call_next):
    """Select the session's tenant before any route or permission middleware."""
    context_token = set_organization_id(None)
    try:
        session_token = _request_session_token(request)
        organization_id = None
        if session_token:
            session = await _raw_collection("user_sessions").find_one(
                {"session_token": session_token}, {"_id": 0}
            )
            if session:
                user_doc = await _raw_collection("users").find_one(
                    {"user_id": session.get("user_id")}, {"_id": 0}
                )
                organization_id = await _resolve_session_organization(session, user_doc)
        
        is_public_webchat = request.url.path.startswith("/api/public/webchat")
        if not organization_id and not is_public_webchat and (
            request.url.path.startswith("/api/public/")
            or request.url.path.startswith("/api/webhook/")
            or request.url.path.startswith("/public/")
        ):
            organization_id = "default"

        if organization_id:
            set_organization_id(organization_id)
            request.state.organization_id = organization_id

        return await call_next(request)
    finally:
        reset_organization_id(context_token)


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
