import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight, BadgeDollarSign, Building2, Check, CheckCircle2, Clock3, CreditCard, Download,
  ReceiptText, RefreshCw, ShieldCheck, Sparkles, Users, UserRoundCheck, XCircle,
} from "lucide-react";
import { toast } from "sonner";
import AppLayout from "@/components/AppLayout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { hasPermission } from "@/lib/permissions";
import {
  billingStatementStatus, downloadBillingStatements, openBillingStatement,
} from "@/lib/billingDocuments";

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
const usd = new Intl.NumberFormat("es-AR", {
  style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4,
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
  const [billingEmail, setBillingEmail] = useState(user?.email || "");
  const [exportingStatements, setExportingStatements] = useState(false);
  const [openingStatementId, setOpeningStatementId] = useState(null);
  const returnHandled = useRef(false);
  const canManage = user?.role === "admin" || hasPermission(user, "settings_admin");
  const subscriptionQ = useQuery({
    queryKey: ["billing-subscription", user?.organization_id],
    queryFn: () => api.get("/billing/subscription").then((response) => response.data),
  });
  const plansQ = useQuery({
    queryKey: ["billing-plans"],
    queryFn: () => api.get("/billing/plans").then((response) => response.data),
  });
  const statementsQ = useQuery({
    queryKey: ["billing-statements", user?.organization_id],
    queryFn: () => api.get("/billing/statements?limit=24").then((response) => response.data),
    enabled: !!user?.organization_id,
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
  const startCheckout = useMutation({
    mutationFn: (planCode) => api.post("/billing/checkout", {
      plan_code: planCode,
      billing_email: billingEmail,
    }).then((response) => response.data),
    onSuccess: (result) => {
      if (result.checkout_url) {
        window.location.assign(result.checkout_url);
        return;
      }
      qc.invalidateQueries({ queryKey: ["billing-subscription"] });
      toast.success(result.plan_updated ? "Plan actualizado en Mercado Pago" : "La suscripción ya está activa");
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo iniciar el pago"),
  });
  const reconcile = useMutation({
    mutationFn: () => api.post("/billing/reconcile").then((response) => response.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["billing-subscription"] });
      window.history.replaceState({}, "", window.location.pathname);
      toast.success("Estado de la suscripción actualizado");
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo actualizar el estado"),
  });
  const cancelSubscription = useMutation({
    mutationFn: () => api.post("/billing/cancel").then((response) => response.data),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["billing-subscription"] });
      toast.success(
        result?.organization?.cancel_at_period_end
          ? "La cancelación quedó programada para el cierre del período"
          : "La renovación automática fue cancelada",
      );
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo cancelar la suscripción"),
  });

  const exportStatements = async () => {
    try {
      setExportingStatements(true);
      await downloadBillingStatements("/billing/statements/export");
      toast.success("Historial de liquidaciones descargado");
    } catch (error) {
      toast.error(error.response?.data?.detail || "No se pudo descargar el historial");
    } finally {
      setExportingStatements(false);
    }
  };

  const openStatement = async (statementId) => {
    try {
      setOpeningStatementId(statementId);
      await openBillingStatement(statementId);
    } catch (error) {
      toast.error(error.response?.data?.detail || "No se pudo abrir el detalle");
    } finally {
      setOpeningStatementId(null);
    }
  };

  const data = subscriptionQ.data;
  const statements = statementsQ.data?.items || [];
  const latestAIStatement = data?.ai_billing?.latest_statement;
  const status = STATUS[data?.organization?.subscription_status] || STATUS.not_configured;
  const limits = data?.plan?.limits || {};
  const relevantDate = useMemo(() => {
    if (!data?.organization) return null;
    if (data.organization.subscription_status === "trialing") return data.organization.trial_ends_at;
    if (data.organization.subscription_status === "past_due") return data.organization.grace_ends_at;
    return data.organization.current_period_end;
  }, [data]);

  useEffect(() => {
    if (data?.organization?.billing_email && billingEmail === user?.email) {
      setBillingEmail(data.organization.billing_email);
    }
  }, [data?.organization?.billing_email, billingEmail, user?.email]);

  useEffect(() => {
    const returnedFromCheckout = new URLSearchParams(window.location.search).get("checkout") === "retorno";
    if (
      returnedFromCheckout && canManage && data?.payment_provider?.status
      && !returnHandled.current && !reconcile.isPending
    ) {
      returnHandled.current = true;
      reconcile.mutate();
    }
  }, [canManage, data?.payment_provider?.status, reconcile]);

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
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <UsageBar icon={Users} label="Usuarios del equipo" value={data?.usage?.users || 0} limit={limits.users} />
            <UsageBar icon={UserRoundCheck} label="Clientes registrados" value={data?.usage?.contacts || 0} limit={limits.contacts} />
            <UsageBar icon={Sparkles} label="Tokens de IA este mes" value={data?.ai_billing?.this_month?.tokens || 0} limit={limits.monthly_ai_tokens} />
          </div>
          <p className="mt-3 text-xs font-semibold text-latus-muted">Al alcanzar el cupo operativo mensual del plan, el bot pausa nuevas llamadas de IA para mantener el consumo bajo control.</p>
        </section>

        <section className="overflow-hidden rounded-[24px] border border-latus-warm-border bg-white shadow-sm">
          <div className="flex flex-col gap-3 border-b border-latus-warm-border bg-latus-cream/35 p-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl bg-violet-50 text-violet-700"><BadgeDollarSign className="h-5 w-5" /></span><div><p className="font-extrabold text-latus-ink">Consumo variable de IA del mes</p><p className="mt-0.5 text-xs text-latus-muted">Separado del valor fijo de tu plan y calculado uso por uso.</p></div></div>
            <p className="text-xs font-bold text-latus-muted">{Number(data?.ai_billing?.this_month?.calls || 0).toLocaleString("es-AR")} llamadas · {Number(data?.ai_billing?.this_month?.tokens || 0).toLocaleString("es-AR")} tokens</p>
          </div>
          <div className="grid gap-px bg-latus-warm-border md:grid-cols-3">
            <div className="bg-white p-5"><p className="text-[11px] font-extrabold uppercase tracking-wider text-latus-muted">Costo del proveedor</p><p className="mt-2 text-2xl font-black text-latus-ink">{usd.format(Number(data?.ai_billing?.this_month?.base_cost_usd || 0))}</p><p className="mt-1 text-xs text-latus-muted">Costo base registrado al realizar cada llamada.</p></div>
            <div className="bg-white p-5"><p className="text-[11px] font-extrabold uppercase tracking-wider text-latus-muted">Fee de Latus · {Number(data?.ai_billing?.fee_percent || 0).toFixed(1)}%</p><p className="mt-2 text-2xl font-black text-latus-ink">{usd.format(Number(data?.ai_billing?.this_month?.ai_fee_usd || 0))}</p><p className="mt-1 text-xs text-latus-muted">El porcentaje histórico queda congelado con cada uso.</p></div>
            <div className="bg-latus-ink p-5 text-white"><p className="text-[11px] font-extrabold uppercase tracking-wider text-white/55">Total facturable de IA</p><p className="mt-2 text-3xl font-black text-white">{usd.format(Number(data?.ai_billing?.this_month?.billable_cost_usd || 0))}</p><p className="mt-1 text-xs text-white/55">Proveedor + fee acumulados durante el mes actual.</p></div>
          </div>
          {latestAIStatement && (
            <div className="flex flex-col gap-2 border-t border-latus-warm-border bg-sky-50/60 px-5 py-4 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-6">
              <div><p className="font-extrabold text-latus-ink">Última liquidación: {latestAIStatement.status === "paid" ? "cobrada" : latestAIStatement.status === "applied" ? "incluida en la próxima renovación" : latestAIStatement.status === "payment_failed" ? "pago pendiente" : "en preparación"}</p><p className="mt-1 text-xs text-latus-muted">Consumo IA {usd.format(Number(latestAIStatement.billable_cost_usd || 0))} · convertido a ARS con cotización {Number(latestAIStatement.usd_to_ars_rate || 0).toLocaleString("es-AR")} y colchón de {Number(latestAIStatement.fx_buffer_percent || 0).toFixed(1)}%.</p></div>
              <p className="text-lg font-black text-latus-ink">Próximo total: {money.format(Number(latestAIStatement.total_amount_ars || 0))}</p>
            </div>
          )}
        </section>

        <section className="overflow-hidden rounded-[24px] border border-latus-warm-border bg-white shadow-sm" data-testid="billing-statements-history">
          <div className="flex flex-col gap-3 border-b border-latus-warm-border bg-latus-cream/35 p-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-sky-50 text-sky-700"><ReceiptText className="h-5 w-5" /></span>
              <div><p className="font-extrabold text-latus-ink">Historial de liquidaciones</p><p className="mt-0.5 text-xs text-latus-muted">Importes congelados, estados de cobro y detalle del cálculo de IA.</p></div>
            </div>
            <Button type="button" variant="outline" onClick={exportStatements} disabled={exportingStatements || !statements.length} className="rounded-lg border-latus-warm-border bg-white">
              <Download className="h-4 w-4" />{exportingStatements ? "Preparando..." : "Descargar CSV"}
            </Button>
          </div>
          {statementsQ.isLoading ? (
            <div className="grid min-h-[150px] place-items-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" /></div>
          ) : statements.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[880px] text-left text-xs">
                <thead className="bg-white text-[10px] font-extrabold uppercase tracking-wider text-latus-muted"><tr><th className="px-5 py-3">Período</th><th className="px-4 py-3">Estado</th><th className="px-4 py-3 text-right">Consumo IA</th><th className="px-4 py-3 text-right">Plan</th><th className="px-4 py-3 text-right">Total</th><th className="px-5 py-3 text-right">Documento</th></tr></thead>
                <tbody className="divide-y divide-latus-warm-border">
                  {statements.map((statement) => {
                    const meta = billingStatementStatus(statement.status);
                    return (
                      <tr key={statement.statement_id} className="hover:bg-latus-cream/35">
                        <td className="px-5 py-4"><p className="font-bold text-latus-ink">{String(statement.period_start || "").slice(0, 10)} → {String(statement.period_end || "").slice(0, 10)}</p><p className="mt-1 font-mono text-[10px] text-latus-muted">{statement.statement_id}</p></td>
                        <td className="px-4 py-4"><span className={`inline-flex rounded-full px-2.5 py-1 font-extrabold ${meta.tone}`}>{meta.label}</span></td>
                        <td className="px-4 py-4 text-right"><p className="font-extrabold text-latus-ink">{money.format(Number(statement.ai_amount_ars || 0))}</p><p className="mt-1 text-[10px] text-latus-muted">{usd.format(Number(statement.billable_cost_usd || 0))}</p></td>
                        <td className="px-4 py-4 text-right font-bold text-latus-ink">{money.format(Number(statement.plan_amount_ars || 0))}</td>
                        <td className="px-4 py-4 text-right text-sm font-black text-latus-ink">{money.format(Number(statement.total_amount_ars || 0))}</td>
                        <td className="px-5 py-4 text-right"><Button type="button" variant="outline" size="sm" disabled={openingStatementId === statement.statement_id} onClick={() => openStatement(statement.statement_id)} className="rounded-lg border-latus-warm-border"><ReceiptText className="h-3.5 w-3.5" />{openingStatementId === statement.statement_id ? "Abriendo..." : "Ver detalle"}</Button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-6 py-10 text-center"><ReceiptText className="mx-auto h-8 w-8 text-latus-muted/45" /><p className="mt-3 font-extrabold text-latus-ink">Todavía no hay liquidaciones</p><p className="mt-1 text-sm text-latus-muted">Cuando se cierre un período de IA, el detalle aparecerá acá.</p></div>
          )}
          <div className="border-t border-latus-warm-border bg-sky-50/45 px-5 py-3 text-[11px] text-sky-900 sm:px-6">Los detalles son documentos informativos de liquidación. La factura fiscal correspondiente debe emitirse por el circuito impositivo de Latus.</div>
        </section>

        <section>
          <div className="mb-4"><p className="text-xs font-extrabold uppercase tracking-[0.15em] text-latus-blue">Planes</p><h3 className="mt-1 text-2xl font-extrabold text-latus-ink">Elegí cómo querés crecer</h3></div>
          <div className="grid gap-5 lg:grid-cols-3">
            {(plansQ.data || []).filter((plan) => plan.is_public).map((plan) => {
              const current = plan.code === data?.organization?.plan_code
                && data?.organization?.subscription_status === "active";
              const pendingRequest = plan.code === data?.latest_request?.plan_code && data?.latest_request?.status === "pending";
              const pendingCheckout = plan.code === data?.payment_provider?.plan_code && data?.payment_provider?.status === "pending";
              const pending = pendingRequest || pendingCheckout;
              const onlineCheckout = data?.payment_provider?.checkout_ready;
              const changingPlan = data?.payment_provider?.status === "authorized" && !current;
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
                    disabled={!canManage || current || startCheckout.isPending || requestPlan.isPending}
                    onClick={() => {
                      if (changingPlan && !window.confirm(`¿Querés cambiar al plan ${plan.name} por ${money.format(plan.monthly_price_ars)} al mes? Mercado Pago aplicará el nuevo importe a la suscripción.`)) return;
                      if (onlineCheckout) startCheckout.mutate(plan.code);
                      else requestPlan.mutate(plan.code);
                    }}
                    className={`mt-6 h-11 w-full rounded-xl font-extrabold ${plan.highlighted ? "bg-latus-blue text-white hover:bg-latus-blue/90" : "bg-latus-ink text-white hover:bg-latus-ink/90"}`}
                  >
                    {current ? "Plan actual" : pending && !onlineCheckout ? "Solicitud pendiente" : pendingCheckout ? <>Continuar pago <ArrowRight className="h-4 w-4" /></> : changingPlan ? <>Cambiar a este plan <ArrowRight className="h-4 w-4" /></> : onlineCheckout ? <>Contratar plan <ArrowRight className="h-4 w-4" /></> : <>Solicitar plan <ArrowRight className="h-4 w-4" /></>}
                  </Button>
                </article>
              );
            })}
          </div>
        </section>

        {canManage && (
          <section className="grid gap-6 rounded-[24px] border border-latus-warm-border bg-white p-6 lg:grid-cols-[1fr_420px] lg:items-center">
            <div className="flex gap-4">
              <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-latus-blue/10 text-latus-blue"><CreditCard className="h-5 w-5" /></span>
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-extrabold text-latus-ink">Facturación y pago online</h3>
                  {data?.payment_provider?.checkout_ready ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-extrabold text-emerald-700"><CheckCircle2 className="h-3.5 w-3.5" />Mercado Pago conectado</span>
                  ) : (
                    <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-extrabold text-amber-800">Pendiente de configurar</span>
                  )}
                </div>
                <p className="mt-2 max-w-2xl text-sm leading-5 text-latus-muted">
                  {data?.payment_provider?.checkout_ready
                    ? "El cobro se completa de forma segura en Mercado Pago. Las renovaciones y el estado de la licencia se concilian automáticamente."
                    : "Mientras se completa la conexión de cobros, podés solicitar un plan para que el administrador de la plataforma lo gestione."}
                </p>
                {data?.payment_provider?.last_synced_at && <p className="mt-3 text-xs font-semibold text-latus-muted">Última actualización: {new Date(data.payment_provider.last_synced_at).toLocaleString("es-AR")}</p>}
              </div>
            </div>
            <div className="rounded-2xl bg-latus-cream p-4">
              <label className="text-xs font-extrabold uppercase tracking-wide text-latus-muted">Email de facturación</label>
              <Input type="email" value={billingEmail} onChange={(event) => setBillingEmail(event.target.value)} className="mt-2 h-11 rounded-xl border-latus-warm-border bg-white" placeholder="pagos@empresa.com" />
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                {data?.payment_provider?.status && (
                  <Button type="button" variant="outline" disabled={reconcile.isPending} onClick={() => reconcile.mutate()} className="h-10 flex-1 rounded-xl border-latus-warm-border bg-white">
                    <RefreshCw className={`h-4 w-4 ${reconcile.isPending ? "animate-spin" : ""}`} />{reconcile.isPending ? "Actualizando..." : "Actualizar estado"}
                  </Button>
                )}
                <div className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-white px-4 py-2.5 text-xs font-bold text-latus-muted"><Building2 className="h-4 w-4" />Administrado por empresa</div>
              </div>
              {data?.payment_provider?.status === "authorized" && (
                <button
                  type="button"
                  disabled={cancelSubscription.isPending || data?.organization?.cancel_at_period_end}
                  onClick={() => {
                    if (!window.confirm("¿Querés cancelar la renovación automática? Mantendrás el acceso hasta el final del período ya abonado. Si existe consumo de IA pendiente, se cobrará únicamente esa liquidación final antes de cerrar la suscripción.")) return;
                    cancelSubscription.mutate();
                  }}
                  className="mt-3 inline-flex items-center gap-1.5 text-xs font-bold text-rose-600 hover:text-rose-700 disabled:opacity-50"
                >
                  <XCircle className="h-3.5 w-3.5" />{data?.organization?.cancel_at_period_end ? "Cancelación programada al cierre" : cancelSubscription.isPending ? "Cancelando..." : "Cancelar renovación automática"}
                </button>
              )}
            </div>
          </section>
        )}
      </div>
    </AppLayout>
  );
}
