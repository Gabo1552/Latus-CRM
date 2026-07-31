import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeDollarSign, Building2, CheckCircle2, FileText, KeyRound, Pencil, RefreshCw,
  Search, ShieldAlert, Sparkles, Users, X,
} from "lucide-react";
import { toast } from "sonner";
import AppLayout from "@/components/AppLayout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import api from "@/lib/api";

const PLAN_NAMES = { base: "Base heredado", starter: "Inicial", growth: "Crecimiento", scale: "Escala" };
const SUBSCRIPTION_LABELS = {
  not_configured: "Sin configurar", trialing: "En prueba", active: "Activa",
  past_due: "Pago pendiente", canceled: "Cancelada", suspended: "Suspendida",
};
const LICENSE_LABELS = {
  not_configured: "Sin configurar", active: "Activa", grace_period: "Período de gracia",
  suspended: "Suspendida", expired: "Vencida",
};
const PROVIDER_LABELS = {
  pending: "Esperando adhesión", authorized: "Cobro autorizado",
  paused: "Pausada", canceled: "Cancelada",
};
const AI_BILLING_STATES = {
  disabled: { label: "Desactivado", tone: "border-slate-200 bg-slate-100 text-slate-700", help: "No calcula ni modifica cobros de Mercado Pago." },
  simulation: { label: "Simulación", tone: "border-amber-200 bg-amber-50 text-amber-800", help: "Permite proyectar importes, sin crear liquidaciones ni tocar Mercado Pago." },
  pilot: { label: "Piloto", tone: "border-violet-200 bg-violet-50 text-violet-800", help: "Solo se liquida cuando un administrador procesa esta empresa manualmente." },
  active: { label: "Activo", tone: "border-emerald-200 bg-emerald-50 text-emerald-800", help: "Participa automáticamente de los cierres cuando la política global está activa." },
};
const PROFITABILITY_STATES = {
  healthy: { label: "Rentable", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  at_risk: { label: "Margen ajustado", tone: "border-amber-200 bg-amber-50 text-amber-800" },
  blocked: { label: "No rentable", tone: "border-rose-200 bg-rose-50 text-rose-700" },
  not_configured: { label: "Sin calcular", tone: "border-slate-200 bg-slate-100 text-slate-600" },
};

const inputClass = "mt-1.5 h-10 w-full rounded-lg border border-latus-warm-border bg-white px-3 text-sm text-latus-ink outline-none focus:border-latus-blue focus:ring-2 focus:ring-latus-blue/10";

function statusTone(allowed) {
  return allowed ? "bg-emerald-50 text-emerald-700 border-emerald-200" : "bg-rose-50 text-rose-700 border-rose-200";
}

function dateInput(value) {
  return value ? String(value).slice(0, 10) : "";
}

const SETTLEMENT_STATUS = {
  pending: "Pendiente", applying: "Aplicando", applied: "Próximo cobro",
  paid: "Cobrado", payment_failed: "Pago rechazado", failed: "Error",
  closed_no_charge: "Cerrado sin saldo",
  blocked_margin: "Bloqueada por margen",
  retrying: "Reintentando",
  retry_exhausted: "Reintentos agotados",
};

function AIVariableBillingPanel() {
  const qc = useQueryClient();
  const policyQ = useQuery({
    queryKey: ["ai-settlement-policy"],
    queryFn: () => api.get("/platform/ai-settlement-policy").then((r) => r.data),
  });
  const statementsQ = useQuery({
    queryKey: ["ai-settlements"],
    queryFn: () => api.get("/platform/ai-settlements?limit=8").then((r) => r.data),
  });
  const [draft, setDraft] = useState(null);
  const [retryStatement, setRetryStatement] = useState(null);
  const [retryConfirmed, setRetryConfirmed] = useState(false);
  useEffect(() => { if (policyQ.data) setDraft(policyQ.data); }, [policyQ.data]);
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["ai-settlement-policy"] });
    qc.invalidateQueries({ queryKey: ["ai-settlements"] });
    qc.invalidateQueries({ queryKey: ["platform-organizations"] });
  };
  const save = useMutation({
    mutationFn: () => {
      const payload = {
        enabled: !!draft.enabled,
        fx_buffer_percent: Number(draft.fx_buffer_percent),
        settlement_lead_hours: Number(draft.settlement_lead_hours),
        max_rate_age_hours: Number(draft.max_rate_age_hours),
        mp_fee_percent: Number(draft.mp_fee_percent),
        tax_percent: Number(draft.tax_percent),
        min_net_margin_percent: Number(draft.min_net_margin_percent),
        min_ai_margin_percent: Number(draft.min_ai_margin_percent),
        profitability_enforcement: draft.profitability_enforcement,
        max_retry_attempts: Number(draft.max_retry_attempts),
        retry_cooldown_minutes: Number(draft.retry_cooldown_minutes),
      };
      if (Number(draft.usd_to_ars_rate) !== Number(policyQ.data?.usd_to_ars_rate)) {
        payload.usd_to_ars_rate = Number(draft.usd_to_ars_rate);
      }
      return api.put("/platform/ai-settlement-policy", payload);
    },
    onSuccess: () => { toast.success("Política de liquidación guardada"); invalidate(); },
    onError: (e) => toast.error(e.response?.data?.detail || "No se pudo guardar"),
  });
  const refreshRate = useMutation({
    mutationFn: () => api.post("/platform/ai-settlement-policy/refresh-rate"),
    onSuccess: () => { toast.success("Cotización oficial actualizada"); invalidate(); },
    onError: (e) => toast.error(e.response?.data?.detail || "No se pudo consultar el BCRA"),
  });
  const run = useMutation({
    mutationFn: () => api.post("/platform/ai-settlements/run").then((r) => r.data),
    onSuccess: (data) => { toast.success(data.applied ? `${data.applied} liquidación aplicada` : "No hay cobros dentro de la ventana de liquidación"); invalidate(); },
    onError: (e) => toast.error(e.response?.data?.detail || "No se pudo ejecutar la liquidación"),
  });
  const retry = useMutation({
    mutationFn: (statementId) => api.post(
      `/platform/ai-billing/statements/${encodeURIComponent(statementId)}/retry`,
      { confirmation: "REINTENTAR" },
    ).then((r) => r.data),
    onSuccess: (result) => {
      if (result.status === "applied") toast.success("Liquidación recuperada y aplicada a Mercado Pago");
      else toast.error(`El reintento volvió a fallar: ${result.error || "revisá la alerta operativa"}`);
      setRetryStatement(null);
      setRetryConfirmed(false);
      invalidate();
    },
    onError: (error) => {
      const detail = error.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : detail?.message || "No se pudo reintentar");
      invalidate();
    },
  });
  if (!draft) return null;
  const invalid = Number(draft.usd_to_ars_rate) <= 0 || Number(draft.fx_buffer_percent) < 0
    || Number(draft.settlement_lead_hours) < 1 || Number(draft.max_rate_age_hours) < 12
    || Number(draft.mp_fee_percent) < 0 || Number(draft.mp_fee_percent) > 30
    || Number(draft.tax_percent) < 0 || Number(draft.tax_percent) > 60
    || Number(draft.min_net_margin_percent) < 0 || Number(draft.min_net_margin_percent) > 100
    || Number(draft.min_ai_margin_percent) < 0 || Number(draft.min_ai_margin_percent) > 100
    || Number(draft.max_retry_attempts) < 1 || Number(draft.max_retry_attempts) > 10
    || Number(draft.retry_cooldown_minutes) < 0 || Number(draft.retry_cooldown_minutes) > 1440;
  const rows = statementsQ.data?.items || [];
  const retryPolicy = statementsQ.data?.retry_policy || { max_attempts: draft.max_retry_attempts || 3, cooldown_minutes: draft.retry_cooldown_minutes || 5 };
  return (
    <section className="overflow-hidden rounded-[24px] border border-latus-warm-border bg-white shadow-sm" data-testid="ai-variable-billing-panel">
      <div className="flex flex-col gap-4 border-b border-latus-warm-border bg-latus-cream/35 p-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div className="flex items-start gap-3"><span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-violet-100 text-violet-700"><BadgeDollarSign className="h-5 w-5" /></span><div><h2 className="font-extrabold text-latus-ink">Cobro automático del consumo de IA</h2><p className="mt-1 max-w-3xl text-sm leading-5 text-latus-muted">La política global habilita el motor, pero el scheduler solo procesa empresas marcadas como Activo. Las empresas Piloto requieren ejecución manual y Simulación nunca modifica Mercado Pago.</p></div></div>
        <label className="flex shrink-0 items-center gap-2 rounded-full border border-latus-warm-border bg-white px-3 py-2 text-xs font-extrabold text-latus-ink"><input type="checkbox" checked={!!draft.enabled} onChange={(e) => setDraft((d) => ({ ...d, enabled: e.target.checked }))} className="h-4 w-4 accent-latus-blue" />{draft.enabled ? "Automatización activa" : "Automatización apagada"}</label>
      </div>
      <div className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-4 sm:p-6">
        <label className="text-xs font-bold text-latus-ink">Cotización USD → ARS
          <Input type="number" min="1" step="0.01" value={draft.usd_to_ars_rate} onChange={(e) => setDraft((d) => ({ ...d, usd_to_ars_rate: e.target.value }))} className="mt-1.5 h-10 rounded-lg border-latus-warm-border" />
          <span className="mt-1 block font-normal text-latus-muted">Fuente: {draft.exchange_rate_source === "bcra" ? "BCRA" : "manual"}</span>
        </label>
        <label className="text-xs font-bold text-latus-ink">Colchón cambiario
          <div className="relative"><Input type="number" min="0" max="100" step="0.5" value={draft.fx_buffer_percent} onChange={(e) => setDraft((d) => ({ ...d, fx_buffer_percent: e.target.value }))} className="mt-1.5 h-10 rounded-lg border-latus-warm-border pr-8" /><span className="absolute bottom-2.5 right-3 text-latus-muted">%</span></div>
        </label>
        <label className="text-xs font-bold text-latus-ink">Anticipación del cierre
          <div className="relative"><Input type="number" min="1" max="168" value={draft.settlement_lead_hours} onChange={(e) => setDraft((d) => ({ ...d, settlement_lead_hours: e.target.value }))} className="mt-1.5 h-10 rounded-lg border-latus-warm-border pr-10" /><span className="absolute bottom-2.5 right-3 text-latus-muted">hs</span></div>
        </label>
        <label className="text-xs font-bold text-latus-ink">Vigencia máxima de cotización
          <div className="relative"><Input type="number" min="12" max="720" value={draft.max_rate_age_hours} onChange={(e) => setDraft((d) => ({ ...d, max_rate_age_hours: e.target.value }))} className="mt-1.5 h-10 rounded-lg border-latus-warm-border pr-10" /><span className="absolute bottom-2.5 right-3 text-latus-muted">hs</span></div>
        </label>
      </div>
      <div className="mx-5 mb-5 rounded-2xl border border-amber-200 bg-amber-50/50 p-4 sm:mx-6 sm:mb-6">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
          <div><p className="text-xs font-extrabold uppercase tracking-[0.12em] text-amber-900">Control global de rentabilidad</p><p className="mt-1 text-xs leading-5 text-amber-800">Se valida el margen total y también el margen aislado de IA, para que el precio del plan no oculte una venta de IA a pérdida.</p></div>
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <label className="text-xs font-bold text-latus-ink">Costo Mercado Pago
            <div className="relative"><Input type="number" min="0" max="30" step="0.1" value={draft.mp_fee_percent} onChange={(e) => setDraft((d) => ({ ...d, mp_fee_percent: e.target.value }))} className="mt-1.5 h-10 border-amber-200 bg-white pr-8" /><span className="absolute bottom-2.5 right-3 text-latus-muted">%</span></div>
          </label>
          <label className="text-xs font-bold text-latus-ink">Impuestos estimados
            <div className="relative"><Input type="number" min="0" max="60" step="0.1" value={draft.tax_percent} onChange={(e) => setDraft((d) => ({ ...d, tax_percent: e.target.value }))} className="mt-1.5 h-10 border-amber-200 bg-white pr-8" /><span className="absolute bottom-2.5 right-3 text-latus-muted">%</span></div>
          </label>
          <label className="text-xs font-bold text-latus-ink">Margen total mínimo
            <div className="relative"><Input type="number" min="0" max="100" step="0.5" value={draft.min_net_margin_percent} onChange={(e) => setDraft((d) => ({ ...d, min_net_margin_percent: e.target.value }))} className="mt-1.5 h-10 border-amber-200 bg-white pr-8" /><span className="absolute bottom-2.5 right-3 text-latus-muted">%</span></div>
          </label>
          <label className="text-xs font-bold text-latus-ink">Margen IA mínimo
            <div className="relative"><Input type="number" min="0" max="100" step="0.5" value={draft.min_ai_margin_percent} onChange={(e) => setDraft((d) => ({ ...d, min_ai_margin_percent: e.target.value }))} className="mt-1.5 h-10 border-amber-200 bg-white pr-8" /><span className="absolute bottom-2.5 right-3 text-latus-muted">%</span></div>
          </label>
          <label className="text-xs font-bold text-latus-ink">Si no alcanza el margen
            <select value={draft.profitability_enforcement} onChange={(e) => setDraft((d) => ({ ...d, profitability_enforcement: e.target.value }))} className={`${inputClass} border-amber-200`}><option value="block">Bloquear cobro</option><option value="warn">Advertir y continuar</option></select>
          </label>
        </div>
      </div>
      <div className="mx-5 mb-5 rounded-2xl border border-rose-200 bg-rose-50/40 p-4 sm:mx-6 sm:mb-6">
        <div className="flex items-start gap-3"><RefreshCw className="mt-0.5 h-4 w-4 shrink-0 text-rose-700" /><div><p className="text-xs font-extrabold uppercase tracking-[0.12em] text-rose-900">Recuperación de errores técnicos</p><p className="mt-1 text-xs leading-5 text-rose-800">Los reintentos usan el importe congelado de la liquidación fallida. Nunca recalculan el período ni se habilitan para pagos ya cobrados o rechazados por el cliente.</p></div></div>
        <div className="mt-4 grid gap-4 sm:max-w-xl sm:grid-cols-2">
          <label className="text-xs font-bold text-latus-ink">Máximo de intentos manuales
            <Input type="number" min="1" max="10" value={draft.max_retry_attempts} onChange={(e) => setDraft((d) => ({ ...d, max_retry_attempts: e.target.value }))} className="mt-1.5 h-10 border-rose-200 bg-white" />
          </label>
          <label className="text-xs font-bold text-latus-ink">Espera entre intentos
            <div className="relative"><Input type="number" min="0" max="1440" value={draft.retry_cooldown_minutes} onChange={(e) => setDraft((d) => ({ ...d, retry_cooldown_minutes: e.target.value }))} className="mt-1.5 h-10 border-rose-200 bg-white pr-12" /><span className="absolute bottom-2.5 right-3 text-latus-muted">min</span></div>
          </label>
        </div>
      </div>
      <div className="flex flex-col gap-3 border-t border-latus-warm-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div><p className={`text-xs font-extrabold ${draft.rate_is_fresh ? "text-emerald-700" : "text-amber-700"}`}>{draft.rate_is_fresh ? "Cotización vigente" : "Cotización vencida o no configurada"}</p><p className="mt-1 text-[11px] text-latus-muted">Observada: {draft.exchange_rate_observed_at || "—"} · actualizada: {draft.exchange_rate_updated_at ? new Date(draft.exchange_rate_updated_at).toLocaleString("es-AR") : "—"}</p></div>
        <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => refreshRate.mutate()} disabled={refreshRate.isPending} className="rounded-lg border-latus-warm-border"><RefreshCw className={`h-4 w-4 ${refreshRate.isPending ? "animate-spin" : ""}`} />Actualizar desde BCRA</Button><Button variant="outline" onClick={() => run.mutate()} disabled={!draft.enabled || run.isPending} className="rounded-lg border-latus-warm-border"><FileText className="h-4 w-4" />Procesar empresas activas</Button><Button onClick={() => save.mutate()} disabled={invalid || save.isPending} className="rounded-lg bg-latus-blue text-white hover:bg-latus-blue/90">Guardar política</Button></div>
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto border-t border-latus-warm-border">
          <table className="w-full min-w-[1120px] text-left text-xs">
            <thead className="bg-latus-cream text-[10px] font-extrabold uppercase tracking-wider text-latus-muted"><tr><th className="px-5 py-3">Empresa</th><th className="px-4 py-3">Período</th><th className="px-4 py-3 text-right">IA USD</th><th className="px-4 py-3 text-right">IA ARS</th><th className="px-4 py-3 text-right">Plan + IA</th><th className="px-4 py-3">Estado</th><th className="px-4 py-3">Intentos</th><th className="px-5 py-3 text-right">Recuperación</th></tr></thead>
            <tbody className="divide-y divide-latus-warm-border">
              {rows.map((row) => {
                const attempts = Number(row.retry_count || 0);
                const canRetry = row.status === "failed" && attempts < Number(retryPolicy.max_attempts || 3);
                return (
                  <tr key={row.statement_id}>
                    <td className="px-5 py-3 font-mono">{row.organization_id}</td>
                    <td className="px-4 py-3 text-latus-muted">{String(row.period_start).slice(0, 10)} → {String(row.period_end).slice(0, 10)}</td>
                    <td className="px-4 py-3 text-right font-mono">USD {Number(row.billable_cost_usd || 0).toFixed(4)}</td>
                    <td className="px-4 py-3 text-right font-mono">$ {Number(row.ai_amount_ars || 0).toLocaleString("es-AR")}</td>
                    <td className="px-4 py-3 text-right font-extrabold">$ {Number(row.total_amount_ars || 0).toLocaleString("es-AR")}</td>
                    <td className="px-4 py-3"><span className={`rounded-full px-2.5 py-1 font-extrabold ${row.status === "failed" || row.status === "retry_exhausted" ? "bg-rose-100 text-rose-700" : row.status === "retrying" ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700"}`}>{SETTLEMENT_STATUS[row.status] || row.status}</span>{row.error && <p className="mt-2 max-w-[240px] truncate text-[10px] text-rose-700" title={row.error}>{row.error}</p>}</td>
                    <td className="px-4 py-3"><span className="font-bold text-latus-ink">{attempts} / {retryPolicy.max_attempts}</span>{row.last_retry_at && <p className="mt-1 text-[10px] text-latus-muted">{new Date(row.last_retry_at).toLocaleString("es-AR")}</p>}</td>
                    <td className="px-5 py-3 text-right">{canRetry ? <Button type="button" variant="outline" size="sm" onClick={() => { setRetryStatement(row); setRetryConfirmed(false); }} className="border-rose-300 bg-rose-50 text-xs font-bold text-rose-800 hover:bg-rose-100"><RefreshCw className="h-3.5 w-3.5" />Reintentar</Button> : row.status === "payment_failed" ? <span className="text-[10px] font-bold text-amber-700">Revisar pago en Mercado Pago</span> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {retryStatement && (
        <div className="fixed inset-0 z-[120] grid place-items-center bg-latus-ink/60 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-lg rounded-[24px] border border-white/10 bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-latus-warm-border px-6 py-5"><div><span className="inline-flex rounded-full bg-rose-100 px-3 py-1 text-xs font-black text-rose-800">RECUPERACIÓN TÉCNICA</span><h3 className="mt-2 text-xl font-extrabold text-latus-ink">Reintentar liquidación</h3></div><button type="button" onClick={() => setRetryStatement(null)} className="rounded-lg p-2 text-latus-muted hover:bg-latus-cream"><X className="h-5 w-5" /></button></div>
            <div className="space-y-4 p-6 text-sm">
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-900"><strong>Se reutilizará el importe congelado.</strong> No se incorporará consumo nuevo ni se recalculará la cotización. Esta acción vuelve a intentar actualizar el próximo cobro en Mercado Pago.</div>
              <div className="grid gap-3 rounded-xl border border-latus-warm-border bg-latus-cream/40 p-4 sm:grid-cols-2"><div><p className="text-[10px] font-bold uppercase text-latus-muted">Empresa</p><p className="mt-1 font-mono text-xs font-bold text-latus-ink">{retryStatement.organization_id}</p></div><div><p className="text-[10px] font-bold uppercase text-latus-muted">Importe congelado</p><p className="mt-1 font-black text-latus-blue">$ {Number(retryStatement.total_amount_ars || 0).toLocaleString("es-AR")} ARS</p></div><div><p className="text-[10px] font-bold uppercase text-latus-muted">Intento siguiente</p><p className="mt-1 font-bold text-latus-ink">{Number(retryStatement.retry_count || 0) + 1} de {retryPolicy.max_attempts}</p></div><div><p className="text-[10px] font-bold uppercase text-latus-muted">Espera configurada</p><p className="mt-1 font-bold text-latus-ink">{retryPolicy.cooldown_minutes} minutos</p></div></div>
              {retryStatement.error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800"><strong>Último error:</strong> {retryStatement.error}</div>}
              <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-rose-200 bg-rose-50/60 p-4 text-xs text-rose-900"><input type="checkbox" checked={retryConfirmed} onChange={(e) => setRetryConfirmed(e.target.checked)} className="mt-0.5 h-4 w-4 accent-rose-700" /><span>Verifiqué la suscripción y autorizo reintentar exactamente este importe.</span></label>
            </div>
            <div className="flex justify-end gap-3 border-t border-latus-warm-border px-6 py-4"><Button type="button" variant="outline" onClick={() => setRetryStatement(null)}>Cancelar</Button><Button type="button" disabled={!retryConfirmed || retry.isPending} onClick={() => retry.mutate(retryStatement.statement_id)} className="bg-rose-700 text-white hover:bg-rose-800">{retry.isPending ? "Reintentando..." : "Confirmar reintento"}</Button></div>
          </div>
        </div>
      )}
    </section>
  );
}

function ManageModal({ organization, onClose, onSave, saving }) {
  const variableBilling = organization.ai_variable_billing || {};
  const [draft, setDraft] = useState(() => ({
    plan_code: organization.plan_code || "base",
    subscription_status: organization.subscription_status || "not_configured",
    license_status: organization.license_status || "not_configured",
    trial_ends_at: dateInput(organization.trial_ends_at),
    current_period_end: dateInput(organization.current_period_end),
    grace_ends_at: dateInput(organization.grace_ends_at),
    billing_email: organization.billing_email || "",
    internal_notes: organization.internal_notes || "",
    ai_fee_percent: organization.ai_fee_percent ?? "",
    ai_billing_state: variableBilling.state || "disabled",
    ai_billing_start_date: dateInput(variableBilling.billing_start_date),
    ai_fx_buffer_percent: variableBilling.fx_buffer_percent ?? "",
    ai_min_net_margin_percent: variableBilling.min_net_margin_percent ?? "",
    ai_min_margin_percent: variableBilling.min_ai_margin_percent ?? "",
    ai_profitability_enforcement: variableBilling.profitability_enforcement ?? "",
  }));
  const set = (field, value) => setDraft((current) => ({ ...current, [field]: value }));

  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-latus-ink/55 p-4" role="dialog" aria-modal="true" aria-label="Administrar suscripción">
      <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-[24px] border border-white/10 bg-latus-cream shadow-2xl">
        <div className="sticky top-0 z-10 flex items-start justify-between border-b border-latus-warm-border bg-white px-6 py-5">
          <div><p className="text-xs font-extrabold uppercase tracking-[0.14em] text-latus-blue">Licencia de empresa</p><h2 className="mt-1 text-xl font-extrabold text-latus-ink">{organization.name}</h2><p className="mt-1 font-mono text-xs text-latus-muted">{organization.organization_id}</p></div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-latus-muted hover:bg-latus-cream hover:text-latus-ink" aria-label="Cerrar"><X className="h-5 w-5" /></button>
        </div>
        <div className="grid gap-5 p-6 sm:grid-cols-2">
          <label className="text-xs font-bold text-latus-ink">Plan
            <select className={inputClass} value={draft.plan_code} onChange={(event) => set("plan_code", event.target.value)}>
              {Object.entries(PLAN_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="text-xs font-bold text-latus-ink">Estado de suscripción
            <select className={inputClass} value={draft.subscription_status} onChange={(event) => set("subscription_status", event.target.value)}>
              {Object.entries(SUBSCRIPTION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="text-xs font-bold text-latus-ink">Estado de licencia
            <select className={inputClass} value={draft.license_status} onChange={(event) => set("license_status", event.target.value)}>
              {Object.entries(LICENSE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="text-xs font-bold text-latus-ink">Email de facturación
            <Input className={inputClass} type="email" value={draft.billing_email} onChange={(event) => set("billing_email", event.target.value)} placeholder="administracion@empresa.com" />
          </label>
          <label className="text-xs font-bold text-latus-ink">Fin de prueba
            <Input className={inputClass} type="date" value={draft.trial_ends_at} onChange={(event) => set("trial_ends_at", event.target.value)} />
          </label>
          <label className="text-xs font-bold text-latus-ink">Fin del período actual
            <Input className={inputClass} type="date" value={draft.current_period_end} onChange={(event) => set("current_period_end", event.target.value)} />
          </label>
          <label className="text-xs font-bold text-latus-ink">Fin del período de gracia
            <Input className={inputClass} type="date" value={draft.grace_ends_at} onChange={(event) => set("grace_ends_at", event.target.value)} />
          </label>
          <label className="text-xs font-bold text-latus-ink sm:col-span-2">Notas internas
            <textarea className={`${inputClass} min-h-[90px] resize-y py-3`} value={draft.internal_notes} onChange={(event) => set("internal_notes", event.target.value)} placeholder="Acuerdos comerciales, contacto o seguimiento..." />
          </label>
          <label className="text-xs font-bold text-latus-ink sm:col-span-2">Fee de IA específico
            <div className="relative">
              <Input className={`${inputClass} pr-9`} type="number" min="0" max="500" step="0.1" value={draft.ai_fee_percent} onChange={(event) => set("ai_fee_percent", event.target.value)} placeholder={`Global: ${organization.ai_billing?.fee_percent ?? 20}%`} />
              <span className="absolute bottom-2.5 right-3 text-sm text-latus-muted">%</span>
            </div>
            <span className="mt-1 block font-normal text-latus-muted">Dejalo vacío para usar el fee global de la plataforma.</span>
          </label>
          <div className="sm:col-span-2 rounded-2xl border border-violet-200 bg-violet-50/60 p-4">
            <p className="text-xs font-extrabold uppercase tracking-[0.12em] text-violet-800">Cobro variable de IA por empresa</p>
            <p className="mt-1 text-xs leading-5 text-violet-700">Este control define si la empresa queda fuera del cobro, solo simula, participa como piloto manual o entra en la automatización.</p>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label className="text-xs font-bold text-latus-ink">Modo de cobro
                <select className={inputClass} value={draft.ai_billing_state} onChange={(event) => set("ai_billing_state", event.target.value)}>
                  {Object.entries(AI_BILLING_STATES).map(([value, meta]) => <option key={value} value={value}>{meta.label}</option>)}
                </select>
                <span className="mt-1.5 block font-normal leading-4 text-latus-muted">{AI_BILLING_STATES[draft.ai_billing_state]?.help}</span>
              </label>
              <label className="text-xs font-bold text-latus-ink">Facturar consumo desde
                <Input className={inputClass} type="date" value={draft.ai_billing_start_date} onChange={(event) => set("ai_billing_start_date", event.target.value)} />
                <span className="mt-1.5 block font-normal leading-4 text-latus-muted">Al habilitar Piloto o Activo sin fecha, se usará el momento de activación.</span>
              </label>
              <label className="text-xs font-bold text-latus-ink sm:col-span-2">Colchón cambiario específico
                <div className="relative max-w-xs"><Input className={`${inputClass} pr-9`} type="number" min="0" max="100" step="0.5" value={draft.ai_fx_buffer_percent} onChange={(event) => set("ai_fx_buffer_percent", event.target.value)} placeholder="Usar política global" /><span className="absolute bottom-2.5 right-3 text-sm text-latus-muted">%</span></div>
                <span className="mt-1 block font-normal text-latus-muted">Dejalo vacío para heredar el porcentaje global.</span>
              </label>
              <label className="text-xs font-bold text-latus-ink">Margen total mínimo propio
                <div className="relative"><Input className={`${inputClass} pr-9`} type="number" min="0" max="100" step="0.5" value={draft.ai_min_net_margin_percent} onChange={(event) => set("ai_min_net_margin_percent", event.target.value)} placeholder="Heredar global" /><span className="absolute bottom-2.5 right-3 text-sm text-latus-muted">%</span></div>
              </label>
              <label className="text-xs font-bold text-latus-ink">Margen IA mínimo propio
                <div className="relative"><Input className={`${inputClass} pr-9`} type="number" min="0" max="100" step="0.5" value={draft.ai_min_margin_percent} onChange={(event) => set("ai_min_margin_percent", event.target.value)} placeholder="Heredar global" /><span className="absolute bottom-2.5 right-3 text-sm text-latus-muted">%</span></div>
              </label>
              <label className="text-xs font-bold text-latus-ink sm:col-span-2">Acción propia ante margen insuficiente
                <select className={inputClass} value={draft.ai_profitability_enforcement} onChange={(event) => set("ai_profitability_enforcement", event.target.value)}><option value="">Heredar política global</option><option value="block">Bloquear liquidación</option><option value="warn">Advertir y continuar</option></select>
              </label>
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-3 border-t border-latus-warm-border bg-white px-6 py-4">
          <Button type="button" variant="outline" onClick={onClose}>Cancelar</Button>
          <Button type="button" disabled={saving} onClick={() => onSave({
            subscription: {
              plan_code: draft.plan_code,
              subscription_status: draft.subscription_status,
              license_status: draft.license_status,
              trial_ends_at: draft.trial_ends_at,
              current_period_end: draft.current_period_end,
              grace_ends_at: draft.grace_ends_at,
              billing_email: draft.billing_email,
              internal_notes: draft.internal_notes,
              ai_fee_percent: draft.ai_fee_percent === "" ? null : Number(draft.ai_fee_percent),
            },
            aiBilling: {
              state: draft.ai_billing_state,
              billing_start_date: draft.ai_billing_start_date || null,
              fx_buffer_percent: draft.ai_fx_buffer_percent === "" ? null : Number(draft.ai_fx_buffer_percent),
              ai_fee_percent: draft.ai_fee_percent === "" ? null : Number(draft.ai_fee_percent),
              min_net_margin_percent: draft.ai_min_net_margin_percent === "" ? null : Number(draft.ai_min_net_margin_percent),
              min_ai_margin_percent: draft.ai_min_margin_percent === "" ? null : Number(draft.ai_min_margin_percent),
              profitability_enforcement: draft.ai_profitability_enforcement || null,
            },
          })} className="bg-latus-blue text-white hover:bg-latus-blue/90">{saving ? "Guardando..." : "Guardar licencia y cobro IA"}</Button>
        </div>
      </div>
    </div>
  );
}

function CreateCompanyModal({ onClose, onCreate, creating }) {
  const [draft, setDraft] = useState({
    name: "",
    plan_code: "starter",
    subscription_status: "active",
    license_status: "active",
    duration_months: 12,
    trial_days: 0,
    billing_email: "",
    admin_name: "",
    admin_email: "",
    admin_password: "Latus12345!",
    ai_fee_percent: "",
    internal_notes: "",
  });

  const set = (field, value) => setDraft((c) => ({ ...c, [field]: value }));

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!draft.name.trim()) {
      toast.error("El nombre de la empresa es obligatorio");
      return;
    }
    onCreate({
      ...draft,
      name: draft.name.trim(),
      billing_email: draft.billing_email.trim() || undefined,
      admin_name: draft.admin_name.trim() || undefined,
      admin_email: draft.admin_email.trim() || undefined,
      admin_password: draft.admin_password || undefined,
      duration_months: Number(draft.duration_months),
      trial_days: Number(draft.trial_days),
      ai_fee_percent: draft.ai_fee_percent === "" ? undefined : Number(draft.ai_fee_percent),
    });
  };

  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-latus-ink/55 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-[24px] border border-white/10 bg-latus-cream shadow-2xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-latus-warm-border bg-white px-6 py-5">
          <div>
            <p className="text-xs font-extrabold uppercase tracking-[0.14em] text-latus-blue">Alta Manual de Empresa</p>
            <h2 className="mt-1 text-xl font-extrabold text-latus-ink">Crear Nueva Empresa y Licencia</h2>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-latus-muted hover:bg-latus-cream"><X className="h-5 w-5" /></button>
        </div>
        <form onSubmit={handleSubmit} className="grid gap-4 p-6 sm:grid-cols-2">
          <label className="text-xs font-bold text-latus-ink sm:col-span-2">Nombre de la Empresa *
            <Input className={inputClass} value={draft.name} onChange={(e) => set("name", e.target.value)} placeholder="Ej: Inmobiliaria Ramos S.A." required />
          </label>
          <label className="text-xs font-bold text-latus-ink">Plan Comercial
            <select className={inputClass} value={draft.plan_code} onChange={(e) => set("plan_code", e.target.value)}>
              {Object.entries(PLAN_NAMES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="text-xs font-bold text-latus-ink">Duración Licencia Manual
            <select className={inputClass} value={draft.duration_months} onChange={(e) => set("duration_months", e.target.value)}>
              <option value="1">1 Mes</option>
              <option value="3">3 Meses</option>
              <option value="6">6 Meses</option>
              <option value="12">12 Meses (1 año)</option>
              <option value="24">24 Meses (2 años)</option>
            </select>
          </label>
          <label className="text-xs font-bold text-latus-ink">Estado de Suscripción
            <select className={inputClass} value={draft.subscription_status} onChange={(e) => set("subscription_status", e.target.value)}>
              {Object.entries(SUBSCRIPTION_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="text-xs font-bold text-latus-ink">Estado de Licencia
            <select className={inputClass} value={draft.license_status} onChange={(e) => set("license_status", e.target.value)}>
              {Object.entries(LICENSE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="text-xs font-bold text-latus-ink">Email Facturación (Opcional)
            <Input className={inputClass} type="email" value={draft.billing_email} onChange={(e) => set("billing_email", e.target.value)} placeholder="facturacion@empresa.com" />
          </label>
          <label className="text-xs font-bold text-latus-ink">Días de Prueba Gratis
            <Input className={inputClass} type="number" min="0" value={draft.trial_days} onChange={(e) => set("trial_days", e.target.value)} />
          </label>
          <div className="sm:col-span-2 my-1 border-t border-latus-warm-border pt-3">
            <p className="text-xs font-extrabold uppercase tracking-wider text-latus-blue">Usuario Administrador Inicial de la Empresa (Opcional)</p>
          </div>
          <label className="text-xs font-bold text-latus-ink">Nombre Completo Admin
            <Input className={inputClass} value={draft.admin_name} onChange={(e) => set("admin_name", e.target.value)} placeholder="Ej: Juan Pérez" />
          </label>
          <label className="text-xs font-bold text-latus-ink">Email del Administrador
            <Input className={inputClass} type="email" value={draft.admin_email} onChange={(e) => set("admin_email", e.target.value)} placeholder="juan@empresa.com" />
          </label>
          <label className="text-xs font-bold text-latus-ink sm:col-span-2">Contraseña Temporal Inicial
            <Input className={inputClass} value={draft.admin_password} onChange={(e) => set("admin_password", e.target.value)} placeholder="Latus12345!" />
          </label>
          <label className="text-xs font-bold text-latus-ink sm:col-span-2">Fee % IA Específico (Opcional)
            <Input className={inputClass} type="number" min="0" max="500" step="0.1" value={draft.ai_fee_percent} onChange={(e) => set("ai_fee_percent", e.target.value)} placeholder="Usar fee global de la plataforma" />
          </label>
          <label className="text-xs font-bold text-latus-ink sm:col-span-2">Notas Internas Superadmin
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={draft.internal_notes} onChange={(e) => set("internal_notes", e.target.value)} placeholder="Detalles comerciales, pago por transferencia, contrato..." />
          </label>
          <div className="flex justify-end gap-3 border-t border-latus-warm-border bg-white px-6 py-4 sm:col-span-2 -mx-6 -mb-6 mt-2 rounded-b-[24px]">
            <Button type="button" variant="outline" onClick={onClose}>Cancelar</Button>
            <Button type="submit" disabled={creating} className="bg-latus-blue text-white hover:bg-latus-blue/90">{creating ? "Creando empresa..." : "Crear Empresa y Licencia"}</Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function SimulationModal({ organizationId, orgName, onClose }) {
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const simulationQ = useQuery({
    queryKey: ["ai-simulation", organizationId, periodStart, periodEnd],
    queryFn: () => api.post("/platform/ai-billing/simulate", {
      organization_id: organizationId,
      period_start: periodStart || undefined,
      period_end: periodEnd || undefined,
    }).then((r) => r.data),
  });

  const sim = simulationQ.data;
  const usage = sim?.usage || {};
  const rates = sim?.rates || {};
  const amounts = sim?.amounts || {};
  const profitability = sim?.profitability || {};
  const state = AI_BILLING_STATES[sim?.organization_billing_state] || AI_BILLING_STATES.disabled;
  const money = (value) => Number(value || 0).toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const dateLabel = (value, endExclusive = false) => {
    if (!value) return "—";
    const parsed = new Date(value);
    if (endExclusive) parsed.setMilliseconds(parsed.getMilliseconds() - 1);
    return parsed.toLocaleDateString("es-AR", { timeZone: "UTC" });
  };
  const errorMessage = simulationQ.error?.response?.data?.detail || "No se pudo obtener la simulación de liquidación.";

  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-latus-ink/55 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-[24px] border border-white/10 bg-latus-cream shadow-2xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-latus-warm-border bg-white px-6 py-5">
          <div>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-3 py-0.5 text-xs font-black text-amber-800">
              MODO SIMULACIÓN · SIN CARGOS
            </span>
            <h2 className="mt-1 text-xl font-extrabold text-latus-ink">Proyección de liquidación · {orgName}</h2>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-latus-muted hover:bg-latus-cream"><X className="h-5 w-5" /></button>
        </div>

        <div className="p-6 space-y-5">
          <div className="flex gap-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-xs leading-relaxed text-emerald-900">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            <p><strong>Simulación aislada.</strong> Consulta el consumo ya registrado y aplica las reglas vigentes. No guarda liquidaciones, no llama a proveedores y no genera cargos en Mercado Pago.</p>
          </div>

          <div className="grid gap-3 rounded-xl border border-latus-warm-border bg-white p-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <label className="text-xs font-bold text-latus-ink">Desde
              <Input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} max={periodEnd || undefined} className="mt-1 h-9 border-latus-warm-border" />
            </label>
            <label className="text-xs font-bold text-latus-ink">Hasta, inclusive
              <Input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} min={periodStart || undefined} className="mt-1 h-9 border-latus-warm-border" />
            </label>
            <Button type="button" variant="outline" onClick={() => simulationQ.refetch()} disabled={simulationQ.isFetching} className="h-9 border-latus-warm-border">
              <RefreshCw className={`h-4 w-4 ${simulationQ.isFetching ? "animate-spin" : ""}`} />Actualizar
            </Button>
            <p className="text-[11px] text-latus-muted sm:col-span-3">Sin fechas manuales se proyecta el ciclo actual pendiente de la empresa.</p>
          </div>

          {simulationQ.isLoading ? (
            <div className="grid min-h-[200px] place-items-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" /></div>
          ) : sim ? (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-xl border border-latus-warm-border bg-white p-4">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Período proyectado</p>
                  <p className="mt-1 text-sm font-extrabold text-latus-ink">{dateLabel(sim.period?.start)} → {dateLabel(sim.period?.end, true)}</p>
                </div>
                <div className="rounded-xl border border-latus-warm-border bg-white p-4">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Consumo consolidado</p>
                  <p className="mt-1 text-sm font-extrabold text-latus-ink">{usage.calls || 0} llamadas</p>
                  <p className="mt-0.5 text-xs text-latus-muted">{Number(usage.total_tokens || 0).toLocaleString("es-AR")} tokens</p>
                </div>
                <div className="rounded-xl border border-latus-warm-border bg-white p-4">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Cotización USD → ARS</p>
                  <p className="mt-1 text-sm font-extrabold text-latus-ink">$ {Number(rates.usd_to_ars_rate || 0).toLocaleString("es-AR")}</p>
                  <p className="mt-0.5 text-xs text-latus-muted">{rates.exchange_rate_source || "manual"} · +{rates.fx_buffer_percent || 0}% colchón</p>
                </div>
                <div className="rounded-xl border border-latus-warm-border bg-white p-4">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Estado de la empresa</p>
                  <span className={`mt-1 inline-flex rounded-full border px-2.5 py-1 text-xs font-extrabold ${state.tone}`}>{state.label}</span>
                  <p className="mt-1 text-[10px] text-latus-muted">Buffer: {rates.buffer_source === "organization" ? "propio" : "global"}</p>
                </div>
              </div>

              <div className="rounded-xl border border-latus-warm-border bg-white overflow-hidden text-xs">
                <div className="border-b border-latus-warm-border bg-latus-cream/40 px-4 py-3 font-extrabold text-latus-ink">Desglose económico estimado</div>
                <div className="space-y-2.5 p-4">
                  <div className="flex justify-between">
                    <span className="text-latus-muted">Plan comercial ({sim.plan_name})</span>
                    <span className="font-extrabold text-latus-ink">$ {money(amounts.plan_amount_ars)} ARS</span>
                  </div>
                  <div className="flex justify-between pt-2">
                    <span className="text-latus-muted">Costo directo del proveedor</span>
                    <span className="font-mono text-slate-600">USD {Number(usage.base_cost_usd || 0).toFixed(6)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-latus-muted">Fee Latus congelado en los consumos ({usage.effective_fee_percent || 0}% efectivo)</span>
                    <span className="font-mono font-bold text-emerald-700">+ USD {Number(usage.ai_fee_usd || 0).toFixed(6)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-latus-muted">Consumo IA facturable</span>
                    <span className="font-mono font-bold text-latus-ink">USD {Number(usage.billable_cost_usd || 0).toFixed(6)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-latus-muted">Consumo IA convertido + colchón</span>
                    <span className="font-mono font-extrabold text-violet-700">$ {money(amounts.ai_amount_ars)} ARS</span>
                  </div>
                  <div className="flex justify-between pt-3 border-t border-latus-warm-border text-sm font-black">
                    <span className="text-latus-ink">Total proyectado al cliente</span>
                    <span className="text-base text-latus-blue">$ {money(amounts.total_amount_ars)} ARS</span>
                  </div>
                  <div className="mt-3 grid gap-2 rounded-lg bg-slate-50 p-3 sm:grid-cols-3">
                    <div><p className="text-[10px] uppercase text-latus-muted">Costo proveedor ARS</p><p className="font-bold text-latus-ink">$ {money(profitability.provider_cost_ars)}</p></div>
                    <div><p className="text-[10px] uppercase text-latus-muted">Costo Mercado Pago estimado</p><p className="font-bold text-latus-ink">$ {money(profitability.mp_fee_ars)}</p></div>
                    <div><p className="text-[10px] uppercase text-latus-muted">Impuestos estimados</p><p className="font-bold text-latus-ink">$ {money(profitability.tax_ars)}</p></div>
                  </div>
                  <div className={`mt-3 flex items-center justify-between rounded-lg border p-3 font-bold ${profitability.is_profitable ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}`}>
                    <span>Margen neto estimado de esta liquidación</span>
                    <span>$ {money(profitability.net_profit_ars)} · {profitability.net_margin_percent || 0}%</span>
                  </div>
                  <div className={`mt-2 flex items-center justify-between rounded-lg border p-3 font-bold ${profitability.meets_ai_margin ? "border-violet-200 bg-violet-50 text-violet-800" : "border-rose-200 bg-rose-50 text-rose-800"}`}>
                    <span>Margen aislado del consumo de IA</span>
                    <span>$ {money(profitability.ai_net_profit_ars)} · {profitability.ai_net_margin_percent == null ? "Sin consumo" : `${profitability.ai_net_margin_percent}%`}</span>
                  </div>
                </div>
              </div>

              <div className="grid gap-3 text-[11px] sm:grid-cols-2">
                <div className="rounded-xl border border-latus-warm-border bg-white p-4 text-latus-muted">
                  <p className="font-extrabold text-latus-ink">Trazabilidad del cálculo</p>
                  <p className="mt-1">Fee configurado actual: {usage.configured_fee_percent ?? 0}% ({usage.fee_source === "organization" ? "empresa" : "global"}). El fee efectivo se calcula con los valores congelados en cada consumo.</p>
                  <p className="mt-1">Fuentes de costo: {Object.entries(usage.cost_sources || {}).map(([source, count]) => `${source}: ${count}`).join(" · ") || "sin consumos"}.</p>
                </div>
                <div className="rounded-xl border border-latus-warm-border bg-white p-4 text-latus-muted">
                  <p className="font-extrabold text-latus-ink">Supuestos</p>
                  <p className="mt-1">El límite de {Number(usage.operational_token_limit || 0).toLocaleString("es-AR")} tokens es operativo: no se descuenta como consumo gratuito. El margen no incluye otros costos operativos del plan.</p>
                </div>
              </div>

              {profitability.warning && (
                <div className="flex gap-2 rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs font-bold text-rose-800">
                  <ShieldAlert className="h-4 w-4 shrink-0" />{profitability.warning}
                </div>
              )}
            </div>
          ) : (
            <p className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs font-bold text-rose-700">{errorMessage}</p>
          )}
        </div>

        <div className="flex justify-end border-t border-latus-warm-border bg-white px-6 py-4 rounded-b-[24px]">
          <Button type="button" onClick={onClose} className="bg-latus-ink text-white hover:bg-latus-ink/90">Cerrar Simulación</Button>
        </div>
      </div>
    </div>
  );
}

function PilotApprovalModal({ organization, onClose }) {
  const qc = useQueryClient();
  const [confirmed, setConfirmed] = useState(false);
  const previewQ = useQuery({
    queryKey: ["ai-pilot-preview", organization.organization_id],
    queryFn: () => api.post("/platform/ai-billing/pilot-preview", {
      organization_id: organization.organization_id,
    }).then((r) => r.data),
  });
  const preview = previewQ.data;
  const statement = preview?.statement;
  const applyPilot = useMutation({
    mutationFn: () => api.post("/platform/ai-billing/pilot-apply", {
      organization_id: organization.organization_id,
      preview_fingerprint: preview.preview_fingerprint,
      confirmation: "APLICAR",
    }).then((r) => r.data),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["ai-settlements"] });
      qc.invalidateQueries({ queryKey: ["platform-organizations"] });
      toast.success(`Piloto aprobado: próximo importe $ ${Number(result.total_amount_ars || 0).toLocaleString("es-AR")}`);
      onClose();
    },
    onError: (error) => {
      const detail = error.response?.data?.detail;
      const message = typeof detail === "string" ? detail : detail?.message;
      toast.error(message || "No se pudo aplicar la liquidación piloto");
      setConfirmed(false);
      previewQ.refetch();
    },
  });
  const money = (value) => Number(value || 0).toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div className="fixed inset-0 z-[110] grid place-items-center bg-latus-ink/60 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-[24px] border border-white/10 bg-latus-cream shadow-2xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-latus-warm-border bg-white px-6 py-5">
          <div>
            <span className="inline-flex rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs font-black text-violet-800">APROBACIÓN PILOTO</span>
            <h2 className="mt-2 text-xl font-extrabold text-latus-ink">Revisar liquidación · {organization.name}</h2>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-latus-muted hover:bg-latus-cream"><X className="h-5 w-5" /></button>
        </div>

        <div className="space-y-4 p-6">
          <div className="flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-relaxed text-amber-900">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <p><strong>Esta acción modifica Mercado Pago.</strong> Establece el próximo importe de la suscripción; no debita dinero inmediatamente. El cobro se realizará en la fecha de renovación.</p>
          </div>

          {previewQ.isLoading ? (
            <div className="grid min-h-[220px] place-items-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-violet-600 border-t-transparent" /></div>
          ) : previewQ.isError ? (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700">No se pudo preparar la revisión del piloto.</div>
          ) : (
            <>
              {preview?.blockers?.length > 0 && (
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                  <p className="text-sm font-extrabold text-rose-800">La liquidación todavía no puede aplicarse</p>
                  <ul className="mt-2 space-y-1 text-xs text-rose-700">
                    {preview.blockers.map((blocker) => <li key={blocker.code}>• {blocker.message}</li>)}
                  </ul>
                </div>
              )}

              {statement && (
                <div className="overflow-hidden rounded-xl border border-latus-warm-border bg-white">
                  <div className="border-b border-latus-warm-border bg-latus-cream/50 px-4 py-3 text-xs font-extrabold text-latus-ink">Importe exacto a aprobar</div>
                  <div className="space-y-3 p-4 text-sm">
                    <div className="flex justify-between gap-4"><span className="text-latus-muted">Período de consumo</span><span className="font-bold text-latus-ink">{String(statement.period_start || "").slice(0, 10)} → {String(statement.period_end || "").slice(0, 10)}</span></div>
                    <div className="flex justify-between gap-4"><span className="text-latus-muted">Llamadas y tokens</span><span className="font-bold text-latus-ink">{Number(statement.calls || 0).toLocaleString("es-AR")} · {Number(statement.tokens || 0).toLocaleString("es-AR")}</span></div>
                    <div className="flex justify-between gap-4"><span className="text-latus-muted">Costo proveedor</span><span className="font-mono text-latus-ink">USD {Number(statement.base_cost_usd || 0).toFixed(6)}</span></div>
                    <div className="flex justify-between gap-4"><span className="text-latus-muted">Fee Latus</span><span className="font-mono font-bold text-emerald-700">USD {Number(statement.ai_fee_usd || 0).toFixed(6)}</span></div>
                    <div className="flex justify-between gap-4"><span className="text-latus-muted">Cotización y colchón</span><span className="font-bold text-latus-ink">$ {Number(statement.usd_to_ars_rate || 0).toLocaleString("es-AR")} · +{statement.fx_buffer_percent || 0}%</span></div>
                    <div className="border-t border-latus-warm-border pt-3">
                      <div className="flex justify-between gap-4"><span className="text-latus-muted">Plan</span><span className="font-bold text-latus-ink">$ {money(statement.plan_amount_ars)} ARS</span></div>
                      <div className="mt-2 flex justify-between gap-4"><span className="text-latus-muted">Consumo IA</span><span className="font-bold text-violet-700">$ {money(statement.ai_amount_ars)} ARS</span></div>
                    </div>
                    <div className="flex justify-between gap-4 rounded-lg bg-latus-blue/10 p-3 text-base font-black text-latus-blue"><span>Próximo importe total</span><span>$ {money(statement.total_amount_ars)} ARS</span></div>
                    <div className={`grid gap-2 rounded-lg border p-3 text-xs sm:grid-cols-2 ${statement.profitability?.is_profitable ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}`}>
                      <div><span className="block text-[10px] font-bold uppercase">Margen total</span><strong>{statement.profitability?.net_margin_percent ?? 0}%</strong> <span>(mín. {statement.profitability?.min_margin_percent ?? 0}%)</span></div>
                      <div><span className="block text-[10px] font-bold uppercase">Margen de IA</span><strong>{statement.profitability?.ai_net_margin_percent ?? 0}%</strong> <span>(mín. {statement.profitability?.min_ai_margin_percent ?? 0}%)</span></div>
                    </div>
                  </div>
                </div>
              )}

              {preview?.ready && (
                <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-violet-200 bg-violet-50 p-4 text-xs text-violet-900">
                  <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-0.5 h-4 w-4 accent-violet-600" />
                  <span><strong>Revisé el período y el importe.</strong> Autorizo actualizar el próximo cobro de esta empresa en Mercado Pago.</span>
                </label>
              )}
            </>
          )}
        </div>

        <div className="flex justify-end gap-3 border-t border-latus-warm-border bg-white px-6 py-4">
          <Button type="button" variant="outline" onClick={onClose}>Cancelar</Button>
          <Button type="button" disabled={!preview?.ready || !confirmed || applyPilot.isPending} onClick={() => applyPilot.mutate()} className="bg-violet-700 text-white hover:bg-violet-800">
            {applyPilot.isPending ? "Verificando y aplicando..." : "Aprobar próximo importe"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function SuperadminExecutiveDashboardPanel({
  selectedOrgId,
  setSelectedOrgId,
  period,
  setPeriod,
  startDate,
  setStartDate,
  endDate,
  setEndDate,
  organizations,
  onExportCSV,
  isExporting,
}) {
  const dashQ = useQuery({
    queryKey: ["platform-financial-dashboard", selectedOrgId, period, startDate, endDate],
    queryFn: () => {
      const params = {
        organization_id: selectedOrgId !== "__all__" ? selectedOrgId : undefined,
        period,
        start_date: period === "custom" ? startDate : undefined,
        end_date: period === "custom" ? endDate : undefined,
      };
      return api.get("/platform/financial-dashboard", { params }).then((r) => r.data);
    },
  });

  const d = dashQ.data?.summary;

  return (
    <section className="overflow-hidden rounded-[24px] border border-latus-warm-border bg-white shadow-sm" data-testid="superadmin-financial-dashboard">
      <div className="flex flex-col gap-4 border-b border-latus-warm-border bg-gradient-to-r from-latus-ink to-slate-900 p-5 sm:flex-row sm:items-center sm:justify-between sm:px-6 text-white">
        <div>
          <div className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-emerald-500/20 text-emerald-400"><BadgeDollarSign className="h-4 w-4" /></span>
            <span className="text-xs font-extrabold uppercase tracking-widest text-emerald-400">Dashboard de Gestión Financiera & Cobros</span>
          </div>
          <h2 className="mt-1 text-xl font-black text-white">Resumen Ejecutivo Superadmin</h2>
          <p className="mt-1 text-xs text-slate-300">Monitoreo consolidado de ingresos por suscripción, consumo variable de IA, costo de proveedores y margen neto.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {d && (
            <div className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-right">
              <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Cotización BCRA Aplicada</p>
              <p className="font-mono text-sm font-extrabold text-emerald-400">$ {Number(d.usd_to_ars_rate || 0).toLocaleString("es-AR")} <span className="text-xs text-slate-300">(+{d.fx_buffer_percent}% buffer)</span></p>
            </div>
          )}
          <Button onClick={onExportCSV} disabled={isExporting} variant="outline" className="rounded-xl border-white/20 bg-white/10 text-white hover:bg-white/20 text-xs font-bold">
            {isExporting ? "Generando CSV..." : "Exportar Registro CSV"}
          </Button>
        </div>
      </div>

      <div className="border-b border-latus-warm-border bg-latus-cream/40 p-4 sm:px-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="w-full sm:w-64">
            <label className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Filtrar por Empresa</label>
            <select value={selectedOrgId} onChange={(e) => setSelectedOrgId(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-latus-warm-border bg-white px-3 text-xs font-bold text-latus-ink">
              <option value="__all__">🏢 Todas las empresas (Consolidado)</option>
              {organizations.map((org) => (
                <option key={org.organization_id} value={org.organization_id}>{org.name} ({org.organization_id})</option>
              ))}
            </select>
          </div>

          <div className="w-full sm:w-52">
            <label className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Filtrar por Período</label>
            <select value={period} onChange={(e) => setPeriod(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-latus-warm-border bg-white px-3 text-xs font-bold text-latus-ink">
              <option value="this_month">📅 Este mes</option>
              <option value="prev_month">📅 Mes anterior</option>
              <option value="last_30">📅 Últimos 30 días</option>
              <option value="custom">📆 Rango personalizado</option>
              <option value="all">♾️ Todo el historial</option>
            </select>
          </div>

          {period === "custom" && (
            <>
              <div className="w-full sm:w-36">
                <label className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Fecha Desde</label>
                <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="mt-1 h-9 rounded-lg border-latus-warm-border text-xs" />
              </div>
              <div className="w-full sm:w-36">
                <label className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Fecha Hasta</label>
                <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="mt-1 h-9 rounded-lg border-latus-warm-border text-xs" />
              </div>
            </>
          )}

          {dashQ.isFetching && (
            <div className="flex items-center gap-1.5 text-xs text-latus-blue font-bold pb-2">
              <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" />
              Actualizando...
            </div>
          )}
        </div>
      </div>

      {dashQ.isLoading ? (
        <div className="grid min-h-[160px] place-items-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" /></div>
      ) : d ? (
        <div className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-4 sm:p-6 bg-latus-cream/20">
          <div className="rounded-2xl border border-latus-warm-border bg-white p-5 shadow-sm">
            <p className="text-xs font-bold uppercase tracking-wider text-latus-muted">Facturación Bruta Estimada</p>
            <p className="mt-2 text-3xl font-black tracking-tight text-latus-ink">$ {Number(d.total_revenue_ars || 0).toLocaleString("es-AR")} <span className="text-xs text-latus-muted font-normal">ARS</span></p>
            <p className="mt-1 text-[11px] text-latus-muted">Planes $ {Number(d.monthly_subscriptions_ars || 0).toLocaleString("es-AR")} + IA $ {Number(d.monthly_ai_billable_ars || 0).toLocaleString("es-AR")}</p>
          </div>

          <div className="rounded-2xl border border-emerald-200 bg-emerald-50/50 p-5 shadow-sm">
            <p className="text-xs font-bold uppercase tracking-wider text-emerald-800">Ganancia Neta Estimada Latus</p>
            <p className="mt-2 text-3xl font-black tracking-tight text-emerald-700">$ {Number(d.estimated_net_profit_ars || 0).toLocaleString("es-AR")} <span className="text-xs font-normal">ARS</span></p>
            <p className="mt-1 text-[11px] font-bold text-emerald-700">Margen Neto: {d.net_margin_percent}%</p>
            <p className="mt-1 text-[10px] text-emerald-800">{d.healthy_organizations || 0} rentables · {d.at_risk_organizations || 0} ajustadas · {d.blocked_organizations || 0} no rentables</p>
          </div>

          <div className="rounded-2xl border border-violet-200 bg-violet-50/50 p-5 shadow-sm">
            <p className="text-xs font-bold uppercase tracking-wider text-violet-800">Fee Comercial IA Bruto</p>
            <p className="mt-2 text-3xl font-black tracking-tight text-violet-700">USD ${Number(d.monthly_ai_fee_gross_profit_usd || 0).toFixed(2)}</p>
            <p className="mt-1 text-[11px] text-violet-700 font-medium">Facturable clientes: USD ${Number(d.monthly_ai_billable_usd || 0).toFixed(2)}</p>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5 shadow-sm">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-600">Costo Proveedores IA (Directo)</p>
            <p className="mt-2 text-3xl font-black tracking-tight text-slate-800">USD ${Number(d.monthly_ai_provider_cost_usd || 0).toFixed(2)}</p>
            <p className="mt-1 text-[11px] text-slate-500">Anthropic / OpenAI / Google</p>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export default function Plataforma() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [simulatingOrg, setSimulatingOrg] = useState(null);
  const [pilotReviewOrg, setPilotReviewOrg] = useState(null);

  const [selectedOrgId, setSelectedOrgId] = useState("__all__");
  const [period, setPeriod] = useState("this_month");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [isExporting, setIsExporting] = useState(false);

  const organizationsQ = useQuery({
    queryKey: ["platform-organizations"],
    queryFn: () => api.get("/platform/organizations").then((response) => response.data),
  });
  const createOrganization = useMutation({
    mutationFn: (body) => api.post("/platform/organizations", body).then((r) => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["platform-organizations"] });
      setShowCreateModal(false);
      const adminInfo = data?.admin_user?.temp_password
        ? ` (Admin: ${data.admin_user.email} / Pwd: ${data.admin_user.temp_password})`
        : "";
      toast.success(`Empresa '${data?.organization?.name}' creada con éxito${adminInfo}`);
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo crear la empresa"),
  });
  const updateSubscription = useMutation({
    mutationFn: async ({ organizationId, body }) => {
      const subscription = await api.patch(
        `/platform/organizations/${organizationId}/subscription`, body.subscription,
      );
      await api.patch(
        `/platform/organizations/${organizationId}/ai-variable-billing`, body.aiBilling,
      );
      return subscription;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["platform-organizations"] });
      setSelected(null);
      toast.success("Licencia actualizada");
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo actualizar la licencia"),
  });
  const organizations = useMemo(() => organizationsQ.data || [], [organizationsQ.data]);
  const filtered = useMemo(() => {
    let list = organizations;
    if (selectedOrgId !== "__all__") {
      list = list.filter((item) => item.organization_id === selectedOrgId);
    }
    const term = search.trim().toLowerCase();
    return term ? list.filter((item) => `${item.name} ${item.organization_id} ${item.billing_email || ""}`.toLowerCase().includes(term)) : list;
  }, [organizations, search, selectedOrgId]);
  const active = organizations.filter((item) => item.access?.allowed).length;
  const pending = organizations.filter((item) => item.latest_request?.status === "pending").length;
  const aiBillable = organizations.reduce((total, item) => total + Number(item.ai_billing?.this_month?.billable_cost_usd || 0), 0);

  const handleExportCSV = async () => {
    try {
      setIsExporting(true);
      const params = {
        organization_id: selectedOrgId !== "__all__" ? selectedOrgId : undefined,
        period,
        start_date: period === "custom" ? startDate : undefined,
        end_date: period === "custom" ? endDate : undefined,
      };
      const response = await api.get("/platform/financial-dashboard/export", {
        params,
        responseType: "blob",
      });

      const blob = new Blob([response.data], { type: "text/csv;charset=utf-8;" });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const filename = `dashboard_financiero_${selectedOrgId !== "__all__" ? selectedOrgId : "global"}_${period}.csv`;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      if (link.parentNode) {
        link.parentNode.removeChild(link);
      }
      window.URL.revokeObjectURL(url);
      toast.success("Exportación descargada con éxito");
    } catch (error) {
      toast.error("Error al exportar los datos en CSV");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <AppLayout title="Plataforma">
      <div className="mx-auto w-full max-w-[1500px] space-y-6 p-4 sm:p-6 lg:p-8" data-testid="platform-page">
        <SuperadminExecutiveDashboardPanel
          selectedOrgId={selectedOrgId}
          setSelectedOrgId={setSelectedOrgId}
          period={period}
          setPeriod={setPeriod}
          startDate={startDate}
          setStartDate={setStartDate}
          endDate={endDate}
          setEndDate={setEndDate}
          organizations={organizations}
          onExportCSV={handleExportCSV}
          isExporting={isExporting}
        />

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[
            { label: "Empresas registradas", value: organizations.length, icon: Building2, tone: "bg-sky-50 text-sky-700" },
            { label: "Licencias habilitadas", value: active, icon: CheckCircle2, tone: "bg-emerald-50 text-emerald-700" },
            { label: "Solicitudes pendientes", value: pending, icon: ShieldAlert, tone: "bg-amber-50 text-amber-800" },
            { label: "IA facturable este mes", value: `USD ${aiBillable.toFixed(2)}`, icon: BadgeDollarSign, tone: "bg-violet-50 text-violet-700" },
          ].map(({ label, value, icon: Icon, tone }) => (
            <article key={label} className="flex items-center gap-4 rounded-2xl border border-latus-warm-border bg-white p-5 shadow-sm"><span className={`grid h-12 w-12 place-items-center rounded-2xl ${tone}`}><Icon className="h-5 w-5" /></span><div><p className="text-3xl font-black tracking-tight text-latus-ink">{value}</p><p className="text-xs font-bold uppercase tracking-wider text-latus-muted">{label}</p></div></article>
          ))}
        </section>

        <section className="flex flex-col gap-4 rounded-2xl border border-sky-200 bg-gradient-to-r from-sky-50 to-white p-5 shadow-sm sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-latus-blue text-white"><Sparkles className="h-5 w-5" /></span>
            <div><h2 className="font-extrabold text-latus-ink">IA global de la plataforma</h2><p className="mt-1 text-sm text-latus-muted">Administrá el proveedor, los modelos y las credenciales que usan las empresas licenciadas.</p></div>
          </div>
          <Button asChild className="shrink-0 bg-latus-blue text-white hover:bg-latus-blue/90"><Link to="/configuracion?tab=ai"><KeyRound className="h-4 w-4" />Configurar IA global</Link></Button>
        </section>

        <AIVariableBillingPanel />

        <section className="overflow-hidden rounded-[24px] border border-latus-warm-border bg-white shadow-sm">
          <div className="flex flex-col gap-4 border-b border-latus-warm-border p-5 md:flex-row md:items-center md:justify-between">
            <div><h2 className="text-xl font-extrabold text-latus-ink">Empresas y licencias</h2><p className="mt-1 text-sm text-latus-muted">Control global reservado al desarrollador de la plataforma.</p></div>
            <div className="flex flex-col sm:flex-row items-center gap-3 w-full md:w-auto">
              <label className="relative block w-full md:w-80"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-latus-muted" /><Input value={search} onChange={(event) => setSearch(event.target.value)} className="h-10 rounded-xl border-latus-warm-border pl-9 text-xs" placeholder="Buscar empresa o identificador" /></label>
              <Button onClick={() => setShowCreateModal(true)} data-testid="btn-create-company" className="h-10 rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 text-xs font-bold w-full sm:w-auto shrink-0"><Building2 className="h-4 w-4 mr-1.5" />+ Nueva Empresa</Button>
            </div>
          </div>
          {organizationsQ.isLoading ? (
            <div className="grid min-h-[260px] place-items-center"><div className="h-9 w-9 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" /></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1420px] text-left">
                <thead className="bg-latus-cream text-[11px] font-extrabold uppercase tracking-[0.12em] text-latus-muted"><tr><th className="px-5 py-3.5">Empresa</th><th className="px-4 py-3.5">Plan</th><th className="px-4 py-3.5">Suscripción</th><th className="px-4 py-3.5">Mercado Pago</th><th className="px-4 py-3.5">Licencia</th><th className="px-4 py-3.5">Cobro IA</th><th className="px-4 py-3.5">Uso</th><th className="px-4 py-3.5">IA este mes</th><th className="px-4 py-3.5">Acceso</th><th className="px-5 py-3.5 text-right">Acciones</th></tr></thead>
                <tbody className="divide-y divide-latus-warm-border">
                  {filtered.map((organization) => (
                    <tr key={organization.organization_id} className="hover:bg-latus-cream/50">
                      <td className="px-5 py-4"><p className="font-extrabold text-latus-ink">{organization.name}</p><p className="mt-1 font-mono text-[11px] text-latus-muted">{organization.organization_id}</p>{organization.latest_request?.status === "pending" && <p className="mt-2 text-xs font-bold text-amber-700">Solicitó {PLAN_NAMES[organization.latest_request.plan_code]}</p>}</td>
                      <td className="px-4 py-4 text-sm font-bold text-latus-ink">{PLAN_NAMES[organization.plan_code] || organization.plan_code}</td>
                      <td className="px-4 py-4 text-sm text-latus-muted">{SUBSCRIPTION_LABELS[organization.subscription_status] || organization.subscription_status}</td>
                      <td className="px-4 py-4 text-sm text-latus-muted">{PROVIDER_LABELS[organization.provider_status] || (organization.provider_status ? organization.provider_status : "Sin vincular")}</td>
                      <td className="px-4 py-4 text-sm text-latus-muted">{LICENSE_LABELS[organization.license_status] || organization.license_status}</td>
                      <td className="px-4 py-4"><span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-extrabold ${AI_BILLING_STATES[organization.ai_variable_billing?.state || "disabled"]?.tone}`}>{AI_BILLING_STATES[organization.ai_variable_billing?.state || "disabled"]?.label}</span>{organization.ai_variable_billing?.billing_start_date && <p className="mt-1 text-[10px] text-latus-muted">Desde {dateInput(organization.ai_variable_billing.billing_start_date)}</p>}</td>
                      <td className="px-4 py-4"><p className="flex items-center gap-1.5 text-sm font-bold text-latus-ink"><Users className="h-3.5 w-3.5 text-latus-blue" />{organization.active_users} usuarios</p><p className="mt-1 text-xs text-latus-muted">{organization.contacts} clientes</p></td>
                      <td className="px-4 py-4"><p className="text-sm font-extrabold text-latus-ink">USD {Number(organization.ai_billing?.this_month?.billable_cost_usd || 0).toFixed(2)}</p><p className="mt-1 text-xs text-latus-muted">Base USD {Number(organization.ai_billing?.this_month?.base_cost_usd || 0).toFixed(2)} · fee {organization.ai_billing?.fee_percent ?? 0}%</p>{(() => { const profitability = organization.ai_billing?.profitability || {}; const meta = PROFITABILITY_STATES[profitability.status] || PROFITABILITY_STATES.not_configured; return <span className={`mt-2 inline-flex rounded-full border px-2 py-0.5 text-[10px] font-extrabold ${meta.tone}`}>{meta.label}{profitability.ai_net_margin_percent != null ? ` · IA ${profitability.ai_net_margin_percent}%` : ""}</span>; })()}</td>
                      <td className="px-4 py-4"><span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-extrabold ${statusTone(organization.access?.allowed)}`}>{organization.access?.allowed ? "Habilitado" : "Bloqueado"}</span></td>
                      <td className="px-5 py-4 text-right">
                        <Button type="button" variant="outline" size="sm" onClick={() => setSimulatingOrg(organization)} className="rounded-lg border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100 text-xs font-bold mr-2">Simular</Button>
                        {organization.ai_variable_billing?.state === "pilot" && <Button type="button" variant="outline" size="sm" onClick={() => setPilotReviewOrg(organization)} className="mr-2 rounded-lg border-violet-300 bg-violet-50 text-violet-900 hover:bg-violet-100 text-xs font-bold">Revisar piloto</Button>}
                        <Button type="button" variant="outline" size="sm" onClick={() => setSelected(organization)} className="rounded-lg border-latus-warm-border"><Pencil className="h-3.5 w-3.5 mr-1" />Administrar</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length === 0 && <div className="grid min-h-[220px] place-items-center text-center"><div><KeyRound className="mx-auto h-8 w-8 text-latus-muted/50" /><p className="mt-3 font-bold text-latus-ink">No encontramos empresas</p><p className="mt-1 text-sm text-latus-muted">Probá con otra búsqueda.</p></div></div>}
            </div>
          )}
        </section>
      </div>
      {simulatingOrg && <SimulationModal organizationId={simulatingOrg.organization_id} orgName={simulatingOrg.name} onClose={() => setSimulatingOrg(null)} />}
      {pilotReviewOrg && <PilotApprovalModal organization={pilotReviewOrg} onClose={() => setPilotReviewOrg(null)} />}
      {showCreateModal && <CreateCompanyModal onClose={() => setShowCreateModal(false)} creating={createOrganization.isPending} onCreate={(body) => createOrganization.mutate(body)} />}
      {selected && <ManageModal organization={selected} onClose={() => setSelected(null)} saving={updateSubscription.isPending} onSave={(body) => updateSubscription.mutate({ organizationId: selected.organization_id, body })} />}
    </AppLayout>
  );
}
