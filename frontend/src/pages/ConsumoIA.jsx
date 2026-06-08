import { useMemo, useState } from "react";
import { Navigate, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  DollarSign, RefreshCw, Sparkles, Filter, AlertOctagon, ChevronLeft, ChevronRight,
  RotateCcw, ExternalLink,
} from "lucide-react";
import AppLayout from "@/components/AppLayout";
import { useAuth } from "@/context/AuthContext";
import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

const STATUS_OPTIONS = [
  { value: "",        label: "Todos los estados" },
  { value: "success", label: "Éxito" },
  { value: "error",   label: "Error" },
];

const PURPOSE_LABEL = {
  bot_pipeline:    "Bot (entrada)",
  summary_regen:   "Resumen",
  suggest_reply:   "Sugerencia",
  connection_test: "Prueba de conexión",
};

function fmtUSD(v) {
  const n = Number(v || 0);
  if (n === 0) return "USD 0";
  if (n < 0.01) return `USD ${n.toFixed(6)}`;
  if (n < 1) return `USD ${n.toFixed(4)}`;
  return `USD ${n.toFixed(2)}`;
}
function fmtInt(v) { return Number(v || 0).toLocaleString("es-AR"); }

function todayISO()    { return new Date().toISOString().slice(0, 10); }
function monthStart()  { const d = new Date(); d.setDate(1); return d.toISOString().slice(0, 10); }


