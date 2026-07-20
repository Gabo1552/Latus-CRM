import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight, Building2, Check, Clock3, CreditCard, ShieldCheck,
  Sparkles, Users, UserRoundCheck,
} from "lucide-react";
import { toast } from "sonner";
import AppLayout from "@/components/AppLayout";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { hasPermission } from "@/lib/permissions";

const STATUS = {
  not_configured: { label: "Acceso heredado", tone: "bg-slate-100 text-slate-700" },
  trialing: { label: "Período de prueba", tone: "bg-sky-100 text-sky-700" },
  active: { label: "Activa", tone: "bg-emerald-100 text-emerald-700" },
  past_due: { label: "Pago pendiente", tone: "bg-amber-100 text-amber-800" },
  canceled: { label: "Cancelada", tone: "bg-slate-100 text-slate-700" },
  suspended: { label: "Suspendida", tone: "bg-rose-100 text-rose-700" },
};

const money = new Intl.NumberFormat("es-AR", {
  style: "currency", currency: "ARS", maximumFractionDigits: 0,
});

function formatDate(value) {
  if (!value) return "Sin fecha definida";
  return new Intl.DateTimeFormat("es-AR", {
    day: "2-digit", month: "long", year: "numeric",
  }).format(new Date(value));
}

function UsageBar({ icon: Icon, label, value, limit }) {
  const unlimited = !limit || limit < 0;
  const percent = unlimited ? 0 : Math.min(100, Math.round((value / Math.max(1, limit)) * 100));
  return (
    <div className="rounded-2xl border border-latus-warm-border bg-white p-5">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-latus-blue/10 text-latus-blue"><Icon className="h-5 w-5" /></span>
          <div>
            <p className="text-sm font-bold text-latus-ink">{label}</p>
            <p className="text-xs text-latus-muted">{value.toLocaleString("es-AR")} usados</p>
          </div>
        </div>
        <p className="text-sm font-extrabold text-latus-ink">{unlimited ? "Sin límite" : `de ${limit.toLocaleString("es-AR")}`}</p>
      </div>
      {!unlimited && (
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-latus-cream">
          <div className={`h-full rounded-full ${percent >= 90 ? "bg-latus-coral" : "bg-latus-blue"}`} style={{ width: `${percent}%` }} />
        </div>
      )}
    </div>
  );
}

