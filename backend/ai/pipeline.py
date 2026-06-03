"""Latus CRM bot pipeline — orchestrates LLM call + DB updates for inbound messages."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .llm import call_llm_json, LLMUnavailable
from .prompts import build_system_prompt, build_summary_only_prompt, DEFAULT_HANDOFF_RULES
from . import providers as ai_providers
from . import catalog_search as cs

logger = logging.getLogger(__name__)

LEAD_STATUSES = ("nuevo", "calificando", "calificado", "propuesta_solicitada",
                 "propuesta_enviada", "negociacion", "ganado", "perdido", "no_responde")
BOT_STATUSES = ("bot_activo", "esperando_cliente", "requiere_humano",
                "en_atencion_humana", "cerrada")
LEGACY_LEAD_MAP = {"new": "nuevo", "qualifying": "calificando", "qualified": "calificado",
                   "proposal": "propuesta_enviada", "won": "ganado", "lost": "perdido"}

DEFAULT_BOT_SETTINGS = {
    "bot_enabled_default": True,
    "confidence_threshold": 0.70,
    "recent_messages_context_max": 12,
    "business_instructions": "",
    "faqs": [],
    "handoff_rules": DEFAULT_HANDOFF_RULES,
    "tone": "profesional, cercano, conciso",
    "model": "gpt-4o-mini",
}

# Sensitive data patterns (Argentinian DNI, CBU, credit card-like)
_RX_SENSITIVE = re.compile(r"\b(\d{7,8})\b|\b(\d{22})\b|\b(\d{13,19})\b")
_RX_HUMAN_REQ = re.compile(r"\b(hablar con|quiero|necesito|pasame con|atender con|por favor)\b.{0,30}\b"
                            r"(humano|persona|vendedor|asesor|operador|representante|alguien real)\b",
                            re.IGNORECASE)


def normalize_lead_status(s: str | None) -> str:
    if not s: return "nuevo"
    return LEGACY_LEAD_MAP.get(s, s if s in LEAD_STATUSES else "nuevo")


def conversation_bot_should_run(conv: dict, bot_settings: dict | None = None) -> bool:
    if not conv.get("bot_enabled", True): return False
    bs = conv.get("bot_status")
    if bs in ("en_atencion_humana", "cerrada"): return False
    if conv.get("status") == "resolved": return False
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load_bot_settings(db) -> dict:
    doc = await db.bot_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
    return {**DEFAULT_BOT_SETTINGS, **doc}


async def regenerate_summary(db, conv_id: str, *, user_id: str | None = None) -> dict:
    msgs = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}) \
        .sort("created_at", -1).to_list(20)
    msgs.reverse()
    block = "\n".join(f"[{m.get('sender_type')}] {(m.get('body') or '')[:300]}" for m in msgs)
    try:
        parsed, raw = await call_llm_json(
            db=db,
            system_prompt=build_summary_only_prompt(),
            user_messages_block=block or "(sin mensajes)",
            model="gpt-4o-mini",
            purpose="summary_regen",
            conversation_id=conv_id,
            user_id=user_id,
        )
    except LLMUnavailable as e:
        return {"summary": "", "error": str(e)}
    summary = (parsed.get("summary") or "").strip()[:600]
    if summary:
        await db.conversations.update_one(
            {"id": conv_id},
            {"$set": {"summary": summary, "last_summary_at": _now_iso()}},
        )
    return {"summary": summary, "last_summary_at": _now_iso()}


async def suggest_reply(db, conv_id: str, *, user_id: str | None = None) -> dict:
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv: return {"draft": "", "confidence": 0.0, "error": "conv not found"}
    settings = await _load_bot_settings(db)
    # Merge global ai_provider.system_prompt_base into business_instructions
    ai_cfg = await ai_providers.load_settings(db)
    base = (ai_cfg.get("system_prompt_base") or "").strip()
    biz = settings.get("business_instructions") or ""
    merged_biz = (base + "\n\n" + biz).strip() if base else biz
    msgs = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}) \
        .sort("created_at", -1).to_list(settings["recent_messages_context_max"])
    msgs.reverse()
    block = "\n".join(f"[{m.get('sender_type')}] {(m.get('body') or '')[:300]}" for m in msgs)
    sp = build_system_prompt(tone=settings["tone"],
                             business_instructions=merged_biz,
                             faqs=settings["faqs"], handoff_rules=settings["handoff_rules"])
    try:
        parsed, _ = await call_llm_json(db=db, system_prompt=sp,
                                        user_messages_block=block,
                                        model=settings["model"],
                                        purpose="suggest_reply",
                                        conversation_id=conv_id,
                                        user_id=user_id)
    except LLMUnavailable as e:
        return {"draft": "", "confidence": 0.0, "error": str(e)}
    return {"draft": (parsed.get("reply") or "").strip(),
            "confidence": float(parsed.get("confidence") or 0.0),
            "intent": parsed.get("intent") or ""}


async def process_inbound(db, conv_id: str, triggered_by_message_id: str,
                          *, force: bool = False,
                          wa_send=None) -> dict:
    """Main pipeline. ``wa_send`` is a callable(conv, text) -> dict | raises.

    Returns the bot_event dict that was persisted.
    """
    # 1) Idempotency lock
    if not force and triggered_by_message_id:
        existing = await db.bot_events.find_one(
            {"triggered_by_message_id": triggered_by_message_id}, {"_id": 0})
        if existing:
            return existing

    event = {
        "event_id": uuid.uuid4().hex,
        "triggered_by_message_id": triggered_by_message_id,
        "conversation_id": conv_id,
        "created_at": _now_iso(),
        "status": "processing",
    }
    try:
        await db.bot_events.insert_one(dict(event))
    except Exception:  # dup key from sparse unique idx
        existing = await db.bot_events.find_one(
            {"triggered_by_message_id": triggered_by_message_id}, {"_id": 0})
        return existing or event

    try:
        conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
        if not conv:
            return await _finish(db, event, decision="no_action",
                                 reason="conversation not found")
        # Global AI provider settings (multi-provider config)
        ai_cfg = await ai_providers.load_settings(db)
        if not ai_cfg.get("ai_enabled", True):
            return await _finish(db, event, decision="no_action",
                                 reason="ia_desactivada")
        settings = await _load_bot_settings(db)

        # Guard: should bot run?
        if not conversation_bot_should_run(conv, settings):
            return await _finish(db, event, decision="no_action",
                                 reason="bot disabled or human handling")

        # Load context
        N = int(settings.get("recent_messages_context_max") or 12)
        msgs = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}) \
            .sort("created_at", -1).to_list(N)
        msgs.reverse()
        last_inbound = next((m for m in reversed(msgs) if m.get("sender_type") == "contact"), None)
        last_text = (last_inbound or {}).get("body", "") if last_inbound else ""

        # Pre-LLM hard rules
        forced_handoff_reason = None
        if _RX_SENSITIVE.search(last_text):
            forced_handoff_reason = "Datos sensibles detectados (DNI/CBU/tarjeta)"
        elif _RX_HUMAN_REQ.search(last_text):
            forced_handoff_reason = "El cliente solicit\u00f3 hablar con un humano"
        elif cs.detect_negotiation(last_text):
            # Hard rule: pricing/negotiation goes straight to a human, no LLM call.
            event["catalog_intent"] = "negotiation"
            human_reason = "Negociación o pricing — necesita asesor humano"
            await db.conversations.update_one(
                {"id": conv_id},
                {"$set": {"bot_enabled": False, "bot_status": "requiere_humano",
                          "human_required_reason": human_reason}},
            )
            return await _finish(db, event, decision="require_human",
                                 reason=human_reason)

        # Catalog hook — inject real product data when the message looks commercial
        cat_intent = cs.detect_commercial_intent(last_text)
        catalog_block = ""
        products_returned = 0
        if cat_intent["is_commercial"]:
            query = cs.extract_product_query(last_text)
            products = await cs.search_catalog(db, query, limit=5)
            products_returned = len(products)
            catalog_block = cs.format_catalog_for_llm(products)
        event["catalog_matched"] = bool(catalog_block)
        event["catalog_intent_type"] = cat_intent.get("intent_type")
        event["catalog_products_returned"] = products_returned
        event["raw_input_excerpt"] = (last_text or "")[:240]

        # Call LLM unless forced handoff (still call for summary)
        block = "\n".join(f"[{m.get('sender_type')}] {(m.get('body') or '')[:300]}" for m in msgs)
        base = (ai_cfg.get("system_prompt_base") or "").strip()
        biz = settings.get("business_instructions") or ""
        merged_biz = (base + "\n\n" + biz).strip() if base else biz
        if catalog_block:
            merged_biz = (merged_biz + "\n\n" + catalog_block).strip()
        sp = build_system_prompt(tone=settings["tone"],
                                 business_instructions=merged_biz,
                                 faqs=settings["faqs"],
                                 handoff_rules=settings["handoff_rules"])
        parsed: dict = {}
        raw = ""
        # Model override: emergent uses bot_settings.model; other providers use
        # the provider-configured model.
        model_override = settings["model"] if ai_cfg.get("provider") == "emergent" else None
        try:
            parsed, raw = await call_llm_json(db=db, system_prompt=sp,
                                              user_messages_block=block or "(sin mensajes)",
                                              model=model_override,
                                              purpose="bot_pipeline",
                                              conversation_id=conv_id,
                                              message_id=triggered_by_message_id)
        except LLMUnavailable as e:
            return await _finish(db, event, decision="no_action",
                                 reason=f"LLM no disponible: {e}",
                                 error_message=str(e), status="error")

        decision = parsed.get("decision") or "no_action"
        confidence = float(parsed.get("confidence") or 0.0)
        reply = (parsed.get("reply") or "").strip()
        summary_new = (parsed.get("summary") or "").strip()[:600]
        intent = (parsed.get("intent") or "").strip()
        nba = (parsed.get("next_best_action") or "").strip() or None
        bot_status_suggested = parsed.get("bot_status_suggested")
        lead_status_suggested = parsed.get("lead_status_suggested")
        evidence = (parsed.get("evidence_for_status_change") or "").strip()
        human_reason = (parsed.get("human_required_reason") or "").strip() or forced_handoff_reason

        # Force handoff overrides
        if forced_handoff_reason:
            decision = "require_human"
            reply = ""
            human_reason = forced_handoff_reason

        # Apply confidence floor: reply requires confidence >= threshold.
        # Prefer the global ai_provider.min_confidence_for_auto_reply if set,
        # fallback to bot_settings.confidence_threshold for backwards compat.
        thresh = float(
            ai_cfg.get("min_confidence_for_auto_reply")
            if ai_cfg.get("min_confidence_for_auto_reply") is not None
            else settings.get("confidence_threshold") or 0.70
        )
        if decision == "reply_with_bot" and confidence < thresh:
            decision = "require_human"
            human_reason = human_reason or f"Confianza baja ({confidence:.2f} < {thresh:.2f})"

        # Global WhatsApp auto-reply switch: degrade reply_with_bot → update_status_only
        if decision == "reply_with_bot" and not ai_cfg.get("whatsapp_auto_reply_enabled", True):
            event["auto_reply_suppressed"] = True
            decision = "update_status_only"
            reply = ""

        # Execute decision
        conv_set: dict[str, Any] = {"detected_intent": intent or None,
                                    "confidence": confidence,
                                    "next_best_action": nba}
        notif_payload = None

        if decision == "reply_with_bot" and reply and wa_send is not None:
            try:
                await wa_send(conv, reply)
                # persist bot outbound message
                await db.messages.insert_one({
                    "id": "msg_" + uuid.uuid4().hex[:12],
                    "conversation_id": conv_id,
                    "sender_type": "bot",
                    "sender_name": "Bot",
                    "body": reply,
                    "direction": "outbound",
                    "delivery_status": "sent",
                    "channel": conv.get("channel"),
                    "created_at": _now_iso(),
                })
                conv_set["last_message"] = reply
                conv_set["last_message_at"] = _now_iso()
                conv_set["bot_status"] = "esperando_cliente"
                event["decision"] = "reply_with_bot"
            except Exception as e:
                logger.exception("wa_send failed in bot pipeline")
                event["error_message"] = f"wa_send failed: {e}"
                event["status"] = "error"
                # notify admins of send failure
                notif_payload = ("handoff_required",
                                 "Bot no pudo enviar respuesta",
                                 f"Falla al enviar via WhatsApp: {e}")
                decision = "require_human"
                conv_set["bot_enabled"] = False
                conv_set["bot_status"] = "requiere_humano"
                conv_set["human_required_reason"] = "Bot no pudo enviar respuesta automática"
        elif decision == "require_human":
            if ai_cfg.get("auto_handoff_enabled", True):
                conv_set["bot_enabled"] = False
                conv_set["bot_status"] = "requiere_humano"
                conv_set["human_required_reason"] = human_reason or "Derivación solicitada por el bot"
            else:
                # Auto-handoff disabled — just notify, keep bot armed
                event["auto_handoff_suppressed"] = True
                conv_set["human_required_reason"] = human_reason or "Derivación sugerida por el bot"
            notif_payload = ("handoff_required",
                             f"Derivación a humano · {conv.get('contact_id','')}",
                             (conv_set.get("human_required_reason") or "")[:160])
            event["decision"] = "require_human"
        elif decision == "update_status_only":
            event["decision"] = "update_status_only"
        else:
            event["decision"] = "no_action"

        # Status changes (if evidence present)
        if evidence and bot_status_suggested in BOT_STATUSES \
                and bot_status_suggested != conv.get("bot_status"):
            event["bot_status_change"] = {"from": conv.get("bot_status"),
                                          "to": bot_status_suggested}
            conv_set["bot_status"] = bot_status_suggested
        if evidence and lead_status_suggested in LEAD_STATUSES:
            lead = await db.leads.find_one({"id": conv.get("lead_id")}, {"_id": 0}) if conv.get("lead_id") else None
            if lead and normalize_lead_status(lead.get("status")) != lead_status_suggested:
                event["lead_status_change"] = {"from": lead.get("status"),
                                               "to": lead_status_suggested}
                await db.leads.update_one({"id": lead["id"]},
                                          {"$set": {"status": lead_status_suggested,
                                                    "updated_at": _now_iso()}})

        # Summary
        if summary_new:
            conv_set["summary"] = summary_new
            conv_set["last_summary_at"] = _now_iso()

        # Persist conversation updates
        if conv_set:
            await db.conversations.update_one({"id": conv_id}, {"$set": conv_set})

        # Notifications
        if notif_payload:
            kind, title, msg = notif_payload
            target_uid = conv.get("assigned_to")
            if not target_uid:
                admins = await db.users.find(
                    {"role": {"$in": ["admin", "supervisor"]}, "active": True,
                     "deleted_at": None}, {"_id": 0, "user_id": 1}).to_list(20)
                targets = [a["user_id"] for a in admins] or [None]
            else:
                targets = [target_uid]
            for uid in targets:
                await db.notifications.insert_one({
                    "id": "ntf_" + uuid.uuid4().hex[:10],
                    "type": kind, "title": title, "message": msg,
                    "entity_type": "conversation", "entity_id": conv_id,
                    "assigned_user_id": uid, "priority": "high",
                    "read": False, "created_at": _now_iso(),
                })

        # Finalize event
        event.update({
            "model": settings["model"],
            "confidence": confidence,
            "intent": intent,
            "reply_text": reply if event.get("decision") == "reply_with_bot" else None,
            "summary_after": summary_new,
            "human_required_reason": human_reason,
            "next_best_action": nba,
            "raw_input_excerpt": block[-3000:],
            "raw_llm_response": (raw or "")[:8000],
            "status": event.get("status", "done"),
        })
        await db.bot_events.update_one({"event_id": event["event_id"]},
                                       {"$set": event}, upsert=True)
        return event
    except Exception as e:
        logger.exception("bot pipeline failed")
        event.update({"status": "error", "error_message": str(e)[:500],
                      "decision": "no_action"})
        await db.bot_events.update_one({"event_id": event["event_id"]},
                                       {"$set": event}, upsert=True)
        return event


async def _finish(db, event: dict, *, decision: str, reason: str,
                  error_message: str | None = None, status: str = "done") -> dict:
    event.update({"decision": decision, "human_required_reason": reason,
                  "status": status, "error_message": error_message})
    await db.bot_events.update_one({"event_id": event["event_id"]},
                                   {"$set": event}, upsert=True)
    return event
