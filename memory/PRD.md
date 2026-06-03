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

## Fix regresión simulate-inbound + POST /messages (2026-06-03)
- **Bug**: `POST /api/conversations/{id}/simulate-inbound` y `POST /api/conversations/{id}/messages` devolvían 500 con `ValueError: [TypeError("'ObjectId' object is not iterable")]` desde `fastapi.encoders.jsonable_encoder`. Causa raíz: Motor muta el dict de entrada de `insert_one` agregando `_id: ObjectId(...)`, y el endpoint devolvía ese mismo dict. FastAPI no sabe serializar `ObjectId`.
- **Fix**: nuevo helper `_strip_oid(doc)` en `backend/server.py` (justo después de `new_id()`); aplicado en los 3 sitios afectados: `_handle_inbound_message` (línea ~1771), `send_message` (POST /messages, línea ~1703), y el WhatsApp outbound (línea ~1610). Idempotente y minimalista — no toca tests reales que no inyectan `_id`.
- **Idempotencia bot**: `simulate-inbound` ahora genera siempre un `external_message_id = sim_<uuid16>` para que el lock de `bot_events.triggered_by_message_id` (índice único sparse) funcione correctamente y dos llamadas seguidas no disparen dos respuestas del bot.
- **Tests**: nuevo archivo `backend/tests/test_simulate_inbound.py` con 8 casos que reproducen exactamente el bug (FakeDB que mutea con `ObjectId`-proxy en `insert_one`), validan response 200 + JSON serializable + dedup + last_message_at + notificación + idempotencia del bot. Total suite: **68/68 pass**.
- **Verificación E2E**: flujo demo end-to-end probado en Inbox — el cliente simulado dispara una respuesta real del bot en español con `intent=seguimiento`, `confidence=0.8`, `bot_status=esperando_cliente`.

## Fase 1 — Configuración multi-proveedor de IA (2026-06-03)
- **Backend** `backend/ai/providers.py`: abstracción `AIProvider` + 6 implementaciones (`EmergentProvider`, `OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`, `OpenRouterProvider`, `CustomOpenAIProvider`). Factory `get_provider(db)`. Cliente unificado expone `chat(system_prompt, user_block, json_mode)` → `ChatResult{content, model, prompt_tokens, completion_tokens, latency_ms, provider}`. `httpx.AsyncClient` para HTTP, errores nunca exponen la api_key (incluye scrub del query-string de Gemini). Settings storage en `app_secrets._id="ai_provider"`, api_key Fernet-cifrada vía `utils/crypto.py` (mismo mecanismo que WhatsApp).
- **Endpoints** (admin only): `GET /api/admin/ai-provider` (devuelve config con `api_key_masked: "••••XYZ"`, nunca el valor); `PUT /api/admin/ai-provider` (valida proveedor/modelo/rangos; cifra y guarda key; `api_key: null` la borra); `POST /api/admin/ai-provider/test` (1 llamada de prueba, mide latencia).
- **Pipeline (`ai/pipeline.py`)**: respeta los flags globales — `ai_enabled=false` ⇒ `decision="no_action"` sin llamar al LLM; `whatsapp_auto_reply_enabled=false` ⇒ degrada `reply_with_bot` a `update_status_only` (no envía mensaje pero actualiza intent/summary); `auto_handoff_enabled=false` ⇒ `require_human` ya no cierra la conversación, solo crea notificación; `min_confidence_for_auto_reply` (global) tiene prioridad sobre `bot_settings.confidence_threshold` (compat). `system_prompt_base` se prepende a `business_instructions`. Model: para provider=emergent usa `bot_settings.model`; para los demás usa `ai_provider.model`.
- **LLM wrapper** `ai/llm.py`: reducido a un thin re-export que enruta a `providers.get_provider()`. Mantiene `call_llm_json` y `LLMUnavailable` para compatibilidad.
- **Frontend** `Configuracion.jsx`: nueva tab `IA y automatización` (4ta tab, admin only). Componente `AIAutoTab` con: toggle IA activa, select proveedor (6 opciones en español), input modelo con datalist de sugerencias, input API Key (password) con estado masked + botón Limpiar, URL base (solo para custom_openai), slider Temperatura 0–2 con valor visible, input Max tokens 100–4096, slider Confianza mínima 0–1 con valor visible, toggles auto-reply y auto-handoff, textarea Prompt base, botón "Probar IA" con toast verde/rojo + latencia, botones Guardar/Descartar. Validación cliente con toasts rojos en español cuando algún rango está fuera o falta API key. Read-only inferido del shell admin.
- **Tests** `backend/tests/test_ai_provider.py`: 11 casos (GET fresco, PUT con key + masking, PUT clear, validación de rangos, base_url obligatoria para custom_openai, RBAC 403 agent/viewer, `/test` mockeado con httpx 200/401 sin leak de key, pipeline ai_enabled=false ⇒ no_action, pipeline auto_reply=false ⇒ update_status_only). Suite completa: **79/79 PASS** (68 anteriores + 11 nuevos).
- **Verificación real**: `POST /admin/ai-provider/test` → `{ok:true, latency_ms:719, model:"gpt-4o-mini", content_preview:'{"ok":true,"echo":"latus"}'}`. Simulate-inbound → bot real responde con `intent=consulta_llamada`, `confidence=0.9`, `bot_status=requiere_humano` (decision real del LLM).

