import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Cell, Tooltip } from "recharts";
import {
  Target, DollarSign, TrendingUp, MessageSquare, Bot, CheckSquare, ArrowUpRight,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { LEAD_STATUSES, money, statusMeta } from "@/lib/constants";
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

export default function Dashboard() {
  const navigate = useNavigate();
  const { data: m } = useQuery({ queryKey: ["metrics"], queryFn: () => api.get("/dashboard/metrics").then((r) => r.data) });
  const { data: convs = [] } = useQuery({ queryKey: ["convs-recent"], queryFn: () => api.get("/conversations").then((r) => r.data) });

  const chartData = m
    ? LEAD_STATUSES.map((s) => ({ name: s.label, value: m.leads_by_status[s.key] || 0, color: s.color }))
    : [];

  return (
    <AppLayout title="Dashboard">
      <div className="p-6 md:p-8 space-y-6 animate-in fade-in duration-300">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Metric icon={DollarSign} label="Pipeline Value" value={money(m?.pipeline_value)} sub={`${money(m?.won_value)} won`} testid="metric-pipeline" />
          <Metric icon={Target} label="Active Leads" value={m?.total_leads ?? "—"} sub={`${m?.total_contacts ?? 0} contacts`} testid="metric-leads" />
          <Metric icon={TrendingUp} label="Conversion" value={`${m?.conversion_rate ?? 0}%`} sub="won / closed" testid="metric-conversion" />
          <Metric icon={MessageSquare} label="Open Chats" value={m?.open_conversations ?? "—"} sub={`${m?.pending_conversations ?? 0} pending`} testid="metric-chats" />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Pipeline chart */}
          <div className="lg:col-span-2 bg-white border border-zinc-200 rounded-sm p-6">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-bold tracking-tight text-[#0A0A0A]">Leads by Stage</h3>
                <p className="text-sm text-[#52525B]">Distribution across the funnel</p>
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
              <p className="text-xs tracking-[0.15em] uppercase font-bold text-zinc-400">Human Handled</p>
              <p className="text-3xl font-extrabold tracking-tighter mt-1">{m?.human_handled ?? "—"}</p>
              <p className="text-xs text-zinc-500 mt-1">conversations off bot</p>
            </div>
            <div className="bg-white border border-zinc-200 rounded-sm p-6">
              <CheckSquare className="h-5 w-5 text-[#FF4500] mb-3" />
              <p className="text-xs tracking-[0.15em] uppercase font-bold text-[#52525B]">Open Tasks</p>
              <p className="text-3xl font-extrabold tracking-tighter mt-1 text-[#0A0A0A]">{m?.open_tasks ?? "—"}</p>
              <button onClick={() => navigate("/tasks")} className="text-xs font-semibold text-[#FF4500] mt-2" data-testid="view-tasks-link">View tasks →</button>
            </div>
          </div>
        </div>

        {/* Recent conversations */}
        <div className="bg-white border border-zinc-200 rounded-sm">
          <div className="flex items-center justify-between p-5 border-b border-zinc-200">
            <h3 className="text-lg font-bold tracking-tight text-[#0A0A0A]">Recent Conversations</h3>
            <button onClick={() => navigate("/inbox")} className="text-sm font-semibold text-[#FF4500] flex items-center gap-1" data-testid="view-inbox-link">
              Open inbox <ArrowUpRight className="h-4 w-4" />
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
                    <span className="text-xs font-bold text-[#FF4500] bg-[#FFF7ED] border border-[#FED7AA] rounded-full px-2 py-0.5">HUMAN</span>
                  )}
                  <StatusBadge list={[{ key: "open", label: "Open", color: "#064E3B", bg: "#ECFDF5" }, { key: "pending", label: "Pending", color: "#EAB308", bg: "#FEFCE8" }, { key: "resolved", label: "Resolved", color: "#52525B", bg: "#F4F4F5" }]} value={c.status} />
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </AppLayout>
  );
}
