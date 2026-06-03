# FlowDesk — WhatsApp Sales CRM · PRD

## Original Problem Statement
Full-stack CRM web app for managing WhatsApp sales conversations: auth, dashboard metrics, leads, contacts, WhatsApp-style inbox, pipeline kanban, tasks/reminders, internal notes, lead & conversation status tracking, bot-to-human handoff, AI conversation summary + suggested reply, and admin/supervisor/sales-agent roles. Structured so real WhatsApp webhooks can be added later.

## User Choices
- Auth: Emergent-managed Google OAuth
- AI features: REAL LLM (Claude Sonnet 4.6 via Emergent Universal Key)
- Demo data: seeded
- Branding: agent-chosen (Swiss high-contrast + Electric Orange #FF4500)
- Admin panel: full user/role management

## Architecture
- Backend: FastAPI (`/app/backend/server.py`), MongoDB (motor). UUID string ids, `_id` excluded everywhere. Auth via httpOnly cookie OR Bearer token. AI via `emergentintegrations` LlmChat (anthropic/claude-sonnet-4-6).
- Frontend: React 19 + CRA/craco, Tailwind, shadcn/ui, react-query, recharts, framer-motion, sonner. `@/` alias.
- Entities: users, contacts, leads, conversations, messages, tasks, notes, tags, bot_events, settings.

## User Personas
- Admin: manages team/roles, full access, reseed demo data.
- Supervisor: oversees pipeline & team (no admin panel).
- Sales Agent: handles leads, chats, tasks.

## Implemented (2026-06-02)
- Google OAuth (first real user → admin), session management, RBAC (admin-only user mgmt, 403 enforced).
- Dashboard: pipeline value, leads-by-stage chart, conversion rate, open/pending chats, human-handled, open tasks, recent conversations.
- Leads: list + filters (status/priority/assigned), create, detail drawer (status/priority/value/owner edit, tasks, internal notes), delete.
- Contacts: grid, search, create (linked to leads).
- Inbox: 3-panel WhatsApp UI — chat list w/ filters, message thread (bot/agent/customer bubbles), send as agent, bot↔human handoff toggle (logs bot_event), conversation status; right panel: contact + linked lead + REAL AI summary + REAL AI suggested reply (Use/Copy).
- Pipeline: drag-and-drop kanban across 6 lead stages with per-column totals.
- Tasks: create, due date, priority, assignment, complete toggle, todo/done tabs.
- Admin: team table, role change, activate/deactivate, regenerate demo data.
- Seed: 8 contacts/leads/conversations, 29 messages, 3 demo team users + admin.
- **In-app Notifications (2026-06-02)**: notifications model + types (new_message, handoff_required, overdue_task, task_due_soon, lead_no_response); header bell with unread count + dropdown; click-to-navigate (conversation/lead/task) + mark read; mark-all-read; new_message on inbound (assigned user or admins/supervisors fallback); handoff_required on bot off; dashboard generates overdue/due-soon task notifs idempotently; conversation unread counters; dashboard "Requires Attention" section (handoffs/unread/overdue); simulate-inbound demo endpoint+button.
- Tested: 27/27 backend pass (incl. 10 notification tests), all frontend flows pass (iteration_1 & iteration_2).

## Lead-no-response automation + Español + rebrand (2026-06-02)
- Renombrado a **Latus CRM**; UI 100% en español (nav, badges, notificaciones, dashboard, admin, inbox, prompts de IA en español).
- Nuevo tipo `lead_no_response`: settings (`lead_no_response_enabled`, `lead_no_response_threshold_hours` default 2, `lead_no_response_business_hours_only` placeholder) vía GET/PATCH `/api/settings` (PATCH solo admin).
- `scan_lead_no_response()` idempotente: notifica cuando el último mensaje es del cliente, sin respuesta de bot/humano, conversación no resuelta, lead no ganado/perdido, y supera el umbral; asigna al responsable o admins/supervisores. Corre en el dashboard y vía POST `/api/automations/lead-no-response/scan`.
- Dashboard "Requiere atención" ahora incluye columna "Lead sin respuesta". Panel admin con tarjeta de configuración del umbral.
- Tests backend: `/app/backend/tests/test_lead_no_response.py` (17 casos, 16/16 efectivos pass) cubriendo los 6 escenarios requeridos. iteration_3.json.

## Asistente de Ventas IA sobre WhatsApp (2026-06-03)
- Nueva colección `bot_events` (idempotencia via índice único sparse en `triggered_by_message_id`) y `bot_settings` (doc `_id: default`) con: `bot_enabled_default`, `confidence_threshold`, `recent_messages_context_max`, `business_instructions`, `handoff_rules`, `faqs[]`, `tone`, `model` (gpt-4o-mini default).
- Campos nuevos en `conversations`: `summary`, `last_summary_at`, `bot_status` (bot_activo|esperando_cliente|requiere_humano|en_atencion_humana|cerrada), `detected_intent`, `human_required_reason`, `next_best_action`, `confidence`. Campo nuevo en `messages`: `sender_type="bot"`.
- Pipeline (`backend/ai/pipeline.py`): `process_inbound()` ejecutado en BackgroundTasks desde el webhook de WhatsApp; idempotente; clasifica intención + confianza + decisión (`reply`, `require_human`, `update_status`, `no_action`); auto-handoff cuando `confidence < threshold` o detecta DNI/CBU/tarjeta; envía notificación `handoff_required` al asignado o broadcast a admins/supervisores; actualiza resumen.
- Endpoints nuevos (todos /api): `POST /conversations/{id}/bot/process` (?force=true), `POST /conversations/{id}/summary/regenerate`, `POST /conversations/{id}/bot/reactivate`, `POST /conversations/{id}/bot/suggest-reply`, `GET/PATCH /admin/bot-settings`. PATCH valida threshold ∈ [0,1], ctx_max ∈ [3,50], y modelo en {gpt-4o-mini, gpt-4o}.
- LLM via Emergent Universal Key (`EMERGENT_LLM_KEY`) + `emergentintegrations.LlmChat.with_params(max_tokens=900, temperature=0.2)`. Cualquier excepción de la llamada se envuelve como `LLMUnavailable` y el endpoint responde 200 con `error` legible — sin 500.
- Frontend Inbox `BotPanel` (sidebar derecho): pill `bot_status` con colores (verde/azul/naranja/violeta/gris), intención detectada + confianza %, motivo de derivación (sólo si existe), próxima acción, resumen + Regenerar, sugerencia editable con Copiar al input / Descartar, botón Reactivar bot (sólo si bot_enabled=false). Mensajes con `sender_type="bot"` muestran badge "Bot" naranja.
- Frontend `/configuracion` Tab "Bot IA" (admin): toggle default, select modelo, slider de confianza con valor numérico, ctx_max con validación cliente (toast rojo en español + cancela PATCH), tono, instrucciones del negocio, reglas de derivación, editor FAQ add/remove (Pregunta/Respuesta). Botones Guardar/Descartar. Read-only para `viewer`.
- Tests: `/app/backend/tests/test_ai_bot.py` 10/10 pass (idempotencia, low-confidence handoff con notificación broadcast, sensitive-data handoff, role gating, model whitelist, bot_settings GET/PATCH, etc.). Total suite 60/60 (excluyendo tests de integración pre-existentes que ya estaban rojos antes de esta feature). iteration_4.json verde tanto en backend HTTP como en flujos UI.

## Backlog / Next
- P1: Real WhatsApp webhook ingestion (`/api/webhooks/whatsapp`) — backend already structured for it (messages/conversations model ready).
- P1: AI streaming (SSE) for summary/suggested reply.
- P2: Tags management UI, contact detail page, conversation auto-assignment rules.
- P2: Notifications for new inbound messages, reminders for due tasks.
- P2: Supervisor analytics (per-agent performance).
