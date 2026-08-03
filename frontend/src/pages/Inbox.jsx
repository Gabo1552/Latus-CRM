import { Fragment, useState, useEffect, useMemo, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Send, Bot, User as UserIcon, Sparkles, Search, Phone, Mail,
  MessageSquare, Zap, Copy, RefreshCw, AlertOctagon, ChevronRight, FileText,
  Lightbulb, ExternalLink, ChevronLeft, Info, X, Globe
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { hasPermission } from "@/lib/permissions";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { CONV_STATUSES, PRIORITIES, LEAD_STATUSES, money, statusMeta } from "@/lib/constants";
import { safeExternalUrl } from "@/lib/security";
import { StatusBadge, Avatar } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
  const [showMobileList, setShowMobileList] = useState(!location.state?.convId);
  const [mobileRightOpen, setMobileRightOpen] = useState(false);

  useEffect(() => {
    if (location.state?.convId) {
      setActiveId(location.state.convId);
      setShowMobileList(false);
    }
  }, [location.state]);

  const [filters, setFilters] = useState({ status: "all", priority: "all", assigned_work_area: "all" });
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const [suggestionDraft, setSuggestionDraft] = useState("");
  const [suggestionMeta, setSuggestionMeta] = useState(null);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [rightPanelTab, setRightPanelTab] = useState("client"); // "client" | "ai"
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

  const filtered = useMemo(() => {
    const list = convs.filter((c) => !search || c.contact?.name?.toLowerCase().includes(search.toLowerCase()));
    const seen = new Set();
    const result = [];
    for (const c of list) {
      const cid = c.contact_id || c.contact?.id;
      const channel = c.channel || "whatsapp";
      const key = `${cid}_${channel}`;
      if (!seen.has(key)) {
        seen.add(key);
        result.push(c);
      }
    }
    return result;
  }, [convs, search]);

  return (
    <AppLayout title="Bandeja">
      <div className="flex h-[calc(100vh-4rem)] w-full overflow-hidden bg-[#F8FAFC] relative">
        
        {/* ----------------------------------------------------------------- */}
        {/* Left: conversation list */}
        {/* ----------------------------------------------------------------- */}
        <div className={`w-full lg:w-80 lg:w-84 border-r border-[#E2E8F0] flex-col bg-white shrink-0 shadow-2xs ${
          showMobileList ? "flex" : "hidden lg:flex"
        }`}>
          
          {/* Filter Header */}
          <div className="p-3.5 space-y-2.5 border-b border-[#E2E8F0] bg-slate-50/60">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
              <Input
                data-testid="inbox-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Buscar por cliente…"
                className="pl-9 bg-white border-[#E2E8F0] h-9 text-xs focus-visible:ring-1 focus-visible:ring-[#0E8DDB]"
              />
            </div>

            <div className="space-y-1.5">
              <div className="grid grid-cols-2 gap-1.5">
                <Select value={filters.status} onValueChange={(v) => setFilters({ ...filters, status: v })}>
                  <SelectTrigger data-testid="inbox-filter-status" className="rounded-sm bg-white h-8 text-xs font-medium border-[#E2E8F0] leading-snug">
                    <SelectValue placeholder="Estado" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos los estados</SelectItem>
                    {CONV_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                  </SelectContent>
                </Select>

                <Select value={filters.priority} onValueChange={(v) => setFilters({ ...filters, priority: v })}>
                  <SelectTrigger data-testid="inbox-filter-priority" className="rounded-sm bg-white h-8 text-xs font-medium border-[#E2E8F0] leading-snug">
                    <SelectValue placeholder="Prioridad" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todas prioridades</SelectItem>
                    {PRIORITIES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>

              <Select value={filters.assigned_work_area} onValueChange={(v) => setFilters({ ...filters, assigned_work_area: v })}>
                <SelectTrigger data-testid="inbox-filter-work-area" className="rounded-sm bg-white h-8 text-xs font-medium w-full border-[#E2E8F0] leading-snug">
                  <SelectValue placeholder="Filtrar por Área" />
                </SelectTrigger>
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

          {/* Conversation List Items */}
          <div className="flex-1 overflow-y-auto divide-y divide-[#F1F5F9]">
            {filtered.length === 0 ? (
              <div className="p-8 text-center text-slate-400 text-xs">
                No se encontraron conversaciones
              </div>
            ) : (
              filtered.map((c) => {
                const isActive = activeId === c.id;
                const priorityColor = statusMeta(PRIORITIES, c.priority).color;
                const timeStr = c.last_message_at ? formatMessageTime(c.last_message_at) : "";

                return (
                  <button
                    key={c.id}
                    onClick={() => {
                      setActiveId(c.id);
                      setShowMobileList(false);
                    }}
                    data-testid={`conv-item-${c.id}`}
                    className={`w-full flex items-start gap-3 p-3.5 text-left transition-all relative ${
                      isActive
                        ? "bg-[#F0F7FF] border-l-4 border-l-[#0E8DDB]"
                        : "hover:bg-slate-50 bg-white"
                    }`}
                  >
                    <div className="relative shrink-0 mt-0.5">
                      <Avatar src={c.contact?.avatar} name={c.contact?.name} size={40} />
                      {c.unread > 0 && (
                        <span className="absolute -top-1 -right-1 bg-[#0E8DDB] text-white text-[10px] font-extrabold rounded-full h-4.5 min-w-4.5 px-1 flex items-center justify-center border-2 border-white shadow-2xs">
                          {c.unread}
                        </span>
                      )}
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-1 mb-1">
                        <p className="font-bold text-sm text-slate-900 truncate leading-snug">
                          {c.contact?.name || "Sin nombre"}
                        </p>
                        <div className="flex items-center gap-1.5 shrink-0">
                          {timeStr && <span className="text-[11px] font-medium text-slate-400">{timeStr}</span>}
                          <span
                            className="h-2 w-2 rounded-full shrink-0"
                            style={{ backgroundColor: priorityColor }}
                            title={`Prioridad: ${c.priority}`}
                          />
                        </div>
                      </div>

                      <p className="text-xs text-slate-500 truncate font-normal leading-relaxed mb-2">
                        {c.last_message || "Sin mensajes"}
                      </p>

                      <div className="flex items-center gap-1.5 flex-wrap">
                        {c.channel === "whatsapp" && (
                          <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-[#0E8DDB] bg-[#E0F2FE] border border-[#BAE6FD] rounded-sm px-1.5 py-0.5 leading-none">
                            WA
                          </span>
                        )}
                        {c.channel === "webchat" && (
                          <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-purple-700 bg-purple-50 border border-purple-200 rounded-sm px-1.5 py-0.5 leading-none">
                            <Globe className="h-2.5 w-2.5 text-purple-600" /> WEB
                          </span>
                        )}

                        {c.assigned_work_area && (
                          <span className="inline-flex items-center text-[10px] font-bold text-slate-600 bg-slate-100 border border-slate-200 rounded-sm px-1.5 py-0.5 leading-none uppercase">
                            {c.assigned_work_area.replace("_", " ")}
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* ----------------------------------------------------------------- */}
        {/* Middle: thread view */}
        {/* ----------------------------------------------------------------- */}
        <div className={`flex-1 flex-col bg-white min-w-0 border-r border-[#E2E8F0] ${
          !showMobileList ? "flex" : "hidden lg:flex"
        }`}>
          {!active ? (
            <div className="flex-1 flex flex-col items-center justify-center text-slate-400 bg-slate-50/50">
              <MessageSquare className="h-12 w-12 mb-3 text-slate-300" />
              <p className="text-sm font-semibold text-slate-600">Seleccioná una conversación de la lista</p>
            </div>
          ) : (
            <>
              {/* Header */}
              <div className="h-16 border-b border-[#E2E8F0] flex items-center justify-between px-3.5 sm:px-5 bg-white shrink-0 shadow-2xs z-10">
                <div className="flex items-center gap-2.5 min-w-0">
                  {/* Mobile Back Button */}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowMobileList(true)}
                    className="lg:hidden p-1.5 h-9 font-bold text-slate-700 flex items-center gap-0.5 shrink-0"
                    title="Volver a lista de chats"
                  >
                    <ChevronLeft className="h-5 w-5 text-slate-600" />
                    <span className="text-xs hidden sm:inline">Chats</span>
                  </Button>

                  <Avatar src={active.contact?.avatar} name={active.contact?.name} size={40} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <p className="font-bold text-sm sm:text-base text-slate-900 truncate leading-snug">
                        {active.contact?.name || "Sin nombre"}
                      </p>
                      {active.channel === "whatsapp" && (
                        <span data-testid="channel-badge-whatsapp" className="inline-flex items-center gap-1 text-[10px] font-extrabold tracking-wider uppercase text-[#0E8DDB] bg-[#E0F2FE] border border-[#BAE6FD] rounded-sm px-1.5 py-0.5 leading-none shrink-0">
                          WhatsApp
                        </span>
                      )}
                      {active.channel === "webchat" && (
                        <span data-testid="channel-badge-webchat" className="inline-flex items-center gap-1 text-[10px] font-extrabold tracking-wider uppercase text-purple-700 bg-purple-50 border border-purple-200 rounded-sm px-1.5 py-0.5 leading-none shrink-0">
                          <Globe className="h-3 w-3 text-purple-600" /> Chat Web
                        </span>
                      )}
                    </div>
                    {active.contact?.phone && (
                      <p className="text-[11px] sm:text-xs text-slate-500 font-medium leading-snug truncate">
                        {active.contact.phone}
                      </p>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1.5 sm:gap-2.5 shrink-0">
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
                      className={`h-9 px-2.5 sm:px-3.5 text-xs font-bold rounded-sm transition-all shadow-2xs flex items-center gap-1.5 ${
                        active.bot_enabled
                          ? "bg-[#16A34A] hover:bg-[#15803D] text-white"
                          : "bg-slate-700 hover:bg-slate-800 text-white"
                      }`}
                    >
                      <Bot className="h-4 w-4" />
                      <span className="hidden sm:inline">{active.bot_enabled ? "Bot Activo" : "Control Humano"}</span>
                    </Button>
                  </div>

                  <div className="hidden md:flex items-center gap-2">
                    <Select value={active.status} onValueChange={(v) => patchConv.mutate({ status: v })}>
                      <SelectTrigger data-testid="conv-status-select" className="w-32 rounded-sm h-9 text-xs font-semibold border-[#CBD5E1]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {CONV_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                      </SelectContent>
                    </Select>

                    <Select value={active.assigned_work_area || "none"} onValueChange={(v) => patchConv.mutate({ assigned_work_area: v === "none" ? null : v })}>
                      <SelectTrigger data-testid="conv-work-area-select" className="w-36 rounded-sm h-9 text-xs font-semibold border-[#CBD5E1]">
                        <SelectValue placeholder="Área" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">Sin área</SelectItem>
                        {workAreas.map((wa) => (
                          <SelectItem key={wa.id} value={wa.id}>{wa.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Mobile Right Drawer Button */}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setMobileRightOpen(true)}
                    className="lg:hidden h-9 px-2.5 border-slate-300 bg-slate-50 text-slate-700 flex items-center gap-1 font-bold shadow-2xs"
                    title="Abrir Ficha e IA"
                  >
                    <Info className="h-4 w-4 text-[#0E8DDB]" />
                    <span className="text-xs hidden sm:inline">Info & IA</span>
                  </Button>
                </div>
              </div>

              {/* Messages Thread */}
              <div
                ref={scrollRef}
                className="flex-1 overflow-y-auto p-3.5 sm:p-5 space-y-4 bg-[#F8FAFC]"
                data-testid="message-thread"
              >
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
                        <div className="flex items-center gap-3 py-1.5" data-testid={`message-day-${messageDayKey(m.created_at)}`}>
                          <span className="h-px flex-1 bg-slate-200" />
                          <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-slate-500 shadow-2xs">
                            {formatMessageDay(m.created_at)}
                          </span>
                          <span className="h-px flex-1 bg-slate-200" />
                        </div>
                      )}

                      {isSystem ? (
                        <div className="flex items-center gap-3 my-3" data-testid={`system-message-${m.id}`}>
                          <span className="h-px flex-1 bg-slate-200" />
                          <div className="inline-flex items-center gap-2 rounded-full border border-slate-300 bg-slate-100 px-4 py-1 text-center text-xs font-semibold text-slate-700 shadow-2xs">
                            <span className="leading-snug">{m.body}</span>
                            {time && <span className="text-[11px] text-slate-500 font-normal" title={fullDate}>· {time}</span>}
                          </div>
                          <span className="h-px flex-1 bg-slate-200" />
                        </div>
                      ) : (
                        <div className={`flex ${isCustomer ? "justify-start" : "justify-end"}`}>
                          <div className={`max-w-[85%] sm:max-w-[72%] rounded-md px-3.5 sm:px-4 py-2.5 shadow-2xs ${
                            isCustomer
                              ? "bg-white border border-slate-200 text-slate-900"
                              : isBot
                              ? "bg-sky-50 border border-sky-200 text-slate-900"
                              : "bg-[#0E8DDB] text-white"
                          }`}>
                            {!isCustomer && (
                              <div className={`flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider mb-1 leading-none ${
                                isBot ? "text-[#0E8DDB]" : "text-sky-100"
                              }`}>
                                {isBot ? <><Bot className="h-3 w-3 text-[#0E8DDB]" />Bot IA</> : <><UserIcon className="h-3 w-3 text-sky-100" />{m.sender_name}</>}
                              </div>
                            )}

                            <p className={`text-sm leading-relaxed whitespace-pre-wrap ${
                              isCustomer || isBot ? "text-slate-900" : "text-white font-normal"
                            }`}>
                              {m.body}
                            </p>

                            {(time || (isOutbound && dsLabel)) && (
                              <div className={`mt-1.5 flex items-center justify-end gap-1.5 text-[11px] font-medium leading-none ${
                                isCustomer ? "text-slate-400" : isBot ? "text-[#0E8DDB]" : "text-sky-100"
                              }`}>
                                {time && <span title={fullDate}>{time}</span>}
                                {time && isOutbound && dsLabel && <span aria-hidden="true">·</span>}
                                {isOutbound && dsLabel && (
                                  <span
                                    data-testid={`delivery-status-${m.id}`}
                                    className={`font-semibold ${ds === "failed" ? "text-amber-200 font-bold" : ""}`}
                                  >
                                    {dsLabel}
                                  </span>
                                )}
                              </div>
                            )}

                            {isOutbound && ds === "failed" && (m.whatsapp_error_code || m.whatsapp_error_message) && (
                              <div className={`mt-2 rounded-sm px-2.5 py-1 text-xs font-medium ${isBot ? "bg-amber-100 text-amber-900" : "bg-white/20 text-amber-100"}`}>
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

              {/* Composer */}
              <div className="border-t border-[#E2E8F0] p-3 sm:p-3.5 bg-white shrink-0">
                {!active.bot_enabled && (
                  <p className="text-xs font-bold text-[#0E8DDB] mb-2.5 flex items-center gap-1.5 leading-snug">
                    <UserIcon className="h-3.5 w-3.5" /> Traspaso a humano activo — estás respondiendo como agente
                  </p>
                )}
                {active.channel === "whatsapp" && waStatus && !waStatus.configured && (
                  <p data-testid="wa-not-configured-banner" className="text-xs font-semibold text-amber-700 mb-2.5 leading-snug">
                    WhatsApp no configurado — el envío real está deshabilitado.
                  </p>
                )}

                {freeTextBlocked ? (
                  <div data-testid="whatsapp-template-only-composer" className="rounded-lg border border-amber-300/80 bg-amber-50/90 p-3.5 shadow-2xs">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex items-start gap-2.5">
                        <AlertOctagon className="h-5 w-5 text-amber-700 shrink-0 mt-0.5" />
                        <div>
                          <p className="text-xs font-bold text-amber-950 leading-snug">Solo se puede enviar una plantilla de recontacto</p>
                          <p className="mt-0.5 text-xs leading-relaxed text-amber-800">
                            La ventana de respuesta libre de 24 hs venció{whatsappWindowExpiry ? ` el ${whatsappWindowExpiry}` : ""}.
                          </p>
                        </div>
                      </div>
                      <Button
                        type="button"
                        onClick={() => setTemplateOpen(true)}
                        disabled={readOnly || !waStatus?.configured || recontactTemplates.length === 0}
                        className="shrink-0 bg-amber-800 text-white hover:bg-amber-900 h-9 px-3.5 text-xs font-bold rounded-sm shadow-2xs flex items-center gap-1.5"
                      >
                        <FileText className="h-4 w-4" /> Seleccionar plantilla
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-end gap-2 sm:gap-2.5">
                    {active.channel === "whatsapp" && recontactTemplates.length > 0 && (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => setTemplateOpen(true)}
                        disabled={readOnly || !waStatus?.configured}
                        className="h-11 shrink-0 border-slate-300 bg-white text-slate-700 font-semibold px-2.5 sm:px-3"
                        title="Enviar plantilla aprobada para recontactar"
                      >
                        <FileText className="h-4 w-4 text-slate-500 sm:mr-1.5" />
                        <span className="hidden sm:inline text-xs">Plantilla</span>
                      </Button>
                    )}
                    <Textarea
                      data-testid="message-input"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      disabled={readOnly}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          if (!readOnly && draft.trim() && !(active.channel === "whatsapp" && waStatus && !waStatus.configured)) {
                            sendMsg.mutate();
                          }
                        }
                      }}
                      placeholder={
                        readOnly
                          ? "Modo solo lectura"
                          : (active.channel === "whatsapp" && waStatus?.configured
                            ? "Escribí una respuesta de WhatsApp…"
                            : "Escribí una respuesta…")
                      }
                      className="rounded-sm resize-none min-h-[44px] max-h-32 text-xs leading-relaxed border-slate-300 focus-visible:ring-1 focus-visible:ring-[#0E8DDB]"
                    />
                    <Button
                      data-testid="send-message-button"
                      disabled={readOnly || !draft.trim() || sendMsg.isPending || (active.channel === "whatsapp" && waStatus && !waStatus.configured)}
                      onClick={() => sendMsg.mutate()}
                      className="bg-[#0E8DDB] hover:bg-[#0a7ab8] text-white rounded-sm h-11 px-3.5 sm:px-4 shrink-0 shadow-2xs font-semibold"
                    >
                      <Send className="h-4 w-4" />
                    </Button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* ----------------------------------------------------------------- */}
        {/* Right: Tabbed Panel (Client Profile & AI Assistant) */}
        {/* Responsive: full screen modal overlay on mobile (< lg), sidebar on lg */}
        {/* ----------------------------------------------------------------- */}
        {active && (
          <div className={`fixed inset-0 z-50 bg-white flex flex-col w-full h-full lg:static lg:z-auto lg:w-80 lg:w-84 lg:h-auto border-l border-[#E2E8F0] shrink-0 overflow-y-auto shadow-2xs transition-all ${
            mobileRightOpen ? "flex" : "hidden lg:flex"
          }`}>
            
            {/* Top Close Header for Mobile */}
            <div className="lg:hidden h-14 border-b border-[#E2E8F0] px-4 flex items-center justify-between bg-slate-50 shrink-0">
              <div className="flex items-center gap-2">
                <Avatar src={active.contact?.avatar} name={active.contact?.name} size={32} />
                <p className="font-bold text-sm text-slate-900 truncate">{active.contact?.name}</p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setMobileRightOpen(false)}
                className="h-8 w-8 p-0 text-slate-500 hover:text-slate-900"
              >
                <X className="h-5 w-5" />
              </Button>
            </div>

            {/* Panel Tabs */}
            <div className="flex border-b border-[#E2E8F0] bg-slate-50/70 p-1.5 gap-1 shrink-0">
              <button
                type="button"
                onClick={() => setRightPanelTab("client")}
                className={`flex-1 py-1.5 px-3 rounded-sm text-xs font-bold transition-all flex items-center justify-center gap-1.5 leading-normal ${
                  rightPanelTab === "client"
                    ? "bg-white text-[#0B1B26] shadow-2xs border border-slate-200"
                    : "text-slate-500 hover:text-slate-800 hover:bg-slate-100"
                }`}
              >
                <UserIcon className="h-3.5 w-3.5 text-[#0E8DDB]" />
                <span>Cliente & Lead</span>
              </button>
              <button
                type="button"
                onClick={() => setRightPanelTab("ai")}
                className={`flex-1 py-1.5 px-3 rounded-sm text-xs font-bold transition-all flex items-center justify-center gap-1.5 leading-normal ${
                  rightPanelTab === "ai"
                    ? "bg-white text-[#0B1B26] shadow-2xs border border-slate-200"
                    : "text-slate-500 hover:text-slate-800 hover:bg-slate-100"
                }`}
              >
                <Sparkles className="h-3.5 w-3.5 text-[#0E8DDB]" />
                <span>Asistente IA</span>
              </button>
            </div>

            {/* Tab 1: Ficha del Cliente */}
            {rightPanelTab === "client" && (
              <div className="divide-y divide-[#E2E8F0] flex-1">
                {/* Contact Data */}
                <div className="p-4 space-y-3">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 leading-tight">
                    Datos del Contacto
                  </p>
                  <div className="flex items-center gap-3">
                    <Avatar src={active.contact?.avatar} name={active.contact?.name} size={44} />
                    <div className="min-w-0">
                      <p className="font-bold text-slate-900 text-sm truncate leading-snug">
                        {active.contact?.name || "Sin nombre"}
                      </p>
                      {active.contact?.company && (
                        <p className="text-xs text-slate-500 truncate leading-snug mt-0.5">
                          {active.contact.company}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="space-y-2 text-xs text-slate-600 pt-1">
                    {active.contact?.phone && (
                      <p className="flex items-center gap-2 font-medium">
                        <Phone className="h-3.5 w-3.5 text-[#0E8DDB] shrink-0" />
                        <a href={`tel:${active.contact.phone}`} className="hover:underline text-slate-800">
                          {active.contact.phone}
                        </a>
                      </p>
                    )}
                    {active.contact?.email && (
                      <p className="flex items-center gap-2 font-medium truncate">
                        <Mail className="h-3.5 w-3.5 text-[#0E8DDB] shrink-0" />
                        <span className="truncate">{active.contact.email}</span>
                      </p>
                    )}
                  </div>

                  <div className="flex items-center justify-between gap-2 text-xs pt-2.5 mt-2 border-t border-slate-100">
                    <span className="text-slate-500 font-semibold shrink-0">Origen de Lead:</span>
                    <Select
                      value={active.contact?.lead_source || "Orgánico"}
                      onValueChange={(val) => updateContactSource.mutate(val)}
                    >
                      <SelectTrigger className="w-36 h-8 rounded-sm text-xs bg-white border-slate-300 font-medium">
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

                {/* Meta Ads source card if applicable */}
                {active.contact?.lead_source === "Meta Ads" && (
                  <div className="p-4 bg-orange-50/30 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-extrabold uppercase tracking-wider text-orange-600">Meta Ads</span>
                      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold bg-orange-100 text-orange-800 border border-orange-200 leading-none">
                        Origen anuncio
                      </span>
                    </div>

                    {active.contact.meta_ad_title && (
                      <div>
                        <p className="text-[10px] font-bold text-slate-400 uppercase leading-tight">Título del anuncio</p>
                        <p className="text-xs font-semibold text-slate-900 leading-snug">{active.contact.meta_ad_title}</p>
                      </div>
                    )}
                    {active.contact.meta_ad_body && (
                      <div>
                        <p className="text-[10px] font-bold text-slate-400 uppercase leading-tight">Texto del anuncio</p>
                        <p className="text-xs text-slate-600 whitespace-pre-wrap leading-relaxed">{active.contact.meta_ad_body}</p>
                      </div>
                    )}
                    {active.contact.meta_ad_image_url && (
                      <div>
                        <p className="text-[10px] font-bold text-slate-400 uppercase leading-tight mb-1">Imagen del anuncio</p>
                        <img src={active.contact.meta_ad_image_url} alt="Ad Visual" className="max-w-full h-auto rounded-sm border border-slate-200 object-cover max-h-36" />
                      </div>
                    )}
                    {safeExternalUrl(active.contact.meta_ad_url) && (
                      <div>
                        <a href={safeExternalUrl(active.contact.meta_ad_url)} target="_blank" rel="noopener noreferrer" className="text-xs text-[#0E8DDB] font-semibold hover:underline flex items-center gap-1">
                          Ver anuncio original <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    )}
                  </div>
                )}

                {/* Lead Vinculado */}
                {active.lead && (
                  <div className="p-4 space-y-2">
                    <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 leading-tight">
                      Lead Vinculado
                    </p>
                    <p className="font-bold text-slate-900 text-sm leading-snug">{active.lead.title}</p>
                    <div className="flex items-center justify-between pt-1">
                      <StatusBadge list={LEAD_STATUSES} value={active.lead.status} />
                      <span className="font-extrabold text-slate-900 text-sm tracking-tight">{money(active.lead.value)}</span>
                    </div>
                  </div>
                )}

                {/* Status & Work Area on Mobile */}
                <div className="p-4 space-y-3 lg:hidden">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 leading-tight">
                    Gestión de Conversación
                  </p>
                  <div>
                    <label className="text-xs font-semibold text-slate-600 block mb-1">Estado de conversación</label>
                    <Select value={active.status} onValueChange={(v) => patchConv.mutate({ status: v })}>
                      <SelectTrigger data-testid="conv-status-select-mobile" className="w-full rounded-sm h-9 text-xs font-semibold border-slate-300">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {CONV_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-xs font-semibold text-slate-600 block mb-1">Área asignada</label>
                    <Select value={active.assigned_work_area || "none"} onValueChange={(v) => patchConv.mutate({ assigned_work_area: v === "none" ? null : v })}>
                      <SelectTrigger data-testid="conv-work-area-select-mobile" className="w-full rounded-sm h-9 text-xs font-semibold border-slate-300">
                        <SelectValue placeholder="Área" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">Sin área</SelectItem>
                        {workAreas.map((wa) => (
                          <SelectItem key={wa.id} value={wa.id}>{wa.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                {/* Operator Assignment */}
                <div className="p-4 space-y-2">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 leading-tight">
                    Responsable Humano
                  </p>
                  <Select
                    disabled={readOnly}
                    value={active.assigned_to || "unassigned"}
                    onValueChange={(v) => {
                      patchConv.mutate({ assigned_to: v === "unassigned" ? null : v });
                      toast.success("Asignación actualizada");
                    }}
                  >
                    <SelectTrigger data-testid="inbox-assignee-select" className="w-full rounded-sm h-9 text-xs font-semibold border-slate-300">
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

                {/* Web Chat Link Section */}
                <div className="p-4 border-t border-slate-200 bg-purple-50/30 space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-[11px] font-bold uppercase tracking-wider text-purple-900 flex items-center gap-1">
                      <Globe className="h-3.5 w-3.5 text-purple-600" /> Link de Chat Web
                    </p>
                    <span className="text-[10px] bg-purple-100 text-purple-800 font-mono px-1.5 py-0.5 rounded font-bold">
                      Único
                    </span>
                  </div>
                  <p className="text-xs text-slate-600">
                    Enlace privado para conversar con este cliente en la web sin costos de WhatsApp Meta.
                  </p>
                  <div className="flex items-center gap-2 pt-1">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        const token = active.webchat_session_token || active.id;
                        const link = `${window.location.origin}/c/${token}`;
                        navigator.clipboard.writeText(link);
                        toast.success("Enlace de Chat Web copiado al portapapeles");
                      }}
                      className="text-xs font-bold gap-1 flex-1 bg-white border-purple-200 text-purple-900 hover:bg-purple-50"
                    >
                      <Copy className="h-3.5 w-3.5 text-purple-600" /> Copiar Link
                    </Button>
                    <a
                      href={`/c/${active.webchat_session_token || active.id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-bold gap-1 px-3 py-1.5 rounded-md bg-purple-700 hover:bg-purple-800 text-white inline-flex items-center"
                      title="Probar vista del cliente"
                    >
                      Probar <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </div>
                </div>
              </div>
            )}

            {/* Tab 2: Asistente IA */}
            {rightPanelTab === "ai" && (
              <div className="flex-1">
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
                  onUseDraft={(text) => {
                    setDraft(text);
                    setMobileRightOpen(false);
                    toast.success("Sugerencia copiada al composer");
                  }}
                />
              </div>
            )}

          </div>
        )}
      </div>

      {/* Dialog Plantillas WhatsApp */}
      <Dialog open={templateOpen} onOpenChange={setTemplateOpen}>
        <DialogContent className="rounded-lg border-slate-200 bg-white max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-bold text-slate-900">Enviar plantilla de recontacto</DialogTitle>
          </DialogHeader>
          <div className="space-y-3.5 py-2">
            <p className="text-xs text-slate-600 leading-relaxed">
              Usá una plantilla aprobada por Meta para volver a contactar al cliente fuera de la ventana de 24 horas.
            </p>
            <div>
              <label className="text-xs font-bold text-slate-900 block mb-1">Seleccionar Plantilla</label>
              <Select value={selectedTemplateId} onValueChange={setSelectedTemplateId}>
                <SelectTrigger className="w-full h-9 rounded-sm text-xs font-medium border-slate-300">
                  <SelectValue placeholder="Seleccionar plantilla" />
                </SelectTrigger>
                <SelectContent>
                  {recontactTemplates.map((template) => (
                    <SelectItem key={template.id} value={template.id}>
                      {template.label || template.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {selectedTemplate && (
              <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">Vista previa</p>
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-800">{selectedTemplate.rendered_preview}</p>
              </div>
            )}
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={() => setTemplateOpen(false)} className="h-9 text-xs">
              Cancelar
            </Button>
            <Button
              type="button"
              onClick={() => sendTemplate.mutate()}
              disabled={!selectedTemplateId || sendTemplate.isPending}
              className="bg-[#0E8DDB] text-white hover:bg-[#0a7ab8] h-9 px-4 text-xs font-semibold rounded-sm"
            >
              {sendTemplate.isPending ? "Enviando..." : "Enviar plantilla"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

// ---------------------------------------------------------------------------
// Right-sidebar Bot IA panel component
// ---------------------------------------------------------------------------
const BOT_STATUS_META = {
  bot_activo:        { label: "Bot activo",         color: "#16A34A", bg: "#DCFCE7", border: "#86EFAC" },
  esperando_cliente: { label: "Esperando cliente",  color: "#1D4ED8", bg: "#DBEAFE", border: "#93C5FD" },
  requiere_humano:   { label: "Requiere humano",    color: "#DC2626", bg: "#FEE2E2", border: "#FCA5A5" },
  en_atencion_humana:{ label: "En atención humana", color: "#7C3AED", bg: "#EDE9FE", border: "#C4B5FD" },
  cerrada:           { label: "Cerrada",            color: "#64748B", bg: "#F1F5F9", border: "#CBD5E1" },
};

function BotStatusPill({ status }) {
  const m = BOT_STATUS_META[status] || BOT_STATUS_META.bot_activo;
  return (
    <span
      data-testid={`bot-status-pill-${status || "bot_activo"}`}
      className="inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-sm border leading-none"
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
    <div data-testid="bot-panel" className="p-4 space-y-4">
      {/* Bot status header */}
      <div>
        <p className="text-[10px] font-bold tracking-wider uppercase text-slate-400 mb-2 leading-tight">Estado del bot</p>
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
            className={`rounded-sm h-8 px-3 text-xs font-bold transition-all shadow-2xs flex items-center gap-1.5 ${
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

      {/* Intent and Confidence */}
      {(intent || confidencePct) && (
        <div className="bg-slate-50 border border-slate-200 rounded-sm p-2.5">
          <p className="text-[10px] font-bold tracking-wider uppercase text-slate-400 mb-1 leading-tight">Intención detectada</p>
          <div className="flex items-center justify-between gap-2" data-testid="bot-intent-block">
            <span className="text-xs font-bold text-slate-900 leading-snug">{intent || "—"}</span>
            {confidencePct && (
              <span data-testid="bot-confidence-value" className="text-xs font-extrabold text-[#0E8DDB] bg-white border border-slate-200 rounded-sm px-1.5 py-0.5 leading-none">
                {confidencePct}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Handoff reason */}
      {reason && (
        <div data-testid="bot-handoff-reason" className="border border-red-200 bg-red-50/70 rounded-sm p-3">
          <p className="flex items-center gap-1 text-[10px] font-bold tracking-wider uppercase text-red-700 mb-1 leading-tight">
            <AlertOctagon className="h-3.5 w-3.5 shrink-0 text-red-600" /> Motivo de derivación
          </p>
          <p className="text-xs text-red-900 leading-relaxed font-medium">{reason}</p>
        </div>
      )}

      {/* Next best action */}
      {nba && (
        <div data-testid="bot-next-action" className="bg-blue-50/50 border border-blue-100 rounded-sm p-2.5">
          <p className="text-[10px] font-bold tracking-wider uppercase text-[#0E8DDB] mb-1 leading-tight">Próxima acción sugerida</p>
          <p className="text-xs text-slate-800 font-medium flex items-start gap-1.5 leading-relaxed">
            <ChevronRight className="h-3.5 w-3.5 text-[#0E8DDB] mt-0.5 shrink-0" /> {nba}
          </p>
        </div>
      )}

      {/* Summary */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <p className="text-[10px] font-bold tracking-wider uppercase text-slate-400 leading-tight">Resumen Conversacional</p>
          <button
            data-testid="bot-summary-regenerate"
            onClick={() => regenSummary.mutate()}
            disabled={readOnly || regenSummary.isPending}
            className="text-[11px] font-bold text-[#0E8DDB] flex items-center gap-1 hover:underline disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${regenSummary.isPending ? "animate-spin" : ""}`} /> Regenerar
          </button>
        </div>
        <div
          data-testid="bot-summary-output"
          className="border border-slate-200 bg-white rounded-sm p-3 min-h-[64px] text-xs text-slate-800 leading-relaxed whitespace-pre-wrap shadow-2xs"
        >
          {regenSummary.isPending ? (
            <span className="text-slate-400 animate-pulse font-medium">Analizando la conversación con IA…</span>
          ) : summary ? (
            summary
          ) : (
            <span className="text-slate-400">Aún no hay resumen. Tocá Regenerar para crearlo.</span>
          )}
        </div>
        {conv?.last_summary_at && (
          <p className="text-[10px] text-slate-400 leading-tight">Actualizado: {new Date(conv.last_summary_at).toLocaleString("es-AR")}</p>
        )}
      </div>

      {/* Suggested reply */}
      <div className="space-y-1.5 pt-1">
        <div className="flex items-center justify-between">
          <p className="flex items-center gap-1 text-[10px] font-bold tracking-wider uppercase text-slate-400 leading-tight">
            <Lightbulb className="h-3.5 w-3.5 text-[#0E8DDB]" /> Sugerencia de respuesta
          </p>
          <button
            data-testid="bot-suggest-button"
            onClick={() => suggestReply.mutate()}
            disabled={readOnly || suggestReply.isPending}
            className="text-[11px] font-bold text-[#0E8DDB] flex items-center gap-1 hover:underline disabled:opacity-50"
          >
            {suggestReply.isPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
            Sugerir
          </button>
        </div>
        {suggestionDraft ? (
          <div className="border border-sky-200 bg-sky-50/50 rounded-sm p-3 space-y-2">
            <Textarea
              data-testid="bot-suggestion-textarea"
              value={suggestionDraft}
              onChange={(e) => setSuggestionDraft(e.target.value)}
              className="rounded-sm bg-white border-slate-300 text-xs leading-relaxed min-h-[85px]"
              disabled={readOnly}
            />
            {suggestionMeta && (
              <div className="flex items-center gap-2 text-[10px] text-slate-500 font-medium">
                {suggestionMeta.intent && <span>Intención: <b className="text-slate-800">{suggestionMeta.intent}</b></span>}
                {suggestionMeta.confidence > 0 && (
                  <span>· Confianza: <b className="text-[#0E8DDB]">{Math.round(suggestionMeta.confidence * 100)}%</b></span>
                )}
              </div>
            )}
            <div className="flex gap-2 pt-1">
              <Button
                data-testid="bot-suggestion-use"
                size="sm"
                disabled={readOnly || !suggestionDraft.trim()}
                onClick={() => onUseDraft(suggestionDraft)}
                className="bg-[#0E8DDB] hover:bg-[#0a7ab8] text-white rounded-sm h-7 text-xs font-semibold flex-1 shadow-2xs"
              >
                <Copy className="h-3 w-3 mr-1" /> Copiar al chat
              </Button>
              <Button
                data-testid="bot-suggestion-discard"
                size="sm"
                variant="outline"
                onClick={clearSuggestion}
                className="rounded-sm h-7 text-xs font-semibold border-slate-300"
              >
                Descartar
              </Button>
            </div>
          </div>
        ) : (
          <div className="border border-slate-200 bg-slate-50 rounded-sm p-3 text-xs text-slate-500 leading-relaxed">
            Generá un borrador inteligente con la IA basado en el contexto de la conversación.
          </div>
        )}
      </div>
    </div>
  );
}