export default function Suscripcion() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [notes, setNotes] = useState("");
  const canManage = user?.role === "admin" || hasPermission(user, "settings_admin");
  const subscriptionQ = useQuery({
    queryKey: ["billing-subscription", user?.organization_id],
    queryFn: () => api.get("/billing/subscription").then((response) => response.data),
  });
  const plansQ = useQuery({
    queryKey: ["billing-plans"],
    queryFn: () => api.get("/billing/plans").then((response) => response.data),
  });
  const requestPlan = useMutation({
    mutationFn: (planCode) => api.post("/billing/plan-requests", { plan_code: planCode, notes }),
    onSuccess: (_, planCode) => {
      qc.invalidateQueries({ queryKey: ["billing-subscription"] });
      toast.success(`Solicitud enviada para el plan ${plansQ.data?.find((plan) => plan.code === planCode)?.name || "seleccionado"}`);
      setNotes("");
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo enviar la solicitud"),
  });

  const data = subscriptionQ.data;
  const status = STATUS[data?.organization?.subscription_status] || STATUS.not_configured;
  const limits = data?.plan?.limits || {};
  const relevantDate = useMemo(() => {
    if (!data?.organization) return null;
    if (data.organization.subscription_status === "trialing") return data.organization.trial_ends_at;
    if (data.organization.subscription_status === "past_due") return data.organization.grace_ends_at;
    return data.organization.current_period_end;
  }, [data]);

  if (subscriptionQ.isLoading || plansQ.isLoading) {
    return <AppLayout title="Suscripción"><div className="grid min-h-[55vh] place-items-center"><div className="h-10 w-10 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" /></div></AppLayout>;
  }

  return (
    <AppLayout title="Suscripción">
      <div className="mx-auto w-full max-w-[1440px] space-y-6 p-4 sm:p-6 lg:p-8" data-testid="subscription-page">
        <section className="relative overflow-hidden rounded-[28px] bg-latus-ink px-6 py-7 text-white shadow-[0_20px_55px_rgba(11,27,38,0.18)] md:px-9 md:py-9">
          <div className="absolute -right-20 -top-24 h-64 w-64 rounded-full bg-latus-blue/25 blur-3xl" />
          <div className="relative grid gap-7 lg:grid-cols-[1fr_auto] lg:items-end">
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <span className={`rounded-full px-3 py-1 text-xs font-extrabold uppercase tracking-wide ${status.tone}`}>{status.label}</span>
                <span className="text-sm font-semibold text-white/60">{data?.organization?.name}</span>
              </div>
              <p className="mt-5 text-sm font-bold uppercase tracking-[0.16em] text-latus-blue">Plan actual</p>
              <h2 className="mt-1 text-4xl font-extrabold tracking-tight md:text-5xl">{data?.plan?.name || "Base"}</h2>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">{data?.plan?.description}</p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.06] p-5 lg:min-w-[280px]">
              <div className="flex items-center gap-2 text-white/65"><Clock3 className="h-4 w-4" /><span className="text-xs font-bold uppercase tracking-wider">Próxima fecha</span></div>
              <p className="mt-2 text-lg font-extrabold">{formatDate(relevantDate)}</p>
              <p className="mt-1 text-xs text-white/50">El acceso actual está {data?.access?.allowed ? "habilitado" : "pendiente de regularización"}.</p>
            </div>
          </div>
        </section>

        {!data?.access?.allowed && (
          <section className="flex flex-col gap-4 rounded-2xl border border-rose-200 bg-rose-50 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex gap-3"><ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" /><div><p className="font-extrabold text-rose-900">La licencia requiere atención</p><p className="mt-1 text-sm text-rose-700">Elegí un plan o contactá al administrador de Latus para reactivar todos los módulos.</p></div></div>
          </section>
        )}

        <section>
          <div className="mb-4 flex items-end justify-between gap-4">
            <div><p className="text-xs font-extrabold uppercase tracking-[0.15em] text-latus-blue">Capacidad</p><h3 className="mt-1 text-2xl font-extrabold text-latus-ink">Uso de tu empresa</h3></div>
            <p className="hidden text-sm text-latus-muted md:block">Los límites se actualizan al cambiar de plan.</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <UsageBar icon={Users} label="Usuarios del equipo" value={data?.usage?.users || 0} limit={limits.users} />
            <UsageBar icon={UserRoundCheck} label="Clientes registrados" value={data?.usage?.contacts || 0} limit={limits.contacts} />
          </div>
        </section>

        <section>
          <div className="mb-4"><p className="text-xs font-extrabold uppercase tracking-[0.15em] text-latus-blue">Planes</p><h3 className="mt-1 text-2xl font-extrabold text-latus-ink">Elegí cómo querés crecer</h3></div>
          <div className="grid gap-5 lg:grid-cols-3">
            {(plansQ.data || []).filter((plan) => plan.is_public).map((plan) => {
              const current = plan.code === data?.organization?.plan_code;
              const pending = plan.code === data?.latest_request?.plan_code && data?.latest_request?.status === "pending";
              return (
                <article key={plan.code} className={`relative flex min-h-[410px] flex-col rounded-[24px] border bg-white p-6 shadow-sm ${plan.highlighted ? "border-latus-blue ring-1 ring-latus-blue" : "border-latus-warm-border"}`}>
                  {plan.highlighted && <span className="absolute right-5 top-5 rounded-full bg-latus-blue px-3 py-1 text-[10px] font-extrabold uppercase tracking-wider text-white">Recomendado</span>}
                  <span className="grid h-11 w-11 place-items-center rounded-xl bg-latus-cream text-latus-blue"><Sparkles className="h-5 w-5" /></span>
                  <h4 className="mt-5 text-2xl font-extrabold text-latus-ink">{plan.name}</h4>
                  <p className="mt-2 min-h-[44px] text-sm leading-5 text-latus-muted">{plan.description}</p>
                  <p className="mt-5 text-3xl font-black tracking-tight text-latus-ink">{money.format(plan.monthly_price_ars)}<span className="ml-1 text-sm font-semibold text-latus-muted">/ mes</span></p>
                  <ul className="mt-6 flex-1 space-y-3">
                    {plan.features.map((feature) => <li key={feature} className="flex gap-2 text-sm text-latus-ink"><Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" /><span>{feature}</span></li>)}
                  </ul>
                  <Button
                    type="button"
                    disabled={!canManage || current || pending || requestPlan.isPending}
                    onClick={() => requestPlan.mutate(plan.code)}
                    className={`mt-6 h-11 w-full rounded-xl font-extrabold ${plan.highlighted ? "bg-latus-blue text-white hover:bg-latus-blue/90" : "bg-latus-ink text-white hover:bg-latus-ink/90"}`}
                  >
                    {current ? "Plan actual" : pending ? "Solicitud pendiente" : <>Elegir plan <ArrowRight className="h-4 w-4" /></>}
                  </Button>
                </article>
              );
            })}
          </div>
        </section>

        {canManage && (
          <section className="grid gap-5 rounded-[24px] border border-latus-warm-border bg-white p-6 md:grid-cols-[1fr_auto] md:items-center">
            <div className="flex gap-4"><span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-latus-blue/10 text-latus-blue"><CreditCard className="h-5 w-5" /></span><div><h3 className="font-extrabold text-latus-ink">Facturación y pago online</h3><p className="mt-1 max-w-2xl text-sm leading-5 text-latus-muted">La selección de plan ya queda registrada. En la próxima etapa conectaremos Mercado Pago para cobrar, renovar y conciliar automáticamente.</p></div></div>
            <div className="flex items-center gap-2 rounded-xl bg-latus-cream px-4 py-3 text-xs font-bold text-latus-muted"><Building2 className="h-4 w-4" />Administrado por empresa</div>
          </section>
        )}
      </div>
    </AppLayout>
  );
}
