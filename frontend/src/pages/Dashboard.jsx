import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Cell, Tooltip, AreaChart, Area } from "recharts";
import {
  Target, DollarSign, TrendingUp, MessageSquare, Bot, CheckSquare, ArrowUpRight,
  AlertTriangle, ArrowRightLeft, AlarmClock, Mail, UserX, Users, ShoppingBag, Calendar
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { LEAD_STATUSES, CONV_STATUSES, money, statusMeta } from "@/lib/constants";
import { StatusBadge, Avatar } from "@/components/Bits";
import { useAuth } from "@/context/AuthContext";

function Metric({ label, value, sub, compareValue, testid }) {
  let difference = null;
  if (compareValue !== undefined && compareValue !== null) {
    const valNum = typeof value === "number" ? value : parseFloat(String(value).replace(/[^0-9.-]+/g, ""));
    const compNum = typeof compareValue === "number" ? compareValue : parseFloat(String(compareValue).replace(/[^0-9.-]+/g, ""));
    if (!isNaN(valNum) && !isNaN(compNum) && compNum > 0) {
      const diffPct = ((valNum - compNum) / compNum) * 100;
      difference = {
        pct: diffPct.toFixed(1),
        isPositive: diffPct >= 0
      };
    }
  }

  return (
    <div className="latus-card min-h-[132px] p-5 transition-all duration-300 hover:-translate-y-0.5 hover:border-latus-ink/25 hover:shadow-[0_16px_35px_rgba(13,31,42,0.07)]" data-testid={testid}>
      <span className="text-xs font-bold uppercase tracking-[0.12em] text-latus-ink">{label}</span>
      <p className="latus-editorial mt-1 text-[2.55rem] leading-none text-[#123c58]">{value}</p>
      <div className="mt-2 flex items-center justify-between text-sm">
        {sub && <span className="text-latus-muted">{sub}</span>}
        {difference && (
          <span className={`font-semibold ml-auto ${difference.isPositive ? "text-green-600" : "text-red-600"}`}>
            {difference.isPositive ? "+" : ""}{difference.pct}% vs anterior
          </span>
        )}
      </div>
    </div>
  );
}

function AttnColumn({ icon: Icon, title, count, children, testid }) {
  return (
    <div className="min-h-[132px] p-4" data-testid={testid}>
      <div className="mb-2 flex items-center justify-between px-2">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-latus-ice/70" />
          <span className="text-sm font-medium text-white/90">{title}</span>
        </div>
        <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs font-bold text-latus-ice">{count}</span>
      </div>
      <div className="space-y-0.5">
        {count === 0 ? <p className="px-2 py-3 text-sm text-white/45">Sin pendientes</p> : children}
      </div>
    </div>
  );
}

const formatDate = (date) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
};

const getPastDate = (daysAgo) => {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return formatDate(d);
};

const parseDateString = (str) => {
  const [y, m, d] = str.split("-").map(Number);
  return new Date(y, m - 1, d);
};

