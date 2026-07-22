"""Operational Alerts Engine for Latus CRM (Point 9).

Persists system alerts for admin review and real-time operational notifications.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

ALERT_TYPES = (
    "fx_rate_expired",
    "unknown_model_price",
    "insufficient_margin",
    "payment_rejected",
    "mp_update_error",
    "mp_restore_error",
    "tenant_quota_warning",
    "abnormal_usage",
    "invalid_provider_key",
)

AlertType = Literal[
    "fx_rate_expired",
    "unknown_model_price",
    "insufficient_margin",
    "payment_rejected",
    "mp_update_error",
    "mp_restore_error",
    "tenant_quota_warning",
    "abnormal_usage",
    "invalid_provider_key",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_system_alert(
    db: Any,
    *,
    alert_type: AlertType,
    title: str,
    message: str,
    organization_id: str | None = None,
    severity: Literal["info", "warning", "error", "critical"] = "warning",
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Create and persist an operational alert."""
    alert_doc = {
        "alert_id": f"alt_{uuid.uuid4().hex[:12]}",
        "alert_type": alert_type,
        "organization_id": organization_id,
        "title": title,
        "message": message,
        "severity": severity,
        "status": "unread",  # unread, read, resolved
        "metadata": metadata or {},
        "created_at": _now_iso(),
        "resolved_at": None,
        "resolved_by": None,
    }

    try:
        await db.system_alerts.insert_one(alert_doc)
        logger.warning(
            "SYSTEM ALERT [%s] (%s): %s - %s",
            severity.upper(),
            alert_type,
            title,
            message,
        )
    except Exception:
        logger.exception("Failed to persist system alert: %s", title)

    return alert_doc


async def list_system_alerts(
    db: Any,
    *,
    organization_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Retrieve system alerts for superadmin or specific tenant."""
    query: dict[str, Any] = {}
    if organization_id:
        query["organization_id"] = organization_id
    if status:
        query["status"] = status

    cursor = db.system_alerts.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cursor.to_list(limit)


async def resolve_system_alert(db: Any, alert_id: str, user_id: str | None = None) -> bool:
    """Mark a system alert as resolved."""
    result = await db.system_alerts.update_one(
        {"alert_id": alert_id},
        {
            "$set": {
                "status": "resolved",
                "resolved_at": _now_iso(),
                "resolved_by": user_id,
            }
        },
    )
    return result.modified_count > 0
