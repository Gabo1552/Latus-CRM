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
  if (!draft) return null;
  const invalid = Number(draft.usd_to_ars_rate) <= 0 || Number(draft.fx_buffer_percent) < 0
    || Number(draft.settlement_lead_hours) < 1 || Number(draft.max_rate_age_hours) < 12;
  const rows = statementsQ.data?.items || [];
  return (
    <section className="overflow-hidden rounded-[24px] border border-latus-warm-border bg-white shadow-sm" data-testid="ai-variable-billing-panel">
      <div className="flex flex-col gap-4 border-b border-latus-warm-border bg-latus-cream/35 p-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div className="flex items-start gap-3"><span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-violet-100 text-violet-700"><BadgeDollarSign className="h-5 w-5" /></span><div><h2 className="font-extrabold text-latus-ink">Cobro automático del consumo de IA</h2><p className="mt-1 max-w-3xl text-sm leading-5 text-latus-muted">Antes de cada renovación suma al plan el consumo cerrado, convertido a pesos con una cotización registrada. Cada liquidación queda congelada para auditoría.</p></div></div>
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
      <div className="flex flex-col gap-3 border-t border-latus-warm-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div><p className={`text-xs font-extrabold ${draft.rate_is_fresh ? "text-emerald-700" : "text-amber-700"}`}>{draft.rate_is_fresh ? "Cotización vigente" : "Cotización vencida o no configurada"}</p><p className="mt-1 text-[11px] text-latus-muted">Observada: {draft.exchange_rate_observed_at || "—"} · actualizada: {draft.exchange_rate_updated_at ? new Date(draft.exchange_rate_updated_at).toLocaleString("es-AR") : "—"}</p></div>
        <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => refreshRate.mutate()} disabled={refreshRate.isPending} className="rounded-lg border-latus-warm-border"><RefreshCw className={`h-4 w-4 ${refreshRate.isPending ? "animate-spin" : ""}`} />Actualizar desde BCRA</Button><Button variant="outline" onClick={() => run.mutate()} disabled={!draft.enabled || run.isPending} className="rounded-lg border-latus-warm-border"><FileText className="h-4 w-4" />Procesar vencimientos</Button><Button onClick={() => save.mutate()} disabled={invalid || save.isPending} className="rounded-lg bg-latus-blue text-white hover:bg-latus-blue/90">Guardar política</Button></div>
      </div>
      {rows.length > 0 && <div className="overflow-x-auto border-t border-latus-warm-border"><table className="w-full min-w-[900px] text-left text-xs"><thead className="bg-latus-cream text-[10px] font-extrabold uppercase tracking-wider text-latus-muted"><tr><th className="px-5 py-3">Empresa</th><th className="px-4 py-3">Período</th><th className="px-4 py-3 text-right">IA USD</th><th className="px-4 py-3 text-right">IA ARS</th><th className="px-4 py-3 text-right">Plan + IA</th><th className="px-5 py-3">Estado</th></tr></thead><tbody className="divide-y divide-latus-warm-border">{rows.map((row) => <tr key={row.statement_id}><td className="px-5 py-3 font-mono">{row.organization_id}</td><td className="px-4 py-3 text-latus-muted">{String(row.period_start).slice(0, 10)} → {String(row.period_end).slice(0, 10)}</td><td className="px-4 py-3 text-right font-mono">USD {Number(row.billable_cost_usd || 0).toFixed(4)}</td><td className="px-4 py-3 text-right font-mono">$ {Number(row.ai_amount_ars || 0).toLocaleString("es-AR")}</td><td className="px-4 py-3 text-right font-extrabold">$ {Number(row.total_amount_ars || 0).toLocaleString("es-AR")}</td><td className="px-5 py-3"><span className="rounded-full bg-slate-100 px-2.5 py-1 font-extrabold text-slate-700">{SETTLEMENT_STATUS[row.status] || row.status}</span></td></tr>)}</tbody></table></div>}
    </section>
  );
}

