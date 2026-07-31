"""Immutable AI-usage settlement helpers.

Amounts from AI logs are already frozen in USD.  A settlement converts that
billable total to ARS exactly once, recording the exchange rate and safety
buffer used so later configuration changes cannot alter historical charges.
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ai.usage import billing_breakdown


BCRA_USD_URL = "https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones/USD"
POLICY_FIELD = "ai_variable_billing"
DEFAULT_POLICY: dict[str, Any] = {
    "enabled": False,
    "usd_to_ars_rate": 0.0,
    "exchange_rate_source": "not_configured",
    "exchange_rate_updated_at": None,
    "exchange_rate_observed_at": None,
    "fx_buffer_percent": 10.0,
    "settlement_lead_hours": 24,
    "max_rate_age_hours": 72,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_policy(patch: dict, current: dict | None = None) -> dict:
    result = {**DEFAULT_POLICY, **(current or {})}
    if "enabled" in patch:
        result["enabled"] = bool(patch["enabled"])
    if "usd_to_ars_rate" in patch:
        rate = float(patch["usd_to_ars_rate"] or 0)
        if not math.isfinite(rate) or rate <= 0 or rate > 1_000_000:
            raise ValueError("La cotización USD/ARS debe ser mayor a cero")
        result["usd_to_ars_rate"] = round(rate, 6)
        result["exchange_rate_source"] = "manual"
        result["exchange_rate_updated_at"] = now_iso()
        result["exchange_rate_observed_at"] = now_iso()
    if "fx_buffer_percent" in patch:
        buffer = float(patch["fx_buffer_percent"])
        if not math.isfinite(buffer) or buffer < 0 or buffer > 100:
            raise ValueError("El colchón cambiario debe estar entre 0% y 100%")
        result["fx_buffer_percent"] = round(buffer, 4)
    if "settlement_lead_hours" in patch:
        lead = int(patch["settlement_lead_hours"])
        if lead < 1 or lead > 168:
            raise ValueError("La anticipación debe estar entre 1 y 168 horas")
        result["settlement_lead_hours"] = lead
    if "max_rate_age_hours" in patch:
        age = int(patch["max_rate_age_hours"])
        if age < 12 or age > 720:
            raise ValueError("La vigencia de cotización debe estar entre 12 y 720 horas")
        result["max_rate_age_hours"] = age
    if result["enabled"] and float(result.get("usd_to_ars_rate") or 0) <= 0:
        raise ValueError("Configurá la cotización USD/ARS antes de activar la liquidación")
    return result


async def load_policy(pricing_collection) -> dict:
    doc = await pricing_collection.find_one({"_id": "default"}, {"_id": 0}) or {}
    return validate_policy({}, doc.get(POLICY_FIELD) or {})


async def save_policy(pricing_collection, patch: dict, user_id: str | None) -> dict:
    current = await load_policy(pricing_collection)
    policy = validate_policy(patch, current)
    policy["updated_at"] = now_iso()
    policy["updated_by"] = user_id
    await pricing_collection.update_one(
        {"_id": "default"}, {"$set": {POLICY_FIELD: policy}}, upsert=True,
    )
    return policy


async def fetch_bcra_usd_rate() -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(BCRA_USD_URL)
    except httpx.HTTPError as exc:
        raise ValueError("No se pudo consultar la cotización del BCRA") from exc
    if response.status_code >= 400:
        raise ValueError(f"El BCRA respondió HTTP {response.status_code}")
    try:
        payload = response.json()
        row = (payload.get("results") or [])[0]
        detail = next(
            item for item in (row.get("detalle") or [])
            if str(item.get("codigoMoneda") or "").upper() == "USD"
        )
        rate = float(detail.get("tipoCotizacion"))
        observed_at = str(row.get("fecha") or "")
    except (ValueError, TypeError, IndexError, KeyError, StopIteration) as exc:
        raise ValueError("El BCRA devolvió una cotización inválida") from exc
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("El BCRA devolvió una cotización inválida")
    return {"rate": round(rate, 6), "observed_at": observed_at, "source": "bcra"}


async def refresh_bcra_rate(pricing_collection, user_id: str | None = None) -> dict:
    quote = await fetch_bcra_usd_rate()
    current = await load_policy(pricing_collection)
    policy = {
        **current,
        "usd_to_ars_rate": quote["rate"],
        "exchange_rate_source": quote["source"],
        "exchange_rate_updated_at": now_iso(),
        "exchange_rate_observed_at": quote["observed_at"],
        "updated_at": now_iso(),
        "updated_by": user_id or "system:bcra",
    }
    await pricing_collection.update_one(
        {"_id": "default"}, {"$set": {POLICY_FIELD: policy}}, upsert=True,
    )
    return policy


def rate_is_fresh(policy: dict, *, at: datetime | None = None) -> bool:
    at = at or datetime.now(timezone.utc)
    updated = parse_datetime(policy.get("exchange_rate_updated_at"))
    if not updated or float(policy.get("usd_to_ars_rate") or 0) <= 0:
        return False
    return at - updated <= timedelta(hours=int(policy.get("max_rate_age_hours") or 72))


def previous_cycle_start(charge_at: datetime) -> datetime:
    """Return the same UTC clock time one calendar month earlier."""
    year, month = charge_at.year, charge_at.month - 1
    if month == 0:
        year, month = year - 1, 12
    # Billing dates are normally <= 28, but clamp defensively.
    import calendar
    day = min(charge_at.day, calendar.monthrange(year, month)[1])
    return charge_at.replace(year=year, month=month, day=day)


def summarize_logs(logs: list[dict]) -> dict:
    summary = {"calls": 0, "tokens": 0, "base_cost_usd": 0.0,
               "ai_fee_usd": 0.0, "billable_cost_usd": 0.0,
               "cost_sources": {}}
    for log in logs:
        breakdown = billing_breakdown(log)
        summary["calls"] += 1
        summary["tokens"] += int(log.get("total_tokens") or 0)
        for field in ("base_cost_usd", "ai_fee_usd", "billable_cost_usd"):
            summary[field] = round(summary[field] + float(breakdown[field]), 8)
        source = str(log.get("cost_source") or breakdown.get("cost_source") or "estimated")
        summary["cost_sources"][source] = summary["cost_sources"].get(source, 0) + 1
    return summary


def calculate_amounts(*, plan_amount_ars: float, billable_cost_usd: float,
                      usd_to_ars_rate: float, fx_buffer_percent: float) -> dict:
    billable = Decimal(str(billable_cost_usd))
    rate = Decimal(str(usd_to_ars_rate))
    buffer_multiplier = Decimal("1") + Decimal(str(fx_buffer_percent)) / Decimal("100")
    converted = billable * rate
    ai_amount = (converted * buffer_multiplier).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    plan_amount = Decimal(str(plan_amount_ars)).quantize(Decimal("0.01"))
    return {
        "ai_cost_converted_ars": float(converted.quantize(Decimal("0.01"))),
        "ai_amount_ars": float(ai_amount),
        "plan_amount_ars": float(plan_amount),
        "total_amount_ars": float((plan_amount + ai_amount).quantize(Decimal("0.01"))),
    }


def calculate_profitability_breakdown(*,
                                      plan_amount_ars: float,
                                      ai_amount_ars: float,
                                      base_cost_usd: float,
                                      usd_to_ars_rate: float,
                                      mp_fee_percent: float = 4.5,
                                      tax_percent: float = 0.0,
                                      min_margin_percent: float = 15.0) -> dict[str, float | bool | str]:
    """Calculate Net Profit breakdown and verify minimum margin safety rules.

    Formula:
      Ingresos - Costo Proveedor - Costo Mercado Pago - Impuestos = Margen Neto
    """
    total_revenue_ars = round(plan_amount_ars + ai_amount_ars, 2)
    provider_cost_ars = round(base_cost_usd * usd_to_ars_rate, 2)
    mp_fee_ars = round(total_revenue_ars * (mp_fee_percent / 100.0), 2)
    tax_ars = round(total_revenue_ars * (tax_percent / 100.0), 2)
    net_profit_ars = round(total_revenue_ars - provider_cost_ars - mp_fee_ars - tax_ars, 2)
    net_margin_percent = round((net_profit_ars / total_revenue_ars * 100.0), 2) if total_revenue_ars > 0 else 0.0

    is_profitable = net_margin_percent >= min_margin_percent and net_profit_ars > 0
    warning = None
    if not is_profitable:
        warning = f"Margen neto insuficiente ({net_margin_percent}% < mínimo {min_margin_percent}%). Revisa Fee o cotización."

    return {
        "total_revenue_ars": total_revenue_ars,
        "provider_cost_ars": provider_cost_ars,
        "mp_fee_ars": mp_fee_ars,
        "tax_ars": tax_ars,
        "net_profit_ars": net_profit_ars,
        "net_margin_percent": net_margin_percent,
        "min_margin_percent": min_margin_percent,
        "is_profitable": is_profitable,
        "warning": warning,
    }


def generate_settlement_reference(organization_id: str, period: str, nonce: str | None = None) -> str:
    """Generate an immutable, unique external reference for Mercado Pago settlements."""
    if not nonce:
        import uuid
        nonce = uuid.uuid4().hex[:8]
    clean_org = str(organization_id).replace("org_", "").replace("-", "")[:8]
    clean_period = str(period).replace("-", "")[:6]
    return f"latus_settle_{clean_org}_{clean_period}_{nonce}"


def simulate_settlement(*,
                        organization_id: str,
                        plan_name: str,
                        plan_amount_ars: float,
                        logs: list[dict],
                        usd_to_ars_rate: float,
                        fx_buffer_percent: float = 10.0,
                        included_tokens: int = 250_000,
                        period_start: str | None = None,
                        period_end: str | None = None,
                        exchange_rate_source: str = "not_configured",
                        exchange_rate_observed_at: str | None = None,
                        configured_fee_percent: float | None = None,
                        fee_source: str = "global",
                        buffer_source: str = "global",
                        mp_fee_percent: float = 4.5,
                        tax_percent: float = 0.0,
                        min_margin_percent: float = 15.0) -> dict:
    """Simulate a settlement statement without modifying Mercado Pago or DB.

    The simulation deliberately uses the same frozen billable cost as the real
    settlement engine. The plan token limit is operational (it limits calls),
    not a free monetary allowance to subtract from the variable charge.
    """
    summary = summarize_logs(logs)
    total_tokens = summary["tokens"]
    billable_cost_usd = round(summary["billable_cost_usd"], 8)
    base_cost_usd = round(summary["base_cost_usd"], 8)
    ai_fee_usd = round(summary["ai_fee_usd"], 8)
    effective_fee_percent = round(ai_fee_usd / base_cost_usd * 100.0, 4) if base_cost_usd else 0.0

    amounts = calculate_amounts(
        plan_amount_ars=plan_amount_ars,
        billable_cost_usd=billable_cost_usd,
        usd_to_ars_rate=usd_to_ars_rate,
        fx_buffer_percent=fx_buffer_percent,
    )

    profitability = calculate_profitability_breakdown(
        plan_amount_ars=plan_amount_ars,
        ai_amount_ars=amounts["ai_amount_ars"],
        base_cost_usd=base_cost_usd,
        usd_to_ars_rate=usd_to_ars_rate,
        mp_fee_percent=mp_fee_percent,
        tax_percent=tax_percent,
        min_margin_percent=min_margin_percent,
    )

    return {
        "simulation": True,
        "side_effects": {
            "database_writes": False,
            "provider_calls": False,
            "mercadopago_charges": False,
        },
        "organization_id": organization_id,
        "plan_name": plan_name,
        "period": {
            "start": period_start,
            "end": period_end,
            "timezone": "UTC",
            "end_exclusive": True,
        },
        "usage": {
            "calls": summary["calls"],
            "total_tokens": total_tokens,
            "operational_token_limit": included_tokens,
            "base_cost_usd": base_cost_usd,
            "ai_fee_usd": ai_fee_usd,
            "billable_cost_usd": billable_cost_usd,
            "configured_fee_percent": configured_fee_percent,
            "effective_fee_percent": effective_fee_percent,
            "fee_source": fee_source,
            "cost_sources": summary["cost_sources"],
        },
        "period_summary": {
            "calls": summary["calls"],
            "total_tokens": total_tokens,
            "included_tokens": included_tokens,
            "base_cost_usd": base_cost_usd,
            "ai_fee_usd": ai_fee_usd,
            "billable_cost_usd": billable_cost_usd,
        },
        "rates": {
            "usd_to_ars_rate": usd_to_ars_rate,
            "exchange_rate_source": exchange_rate_source,
            "exchange_rate_observed_at": exchange_rate_observed_at,
            "fx_buffer_percent": fx_buffer_percent,
            "buffer_source": buffer_source,
        },
        "amounts": amounts,
        "profitability": profitability,
        "assumptions": {
            "variable_charge_uses_all_frozen_usage": True,
            "operational_token_limit_is_not_a_free_allowance": True,
            "plan_operating_costs_are_not_included": True,
            "mercadopago_fee_percent": mp_fee_percent,
            "tax_percent": tax_percent,
        },
        "generated_at": now_iso(),
    }
