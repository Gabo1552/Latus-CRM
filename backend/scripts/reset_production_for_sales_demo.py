"""Reinicia los datos comerciales y crea las empresas de venta y demostración.

El comando es deliberadamente conservador:

* sin argumentos muestra una vista previa y no modifica datos;
* sólo se ejecuta en ``ENVIRONMENT=production``;
* requiere una frase de confirmación exacta;
* aborta si Mongo contiene una colección desconocida;
* conserva migraciones, credenciales globales de IA y configuración económica.

Uso en el servicio backend de Railway::

    python scripts/reset_production_for_sales_demo.py
    python scripts/reset_production_for_sales_demo.py --execute \
        --confirm RESET-LATUS-PRODUCTION
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from dealership_demo_data import build_dealership_demo_dataset  # noqa: E402


load_dotenv(ROOT / ".env")

CONFIRMATION = "RESET-LATUS-PRODUCTION"
DEFAULT_ADMIN_EMAIL = "admin@latus.test"
LATUS_ORGANIZATION_ID = "org_latus_internal"
DEMO_ORGANIZATION_ID = "org_demo_autonorte"

# Estos documentos contienen configuración global y no pertenecen a un cliente.
PRESERVED_COLLECTIONS = frozenset({
    "system_migrations",
    "system_ai_credentials",
    "platform_secrets",
    "pricing_config",
})

# Toda colección conocida que contiene datos operativos, usuarios o facturación.
# Si aparece una colección nueva, el script aborta en vez de adivinar su destino.
RESET_COLLECTIONS = frozenset({
    "ai_billing_statements",
    "ai_usage_logs",
    "app_secrets",
    "appointments",
    "automation_leases",
    "billing_events",
    "billing_requests",
    "bot_events",
    "bot_settings",
    "contacts",
    "conversations",
    "inventory_movements",
    "leads",
    "memberships",
    "messages",
    "notes",
    "notifications",
    "organizations",
    "password_reset_tokens",
    "products",
    "roles",
    "sales",
    "security_rate_limits",
    "settings",
    "system_alerts",
    "tags",
    "tasks",
    "user_sessions",
    "users",
    "wa_status",
    "whatsapp_events",
    "whatsapp_routes",
    "work_area_members",
    "work_areas",
})

TENANT_DATASET_COLLECTIONS = (
    "work_areas",
    "tags",
    "products",
    "contacts",
    "leads",
    "conversations",
    "messages",
    "notes",
    "tasks",
    "appointments",
    "sales",
    "inventory_movements",
    "bot_events",
    "ai_usage_logs",
)

ROLE_PERMISSIONS = {
    "admin": [
        "crm_admin", "inbox_admin", "calendar_admin", "catalog_admin",
        "ai_admin", "users_admin", "whatsapp_admin", "settings_admin",
    ],
    "supervisor": [
        "crm_admin", "inbox_admin", "calendar_admin", "catalog_admin",
        "ai_use", "users_view", "whatsapp_view", "settings_view",
    ],
    "agent": ["crm_use", "inbox_use", "calendar_use", "catalog_view", "ai_use"],
    "viewer": ["crm_view", "inbox_view", "calendar_view", "catalog_view", "ai_view"],
}

ROLE_NAMES = {
    "admin": "Administrador",
    "supervisor": "Supervisor",
    "agent": "Agente",
    "viewer": "Sólo lectura",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _organization_document(*, organization_id: str, name: str, slug: str,
                           billing_email: str, is_demo: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "organization_id": organization_id,
        "name": name,
        "slug": slug,
        "status": "active",
        "plan_code": "scale",
        "subscription_status": "active",
        "license_status": "active",
        "trial_started_at": None,
        "trial_ends_at": None,
        "current_period_end": (now + timedelta(days=3650)).isoformat(),
        "grace_ends_at": None,
        "billing_email": billing_email,
        "billing_cycle": "monthly",
        "billing_manual_override": True,
        "ai_fee_percent": None,
        "ai_variable_billing": {
            "state": "simulation" if is_demo else "disabled",
            "billing_start_date": None,
            "fx_buffer_percent": None,
            "ai_fee_percent": None,
            "min_net_margin_percent": None,
            "min_ai_margin_percent": None,
            "profitability_enforcement": None,
        },
        "internal_notes": (
            "Empresa demostrativa: concesionaria argentina. No facturar."
            if is_demo else
            "Empresa interna de Latus para administración y venta de Latus CRM."
        ),
        "is_demo": is_demo,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _membership(*, organization_id: str, user: dict[str, Any], role: str,
                work_areas: list[str] | None = None) -> dict[str, Any]:
    return {
        "_id": f"membership:{organization_id}:{user['user_id']}",
        "organization_id": organization_id,
        "user_id": user["user_id"],
        "role": role,
        "status": "active",
        "work_areas": list(work_areas or []),
        "calendar_settings": deepcopy(user.get("calendar_settings")),
        "display_name": user.get("name") or user.get("email"),
        "created_at": user.get("created_at") or _now_iso(),
    }


def _tenant_documents(documents: list[dict[str, Any]], organization_id: str) -> list[dict[str, Any]]:
    return [{**deepcopy(document), "organization_id": organization_id} for document in documents]


def build_seed_documents(admin_doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Construye el estado final sin acceder a Mongo; facilita pruebas exhaustivas."""
    if not admin_doc.get("user_id") or not admin_doc.get("password_hash"):
        raise ValueError("El administrador existente debe tener user_id y password_hash")

    now = _now_iso()
    admin = deepcopy(admin_doc)
    admin.pop("_id", None)
    admin.update({
        "email": DEFAULT_ADMIN_EMAIL,
        "name": admin.get("name") or "Administrador Latus",
        "role": "admin",
        "active": True,
        "auth_provider": "local" if admin.get("auth_provider") not in {"local", "both"} else admin["auth_provider"],
        "is_demo": False,
        "default_organization_id": LATUS_ORGANIZATION_ID,
        "work_areas": [],
        "updated_at": now,
        "deleted_at": None,
    })

    latus = _organization_document(
        organization_id=LATUS_ORGANIZATION_ID,
        name="Latus CRM",
        slug="latus",
        billing_email=DEFAULT_ADMIN_EMAIL,
        is_demo=False,
    )
    dealership = _organization_document(
        organization_id=DEMO_ORGANIZATION_ID,
        name="AutoNorte Concesionaria",
        slug="autonorte-demo",
        billing_email="demo@autonorte.com.ar",
        is_demo=True,
    )
    dataset = build_dealership_demo_dataset()

    demo_users: list[dict[str, Any]] = []
    for source in dataset["users"]:
        user = deepcopy(source)
        user.update({
            "password_hash": None,
            "login_disabled": True,
            "default_organization_id": DEMO_ORGANIZATION_ID,
            "deleted_at": None,
        })
        demo_users.append(user)

    memberships = [
        _membership(organization_id=LATUS_ORGANIZATION_ID, user=admin, role="admin"),
        _membership(organization_id=DEMO_ORGANIZATION_ID, user=admin, role="admin"),
    ]
    memberships.extend(
        _membership(
            organization_id=DEMO_ORGANIZATION_ID,
            user=user,
            role=user["role"],
            work_areas=user.get("work_areas"),
        )
        for user in demo_users
    )

    roles: list[dict[str, Any]] = []
    for organization_id in (LATUS_ORGANIZATION_ID, DEMO_ORGANIZATION_ID):
        roles.extend({
            "role_id": role_id,
            "name": ROLE_NAMES[role_id],
            "permissions": permissions,
            "is_default": True,
            "organization_id": organization_id,
        } for role_id, permissions in ROLE_PERMISSIONS.items())

    result: dict[str, list[dict[str, Any]]] = {
        "users": [admin, *demo_users],
        "organizations": [latus, dealership],
        "memberships": memberships,
        "roles": roles,
    }
    for collection_name in TENANT_DATASET_COLLECTIONS:
        result[collection_name] = _tenant_documents(
            dataset[collection_name], DEMO_ORGANIZATION_ID,
        )

    bot_settings = deepcopy(dataset["bot_settings"])
    bot_settings["_id"] = f"{DEMO_ORGANIZATION_ID}:default"
    bot_settings["organization_id"] = DEMO_ORGANIZATION_ID
    result["bot_settings"] = [bot_settings]

    app_settings = deepcopy(dataset["app_settings"])
    app_settings["organization_id"] = DEMO_ORGANIZATION_ID
    result["settings"] = [
        app_settings,
        {
            "key": "seeded",
            "scenario": "autonorte_concesionaria_argentina_v1",
            "at": now,
            "is_demo": True,
            "organization_id": DEMO_ORGANIZATION_ID,
        },
    ]
    return result


