import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Cell, Tooltip } from "recharts";
import {
  Target, DollarSign, TrendingUp, MessageSquare, Bot, CheckSquare, ArrowUpRight,
  AlertTriangle, ArrowRightLeft, AlarmClock, Mail, UserX,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { LEAD_STATUSES, CONV_STATUSES, money, statusMeta } from "@/lib/constants";
import { StatusBadge, Avatar } from "@/components/Bits";

function Metric({ icon: Icon, label, value, sub, testid }) {
  return (
    <div className="bg-white border border-zinc-200 rounded-sm p-5 hover:border-zinc-300 transition-colors" data-testid={testid}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs tracking-[0.15em] uppercase font-bold text-[#52525B]">{label}</span>
        <Icon className="h-4 w-4 text-[#FF4500]" />
      </div>
      <p className="text-3xl font-extrabold tracking-tighter text-[#0A0A0A]">{value}</p>
      {sub && <p className="text-xs text-[#52525B] mt-1">{sub}</p>}
    </div>
  );
}

function AttnColumn({ icon: Icon, title, count, children, testid }) {
  return (
    <div className="p-4" data-testid={testid}>
      <div className="flex items-center justify-between mb-2 px-2">
        <div className="flex items-center gap-2">
          <Icon className="h-3.5 w-3.5 text-[#52525B]" />
          <span className="text-xs tracking-[0.12em] uppercase font-bold text-[#52525B]">{title}</span>
        </div>
        <span className="text-xs font-bold text-[#0A0A0A] bg-zinc-100 rounded-full px-2">{count}</span>
      </div>
      <div className="space-y-0.5">
        {count === 0 ? <p className="text-sm text-zinc-400 px-2 py-3">Sin pendientes</p> : children}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { data: m } = useQuery({ queryKey: ["metrics"], queryFn: () => api.get("/dashboard/metrics").then((r) => r.data) });
  const { data: convs = [] } = useQuery({ queryKey: ["convs-recent"], queryFn: () => api.get("/conversations").then((r) => r.data) });

  const chartData = m
    ? LEAD_STATUSES.map((s) => ({ name: s.label, value: m.leads_by_status[s.key] || 0, color: s.color }))
    : [];

  const attn = m?.requires_attention || { open_handoffs: [], unread_conversations: [], overdue_tasks: [], no_response: [] };
  const attnTotal = attn.open_handoffs.length + attn.unread_conversations.length + attn.overdue_tasks.length + (attn.no_response?.length || 0);

  return (
    <AppLayout title="Panel principal">
      <div className="p-6 md:p-8 space-y-6 animate-in fade-in duration-300">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Metric icon={DollarSign} label="Valor del pipeline" value={money(m?.pipeline_value)} sub={`${money(m?.won_value)} ganado`} testid="metric-pipeline" />
          <Metric icon={Target} label="Leads activos" value={m?.total_leads ?? "—"} sub={`${m?.total_contacts ?? 0} contactos`} testid="metric-leads" />
          <Metric icon={TrendingUp} label="Conversión" value={`${m?.conversion_rate ?? 0}%`} sub="ganados / cerrados" testid="metric-conversion" />
          <Metric icon={MessageSquare} label="Chats abiertos" value={m?.open_conversations ?? "—"} sub={`${m?.pending_conversations ?? 0} pendientes`} testid="metric-chats" />
        </div>

        {/* Requires attention */}
        <div className="bg-white border border-zinc-200 rounded-sm" data-testid="requires-attention">
          <div className="flex items-center gap-2 px-5 py-4 border-b border-zinc-200">
            <AlertTriangle className="h-4 w-4 text-[#FF4500]" />
            <h3 className="text-lg font-bold tracking-tight text-[#0A0A0A]">Requiere atención</h3>
            <span className="text-xs font-bold text-white bg-[#FF4500] rounded-full px-2 py-0.5">{attnTotal}</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 divide-y sm:divide-y-0 sm:divide-x divide-zinc-100">
            {/* Handoffs */}
            <AttnColumn icon={ArrowRightLeft} title="Atención humana" count={attn.open_handoffs.length} testid="attn-handoffs">
              {attn.open_handoffs.slice(0, 4).map((c) => (
                <button key={c.id} onClick={() => navigate("/inbox", { state: { convId: c.id } })} data-testid={`attn-handoff-${c.id}`} className="w-full flex items-center gap-2.5 p-2 rounded-sm hover:bg-zinc-50 text-left transition-colors">
                  <Avatar src={c.contact_avatar} name={c.contact_name} size={28} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-[#0A0A0A] truncate">{c.contact_name}</p>
                    <p className="text-xs text-[#52525B] truncate">{c.last_message}</p>
                  </div>
                </button>
              ))}
            </AttnColumn>
            {/* Unread */}
            <AttnColumn icon={Mail} title="Chats sin leer" count={attn.unread_conversations.length} testid="attn-unread">
              {attn.unread_conversations.slice(0, 4).map((c) => (
                <button key={c.id} onClick={() => navigate("/inbox", { state: { convId: c.id } })} data-testid={`attn-unread-${c.id}`} className="w-full flex items-center gap-2.5 p-2 rounded-sm hover:bg-zinc-50 text-left transition-colors">
                  <Avatar src={c.contact_avatar} name={c.contact_name} size={28} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-[#0A0A0A] truncate">{c.contact_name}</p>
                    <p className="text-xs text-[#52525B] truncate">{c.last_message}</p>
                  </div>
                  <span className="bg-[#FF4500] text-white text-[10px] font-bold rounded-full h-4 min-w-4 px-1 flex items-center justify-center shrink-0">{c.unread}</span>
                </button>
              ))}
            </AttnColumn>
            {/* Lead sin respuesta */}
            <AttnColumn icon={UserX} title="Lead sin respuesta" count={attn.no_response?.length || 0} testid="attn-no-response">
              {(attn.no_response || []).slice(0, 4).map((c) => (
                <button key={c.id} onClick={() => navigate("/inbox", { state: { convId: c.id } })} data-testid={`attn-no-response-${c.id}`} className="w-full flex items-center gap-2.5 p-2 rounded-sm hover:bg-zinc-50 text-left transition-colors">
                  <Avatar src={c.contact_avatar} name={c.contact_name} size={28} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-[#0A0A0A] truncate">{c.contact_name}</p>
                    <p className="text-xs text-[#7C3AED] truncate">{c.last_message}</p>
                  </div>
                </button>
              ))}
            </AttnColumn>
            {/* Overdue tasks */}
            <AttnColumn icon={AlarmClock} title="Tareas vencidas" count={attn.overdue_tasks.length} testid="attn-overdue">
              {attn.overdue_tasks.slice(0, 4).map((t) => (
                <button key={t.id} onClick={() => navigate("/tasks")} data-testid={`attn-overdue-${t.id}`} className="w-full flex items-center gap-2.5 p-2 rounded-sm hover:bg-zinc-50 text-left transition-colors">
                  <span className="h-2 w-2 rounded-full bg-[#DC2626] shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-[#0A0A0A] truncate">{t.title}</p>
                    <p className="text-xs text-[#DC2626] truncate">Vence {t.due_date}</p>
                  </div>
                </button>
              ))}
            </AttnColumn>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Pipeline chart */}
          <div className="lg:col-span-2 bg-white border border-zinc-200 rounded-sm p-6">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-bold tracking-tight text-[#0A0A0A]">Leads por etapa</h3>
                <p className="text-sm text-[#52525B]">Distribución en el embudo</p>
              </div>
              <button onClick={() => navigate("/pipeline")} className="text-sm font-semibold text-[#FF4500] flex items-center gap-1 hover:gap-2 transition-all" data-testid="view-pipeline-link">
                Pipeline <ArrowUpRight className="h-4 w-4" />
              </button>
            </div>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={chartData} barCategoryGap={24}>
                <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#52525B" }} axisLine={{ stroke: "#E4E4E7" }} tickLine={false} />
                <YAxis tick={{ fontSize: 12, fill: "#52525B" }} axisLine={false} tickLine={false} allowDecimals={false} />
                <Tooltip cursor={{ fill: "#F4F4F5" }} contentStyle={{ borderRadius: 2, border: "1px solid #E4E4E7", fontSize: 13 }} />
                <Bar dataKey="value" radius={[2, 2, 0, 0]}>
                  {chartData.map((e, i) => <Cell key={i} fill={e.color} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Quick stats */}
          <div className="space-y-4">
            <div className="bg-[#0A0A0A] rounded-sm p-6 text-white">
              <Bot className="h-5 w-5 text-[#FF4500] mb-3" />
              <p className="text-xs tracking-[0.15em] uppercase font-bold text-zinc-400">Atendido por humano</p>
              <p className="text-3xl font-extrabold tracking-tighter mt-1">{m?.human_handled ?? "—"}</p>
              <p className="text-xs text-zinc-500 mt-1">conversaciones fuera del bot</p>
            </div>
            <div className="bg-white border border-zinc-200 rounded-sm p-6">
              <CheckSquare className="h-5 w-5 text-[#FF4500] mb-3" />
              <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#52525B]">Tareas abiertas</p>
              <p className="text-3xl font-extrabold tracking-tighter mt-1 text-[#0A0A0A]">{m?.open_tasks ?? "—"}</p>
              <button onClick={() => navigate("/tasks")} className="text-xs font-semibold text-[#FF4500] mt-2" data-testid="view-tasks-link">Ver tareas →</button>
            </div>
          </div>
        </div>

        {/* Recent conversations */}
        <div className="bg-white border border-zinc-200 rounded-sm">
          <div className="flex items-center justify-between p-5 border-b border-zinc-200">
            <h3 className="text-lg font-bold tracking-tight text-[#0A0A0A]">Conversaciones recientes</h3>
            <button onClick={() => navigate("/inbox")} className="text-sm font-semibold text-[#FF4500] flex items-center gap-1" data-testid="view-inbox-link">
              Abrir bandeja <ArrowUpRight className="h-4 w-4" />
            </button>
          </div>
          <div className="divide-y divide-zinc-100">
            {convs.slice(0, 5).map((c) => (
              <button
                key={c.id}
                onClick={() => navigate("/inbox", { state: { convId: c.id } })}
                className="w-full flex items-center gap-4 p-4 hover:bg-zinc-50 transition-colors text-left"
                data-testid={`recent-conv-${c.id}`}
              >
                <Avatar src={c.contact?.avatar} name={c.contact?.name} />
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-sm text-[#0A0A0A] truncate">{c.contact?.name}</p>
                  <p className="text-sm text-[#52525B] truncate">{c.last_message}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {!c.bot_enabled && (
                    <span className="text-xs font-bold text-[#FF4500] bg-[#FFF7ED] border border-[#FED7AA] rounded-full px-2 py-0.5">HUMANO</span>
                  )}
                  <StatusBadge list={CONV_STATUSES} value={c.status} />
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </AppLayout>
  );
}