export default function ConsumoIA() {
  const { user } = useAuth();
  const perms = user?.permissions || [];
  const isAdmin = perms.includes("configure_ai");

  const [filters, setFilters] = useState({
    from: monthStart(),
    to: todayISO(),
    model: "",
    status: "",
  });
  const [logsPage, setLogsPage] = useState(0);
  const LIMIT = 50;
  const rangeBad = filters.from && filters.to && filters.from > filters.to;

  const qc = useQueryClient();

  const quickQ = useQuery({
    queryKey: ["ai-usage-quick"],
    queryFn: () => api.get("/admin/ai-usage/quick").then((r) => r.data),
    enabled: isAdmin,
  });
  const params = useMemo(() => {
    if (rangeBad) return null;
    const p = new URLSearchParams();
    if (filters.from) p.set("from", filters.from);
    if (filters.to)   p.set("to", filters.to);
    if (filters.model) p.set("model", filters.model);
    if (filters.status) p.set("status", filters.status);
    return p.toString();
  }, [filters, rangeBad]);

  const summaryQ = useQuery({
    queryKey: ["ai-usage-summary", params],
    queryFn: () => api.get(`/admin/ai-usage/summary?${params}`).then((r) => r.data),
    enabled: isAdmin && params !== null,
  });
  const logsQ = useQuery({
    queryKey: ["ai-usage-logs", params, logsPage],
    queryFn: () => api.get(`/admin/ai-usage/logs?${params}&limit=${LIMIT}&offset=${logsPage * LIMIT}`)
      .then((r) => r.data),
    enabled: isAdmin && params !== null,
  });
  const pricingQ = useQuery({
    queryKey: ["ai-pricing"],
    queryFn: () => api.get("/admin/ai-pricing").then((r) => r.data),
    enabled: isAdmin,
  });

  if (user && !isAdmin) return <Navigate to="/dashboard" replace />;
  const reloadAll = () => {
    qc.invalidateQueries({ queryKey: ["ai-usage-quick"] });
    qc.invalidateQueries({ queryKey: ["ai-usage-summary"] });
    qc.invalidateQueries({ queryKey: ["ai-usage-logs"] });
  };

  const totalLogs = logsQ.data?.total ?? 0;
  const numPages = Math.max(1, Math.ceil(totalLogs / LIMIT));

  return (
    <AppLayout title="Consumo de IA" actions={
      <Button data-testid="reload-button" variant="outline"
              onClick={reloadAll} className="rounded-sm">
        <RefreshCw className={`h-4 w-4 mr-1.5 ${quickQ.isFetching || summaryQ.isFetching ? "animate-spin" : ""}`} />
        Actualizar
      </Button>
    }>
      <div className="space-y-6" data-testid="consumo-ia-page">

        {/* Quick cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <QuickCard data-testid="card-today"      label="Hoy"
                     calls={quickQ.data?.today?.calls}
                     cost={quickQ.data?.today?.cost_usd}
                     loading={quickQ.isPending} />
          <QuickCard data-testid="card-month"      label="Este mes"
                     calls={quickQ.data?.this_month?.calls}
                     cost={quickQ.data?.this_month?.cost_usd}
                     loading={quickQ.isPending} />
          <QuickCard data-testid="card-all-time"   label="Total acumulado"
                     calls={quickQ.data?.all_time?.calls}
                     cost={quickQ.data?.all_time?.cost_usd}
                     loading={quickQ.isPending} />
          <TopModelCard data-testid="card-top-model"
                        model={quickQ.data?.top_model?.model}
                        share={quickQ.data?.top_model?.share_pct}
                        loading={quickQ.isPending} />
        </div>

        {/* Filters */}
        <div className="bg-white border border-[#E9E6DC] rounded-sm p-4">
          <div className="flex items-center gap-2 mb-3">
            <Filter className="h-4 w-4 text-[#0E8DDB]" />
            <p className="text-sm font-bold tracking-tight text-[#0B1B26]">Filtros</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
            <div>
              <Label className="text-xs font-bold text-[#888888]">Desde</Label>
              <Input data-testid="filter-from" type="date"
                     value={filters.from}
                     onChange={(e) => { setFilters((f) => ({ ...f, from: e.target.value })); setLogsPage(0); }}
                     className="rounded-sm h-9 mt-1" />
            </div>
            <div>
              <Label className="text-xs font-bold text-[#888888]">Hasta</Label>
              <Input data-testid="filter-to" type="date"
                     value={filters.to}
                     onChange={(e) => { setFilters((f) => ({ ...f, to: e.target.value })); setLogsPage(0); }}
                     className="rounded-sm h-9 mt-1" />
            </div>
            <div>
              <Label className="text-xs font-bold text-[#888888]">Modelo</Label>
              <Input data-testid="filter-model"
                     placeholder="Cualquier modelo"
                     value={filters.model}
                     onChange={(e) => { setFilters((f) => ({ ...f, model: e.target.value })); setLogsPage(0); }}
                     className="rounded-sm h-9 mt-1" />
            </div>
            <div>
              <Label className="text-xs font-bold text-[#888888]">Estado</Label>
              <Select value={filters.status}
                      onValueChange={(v) => { setFilters((f) => ({ ...f, status: v === "all" ? "" : v })); setLogsPage(0); }}>
                <SelectTrigger data-testid="filter-status" className="rounded-sm h-9 mt-1 text-sm">
                  <SelectValue placeholder="Todos los estados" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todos los estados</SelectItem>
                  {STATUS_OPTIONS.filter(o => o.value).map(o => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          {rangeBad && (
            <p data-testid="range-error" className="text-xs text-[#DC2626] mt-2">
              La fecha "Desde" no puede ser mayor que "Hasta".
            </p>
          )}
        </div>

        {/* Empty state */}
        {!summaryQ.isPending && summaryQ.data && summaryQ.data.total_calls === 0 && (
          <div data-testid="empty-state"
               className="bg-latus-cream border border-dashed border-zinc-300 rounded-sm p-8 text-center">
            <Sparkles className="h-6 w-6 text-[#0E8DDB] mx-auto mb-2" />
            <p className="text-sm font-bold text-[#0B1B26]">Aún no hay registros de consumo</p>
            <p className="text-xs text-[#888888] mt-1">
              Cuando el bot procese mensajes, vas a verlos acá.
            </p>
          </div>
        )}

        {/* By model + By day */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ByModelTable data={summaryQ.data} />
          <ByDayTable   data={summaryQ.data} />
        </div>

        {/* Top conversations */}
        <TopConvsTable data={summaryQ.data} />

        {/* Logs */}
        <LogsTable
          logs={logsQ.data?.items || []}
          total={totalLogs}
          page={logsPage}
          numPages={numPages}
          onPrev={() => setLogsPage((p) => Math.max(0, p - 1))}
          onNext={() => setLogsPage((p) => Math.min(numPages - 1, p + 1))}
          loading={logsQ.isPending}
        />

        {/* Pricing editor */}
        <PricingEditor pricing={pricingQ.data} onSaved={() => qc.invalidateQueries({ queryKey: ["ai-pricing"] })} />
      </div>
    </AppLayout>
  );
}


// ---------------------------------------------------------------------------
// Quick cards
// ---------------------------------------------------------------------------

function QuickCard({ label, calls, cost, loading, ...props }) {
  return (
    <div {...props} className="bg-white border border-[#E9E6DC] rounded-sm p-4">
      <p className="text-[10px] font-bold tracking-[0.15em] uppercase text-[#888888]">{label}</p>
      <p className="text-3xl font-bold text-[#0B1B26] mt-2 tracking-tight">
        {loading ? "—" : fmtInt(calls)}
      </p>
      <p className="text-xs text-[#888888] mt-0.5">llamadas</p>
      <p className="text-sm font-bold text-[#0E8DDB] mt-3 flex items-center gap-1">
        <DollarSign className="h-3 w-3" /> {loading ? "—" : fmtUSD(cost)}
      </p>
    </div>
  );
}

function TopModelCard({ model, share, loading, ...props }) {
  return (
    <div {...props} className="bg-[#0B1B26] text-white rounded-sm p-4 border border-zinc-800">
      <p className="text-[10px] font-bold tracking-[0.15em] uppercase text-[#0E8DDB]">Modelo más usado</p>
      <p className="text-base font-bold mt-2 truncate" title={model || ""}>
        {loading ? "—" : (model || "Sin datos")}
      </p>
      <div className="flex items-end justify-between mt-3">
        <p className="text-3xl font-bold tracking-tight">
          {loading ? "—" : `${(share || 0).toFixed(0)}%`}
        </p>
        <p className="text-[10px] text-latus-muted">del total</p>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Tables
// ---------------------------------------------------------------------------


function ByModelTable({ data }) {
  const rows = data?.by_model || [];
  const total = data?.total_calls || 0;
  return (
    <div className="bg-white border border-[#E9E6DC] rounded-sm overflow-hidden" data-testid="by-model-table">
      <div className="p-3 border-b border-[#E9E6DC] flex items-center gap-2">
        <p className="text-sm font-bold text-[#0B1B26]">Por modelo</p>
        <span className="text-xs text-[#888888]">({rows.length})</span>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
          <tr>
            <th className="text-left px-3 py-2">Modelo</th>
            <th className="text-right px-3 py-2">Llamadas</th>
            <th className="text-right px-3 py-2">Tokens</th>
            <th className="text-right px-3 py-2">Costo</th>
            <th className="text-right px-3 py-2">%</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={5} className="text-center text-[#888888] py-4">Sin datos</td></tr>
          )}
          {rows.map((r) => (
            <tr key={r.model} className="border-t border-[#E9E6DC]">
              <td className="px-3 py-2 font-mono text-xs">{r.model}</td>
              <td className="px-3 py-2 text-right">{fmtInt(r.calls)}</td>
              <td className="px-3 py-2 text-right">{fmtInt(r.tokens)}</td>
              <td className="px-3 py-2 text-right font-mono">{fmtUSD(r.cost_usd)}</td>
              <td className="px-3 py-2 text-right">
                {total ? `${((r.calls / total) * 100).toFixed(0)}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ByDayTable({ data }) {
  const rows = data?.by_day || [];
  const max = Math.max(1, ...rows.map((r) => Number(r.cost_usd || 0)));
  return (
    <div className="bg-white border border-[#E9E6DC] rounded-sm overflow-hidden" data-testid="by-day-table">
      <div className="p-3 border-b border-[#E9E6DC] flex items-center gap-2">
        <p className="text-sm font-bold text-[#0B1B26]">Por día</p>
        <span className="text-xs text-[#888888]">({rows.length})</span>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
          <tr>
            <th className="text-left px-3 py-2">Fecha</th>
            <th className="text-right px-3 py-2">Llamadas</th>
            <th className="text-right px-3 py-2">Tokens</th>
            <th className="text-right px-3 py-2">Costo</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={4} className="text-center text-[#888888] py-4">Sin datos</td></tr>
          )}
          {rows.map((r) => {
            const pct = (Number(r.cost_usd || 0) / max) * 100;
            return (
              <tr key={r.date} className="border-t border-[#E9E6DC]">
                <td className="px-3 py-2 font-mono text-xs">{r.date}</td>
                <td className="px-3 py-2 text-right">{fmtInt(r.calls)}</td>
                <td className="px-3 py-2 text-right">{fmtInt(r.tokens)}</td>
                <td className="px-3 py-2 text-right font-mono">
                  <div className="flex items-center justify-end gap-2">
                    <div className="w-12 h-1 bg-latus-warm-gray rounded-sm overflow-hidden">
                      <div className="h-1 bg-[#0E8DDB]" style={{ width: `${pct}%` }} />
                    </div>
                    {fmtUSD(r.cost_usd)}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TopConvsTable({ data }) {
  const rows = data?.top_conversations || [];
  return (
    <div className="bg-white border border-[#E9E6DC] rounded-sm overflow-hidden" data-testid="top-convs-table">
      <div className="p-3 border-b border-[#E9E6DC] flex items-center gap-2">
        <p className="text-sm font-bold text-[#0B1B26]">Conversaciones con mayor consumo</p>
        <span className="text-xs text-[#888888]">(top 10)</span>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
          <tr>
            <th className="text-left px-3 py-2">Conversación</th>
            <th className="text-right px-3 py-2">Llamadas</th>
            <th className="text-right px-3 py-2">Costo</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={4} className="text-center text-[#888888] py-4">Sin datos</td></tr>
          )}
          {rows.map((r) => (
            <tr key={r.conversation_id} className="border-t border-[#E9E6DC]">
              <td className="px-3 py-2 font-mono text-xs truncate max-w-[280px]">{r.conversation_id}</td>
              <td className="px-3 py-2 text-right">{fmtInt(r.calls)}</td>
              <td className="px-3 py-2 text-right font-mono">{fmtUSD(r.cost_usd)}</td>
              <td className="px-3 py-2 text-right">
                <Link to="/inbox" state={{ convId: r.conversation_id }}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-[#0E8DDB] hover:underline">
                  Abrir <ExternalLink className="h-3 w-3" />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function LogsTable({ logs, total, page, numPages, onPrev, onNext, loading }) {
  return (
    <div className="bg-white border border-[#E9E6DC] rounded-sm overflow-hidden" data-testid="logs-table">
      <div className="p-3 border-b border-[#E9E6DC] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <p className="text-sm font-bold text-[#0B1B26]">Logs detallados</p>
          <span className="text-xs text-[#888888]">({fmtInt(total)} totales)</span>
        </div>
        <div className="flex items-center gap-2">
          <span data-testid="logs-page-indicator" className="text-xs text-[#888888]">
            Página {page + 1} de {numPages}
          </span>
          <Button data-testid="logs-prev" size="sm" variant="outline"
                  onClick={onPrev} disabled={page === 0}
                  className="rounded-sm h-7"><ChevronLeft className="h-3 w-3" /></Button>
          <Button data-testid="logs-next" size="sm" variant="outline"
                  onClick={onNext} disabled={page + 1 >= numPages}
                  className="rounded-sm h-7"><ChevronRight className="h-3 w-3" /></Button>
        </div>
      </div>
      <div className="overflow-auto">
        <table className="w-full text-xs">
          <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
            <tr>
              <th className="text-left px-3 py-2">Fecha/hora</th>
              <th className="text-left px-3 py-2">Modelo</th>
              <th className="text-left px-3 py-2">Propósito</th>
              <th className="text-right px-3 py-2">Tokens</th>
              <th className="text-right px-3 py-2">Costo</th>
              <th className="text-right px-3 py-2">Latencia</th>
              <th className="text-left px-3 py-2">Estado</th>
              <th className="text-left px-3 py-2">Conversación</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={8} className="text-center text-[#888888] py-4">Cargando…</td></tr>
            )}
            {!loading && logs.length === 0 && (
              <tr><td colSpan={8} className="text-center text-[#888888] py-4">Sin registros</td></tr>
            )}
            {logs.map((l) => (
              <tr key={l.log_id} className="border-t border-[#E9E6DC] align-top">
                <td className="px-3 py-2 font-mono">{(l.created_at || "").replace("T", " ").slice(0, 19)}</td>
                <td className="px-3 py-2 font-mono">{l.model}</td>
                <td className="px-3 py-2">{PURPOSE_LABEL[l.purpose] || l.purpose}</td>
                <td className="px-3 py-2 text-right">{fmtInt(l.total_tokens)}</td>
                <td className="px-3 py-2 text-right font-mono">{fmtUSD(l.estimated_cost_usd)}</td>
                <td className="px-3 py-2 text-right">{fmtInt(l.latency_ms)} ms</td>
                <td className="px-3 py-2">
                  {l.status === "success" ? (
                    <span className="text-[10px] font-bold uppercase text-[#16A34A] bg-[#DCFCE7] border border-[#86EFAC] px-1.5 py-0.5 rounded-sm">éxito</span>
                  ) : (
                    <span title={l.error_message || ""}
                          className="text-[10px] font-bold uppercase text-[#DC2626] bg-[#FEE2E2] border border-[#FCA5A5] px-1.5 py-0.5 rounded-sm inline-flex items-center gap-1">
                      <AlertOctagon className="h-2.5 w-2.5" /> error
                    </span>
                  )}
                  {l.status === "error" && l.error_message && (
                    <p className="text-[10px] text-[#DC2626] mt-0.5 line-clamp-2 max-w-[260px]">{l.error_message}</p>
                  )}
                </td>
                <td className="px-3 py-2 font-mono">
                  {l.conversation_id ? (
                    <Link to="/inbox" state={{ convId: l.conversation_id }}
                          className="text-[#0E8DDB] hover:underline">{l.conversation_id}</Link>
                  ) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Pricing editor (compact sub-section, lives at the bottom of /consumo-ia)
// ---------------------------------------------------------------------------


function PricingEditor({ pricing, onSaved }) {
  const [drafts, setDrafts] = useState({}); // { model: {input, output} }
  const save = useMutation({
    mutationFn: (payload) => api.put("/admin/ai-pricing", payload),
    onSuccess: () => { toast.success("Precio guardado"); setDrafts({}); onSaved && onSaved(); },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo guardar el precio"),
  });
  const reset = useMutation({
    mutationFn: () => api.post("/admin/ai-pricing/reset"),
    onSuccess: () => { toast.success("Valores restaurados"); setDrafts({}); onSaved && onSaved(); },
    onError: () => toast.error("No se pudo restaurar"),
  });

  if (!pricing) return null;
  const allModels = { ...pricing.defaults, ...pricing.models };
  const sortedKeys = Object.keys(allModels).sort();

  const onChange = (model, field, value) => {
    const v = parseFloat(value);
    setDrafts((d) => ({ ...d, [model]: { ...(d[model] || allModels[model]), [field]: isNaN(v) ? 0 : v } }));
  };

  return (
    <div className="bg-white border border-[#E9E6DC] rounded-sm" data-testid="pricing-editor">
      <div className="p-3 border-b border-[#E9E6DC] flex items-center justify-between">
        <div>
          <p className="text-sm font-bold text-[#0B1B26]">Precios por modelo</p>
          <p className="text-[11px] text-[#888888]">USD por 1 millón de tokens. Sirven para estimar el costo.</p>
        </div>
        <Button data-testid="pricing-reset"
                variant="outline" size="sm" className="rounded-sm"
                onClick={() => reset.mutate()} disabled={reset.isPending}>
          <RotateCcw className="h-3.5 w-3.5 mr-1" /> Restaurar valores por defecto
        </Button>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
          <tr>
            <th className="text-left px-3 py-2">Modelo</th>
            <th className="text-right px-3 py-2">Input (USD / 1M)</th>
            <th className="text-right px-3 py-2">Output (USD / 1M)</th>
            <th className="text-right px-3 py-2">Acción</th>
          </tr>
        </thead>
        <tbody>
          {sortedKeys.map((m) => {
            const cur = drafts[m] || allModels[m];
            const dirty = drafts[m] !== undefined;
            const bad = (cur.input < 0) || (cur.output < 0);
            return (
              <tr key={m} className="border-t border-[#E9E6DC]">
                <td className="px-3 py-2 font-mono text-xs">{m}</td>
                <td className="px-3 py-2 text-right">
                  <Input data-testid={`pricing-input-${m}`} type="number" step="0.001" min="0"
                         value={cur.input}
                         onChange={(e) => onChange(m, "input", e.target.value)}
                         className="rounded-sm h-8 text-right w-28 ml-auto" />
                </td>
                <td className="px-3 py-2 text-right">
                  <Input data-testid={`pricing-output-${m}`} type="number" step="0.001" min="0"
                         value={cur.output}
                         onChange={(e) => onChange(m, "output", e.target.value)}
                         className="rounded-sm h-8 text-right w-28 ml-auto" />
                </td>
                <td className="px-3 py-2 text-right">
                  <Button data-testid={`pricing-save-${m}`}
                          size="sm" disabled={!dirty || bad || save.isPending}
                          onClick={() => save.mutate({
                            model: m,
                            input_per_million: cur.input,
                            output_per_million: cur.output,
                          })}
                          className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm h-8 text-xs">
                    Guardar
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
