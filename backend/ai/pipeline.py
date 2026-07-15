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
LEGACY_LEAD_MAP = {
    "new": "nuevo",
    "contacted": "calificando",
    "qualifying": "calificando",
    "qualified": "calificado",
    "proposal": "propuesta_enviada",
    "won": "ganado",
    "lost": "perdido"
}
SPANISH_TO_ENGLISH_STATUS = {
    "nuevo": "new",
    "calificando": "contacted",
    "calificado": "qualified",
    "propuesta_solicitada": "proposal",
    "propuesta_enviada": "proposal",
    "negociacion": "proposal",
    "ganado": "won",
    "perdido": "lost",
    "no_responde": "lost"
}

DEFAULT_BOT_SETTINGS = {
    "bot_enabled_default": True,
    "confidence_threshold": 0.70,
    "recent_messages_context_max": 12,
    "business_instructions": "",
    "faqs": [],
    "handoff_rules": DEFAULT_HANDOFF_RULES,
    "tone": "profesional, cercano, conciso",
    "provider": "built_in",
    "model": "gpt-4o-mini",
    "bot_name": "Bot",
    "include_client_info": True,
    "default_handoff_user_id": None,
    "company_context": "",
    "response_instructions": "",
    "catalog_reading_enabled": True,
    "bot_inactive_close_hours": 48,
    "appointment_scheduling_enabled": False,
    "appointment_available_days": [1, 2, 3, 4, 5],  # 1=Monday, 5=Friday
    "appointment_business_hours": "09:00-18:00",
    "appointment_duration_minutes": 30,
    "appointment_mode": "people",
    "appointment_timezone": "America/Argentina/Buenos_Aires",
    "appointment_services": [],
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
    ai_cfg = await ai_providers.load_settings(db)
    global_provider = ai_cfg.get("provider", "built_in")
    global_model = ai_cfg.get("model", "gpt-4o-mini")
    base = (ai_cfg.get("system_prompt_base") or "").strip()
    instructions = build_summary_only_prompt()
    system_prompt = (base + "\n\n" + instructions).strip() if base else instructions
    try:
        parsed, raw = await call_llm_json(
            db=db,
            system_prompt=system_prompt,
            user_messages_block=block or "(sin mensajes)",
            provider=global_provider,
            model=global_model,
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
 
 
async def _compile_client_info(db, conv, settings: dict) -> str | None:
    if not settings.get("include_client_info", True) or not conv:
        return None
    contact_doc = await db.contacts.find_one({"id": conv.get("contact_id")})
    lead_doc = await db.leads.find_one({"id": conv.get("lead_id")}) if conv.get("lead_id") else None
    
    info_lines = []
    if contact_doc:
        if contact_doc.get("name"):
            info_lines.append(f"- Nombre: {contact_doc['name']}")
        if contact_doc.get("phone"):
            info_lines.append(f"- Teléfono: {contact_doc['phone']}")
        if contact_doc.get("email"):
            info_lines.append(f"- Email: {contact_doc['email']}")
        if contact_doc.get("company"):
            info_lines.append(f"- Empresa: {contact_doc['company']}")
        if contact_doc.get("notes"):
            info_lines.append(f"- Notas en CRM: {contact_doc['notes']}")
    if lead_doc:
        if lead_doc.get("status"):
            info_lines.append(f"- Estado del Lead: {lead_doc['status']}")
        if lead_doc.get("value"):
            info_lines.append(f"- Valor estimado: {lead_doc['value']}")
    
    return "\n".join(info_lines) if info_lines else None


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
    client_info = await _compile_client_info(db, conv, settings)
    sp = build_system_prompt(tone=settings["tone"],
                             business_instructions=merged_biz,
                             faqs=settings["faqs"], handoff_rules=settings["handoff_rules"],
                             bot_name=settings.get("bot_name", "Bot"),
                             client_info=client_info)
    asst_provider = ai_cfg.get("provider", "built_in")
    asst_model = ai_cfg.get("model", "gpt-4o-mini")
    try:
        parsed, _ = await call_llm_json(db=db, system_prompt=sp,
                                        user_messages_block=block,
                                        provider=asst_provider,
                                        model=asst_model,
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
        work_areas = await db.work_areas.find({}, {"_id": 0}).to_list(100)

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
            await db.messages.insert_one({
                "id": f"msg_{uuid.uuid4().hex[:12]}",
                "conversation_id": conv_id,
                "sender_type": "system",
                "sender_name": "Sistema",
                "body": f"Control humano activado - Derivación automática: {human_reason}",
                "created_at": _now_iso(),
                "direction": "outbound",
                "delivery_status": "sent",
                "message_type": "text",
                "channel": "whatsapp",
            })
            return await _finish(db, event, decision="require_human",
                                 reason=human_reason)

        # Catalog hook — inject real product data when the message looks commercial and catalog reading is enabled
        cat_intent = cs.detect_commercial_intent(last_text)
        catalog_block = ""
        products_returned = 0
        if settings.get("catalog_reading_enabled", True) and cat_intent["is_commercial"]:
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
        cc = settings.get("company_context") or settings.get("business_instructions") or ""
        merged_cc = (base + "\n\n" + cc).strip() if base else cc
        if catalog_block:
            merged_cc = (merged_cc + "\n\n" + catalog_block).strip()
        client_info = await _compile_client_info(db, conv, settings)
        appointment_context = ""
        if settings.get("appointment_scheduling_enabled"):
            from utils.scheduling import build_appointment_context
            appointment_context = await build_appointment_context(db, conv, settings)

        sp = build_system_prompt(tone=settings["tone"],
                                 company_context=merged_cc,
                                 response_instructions=settings.get("response_instructions") or "",
                                 faqs=settings["faqs"],
                                 handoff_rules=settings["handoff_rules"],
                                 bot_name=settings.get("bot_name", "Bot"),
                                 client_info=client_info,
                                 auto_reply_enabled=ai_cfg.get("whatsapp_auto_reply_enabled", True),
                                 work_areas=work_areas,
                                 appointment_context=appointment_context if appointment_context else None)
        parsed: dict = {}
        raw = ""
        bot_provider = settings.get("provider", "built_in")
        bot_model = settings.get("model", "gpt-4o-mini")
        try:
            parsed, raw = await call_llm_json(db=db, system_prompt=sp,
                                              user_messages_block=block or "(sin mensajes)",
                                              provider=bot_provider,
                                              model=bot_model,
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

        # Enforce classification-only if whatsapp_auto_reply_enabled is False
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
            # This block won't execute because of the suppression above, kept for structure
            try:
                await wa_send(conv, reply)
                await db.messages.insert_one({
                    "id": "msg_" + uuid.uuid4().hex[:12],
                    "conversation_id": conv_id,
                    "sender_type": "bot",
                    "sender_name": settings.get("bot_name", "Bot"),
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
                target_work_area = parsed.get("target_work_area")
                if target_work_area:
                    conv_set["assigned_work_area"] = target_work_area
                    conv_set["assigned_to"] = None
                else:
                    # Route handoff to the configured default user if specified
                    handoff_uid = settings.get("default_handoff_user_id")
                    if handoff_uid:
                        conv_set["assigned_to"] = handoff_uid
                    if conv.get("lead_id"):
                        await db.leads.update_one({"id": conv["lead_id"]}, {"$set": {"assigned_to": handoff_uid}})
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
        elif decision == "schedule_appointment":
            event["decision"] = "schedule_appointment"
            appt_start = parsed.get("appointment_start_time")
            if appt_start and settings.get("appointment_scheduling_enabled"):
                try:
                    from datetime import timedelta
                    from zoneinfo import ZoneInfo
                    from utils.scheduling import (
                        appointment_duration_minutes,
                        get_business_service,
                        get_person_availability,
                        parse_datetime,
                        validate_appointment_slot,
                    )
                    mode = settings.get("appointment_mode") or "people"
                    assigned_to = parsed.get("appointment_assigned_to") or conv.get("assigned_to")
                    service_id = parsed.get("appointment_service_id") if mode == "business" else None
                    if mode == "business":
                        resource = get_business_service(settings, service_id)
                        timezone_name = resource["timezone"]
                    else:
                        _, resource = await get_person_availability(db, assigned_to, settings)
                        timezone_name = resource["timezone"]
                    duration = await appointment_duration_minutes(
                        db,
                        settings,
                        mode=mode,
                        assigned_to=assigned_to,
                        service_id=service_id,
                    )
                    start_dt = parse_datetime(appt_start, timezone_name)
                    end_dt = start_dt + timedelta(minutes=duration)
                    slot = await validate_appointment_slot(
                        db,
                        settings,
                        start_time=start_dt,
                        end_time=end_dt,
                        mode=mode,
                        assigned_to=assigned_to,
                        service_id=service_id,
                    )

                    appt_doc = {
                        "id": "appt_" + uuid.uuid4().hex[:12],
                        "contact_id": conv.get("contact_id"),
                        "lead_id": conv.get("lead_id"),
                        "title": (
                            f"{slot['resource_name']} · {client_info.splitlines()[0]}"
                            if mode == "business" and client_info
                            else f"Cita con {client_info.splitlines()[0] if client_info else 'Cliente'}"
                        ),
                        "event_type": "appointment",
                        "start_time": slot["start_time"],
                        "end_time": slot["end_time"],
                        "status": "scheduled",
                        "assigned_to": assigned_to,
                        "scheduling_mode": mode,
                        "service_id": service_id,
                        "service_name": slot["resource_name"] if mode == "business" else None,
                        "created_by_bot": True,
                        "created_at": _now_iso(),
                    }
                    await db.appointments.insert_one(appt_doc)
                    event["appointment_created"] = appt_doc["id"]
                    event["appointment_resource_id"] = slot["resource_id"]
                    if mode == "people" and assigned_to and not conv.get("assigned_to"):
                        conv_set["assigned_to"] = assigned_to
                    
                    # Send notification to assigned agent or generic
                    target_uid = conv.get("assigned_to")
                    cname = client_info.splitlines()[0] if client_info else "un cliente"
                    notif_payload = (
                        "appointment_created",
                        f"Nueva cita agendada: {cname}",
                        f"El bot agendó una cita para el {start_dt.astimezone(ZoneInfo(slot['timezone'])).strftime('%d/%m %H:%M')}"
                    )
                    
                    # If we need to send a confirmation reply
                    if reply and wa_send is not None:
                        try:
                            await wa_send(conv, reply)
                            await db.messages.insert_one({
                                "id": "msg_" + uuid.uuid4().hex[:12],
                                "conversation_id": conv_id,
                                "sender_type": "bot",
                                "sender_name": settings.get("bot_name", "Bot"),
                                "body": reply,
                                "direction": "outbound",
                                "delivery_status": "sent",
                                "channel": conv.get("channel"),
                                "created_at": _now_iso(),
                            })
                            conv_set["last_message"] = reply
                            conv_set["last_message_at"] = _now_iso()
                        except Exception as e:
                            logger.exception("wa_send failed for appointment confirmation")
                except Exception as e:
                    logger.exception("Failed to schedule appointment")
                    event["error_message"] = f"schedule failed: {e}"
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
                db_status = SPANISH_TO_ENGLISH_STATUS.get(lead_status_suggested, lead_status_suggested)
                event["lead_status_change"] = {"from": lead.get("status"),
                                               "to": lead_status_suggested}
                await db.leads.update_one({"id": lead["id"]},
                                          {"$set": {"status": db_status,
                                                    "updated_at": _now_iso()}})

        # Summary
        if summary_new:
            conv_set["summary"] = summary_new
            conv_set["last_summary_at"] = _now_iso()

        # Persist conversation updates
        if conv_set:
            await db.conversations.update_one({"id": conv_id}, {"$set": conv_set})
            if conv_set.get("bot_enabled") is False and conv.get("bot_enabled", True) is True:
                reason = conv_set.get("human_required_reason") or "Derivación automática"
                await db.messages.insert_one({
                    "id": f"msg_{uuid.uuid4().hex[:12]}",
                    "conversation_id": conv_id,
                    "sender_type": "system",
                    "sender_name": "Sistema",
                    "body": f"Control humano activado - Derivación automática: {reason}",
                    "created_at": _now_iso(),
                    "direction": "outbound",
                    "delivery_status": "sent",
                    "message_type": "text",
                    "channel": "whatsapp",
                })

        # Notifications
        if notif_payload:
            kind, title, msg = notif_payload
            target_uid = conv_set.get("assigned_to") or conv.get("assigned_to")
            
            targets = []
            assigned_wa = conv_set.get("assigned_work_area") or conv.get("assigned_work_area")
            if assigned_wa:
                all_active = await db.users.find({
                    "active": True,
                    "deleted_at": None
                }, {"_id": 0, "user_id": 1, "work_areas": 1}).to_list(100)
                targets = [
                    u["user_id"] for u in all_active
                    if u.get("work_areas") and assigned_wa in u["work_areas"]
                ]
            
            if not targets:
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
