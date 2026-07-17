import { useMemo, useState } from "react";
import { Navigate, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Activity, AlertOctagon, BadgeDollarSign, CheckCircle2, ChevronLeft, ChevronRight,
  CircleHelp, Database, DollarSign, ExternalLink, Filter, KeyRound, RefreshCw,
  RotateCcw, ShieldCheck, Sparkles, TrendingUp,
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
    provider: "",
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
    if (filters.provider) p.set("provider", filters.provider);
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
  const reportingQ = useQuery({
    queryKey: ["ai-usage-provider-reporting"],
    queryFn: () => api.get("/admin/ai-usage/provider-reporting").then((r) => r.data),
    enabled: isAdmin,
  });

  if (user && !isAdmin) return <Navigate to="/dashboard" replace />;
  const reloadAll = () => {
    qc.invalidateQueries({ queryKey: ["ai-usage-quick"] });
    qc.invalidateQueries({ queryKey: ["ai-usage-summary"] });
    qc.invalidateQueries({ queryKey: ["ai-usage-logs"] });
    qc.invalidateQueries({ queryKey: ["ai-usage-provider-reporting"] });
  };

  const totalLogs = logsQ.data?.total ?? 0;
  const numPages = Math.max(1, Math.ceil(totalLogs / LIMIT));

  return (
    <AppLayout title="Consumo de IA" actions={
      <Button data-testid="reload-button" variant="outline"
              onClick={reloadAll} className="h-10 rounded-lg border-latus-warm-border bg-white">
        <RefreshCw className={`h-4 w-4 mr-1.5 ${quickQ.isFetching || summaryQ.isFetching ? "animate-spin" : ""}`} />
        Actualizar
      </Button>
    }>
      <div className="mx-auto w-full max-w-[1500px] space-y-6 p-4 sm:p-6 lg:p-8" data-testid="consumo-ia-page">

        <section className="overflow-hidden rounded-2xl border border-latus-warm-border bg-gradient-to-br from-white via-latus-surface to-latus-ice/30 shadow-[0_18px_45px_rgba(13,31,42,0.06)]">
          <div className="flex flex-col gap-5 p-5 sm:p-6 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-latus-ink text-white shadow-[0_10px_24px_rgba(13,31,42,0.18)]"><Activity className="h-5 w-5" /></div>
              <div>
                <p className="text-lg font-bold tracking-tight text-latus-ink">Control de uso y costos</p>
                <p className="mt-1 max-w-2xl text-sm leading-relaxed text-latus-muted">Los tokens se toman de la respuesta del proveedor. Los importes calculados por el CRM son estimaciones hasta conciliarlos con la API de facturación.</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 font-semibold text-emerald-800"><CheckCircle2 className="h-3.5 w-3.5" /> Cobertura de tokens {summaryQ.data?.measurement?.token_coverage_pct ?? "—"}%</span>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1.5 font-semibold text-amber-800"><CircleHelp className="h-3.5 w-3.5" /> Costos estimados</span>
            </div>
          </div>
        </section>

        {/* Quick cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <QuickCard data-testid="card-today"      label="Hoy"
                     calls={quickQ.data?.today?.calls}
                     tokens={quickQ.data?.today?.tokens}
                     cost={quickQ.data?.today?.cost_usd}
                     loading={quickQ.isPending} />
          <QuickCard data-testid="card-month"      label="Este mes"
                     calls={quickQ.data?.this_month?.calls}
                     tokens={quickQ.data?.this_month?.tokens}
                     cost={quickQ.data?.this_month?.cost_usd}
                     loading={quickQ.isPending} />
          <QuickCard data-testid="card-all-time"   label="Total acumulado"
                     calls={quickQ.data?.all_time?.calls}
                     tokens={quickQ.data?.all_time?.tokens}
                     cost={quickQ.data?.all_time?.cost_usd}
                     loading={quickQ.isPending} />
          <TopModelCard data-testid="card-top-model"
                        model={quickQ.data?.top_model?.model}
                        share={quickQ.data?.top_model?.share_pct}
                        loading={quickQ.isPending} />
        </div>

        <ProviderVerificationPanel
          status={reportingQ.data}
          loading={reportingQ.isPending}
          from={filters.from}
          to={filters.to}
          onStatusChanged={() => qc.invalidateQueries({ queryKey: ["ai-usage-provider-reporting"] })}
        />

        {/* Filters */}
        <div className="rounded-xl border border-latus-warm-border bg-white p-4 shadow-[0_10px_28px_rgba(13,31,42,0.035)] sm:p-5">
          <div className="mb-4 flex items-center gap-2">
            <Filter className="h-4 w-4 text-latus-blue" />
            <div><p className="text-sm font-bold tracking-tight text-latus-ink">Filtros del informe</p><p className="mt-0.5 text-xs text-latus-muted">Aplican a los registros internos del CRM.</p></div>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
            <div>
              <Label className="text-xs font-bold text-[#888888]">Desde</Label>
              <Input data-testid="filter-from" type="date"
                     value={filters.from}
                     onChange={(e) => { setFilters((f) => ({ ...f, from: e.target.value })); setLogsPage(0); }}
                     className="mt-1 h-10 rounded-lg border-latus-warm-border" />
            </div>
            <div>
              <Label className="text-xs font-bold text-[#888888]">Hasta</Label>
              <Input data-testid="filter-to" type="date"
                     value={filters.to}
                     onChange={(e) => { setFilters((f) => ({ ...f, to: e.target.value })); setLogsPage(0); }}
                     className="mt-1 h-10 rounded-lg border-latus-warm-border" />
            </div>
            <div>
              <Label className="text-xs font-bold text-[#888888]">Proveedor</Label>
              <Select value={filters.provider || "all"} onValueChange={(v) => { setFilters((f) => ({ ...f, provider: v === "all" ? "" : v })); setLogsPage(0); }}>
                <SelectTrigger data-testid="filter-provider" className="mt-1 h-10 rounded-lg border-latus-warm-border text-sm"><SelectValue placeholder="Todos" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todos los proveedores</SelectItem>
                  {(reportingQ.data?.providers || []).map((item) => <SelectItem key={item.provider} value={item.provider}>{item.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-xs font-bold text-[#888888]">Modelo</Label>
              <Input data-testid="filter-model"
                     placeholder="Cualquier modelo"
                     value={filters.model}
                     onChange={(e) => { setFilters((f) => ({ ...f, model: e.target.value })); setLogsPage(0); }}
                     className="mt-1 h-10 rounded-lg border-latus-warm-border" />
            </div>
            <div>
              <Label className="text-xs font-bold text-[#888888]">Estado</Label>
              <Select value={filters.status}
                      onValueChange={(v) => { setFilters((f) => ({ ...f, status: v === "all" ? "" : v })); setLogsPage(0); }}>
                <SelectTrigger data-testid="filter-status" className="mt-1 h-10 rounded-lg border-latus-warm-border text-sm">
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

function ProviderVerificationPanel({ status, loading, from, to, onStatusChanged }) {
  const [providerChoice, setProviderChoice] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [report, setReport] = useState(null);
  const selectedProvider = providerChoice || status?.active_provider || "built_in";
  const capability = (status?.providers || []).find((item) => item.provider === selectedProvider);
  const localProviderQ = useQuery({
    queryKey: ["ai-usage-provider-comparison", selectedProvider, from, to],
    queryFn: () => {
      const query = new URLSearchParams({ from, to, provider: selectedProvider });
      return api.get(`/admin/ai-usage/summary?${query}`).then((r) => r.data);
    },
    enabled: Boolean(selectedProvider && from && to),
  });
  const localSummary = localProviderQ.data;

  const saveKey = useMutation({
    mutationFn: (key) => api.put(`/admin/ai-usage/provider-reporting/${selectedProvider}`, { key }),
    onSuccess: () => {
      toast.success("Clave de consulta guardada de forma segura");
      setKeyDraft("");
      onStatusChanged && onStatusChanged();
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo guardar la clave"),
  });
  const fetchReport = useMutation({
    mutationFn: () => {
      const query = new URLSearchParams({ provider: selectedProvider, from, to });
      return api.post(`/admin/ai-usage/provider-report?${query}`).then((r) => r.data);
    },
    onSuccess: (data) => setReport(data),
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo consultar al proveedor"),
  });

  const localTokens = Number(localSummary?.total_tokens || 0);
  const reportTokens = report?.tokens == null ? null : Number(report.tokens || 0);
  const tokenDelta = reportTokens == null ? null : reportTokens - localTokens;

  return (
    <section className="overflow-hidden rounded-xl border border-latus-warm-border bg-white shadow-[0_14px_36px_rgba(13,31,42,0.045)]" data-testid="provider-verification-panel">
      <div className="flex flex-col gap-4 border-b border-latus-warm-border bg-latus-cream/30 p-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-latus-ice"><ShieldCheck className="h-5 w-5 text-latus-blue" /></div>
          <div><p className="text-sm font-bold text-latus-ink">Conciliación con el proveedor</p><p className="mt-1 max-w-2xl text-xs leading-relaxed text-latus-muted">Compara los registros del CRM con el informe oficial de uso y facturación.</p></div>
        </div>
        <Select value={selectedProvider} onValueChange={(value) => { setProviderChoice(value); setReport(null); setKeyDraft(""); }}>
          <SelectTrigger className="h-10 w-full rounded-lg border-latus-warm-border bg-white text-sm sm:w-56"><SelectValue /></SelectTrigger>
          <SelectContent>{(status?.providers || []).map((item) => <SelectItem key={item.provider} value={item.provider}>{item.label}</SelectItem>)}</SelectContent>
        </Select>
      </div>

      {loading || !capability ? (
        <div className="flex min-h-32 items-center justify-center"><RefreshCw className="h-5 w-5 animate-spin text-latus-blue" /></div>
      ) : (
        <div className="grid gap-5 p-5 sm:p-6 xl:grid-cols-[1fr_1.25fr]">
          <div className="space-y-4">
            <div className="rounded-xl border border-latus-warm-border bg-latus-cream/25 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${capability.tokens === "provider_response" ? "bg-emerald-100 text-emerald-800" : "bg-neutral-100 text-neutral-600"}`}>Tokens: {capability.tokens === "provider_response" ? "medidos" : "no disponibles"}</span>
                <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${["admin_api", "provider_response"].includes(capability.cost) ? "bg-blue-100 text-blue-800" : "bg-amber-100 text-amber-800"}`}>Costo: {capability.cost === "provider_response" ? "real por llamada" : capability.cost === "admin_api" ? "API administrativa" : capability.cost === "external_console" ? "consola externa" : "estimado"}</span>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-latus-muted">{capability.description}</p>
            </div>

            {capability.requires_separate_key && (
              <div className="rounded-xl border border-latus-warm-border p-4">
                <Label className="flex items-center gap-2 text-xs font-bold text-latus-ink"><KeyRound className="h-3.5 w-3.5 text-latus-blue" /> {capability.key_label}</Label>
                <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                  <Input type="password" value={keyDraft} onChange={(e) => setKeyDraft(e.target.value)} placeholder={capability.configured ? capability.masked : "Ingresá la clave administrativa"} className="h-10 rounded-lg border-latus-warm-border" />
                  <Button onClick={() => saveKey.mutate(keyDraft)} disabled={!keyDraft.trim() || saveKey.isPending} className="h-10 rounded-lg bg-latus-blue px-4 text-white hover:bg-latus-blue-deep">Guardar</Button>
                  {capability.configured && <Button variant="outline" onClick={() => saveKey.mutate(null)} disabled={saveKey.isPending} className="h-10 rounded-lg border-latus-warm-border">Quitar</Button>}
                </div>
                <p className="mt-2 text-[11px] text-latus-muted">Se cifra en el servidor y nunca se muestra completa.</p>
              </div>
            )}

            {capability.reporting_supported && (
              <Button data-testid="provider-report-fetch" onClick={() => fetchReport.mutate()} disabled={!capability.configured || fetchReport.isPending || !from || !to} className="h-10 rounded-lg bg-latus-ink px-4 text-white hover:bg-latus-ink-soft">
                {fetchReport.isPending ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Database className="mr-2 h-4 w-4" />} Consultar consumo real
              </Button>
            )}
          </div>

          <div className="rounded-xl border border-latus-warm-border bg-latus-ink p-5 text-white">
            {!report ? (
              <div className="flex min-h-48 flex-col items-center justify-center text-center"><BadgeDollarSign className="h-7 w-7 text-latus-blue" /><p className="mt-3 text-sm font-bold">Informe oficial del proveedor</p><p className="mt-1 max-w-sm text-xs leading-relaxed text-latus-ice/65">{capability.reporting_supported ? (capability.configured ? "Consultá el período para comparar tokens y costos." : "Configurá la credencial necesaria para habilitar la consulta.") : "Este proveedor no permite obtener la facturación consolidada con la API de inferencia configurada."}</p></div>
            ) : report.periods ? (
              <div><p className="text-xs font-bold uppercase tracking-[0.14em] text-latus-blue">Consumo de la API key</p><div className="mt-5 grid grid-cols-2 gap-3"><MetricDark label="Hoy" value={fmtUSD(report.periods.today_usd)} /><MetricDark label="Esta semana" value={fmtUSD(report.periods.week_usd)} /><MetricDark label="Este mes" value={fmtUSD(report.periods.month_usd)} /><MetricDark label="Acumulado" value={fmtUSD(report.periods.all_time_usd)} /></div></div>
            ) : (
              <div>
                <div className="flex items-center justify-between gap-3"><p className="text-xs font-bold uppercase tracking-[0.14em] text-latus-blue">Datos conciliados</p><span className="text-[10px] text-latus-ice/60">{report.from} → {report.to}</span></div>
                <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3"><MetricDark label="Solicitudes" value={fmtInt(report.requests)} /><MetricDark label="Tokens oficiales" value={fmtInt(report.tokens)} /><MetricDark label="Costo facturado" value={fmtUSD(report.actual_cost_usd)} /></div>
                <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.05] p-3 text-xs text-latus-ice/75"><p>CRM: <strong className="text-white">{fmtInt(localTokens)} tokens</strong> · Estimación: <strong className="text-white">{fmtUSD(localSummary?.estimated_cost_usd)}</strong></p><p className="mt-1">Diferencia de tokens: <strong className={tokenDelta === 0 ? "text-emerald-300" : "text-amber-300"}>{tokenDelta == null ? "—" : `${tokenDelta > 0 ? "+" : ""}${fmtInt(tokenDelta)}`}</strong></p></div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function MetricDark({ label, value }) {
  return <div className="rounded-lg border border-white/10 bg-white/[0.055] p-3"><p className="text-[10px] font-semibold uppercase tracking-wider text-latus-ice/55">{label}</p><p className="mt-1.5 text-lg font-extrabold text-white">{value}</p></div>;
}

function QuickCard({ label, calls, tokens, cost, loading, ...props }) {
  return (
    <div {...props} className="rounded-xl border border-latus-warm-border bg-white p-5 shadow-[0_10px_28px_rgba(13,31,42,0.035)]">
      <div className="flex items-center justify-between"><p className="text-[10px] font-bold uppercase tracking-[0.15em] text-latus-muted">{label}</p><TrendingUp className="h-4 w-4 text-latus-blue" /></div>
      <p className="mt-3 text-3xl font-bold tracking-tight text-latus-ink">
        {loading ? "—" : fmtInt(calls)}
      </p>
      <p className="mt-0.5 text-xs text-latus-muted">llamadas · {loading ? "—" : fmtInt(tokens)} tokens</p>
      <div className="mt-4 flex items-center justify-between border-t border-latus-warm-border pt-3"><span className="text-[10px] font-semibold uppercase tracking-wide text-latus-muted">Costo estimado</span><p className="flex items-center gap-1 text-sm font-bold text-latus-blue"><DollarSign className="h-3 w-3" /> {loading ? "—" : fmtUSD(cost)}</p></div>
    </div>
  );
}

function TopModelCard({ model, share, loading, ...props }) {
  return (
    <div {...props} className="rounded-xl border border-zinc-800 bg-[#0B1B26] p-5 text-white shadow-[0_12px_30px_rgba(13,31,42,0.14)]">
      <p className="text-[10px] font-bold tracking-[0.15em] uppercase text-[#0E8DDB]">Modelo más usado</p>
      <p className="mt-3 truncate text-base font-bold" title={model || ""}>
        {loading ? "—" : (model || "Sin datos")}
      </p>
      <div className="mt-4 flex items-end justify-between border-t border-white/10 pt-3">
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
    <div className="overflow-hidden rounded-xl border border-latus-warm-border bg-white" data-testid="by-model-table">
      <div className="flex items-center gap-2 border-b border-latus-warm-border px-5 py-4">
        <p className="text-sm font-bold text-[#0B1B26]">Por modelo</p>
        <span className="text-xs text-[#888888]">({rows.length})</span>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
          <tr>
            <th className="text-left px-3 py-2">Modelo</th>
            <th className="text-right px-3 py-2">Llamadas</th>
            <th className="text-right px-3 py-2">Tokens</th>
            <th className="text-right px-3 py-2">Estimado</th>
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
    <div className="overflow-hidden rounded-xl border border-latus-warm-border bg-white" data-testid="by-day-table">
      <div className="flex items-center gap-2 border-b border-latus-warm-border px-5 py-4">
        <p className="text-sm font-bold text-[#0B1B26]">Por día</p>
        <span className="text-xs text-[#888888]">({rows.length})</span>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
          <tr>
            <th className="text-left px-3 py-2">Fecha</th>
            <th className="text-right px-3 py-2">Llamadas</th>
            <th className="text-right px-3 py-2">Tokens</th>
            <th className="text-right px-3 py-2">Estimado</th>
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
    <div className="overflow-hidden rounded-xl border border-latus-warm-border bg-white" data-testid="top-convs-table">
      <div className="flex items-center gap-2 border-b border-latus-warm-border px-5 py-4">
        <p className="text-sm font-bold text-[#0B1B26]">Conversaciones con mayor consumo</p>
        <span className="text-xs text-[#888888]">(top 10)</span>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
          <tr>
            <th className="text-left px-3 py-2">Conversación</th>
            <th className="text-right px-3 py-2">Llamadas</th>
            <th className="text-right px-3 py-2">Costo estimado</th>
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
    <div className="overflow-hidden rounded-xl border border-latus-warm-border bg-white" data-testid="logs-table">
      <div className="flex flex-col gap-3 border-b border-latus-warm-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
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
              <th className="text-left px-3 py-2">Proveedor</th>
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
              <tr><td colSpan={9} className="text-center text-[#888888] py-4">Cargando…</td></tr>
            )}
            {!loading && logs.length === 0 && (
              <tr><td colSpan={9} className="text-center text-[#888888] py-4">Sin registros</td></tr>
            )}
            {logs.map((l) => (
              <tr key={l.log_id} className="border-t border-[#E9E6DC] align-top">
                <td className="px-3 py-2 font-mono">{(l.created_at || "").replace("T", " ").slice(0, 19)}</td>
                <td className="px-3 py-2"><span className="rounded-full bg-latus-ice px-2 py-1 text-[10px] font-bold uppercase text-latus-blue">{l.provider || "—"}</span></td>
                <td className="px-3 py-2 font-mono">{l.model}</td>
                <td className="px-3 py-2">{PURPOSE_LABEL[l.purpose] || l.purpose}</td>
                <td className="px-3 py-2 text-right">{fmtInt(l.total_tokens)}</td>
                <td className="px-3 py-2 text-right font-mono"><p>{fmtUSD(l.provider_cost_usd ?? l.estimated_cost_usd)}</p><span className={`mt-0.5 inline-flex rounded-full px-1.5 py-0.5 text-[8px] font-bold uppercase ${l.provider_cost_usd != null ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>{l.provider_cost_usd != null ? "real" : "estimado"}</span></td>
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
