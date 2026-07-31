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
    "tone_dialect": "cordobes",
    "response_length_limit": "conciso",
    "writing_rules": {
        "only_closing_punctuation": True,
        "allow_slang": True,
        "custom_rules_text": "",
    },
    "company_workflow_steps": [
        "Saludar amablemente en tono cercano y entender la consulta inicial del cliente",
        "Calificar requerimientos clave (presupuesto, ubicación o tipo de producto)",
        "Ofrecer 1 o 2 opciones del catálogo de la empresa que coincidan con sus intereses",
        "Proponer agendar una cita o llamada con un asesor humano para concretar",
    ],
    "custom_client_fields": [
        {"key": "presupuesto", "label": "Presupuesto estimado", "description": "Monto o rango proyectado"},
        {"key": "zona_interes", "label": "Zona de preferencia", "description": "Ubicación o barrio buscado"},
        {"key": "plazo_compra", "label": "Plazo estimado de compra", "description": "Tiempo proyectado para concretar"},
    ],
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
    "whatsapp_recontact_templates": [],
    "appointment_reminders_enabled": False,
    "appointment_reminder_minutes_before": 1440,
    "appointment_reminder_templates": [],
    "appointment_reminder_template_id": None,
    "appointment_rescheduling_enabled": True,
    "webchat_enabled": True,
    "webchat_auto_invite_whatsapp": False,
    "webchat_title": "Asistente Latus",
    "webchat_welcome_message": "¡Hola! ¿En qué puedo ayudarte hoy?",
    "webchat_primary_color": "#0E8DDB",
    "webchat_position": "right",
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
        if contact_doc.get("custom_fields"):
            cf_parts = [f"{k}: {v}" for k, v in contact_doc["custom_fields"].items() if v]
            if cf_parts:
                info_lines.append(f"- Ficha Personalizada Cliente: {', '.join(cf_parts)}")
    if lead_doc:
        if lead_doc.get("status"):
            info_lines.append(f"- Estado del Lead: {lead_doc['status']}")
        if lead_doc.get("value"):
            info_lines.append(f"- Valor estimado: {lead_doc['value']}")
        if lead_doc.get("custom_fields"):
            lead_cf_parts = [f"{k}: {v}" for k, v in lead_doc["custom_fields"].items() if v]
            if lead_cf_parts:
                info_lines.append(f"- Ficha Personalizada Lead: {', '.join(lead_cf_parts)}")
    
    return "\n".join(info_lines) if info_lines else None


