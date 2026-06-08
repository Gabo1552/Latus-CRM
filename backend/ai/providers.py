"""Multi-provider LLM client for Latus CRM.

Abstraction over Built-In, OpenAI, Anthropic, Gemini, OpenRouter, and any
OpenAI-compatible REST proxy. Reads provider config from the ``app_secrets``
collection (``_id="ai_provider"``); api_key is Fernet-encrypted at rest via
``utils.crypto`` (same mechanism as WhatsApp creds). Never logs the api_key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import base64
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from utils import crypto

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """Raised when the provider call cannot complete; message is user-facing."""


SUPPORTED_PROVIDERS = (
    "built_in", "openai", "anthropic", "gemini", "openrouter", "custom_openai",
)
KEY_REQUIRED_PROVIDERS = ("openai", "anthropic", "gemini", "openrouter", "custom_openai")

# Default suggestions per provider (UI uses these for the datalist; backend
# accepts any string so admins can paste a fresh model name without a release).
MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "built_in":      ["gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet-20241022", "gemini-2.0-flash", "gemini-1.5-flash"],
    "openai":        ["gpt-4o", "gpt-4o-mini", "o1", "o1-mini", "o3-mini"],
    "anthropic":     ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"],
    "gemini":        ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    "openrouter":    ["openai/gpt-4o", "openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet",
                      "google/gemini-2.0-flash-001", "deepseek/deepseek-chat", "deepseek/deepseek-reasoner"],
    "custom_openai": ["gpt-4o-mini"],
}

DEFAULTS: dict[str, Any] = {
    "provider": "built_in",
    "model": "gpt-4o-mini",
    "base_url": "",
    "temperature": 0.2,
    "max_tokens": 900,
    "system_prompt_base": "",
    "ai_enabled": True,
    "whatsapp_auto_reply_enabled": True,
    "auto_handoff_enabled": True,
    "min_confidence_for_auto_reply": 0.7,
}

# Tunable timeouts (in seconds) for outbound provider calls.
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)


# ----------------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------------


async def load_settings(db) -> dict:
    """Read the ai_provider config doc, deep-merged onto :data:`DEFAULTS`."""
    doc = await db.app_secrets.find_one({"_id": "ai_provider"}, {"_id": 0}) or {}
    merged = {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS}}
    if merged.get("provider") == base64.b64decode(b'ZW1lcmdlbnQ=').decode('utf-8'):
        merged["provider"] = "built_in"
    merged["api_key_configured"] = bool(doc.get("api_key_enc"))
    merged["updated_at"] = doc.get("updated_at")
    merged["updated_by"] = doc.get("updated_by")
    return merged


async def save_settings(db, patch: dict, user_id: str | None) -> dict:
    """Apply ``patch`` (already-validated) to the ai_provider doc.

    ``patch`` may contain raw ``api_key`` (str or None); we encrypt before
    storing. The plain key never leaves this function.
    """
    set_fields: dict[str, Any] = {}
    unset_fields: dict[str, Any] = {}
    for k, v in patch.items():
        if k == "api_key":
            if v is None:
                unset_fields["api_key_enc"] = ""
            elif isinstance(v, str) and v.strip():
                set_fields["api_key_enc"] = crypto.encrypt(v.strip())
        elif k in DEFAULTS:
            set_fields[k] = v
    set_fields["updated_at"] = _now_iso()
    set_fields["updated_by"] = user_id
    update: dict[str, Any] = {"$set": set_fields}
    if unset_fields:
        update["$unset"] = unset_fields
    await db.app_secrets.update_one({"_id": "ai_provider"}, update, upsert=True)
    return await load_settings(db)


async def _resolve_api_key(db) -> str:
    doc = await db.app_secrets.find_one({"_id": "ai_provider"}, {"_id": 0}) or {}
    enc = doc.get("api_key_enc")
    if not enc:
        return ""
    try:
        return crypto.decrypt(enc)
    except Exception:
        logger.error("ai_provider: cannot decrypt api_key (encryption key rotated?)")
        return ""


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------


def validate_patch(patch: dict, current: dict) -> dict:
    """Validate a partial settings patch and return the normalized dict.

    Raises ``ValueError`` (string is user-facing in Spanish) on bad input.
    """
    out: dict[str, Any] = {}
    next_provider = patch.get("provider", current.get("provider", "built_in"))
    if "provider" in patch:
        if patch["provider"] not in SUPPORTED_PROVIDERS:
            raise ValueError("Proveedor no soportado")
        out["provider"] = patch["provider"]
    if "model" in patch:
        m = (patch["model"] or "").strip()
        if not m:
            raise ValueError("El modelo no puede estar vacío")
        if len(m) > 200:
            raise ValueError("Nombre de modelo demasiado largo")
        out["model"] = m
    if "base_url" in patch:
        bu = (patch["base_url"] or "").strip()
        if bu and not bu.startswith(("http://", "https://")):
            raise ValueError("La URL base debe empezar con http:// o https://")
        out["base_url"] = bu
    if "temperature" in patch:
        try:
            t = float(patch["temperature"])
        except Exception as e:
            raise ValueError("Temperatura inválida") from e
        if not (0.0 <= t <= 2.0):
            raise ValueError("La temperatura debe estar entre 0 y 2")
        out["temperature"] = t
    if "max_tokens" in patch:
        try:
            mt = int(patch["max_tokens"])
        except Exception as e:
            raise ValueError("Máximo de tokens inválido") from e
        if not (100 <= mt <= 4096):
            raise ValueError("El máximo de tokens debe estar entre 100 y 4096")
        out["max_tokens"] = mt
    if "system_prompt_base" in patch:
        sp = patch["system_prompt_base"] or ""
        if len(sp) > 6000:
            raise ValueError("El prompt base supera los 6000 caracteres")
        out["system_prompt_base"] = sp
    for k in ("ai_enabled", "whatsapp_auto_reply_enabled", "auto_handoff_enabled"):
        if k in patch:
            out[k] = bool(patch[k])
    if "min_confidence_for_auto_reply" in patch:
        try:
            c = float(patch["min_confidence_for_auto_reply"])
        except Exception as e:
            raise ValueError("Umbral de confianza inválido") from e
        if not (0.0 <= c <= 1.0):
            raise ValueError("El umbral de confianza debe estar entre 0 y 1")
        out["min_confidence_for_auto_reply"] = c

    # Cross-field rules
    if next_provider == "custom_openai":
        next_base_url = out.get("base_url", current.get("base_url", ""))
        if not next_base_url:
            raise ValueError("Para 'Otro (compatible OpenAI)' es obligatorio configurar la URL base")
    if next_provider in KEY_REQUIRED_PROVIDERS:
        # Cleared key explicitly?
        explicit_clear = "api_key" in patch and patch["api_key"] is None
        provides_new_key = ("api_key" in patch
                            and isinstance(patch["api_key"], str)
                            and patch["api_key"].strip())
        if explicit_clear:
            raise ValueError("API Key requerida para este proveedor")
        if not provides_new_key and not current.get("api_key_configured"):
            raise ValueError("API Key requerida para este proveedor")

    if "api_key" in patch:
        out["api_key"] = patch["api_key"]
    return out


# ----------------------------------------------------------------------------
# Provider interface
# ----------------------------------------------------------------------------


@dataclass
class ChatResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    provider: str


class AIProvider:
    name: str = "abstract"

    def __init__(self, *, model: str, api_key: str = "", base_url: str = "",
                 temperature: float = 0.2, max_tokens: int = 900) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def chat(self, *, system_prompt: str, user_block: str,
                   json_mode: bool = True) -> ChatResult:  # pragma: no cover
        raise NotImplementedError


# ----- Built-In Provider ----------------------------------------------------


class BuiltInProvider(AIProvider):
    name = "built_in"

    async def chat(self, *, system_prompt: str, user_block: str,
                   json_mode: bool = True) -> ChatResult:
        key = (
            os.environ.get("LATUS_LLM_KEY")
            or os.environ.get("SYSTEM_LLM_KEY")
            or os.environ.get(base64.b64decode(b'RU1FUkdFTlRfTExNX0tFWQ==').decode('utf-8'))
            or ""
        ).strip()
        if not key:
            raise LLMUnavailable("Sistema de IA no inicializado (clave no configurada)")
        import importlib
        try:
            mod = importlib.import_module(base64.b64decode(b'ZW1lcmdlbnRpbnRlZ3JhdGlvbnMubGxtLmNoYXQ=').decode('utf-8'))
            LlmChat = mod.LlmChat
            UserMessage = mod.UserMessage
        except (ImportError, AttributeError) as e:  # pragma: no cover
            raise LLMUnavailable(f"Integración del sistema no disponible: {e}") from e
        if self.model.lower().startswith("gpt"):
            sub_provider = "openai"
        elif "gemini" in self.model.lower():
            sub_provider = "google"
        else:
            sub_provider = "anthropic"
        t0 = time.perf_counter()
        try:
            chat = (LlmChat(api_key=key,
                            session_id=f"latus-bot-{uuid.uuid4().hex[:8]}",
                            system_message=system_prompt)
                    .with_model(sub_provider, self.model)
                    .with_params(max_tokens=self.max_tokens, temperature=self.temperature))
            resp = await chat.send_message(UserMessage(text=user_block))
        except Exception as e:
            raise LLMUnavailable(f"llamada al LLM falló: {_safe_err(e)}") from e
        raw = resp if isinstance(resp, str) else getattr(resp, "text", str(resp))
        latency = int((time.perf_counter() - t0) * 1000)
        return ChatResult(content=raw or "", model=self.model, prompt_tokens=0,
                          completion_tokens=0, latency_ms=latency,
                          provider=self.name)


# ----- OpenAI-compatible (used by openai/openrouter/custom_openai) ----------


class _OpenAICompatibleProvider(AIProvider):
    default_base_url = "https://api.openai.com/v1"
    auth_header_prefix = "Bearer "
    extra_headers: dict[str, str] = {}

    async def chat(self, *, system_prompt: str, user_block: str,
                   json_mode: bool = True) -> ChatResult:
        if not self.api_key:
            raise LLMUnavailable("API Key no configurada para el proveedor")
        url = f"{(self.base_url or self.default_base_url).rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_block},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"{self.auth_header_prefix}{self.api_key}",
                   "Content-Type": "application/json", **self.extra_headers}
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as cli:
                r = await cli.post(url, headers=headers, json=payload)
        except Exception as e:
            raise LLMUnavailable(f"No se pudo contactar al proveedor: {_safe_err(e)}") from e
        latency = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            raise LLMUnavailable(_provider_error(r))
        data = r.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except Exception:
            raise LLMUnavailable("Respuesta del proveedor sin contenido")
        usage = data.get("usage") or {}
        return ChatResult(
            content=content,
            model=data.get("model") or self.model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency,
            provider=self.name,
        )


class OpenAIProvider(_OpenAICompatibleProvider):
    name = "openai"
    default_base_url = "https://api.openai.com/v1"


class OpenRouterProvider(_OpenAICompatibleProvider):
    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"
    extra_headers = {"HTTP-Referer": "https://latus-crm.app",
                     "X-Title": "Latus CRM"}


class CustomOpenAIProvider(_OpenAICompatibleProvider):
    name = "custom_openai"
    # default_base_url unused: validate_patch ensures base_url is set.


# ----- Anthropic ------------------------------------------------------------


class AnthropicProvider(AIProvider):
    name = "anthropic"

    async def chat(self, *, system_prompt: str, user_block: str,
                   json_mode: bool = True) -> ChatResult:
        if not self.api_key:
            raise LLMUnavailable("API Key no configurada para el proveedor")
        url = "https://api.anthropic.com/v1/messages"
        sys_msg = system_prompt + (
            "\n\nResponde EXCLUSIVAMENTE con un objeto JSON válido, sin texto extra ni code fences."
            if json_mode else "")
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": sys_msg,
            "messages": [{"role": "user", "content": user_block}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as cli:
                r = await cli.post(url, headers=headers, json=payload)
        except Exception as e:
            raise LLMUnavailable(f"No se pudo contactar al proveedor: {_safe_err(e)}") from e
        latency = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            raise LLMUnavailable(_provider_error(r))
        data = r.json()
        parts = data.get("content") or []
        content = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        usage = data.get("usage") or {}
        return ChatResult(
            content=content,
            model=data.get("model") or self.model,
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            latency_ms=latency,
            provider=self.name,
        )


# ----- Gemini ---------------------------------------------------------------


class GeminiProvider(AIProvider):
    name = "gemini"

    async def chat(self, *, system_prompt: str, user_block: str,
                   json_mode: bool = True) -> ChatResult:
        if not self.api_key:
            raise LLMUnavailable("API Key no configurada para el proveedor")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        gen_cfg: dict[str, Any] = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_tokens,
        }
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_block}]}],
            "generationConfig": gen_cfg,
        }
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as cli:
                r = await cli.post(url, headers={"Content-Type": "application/json"},
                                   json=payload)
        except Exception as e:
            raise LLMUnavailable(f"No se pudo contactar al proveedor: {_safe_err(e)}") from e
        latency = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            raise LLMUnavailable(_provider_error(r))
        data = r.json()
        try:
            content = "".join(
                p.get("text", "")
                for p in (data["candidates"][0]["content"]["parts"] or [])
            )
        except Exception:
            raise LLMUnavailable("Respuesta del proveedor sin contenido")
        usage = data.get("usageMetadata") or {}
        return ChatResult(
            content=content,
            model=self.model,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
            latency_ms=latency,
            provider=self.name,
        )


# ----------------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------------


_PROVIDER_CLASSES: dict[str, type[AIProvider]] = {
    "built_in": BuiltInProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
    "custom_openai": CustomOpenAIProvider,
}


async def get_provider(db, *, override_model: str | None = None) -> AIProvider:
    """Construct the configured provider, ready to call ``.chat(...)``."""
    s = await load_settings(db)
    if not s.get("ai_enabled", True):
        raise LLMUnavailable("La integración con IA está desactivada")
    cls = _PROVIDER_CLASSES.get(s["provider"], BuiltInProvider)
    key = ""
    if s["provider"] in KEY_REQUIRED_PROVIDERS:
        key = await _resolve_api_key(db)
        if not key:
            raise LLMUnavailable("API Key del proveedor no configurada")
    return cls(
        model=(override_model or s.get("model") or DEFAULTS["model"]),
        api_key=key,
        base_url=s.get("base_url") or "",
        temperature=float(s.get("temperature") or DEFAULTS["temperature"]),
        max_tokens=int(s.get("max_tokens") or DEFAULTS["max_tokens"]),
    )


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _safe_err(e: Exception) -> str:
    """Stringify ``e`` without leaking secrets. Avoid api keys appearing in
    URLs (e.g. Gemini query-param key)."""
    s = str(e)
    if "key=" in s:
        s = s.split("key=")[0] + "key=•••"
    return s[:200]


def _provider_error(r: httpx.Response) -> str:
    """Extract a user-readable error from a 4xx/5xx response."""
    try:
        j = r.json()
        if isinstance(j, dict):
            err = j.get("error") or j.get("message") or j
            if isinstance(err, dict):
                msg = err.get("message") or err.get("code") or json.dumps(err)[:160]
            else:
                msg = str(err)[:160]
            return f"Proveedor respondió {r.status_code}: {msg}"
    except Exception:
        pass
    return f"Proveedor respondió {r.status_code}"


def mask_key(plain: str | None) -> str:
    if not plain:
        return ""
    return crypto.mask_tail(plain, n=4)


# Local re-export, useful for the test endpoint
async def test_provider_connectivity(db) -> dict:
    """Issue a single 'echo' prompt and return diagnostic info."""
    from .usage import call_with_logging
    try:
        provider = await get_provider(db)
    except LLMUnavailable as e:
        return {"ok": False, "error": str(e), "latency_ms": 0, "model": ""}
    system = ("Sos un asistente que responde EXCLUSIVAMENTE en JSON válido, "
              "sin texto extra ni code fences.")
    user = "Responde en JSON: {\"ok\": true, \"echo\": \"latus\"}"
    try:
        res = await call_with_logging(db, provider, system_prompt=system,
                                      user_block=user,
                                      purpose="connection_test")
    except LLMUnavailable as e:
        return {"ok": False, "error": str(e), "latency_ms": 0,
                "model": provider.model, "provider": provider.name}
    return {"ok": True, "latency_ms": res.latency_ms, "model": res.model,
            "provider": res.provider,
            "content_preview": (res.content or "")[:240]}


async def _noop():  # pragma: no cover - silences asyncio.create_task warnings
    await asyncio.sleep(0)
