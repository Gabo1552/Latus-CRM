import { Fragment, useState, useEffect, useMemo, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Send, Bot, User as UserIcon, Sparkles, Search, Phone, Building2,
  MessageSquare, Zap, Copy, RefreshCw, ArrowRightLeft, AlertOctagon,
  ChevronRight, FileText, Lightbulb,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { hasPermission } from "@/lib/permissions";
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
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const messageDate = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const messageDayKey = (value) => {
  const date = messageDate(value);
  if (!date) return null;
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
};

const formatMessageDay = (value) => {
  const date = messageDate(value);
  if (!date) return "";
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (messageDayKey(date) === messageDayKey(today)) return "Hoy";
  if (messageDayKey(date) === messageDayKey(yesterday)) return "Ayer";
  const formatted = date.toLocaleDateString("es-AR", {
    weekday: "long",
    day: "numeric",
    month: "long",
    ...(date.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}),
  });
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
};

const formatMessageTime = (value) => {
  const date = messageDate(value);
  return date ? date.toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" }) : "";
};

const formatMessageFullDate = (value) => {
  const date = messageDate(value);
  return date ? date.toLocaleString("es-AR", { dateStyle: "full", timeStyle: "short" }) : "";
};

export default function Inbox() {
  const qc = useQueryClient();
  const location = useLocation();
  const { user } = useAuth();
  const readOnly = !hasPermission(user, "inbox_use");
  const [activeId, setActiveId] = useState(location.state?.convId || null);

  useEffect(() => {
    if (location.state?.convId) setActiveId(location.state.convId);
  }, [location.state]);
  const [filters, setFilters] = useState({ status: "all", priority: "all", assigned_work_area: "all" });
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const [suggestionDraft, setSuggestionDraft] = useState("");
  const [suggestionMeta, setSuggestionMeta] = useState(null); // {confidence, intent}
  const [templateOpen, setTemplateOpen] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const scrollRef = useRef(null);

  const { data: workAreas = [] } = useQuery({
    queryKey: ["work-areas"],
    queryFn: () => api.get("/admin/work-areas").then((r) => r.data).catch(() => []),
  });

  const params = {};
  Object.entries(filters).forEach(([k, v]) => { if (v !== "all") params[k] = v; });

  const { data: convs = [] } = useQuery({
    queryKey: ["conversations", filters],
    queryFn: () => api.get("/conversations", { params }).then((r) => r.data),
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (!activeId && convs.length) setActiveId(convs[0].id);
  }, [convs, activeId]);

  const { data: active } = useQuery({
    queryKey: ["conversation", activeId],
    queryFn: () => api.get(`/conversations/${activeId}`).then((r) => r.data),
    enabled: !!activeId,
    refetchInterval: 3000,
  });

  const { data: templateData } = useQuery({
    queryKey: ["whatsapp-recontact-templates", activeId],
    queryFn: () => api.get(`/conversations/${activeId}/whatsapp-templates`).then((response) => response.data),
    enabled: !!activeId && active?.channel === "whatsapp" && !readOnly,
  });
  const recontactTemplates = useMemo(() => templateData?.templates || [], [templateData]);
  const selectedTemplate = recontactTemplates.find((template) => template.id === selectedTemplateId);
  const freeTextBlocked = active?.channel === "whatsapp" && active?.whatsapp_free_text_allowed === false;
  const whatsappWindowExpiry = active?.whatsapp_window_expires_at
    ? formatMessageFullDate(active.whatsapp_window_expires_at)
    : "";

  useEffect(() => {
    if (!selectedTemplateId && recontactTemplates.length) setSelectedTemplateId(recontactTemplates[0].id);
    if (selectedTemplateId && !recontactTemplates.some((template) => template.id === selectedTemplateId)) {
      setSelectedTemplateId(recontactTemplates[0]?.id || "");
    }
  }, [recontactTemplates, selectedTemplateId]);

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
      if (detail) toast.error(detail);
      else toast.error("No se pudo enviar el mensaje");
    },
  });

  const sendTemplate = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/send-whatsapp-template`, { template_id: selectedTemplateId }),
    onSuccess: () => {
      setTemplateOpen(false);
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      toast.success("Plantilla de recontacto enviada");
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo enviar la plantilla"),
  });

  // WhatsApp integration status (cheap; cached). Drives badges + composer mode.
  const { data: waStatus } = useQuery({
    queryKey: ["wa-status"],
    queryFn: () => api.get("/admin/whatsapp/status").then((r) => r.data).catch(() => ({ configured: false })),
    staleTime: 60_000,
  });

  const usersQ = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get("/users").then((r) => r.data),
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

  const updateContactSource = useMutation({
    mutationFn: (source) => api.patch(`/contacts/${active?.contact?.id}`, { lead_source: source }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      toast.success("Origen de contacto actualizado");
    },
    onError: () => toast.error("No se pudo actualizar el origen del contacto"),
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

  const deactivateBot = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/bot/deactivate`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      toast.success("Bot desactivado");
    },
    onError: () => toast.error("No se pudo desactivar el bot"),
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
        <div className="w-80 border-r border-[#E9E6DC] flex flex-col bg-latus-cream shrink-0">
          <div className="p-3 space-y-2 border-b border-[#E9E6DC]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-latus-muted" />
              <Input data-testid="inbox-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar chats…" className="pl-9 rounded-sm bg-white h-9" />
            </div>
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-1.5">
                <Select value={filters.status} onValueChange={(v) => setFilters({ ...filters, status: v })}>
                  <SelectTrigger data-testid="inbox-filter-status" className="rounded-md bg-white h-8 text-xs border-[#E2E8F0]"><SelectValue placeholder="Estado" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos los estados</SelectItem>
                    {CONV_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Select value={filters.priority} onValueChange={(v) => setFilters({ ...filters, priority: v })}>
                  <SelectTrigger data-testid="inbox-filter-priority" className="rounded-md bg-white h-8 text-xs border-[#E2E8F0]"><SelectValue placeholder="Prioridad" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todas las prioridades</SelectItem>
                    {PRIORITIES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <Select value={filters.assigned_work_area} onValueChange={(v) => setFilters({ ...filters, assigned_work_area: v })}>
                <SelectTrigger data-testid="inbox-filter-work-area" className="rounded-md bg-white h-8 text-xs w-full border-[#E2E8F0]"><SelectValue placeholder="Filtrar por Área" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todas las áreas</SelectItem>
                  <SelectItem value="unassigned">Sin área asignada</SelectItem>
                  {workAreas.map((wa) => (
                    <SelectItem key={wa.id} value={wa.id}>{wa.name}</SelectItem>
                  ))}
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
                className={`w-full flex items-center gap-3 p-3.5 text-left border-b border-[#E9E6DC] transition-colors relative ${
                  activeId === c.id ? "bg-white border-l-4 border-l-[#0E8DDB] shadow-2xs" : "hover:bg-white/60"
                }`}
              >
                <div className="relative shrink-0">
                  <Avatar src={c.contact?.avatar} name={c.contact?.name} size={42} />
                  {c.unread > 0 && (
                    <span className="absolute -top-1 -right-1 bg-[#0E8DDB] text-white text-[10px] font-bold rounded-full h-4 min-w-4 px-1 flex items-center justify-center border-2 border-white shadow-2xs">
                      {c.unread}
                    </span>
                  )}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-1 mb-0.5">
                    <p className="font-bold text-sm text-[#0B1B26] truncate">{c.contact?.name || "Sin nombre"}</p>
                    <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: statusMeta(PRIORITIES, c.priority).color }} title={`Prioridad: ${c.priority}`} />
                  </div>

                  <p className="text-xs text-[#71717A] truncate font-normal leading-normal">{c.last_message || "Sin mensajes"}</p>

                  <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                    {!c.bot_enabled ? (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-[#0E8DDB] bg-[#E0F2FE] border border-[#BAE6FD] rounded-md px-1.5 py-0.5">
                        <UserIcon className="h-2.5 w-2.5" /> HUMANO
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-[#52525B] bg-[#F4F4F5] border border-[#E4E4E7] rounded-md px-1.5 py-0.5">
                        <Bot className="h-2.5 w-2.5 text-[#0E8DDB]" /> BOT
                      </span>
                    )}

                    {c.assigned_work_area && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-[#475569] bg-[#F1F5F9] border border-[#E2E8F0] rounded-md px-1.5 py-0.5 uppercase">
                        {c.assigned_work_area.replace("_", " ")}
                      </span>
                    )}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Middle: thread */}
        <div className="flex-1 flex flex-col bg-white min-w-0">
          {!active ? (
            <div className="flex-1 flex flex-col items-center justify-center text-latus-muted">
              <MessageSquare className="h-10 w-10 mb-3" />
              <p className="text-sm">Seleccioná una conversación</p>
            </div>
          ) : (
            <>
              {/* header */}
              <div className="h-16 border-b border-[#E9E6DC] flex items-center justify-between px-5 shrink-0">
                <div className="flex items-center gap-3">
                  <Avatar src={active.contact?.avatar} name={active.contact?.name} size={40} />
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-bold text-base text-[#0B1B26] leading-tight">{active.contact?.name || "Sin nombre"}</p>
                      {active.channel === "whatsapp" && (
                        <span data-testid="channel-badge-whatsapp" className="inline-flex items-center gap-1 text-[10px] font-bold tracking-wider uppercase text-[#0E8DDB] bg-[#E0F2FE] border border-[#BAE6FD] rounded-md px-2 py-0.5">
                          WhatsApp
                        </span>
                      )}
                    </div>
                    {active.contact?.phone && <p className="text-xs text-[#71717A] mt-0.5 font-medium">{active.contact.phone}</p>}
                  </div>
                </div>
                <div className="flex items-center gap-2.5">
                  <div className="flex items-center gap-2" data-testid="handoff-control">
                    <Button
                      data-testid="bot-status-header-button"
                      disabled={readOnly || deactivateBot.isPending || reactivateBot.isPending}
                      onClick={() => {
                        if (active.bot_enabled) {
                          deactivateBot.mutate();
                        } else {
                          reactivateBot.mutate();
                        }
                      }}
                      className={`h-9 px-3.5 text-xs font-bold rounded-md transition-all shadow-2xs flex items-center gap-1.5 ${
                        active.bot_enabled
                          ? "bg-[#16A34A] hover:bg-[#15803D] text-white"
                          : "bg-[#475569] hover:bg-[#334155] text-white"
                      }`}
                    >
                      <Bot className="h-4 w-4" />
                      {active.bot_enabled ? "Bot Activo" : "Control Humano"}
                    </Button>
                  </div>
                  <Select value={active.status} onValueChange={(v) => patchConv.mutate({ status: v })}>
                    <SelectTrigger data-testid="conv-status-select" className="w-32 rounded-md h-9 text-xs font-medium border-[#E2E8F0]"><SelectValue /></SelectTrigger>
                    <SelectContent>{CONV_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}</SelectContent>
                  </Select>
                  <Select value={active.assigned_work_area || "none"} onValueChange={(v) => patchConv.mutate({ assigned_work_area: v === "none" ? null : v })}>
                    <SelectTrigger data-testid="conv-work-area-select" className="w-36 rounded-md h-9 text-xs font-medium border-[#E2E8F0]"><SelectValue placeholder="Área" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Sin área</SelectItem>
                      {workAreas.map((wa) => (
                        <SelectItem key={wa.id} value={wa.id}>{wa.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* messages */}
              <div ref={scrollRef} className="flex-1 overflow-auto p-5 space-y-3" style={{ background: "#FAFAF9" }} data-testid="message-thread">
                {active.messages?.map((m, index) => {
                  const isSystem = m.sender_type === "system";
                  const isCustomer = m.sender_type === "contact";
                  const isBot = m.sender_type === "bot";
                  const isOutbound = !isCustomer;
                  const previous = index > 0 ? active.messages[index - 1] : null;
                  const showDay = index === 0 || messageDayKey(m.created_at) !== messageDayKey(previous?.created_at);
                  const time = formatMessageTime(m.created_at);
                  const fullDate = formatMessageFullDate(m.created_at);
                  const ds = m.delivery_status;
                  const dsLabel = ds === "sent" ? "Enviado"
                    : ds === "delivered" ? "Entregado"
                    : ds === "read" ? "Leído"
                    : ds === "failed" ? "Falló"
                    : null;
                  return (
                    <Fragment key={m.id}>
                      {showDay && formatMessageDay(m.created_at) && (
                        <div className="flex items-center gap-3 py-1" data-testid={`message-day-${messageDayKey(m.created_at)}`}>
                          <span className="h-px flex-1 bg-[#E9E6DC]" />
                          <span className="rounded-full border border-[#E1DED4] bg-white px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-[#77736C] shadow-sm">
                            {formatMessageDay(m.created_at)}
                          </span>
                          <span className="h-px flex-1 bg-[#E9E6DC]" />
                        </div>
                      )}

                      {isSystem ? (
                        <div className="flex items-center gap-3 my-3" data-testid={`system-message-${m.id}`}>
                          <span className="h-px flex-1 bg-[#E2E8F0]" />
                          <div className="inline-flex items-center gap-2 rounded-full border border-[#CBD5E1] bg-[#F1F5F9] px-3.5 py-1 text-center text-[11px] font-semibold text-[#475569] shadow-2xs">
                            <span>{m.body}</span>
                            {time && <span className="text-[10px] text-[#64748B] font-normal" title={fullDate}>· {time}</span>}
                          </div>
                          <span className="h-px flex-1 bg-[#E2E8F0]" />
                        </div>
                      ) : (
                        <div className={`flex ${isCustomer ? "justify-start" : "justify-end"}`}>
                          <div className={`max-w-[70%] rounded-sm px-3.5 py-2 ${
                            isCustomer ? "bg-white border border-[#E9E6DC]"
                            : isBot ? "bg-latus-warm-gray border border-[#E9E6DC]"
                            : "bg-[#0E8DDB] text-white"}`}>
                            {!isCustomer && (
                              <div className={`flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide mb-0.5 ${isBot ? "text-[#0E8DDB]" : "text-orange-100"}`}>
                                {isBot ? <><Bot className="h-2.5 w-2.5" />Bot</> : <><UserIcon className="h-2.5 w-2.5" />{m.sender_name}</>}
                              </div>
                            )}
                            <p className={`text-sm ${isCustomer || isBot ? "text-[#0B1B26]" : "text-white"}`}>{m.body}</p>
                            {(time || (isOutbound && dsLabel)) && (
                              <div className={`mt-1 flex items-center justify-end gap-1 text-[10px] font-semibold ${
                                isCustomer ? "text-[#96928A]" : isBot ? "text-[#0E8DDB]" : "text-orange-100"
                              }`}>
                                {time && <span title={fullDate}>{time}</span>}
                                {time && isOutbound && dsLabel && <span aria-hidden="true">·</span>}
                                {isOutbound && dsLabel && (
                                  <span
                                    data-testid={`delivery-status-${m.id}`}
                                    className={ds === "failed" ? "text-yellow-200" : ""}
                                  >
                                    {dsLabel}
                                  </span>
                                )}
                              </div>
                            )}
                            {isOutbound && ds === "failed" && (m.whatsapp_error_code || m.whatsapp_error_message) && (
                              <div className={`mt-1.5 rounded-sm px-2 py-1 text-[10px] font-semibold ${isBot ? "bg-amber-50 text-amber-800" : "bg-white/15 text-yellow-100"}`}>
                                {m.whatsapp_error_code ? `Error #${m.whatsapp_error_code}` : "Error de WhatsApp"}
                                {m.whatsapp_error_message ? ` · ${m.whatsapp_error_message}` : ""}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </Fragment>
                  );
                })}
              </div>

              {/* composer */}
              <div className="border-t border-[#E9E6DC] p-3 shrink-0">
                {!active.bot_enabled && (
                  <p className="text-[11px] font-semibold text-[#0E8DDB] mb-2 flex items-center gap-1"><UserIcon className="h-3 w-3" /> Traspaso a humano activo — estás respondiendo como agente</p>
                )}
                {active.channel === "whatsapp" && waStatus && !waStatus.configured && (
                  <p data-testid="wa-not-configured-banner" className="text-[11px] font-semibold text-[#0E8DDB] mb-2">
                    WhatsApp no configurado — el envío real está deshabilitado.
                  </p>
                )}
                {freeTextBlocked ? (
                  <div data-testid="whatsapp-template-only-composer" className="rounded-xl border border-amber-300/80 bg-amber-50/90 p-3.5 shadow-2xs">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex items-start gap-2.5">
                        <AlertOctagon className="h-5 w-5 text-amber-700 shrink-0 mt-0.5" />
                        <div>
                          <p className="text-xs font-bold text-amber-950">Solo se puede enviar una plantilla</p>
                          <p className="mt-0.5 text-[11px] leading-relaxed text-amber-800">
                            La ventana de respuesta libre venció{whatsappWindowExpiry ? ` el ${whatsappWindowExpiry}` : ""}. Se bloqueó el mensaje manual para evitar el Error #131047.
                          </p>
                        </div>
                      </div>
                      <Button type="button" onClick={() => setTemplateOpen(true)} disabled={readOnly || !waStatus?.configured || recontactTemplates.length === 0} className="shrink-0 bg-amber-800 text-white hover:bg-amber-900 h-9 px-3.5 text-xs font-semibold rounded-md shadow-2xs flex items-center gap-1.5">
                        <FileText className="h-4 w-4" /> Seleccionar plantilla
                      </Button>
                    </div>
                    {recontactTemplates.length === 0 && <p className="mt-2 text-[11px] font-semibold text-red-700">No hay plantillas de recontacto activas. Configuralas en Configuración → Agenda.</p>}
                  </div>
                ) : (
                  <div className="flex items-end gap-2">
                    {active.channel === "whatsapp" && recontactTemplates.length > 0 && (
                      <Button type="button" variant="outline" onClick={() => setTemplateOpen(true)} disabled={readOnly || !waStatus?.configured} className="h-11 shrink-0 border-latus-warm-border bg-white text-latus-ink" title="Enviar plantilla aprobada para recontactar">
                        <FileText className="h-4 w-4" /><span className="hidden xl:inline">Plantilla</span>
                      </Button>
                    )}
                    <Textarea
                      data-testid="message-input"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      disabled={readOnly}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!readOnly && draft.trim() && !(active.channel === "whatsapp" && waStatus && !waStatus.configured)) sendMsg.mutate(); } }}
                      placeholder={readOnly ? "Modo solo lectura" : (active.channel === "whatsapp" && waStatus?.configured ? "Escribí una respuesta de WhatsApp…" : "Escribí una respuesta…")}
                      className="rounded-sm resize-none min-h-[44px] max-h-32"
                    />
                    <Button data-testid="send-message-button" disabled={readOnly || !draft.trim() || sendMsg.isPending || (active.channel === "whatsapp" && waStatus && !waStatus.configured)} onClick={() => sendMsg.mutate()} className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm h-11 px-4">
                      <Send className="h-4 w-4" />
                    </Button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Right: lead info + AI */}
        {active && (
          <div className="w-80 border-l border-[#E9E6DC] flex flex-col bg-latus-cream shrink-0 overflow-auto">
            <div className="p-4 border-b border-[#E9E6DC]">
              <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#888888] mb-3">Contacto</p>
              <div className="flex items-center gap-3 mb-3">
                <Avatar src={active.contact?.avatar} name={active.contact?.name} size={44} />
                <div className="min-w-0">
                  <p className="font-bold text-[#0B1B26] truncate">{active.contact?.name}</p>
                  <p className="text-xs text-[#888888] truncate">{active.contact?.company}</p>
                </div>
              </div>
              <div className="space-y-1.5 text-sm text-[#888888]">
                <p className="flex items-center gap-2"><Phone className="h-3.5 w-3.5 text-[#0E8DDB]" /> {active.contact?.phone}</p>
                <p className="flex items-center gap-2"><Building2 className="h-3.5 w-3.5 text-[#0E8DDB]" /> {active.contact?.company}</p>
              </div>

              <div className="flex items-center justify-between gap-2 text-xs pt-3 mt-3 border-t border-[#E2E8F0]">
                <span className="text-[#64748B] font-semibold shrink-0">Origen del lead:</span>
                <Select
                  value={active.contact?.lead_source || "Orgánico"}
                  onValueChange={(val) => updateContactSource.mutate(val)}
                >
                  <SelectTrigger className="w-36 h-8 rounded-md text-xs bg-white border border-[#CBD5E1]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Meta Ads">Meta Ads</SelectItem>
                    <SelectItem value="WhatsApp">WhatsApp</SelectItem>
                    <SelectItem value="Orgánico">Orgánico</SelectItem>
                    <SelectItem value="Recomendado">Recomendado</SelectItem>
                    <SelectItem value="Otro">Otro</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {active.contact?.lead_source === "Meta Ads" && (
              <div className="p-4 border-b border-[#E9E6DC] bg-orange-50/20 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] tracking-[0.15em] uppercase font-extrabold text-[#FF4500]">Origen Meta Ads</span>
                  <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold bg-[#EFF6FF] text-[#1D4ED8] border border-[#1D4ED8]/20">
                    Vino por anuncio
                  </span>
                </div>
                {active.contact.meta_ad_title && (
                  <div>
                    <p className="text-[10px] font-bold text-[#888888] uppercase">Título del anuncio</p>
                    <p className="text-sm font-semibold text-[#0B1B26]">{active.contact.meta_ad_title}</p>
                  </div>
                )}
                {active.contact.meta_ad_body && (
                  <div>
                    <p className="text-[10px] font-bold text-[#888888] uppercase">Texto del anuncio</p>
                    <p className="text-xs text-[#52525B] whitespace-pre-wrap">{active.contact.meta_ad_body}</p>
                  </div>
                )}
                {active.contact.meta_ad_image_url && (
                  <div>
                    <p className="text-[10px] font-bold text-[#888888] uppercase mb-1">Imagen del anuncio</p>
                    <img src={active.contact.meta_ad_image_url} alt="Ad Visual" className="max-w-full h-auto rounded-sm border border-[#E9E6DC] object-cover max-h-40" />
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2 text-xs">
                  {active.contact.meta_ad_id && (
                    <div>
                      <p className="text-[10px] font-bold text-[#888888] uppercase">ID del anuncio</p>
                      <p className="text-[#52525B] font-mono text-[11px] truncate" title={active.contact.meta_ad_id}>{active.contact.meta_ad_id}</p>
                    </div>
                  )}
                  {active.contact.meta_ad_url && (
                    <div>
                      <p className="text-[10px] font-bold text-[#888888] uppercase">Link al anuncio</p>
                      <a href={active.contact.meta_ad_url} target="_blank" rel="noopener noreferrer" className="text-[#0E8DDB] hover:underline font-semibold block truncate">
                        Ver anuncio &rarr;
                      </a>
                    </div>
                  )}
                </div>
                {active.contact.first_ad_message_at && (
                  <div>
                    <p className="text-[10px] font-bold text-[#888888] uppercase">Fecha del primer mensaje</p>
                    <p className="text-xs text-[#52525B]">{new Date(active.contact.first_ad_message_at).toLocaleString("es-AR")}</p>
                  </div>
                )}
                {active.contact.first_message_from_ad && (
                  <div>
                    <p className="text-[10px] font-bold text-[#888888] uppercase">Primer mensaje recibido</p>
                    <p className="text-xs bg-white border border-[#E9E6DC] p-2 rounded-sm text-[#0B1B26] italic">
                      "{active.contact.first_message_from_ad}"
                    </p>
                  </div>
                )}
              </div>
            )}

            {active.lead && (
              <div className="p-4 border-b border-[#E9E6DC]">
                <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#888888] mb-3">Lead vinculado</p>
                <p className="font-semibold text-[#0B1B26] text-sm">{active.lead.title}</p>
                <div className="flex items-center justify-between mt-2">
                  <StatusBadge list={LEAD_STATUSES} value={active.lead.status} />
                  <span className="font-extrabold tracking-tighter text-[#0B1B26]">{money(active.lead.value)}</span>
                </div>
              </div>
            )}

            {/* Operator Assignment */}
            <div className="p-4 border-b border-[#E9E6DC]">
              <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#888888] mb-3">Responsable</p>
              <Select
                disabled={readOnly}
                value={active.assigned_to || "unassigned"}
                onValueChange={(v) => {
                  patchConv.mutate({ assigned_to: v === "unassigned" ? null : v });
                  toast.success("Asignación de conversación actualizada");
                }}
              >
                <SelectTrigger data-testid="inbox-assignee-select" className="w-full rounded-sm h-9 text-xs">
                  <SelectValue placeholder="Sin asignar" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="unassigned">Sin asignar</SelectItem>
                  {(usersQ.data || [])
                    .filter((u) => u.is_active)
                    .map((u) => (
                      <SelectItem key={u.user_id} value={u.user_id}>
                        {u.name || u.email}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>

            {/* AI Bot panel */}
            <BotPanel
              conv={active}
              readOnly={readOnly}
              regenSummary={regenSummary}
              suggestReply={suggestReply}
              reactivateBot={reactivateBot}
              deactivateBot={deactivateBot}
              suggestionDraft={suggestionDraft}
              setSuggestionDraft={setSuggestionDraft}
              suggestionMeta={suggestionMeta}
              clearSuggestion={() => { setSuggestionDraft(""); setSuggestionMeta(null); }}
              onUseDraft={(text) => { setDraft(text); toast.success("Sugerencia copiada al composer"); }}
            />
          </div>
        )}
      </div>

      <Dialog open={templateOpen} onOpenChange={setTemplateOpen}>
        <DialogContent className="rounded-xl border-latus-warm-border bg-latus-surface">
          <DialogHeader><DialogTitle>Enviar plantilla de recontacto</DialogTitle></DialogHeader>
          <div className="space-y-4 py-2">
            <p className="text-sm text-latus-muted">Usá una plantilla aprobada por Meta para volver a contactar al cliente, incluso fuera de la ventana de 24 horas.</p>
            <div>
              <label className="text-xs font-bold text-latus-ink">Plantilla</label>
              <Select value={selectedTemplateId} onValueChange={setSelectedTemplateId}>
                <SelectTrigger className="mt-1"><SelectValue placeholder="Seleccionar plantilla" /></SelectTrigger>
                <SelectContent>{recontactTemplates.map((template) => <SelectItem key={template.id} value={template.id}>{template.label || template.name}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            {selectedTemplate && (
              <div className="rounded-lg border border-latus-warm-border bg-latus-cream/45 p-4">
                <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-latus-muted">Vista previa</p>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-latus-ink">{selectedTemplate.rendered_preview}</p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setTemplateOpen(false)}>Cancelar</Button>
            <Button type="button" onClick={() => sendTemplate.mutate()} disabled={!selectedTemplateId || sendTemplate.isPending} className="bg-latus-blue text-white hover:bg-latus-blue-deep">
              {sendTemplate.isPending ? "Enviando..." : "Enviar plantilla"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

// ---------------------------------------------------------------------------
// Right-sidebar Bot IA panel
// ---------------------------------------------------------------------------
const BOT_STATUS_META = {
  bot_activo:        { label: "Bot activo",         color: "#16A34A", bg: "#DCFCE7", border: "#86EFAC" },
  esperando_cliente: { label: "Esperando cliente",  color: "#1D4ED8", bg: "#DBEAFE", border: "#93C5FD" },
  requiere_humano:   { label: "Requiere humano",    color: "#EF5030", bg: "#EFE3E1", border: "#EF5030" },
  en_atencion_humana:{ label: "En atención humana", color: "#7C3AED", bg: "#EDE9FE", border: "#C4B5FD" },
  cerrada:           { label: "Cerrada",            color: "#888888", bg: "#F4F4F5", border: "#D4D4D8" },
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
  conv, readOnly, regenSummary, suggestReply, reactivateBot, deactivateBot,
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
    <div data-testid="bot-panel" className="p-4 border-b border-[#E9E6DC] space-y-4">
      <p className="flex items-center gap-1.5 text-xs tracking-[0.15em] uppercase font-bold text-[#0E8DDB]">
        <Sparkles className="h-3.5 w-3.5" /> Asistente IA
      </p>

      {/* Bot status */}
      <div>
        <p className="text-[10px] font-bold tracking-wider uppercase text-[#64748B] mb-2">Estado del bot</p>
        <div className="flex items-center justify-between gap-2">
          <BotStatusPill status={botStatus} />
          <Button
            data-testid="bot-toggle-sidebar"
            size="sm"
            disabled={readOnly || reactivateBot.isPending || deactivateBot.isPending}
            onClick={() => {
              if (conv?.bot_enabled) {
                deactivateBot.mutate();
              } else {
                reactivateBot.mutate();
              }
            }}
            className={`rounded-md h-8 px-3 text-xs font-bold transition-all shadow-2xs flex items-center gap-1.5 ${
              conv?.bot_enabled
                ? "bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300"
                : "bg-[#16A34A] hover:bg-[#15803D] text-white"
            }`}
          >
            {reactivateBot.isPending || deactivateBot.isPending ? (
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Bot className="h-3.5 w-3.5" />
            )}
            {conv?.bot_enabled ? "Desactivar bot" : "Reactivar bot"}
          </Button>
        </div>
      </div>

      {/* Intent */}
      {(intent || confidencePct) && (
        <div>
          <p className="text-[10px] font-bold tracking-[0.12em] uppercase text-[#888888] mb-1.5">Intención detectada</p>
          <div className="flex items-center justify-between gap-2" data-testid="bot-intent-block">
            <span className="text-sm font-semibold text-[#0B1B26]">{intent || "—"}</span>
            {confidencePct && (
              <span data-testid="bot-confidence-value" className="text-xs font-bold text-[#888888] bg-latus-warm-gray border border-[#E9E6DC] rounded-sm px-1.5 py-0.5">
                {confidencePct}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Handoff reason — only when present */}
      {reason && (
        <div data-testid="bot-handoff-reason" className="border border-[#EFE3E1] bg-[#F4F2EC] rounded-sm p-2.5">
          <p className="flex items-center gap-1 text-[10px] font-bold tracking-[0.12em] uppercase text-[#0E8DDB] mb-1">
            <AlertOctagon className="h-3 w-3" /> Motivo de derivación
          </p>
          <p className="text-xs text-[#0B1B26]">{reason}</p>
        </div>
      )}

      {/* Next best action */}
      {nba && (
        <div data-testid="bot-next-action">
          <p className="text-[10px] font-bold tracking-[0.12em] uppercase text-[#888888] mb-1">Próxima acción sugerida</p>
          <p className="text-xs text-[#0B1B26] flex items-start gap-1.5">
            <ChevronRight className="h-3.5 w-3.5 text-[#0E8DDB] mt-0.5 shrink-0" /> {nba}
          </p>
        </div>
      )}

      {/* Summary */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <p className="text-[10px] font-bold tracking-[0.12em] uppercase text-[#888888]">Resumen</p>
          <button
            data-testid="bot-summary-regenerate"
            onClick={() => regenSummary.mutate()}
            disabled={readOnly || regenSummary.isPending}
            className="text-[11px] font-semibold text-[#0E8DDB] flex items-center gap-1 disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${regenSummary.isPending ? "animate-spin" : ""}`} /> Regenerar
          </button>
        </div>
        <div
          data-testid="bot-summary-output"
          className="border border-[#E9E6DC] bg-white rounded-sm p-2.5 min-h-[60px] text-sm text-[#0B1B26] whitespace-pre-wrap"
        >
          {regenSummary.isPending
            ? <span className="text-[#888888] animate-pulse">Analizando la conversación…</span>
            : summary
              ? summary
              : <span className="text-[#888888]">Aún no hay resumen. Tocá Regenerar para crearlo.</span>}
        </div>
        {conv?.last_summary_at && (
          <p className="text-[10px] text-[#888888] mt-1">Actualizado: {new Date(conv.last_summary_at).toLocaleString("es-AR")}</p>
        )}
      </div>

      {/* Suggested reply */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <p className="flex items-center gap-1 text-[10px] font-bold tracking-[0.12em] uppercase text-[#888888]">
            <Lightbulb className="h-3 w-3 text-[#0E8DDB]" /> Sugerencia de respuesta
          </p>
          <button
            data-testid="bot-suggest-button"
            onClick={() => suggestReply.mutate()}
            disabled={readOnly || suggestReply.isPending}
            className="text-[11px] font-semibold text-[#0E8DDB] flex items-center gap-1 disabled:opacity-50"
          >
            {suggestReply.isPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
            Sugerir
          </button>
        </div>
        {suggestionDraft ? (
          <div className="border border-[#EFE3E1] bg-[#F4F2EC] rounded-sm p-2.5">
            <Textarea
              data-testid="bot-suggestion-textarea"
              value={suggestionDraft}
              onChange={(e) => setSuggestionDraft(e.target.value)}
              className="rounded-sm bg-white text-sm min-h-[80px] mb-2"
              disabled={readOnly}
            />
            {suggestionMeta && (
              <div className="flex items-center gap-2 text-[10px] text-[#888888] mb-2">
                {suggestionMeta.intent && <span>Intención: <b className="text-[#0B1B26]">{suggestionMeta.intent}</b></span>}
                {suggestionMeta.confidence > 0 && (
                  <span>· Confianza: <b className="text-[#0B1B26]">{Math.round(suggestionMeta.confidence * 100)}%</b></span>
                )}
              </div>
            )}
            <div className="flex gap-2">
              <Button
                data-testid="bot-suggestion-use"
                size="sm"
                disabled={readOnly || !suggestionDraft.trim()}
                onClick={() => onUseDraft(suggestionDraft)}
                className="bg-[#0B1B26] hover:bg-[#0E8DDB] rounded-sm h-7 text-xs flex-1"
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
          <div className="border border-[#E9E6DC] bg-latus-cream rounded-sm p-2.5 text-xs text-[#888888]">
            Generá un borrador editable con la IA basado en el contexto actual.
          </div>
        )}
      </div>
    </div>
  );
}
