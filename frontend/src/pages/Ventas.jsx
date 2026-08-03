import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote, CheckCircle2, ChevronDown, ChevronUp, CircleDollarSign, Plus, ReceiptText, RotateCcw, ShoppingCart, Trash2 } from "lucide-react";
import { toast } from "sonner";

import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/context/AuthContext";
import { money } from "@/lib/constants";
import { hasPermission } from "@/lib/permissions";

const STATUS = {
  draft: { label: "Borrador", cls: "bg-amber-50 text-amber-800 border-amber-200" },
  confirmed: { label: "Confirmada", cls: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  cancelled: { label: "Cancelada", cls: "bg-rose-50 text-rose-700 border-rose-200" },
};

const PAYMENT = {
  pending: "Pendiente",
  partial: "Pago parcial",
  paid: "Pagada",
  cancelled: "Sin cobro",
  refund_pending: "Reintegro pendiente",
};

const METHODS = [
  ["cash", "Efectivo"], ["transfer", "Transferencia"], ["card", "Tarjeta"],
  ["mercadopago", "Mercado Pago"], ["other", "Otro"],
];

const formatDate = (value) => value
  ? new Date(value).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" })
  : "—";

export default function Ventas() {
  const { user } = useAuth();
  const canUse = hasPermission(user, "crm_use");
  const qc = useQueryClient();
  const [status, setStatus] = useState("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [action, setAction] = useState(null);

  const querySuffix = status === "all" ? "" : `?status=${status}`;
  const salesQ = useQuery({
    queryKey: ["sales", status],
    queryFn: () => api.get(`/sales${querySuffix}`).then((response) => response.data),
  });
  const summaryQ = useQuery({
    queryKey: ["sales-summary"],
    queryFn: () => api.get("/sales/summary").then((response) => response.data),
  });
  const contactsQ = useQuery({
    queryKey: ["contacts", "sales"],
    queryFn: () => api.get("/contacts").then((response) => response.data),
    enabled: createOpen,
  });
  const productsQ = useQuery({
    queryKey: ["catalog-products", "sales"],
    queryFn: () => api.get("/sales/products").then((response) => response.data),
    enabled: createOpen,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["sales"] });
    qc.invalidateQueries({ queryKey: ["sales-summary"] });
    qc.invalidateQueries({ queryKey: ["catalog-products"] });
    qc.invalidateQueries({ queryKey: ["catalog-stats"] });
  };

  const confirmSale = useMutation({
    mutationFn: (saleId) => api.post(`/sales/${saleId}/confirm`).then((response) => response.data),
    onSuccess: () => { toast.success("Venta confirmada y stock actualizado"); refresh(); },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo confirmar la venta"),
  });

  const summary = summaryQ.data || {};
  const sales = salesQ.data?.items || [];

  return (
    <AppLayout
      title="Ventas"
      actions={canUse ? (
        <Button onClick={() => setCreateOpen(true)} className="rounded-lg bg-latus-blue text-white hover:bg-latus-blue-deep">
          <Plus className="mr-1.5 h-4 w-4" /> Nueva venta
        </Button>
      ) : null}
    >
      <div className="space-y-5 p-4 sm:p-6 lg:p-8">
        <section className="rounded-2xl border border-latus-warm-border bg-gradient-to-br from-white via-latus-surface to-latus-ice/35 p-5 shadow-[0_18px_45px_rgba(13,31,42,0.06)] sm:p-6">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-xs font-extrabold uppercase tracking-[0.14em] text-latus-blue">Operación comercial</p>
              <h1 className="mt-1 text-2xl font-extrabold text-latus-ink">Ventas, cobros y stock</h1>
              <p className="mt-1 max-w-2xl text-sm text-latus-muted">Confirmá pedidos con precios congelados, registrá pagos y mantené un historial de cada movimiento.</p>
            </div>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="h-10 w-full rounded-lg border-latus-warm-border bg-white sm:w-48"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos los estados</SelectItem>
                <SelectItem value="draft">Borradores</SelectItem>
                <SelectItem value="confirmed">Confirmadas</SelectItem>
                <SelectItem value="cancelled">Canceladas</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </section>

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            { label: "Ventas confirmadas", value: summary.confirmed_count || 0, icon: CheckCircle2, tone: "bg-emerald-50 text-emerald-700" },
            { label: "Total vendido", value: money(summary.confirmed_total), icon: CircleDollarSign, tone: "bg-sky-50 text-sky-700" },
            { label: "Saldo por cobrar", value: money(summary.pending_collection), icon: Banknote, tone: "bg-amber-50 text-amber-700" },
            { label: "Borradores", value: summary.draft_count || 0, icon: ReceiptText, tone: "bg-slate-100 text-slate-700" },
          ].map(({ label, value, icon: Icon, tone }) => (
            <div key={label} className="rounded-xl border border-latus-warm-border bg-white p-4 shadow-[0_10px_28px_rgba(13,31,42,0.04)]">
              <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${tone}`}><Icon className="h-4 w-4" /></div>
              <p className="mt-3 text-xs font-bold uppercase tracking-wider text-latus-muted">{label}</p>
              <p className="mt-1 text-xl font-extrabold text-latus-ink">{value}</p>
            </div>
          ))}
        </section>

        {salesQ.isLoading ? (
          <div className="flex min-h-64 items-center justify-center rounded-xl border border-latus-warm-border bg-white"><div className="h-8 w-8 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" /></div>
        ) : sales.length === 0 ? (
          <div className="rounded-xl border border-dashed border-latus-warm-border bg-white px-6 py-16 text-center">
            <ShoppingCart className="mx-auto h-10 w-10 text-latus-blue/55" />
            <h2 className="mt-3 font-bold text-latus-ink">Todavía no hay ventas en este estado</h2>
            <p className="mt-1 text-sm text-latus-muted">Creá un borrador para comenzar a registrar la operación.</p>
          </div>
        ) : (
          <section className="space-y-3">
            {sales.map((sale) => {
              const meta = STATUS[sale.status] || STATUS.draft;
              const isExpanded = expanded === sale.sale_id;
              return (
                <article key={sale.sale_id} className="overflow-hidden rounded-xl border border-latus-warm-border bg-white shadow-[0_10px_28px_rgba(13,31,42,0.035)]">
                  <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-center">
                    <button type="button" onClick={() => setExpanded(isExpanded ? null : sale.sale_id)} className="flex min-w-0 flex-1 items-start gap-3 text-left">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-latus-ice text-latus-blue"><ReceiptText className="h-4 w-4" /></div>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="font-bold text-latus-ink">{sale.customer_name || "Venta de mostrador"}</p>
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-extrabold uppercase ${meta.cls}`}>{meta.label}</span>
                        </div>
                        <p className="mt-1 text-xs text-latus-muted">{sale.sale_id.slice(-8).toUpperCase()} · {formatDate(sale.created_at)} · {sale.lines?.length || 0} productos</p>
                      </div>
                    </button>
                    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-latus-warm-border pt-3 lg:border-0 lg:pt-0">
                      <div className="text-right">
                        <p className="text-lg font-extrabold text-latus-ink">{money(sale.total)}</p>
                        <p className="text-[11px] font-semibold text-latus-muted">{PAYMENT[sale.payment_status] || sale.payment_status}</p>
                      </div>
                      {canUse && sale.status === "draft" && <Button size="sm" onClick={() => confirmSale.mutate(sale.sale_id)} disabled={confirmSale.isPending} className="bg-emerald-600 text-white hover:bg-emerald-700">Confirmar</Button>}
                      {canUse && sale.status === "confirmed" && Number(sale.balance_due || 0) > 0 && <Button size="sm" variant="outline" onClick={() => setAction({ type: "payment", sale })}>Registrar pago</Button>}
                      {canUse && sale.status !== "cancelled" && <Button size="sm" variant="ghost" onClick={() => setAction({ type: "cancel", sale })} className="text-rose-700 hover:bg-rose-50"><RotateCcw className="mr-1 h-3.5 w-3.5" />Cancelar</Button>}
                      <button type="button" onClick={() => setExpanded(isExpanded ? null : sale.sale_id)} className="rounded-lg p-2 text-latus-muted hover:bg-latus-cream">{isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</button>
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="border-t border-latus-warm-border bg-latus-cream/30 p-4 sm:p-5">
                      <div className="space-y-2">
                        {(sale.lines || []).map((line) => (
                          <div key={line.product_id} className="flex items-center justify-between gap-4 rounded-lg border border-latus-warm-border bg-white px-3 py-2.5 text-sm">
                            <div className="min-w-0"><p className="truncate font-semibold text-latus-ink">{line.name}</p><p className="text-xs text-latus-muted">{line.quantity} × {money(line.unit_price)}{line.promotion_applied ? " · promoción" : ""}</p></div>
                            <p className="font-bold text-latus-ink">{money(line.line_total)}</p>
                          </div>
                        ))}
                      </div>
                      <div className="mt-4 grid gap-2 text-xs sm:grid-cols-3">
                        <Info label="Cobrado" value={money(sale.amount_paid)} />
                        <Info label="Saldo" value={money(sale.balance_due)} />
                        <Info label="Notas" value={sale.notes || "Sin notas"} />
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        )}
      </div>

      <NewSaleDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        contacts={contactsQ.data || []}
        products={productsQ.data || []}
        onCreated={refresh}
      />
      <SaleActionDialog action={action} onClose={() => setAction(null)} onDone={refresh} />
    </AppLayout>
  );
}