export default function Dashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();

  const [startDate, setStartDate] = useState(getPastDate(30));
  const [endDate, setEndDate] = useState(formatDate(new Date()));
  const [compare, setCompare] = useState(false);
  const [compareStartDate, setCompareStartDate] = useState(getPastDate(60));
  const [compareEndDate, setCompareEndDate] = useState(getPastDate(31));

  const { data: m } = useQuery({ 
    queryKey: ["metrics", startDate, endDate, compare, compareStartDate, compareEndDate], 
    queryFn: () => api.get("/dashboard/metrics", {
      params: {
        start_date: startDate,
        end_date: endDate,
        compare_start_date: compare ? compareStartDate : undefined,
        compare_end_date: compare ? compareEndDate : undefined
      }
    }).then((r) => r.data) 
  });
  const { data: convs = [] } = useQuery({ queryKey: ["convs-recent"], queryFn: () => api.get("/conversations").then((r) => r.data) });

  const chartData = m
    ? LEAD_STATUSES.map((s) => ({ name: s.label, value: m.leads_by_status[s.key] || 0, color: s.color }))
    : [];

  const attn = m?.requires_attention || { open_handoffs: [], unread_conversations: [], overdue_tasks: [], no_response: [] };
  const attnTotal = attn.open_handoffs.length + attn.unread_conversations.length + attn.overdue_tasks.length + (attn.no_response?.length || 0);

  const role = user?.role ? user.role.toLowerCase() : "";
  const isAdminOrSupervisor = role === "admin" || role === "supervisor";
  const firstName = (user?.name || "Usuario").trim().split(/\s+/)[0];

  const [activeTab, setActiveTab] = useState("presales");

  const handleCompareToggle = (checked) => {
    setCompare(checked);
    if (checked) {
      const start = parseDateString(startDate);
      const end = parseDateString(endDate);
      const diffTime = Math.abs(end - start);
      const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
      
      const compEnd = new Date(start);
      compEnd.setDate(compEnd.getDate() - 1);
      const compStart = new Date(compEnd);
      compStart.setDate(compStart.getDate() - diffDays + 1);
      
      setCompareStartDate(formatDate(compStart));
      setCompareEndDate(formatDate(compEnd));
    }
  };

  const dateFilterActions = (
    <Popover>
      <PopoverTrigger asChild>
        <button 
          className="flex items-center gap-2 rounded-md border border-latus-warm-border bg-latus-surface px-3 py-2 text-xs font-semibold text-latus-ink shadow-sm transition-colors hover:border-latus-ink/30"
          data-testid="date-picker-trigger"
        >
          <Calendar className="h-3.5 w-3.5 text-[#0E8DDB]" />
          <span>
            {startDate} al {endDate}
          </span>
          {compare && (
            <span className="text-[10px] text-zinc-500 font-normal">
              (vs {compareStartDate} al {compareEndDate})
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="latus-card w-80 space-y-4 p-5 shadow-md">
        {/* Presets */}
        <div className="flex gap-1.5 border-b border-[#E9E6DC]/60 pb-3">
          <button
            onClick={() => {
              setStartDate(getPastDate(7));
              setEndDate(formatDate(new Date()));
              if (compare) {
                setCompareStartDate(getPastDate(14));
                setCompareEndDate(getPastDate(8));
              }
            }}
            className="px-2 py-1 bg-latus-warm-gray hover:bg-zinc-200 rounded-sm text-[10px] font-bold text-[#0B1B26] transition-colors cursor-pointer"
            data-testid="preset-7d"
          >
            7 días
          </button>
          <button
            onClick={() => {
              setStartDate(getPastDate(30));
              setEndDate(formatDate(new Date()));
              if (compare) {
                setCompareStartDate(getPastDate(60));
                setCompareEndDate(getPastDate(31));
              }
            }}
            className="px-2 py-1 bg-latus-warm-gray hover:bg-zinc-200 rounded-sm text-[10px] font-bold text-[#0B1B26] transition-colors cursor-pointer"
            data-testid="preset-30d"
          >
            30 días
          </button>
          <button
            onClick={() => {
              const startOfMonth = new Date();
              startOfMonth.setDate(1);
              setStartDate(formatDate(startOfMonth));
              setEndDate(formatDate(new Date()));
              if (compare) {
                const prevMonthStart = new Date();
                prevMonthStart.setMonth(prevMonthStart.getMonth() - 1);
                prevMonthStart.setDate(1);
                const prevMonthEnd = new Date();
                prevMonthEnd.setDate(0);
                setCompareStartDate(formatDate(prevMonthStart));
                setCompareEndDate(formatDate(prevMonthEnd));
              }
            }}
            className="px-2 py-1 bg-latus-warm-gray hover:bg-zinc-200 rounded-sm text-[10px] font-bold text-[#0B1B26] transition-colors cursor-pointer"
            data-testid="preset-month"
          >
            Este mes
          </button>
        </div>

        <div className="space-y-1.5">
          <h4 className="font-bold text-xs tracking-wider uppercase text-[#0B1B26]">Rango de fechas</h4>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <label className="text-[10px] text-[#888888] font-semibold">Desde</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full text-xs bg-white border border-[#E9E6DC] rounded-sm px-2 py-1 outline-none text-[#0B1B26]"
                data-testid="filter-start-date"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-[#888888] font-semibold">Hasta</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full text-xs bg-white border border-[#E9E6DC] rounded-sm px-2 py-1 outline-none text-[#0B1B26]"
                data-testid="filter-end-date"
              />
            </div>
          </div>
        </div>

        <div className="border-t border-[#E9E6DC]/60 pt-3 space-y-3">
          <div className="flex items-center justify-between">
            <label htmlFor="compare-checkbox" className="text-xs font-semibold text-[#888888] select-none cursor-pointer">
              Comparar período anterior
            </label>
            <input
              type="checkbox"
              id="compare-checkbox"
              checked={compare}
              onChange={(e) => handleCompareToggle(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-[#E9E6DC] text-[#0E8DDB] focus:ring-[#0E8DDB] cursor-pointer"
              data-testid="compare-checkbox"
            />
          </div>

          {compare && (
            <div className="grid grid-cols-2 gap-2 animate-in fade-in duration-200">
              <div className="space-y-1">
                <label className="text-[10px] text-[#888888] font-semibold">vs Desde</label>
                <input
                  type="date"
                  value={compareStartDate}
                  onChange={(e) => setCompareStartDate(e.target.value)}
                  className="w-full text-xs bg-white border border-[#E9E6DC] rounded-sm px-2 py-1 outline-none text-[#0B1B26]"
                  data-testid="compare-start-date"
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] text-[#888888] font-semibold">vs Hasta</label>
                <input
                  type="date"
                  value={compareEndDate}
                  onChange={(e) => setCompareEndDate(e.target.value)}
                  className="w-full text-xs bg-white border border-[#E9E6DC] rounded-sm px-2 py-1 outline-none text-[#0B1B26]"
                  data-testid="compare-end-date"
                />
              </div>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );

  return (
    <AppLayout title="Panel principal" actions={dateFilterActions}>
      <div className="mx-auto max-w-[1720px] space-y-5 p-4 animate-in fade-in duration-300 md:p-6 xl:p-7">
        
        {/* Welcome Section */}
        <div className="latus-surface flex flex-col justify-between gap-4 p-6 transition-all duration-300 md:flex-row md:items-center">
          <div>
            <h2 className="text-[2.1rem] font-light leading-tight tracking-[-0.035em] text-latus-ink md:text-[2.6rem]">
              Hola, de nuevo <span className="latus-editorial text-[#123c58]">{firstName}</span>
            </h2>
            <p className="mt-1 text-sm text-latus-muted md:text-base">
              {isAdminOrSupervisor 
                ? "Este es el resumen general del rendimiento y actividad de Latus CRM."
                : "Este es el resumen de tus leads, conversaciones y tareas asignadas."}
            </p>
          </div>
          {isAdminOrSupervisor && (
            <div className="flex w-fit items-center gap-2 rounded-full border border-latus-blue/20 bg-latus-ice px-3 py-1.5 text-xs font-semibold text-latus-blue">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-latus-blue" />
              Vista Administrador
            </div>
          )}
        </div>

        {/* Tabs Switcher */}
        <div className="flex gap-1 border-b-2 border-latus-ink">
          <button
            onClick={() => setActiveTab("presales")}
            className={`rounded-t-md px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] transition-all duration-200 ${
              activeTab === "presales"
                ? "bg-latus-ink text-white"
                : "text-latus-muted hover:bg-latus-surface/70 hover:text-latus-ink"
            }`}
          >
            Embudo de Pre-venta
          </button>
          <button
            onClick={() => setActiveTab("sales")}
            className={`rounded-t-md px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] transition-all duration-200 ${
              activeTab === "sales"
                ? "bg-latus-ink text-white"
                : "text-latus-muted hover:bg-latus-surface/70 hover:text-latus-ink"
            }`}
          >
            Ventas y Clientes
          </button>
        </div>

        {activeTab === "presales" ? (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <Metric icon={DollarSign} label="Valor del pipeline" value={money(m?.pipeline_value)} sub={`${money(m?.won_value)} ganado`} compareValue={compare ? money(m?.comparison?.pipeline_value) : undefined} testid="metric-pipeline" />
              <Metric icon={Target} label="Leads activos" value={m?.total_leads ?? "—"} sub={`${m?.total_contacts ?? 0} contactos`} compareValue={compare ? (m?.comparison?.total_leads ?? 0) : undefined} testid="metric-leads" />
              <Metric icon={TrendingUp} label="Conversión" value={`${m?.conversion_rate ?? 0}%`} sub="ganados / cerrados" compareValue={compare ? `${m?.comparison?.conversion_rate ?? 0}%` : undefined} testid="metric-conversion" />
              <Metric icon={MessageSquare} label="Chats abiertos" value={m?.open_conversations ?? "—"} sub={`${m?.pending_conversations ?? 0} pendientes`} testid="metric-chats" />
            </div>

            {/* Requires attention */}
            <div className="overflow-hidden rounded-lg border border-latus-ink/80 bg-latus-ink text-white shadow-[0_18px_38px_rgba(13,31,42,0.08)]" data-testid="requires-attention">
              <div className="flex items-center gap-2 border-b border-white/10 px-5 py-4">
                <AlertTriangle className="h-4 w-4 text-latus-blue" />
                <h3 className="text-sm font-bold uppercase tracking-[0.1em] text-white">Requiere atención</h3>
                <span className="rounded-full bg-latus-blue px-2 py-0.5 text-xs font-bold text-white">{attnTotal}</span>
              </div>
              <div className="grid grid-cols-1 divide-y divide-white/10 sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-4">
                {/* Handoffs */}
                <AttnColumn icon={ArrowRightLeft} title="Atención humana" count={attn.open_handoffs.length} testid="attn-handoffs">
                  {attn.open_handoffs.slice(0, 4).map((c) => (
                    <button key={c.id} onClick={() => navigate("/inbox", { state: { convId: c.id } })} data-testid={`attn-handoff-${c.id}`} className="flex w-full items-center gap-2.5 rounded-md bg-white/[0.05] p-2 text-left transition-colors hover:bg-white/10">
                      <Avatar src={c.contact_avatar} name={c.contact_name} size={28} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-white">{c.contact_name}</p>
                        <p className="truncate text-xs text-white/45">{c.last_message}</p>
                      </div>
                    </button>
                  ))}
                </AttnColumn>
                {/* Unread */}
                <AttnColumn icon={Mail} title="Chats sin leer" count={attn.unread_conversations.length} testid="attn-unread">
                  {attn.unread_conversations.slice(0, 4).map((c) => (
                    <button key={c.id} onClick={() => navigate("/inbox", { state: { convId: c.id } })} data-testid={`attn-unread-${c.id}`} className="flex w-full items-center gap-2.5 rounded-md bg-white/[0.05] p-2 text-left transition-colors hover:bg-white/10">
                      <Avatar src={c.contact_avatar} name={c.contact_name} size={28} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-white">{c.contact_name}</p>
                        <p className="truncate text-xs text-white/45">{c.last_message}</p>
                      </div>
                      <span className="flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-latus-blue px-1 text-[10px] font-bold text-white">{c.unread}</span>
                    </button>
                  ))}
                </AttnColumn>
                {/* Lead sin respuesta */}
                <AttnColumn icon={UserX} title="Lead sin respuesta" count={attn.no_response?.length || 0} testid="attn-no-response">
                  {(attn.no_response || []).slice(0, 4).map((c) => (
                    <button key={c.id} onClick={() => navigate("/inbox", { state: { convId: c.id } })} data-testid={`attn-no-response-${c.id}`} className="flex w-full items-center gap-2.5 rounded-md bg-white/[0.05] p-2 text-left transition-colors hover:bg-white/10">
                      <Avatar src={c.contact_avatar} name={c.contact_name} size={28} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-white">{c.contact_name}</p>
                        <p className="truncate text-xs text-latus-ice/60">{c.last_message}</p>
                      </div>
                    </button>
                  ))}
                </AttnColumn>
                {/* Overdue tasks */}
                <AttnColumn icon={AlarmClock} title="Tareas vencidas" count={attn.overdue_tasks.length} testid="attn-overdue">
                  {attn.overdue_tasks.slice(0, 4).map((t) => (
                    <button key={t.id} onClick={() => navigate("/tasks")} data-testid={`attn-overdue-${t.id}`} className="flex w-full items-center gap-2.5 rounded-md bg-white/[0.05] p-2 text-left transition-colors hover:bg-white/10">
                      <span className="h-2 w-2 shrink-0 rounded-full bg-latus-coral" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-white">{t.title}</p>
                        <p className="truncate text-xs text-latus-coral">Vence {t.due_date}</p>
                      </div>
                    </button>
                  ))}
                </AttnColumn>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Pipeline chart */}
              <div className="latus-card p-6 lg:col-span-2">
                <div className="flex items-center justify-between mb-6">
                  <div>
                    <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Leads por etapa</h3>
                    <p className="text-sm text-[#888888]">Distribución en el embudo</p>
                  </div>
                  <button onClick={() => navigate("/pipeline")} className="text-sm font-semibold text-[#0E8DDB] flex items-center gap-1 hover:gap-2 transition-all" data-testid="view-pipeline-link">
                    Pipeline <ArrowUpRight className="h-4 w-4" />
                  </button>
                </div>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={chartData} barCategoryGap={24}>
                    <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#888888" }} axisLine={{ stroke: "#E4E4E7" }} tickLine={false} />
                    <YAxis tick={{ fontSize: 12, fill: "#888888" }} axisLine={false} tickLine={false} allowDecimals={false} />
                    <Tooltip cursor={{ fill: "#F4F4F5" }} contentStyle={{ borderRadius: 2, border: "1px solid #E4E4E7", fontSize: 13 }} />
                    <Bar dataKey="value" radius={[2, 2, 0, 0]}>
                      {chartData.map((e, i) => <Cell key={i} fill={e.color} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* Quick stats */}
              <div className="space-y-4">
                <div className="latus-card p-6 text-latus-ink">
                  <Bot className="mb-3 h-5 w-5 text-latus-blue" />
                  <p className="text-xs font-bold uppercase tracking-[0.15em] text-latus-muted">Atendido por humano</p>
                  <p className="latus-editorial mt-1 text-4xl text-[#123c58]">{m?.human_handled ?? "—"}</p>
                  <p className="mt-1 text-xs text-latus-muted">conversaciones fuera del bot</p>
                </div>
                <div className="latus-card p-6">
                  <CheckSquare className="h-5 w-5 text-[#0E8DDB] mb-3" />
                  <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#888888]">Tareas abiertas</p>
                  <p className="text-3xl font-extrabold tracking-tighter mt-1 text-[#0B1B26]">{m?.open_tasks ?? "—"}</p>
                  <button onClick={() => navigate("/tasks")} className="text-xs font-semibold text-[#0E8DDB] mt-2" data-testid="view-tasks-link">Ver tareas →</button>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Leads por día chart */}
              <div className="latus-card p-6" data-testid="leads-per-day-chart">
                <div>
                  <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Leads por día</h3>
                  <p className="text-sm text-[#888888] mb-6">Cantidad de leads registrados por fecha</p>
                </div>
                {m?.leads_trend && m.leads_trend.length > 0 ? (
                  <ResponsiveContainer width="100%" height={240}>
                    <AreaChart data={m.leads_trend}>
                      <defs>
                        <linearGradient id="colorLeads" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#0E8DDB" stopOpacity={0.2}/>
                          <stop offset="95%" stopColor="#0E8DDB" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="date" tick={{ fontSize: 12, fill: "#888888" }} axisLine={{ stroke: "#E4E4E7" }} tickLine={false} />
                      <YAxis tick={{ fontSize: 12, fill: "#888888" }} axisLine={false} tickLine={false} allowDecimals={false} />
                      <Tooltip contentStyle={{ borderRadius: 2, border: "1px solid #E4E4E7", fontSize: 13 }} />
                      <Area type="monotone" dataKey="value" stroke="#0E8DDB" name="Leads" strokeWidth={2} fillOpacity={1} fill="url(#colorLeads)" />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-[240px] flex items-center justify-center text-sm text-[#888888] border border-dashed border-[#E9E6DC] rounded-sm">
                    No hay datos de leads para el período seleccionado
                  </div>
                )}
              </div>

              {/* Distribución por origen chart */}
              <div className="latus-card p-6" data-testid="leads-by-source-chart">
                <div>
                  <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Distribución por origen</h3>
                  <p className="text-sm text-[#888888] mb-6">Cantidad de leads por canal de proveniencia</p>
                </div>
                {m?.leads_by_source && m.leads_by_source.length > 0 ? (
                  <ResponsiveContainer width="100%" height={240}>
                    <BarChart data={m.leads_by_source} barCategoryGap={16}>
                      <XAxis dataKey="source" tick={{ fontSize: 12, fill: "#888888" }} axisLine={{ stroke: "#E4E4E7" }} tickLine={false} />
                      <YAxis tick={{ fontSize: 12, fill: "#888888" }} axisLine={false} tickLine={false} allowDecimals={false} />
                      <Tooltip cursor={{ fill: "#F4F4F5" }} contentStyle={{ borderRadius: 2, border: "1px solid #E4E4E7", fontSize: 13 }} />
                      <Bar dataKey="count" name="Leads" fill="#0E8DDB" radius={[2, 2, 0, 0]}>
                        {m.leads_by_source.map((entry, index) => {
                          const colors = ["#0E8DDB", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899"];
                          return <Cell key={`cell-${index}`} fill={colors[index % colors.length]} />;
                        })}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-[240px] flex items-center justify-center text-sm text-[#888888] border border-dashed border-[#E9E6DC] rounded-sm">
                    No hay datos de origen para el período seleccionado
                  </div>
                )}
              </div>
            </div>

            {/* Recent conversations */}
            <div className="latus-card overflow-hidden">
              <div className="flex items-center justify-between p-5 border-b border-[#E9E6DC]">
                <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Conversaciones recientes</h3>
                <button onClick={() => navigate("/inbox")} className="text-sm font-semibold text-[#0E8DDB] flex items-center gap-1" data-testid="view-inbox-link">
                  Abrir bandeja <ArrowUpRight className="h-4 w-4" />
                </button>
              </div>
              <div className="divide-y divide-zinc-100">
                {convs.slice(0, 5).map((c) => (
                  <button
                    key={c.id}
                    onClick={() => navigate("/inbox", { state: { convId: c.id } })}
                    className="w-full flex items-center gap-4 p-4 hover:bg-latus-cream transition-colors text-left"
                    data-testid={`recent-conv-${c.id}`}
                  >
                    <Avatar src={c.contact?.avatar} name={c.contact?.name} />
                    <div className="flex-1 min-w-0">
                      <p className="font-semibold text-sm text-[#0B1B26] truncate">{c.contact?.name}</p>
                      <p className="text-sm text-[#888888] truncate">{c.last_message}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {!c.bot_enabled && (
                        <span className="text-xs font-bold text-[#0E8DDB] bg-[#F4F2EC] border border-[#EFE3E1] rounded-full px-2 py-0.5">HUMANO</span>
                      )}
                      <StatusBadge list={CONV_STATUSES} value={c.status} />
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </>
        ) : (
          <>
            {/* Sales & Customers metrics */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <Metric icon={Users} label="Clientes totales" value={m?.sales?.total_customers ?? 0} sub="con compras ganadas" compareValue={compare ? (m?.comparison?.sales?.total_customers ?? 0) : undefined} testid="metric-sales-customers" />
              <Metric icon={DollarSign} label="Facturación total" value={money(m?.won_value)} sub="ventas realizadas" compareValue={compare ? money(m?.comparison?.won_value) : undefined} testid="metric-sales-billing" />
              <Metric icon={TrendingUp} label="Ticket promedio" value={money(m?.sales?.average_ticket)} sub="por oportunidad ganada" compareValue={compare ? money(m?.comparison?.sales?.average_ticket) : undefined} testid="metric-sales-ticket" />
              <Metric icon={ArrowRightLeft} label="Clientes recurrentes" value={m?.sales?.recurring_customers ?? 0} sub="compras múltiples" compareValue={compare ? (m?.comparison?.sales?.recurring_customers ?? 0) : undefined} testid="metric-sales-recurring" />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Sales trend chart */}
              <div className="latus-card p-6 lg:col-span-2">
                <div>
                  <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Facturación mensual</h3>
                  <p className="text-sm text-[#888888] mb-6">Histórico de ingresos por mes</p>
                </div>
                {m?.sales?.sales_trend && m.sales.sales_trend.length > 0 ? (
                  <ResponsiveContainer width="100%" height={240}>
                    <AreaChart data={m.sales.sales_trend}>
                      <defs>
                        <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#0E8DDB" stopOpacity={0.2}/>
                          <stop offset="95%" stopColor="#0E8DDB" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="month" tick={{ fontSize: 12, fill: "#888888" }} axisLine={{ stroke: "#E4E4E7" }} tickLine={false} />
                      <YAxis tick={{ fontSize: 12, fill: "#888888" }} axisLine={false} tickLine={false} tickFormatter={(v) => money(v)} />
                      <Tooltip contentStyle={{ borderRadius: 2, border: "1px solid #E4E4E7", fontSize: 13 }} formatter={(v) => [money(v), "Ventas"]} />
                      <Area type="monotone" dataKey="value" stroke="#0E8DDB" strokeWidth={2} fillOpacity={1} fill="url(#colorValue)" />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-[240px] flex items-center justify-center text-sm text-[#888888] border border-dashed border-[#E9E6DC] rounded-sm">
                    Aún no hay datos de ventas para mostrar
                  </div>
                )}
              </div>

              {/* Top Products */}
              <div className="latus-card flex flex-col justify-between p-6">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <ShoppingBag className="h-4 w-4 text-[#0E8DDB]" />
                    <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Top Productos</h3>
                  </div>
                  <p className="text-sm text-[#888888] mb-4">Productos más vendidos</p>
                  
                  <div className="space-y-4">
                    {m?.sales?.top_products && m.sales.top_products.length > 0 ? (
                      m.sales.top_products.map((p, idx) => (
                        <div key={idx} className="flex items-center justify-between border-b border-zinc-50 pb-2 last:border-0 last:pb-0">
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-semibold text-[#0B1B26] truncate">{p.name}</p>
                            <p className="text-xs text-[#888888]">{p.quantity} {p.quantity === 1 ? 'unidad' : 'unidades'}</p>
                          </div>
                          <span className="text-sm font-extrabold text-[#0B1B26] ml-4">{money(p.revenue)}</span>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-[#888888] py-8 text-center">No hay productos registrados</p>
                    )}
                  </div>
                </div>
                <div className="border-t border-[#E9E6DC] pt-4 mt-6">
                  <button onClick={() => navigate("/catalogo")} className="text-xs font-semibold text-[#0E8DDB]">Ver catálogo completo →</button>
                </div>
              </div>
            </div>

            {/* Top Customers list */}
            <div className="latus-card overflow-hidden">
              <div className="p-5 border-b border-[#E9E6DC]">
                <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Top Clientes</h3>
                <p className="text-sm text-[#888888]">Clientes con mayor volumen de compra acumulado</p>
              </div>
              <div className="divide-y divide-zinc-100">
                {m?.sales?.top_customers && m.sales.top_customers.length > 0 ? (
                  m.sales.top_customers.map((c) => (
                    <button
                      key={c.contact_id}
                      onClick={() => navigate("/contacts", { state: { search: c.name } })}
                      className="w-full flex items-center gap-4 p-4 hover:bg-latus-cream transition-colors text-left"
                    >
                      <Avatar src={c.avatar} name={c.name} />
                      <div className="flex-1 min-w-0">
                        <p className="font-semibold text-sm text-[#0B1B26] truncate">{c.name}</p>
                        <p className="text-xs text-[#888888] truncate">{c.company}</p>
                      </div>
                      <div className="text-right shrink-0">
                        <p className="text-sm font-extrabold text-[#0B1B26]">{money(c.total_value)}</p>
                        <p className="text-[10px] uppercase font-bold tracking-wider text-[#0E8DDB]">Facturado</p>
                      </div>
                    </button>
                  ))
                ) : (
                  <p className="text-sm text-[#888888] p-8 text-center">No hay datos de clientes registrados</p>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </AppLayout>
  );
}
