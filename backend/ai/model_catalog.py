"""Provider-backed AI model catalog for platform administrators.

Credentials stay server-side. OpenRouter publishes token prices in its model
response, so those rates can be imported automatically. Other providers expose
availability but not a billable price table; those models need an explicit
price before they can be activated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ai import providers, usage


CATALOG_ID = "ai_model_catalog"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fallback(provider: str) -> list[dict[str, Any]]:
    return [{"id": model, "name": model, "pricing_source": "configured"}
            for model in providers.MODEL_SUGGESTIONS.get(provider, [])]


async def _request_json(method: str, url: str, **kwargs) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.request(method, url, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {}).get("message")
        except Exception:
            detail = None
        raise ValueError(detail or f"El proveedor respondió HTTP {response.status_code}")
    return response.json()


async def load_catalog(db, provider: str) -> dict:
    doc = await db.platform_secrets.find_one({"_id": CATALOG_ID}, {"_id": 0}) or {}
    cached = (doc.get("providers") or {}).get(provider) or {}
    return {
        "provider": provider,
        "models": cached.get("models") or _fallback(provider),
        "synced_at": cached.get("synced_at"),
        "source": cached.get("source") or "fallback",
    }


async def _fetch_models(db, provider: str, base_url: str = "") -> list[dict[str, Any]]:
    if provider == "built_in":
        return _fallback(provider)
    key = await providers._resolve_api_key(db, provider)
    if not key:
        raise ValueError("Configurá la API Key del proveedor antes de actualizar modelos")

    if provider == "openai":
        payload = await _request_json("GET", "https://api.openai.com/v1/models",
                                      headers={"Authorization": f"Bearer {key}"})
        excluded = ("embedding", "moderation", "tts", "transcribe", "realtime", "audio", "image")
        return [{"id": model, "name": model, "pricing_source": "manual"}
                for item in payload.get("data") or []
                if (model := str(item.get("id") or "").strip())
                and model.lower().startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))
                and not any(token in model.lower() for token in excluded)]

    if provider == "anthropic":
        payload = await _request_json(
            "GET", "https://api.anthropic.com/v1/models?limit=1000",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        return [{"id": item["id"], "name": item.get("display_name") or item["id"],
                 "pricing_source": "manual"}
                for item in payload.get("data") or [] if item.get("id")]

    if provider == "gemini":
        rows: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            payload = await _request_json(
                "GET", "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": key}, params=params)
            for item in payload.get("models") or []:
                actions = item.get("supportedGenerationMethods") or item.get("supportedActions") or []
                model = str(item.get("name") or "").removeprefix("models/")
                if model and "generateContent" in actions:
                    rows.append({"id": model, "name": item.get("displayName") or model,
                                 "context_length": item.get("inputTokenLimit"),
                                 "pricing_source": "manual"})
            page_token = payload.get("nextPageToken") or ""
            if not page_token:
                return rows

    if provider == "openrouter":
        payload = await _request_json("GET", "https://openrouter.ai/api/v1/models",
                                      headers={"Authorization": f"Bearer {key}"})
        rows = []
        for item in payload.get("data") or []:
            model = str(item.get("id") or "").strip()
            output_modalities = (item.get("architecture") or {}).get("output_modalities") or []
            if not model or (output_modalities and "text" not in output_modalities):
                continue
            pricing = item.get("pricing") or {}
            pricing_available = pricing.get("prompt") is not None and pricing.get("completion") is not None
            try:
                input_rate = float(pricing.get("prompt") or 0) * 1_000_000
                output_rate = float(pricing.get("completion") or 0) * 1_000_000
            except (TypeError, ValueError):
                input_rate = output_rate = 0.0
                pricing_available = False
            rows.append({"id": model, "name": item.get("name") or model,
                         "context_length": item.get("context_length"),
                         "input_per_million": round(input_rate, 8) if pricing_available else None,
                         "output_per_million": round(output_rate, 8) if pricing_available else None,
                         "pricing_source": "provider_api" if pricing_available else "manual",
                         "pricing_available": pricing_available,
                         "verified_free": pricing_available and input_rate == 0 and output_rate == 0})
        return rows

    if provider == "custom_openai":
        url = (base_url or "").rstrip("/")
        if not url:
            raise ValueError("Configurá la URL base antes de actualizar modelos")
        payload = await _request_json("GET", f"{url}/models",
                                      headers={"Authorization": f"Bearer {key}"})
        return [{"id": item["id"], "name": item.get("name") or item["id"],
                 "pricing_source": "manual"}
                for item in payload.get("data") or [] if item.get("id")]
    raise ValueError("Proveedor no soportado")


async def sync_catalog(db, provider: str, *, base_url: str = "", user_id: str | None = None) -> dict:
    if provider not in providers.SUPPORTED_PROVIDERS:
        raise ValueError("Proveedor no soportado")
    fetched = await _fetch_models(db, provider, base_url)
    models = sorted({item["id"]: item for item in fetched if item.get("id")}.values(),
                    key=lambda item: item["id"].lower())[:2000]
    if not models:
        raise ValueError("El proveedor no devolvió modelos compatibles")
    if provider == "openrouter":
        for item in models:
            if not item.get("pricing_available"):
                continue
            await usage.save_provider_pricing(
                db, item["id"], item["input_per_million"], item["output_per_million"],
                user_id=user_id, verified_free=bool(item.get("verified_free")))

    doc = await db.platform_secrets.find_one({"_id": CATALOG_ID}, {"_id": 0}) or {}
    all_providers = dict(doc.get("providers") or {})
    all_providers[provider] = {"models": models, "synced_at": _now_iso(),
                               "source": "provider_api" if provider != "built_in" else "configured",
                               "updated_by": user_id}
    await db.platform_secrets.update_one({"_id": CATALOG_ID},
                                         {"$set": {"providers": all_providers}}, upsert=True)
    return await load_catalog(db, provider)


async def catalog_with_pricing(db, provider: str) -> dict:
    catalog = await load_catalog(db, provider)
    pricing = await usage.load_pricing(db)
    metadata = await usage.load_pricing_metadata(db)
    catalog["models"] = [{
        **item,
        "pricing_configured": item["id"] in pricing,
        "input_per_million": pricing.get(item["id"], {}).get("input", item.get("input_per_million")),
        "output_per_million": pricing.get(item["id"], {}).get("output", item.get("output_per_million")),
        "verified_free": bool((metadata.get(item["id"]) or {}).get("verified_free") or item.get("verified_free")),
    } for item in catalog["models"]]
    return catalog