## Fase 2 — Registro de consumo y gastos de IA (2026-06-03)
- **Nuevo** `backend/ai/usage.py`: helpers `load_pricing/save_pricing/reset_pricing`, `estimate_cost(model, prompt, completion, pricing?)`, `log_usage(...)` (best-effort, nunca propaga), `call_with_logging(db, provider, ...)` que envuelve `provider.chat()` con timing + persistencia automática del doc en `ai_usage_logs`. Defaults de pricing (USD por 1M tokens): gpt-4o-mini 0.150/0.600, gpt-4o 2.5/10, claude-3-5-sonnet 3/15, claude-3-5-haiku 0.8/4, gemini-1.5-pro 1.25/5, gemini-1.5-flash 0.075/0.3. Modelos desconocidos → cost 0 + warning una sola vez.
- **Colección** `ai_usage_logs` con campos: `log_id`, `created_at`, `provider`, `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `estimated_cost_usd`, `latency_ms`, `status`, `error_message` (truncado 500 chars, scrubeado de api_key), `conversation_id`, `message_id`, `user_id`, `purpose` (`bot_pipeline`/`summary_regen`/`suggest_reply`/`connection_test`). Índices: `(created_at desc, status)`, `model`, `conversation_id` (sparse).
- **Pipeline + endpoints** ahora propagan `purpose`/`conversation_id`/`message_id`/`user_id` al provider via `call_llm_json(...)`. El test endpoint también registra como `connection_test`.
- **5 endpoints nuevos** (admin only): `GET /api/admin/ai-usage/summary` (filtros `from`/`to`/`model`/`status`, agregados `by_model`/`by_day`/`top_conversations`), `GET /api/admin/ai-usage/logs` (paginación 1–200, sorted desc por fecha), `GET /api/admin/ai-usage/quick` (today/this_month/all_time/top_model), `GET /api/admin/ai-pricing`, `PUT /api/admin/ai-pricing` (un modelo por request, valida ≥0), `POST /api/admin/ai-pricing/reset`. RBAC: agent/viewer → 403. Rango inválido (`from > to`) → 400.
- **Frontend** nueva ruta `/consumo-ia` (admin only, redirige a `/dashboard` si no es admin). Sidebar ahora muestra item "Consumo de IA" (icono $) entre Admin y Configuración. Página completa con: 4 cards (Hoy / Este mes / Total acumulado / Modelo más usado), filtros de rango/modelo/estado con validación inline de fechas, tablas Por modelo / Por día (con barras inline), Top conversaciones (clickeable a `/inbox`), Logs detallados paginados (50 por página, con tooltip de error truncado, deep-link a la conversación), editor inline de precios con botón Restaurar valores por defecto. Toda en español.
- **Tests** `backend/tests/test_ai_usage.py` (12 casos): cost calc exacto (`0.45` para 1M+0.5M en gpt-4o-mini), success log con tokens reales, modelo desconocido → cost 0, error log con scrub de api_key, paginación, quick con porcentajes, pricing GET/PUT round-trip que afecta estimate_cost, validación negativos 400, RBAC 403, rango de fechas inválido 400, pipeline end-to-end genera log con `purpose=bot_pipeline` + conv_id + msg_id. FakeDB en tests/`test_simulate_inbound.py` ahora honra proyección `{"_id": 0}` para imitar Mongo. **Suite scope: 91/91 PASS**.
- **Verificación E2E live**: `POST /conversations/conv_cc7b7c3cc240/simulate-inbound` → log nuevo `bot_pipeline gpt-4o-mini conv: conv_cc7b7c3cc240 msg: sim_b1c9f4bbb8ed41f6 status: success` visible inmediatamente en `/consumo-ia` con todas las tablas pobladas. Tokens=0/cost=0 esperado para provider=emergent (la SDK de emergentintegrations no expone `usage`); con provider=openai directo los tokens serían reales.

## Backlog / Next
- P1: Real WhatsApp webhook ingestion (`/api/webhooks/whatsapp`) — backend already structured for it (messages/conversations model ready).
- P1: AI streaming (SSE) for summary/suggested reply.
- P2: Tags management UI, contact detail page, conversation auto-assignment rules.
- P2: Notifications for new inbound messages, reminders for due tasks.
- P2: Supervisor analytics (per-agent performance).
