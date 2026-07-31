import api from "@/lib/api";

export const BILLING_STATEMENT_STATUS = {
  pending: { label: "Pendiente", tone: "bg-slate-100 text-slate-700" },
  applying: { label: "Aplicando", tone: "bg-sky-100 text-sky-700" },
  applied: { label: "Próxima renovación", tone: "bg-sky-100 text-sky-700" },
  paid: { label: "Cobrada", tone: "bg-emerald-100 text-emerald-700" },
  payment_failed: { label: "Pago rechazado", tone: "bg-amber-100 text-amber-800" },
  failed: { label: "Error técnico", tone: "bg-rose-100 text-rose-700" },
  retrying: { label: "Reintentando", tone: "bg-amber-100 text-amber-800" },
  retry_exhausted: { label: "Reintentos agotados", tone: "bg-rose-100 text-rose-700" },
  blocked_margin: { label: "Bloqueada por margen", tone: "bg-rose-100 text-rose-700" },
  closed_no_charge: { label: "Cerrada sin saldo", tone: "bg-slate-100 text-slate-700" },
};

export function billingStatementStatus(status) {
  return BILLING_STATEMENT_STATUS[status] || {
    label: status || "Pendiente",
    tone: "bg-slate-100 text-slate-700",
  };
}

function responseFilename(response, fallback) {
  const disposition = response.headers?.["content-disposition"] || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallback;
}

export async function downloadBillingStatements(path, params = {}, fallback = "liquidaciones.csv") {
  const response = await api.get(path, { params, responseType: "blob" });
  const blob = new Blob([response.data], { type: "text/csv;charset=utf-8" });
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = responseFilename(response, fallback);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

export async function openBillingStatement(statementId) {
  const popup = window.open("about:blank", "_blank");
  if (popup) popup.opener = null;
  try {
    const response = await api.get(
      `/billing/statements/${encodeURIComponent(statementId)}/receipt`,
      { responseType: "blob" },
    );
    const blob = new Blob([response.data], { type: "text/html;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);
    if (popup) {
      popup.location.replace(url);
    } else {
      const link = document.createElement("a");
      link.href = url;
      link.download = responseFilename(response, `liquidacion-${statementId}.html`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    }
    window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    if (popup) popup.close();
    throw error;
  }
}