async def suggest_reply(db, conv_id: str, *, user_id: str | None = None) -> dict:
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv: return {"draft": "", "confidence": 0.0, "error": "conv not found"}
    settings = await _load_bot_settings(db)
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
                             company_context=merged_biz,
                             response_instructions=settings.get("response_instructions") or "",
                             faqs=settings["faqs"], handoff_rules=settings["handoff_rules"],
                             bot_name=settings.get("bot_name", "Bot"),
                             client_info=client_info,
                             tone_dialect=settings.get("tone_dialect") or "cordobes",
                             response_length_limit=settings.get("response_length_limit") or "conciso",
                             writing_rules=settings.get("writing_rules"),
                             company_workflow_steps=settings.get("company_workflow_steps"),
                             custom_client_fields=settings.get("custom_client_fields"))
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
    except Exception:
        existing = await db.bot_events.find_one(
            {"triggered_by_message_id": triggered_by_message_id}, {"_id": 0})
        return existing or event

    try:
        conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
        if not conv:
            return await _finish(db, event, decision="no_action",
                                 reason="conversation not found")
        ai_cfg = await ai_providers.load_settings(db)
        if not ai_cfg.get("ai_enabled", True):
            return await _finish(db, event, decision="no_action",
                                 reason="ia_desactivada")
        settings = await _load_bot_settings(db)
        work_areas = await db.work_areas.find({}, {"_id": 0}).to_list(100)

        if not conversation_bot_should_run(conv, settings):
            return await _finish(db, event, decision="no_action",
                                 reason="bot disabled or human handling")

        N = int(settings.get("recent_messages_context_max") or 12)
        msgs = await db.messages.find({"conversation_id": conv_id}, {"_id": 0}) \
            .sort("created_at", -1).to_list(N)
        msgs.reverse()
        last_inbound = next((m for m in reversed(msgs) if m.get("sender_type") == "contact"), None)
        last_text = (last_inbound or {}).get("body", "") if last_inbound else ""

        forced_handoff_reason = None
        if _RX_SENSITIVE.search(last_text):
            forced_handoff_reason = "Datos sensibles detectados (DNI/CBU/tarjeta)"
        elif _RX_HUMAN_REQ.search(last_text):
            forced_handoff_reason = "El cliente solicitó hablar con un humano"
        elif cs.detect_negotiation(last_text):
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

        session_token = conv.get("webchat_session_token")
        if not session_token:
            session_token = f"cw_{uuid.uuid4().hex[:16]}"
            await db.conversations.update_one({"id": conv_id}, {"$set": {"webchat_session_token": session_token}})

        resp_instr = settings.get("response_instructions") or ""
        if conv.get("channel") == "whatsapp" and settings.get("webchat_auto_invite_whatsapp"):
            import os
            app_url = os.environ.get("PUBLIC_APP_URL", "http://localhost:3000")
            invite_url = f"{app_url}/c/{session_token}"
            resp_instr += f"\n\nINVITACIÓN AL CHAT WEB (REDUCCIÓN DE COSTOS): Invitá amablemente al cliente a ingresar a su chat web interactivo sin límites ni demoras usando este link: {invite_url}"

        if conv.get("channel") == "webchat":
            contact = await db.contacts.find_one({"id": conv.get("contact_id")}, {"_id": 0}) or {}
            phone = contact.get("phone", "")
            if not phone or "cnt_" in phone or len(phone) < 8 or not phone.replace("+", "").isdigit():
                resp_instr += (
                    "\n\nREGISTRO DE TELÉFONO (CHAT WEB): Al saludar o al avanzar en la consulta, "
                    "pedile amablemente su número de WhatsApp/Teléfono al cliente para registrar su legajo único "
                    "y enviarle el resumen de atención al finalizar la charla."
                )

        # For webchat: always auto-reply regardless of WhatsApp auto_reply setting
        is_webchat = conv.get("channel") == "webchat"
        auto_reply_enabled = True if is_webchat else ai_cfg.get("whatsapp_auto_reply_enabled", True)

        sp = build_system_prompt(
            tone=settings["tone"],
            company_context=merged_cc,
            response_instructions=resp_instr,
            faqs=settings["faqs"],
            handoff_rules=settings["handoff_rules"],
            bot_name=settings.get("bot_name", "Bot"),
            client_info=client_info,
            auto_reply_enabled=auto_reply_enabled,
            work_areas=work_areas,
            appointment_context=appointment_context if appointment_context else None,
            tone_dialect=settings.get("tone_dialect") or "cordobes",
            response_length_limit=settings.get("response_length_limit") or "conciso",
            writing_rules=settings.get("writing_rules"),
            company_workflow_steps=settings.get("company_workflow_steps"),
            custom_client_fields=settings.get("custom_client_fields"),
        )
        parsed: dict = {}
        raw = ""
        bot_provider = ai_cfg.get("provider", "built_in")
        bot_model = ai_cfg.get("model", "gpt-4o-mini")
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

        # Process extracted client profile data (Ficha Personalizada)
        extracted_profile = parsed.get("extracted_client_profile")
        if isinstance(extracted_profile, dict) and extracted_profile:
            clean_profile = {k: str(v) for k, v in extracted_profile.items() if v is not None and str(v).strip()}
            if clean_profile:
                event["extracted_client_profile"] = clean_profile
                cid = conv.get("contact_id")
                if cid:
                    sets = {f"custom_fields.{k}": v for k, v in clean_profile.items()}
                    await db.contacts.update_one({"id": cid}, {"$set": sets})
                lid = conv.get("lead_id")
                if lid:
                    lead_sets = {f"custom_fields.{k}": v for k, v in clean_profile.items()}
                    await db.leads.update_one({"id": lid}, {"$set": lead_sets})

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

        if forced_handoff_reason:
            decision = "require_human"
            reply = ""
            human_reason = forced_handoff_reason

        thresh = float(
            ai_cfg.get("min_confidence_for_auto_reply")
            if ai_cfg.get("min_confidence_for_auto_reply") is not None
            else settings.get("confidence_threshold") or 0.70
        )
        if decision == "reply_with_bot" and confidence < thresh:
            decision = "require_human"
            human_reason = human_reason or f"Confianza baja ({confidence:.2f} < {thresh:.2f})"

        if decision == "reply_with_bot" and not ai_cfg.get("whatsapp_auto_reply_enabled", True):
            event["auto_reply_suppressed"] = True
            decision = "update_status_only"
            reply = ""

        conv_set: dict[str, Any] = {"detected_intent": intent or None,
                                    "confidence": confidence,
                                    "next_best_action": nba}
        notif_payload = None

        if decision == "reply_with_bot" and reply and wa_send is not None:
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
        elif decision == "reply_with_bot" and reply and wa_send is None and conv.get("channel") == "webchat":
            # Web chat channel: no WhatsApp send needed, persist reply directly to DB
            await db.messages.insert_one({
                "id": "msg_" + uuid.uuid4().hex[:12],
                "conversation_id": conv_id,
                "sender_type": "bot",
                "sender_name": settings.get("bot_name", "Bot"),
                "body": reply,
                "direction": "outbound",
                "delivery_status": "sent",
                "channel": "webchat",
                "created_at": _now_iso(),
            })
            conv_set["last_message"] = reply
            conv_set["last_message_at"] = _now_iso()
            conv_set["bot_status"] = "esperando_cliente"
            event["decision"] = "reply_with_bot"
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
                    handoff_uid = settings.get("default_handoff_user_id")
                    if handoff_uid:
                        conv_set["assigned_to"] = handoff_uid
                    if conv.get("lead_id"):
                        await db.leads.update_one({"id": conv["lead_id"]}, {"$set": {"assigned_to": handoff_uid}})
            else:
                event["auto_handoff_suppressed"] = True
                conv_set["human_required_reason"] = human_reason or "Derivación sugerida por el bot"
            notif_payload = ("handoff_required",
                             f"Derivación a humano · {conv.get('contact_id','')}",
                             (conv_set.get("human_required_reason") or "")[:160])
            event["decision"] = "require_human"
            event["human_required_reason"] = human_reason or conv_set.get("human_required_reason")
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
                        "conversation_id": conv_id,
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
                    from utils.appointment_reminders import reminder_fields
                    appt_doc.update(reminder_fields(appt_doc, settings, reset_status=True))
                    await db.appointments.insert_one(appt_doc)
                    event["appointment_created"] = appt_doc["id"]
                    event["appointment_resource_id"] = slot["resource_id"]
                    if mode == "people" and assigned_to and not conv.get("assigned_to"):
                        conv_set["assigned_to"] = assigned_to
                    
                    cname = client_info.splitlines()[0] if client_info else "un cliente"
                    notif_payload = (
                        "appointment_created",
                        f"Nueva cita agendada: {cname}",
                        f"El bot agendó una cita para el {start_dt.astimezone(ZoneInfo(slot['timezone'])).strftime('%d/%m %H:%M')}"
                    )
                    
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
        elif decision == "reschedule_appointment":
            event["decision"] = "reschedule_appointment"
            appointment_id = parsed.get("appointment_id")
            appt_start = parsed.get("appointment_start_time")
            if not settings.get("appointment_rescheduling_enabled", True):
                event["error_message"] = "reschedule failed: la reprogramación automática está desactivada"
            elif appointment_id and appt_start and settings.get("appointment_scheduling_enabled"):
                try:
                    from datetime import timedelta
                    from zoneinfo import ZoneInfo
                    from utils.appointment_reminders import reminder_fields
                    from utils.scheduling import (
                        get_business_service,
                        get_person_availability,
                        parse_datetime,
                        validate_appointment_slot,
                    )
                    appointment = await db.appointments.find_one({
                        "id": appointment_id,
                        "contact_id": conv.get("contact_id"),
                        "event_type": "appointment",
                        "status": "scheduled",
                    }, {"_id": 0})
                    if not appointment:
                        raise ValueError("No se encontró un turno próximo de este cliente con ese ID")
                    mode = appointment.get("scheduling_mode") or settings.get("appointment_mode") or "people"
                    assigned_to = appointment.get("assigned_to")
                    service_id = appointment.get("service_id") if mode == "business" else None
                    if mode == "business":
                        resource = get_business_service(settings, service_id)
                        timezone_name = resource["timezone"]
                    else:
                        _, resource = await get_person_availability(db, assigned_to, settings)
                        timezone_name = resource["timezone"]
                    old_start = parse_datetime(appointment["start_time"], timezone_name)
                    old_end = parse_datetime(appointment["end_time"], timezone_name)
                    duration = max(5, int((old_end - old_start).total_seconds() // 60))
                    new_start = parse_datetime(appt_start, timezone_name)
                    new_end = new_start + timedelta(minutes=duration)
                    slot = await validate_appointment_slot(
                        db,
                        settings,
                        start_time=new_start,
                        end_time=new_end,
                        mode=mode,
                        assigned_to=assigned_to,
                        service_id=service_id,
                        exclude_appointment_id=appointment_id,
                    )
                    update = {
                        "start_time": slot["start_time"],
                        "end_time": slot["end_time"],
                        "updated_at": _now_iso(),
                        "updated_by": "bot",
                        "rescheduled_at": _now_iso(),
                        "rescheduled_by_bot": True,
                        "conversation_id": conv_id,
                    }
                    update.update(reminder_fields({**appointment, **update}, settings, reset_status=True))
                    await db.appointments.update_one({"id": appointment_id}, {"$set": update})
                    event["appointment_rescheduled"] = appointment_id
                    event["appointment_resource_id"] = slot["resource_id"]
                    cname = client_info.splitlines()[0] if client_info else "un cliente"
                    notif_payload = (
                        "appointment_rescheduled",
                        f"Turno reprogramado: {cname}",
                        f"El bot movió el turno al {new_start.astimezone(ZoneInfo(slot['timezone'])).strftime('%d/%m %H:%M')}",
                    )
                    if reply and wa_send is not None:
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
                    logger.exception("Failed to reschedule appointment")
                    event["error_message"] = f"reschedule failed: {e}"
            else:
                event["error_message"] = "reschedule failed: faltan el turno o el nuevo horario"
        elif decision == "confirm_appointment":
            event["decision"] = "confirm_appointment"
            appointment_id = parsed.get("appointment_id")
            if appointment_id:
                try:
                    appointment = await db.appointments.find_one({
                        "id": appointment_id,
                        "contact_id": conv.get("contact_id"),
                        "event_type": "appointment",
                        "status": "scheduled",
                    }, {"_id": 0})
                    if not appointment:
                        raise ValueError("No se encontró un turno próximo de este cliente con ese ID")
                    confirmed_at = _now_iso()
                    await db.appointments.update_one(
                        {"id": appointment_id},
                        {"$set": {
                            "confirmation_status": "confirmed",
                            "confirmed_at": confirmed_at,
                            "confirmation_conversation_id": conv_id,
                            "updated_at": confirmed_at,
                            "updated_by": "bot",
                        }},
                    )
                    event["appointment_confirmed"] = appointment_id
                    cname = client_info.splitlines()[0] if client_info else "un cliente"
                    notif_payload = (
                        "appointment_confirmed",
                        f"Turno confirmado: {cname}",
                        "El cliente confirmó su asistencia al turno",
                    )
                    if reply and wa_send is not None:
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
                            "created_at": confirmed_at,
                        })
                        conv_set["last_message"] = reply
                        conv_set["last_message_at"] = confirmed_at
                except Exception as e:
                    logger.exception("Failed to confirm appointment")
                    event["error_message"] = f"confirmation failed: {e}"
            else:
                event["error_message"] = "confirmation failed: falta el turno"
        else:
            event["decision"] = "no_action"

        if evidence and bot_status_suggested in BOT_STATUSES \
                and bot_status_suggested != conv.get("bot_status"):
            event["bot_status_change"] = {"from": conv.get("bot_status"),
                                          "to": bot_status_suggested}
            conv_set["bot_status"] = bot_status_suggested
        if evidence and lead_status_suggested in LEAD_STATUSES:
            lead = await db.leads.find_one({"id": conv.get("lead_id")}, {"_id": 0}) if conv.get("lead_id") else None
            if lead and normalize_lead_status(lead.get("status")) != lead_status_suggested:
                db_status = SPANISH_TO_ENGLISH_STATUS.get(lead_status_suggested, lead_status_suggested)
                lead_update = {"status": db_status, "updated_at": _now_iso()}
                try:
                    if lead.get("status") != "won" and db_status == "won":
                        from utils.sales import close_sale
                        lead_update.update(await close_sale(
                            db,
                            lead,
                            lead.get("products") or [],
                            user_id=conv.get("assigned_to"),
                        ))
                    elif lead.get("status") == "won" and db_status != "won":
                        from utils.sales import reverse_sale
                        lead_update["sale_snapshot"] = await reverse_sale(
                            db,
                            lead.get("sale_snapshot"),
                            user_id=conv.get("assigned_to"),
                        )
                    await db.leads.update_one({"id": lead["id"]}, {"$set": lead_update})
                    event["lead_status_change"] = {
                        "from": lead.get("status"), "to": lead_status_suggested
                    }
                except Exception as exc:
                    logger.warning("Sale close/status change blocked for lead=%s: %s", lead.get("id"), exc)
                    event["sale_close_blocked"] = True
                    event["error_message"] = f"sale close blocked: {exc}"

        if summary_new:
            conv_set["summary"] = summary_new
            conv_set["last_summary_at"] = _now_iso()

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

        if notif_payload:
            kind, title, msg = notif_payload
            target_uid = conv_set.get("assigned_to") or conv.get("assigned_to")
            
            targets = []
            assigned_wa = conv_set.get("assigned_work_area") or conv.get("assigned_work_area")
            if assigned_wa:
                try:
                    from utils.tenancy import get_organization_id
                    all_active = await db.memberships.find({
                        "organization_id": get_organization_id(),
                        "status": "active",
                    }, {"_id": 0}).to_list(500)
                    active_uids = [m["user_id"] for m in all_active]
                except Exception:
                    active_uids = []
                
                wa_coll = getattr(db, "work_area_members", None)
                if wa_coll is not None and active_uids:
                    wa_members = await wa_coll.find({
                        "work_area_id": assigned_wa,
                        "user_id": {"$in": active_uids}
                    }, {"_id": 0}).to_list(500)
                    targets = [m["user_id"] for m in wa_members]
                
                if not targets:
                    user_coll = getattr(db, "users", None)
                    if user_coll is not None:
                        wa_users = await user_coll.find({
                            "work_areas": assigned_wa,
                            "active": True
                        }, {"_id": 0}).to_list(500)
                        targets = [u["user_id"] for u in wa_users if u.get("user_id")]
            
            if not targets and target_uid:
                targets = [target_uid]
            
            if not targets:
                user_coll = getattr(db, "users", None)
                if user_coll is not None:
                    all_users = await user_coll.find({"active": True}, {"_id": 0}).to_list(500)
                    targets = [u["user_id"] for u in all_users if u.get("user_id")]
            
            if not targets:
                targets = [target_uid or "unassigned"]

            for uid in targets:
                if uid:
                    await db.notifications.insert_one({
                        "id": f"notif_{uuid.uuid4().hex[:12]}",
                        "type": kind,
                        "title": title,
                        "message": msg,
                        "user_id": uid,
                        "assigned_user_id": uid,
                        "is_read": False,
                        "link": f"/conversaciones?id={conv_id}",
                        "created_at": _now_iso(),
                    })

        return await _finish(db, event, decision=decision, status="completed")

    except Exception as e:
        logger.exception("bot pipeline error for conv=%s", conv_id)
        return await _finish(db, event, decision="no_action",
                             reason=f"Excepción interna: {e}",
                             error_message=str(e), status="error")


async def _finish(db, event: dict, *, decision: str, reason: str = "",
                  error_message: str = "", status: str = "completed") -> dict:
    event["status"] = status
    event["decision"] = decision
    if reason:
        event["reason"] = reason
        event["human_required_reason"] = reason
    if error_message: event["error_message"] = error_message
    event["completed_at"] = _now_iso()
    await db.bot_events.update_one(
        {"event_id": event["event_id"]}, {"$set": event})
    return event