function ManageModal({ organization, onClose, onSave, saving }) {
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
        </div>
        <div className="flex justify-end gap-3 border-t border-latus-warm-border bg-white px-6 py-4">
          <Button type="button" variant="outline" onClick={onClose}>Cancelar</Button>
          <Button type="button" disabled={saving} onClick={() => onSave({ ...draft, ai_fee_percent: draft.ai_fee_percent === "" ? null : Number(draft.ai_fee_percent) })} className="bg-latus-blue text-white hover:bg-latus-blue/90">{saving ? "Guardando..." : "Guardar licencia"}</Button>
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
  const simulationQ = useQuery({
    queryKey: ["ai-simulation", organizationId],
    queryFn: () => api.post("/platform/ai-billing/simulate", { organization_id: organizationId }).then((r) => r.data),
  });

  const sim = simulationQ.data;

  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-latus-ink/55 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-[24px] border border-white/10 bg-latus-cream shadow-2xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-latus-warm-border bg-white px-6 py-5">
          <div>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-3 py-0.5 text-xs font-black text-amber-800">
              ⚡ MODO SIMULACIÓN DE PRUEBA
            </span>
            <h2 className="mt-1 text-xl font-extrabold text-latus-ink">Simulación de Liquidación - {orgName}</h2>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-latus-muted hover:bg-latus-cream"><X className="h-5 w-5" /></button>
        </div>

        <div className="p-6 space-y-5">
          <div className="rounded-xl border border-amber-200 bg-amber-50/80 p-4 text-xs text-amber-900 leading-relaxed">
            <strong>💡 Nota de Simulación:</strong> Este cálculo previo muestra el período facturado, consumo de IA, costo del proveedor, fee de Latus y cotización oficial sin realizar ningún cambio ni cargo en Mercado Pago.
          </div>

          {simulationQ.isLoading ? (
            <div className="grid min-h-[200px] place-items-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" /></div>
          ) : sim ? (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-latus-warm-border bg-white p-4">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Período Facturado</p>
                  <p className="mt-1 text-sm font-extrabold text-latus-ink">{sim.period_start?.slice(0, 10)} → {sim.period_end?.slice(0, 10)}</p>
                </div>
                <div className="rounded-xl border border-latus-warm-border bg-white p-4">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Consumo Consolidado</p>
                  <p className="mt-1 text-sm font-extrabold text-latus-ink">{sim.usage_summary?.calls || 0} llamadas ({Number(sim.usage_summary?.total_tokens || 0).toLocaleString()} tokens)</p>
                </div>
                <div className="rounded-xl border border-latus-warm-border bg-white p-4">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-latus-muted">Cotización USD → ARS</p>
                  <p className="mt-1 text-sm font-extrabold text-latus-ink">$ {Number(sim.usd_to_ars_rate || 0).toLocaleString("es-AR")} <span className="text-xs text-latus-muted">(+{sim.fx_buffer_percent}% colchón)</span></p>
                </div>
              </div>

              <div className="rounded-xl border border-latus-warm-border bg-white overflow-hidden text-xs">
                <div className="border-b border-latus-warm-border bg-latus-cream/40 px-4 py-3 font-extrabold text-latus-ink">Desglose Financiero Estimado</div>
                <div className="divide-y divide-latus-warm-border p-4 space-y-2.5">
                  <div className="flex justify-between">
                    <span className="text-latus-muted">Precio del Plan Comercial ({sim.plan_name}):</span>
                    <span className="font-extrabold text-latus-ink">$ {Number(sim.plan_amount_ars || 0).toLocaleString("es-AR")} ARS</span>
                  </div>
                  <div className="flex justify-between pt-2">
                    <span className="text-latus-muted">Costo Base del Proveedor (USD):</span>
                    <span className="font-mono text-slate-600">USD ${Number(sim.usage_summary?.base_cost_usd || 0).toFixed(4)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-latus-muted">Fee Latus (%) Ganancia Bruta:</span>
                    <span className="font-mono text-emerald-700 font-bold">+ USD ${Number(sim.usage_summary?.ai_fee_usd || 0).toFixed(4)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-latus-muted">Total Excedente IA (USD Facturable):</span>
                    <span className="font-mono font-bold text-latus-ink">USD ${Number(sim.billable_cost_usd || 0).toFixed(4)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-latus-muted">Total Excedente IA Convertido a ARS:</span>
                    <span className="font-mono font-extrabold text-violet-700">$ {Number(sim.ai_amount_ars || 0).toLocaleString("es-AR")} ARS</span>
                  </div>
                  <div className="flex justify-between pt-3 border-t border-latus-warm-border text-sm font-black">
                    <span className="text-latus-ink">Total a Recibir del Cliente (Plan + IA):</span>
                    <span className="text-latus-blue text-base">$ {Number(sim.total_amount_ars || 0).toLocaleString("es-AR")} ARS</span>
                  </div>
                  <div className="flex justify-between pt-1 text-xs text-emerald-700 font-bold">
                    <span>Ganancia Neta Estimada de Latus (en esta liquidación):</span>
                    <span>$ {Number(sim.estimated_latus_net_profit_ars || 0).toLocaleString("es-AR")} ARS</span>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-rose-600 font-bold">No se pudo obtener la simulación de liquidación.</p>
          )}
        </div>

        <div className="flex justify-end border-t border-latus-warm-border bg-white px-6 py-4 rounded-b-[24px]">
          <Button type="button" onClick={onClose} className="bg-latus-ink text-white hover:bg-latus-ink/90">Cerrar Simulación</Button>
        </div>
      </div>
    </div>
  );
}

function SuperadminExecutiveDashboardPanel({ onExportCSV }) {
  const dashQ = useQuery({
    queryKey: ["platform-financial-dashboard"],
    queryFn: () => api.get("/platform/financial-dashboard").then((r) => r.data),
  });

  const d = dashQ.data?.summary;
  if (!d) return null;

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
          <div className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-right">
            <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Cotización BCRA Aplicada</p>
            <p className="font-mono text-sm font-extrabold text-emerald-400">$ {Number(d.usd_to_ars_rate || 0).toLocaleString("es-AR")} <span className="text-xs text-slate-300">(+{d.fx_buffer_percent}% buffer)</span></p>
          </div>
          <Button onClick={onExportCSV} variant="outline" className="rounded-xl border-white/20 bg-white/10 text-white hover:bg-white/20 text-xs font-bold">Exportar Registro CSV</Button>
        </div>
      </div>

      <div className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-4 sm:p-6 bg-latus-cream/20">
        <div className="rounded-2xl border border-latus-warm-border bg-white p-5 shadow-sm">
          <p className="text-xs font-bold uppercase tracking-wider text-latus-muted">Facturación Bruta Estimada Mes</p>
          <p className="mt-2 text-3xl font-black tracking-tight text-latus-ink">$ {Number(d.total_revenue_ars || 0).toLocaleString("es-AR")} <span className="text-xs text-latus-muted font-normal">ARS</span></p>
          <p className="mt-1 text-[11px] text-latus-muted">Planes $ {Number(d.monthly_subscriptions_ars || 0).toLocaleString("es-AR")} + IA $ {Number(d.monthly_ai_billable_ars || 0).toLocaleString("es-AR")}</p>
        </div>

        <div className="rounded-2xl border border-emerald-200 bg-emerald-50/50 p-5 shadow-sm">
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-800">Ganancia Neta Estimada Latus</p>
          <p className="mt-2 text-3xl font-black tracking-tight text-emerald-700">$ {Number(d.estimated_net_profit_ars || 0).toLocaleString("es-AR")} <span className="text-xs font-normal">ARS</span></p>
          <p className="mt-1 text-[11px] font-bold text-emerald-700">Margen Neto Global: {d.net_margin_percent}%</p>
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
    </section>
  );
}

export default function Plataforma() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [simulatingOrg, setSimulatingOrg] = useState(null);
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
    mutationFn: ({ organizationId, body }) => api.patch(`/platform/organizations/${organizationId}/subscription`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["platform-organizations"] });
      setSelected(null);
      toast.success("Licencia actualizada");
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo actualizar la licencia"),
  });
  const organizations = useMemo(() => organizationsQ.data || [], [organizationsQ.data]);
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return term ? organizations.filter((item) => `${item.name} ${item.organization_id} ${item.billing_email || ""}`.toLowerCase().includes(term)) : organizations;
  }, [organizations, search]);
  const active = organizations.filter((item) => item.access?.allowed).length;
  const pending = organizations.filter((item) => item.latest_request?.status === "pending").length;
  const aiBillable = organizations.reduce((total, item) => total + Number(item.ai_billing?.this_month?.billable_cost_usd || 0), 0);

  const handleExportCSV = () => {
    window.open("/api/billing/statements/export", "_blank");
  };

  return (
    <AppLayout title="Plataforma">
      <div className="mx-auto w-full max-w-[1500px] space-y-6 p-4 sm:p-6 lg:p-8" data-testid="platform-page">
        <SuperadminExecutiveDashboardPanel onExportCSV={handleExportCSV} />

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
              <table className="w-full min-w-[1280px] text-left">
                <thead className="bg-latus-cream text-[11px] font-extrabold uppercase tracking-[0.12em] text-latus-muted"><tr><th className="px-5 py-3.5">Empresa</th><th className="px-4 py-3.5">Plan</th><th className="px-4 py-3.5">Suscripción</th><th className="px-4 py-3.5">Mercado Pago</th><th className="px-4 py-3.5">Licencia</th><th className="px-4 py-3.5">Uso</th><th className="px-4 py-3.5">IA este mes</th><th className="px-4 py-3.5">Acceso</th><th className="px-5 py-3.5 text-right">Acciones</th></tr></thead>
                <tbody className="divide-y divide-latus-warm-border">
                  {filtered.map((organization) => (
                    <tr key={organization.organization_id} className="hover:bg-latus-cream/50">
                      <td className="px-5 py-4"><p className="font-extrabold text-latus-ink">{organization.name}</p><p className="mt-1 font-mono text-[11px] text-latus-muted">{organization.organization_id}</p>{organization.latest_request?.status === "pending" && <p className="mt-2 text-xs font-bold text-amber-700">Solicitó {PLAN_NAMES[organization.latest_request.plan_code]}</p>}</td>
                      <td className="px-4 py-4 text-sm font-bold text-latus-ink">{PLAN_NAMES[organization.plan_code] || organization.plan_code}</td>
                      <td className="px-4 py-4 text-sm text-latus-muted">{SUBSCRIPTION_LABELS[organization.subscription_status] || organization.subscription_status}</td>
                      <td className="px-4 py-4 text-sm text-latus-muted">{PROVIDER_LABELS[organization.provider_status] || (organization.provider_status ? organization.provider_status : "Sin vincular")}</td>
                      <td className="px-4 py-4 text-sm text-latus-muted">{LICENSE_LABELS[organization.license_status] || organization.license_status}</td>
                      <td className="px-4 py-4"><p className="flex items-center gap-1.5 text-sm font-bold text-latus-ink"><Users className="h-3.5 w-3.5 text-latus-blue" />{organization.active_users} usuarios</p><p className="mt-1 text-xs text-latus-muted">{organization.contacts} clientes</p></td>
                      <td className="px-4 py-4"><p className="text-sm font-extrabold text-latus-ink">USD {Number(organization.ai_billing?.this_month?.billable_cost_usd || 0).toFixed(2)}</p><p className="mt-1 text-xs text-latus-muted">Base USD {Number(organization.ai_billing?.this_month?.base_cost_usd || 0).toFixed(2)} · fee {organization.ai_billing?.fee_percent ?? 0}%</p></td>
                      <td className="px-4 py-4"><span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-extrabold ${statusTone(organization.access?.allowed)}`}>{organization.access?.allowed ? "Habilitado" : "Bloqueado"}</span></td>
                      <td className="px-5 py-4 text-right">
                        <Button type="button" variant="outline" size="sm" onClick={() => setSimulatingOrg(organization)} className="rounded-lg border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100 text-xs font-bold mr-2">Simular</Button>
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
      {showCreateModal && <CreateCompanyModal onClose={() => setShowCreateModal(false)} creating={createOrganization.isPending} onCreate={(body) => createOrganization.mutate(body)} />}
      {selected && <ManageModal organization={selected} onClose={() => setSelected(null)} saving={updateSubscription.isPending} onSave={(body) => updateSubscription.mutate({ organizationId: selected.organization_id, body })} />}
    </AppLayout>
  );
}
