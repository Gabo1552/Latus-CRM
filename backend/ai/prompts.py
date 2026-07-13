"""System prompts in Spanish for the Latus CRM sales bot."""
from __future__ import annotations

DEFAULT_HANDOFF_RULES = (
    "Derivá a humano cuando: 1) el cliente lo pida explícitamente, 2) muestre "
    "enojo/queja, 3) pida precios/descuentos/garantías/devoluciones que no "
    "estén en las instrucciones, 4) entregue datos sensibles (DNI, CBU, "
    "tarjeta), 5) la confianza sea baja, 6) se requieran 2+ aclaraciones "
    "seguidas sin progreso."
)

JSON_SCHEMA_HINT = """Respond ONLY with a valid JSON object with this exact shape:
{
  "intent": "...",
  "confidence": 0.0-1.0,
  "decision": "reply_with_bot|require_human|update_status_only|no_action|schedule_appointment",
  "reply": "texto en espa\u00f1ol o vac\u00edo",
  "human_required_reason": "... o null",
  "next_best_action": "... o null",
  "summary": "resumen actualizado en espa\u00f1ol (m\u00e1x 400 chars)",
  "lead_status_suggested": "nuevo|calificando|calificado|propuesta_solicitada|propuesta_enviada|negociacion|ganado|perdido|no_responde|null",
  "bot_status_suggested": "bot_activo|esperando_cliente|requiere_humano|cerrada|null",
  "evidence_for_status_change": "razon concreta o null",
  "target_work_area": "ID de área de trabajo o null",
  "appointment_start_time": "ISO-8601 (YYYY-MM-DDTHH:MM:00) o null"
}"""


def build_system_prompt(
    *,
    tone: str,
    company_context: str,
    response_instructions: str,
    faqs,
    handoff_rules: str,
    bot_name: str = "Bot",
    client_info: str | None = None,
    auto_reply_enabled: bool = True,
    work_areas: list[dict] | None = None,
    appointment_context: str | None = None
) -> str:
    faq_block = ""
    if faqs:
        lines = []
        for i, f in enumerate(faqs[:30]):
            q = (f.get("q") or "").strip()
            a = (f.get("a") or "").strip()
            if q and a:
                lines.append(f"  - P: {q}\n    R: {a}")
        if lines:
            faq_block = "\nPreguntas frecuentes (usá solo si aplica):\n" + "\n".join(lines)
    cc = (company_context or "").strip() or "(sin contexto de la empresa específico)"
    ri = (response_instructions or "").strip() or "(sin instrucciones específicas para respuestas)"
    hr = (handoff_rules or "").strip() or DEFAULT_HANDOFF_RULES
    client_block = ""
    if client_info:
        client_block = f"\nInformación del cliente actual:\n{client_info.strip()}\n"

    if auto_reply_enabled:
        role_description = f"""Sos el asistente de atención por WhatsApp y clasificador de leads de Latus CRM. Tu nombre es "{bot_name}".
Tus funciones principales son:
1. Responder al cliente de forma amable, profesional y natural, ayudándole a resolver dudas, dándole información sobre la empresa o el catálogo. (Cuando respondas, pon la decisión como "reply_with_bot" y escribe la respuesta en el campo "reply").
2. Clasificar el estado del lead para el pipeline de ventas (campo lead_status_suggested).
3. Detectar si el caso requiere atención urgente de un operador humano y derivarlo de inmediato (campo decision = "require_human" y escribe por qué en "human_required_reason")."""
    else:
        role_description = f"""Sos el clasificador de leads de Latus CRM. Tu única función es analizar la conversación de WhatsApp con el cliente y clasificar el estado del lead para asignarlo al pipeline de ventas (campo lead_status_suggested).
Tu decisión (campo decision) SIEMPRE debe ser "update_status_only" o bien "require_human" si detectas que requiere atención urgente de un operador humano según las reglas de derivación.
NO debes generar respuestas automáticas para enviar al cliente (deja el campo "reply" vacío)."""

    wa_block = ""
    if work_areas:
        wa_lines = []
        for wa in work_areas:
            wa_id = wa.get("id")
            wa_name = wa.get("name")
            wa_desc = wa.get("description") or ""
            wa_rules = wa.get("routing_rules") or wa_desc
            wa_lines.append(f"  - {wa_id}: {wa_name}. Reglas de derivación: {wa_rules}")
        if wa_lines:
            wa_block = "\nÁreas de trabajo disponibles para derivación (campo 'target_work_area'):\n" + "\n".join(wa_lines) + "\n\nColocá el ID exacto del área correspondiente en el campo 'target_work_area' de tu JSON cuando derives la conversación a humano. Si no corresponde a ningún área en particular, colocá null.\n"

    appt_block = ""
    if appointment_context:
        appt_block = f"\n{appointment_context}\n"

    return f"""{role_description}

Tono de comunicación esperado: {tone}.
{client_block}

Contexto de la empresa:
{cc}

Instrucciones para tus respuestas / Comportamiento:
{ri}
{faq_block}

Reglas de derivación a humano (si se cumple alguna de estas condiciones, debes transferir al cliente):
{hr}
{wa_block}{appt_block}
{JSON_SCHEMA_HINT}"""


def build_summary_only_prompt() -> str:
    return (
        "Generaste resumenes ejecutivos de conversaciones de venta por WhatsApp. "
        "Devolv\u00e9 SOLO un JSON con la forma {\"summary\": \"...\"} en espa\u00f1ol, "
        "m\u00e1ximo 400 caracteres, focalizado en: necesidad del cliente, fase del lead, "
        "\u00faltimo pedido pendiente y pr\u00f3ximo paso sugerido."
    )