async def _collection_counts(db, names: list[str]) -> dict[str, int]:
    return {name: await db[name].count_documents({}) for name in names}


async def preview(db, admin_email: str) -> tuple[dict[str, Any], dict[str, Any]]:
    actual = set(await db.list_collection_names())
    unknown = sorted(actual - RESET_COLLECTIONS - PRESERVED_COLLECTIONS)
    admin = await db.users.find_one({"email": admin_email.lower()})
    reset_existing = sorted(actual & RESET_COLLECTIONS)
    preserved_existing = sorted(actual & PRESERVED_COLLECTIONS)
    report = {
        "mode": "preview",
        "database": db.name,
        "admin_found": bool(admin),
        "admin_has_password": bool(admin and admin.get("password_hash")),
        "will_delete": await _collection_counts(db, reset_existing),
        "will_preserve": await _collection_counts(db, preserved_existing),
        "unknown_collections": unknown,
        "will_create": {
            "organizations": ["Latus CRM", "AutoNorte Concesionaria"],
            "dealership_scenario": "autonorte_concesionaria_argentina_v1",
        },
    }
    return report, admin or {}


async def execute_reset(db, admin: dict[str, Any]) -> dict[str, Any]:
    documents = build_seed_documents(admin)

    deleted: dict[str, int] = {}
    for collection_name in sorted(RESET_COLLECTIONS):
        result = await db[collection_name].delete_many({})
        deleted[collection_name] = result.deleted_count

    inserted: dict[str, int] = {}
    for collection_name, rows in documents.items():
        if not rows:
            continue
        await db[collection_name].insert_many(deepcopy(rows), ordered=True)
        inserted[collection_name] = len(rows)

    return {
        "mode": "executed",
        "database": db.name,
        "deleted": deleted,
        "inserted": inserted,
        "admin_email": DEFAULT_ADMIN_EMAIL,
        "default_organization_id": LATUS_ORGANIZATION_ID,
        "demo_organization_id": DEMO_ORGANIZATION_ID,
        "sessions_revoked": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reinicia producción para la demo comercial de Latus CRM")
    parser.add_argument("--execute", action="store_true", help="Ejecuta el borrado y la nueva carga")
    parser.add_argument("--confirm", default="", help="Frase exacta requerida para ejecutar")
    parser.add_argument("--admin-email", default=DEFAULT_ADMIN_EMAIL)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    mongo_url = (os.environ.get("MONGO_URL") or "").strip()
    db_name = (os.environ.get("DB_NAME") or "").strip()
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL y DB_NAME deben estar configurados")

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=10_000)
    try:
        db = client[db_name]
        report, admin = await preview(db, args.admin_email)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

        if report["unknown_collections"]:
            raise SystemExit("ABORTADO: hay colecciones desconocidas; revisarlas antes de continuar")
        if not report["admin_found"] or not report["admin_has_password"]:
            raise SystemExit("ABORTADO: no se encontró el administrador local con contraseña")
        if not args.execute:
            print("VISTA PREVIA: no se modificó ningún dato")
            return
        if (os.environ.get("ENVIRONMENT") or "").strip().lower() != "production":
            raise SystemExit("ABORTADO: la ejecución sólo está habilitada con ENVIRONMENT=production")
        if args.confirm != CONFIRMATION:
            raise SystemExit(f"ABORTADO: use --confirm {CONFIRMATION}")

        result = await execute_reset(db, admin)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
