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

## Backlog / Next
- P1: Real WhatsApp webhook ingestion (`/api/webhooks/whatsapp`) — backend already structured for it (messages/conversations model ready).
- P1: AI streaming (SSE) for summary/suggested reply.
- P2: Tags management UI, contact detail page, conversation auto-assignment rules.
- P2: Notifications for new inbound messages, reminders for due tasks.
- P2: Supervisor analytics (per-agent performance).
