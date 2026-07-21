import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeDollarSign, Building2, CheckCircle2, KeyRound, Pencil, Search, ShieldAlert,
  Sparkles, Users, X,
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

export default function Plataforma() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const organizationsQ = useQuery({
    queryKey: ["platform-organizations"],
    queryFn: () => api.get("/platform/organizations").then((response) => response.data),
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

  return (
    <AppLayout title="Plataforma">
      <div className="mx-auto w-full max-w-[1500px] space-y-6 p-4 sm:p-6 lg:p-8" data-testid="platform-page">
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

        <section className="overflow-hidden rounded-[24px] border border-latus-warm-border bg-white shadow-sm">
          <div className="flex flex-col gap-4 border-b border-latus-warm-border p-5 md:flex-row md:items-center md:justify-between">
            <div><h2 className="text-xl font-extrabold text-latus-ink">Empresas y licencias</h2><p className="mt-1 text-sm text-latus-muted">Control global reservado al desarrollador de la plataforma.</p></div>
            <label className="relative block w-full md:max-w-sm"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-latus-muted" /><Input value={search} onChange={(event) => setSearch(event.target.value)} className="h-10 rounded-xl border-latus-warm-border pl-9" placeholder="Buscar empresa o identificador" /></label>
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
                      <td className="px-5 py-4 text-right"><Button type="button" variant="outline" size="sm" onClick={() => setSelected(organization)} className="rounded-lg border-latus-warm-border"><Pencil className="h-3.5 w-3.5" />Administrar</Button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length === 0 && <div className="grid min-h-[220px] place-items-center text-center"><div><KeyRound className="mx-auto h-8 w-8 text-latus-muted/50" /><p className="mt-3 font-bold text-latus-ink">No encontramos empresas</p><p className="mt-1 text-sm text-latus-muted">Probá con otra búsqueda.</p></div></div>}
            </div>
          )}
        </section>
      </div>
      {selected && <ManageModal organization={selected} onClose={() => setSelected(null)} saving={updateSubscription.isPending} onSave={(body) => updateSubscription.mutate({ organizationId: selected.organization_id, body })} />}
    </AppLayout>
  );
}
