import { useState, useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Send, Bot, User as UserIcon, Sparkles, Search, Phone, Building2,
  MessageSquare, Zap, Copy, RefreshCw, ArrowRightLeft,
} from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { CONV_STATUSES, PRIORITIES, money, statusMeta } from "@/lib/constants";
import { StatusBadge, Avatar } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export default function Inbox() {
  const qc = useQueryClient();
  const location = useLocation();
  const [activeId, setActiveId] = useState(location.state?.convId || null);

  useEffect(() => {
    if (location.state?.convId) setActiveId(location.state.convId);
  }, [location.state]);
  const [filters, setFilters] = useState({ status: "all", priority: "all" });
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const [summary, setSummary] = useState("");
  const [suggestion, setSuggestion] = useState("");
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

  useEffect(() => { setSummary(""); setSuggestion(""); }, [activeId]);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [active?.messages?.length]);

  const sendMsg = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/messages`, { body: draft, sender_type: "agent" }),
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
    },
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

  const genSummary = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/ai-summary`),
    onSuccess: (r) => setSummary(r.data.summary),
    onError: () => toast.error("AI summary unavailable"),
  });
  const genSuggest = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/ai-suggest`),
    onSuccess: (r) => setSuggestion(r.data.suggestion),
    onError: () => toast.error("AI suggestion unavailable"),
  });

  const simulateInbound = useMutation({
    mutationFn: () => api.post(`/conversations/${activeId}/simulate-inbound`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", activeId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["notif-count"] });
      toast.success("Inbound customer message received");
    },
  });

  const filtered = convs.filter((c) => !search || c.contact?.name?.toLowerCase().includes(search.toLowerCase()));

  return (
    <AppLayout title="Inbox">
      <div className="flex h-[calc(100vh-4rem)] w-full overflow-hidden bg-white">
        {/* Left: conversation list */}
        <div className="w-80 border-r border-zinc-200 flex flex-col bg-zinc-50 shrink-0">
          <div className="p-3 space-y-2 border-b border-zinc-200">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400" />
              <Input data-testid="inbox-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search chats…" className="pl-9 rounded-sm bg-white h-9" />
            </div>
            <div className="flex gap-2">
              <Select value={filters.status} onValueChange={(v) => setFilters({ ...filters, status: v })}>
                <SelectTrigger data-testid="inbox-filter-status" className="rounded-sm bg-white h-8 text-xs"><SelectValue placeholder="Status" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Status</SelectItem>
                  {CONV_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <Select value={filters.priority} onValueChange={(v) => setFilters({ ...filters, priority: v })}>
                <SelectTrigger data-testid="inbox-filter-priority" className="rounded-sm bg-white h-8 text-xs"><SelectValue placeholder="Priority" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Priority</SelectItem>
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
                      ? <span className="inline-flex items-center gap-1 text-[10px] font-bold text-[#FF4500] bg-[#FFF7ED] border border-[#FED7AA] rounded-full px-1.5 py-px"><UserIcon className="h-2.5 w-2.5" />HUMAN</span>
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
              <p className="text-sm">Select a conversation</p>
            </div>
          ) : (
            <>
              {/* header */}
              <div className="h-16 border-b border-zinc-200 flex items-center justify-between px-5 shrink-0">
                <div className="flex items-center gap-3">
                  <Avatar src={active.contact?.avatar} name={active.contact?.name} size={38} />
                  <div>
                    <p className="font-bold text-[#0A0A0A] leading-tight">{active.contact?.name}</p>
                    <p className="text-xs text-[#52525B]">{active.contact?.phone}</p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <button
                    data-testid="simulate-inbound-button"
                    onClick={() => simulateInbound.mutate()}
                    disabled={simulateInbound.isPending}
                    className="text-xs font-semibold text-[#52525B] hover:text-[#FF4500] border border-zinc-200 rounded-sm px-2.5 py-1.5 transition-colors"
                    title="Simulate an inbound WhatsApp message from the customer"
                  >
                    + Customer reply
                  </button>
                  <div className={`flex items-center gap-2 px-3 py-1.5 rounded-sm border ${active.bot_enabled ? "border-zinc-200 bg-zinc-50" : "border-[#FED7AA] bg-[#FFF7ED]"}`} data-testid="handoff-control">
                    <ArrowRightLeft className="h-3.5 w-3.5 text-[#FF4500]" />
                    <span className="text-xs font-bold text-[#0A0A0A]">{active.bot_enabled ? "Bot active" : "Human only"}</span>
                    <Switch
                      data-testid="bot-toggle"
                      checked={active.bot_enabled}
                      onCheckedChange={(v) => { patchConv.mutate({ bot_enabled: v }); toast.success(v ? "Bot re-enabled" : "Handed off to human"); }}
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
                  return (
                    <div key={m.id} className={`flex ${isCustomer ? "justify-start" : "justify-end"}`}>
                      <div className={`max-w-[70%] rounded-sm px-3.5 py-2 ${
                        isCustomer ? "bg-white border border-zinc-200"
                        : isBot ? "bg-zinc-100 border border-zinc-200"
                        : "bg-[#FF4500] text-white"}`}>
                        {!isCustomer && (
                          <div className={`flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide mb-0.5 ${isBot ? "text-[#52525B]" : "text-orange-100"}`}>
                            {isBot ? <><Bot className="h-2.5 w-2.5" />Bot</> : <><UserIcon className="h-2.5 w-2.5" />{m.sender_name}</>}
                          </div>
                        )}
                        <p className={`text-sm ${isCustomer || isBot ? "text-[#0A0A0A]" : "text-white"}`}>{m.body}</p>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* composer */}
              <div className="border-t border-zinc-200 p-3 shrink-0">
                {!active.bot_enabled && (
                  <p className="text-[11px] font-semibold text-[#FF4500] mb-2 flex items-center gap-1"><UserIcon className="h-3 w-3" /> Human handoff active — you are replying as agent</p>
                )}
                <div className="flex items-end gap-2">
                  <Textarea
                    data-testid="message-input"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (draft.trim()) sendMsg.mutate(); } }}
                    placeholder="Type a reply…"
                    className="rounded-sm resize-none min-h-[44px] max-h-32"
                  />
                  <Button data-testid="send-message-button" disabled={!draft.trim() || sendMsg.isPending} onClick={() => sendMsg.mutate()} className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm h-11 px-4">
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
              <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#52525B] mb-3">Contact</p>
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
                <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#52525B] mb-3">Linked Lead</p>
                <p className="font-semibold text-[#0A0A0A] text-sm">{active.lead.title}</p>
                <div className="flex items-center justify-between mt-2">
                  <StatusBadge list={[{ key: active.lead.status, label: active.lead.status, color: "#FF4500", bg: "#FFF7ED" }]} value={active.lead.status} />
                  <span className="font-extrabold tracking-tighter text-[#0A0A0A]">{money(active.lead.value)}</span>
                </div>
              </div>
            )}

            {/* AI Summary */}
            <div className="p-4 border-b border-zinc-200">
              <div className="flex items-center justify-between mb-3">
                <p className="flex items-center gap-1.5 text-xs tracking-[0.15em] uppercase font-bold text-[#FF4500]"><Sparkles className="h-3.5 w-3.5" /> AI Summary</p>
                <button data-testid="ai-summary-button" onClick={() => genSummary.mutate()} disabled={genSummary.isPending} className="text-xs font-semibold text-[#FF4500] flex items-center gap-1">
                  {genSummary.isPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />} Generate
                </button>
              </div>
              <div className="border border-[#FED7AA] bg-[#FFF7ED] rounded-sm p-3 min-h-[60px]" data-testid="ai-summary-output">
                {genSummary.isPending ? (
                  <p className="text-sm text-[#52525B] animate-pulse">Analyzing conversation…</p>
                ) : summary ? (
                  <p className="text-sm text-[#0A0A0A] whitespace-pre-wrap">{summary}</p>
                ) : (
                  <p className="text-sm text-[#52525B]">Click generate for an AI recap of this chat.</p>
                )}
              </div>
            </div>

            {/* AI Suggested reply */}
            <div className="p-4">
              <div className="flex items-center justify-between mb-3">
                <p className="flex items-center gap-1.5 text-xs tracking-[0.15em] uppercase font-bold text-[#FF4500]"><Zap className="h-3.5 w-3.5" /> Suggested Reply</p>
                <button data-testid="ai-suggest-button" onClick={() => genSuggest.mutate()} disabled={genSuggest.isPending} className="text-xs font-semibold text-[#FF4500] flex items-center gap-1">
                  {genSuggest.isPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />} Generate
                </button>
              </div>
              <div className="border border-[#FED7AA] bg-[#FFF7ED] rounded-sm p-3 min-h-[60px]" data-testid="ai-suggest-output">
                {genSuggest.isPending ? (
                  <p className="text-sm text-[#52525B] animate-pulse">Drafting reply…</p>
                ) : suggestion ? (
                  <>
                    <p className="text-sm text-[#0A0A0A]">{suggestion}</p>
                    <div className="flex gap-2 mt-3">
                      <Button data-testid="use-suggestion-button" size="sm" onClick={() => setDraft(suggestion)} className="bg-[#0A0A0A] hover:bg-[#FF4500] rounded-sm h-7 text-xs flex-1">Use reply</Button>
                      <Button size="sm" variant="outline" onClick={() => { navigator.clipboard.writeText(suggestion); toast.success("Copied"); }} className="rounded-sm h-7 px-2"><Copy className="h-3 w-3" /></Button>
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-[#52525B]">Get an AI-drafted next reply for the agent.</p>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
