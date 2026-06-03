import { useState, useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Send, Bot, User as UserIcon, Sparkles, Search, Phone, Building2,
  MessageSquare, Zap, Copy, RefreshCw, ArrowRightLeft, AlertOctagon,
  ChevronRight, Lightbulb,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { CONV_STATUSES, PRIORITIES, LEAD_STATUSES, money, statusMeta } from "@/lib/constants";
import { StatusBadge, Avatar } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export default function Inbox() {
  const qc = useQueryClient();
  const location = useLocation();
  const { user } = useAuth();
  const readOnly = user?.role === "viewer";
  const [activeId, setActiveId] = useState(location.state?.convId || null);

  useEffect(() => {
    if (location.state?.convId) setActiveId(location.state.convId);
  }, [location.state]);
  const [filters, setFilters] = useState({ status: "all", priority: "all" });
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const [suggestionDraft, setSuggestionDraft] = useState("");
  const [suggestionMeta, setSuggestionMeta] = useState(null); // {confidence, intent}
  const scrollRef = useRef(null);

  const params = {};
  Object.entries(filters).forEach(([k, v]) => { if (v !== "all") params[k] = v; });

  const { data: convs = [] } = useQuery({
    queryKey: ["conversations", filters],
    queryFn: () => api.get("/conversations", { params }).then((r) => r.data),
  });

  useEffect(() => {
    if (!activeId && convs.length) setActiveId(convs[0].id);
  }, [convs, activeId]);

  const { data: active } = useQuery({
    queryKey: ["conversation", activeId],
    queryFn: () => api.get(`/conversations/${activeId}`).then((r) => r.data),
    enabled: !!activeId,
  });

  useEffect(() => { setSuggestionDraft(""); setSuggestionMeta(null); }, [activeId]);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [active?.messages?.length]);

  const sendMsg = useMutation({
    mutationFn: async () => {
      const isWa = active?.channel === "whatsapp";
      const waConfigured = waStatus?.configured === true;
      if (isWa && waConfigured) {
        return api.post(`/conversations/${activeId}/send-whatsapp`, { text: draft });
      }
      return api.post(`/conversations/${activeId}/messages`, { body: draft, sender_type: "agent" });
    },
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (e) => {
      const detail = e?.response?.data?.detail;
      if (detail === "WhatsApp no configurado") toast.error("WhatsApp no configurado");
      else toast.error("No se pudo enviar el mensaje");
    },
  });

  // WhatsApp integration status (cheap; cached). Drives badges + composer mode.
  const { data: waStatus } = useQuery({
    queryKey: ["wa-status"],
    queryFn: () => api.get("/admin/whatsapp/status").then((r) => r.data).catch(() => ({ configured: false })),
    staleTime: 60_000,
  });

  const patchConv = useMutation({
    mutationFn: (body) => api.patch(`/conversations/${activeId}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["notif-count"] });
    },
  });

  // New AI bot endpoints (replace legacy ai-summary/ai-suggest UI). The legacy
  // backend endpoints stay intact for backwards compatibility — TODO: deprecate.
  const regenSummary = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/summary/regenerate`),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      if (r.data?.error) toast.error(`Resumen no disponible: ${r.data.error}`);
      else toast.success("Resumen regenerado");
    },
    onError: () => toast.error("No se pudo regenerar el resumen"),
  });
  const suggestReply = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/bot/suggest-reply`),
    onSuccess: (r) => {
      if (r.data?.error) {
        toast.error(`Sugerencia no disponible: ${r.data.error}`);
        setSuggestionDraft("");
        setSuggestionMeta(null);
        return;
      }
      setSuggestionDraft(r.data?.draft || "");
      setSuggestionMeta({
        confidence: r.data?.confidence ?? 0,
        intent: r.data?.intent || "",
      });
    },
    onError: () => toast.error("No se pudo generar la sugerencia"),
  });
  const reactivateBot = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/bot/reactivate`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      toast.success("Bot reactivado");
    },
    onError: () => toast.error("No se pudo reactivar el bot"),
  });

  const simulateInbound = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/simulate-inbound`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["notif-count"] });
      toast.success("Mensaje del cliente recibido");
    },
  });

  const filtered = convs.filter((c) => !search || c.contact?.name?.toLowerCase().includes(search.toLowerCase()));

  return (
    <AppLayout title="Bandeja">
      <div className="flex h-[calc(100vh-4rem)] w-full overflow-hidden bg-white">
        {/* Left: conversation list */}
        <div className="w-80 border-r border-zinc-200 flex flex-col bg-zinc-50 shrink-0">
          <div className="p-3 space-y-2 border-b border-zinc-200">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400" />
              <Input data-testid="inbox-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar chats…" className="pl-9 rounded-sm bg-white h-9" />
            </div>
            <div className="flex gap-2">
              <Select value={filters.status} onValueChange={(v) => setFilters({ ...filters, status: v })}>
                <SelectTrigger data-testid="inbox-filter-status" className="rounded-sm bg-white h-8 text-xs"><SelectValue placeholder="Estado" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todos los estados</SelectItem>
                  {CONV_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <Select value={filters.priority} onValueChange={(v) => setFilters({ ...filters, priority: v })}>
                <SelectTrigger data-testid="inbox-filter-priority" className="rounded-sm bg-white h-8 text-xs"><SelectValue placeholder="Prioridad" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todas las prioridades</SelectItem>
                  {PRIORITIES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex-1 overflow-auto">
            {filtered.map((c) => (
              <button
                key={c.id}
                onClick={() => setActiveId(c.id)}
                data-testid={`conv-item-${c.id}`}
                className={`w-full flex items-start gap-3 p-3 text-left border-b border-zinc-100 transition-colors ${activeId === c.id ? "bg-white border-l-2 border-l-[#FF4500]" : "hover:bg-white"}`}
              >
                <Avatar src={c.contact?.avatar} name={c.contact?.name} size={40} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-semibold text-sm text-[#0A0A0A] truncate">{c.contact?.name}</p>
                    {c.unread > 0 && <span className="bg-[#FF4500] text-white text-[10px] font-bold rounded-full h-4 min-w-4 px-1 flex items-center justify-center">{c.unread}</span>}
                  </div>
                  <p className="text-xs text-[#52525B] truncate mt-0.5">{c.last_message}</p>
                  <div className="flex items-center gap-1.5 mt-1.5">
                    {!c.bot_enabled
                      ? <span className="inline-flex items-center gap-1 text-[10px] font-bold text-[#FF4500] bg-[#FFF7ED] border border-[#FED7AA] rounded-full px-1.5 py-px"><UserIcon className="h-2.5 w-2.5" />HUMANO</span>
                      : <span className="inline-flex items-center gap-1 text-[10px] font-bold text-[#52525B] bg-zinc-100 rounded-full px-1.5 py-px"><Bot className="h-2.5 w-2.5" />BOT</span>}
                    <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: statusMeta(PRIORITIES, c.priority).color }} />
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Middle: thread */}
        <div className="flex-1 flex flex-col bg-white min-w-0">
          {!active ? (
            <div className="flex-1 flex flex-col items-center justify-center text-zinc-400">
              <MessageSquare className="h-10 w-10 mb-3" />
              <p className="text-sm">Seleccioná una conversación</p>
            </div>
          ) : (
            <>
              {/* header */}
              <div className="h-16 border-b border-zinc-200 flex items-center justify-between px-5 shrink-0">
                <div className="flex items-center gap-3">
                  <Avatar src={active.contact?.avatar} name={active.contact?.name} size={38} />
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-bold text-[#0A0A0A] leading-tight">{active.contact?.name}</p>
                      {active.channel === "whatsapp" && (
                        <span data-testid="channel-badge-whatsapp" className="inline-flex items-center gap-1 text-[10px] font-bold tracking-wide uppercase text-[#FF4500] bg-[#FFF7ED] border border-[#FED7AA] rounded-sm px-1.5 py-px">
                          WhatsApp
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-[#52525B]">{active.contact?.phone}</p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <button
                    data-testid="simulate-inbound-button"
                    onClick={() => simulateInbound.mutate()}
                    disabled={simulateInbound.isPending}
                    className="text-xs font-semibold text-[#52525B] hover:text-[#FF4500] border border-zinc-200 rounded-sm px-2.5 py-1.5 transition-colors"
                    title="Simular un mensaje entrante de WhatsApp del cliente"
                  >
                    + Respuesta del cliente
                  </button>
                  <div className={`flex items-center gap-2 px-3 py-1.5 rounded-sm border ${active.bot_enabled ? "border-zinc-200 bg-zinc-50" : "border-[#FED7AA] bg-[#FFF7ED]"}`} data-testid="handoff-control" title="Tomar control / Devolver al bot">
                    <ArrowRightLeft className="h-3.5 w-3.5 text-[#FF4500]" />
                    <span className="text-xs font-bold text-[#0A0A0A]">{active.bot_enabled ? "Bot activo" : "Solo humano"}</span>
                    <Switch
                      data-testid="bot-toggle"
                      checked={active.bot_enabled}
                      disabled={readOnly}
                      onCheckedChange={(v) => { patchConv.mutate({ bot_enabled: v }); toast.success(v ? "Bot reactivado" : "Traspasado a un agente"); }}
                      className="data-[state=checked]:bg-zinc-400 data-[state=unchecked]:bg-[#FF4500]"
                    />
                  </div>
                  <Select value={active.status} onValueChange={(v) => patchConv.mutate({ status: v })}>
                    <SelectTrigger data-testid="conv-status-select" className="w-32 rounded-sm h-9 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>{CONV_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </div>

              {/* messages */}
              <div ref={scrollRef} className="flex-1 overflow-auto p-5 space-y-3" style={{ background: "#FAFAF9" }} data-testid="message-thread">
                {active.messages?.map((m) => {
                  const isCustomer = m.sender_type === "contact";
                  const isBot = m.sender_type === "bot";
                  const isOutbound = !isCustomer;
                  const ds = m.delivery_status;
                  const dsLabel = ds === "sent" ? "Enviado"
                    : ds === "delivered" ? "Entregado"
                    : ds === "read" ? "Leído"
                    : ds === "failed" ? "Falló"
                    : null;
                  return (
                    <div key={m.id} className={`flex ${isCustomer ? "justify-start" : "justify-end"}`}>
                      <div className={`max-w-[70%] rounded-sm px-3.5 py-2 ${
                        isCustomer ? "bg-white border border-zinc-200"
                        : isBot ? "bg-zinc-100 border border-zinc-200"
                        : "bg-[#FF4500] text-white"}`}>
                        {!isCustomer && (
                          <div className={`flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide mb-0.5 ${isBot ? "text-[#FF4500]" : "text-orange-100"}`}>
                            {isBot ? <><Bot className="h-2.5 w-2.5" />Bot</> : <><UserIcon className="h-2.5 w-2.5" />{m.sender_name}</>}
                          </div>
                        )}
                        <p className={`text-sm ${isCustomer || isBot ? "text-[#0A0A0A]" : "text-white"}`}>{m.body}</p>
                        {isOutbound && dsLabel && (
                          <div
                            data-testid={`delivery-status-${m.id}`}
                            className={`text-[10px] font-semibold mt-1 text-right ${
                              ds === "failed"
                                ? "text-yellow-200"
                                : isBot ? "text-[#FF4500]" : "text-orange-100"
                            }`}
                          >
                            {dsLabel}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* composer */}
              <div className="border-t border-zinc-200 p-3 shrink-0">
                {!active.bot_enabled && (
                  <p className="text-[11px] font-semibold text-[#FF4500] mb-2 flex items-center gap-1"><UserIcon className="h-3 w-3" /> Traspaso a humano activo — estás respondiendo como agente</p>
                )}
                {active.channel === "whatsapp" && waStatus && !waStatus.configured && (
                  <p data-testid="wa-not-configured-banner" className="text-[11px] font-semibold text-[#FF4500] mb-2">
                    WhatsApp no configurado — el envío real está deshabilitado. Podés seguir usando &quot;+ Respuesta del cliente&quot; como simulador.
                  </p>
                )}
                <div className="flex items-end gap-2">
                  <Textarea
                    data-testid="message-input"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    disabled={readOnly}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!readOnly && draft.trim() && !(active.channel === "whatsapp" && waStatus && !waStatus.configured)) sendMsg.mutate(); } }}
                    placeholder={readOnly ? "Modo solo lectura" : (active.channel === "whatsapp" && waStatus?.configured ? "Escribí una respuesta de WhatsApp…" : "Escribí una respuesta…")}
                    className="rounded-sm resize-none min-h-[44px] max-h-32"
                  />
                  <Button
                    data-testid="send-message-button"
                    disabled={readOnly || !draft.trim() || sendMsg.isPending || (active.channel === "whatsapp" && waStatus && !waStatus.configured)}
                    onClick={() => sendMsg.mutate()}
                    className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm h-11 px-4"
                  >
                    <Send className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Right: lead info + AI */}
        {active && (
          <div className="w-80 border-l border-zinc-200 flex flex-col bg-zinc-50 shrink-0 overflow-auto">
            <div className="p-4 border-b border-zinc-200">
              <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#52525B] mb-3">Contacto</p>
              <div className="flex items-center gap-3 mb-3">
                <Avatar src={active.contact?.avatar} name={active.contact?.name} size={44} />
                <div className="min-w-0">
                  <p className="font-bold text-[#0A0A0A] truncate">{active.contact?.name}</p>
                  <p className="text-xs text-[#52525B] truncate">{active.contact?.company}</p>
                </div>
              </div>
              <div className="space-y-1.5 text-sm text-[#52525B]">
                <p className="flex items-center gap-2"><Phone className="h-3.5 w-3.5 text-[#FF4500]" /> {active.contact?.phone}</p>
                <p className="flex items-center gap-2"><Building2 className="h-3.5 w-3.5 text-[#FF4500]" /> {active.contact?.company}</p>
              </div>
            </div>

            {active.lead && (
              <div className="p-4 border-b border-zinc-200">
                <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#52525B] mb-3">Lead vinculado</p>
                <p className="font-semibold text-[#0A0A0A] text-sm">{active.lead.title}</p>
                <div className="flex items-center justify-between mt-2">
                  <StatusBadge list={LEAD_STATUSES} value={active.lead.status} />
                  <span className="font-extrabold tracking-tighter text-[#0A0A0A]">{money(active.lead.value)}</span>
                </div>
              </div>
            )}

            {/* AI Bot panel */}
            <BotPanel
              conv={active}
              readOnly={readOnly}
              regenSummary={regenSummary}
              suggestReply={suggestReply}
              reactivateBot={reactivateBot}
              suggestionDraft={suggestionDraft}
              setSuggestionDraft={setSuggestionDraft}
              suggestionMeta={suggestionMeta}
              clearSuggestion={() => { setSuggestionDraft(""); setSuggestionMeta(null); }}
              onUseDraft={(text) => { setDraft(text); toast.success("Sugerencia copiada al composer"); }}
            />
          </div>
        )}
      </div>
    </AppLayout>
  );
}

// ---------------------------------------------------------------------------
// Right-sidebar Bot IA panel
// ---------------------------------------------------------------------------
const BOT_STATUS_META = {
  bot_activo:        { label: "Bot activo",         color: "#16A34A", bg: "#DCFCE7", border: "#86EFAC" },
  esperando_cliente: { label: "Esperando cliente",  color: "#1D4ED8", bg: "#DBEAFE", border: "#93C5FD" },
  requiere_humano:   { label: "Requiere humano",    color: "#FF4500", bg: "#FFF7ED", border: "#FED7AA" },
  en_atencion_humana:{ label: "En atención humana", color: "#7C3AED", bg: "#EDE9FE", border: "#C4B5FD" },
  cerrada:           { label: "Cerrada",            color: "#52525B", bg: "#F4F4F5", border: "#D4D4D8" },
};

function BotStatusPill({ status }) {
  const m = BOT_STATUS_META[status] || BOT_STATUS_META.bot_activo;
  return (
    <span
      data-testid={`bot-status-pill-${status || "bot_activo"}`}
      className="inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide px-2 py-1 rounded-sm border"
      style={{ color: m.color, background: m.bg, borderColor: m.border }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: m.color }} />
      {m.label}
    </span>
  );
}

function BotPanel({
  conv, readOnly, regenSummary, suggestReply, reactivateBot,
  suggestionDraft, setSuggestionDraft, suggestionMeta, clearSuggestion, onUseDraft,
}) {
  const summary = conv?.summary || "";
  const botStatus = conv?.bot_status || (conv?.bot_enabled ? "bot_activo" : "en_atencion_humana");
  const intent = conv?.detected_intent || "";
  const confidence = conv?.confidence;
  const reason = (conv?.human_required_reason || "").trim();
  const nba = (conv?.next_best_action || "").trim();
  const confidencePct = typeof confidence === "number" && confidence > 0
    ? `${Math.round(confidence * 100)}%`
    : null;

  return (
    <div data-testid="bot-panel" className="p-4 border-b border-zinc-200 space-y-4">
      <p className="flex items-center gap-1.5 text-xs tracking-[0.15em] uppercase font-bold text-[#FF4500]">
        <Sparkles className="h-3.5 w-3.5" /> Asistente IA
      </p>

      {/* Bot status */}
      <div>
        <p className="text-[10px] font-bold tracking-[0.12em] uppercase text-[#52525B] mb-1.5">Estado del bot</p>
        <div className="flex items-center justify-between gap-2">
          <BotStatusPill status={botStatus} />
          {!conv?.bot_enabled && (
            <Button
              data-testid="bot-reactivate-button"
              size="sm"
              disabled={readOnly || reactivateBot.isPending}
              onClick={() => reactivateBot.mutate()}
              className="bg-[#0A0A0A] hover:bg-[#FF4500] rounded-sm h-7 text-xs"
            >
              {reactivateBot.isPending ? <RefreshCw className="h-3 w-3 animate-spin mr-1" /> : <Bot className="h-3 w-3 mr-1" />}
              Reactivar bot
            </Button>
          )}
        </div>
      </div>

      {/* Intent */}
      {(intent || confidencePct) && (
        <div>
          <p className="text-[10px] font-bold tracking-[0.12em] uppercase text-[#52525B] mb-1.5">Intención detectada</p>
          <div className="flex items-center justify-between gap-2" data-testid="bot-intent-block">
            <span className="text-sm font-semibold text-[#0A0A0A]">{intent || "—"}</span>
            {confidencePct && (
              <span data-testid="bot-confidence-value" className="text-xs font-bold text-[#52525B] bg-zinc-100 border border-zinc-200 rounded-sm px-1.5 py-0.5">
                {confidencePct}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Handoff reason — only when present */}
      {reason && (
        <div data-testid="bot-handoff-reason" className="border border-[#FED7AA] bg-[#FFF7ED] rounded-sm p-2.5">
          <p className="flex items-center gap-1 text-[10px] font-bold tracking-[0.12em] uppercase text-[#FF4500] mb-1">
            <AlertOctagon className="h-3 w-3" /> Motivo de derivación
          </p>
          <p className="text-xs text-[#0A0A0A]">{reason}</p>
        </div>
      )}

      {/* Next best action */}
      {nba && (
        <div data-testid="bot-next-action">
          <p className="text-[10px] font-bold tracking-[0.12em] uppercase text-[#52525B] mb-1">Próxima acción sugerida</p>
          <p className="text-xs text-[#0A0A0A] flex items-start gap-1.5">
            <ChevronRight className="h-3.5 w-3.5 text-[#FF4500] mt-0.5 shrink-0" /> {nba}
          </p>
        </div>
      )}

      {/* Summary */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <p className="text-[10px] font-bold tracking-[0.12em] uppercase text-[#52525B]">Resumen</p>
          <button
            data-testid="bot-summary-regenerate"
            onClick={() => regenSummary.mutate()}
            disabled={readOnly || regenSummary.isPending}
            className="text-[11px] font-semibold text-[#FF4500] flex items-center gap-1 disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${regenSummary.isPending ? "animate-spin" : ""}`} /> Regenerar
          </button>
        </div>
        <div
          data-testid="bot-summary-output"
          className="border border-zinc-200 bg-white rounded-sm p-2.5 min-h-[60px] text-sm text-[#0A0A0A] whitespace-pre-wrap"
        >
          {regenSummary.isPending
            ? <span className="text-[#52525B] animate-pulse">Analizando la conversación…</span>
            : summary
              ? summary
              : <span className="text-[#52525B]">Aún no hay resumen. Tocá Regenerar para crearlo.</span>}
        </div>
        {conv?.last_summary_at && (
          <p className="text-[10px] text-[#52525B] mt-1">Actualizado: {new Date(conv.last_summary_at).toLocaleString("es-AR")}</p>
        )}
      </div>

      {/* Suggested reply */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <p className="flex items-center gap-1 text-[10px] font-bold tracking-[0.12em] uppercase text-[#52525B]">
            <Lightbulb className="h-3 w-3 text-[#FF4500]" /> Sugerencia de respuesta
          </p>
          <button
            data-testid="bot-suggest-button"
            onClick={() => suggestReply.mutate()}
            disabled={readOnly || suggestReply.isPending}
            className="text-[11px] font-semibold text-[#FF4500] flex items-center gap-1 disabled:opacity-50"
          >
            {suggestReply.isPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
            Sugerir
          </button>
        </div>
        {suggestionDraft ? (
          <div className="border border-[#FED7AA] bg-[#FFF7ED] rounded-sm p-2.5">
            <Textarea
              data-testid="bot-suggestion-textarea"
              value={suggestionDraft}
              onChange={(e) => setSuggestionDraft(e.target.value)}
              className="rounded-sm bg-white text-sm min-h-[80px] mb-2"
              disabled={readOnly}
            />
            {suggestionMeta && (
              <div className="flex items-center gap-2 text-[10px] text-[#52525B] mb-2">
                {suggestionMeta.intent && <span>Intención: <b className="text-[#0A0A0A]">{suggestionMeta.intent}</b></span>}
                {suggestionMeta.confidence > 0 && (
                  <span>· Confianza: <b className="text-[#0A0A0A]">{Math.round(suggestionMeta.confidence * 100)}%</b></span>
                )}
              </div>
            )}
            <div className="flex gap-2">
              <Button
                data-testid="bot-suggestion-use"
                size="sm"
                disabled={readOnly || !suggestionDraft.trim()}
                onClick={() => onUseDraft(suggestionDraft)}
                className="bg-[#0A0A0A] hover:bg-[#FF4500] rounded-sm h-7 text-xs flex-1"
              >
                <Copy className="h-3 w-3 mr-1" /> Copiar al input
              </Button>
              <Button
                data-testid="bot-suggestion-discard"
                size="sm"
                variant="outline"
                onClick={clearSuggestion}
                className="rounded-sm h-7 text-xs"
              >
                Descartar
              </Button>
            </div>
          </div>
        ) : (
          <div className="border border-zinc-200 bg-zinc-50 rounded-sm p-2.5 text-xs text-[#52525B]">
            Generá un borrador editable con la IA basado en el contexto actual.
          </div>
        )}
      </div>
    </div>
  );
}