function Info({ label, value }) {
  return <div className="rounded-lg border border-latus-warm-border bg-white px-3 py-2"><p className="font-bold uppercase tracking-wider text-latus-muted">{label}</p><p className="mt-1 font-semibold text-latus-ink">{value}</p></div>;
}

function NewSaleDialog({ open, onOpenChange, contacts, products, onCreated }) {
  const [contactId, setContactId] = useState("__none__");
  const [notes, setNotes] = useState("");
  const [productId, setProductId] = useState("__none__");
  const [quantity, setQuantity] = useState(1);
  const [lines, setLines] = useState([]);

  const productMap = useMemo(() => new Map(products.map((product) => [product.product_id, product])), [products]);
  const total = lines.reduce((sum, line) => sum + Number(line.effective_price || 0) * line.quantity, 0);
  const create = useMutation({
    mutationFn: () => api.post("/sales", {
      contact_id: contactId === "__none__" ? null : contactId,
      notes: notes || null,
      lines: lines.map((line) => ({ product_id: line.product_id, quantity: line.quantity })),
    }).then((response) => response.data),
    onSuccess: () => {
      toast.success("Borrador de venta creado");
      onCreated();
      onOpenChange(false);
      setContactId("__none__"); setNotes(""); setLines([]);
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo crear la venta"),
  });

  const addLine = () => {
    const product = productMap.get(productId);
    const amount = Math.max(1, Number(quantity || 1));
    if (!product) return;
    setLines((current) => {
      const found = current.find((line) => line.product_id === product.product_id);
      return found
        ? current.map((line) => line.product_id === product.product_id ? { ...line, quantity: line.quantity + amount } : line)
        : [...current, { ...product, quantity: amount }];
    });
    setProductId("__none__"); setQuantity(1);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto rounded-2xl border-latus-warm-border">
        <DialogHeader><DialogTitle>Nueva venta</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <div><Label>Cliente</Label><Select value={contactId} onValueChange={setContactId}><SelectTrigger className="mt-1"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="__none__">Venta de mostrador</SelectItem>{contacts.map((contact) => <SelectItem key={contact.id} value={contact.id}>{contact.name} · {contact.phone}</SelectItem>)}</SelectContent></Select></div>
          <div className="rounded-xl border border-latus-warm-border bg-latus-cream/35 p-3">
            <Label>Agregar producto</Label>
            <div className="mt-1 grid gap-2 sm:grid-cols-[1fr_90px_auto]">
              <Select value={productId} onValueChange={setProductId}><SelectTrigger className="bg-white"><SelectValue placeholder="Seleccionar" /></SelectTrigger><SelectContent>{products.filter((product) => product.active !== false).map((product) => <SelectItem key={product.product_id} value={product.product_id}>{product.name} · {money(product.effective_price)}{product.track_stock ? ` · stock ${product.stock_quantity}` : ""}</SelectItem>)}</SelectContent></Select>
              <Input type="number" min="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} className="bg-white" />
              <Button type="button" onClick={addLine} disabled={productId === "__none__"}>Agregar</Button>
            </div>
          </div>
          <div className="space-y-2">
            {lines.length === 0 ? <p className="rounded-lg border border-dashed border-latus-warm-border p-6 text-center text-sm text-latus-muted">Agregá productos para crear el borrador.</p> : lines.map((line) => <div key={line.product_id} className="flex items-center justify-between rounded-lg border border-latus-warm-border px-3 py-2"><div><p className="text-sm font-semibold">{line.name}</p><p className="text-xs text-latus-muted">{line.quantity} × {money(line.effective_price)}</p></div><div className="flex items-center gap-2"><b className="text-sm">{money(line.quantity * line.effective_price)}</b><button type="button" onClick={() => setLines((current) => current.filter((item) => item.product_id !== line.product_id))} className="rounded p-1 text-rose-600 hover:bg-rose-50"><Trash2 className="h-4 w-4" /></button></div></div>)}
          </div>
          <div><Label>Notas</Label><Textarea value={notes} onChange={(event) => setNotes(event.target.value)} className="mt-1" placeholder="Información interna de la operación" /></div>
          <div className="flex items-center justify-between rounded-xl bg-latus-ink px-4 py-3 text-white"><span className="text-sm font-semibold">Total estimado</span><strong className="text-xl">{money(total)}</strong></div>
        </div>
        <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button><Button onClick={() => create.mutate()} disabled={!lines.length || create.isPending} className="bg-latus-blue text-white">Crear borrador</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SaleActionDialog({ action, onClose, onDone }) {
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("transfer");
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");
  const mutation = useMutation({
    mutationFn: () => action.type === "payment"
      ? api.post(`/sales/${action.sale.sale_id}/payments`, { amount: Number(amount), method, reference: reference || null })
      : api.post(`/sales/${action.sale.sale_id}/cancel`, { reason }),
    onSuccess: () => { toast.success(action.type === "payment" ? "Pago registrado" : "Venta cancelada y stock restituido"); onDone(); onClose(); setAmount(""); setReference(""); setReason(""); },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo completar la operación"),
  });
  if (!action) return null;
  const payment = action.type === "payment";
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="rounded-2xl border-latus-warm-border">
        <DialogHeader><DialogTitle>{payment ? "Registrar pago" : "Cancelar venta"}</DialogTitle></DialogHeader>
        {payment ? <div className="space-y-3"><p className="text-sm text-latus-muted">Saldo pendiente: <b className="text-latus-ink">{money(action.sale.balance_due)}</b></p><div><Label>Importe</Label><Input type="number" min="0.01" max={action.sale.balance_due} value={amount} onChange={(event) => setAmount(event.target.value)} className="mt-1" /></div><div><Label>Medio de pago</Label><Select value={method} onValueChange={setMethod}><SelectTrigger className="mt-1"><SelectValue /></SelectTrigger><SelectContent>{METHODS.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div><div><Label>Referencia</Label><Input value={reference} onChange={(event) => setReference(event.target.value)} className="mt-1" placeholder="Número de operación opcional" /></div></div> : <div><p className="mb-3 text-sm text-latus-muted">El stock de los productos será restituido. Si tiene cobros, quedará marcado como reintegro pendiente.</p><Label>Motivo</Label><Textarea value={reason} onChange={(event) => setReason(event.target.value)} className="mt-1" placeholder="Explicá por qué se cancela" /></div>}
        <DialogFooter><Button variant="outline" onClick={onClose}>Volver</Button><Button onClick={() => mutation.mutate()} disabled={mutation.isPending || (payment ? Number(amount) <= 0 : reason.trim().length < 3)} className={payment ? "bg-latus-blue text-white" : "bg-rose-600 text-white hover:bg-rose-700"}>{payment ? "Guardar pago" : "Confirmar cancelación"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
