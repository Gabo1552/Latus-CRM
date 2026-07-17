"""Provider-side AI usage reconciliation.

Local request logs are useful for product analytics, but only the provider's
reporting/billing API can be treated as authoritative for organization spend.
Reporting credentials are stored separately from inference credentials because
OpenAI and Anthropic require organization-level admin keys.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from utils import crypto
from . import providers


REPORTING_PROVIDERS = ("openai", "anthropic", "openrouter")
_TIMEOUT = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)

CAPABILITIES: dict[str, dict[str, Any]] = {
    "built_in": {
        "label": "IA incluida",
        "tokens": "unavailable",
        "cost": "unavailable",
        "reporting_supported": False,
        "requires_separate_key": False,
        "description": "El gateway incluido no expone actualmente métricas de facturación al CRM.",
    },
    "openai": {
        "label": "OpenAI",
        "tokens": "provider_response",
        "cost": "admin_api",
        "reporting_supported": True,
        "requires_separate_key": True,
        "key_label": "Admin API key de OpenAI",
        "description": "Los tokens se miden en cada respuesta. El uso y costo conciliado requieren una Admin API key de la organización.",
    },
    "anthropic": {
        "label": "Anthropic",
        "tokens": "provider_response",
        "cost": "admin_api",
        "reporting_supported": True,
        "requires_separate_key": True,
        "key_label": "Admin API key de Anthropic",
        "description": "Los tokens se miden en cada respuesta. La conciliación de uso y costo requiere una Admin API key de Claude Console.",
    },
    "gemini": {
        "label": "Google Gemini",
        "tokens": "provider_response",
        "cost": "external_console",
        "reporting_supported": False,
        "requires_separate_key": False,
        "description": "Gemini devuelve tokens por respuesta; el gasto consolidado se consulta en AI Studio o Cloud Billing.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "tokens": "provider_response",
        "cost": "provider_response",
        "reporting_supported": True,
        "requires_separate_key": False,
        "key_label": "API key de OpenRouter",
        "description": "OpenRouter devuelve tokens y costo real por llamada. También permite consultar el consumo acumulado de la API key.",
    },
    "custom_openai": {
        "label": "Proveedor compatible con OpenAI",
        "tokens": "provider_response",
        "cost": "estimated",
        "reporting_supported": False,
        "requires_separate_key": False,
        "description": "Se usan los tokens informados por la respuesta; la disponibilidad de facturación depende del proveedor personalizado.",
    },
}


class ProviderUsageError(Exception):
    pass


def mask_key(value: str) -> str:
    if not value:
        return ""
    return f"••••••••{value[-4:]}"


async def _reporting_doc(db) -> dict:
    return await db.app_secrets.find_one({"_id": "ai_usage_reporting"}, {"_id": 0}) or {}


async def save_reporting_key(db, provider: str, value: str | None, user_id: str | None) -> None:
    if provider not in ("openai", "anthropic"):
        raise ValueError("Este proveedor no utiliza una clave administrativa separada")
    field = f"reporting_key_{provider}_enc"
    update: dict[str, Any]
    if value is None:
        update = {"$unset": {field: ""}, "$set": {"updated_at": _now_iso(), "updated_by": user_id}}
    else:
        clean = value.strip()
        if not clean:
            raise ValueError("Ingresá una clave válida")
        update = {"$set": {field: crypto.encrypt(clean), "updated_at": _now_iso(), "updated_by": user_id}}
    await db.app_secrets.update_one({"_id": "ai_usage_reporting"}, update, upsert=True)


async def _resolve_reporting_key(db, provider: str) -> str:
    if provider == "openrouter":
        return await providers._resolve_api_key(db, "openrouter")
    doc = await _reporting_doc(db)
    enc = doc.get(f"reporting_key_{provider}_enc")
    if not enc:
        return ""
    try:
        return crypto.decrypt(enc)
    except Exception:
        return ""


async def reporting_status(db) -> dict:
    settings = await providers.load_settings(db)
    doc = await _reporting_doc(db)
    items = []
    for provider, cap in CAPABILITIES.items():
        configured = False
        masked = ""
        if provider in ("openai", "anthropic"):
            enc = doc.get(f"reporting_key_{provider}_enc")
            if enc:
                try:
                    raw = crypto.decrypt(enc)
                    configured, masked = bool(raw), mask_key(raw)
                except Exception:
                    pass
        elif provider == "openrouter":
            raw = await providers._resolve_api_key(db, provider)
            configured, masked = bool(raw), mask_key(raw)
        items.append({"provider": provider, **cap, "configured": configured, "masked": masked})
    return {"active_provider": settings.get("provider", "built_in"), "providers": items}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _range(from_day: str, to_day: str) -> tuple[date, date, datetime, datetime]:
    try:
        start_day = date.fromisoformat(from_day)
        end_day = date.fromisoformat(to_day)
    except Exception as exc:
        raise ProviderUsageError("Rango de fechas inválido") from exc
    if start_day > end_day:
        raise ProviderUsageError("La fecha inicial no puede ser mayor que la final")
    if (end_day - start_day).days > 365:
        raise ProviderUsageError("La consulta al proveedor no puede superar 366 días")
    start = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    end_exclusive = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_day, end_day, start, end_exclusive


async def _json_get(client: httpx.AsyncClient, url: str, *, headers: dict, params: Any) -> dict:
    try:
        response = await client.get(url, headers=headers, params=params)
    except Exception as exc:
        raise ProviderUsageError("No se pudo contactar la API de consumo del proveedor") from exc
    if response.status_code >= 400:
        detail = ""
        try:
            payload = response.json()
            detail = payload.get("error", {}).get("message") or payload.get("error") or payload.get("message") or ""
        except Exception:
            detail = ""
        if response.status_code in (401, 403):
            raise ProviderUsageError("La clave no tiene permisos para consultar uso y facturación")
        raise ProviderUsageError(str(detail)[:240] or f"El proveedor respondió HTTP {response.status_code}")
    try:
        return response.json()
    except Exception as exc:
        raise ProviderUsageError("El proveedor devolvió una respuesta inválida") from exc


async def _paged(client: httpx.AsyncClient, url: str, *, headers: dict, params: Any) -> list[dict]:
    pages: list[dict] = []
    page_token = None
    for _ in range(100):
        request_params = list(params) if isinstance(params, list) else dict(params)
        if page_token:
            if isinstance(request_params, list):
                request_params.append(("page", page_token))
            else:
                request_params["page"] = page_token
        payload = await _json_get(client, url, headers=headers, params=request_params)
        pages.append(payload)
        if not payload.get("has_more") or not payload.get("next_page"):
            break
        page_token = payload["next_page"]
    return pages


def _openai_usage(pages: list[dict]) -> tuple[int, int, list[dict], list[dict]]:
    total_tokens = requests = 0
    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    for page in pages:
        for bucket in page.get("data") or []:
            day = datetime.fromtimestamp(int(bucket.get("start_time") or 0), timezone.utc).date().isoformat()
            day_row = by_day.setdefault(day, {"date": day, "requests": 0, "tokens": 0})
            for result in bucket.get("results") or []:
                tokens = int(result.get("input_tokens") or 0) + int(result.get("output_tokens") or 0)
                count = int(result.get("num_model_requests") or 0)
                model = result.get("model") or "Sin agrupar"
                total_tokens += tokens
                requests += count
                day_row["tokens"] += tokens
                day_row["requests"] += count
                model_row = by_model.setdefault(model, {"model": model, "requests": 0, "tokens": 0})
                model_row["tokens"] += tokens
                model_row["requests"] += count
    return total_tokens, requests, sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True), sorted(by_day.values(), key=lambda x: x["date"])


def _openai_cost(pages: list[dict]) -> float:
    total = Decimal("0")
    for page in pages:
        for bucket in page.get("data") or []:
            for result in bucket.get("results") or []:
                amount = result.get("amount") or {}
                try:
                    total += Decimal(str(amount.get("value") or 0))
                except (InvalidOperation, AttributeError):
                    continue
    return round(float(total), 6)


async def _fetch_openai(key: str, start: datetime, end: datetime) -> dict:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    base = {"start_time": int(start.timestamp()), "end_time": int(end.timestamp()), "bucket_width": "1d", "limit": 31}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        usage_pages = await _paged(client, "https://api.openai.com/v1/organization/usage/completions", headers=headers, params={**base, "group_by": "model"})
        cost_pages = await _paged(client, "https://api.openai.com/v1/organization/costs", headers=headers, params=base)
    tokens, requests, by_model, by_day = _openai_usage(usage_pages)
    return {"requests": requests, "tokens": tokens, "actual_cost_usd": _openai_cost(cost_pages), "by_model": by_model, "by_day": by_day}


def _anthropic_tokens(result: dict) -> int:
    detailed = [
        int(v or 0) for k, v in result.items()
        if k != "input_tokens" and k.endswith("input_tokens") and isinstance(v, (int, float))
    ]
    input_total = sum(detailed) if detailed else int(result.get("input_tokens") or 0)
    return input_total + int(result.get("output_tokens") or 0)


def _anthropic_usage(pages: list[dict]) -> tuple[int, int, list[dict], list[dict]]:
    total_tokens = requests = 0
    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    for page in pages:
        for bucket in page.get("data") or []:
            day = str(bucket.get("starting_at") or bucket.get("start_time") or "")[:10]
            day_row = by_day.setdefault(day, {"date": day, "requests": 0, "tokens": 0})
            for result in bucket.get("results") or []:
                tokens = _anthropic_tokens(result)
                count = int(result.get("requests") or result.get("num_requests") or 0)
                model = result.get("model") or "Sin agrupar"
                total_tokens += tokens
                requests += count
                day_row["tokens"] += tokens
                day_row["requests"] += count
                model_row = by_model.setdefault(model, {"model": model, "requests": 0, "tokens": 0})
                model_row["tokens"] += tokens
                model_row["requests"] += count
    return total_tokens, requests, sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True), sorted(by_day.values(), key=lambda x: x["date"])


def _anthropic_cost(pages: list[dict]) -> float:
    cents = Decimal("0")
    for page in pages:
        for bucket in page.get("data") or []:
            for result in bucket.get("results") or []:
                amount = result.get("amount") or result.get("cost") or 0
                if isinstance(amount, dict):
                    amount = amount.get("value") or 0
                try:
                    cents += Decimal(str(amount))
                except InvalidOperation:
                    continue
    return round(float(cents / Decimal("100")), 6)


async def _fetch_anthropic(key: str, start: datetime, end: datetime) -> dict:
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "User-Agent": "LatusCRM/1.0"}
    base = [("starting_at", start.isoformat().replace("+00:00", "Z")), ("ending_at", end.isoformat().replace("+00:00", "Z")), ("bucket_width", "1d"), ("limit", "31")]
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        usage_pages = await _paged(client, "https://api.anthropic.com/v1/organizations/usage_report/messages", headers=headers, params=[*base, ("group_by[]", "model")])
        cost_pages = await _paged(client, "https://api.anthropic.com/v1/organizations/cost_report", headers=headers, params=base)
    tokens, requests, by_model, by_day = _anthropic_usage(usage_pages)
    return {"requests": requests, "tokens": tokens, "actual_cost_usd": _anthropic_cost(cost_pages), "by_model": by_model, "by_day": by_day}


async def _fetch_openrouter(key: str) -> dict:
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        payload = await _json_get(client, "https://openrouter.ai/api/v1/key", headers=headers, params={})
    data = payload.get("data") or {}
    return {
        "requests": None,
        "tokens": None,
        "actual_cost_usd": None,
        "by_model": [],
        "by_day": [],
        "periods": {
            "today_usd": float(data.get("usage_daily") or 0),
            "week_usd": float(data.get("usage_weekly") or 0),
            "month_usd": float(data.get("usage_monthly") or 0),
            "all_time_usd": float(data.get("usage") or 0),
            "remaining_usd": data.get("limit_remaining"),
        },
    }


async def fetch_provider_report(db, provider: str, from_day: str, to_day: str) -> dict:
    if provider not in REPORTING_PROVIDERS:
        raise ProviderUsageError("Este proveedor no ofrece conciliación directa desde el CRM")
    start_day, end_day, start, end = _range(from_day, to_day)
    key = await _resolve_reporting_key(db, provider)
    if not key:
        message = "Configurá la clave administrativa para consultar el proveedor" if provider in ("openai", "anthropic") else "Configurá la API key de OpenRouter"
        raise ProviderUsageError(message)
    if provider == "anthropic" and (end_day - start_day).days >= 31:
        raise ProviderUsageError("Anthropic permite consultar hasta 31 días por informe")
    if provider == "openai":
        report = await _fetch_openai(key, start, end)
    elif provider == "anthropic":
        report = await _fetch_anthropic(key, start, end)
    else:
        report = await _fetch_openrouter(key)
    return {
        "provider": provider,
        "from": start_day.isoformat(),
        "to": end_day.isoformat(),
        "source": "provider_api",
        "fetched_at": _now_iso(),
        **report,
    }
