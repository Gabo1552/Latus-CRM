import { useMemo, useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle, CheckCircle2, Layers3, Package, Plus, Upload, Download, Search, RefreshCw, Edit3, Trash2,
  FileSpreadsheet, SlidersHorizontal, X,
} from "lucide-react";
import AppLayout from "@/components/AppLayout";
import { Badge } from "@/components/Bits";
import { useAuth } from "@/context/AuthContext";
import api from "@/lib/api";
import { hasPermission } from "@/lib/permissions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

const CURRENCIES = ["ARS", "USD", "EUR", "BRL", "CLP", "UYU", "MXN"];
const STOCK_OPTS = [
  { v: "disponible", l: "Disponible" },
  { v: "sin_stock",  l: "Sin stock" },
  { v: "consultar",  l: "Consultar" },
];
const STOCK_COLOR = {
  disponible: { bg: "#DCFCE7", color: "#16A34A" },
  sin_stock:  { bg: "#FEE2E2", color: "#DC2626" },
  consultar:  { bg: "#F4F4F5", color: "#888888" },
};


function fmtPrice(p, cur) {
  if (p === null || p === undefined || p === "") return "Consultar";
  const n = Number(p);
  return `${cur || "ARS"} ${n.toLocaleString("es-AR")}`;
}

function toLocalDateTimeInput(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function promoDescription(product) {
  if (!product?.promo_price) return null;
  if (product.promo_active) {
    if (product.promo_limit_type === "units") return `${product.promo_units_remaining} unidades restantes`;
    if (product.promo_limit_type === "date" && product.promo_end_at) {
      return `Hasta ${new Date(product.promo_end_at).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" })}`;
    }
    return "Promoción activa";
  }
  return {
    scheduled: "Promoción programada",
    expired: "Promoción vencida",
    exhausted: "Cupo promocional agotado",
  }[product.promo_status] || "Promoción inactiva";
}


export default function Catalogo() {
  const { user } = useAuth();
  const canWrite = hasPermission(user, "catalog_admin");

  const [filters, setFilters] = useState({
    q: "", category: "all", stock_status: "all", include_inactive: false,
    sort: "name", offset: 0,
  });
  const [editing, setEditing] = useState(null); // product object or null
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  const qc = useQueryClient();
  const params = useMemo(() => {
    const p = new URLSearchParams();
    if (filters.q) p.set("q", filters.q);
    if (filters.category && filters.category !== "all") p.set("category", filters.category);
    if (filters.stock_status && filters.stock_status !== "all") p.set("stock_status", filters.stock_status);
    if (filters.include_inactive) p.set("include_inactive", "true");
    p.set("sort", filters.sort);
    p.set("limit", "50");
    p.set("offset", String(filters.offset));
    return p.toString();
  }, [filters]);

  const listQ = useQuery({
    queryKey: ["catalog-list", params],
    queryFn: () => api.get(`/catalog/products?${params}`).then((r) => r.data),
    enabled: !!user,
  });
  const catsQ = useQuery({
    queryKey: ["catalog-categories"],
    queryFn: () => api.get("/catalog/categories").then((r) => r.data),
    enabled: !!user,
  });
  const statsQ = useQuery({
    queryKey: ["catalog-stats"],
    queryFn: () => api.get("/catalog/stats").then((r) => r.data),
    enabled: !!user,
  });
  const reload = () => {
    qc.invalidateQueries({ queryKey: ["catalog-list"] });
    qc.invalidateQueries({ queryKey: ["catalog-stats"] });
    qc.invalidateQueries({ queryKey: ["catalog-categories"] });
  };

  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get("/settings").then((r) => r.data),
    enabled: !!user,
  });
  const categoryColors = settingsQ.data?.catalog_category_colors || {};

  const items = listQ.data?.items || [];
  const total = listQ.data?.total || 0;

  const onExport = async () => {
    try {
      const url = `${process.env.REACT_APP_BACKEND_URL}/api/catalog/products/export-csv`;
      const res = await fetch(url, { credentials: "include" });
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `catalogo_latus_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      toast.success("CSV exportado");
    } catch { toast.error("No se pudo exportar"); }
  };

  return (
    <AppLayout title="Catálogo">
      <div className="mx-auto w-full max-w-[1500px] space-y-6 p-4 sm:p-6 lg:p-8" data-testid="catalogo-page">
        {/* Header / stats */}
        <section className="overflow-hidden rounded-2xl border border-latus-warm-border bg-gradient-to-br from-white via-latus-surface to-latus-ice/35 shadow-[0_18px_45px_rgba(13,31,42,0.06)]">
          <div className="flex flex-col justify-between gap-5 p-5 sm:p-6 xl:flex-row xl:items-center">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-latus-ink text-white shadow-[0_10px_24px_rgba(13,31,42,0.18)]">
                <Package className="h-5 w-5" />
              </div>
              <div>
                <p className="text-lg font-bold tracking-tight text-latus-ink">Gestión de productos</p>
                <p className="mt-1 max-w-xl text-sm leading-relaxed text-latus-muted">Administrá precios, promociones, disponibilidad y la información comercial que consulta el equipo y utiliza el bot.</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 xl:justify-end">
              <Button data-testid="catalog-reload" variant="outline" onClick={reload} className="h-10 rounded-lg border-latus-warm-border bg-white px-3.5">
                <RefreshCw className={`mr-1.5 h-4 w-4 ${listQ.isFetching ? "animate-spin" : ""}`} /> Actualizar
              </Button>
              {canWrite && (
                <>
                  <Button data-testid="catalog-import" variant="outline" onClick={() => setImporting(true)} className="h-10 rounded-lg border-latus-warm-border bg-white px-3.5"><Upload className="mr-1.5 h-4 w-4" /> Importar CSV</Button>
                  <Button data-testid="catalog-export" variant="outline" onClick={onExport} className="h-10 rounded-lg border-latus-warm-border bg-white px-3.5"><Download className="mr-1.5 h-4 w-4" /> Exportar</Button>
                  <Button data-testid="catalog-new" onClick={() => setCreating(true)} className="h-10 rounded-lg bg-latus-blue px-4 font-semibold text-white shadow-sm hover:bg-latus-blue-deep"><Plus className="mr-1.5 h-4 w-4" /> Nuevo producto</Button>
                </>
              )}
            </div>
          </div>
          <div className="grid border-t border-latus-warm-border bg-white/45 sm:grid-cols-3">
            {[
              { label: "Productos totales", value: statsQ.data?.total, icon: Layers3, color: "text-latus-blue", bg: "bg-latus-ice" },
              { label: "Productos activos", value: statsQ.data?.active, icon: CheckCircle2, color: "text-emerald-700", bg: "bg-emerald-50" },
              { label: "Sin stock", value: statsQ.data?.out_of_stock, icon: AlertTriangle, color: "text-amber-700", bg: "bg-amber-50" },
            ].map((stat, index) => (
              <div key={stat.label} className={`flex items-center gap-3 px-5 py-4 sm:px-6 ${index > 0 ? "border-t border-latus-warm-border sm:border-l" : ""} ${index > 0 ? "sm:border-t-0" : ""}`}>
                <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${stat.bg}`}><stat.icon className={`h-4 w-4 ${stat.color}`} /></div>
                <div><p className="text-xl font-extrabold tracking-tight text-latus-ink">{stat.value ?? "—"}</p><p className="text-[11px] font-semibold uppercase tracking-wider text-latus-muted">{stat.label}</p></div>
              </div>
            ))}
          </div>
        </section>

        {/* Filters */}
        <section className="rounded-xl border border-latus-warm-border bg-white p-4 shadow-[0_10px_28px_rgba(13,31,42,0.035)] sm:p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <p className="flex items-center gap-2 text-sm font-bold text-latus-ink"><SlidersHorizontal className="h-4 w-4 text-latus-blue" /> Buscar y filtrar</p>
              <p className="mt-1 text-xs text-latus-muted">Encontrá productos por nombre, código, categoría o disponibilidad.</p>
            </div>
            {filters.q || filters.category !== "all" || filters.stock_status !== "all" || filters.include_inactive ? (
              <Button type="button" variant="ghost" size="sm" onClick={() => setFilters({ q: "", category: "all", stock_status: "all", include_inactive: false, sort: "name", offset: 0 })} className="text-latus-blue">Limpiar filtros</Button>
            ) : null}
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-[minmax(320px,2fr)_minmax(180px,1fr)_minmax(160px,0.9fr)_minmax(180px,1fr)_auto] xl:items-center">
            <div className="relative">
              <Search className="absolute left-3.5 top-3 h-4 w-4 text-latus-muted" />
              <Input data-testid="catalog-search" placeholder="Buscar por nombre, SKU o tag…" value={filters.q} onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value, offset: 0 }))} className="h-10 rounded-lg border-latus-warm-border bg-latus-cream/25 pl-10" />
            </div>
            <Select value={filters.category} onValueChange={(v) => setFilters((f) => ({ ...f, category: v, offset: 0 }))}>
              <SelectTrigger data-testid="catalog-filter-category" className="h-10 rounded-lg border-latus-warm-border bg-white text-sm"><SelectValue placeholder="Categoría" /></SelectTrigger>
              <SelectContent><SelectItem value="all">Todas las categorías</SelectItem>{(catsQ.data?.categories || []).map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
            </Select>
            <Select value={filters.stock_status} onValueChange={(v) => setFilters((f) => ({ ...f, stock_status: v, offset: 0 }))}>
              <SelectTrigger data-testid="catalog-filter-stock" className="h-10 rounded-lg border-latus-warm-border bg-white text-sm"><SelectValue placeholder="Stock" /></SelectTrigger>
              <SelectContent><SelectItem value="all">Todos los stocks</SelectItem>{STOCK_OPTS.map(o => <SelectItem key={o.v} value={o.v}>{o.l}</SelectItem>)}</SelectContent>
            </Select>
            <Select value={filters.sort} onValueChange={(v) => setFilters((f) => ({ ...f, sort: v }))}>
              <SelectTrigger data-testid="catalog-sort" className="h-10 rounded-lg border-latus-warm-border bg-white text-sm"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="name">Nombre (A-Z)</SelectItem><SelectItem value="-name">Nombre (Z-A)</SelectItem><SelectItem value="price">Precio (menor)</SelectItem><SelectItem value="-price">Precio (mayor)</SelectItem><SelectItem value="-created_at">Más recientes</SelectItem></SelectContent>
            </Select>
            <label className="flex h-10 cursor-pointer items-center justify-between gap-3 rounded-lg border border-latus-warm-border bg-latus-cream/30 px-3 xl:justify-start">
              <Switch checked={filters.include_inactive} onCheckedChange={(include_inactive) => setFilters((f) => ({ ...f, include_inactive, offset: 0 }))} />
              <span className="whitespace-nowrap text-xs font-semibold text-latus-ink">Incluir inactivos</span>
            </label>
          </div>
        </section>

        {/* Empty / table */}
        {listQ.isPending ? (
          <div className="flex min-h-64 items-center justify-center rounded-xl border border-latus-warm-border bg-white">
            <div className="text-center"><RefreshCw className="mx-auto h-6 w-6 animate-spin text-latus-blue" /><p className="mt-3 text-sm font-semibold text-latus-muted">Cargando catálogo…</p></div>
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-latus-warm-border bg-white p-12 text-center shadow-[0_10px_28px_rgba(13,31,42,0.025)]">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-latus-ice"><Package className="h-5 w-5 text-latus-blue" /></div>
            <p className="text-base font-bold text-latus-ink">No encontramos productos</p>
            <p className="mx-auto mt-1 max-w-md text-sm text-latus-muted">
              {canWrite
                ? "Agregá el primero, importá un CSV o ajustá los filtros de búsqueda."
                : "Pediles a un administrador que cargue el catálogo."}
            </p>
          </div>
        ) : (
          <section className="overflow-hidden rounded-xl border border-latus-warm-border bg-white shadow-[0_14px_36px_rgba(13,31,42,0.045)]" data-testid="catalog-table">
            <div className="flex items-center justify-between border-b border-latus-warm-border px-5 py-4 sm:px-6">
              <div><p className="text-sm font-bold text-latus-ink">Productos</p><p className="mt-0.5 text-xs text-latus-muted">{total} {total === 1 ? "producto encontrado" : "productos encontrados"}</p></div>
              <p className="hidden text-xs text-latus-muted sm:block">Hacé clic en una fila para editarla</p>
            </div>
            <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] table-fixed text-sm">
              <colgroup><col className="w-[34%]" /><col className="w-[14%]" /><col className="w-[17%]" /><col className="w-[18%]" /><col className="w-[12%]" /><col className="w-[5%]" /></colgroup>
              <thead className="bg-latus-cream/75 text-[10px] uppercase tracking-[0.13em] text-latus-muted">
                <tr>
                  <th className="px-6 py-3 text-left font-bold">Producto</th>
                  <th className="px-4 py-3 text-left font-bold">SKU</th>
                  <th className="px-4 py-3 text-left font-bold">Categoría</th>
                  <th className="px-4 py-3 text-right font-bold">Precio</th>
                  <th className="px-4 py-3 text-left font-bold">Stock</th>
                  <th className="px-4 py-3 text-right"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => {
                  const s = STOCK_COLOR[p.stock_status] || STOCK_COLOR.consultar;
                  return (
                    <tr key={p.product_id}
                        data-testid={`catalog-row-${p.product_id}`}
                        className="cursor-pointer border-t border-latus-warm-border/80 transition-colors hover:bg-latus-ice/20"
                        onClick={() => canWrite && setEditing(p)}>
                      <td className="px-6 py-4">
                        <div className="flex min-w-0 items-center gap-3">
                          {p.image_url ? (
                            <img src={p.image_url} alt="" className="h-11 w-11 shrink-0 rounded-lg border border-latus-warm-border object-cover" />
                          ) : (
                            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-latus-warm-gray">
                              <Package className="h-4 w-4 text-latus-muted" />
                            </div>
                          )}
                          <div className="min-w-0">
                            <div className="flex items-center gap-2"><p className="truncate font-bold text-latus-ink">{p.name}</p>{p.active === false && <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[9px] font-bold uppercase text-neutral-500">Inactivo</span>}</div>
                            {Array.isArray(p.tags) && p.tags.length > 0 && (
                              <p className="mt-1 truncate text-[10px] text-latus-muted">{p.tags.join(" · ")}</p>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-4 font-mono text-xs text-latus-muted">{p.sku || "—"}</td>
                      <td className="px-4 py-4">
                        {p.category ? (
                          categoryColors[p.category] ? (
                            <Badge
                              label={p.category}
                              color={categoryColors[p.category]}
                              bg={categoryColors[p.category] + "1a"}
                            />
                          ) : (
                            p.category
                          )
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-4 text-right">
                        <span className={p.promo_active ? "text-xs text-latus-muted line-through" : "font-bold text-latus-ink"}>
                          {fmtPrice(p.price, p.currency)}
                        </span>
                        {p.promo_active && (
                          <p className="mt-0.5 font-bold text-latus-blue">{fmtPrice(p.effective_price, p.currency)}</p>
                        )}
                        {p.promo_price && <p className={`text-[10px] ${p.promo_active ? "text-emerald-700" : "text-[#888888]"}`}>{promoDescription(p)}</p>}
                      </td>
                      <td className="px-4 py-4">
                        <span className="inline-flex rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide"
                              style={{ background: s.bg, color: s.color }}>
                          {STOCK_OPTS.find(o => o.v === p.stock_status)?.l || p.stock_status}
                        </span>
                      </td>
                      <td className="px-4 py-4 text-right">
                        {canWrite && (
                          <Button data-testid={`catalog-edit-${p.product_id}`}
                                  variant="outline" size="sm" className="h-8 w-8 rounded-lg border-latus-warm-border p-0"
                                  onClick={(e) => { e.stopPropagation(); setEditing(p); }}>
                            <Edit3 className="h-3 w-3" />
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
            <div className="flex flex-col items-center justify-between gap-3 border-t border-latus-warm-border bg-latus-cream/25 px-5 py-4 text-xs sm:flex-row sm:px-6">
              <p className="text-latus-muted">Mostrando {Math.min(filters.offset + 1, total)}–{Math.min(filters.offset + items.length, total)} de {total}</p>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" className="h-8 rounded-lg border-latus-warm-border bg-white"
                        disabled={filters.offset === 0}
                        onClick={() => setFilters((f) => ({ ...f, offset: Math.max(0, f.offset - 50) }))}>
                  Anterior
                </Button>
                <Button size="sm" variant="outline" className="h-8 rounded-lg border-latus-warm-border bg-white"
                        disabled={filters.offset + 50 >= total}
                        onClick={() => setFilters((f) => ({ ...f, offset: f.offset + 50 }))}>
                  Siguiente
                </Button>
              </div>
            </div>
          </section>
        )}

        {/* Modals */}
        {(creating || editing) && (
          <ProductModal
            product={editing}
            categories={catsQ.data?.categories || []}
            onClose={() => { setCreating(false); setEditing(null); }}
            onSaved={() => { setCreating(false); setEditing(null); reload(); }}
          />
        )}
        {importing && (
          <ImportModal onClose={() => setImporting(false)} onDone={reload} />
        )}
      </div>
    </AppLayout>
  );
}


// ---------------------------------------------------------------------------
// Product modal (create / edit)
// ---------------------------------------------------------------------------

function ProductModal({ product, categories, onClose, onSaved }) {
  const isEdit = !!product;
  const [d, setD] = useState(product ? {
    ...product,
    promo_start_at: toLocalDateTimeInput(product.promo_start_at),
    promo_end_at: toLocalDateTimeInput(product.promo_end_at),
  } : {
    name: "", sku: "", category: "", description: "",
    price: "", currency: "ARS", stock_status: "disponible",
    active: true, tags: [], image_url: "", promo_price: "",
    promo_limit_type: "none", promo_start_at: "", promo_end_at: "",
    promo_unit_limit: "", promo_units_used: 0,
    commercial_conditions: "", external_link: "",
  });
  const [tagDraft, setTagDraft] = useState("");

  const save = useMutation({
    mutationFn: (payload) => isEdit
      ? api.put(`/catalog/products/${product.product_id}`, payload)
      : api.post("/catalog/products", payload),
    onSuccess: () => { toast.success(isEdit ? "Producto actualizado" : "Producto creado"); onSaved(); },
    onError: (e) => toast.error(e?.response?.data?.detail || "Error al guardar"),
  });
  const del = useMutation({
    mutationFn: () => api.delete(`/catalog/products/${product.product_id}`),
    onSuccess: () => { toast.success("Producto eliminado"); onSaved(); },
    onError: () => toast.error("No se pudo eliminar"),
  });

  const onSave = () => {
    if (!d.name?.trim()) { toast.error("El nombre es requerido"); return; }
    if (d.external_link && !/^https?:\/\//.test(d.external_link)) {
      toast.error("El enlace externo debe empezar con http:// o https://"); return;
    }
    const payload = { ...d };
    if (payload.price === "") delete payload.price;
    if (payload.promo_price === "") {
      payload.promo_price = null;
      payload.promo_limit_type = "none";
      payload.promo_start_at = null;
      payload.promo_end_at = null;
      payload.promo_unit_limit = null;
    } else if (payload.promo_limit_type === "date") {
      if (!payload.promo_end_at) { toast.error("Indicá hasta cuándo dura la promoción"); return; }
      payload.promo_start_at = payload.promo_start_at ? new Date(payload.promo_start_at).toISOString() : null;
      payload.promo_end_at = new Date(payload.promo_end_at).toISOString();
      payload.promo_unit_limit = null;
    } else if (payload.promo_limit_type === "units") {
      if (!Number(payload.promo_unit_limit)) { toast.error("Indicá cuántas unidades tendrá la promoción"); return; }
      payload.promo_start_at = null;
      payload.promo_end_at = null;
      payload.promo_unit_limit = Number(payload.promo_unit_limit);
    } else {
      payload.promo_start_at = null;
      payload.promo_end_at = null;
      payload.promo_unit_limit = null;
    }
    delete payload.promo_active;
    delete payload.promo_status;
    delete payload.promo_units_remaining;
    delete payload.effective_price;
    delete payload.promo_units_used;
    save.mutate(payload);
  };

  const addTag = () => {
    const t = tagDraft.trim();
    if (!t) return;
    setD((x) => ({ ...x, tags: [...(x.tags || []), t] }));
    setTagDraft("");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-latus-ink/55 p-3 backdrop-blur-[2px] sm:p-6" onClick={onClose}>
      <div data-testid="catalog-product-modal"
           className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-white/20 bg-white shadow-[0_30px_90px_rgba(13,31,42,0.28)]"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-latus-warm-border bg-latus-cream/35 px-5 py-4 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-latus-ice"><Package className="h-4 w-4 text-latus-blue" /></div>
            <div><p className="text-base font-bold text-latus-ink">{isEdit ? "Editar producto" : "Nuevo producto"}</p><p className="mt-0.5 text-xs text-latus-muted">Completá la información comercial y de disponibilidad.</p></div>
          </div>
          <button onClick={onClose} className="flex h-9 w-9 items-center justify-center rounded-lg text-latus-muted transition hover:bg-white hover:text-latus-ink" aria-label="Cerrar">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid grid-cols-1 gap-4 overflow-y-auto p-5 sm:grid-cols-2 sm:p-6">
          <div className="sm:col-span-2">
            <Label className="text-xs font-bold text-[#888888]">Nombre *</Label>
            <Input data-testid="modal-name" value={d.name || ""} onChange={(e) => setD({ ...d, name: e.target.value })}
                   className="rounded-sm h-9 mt-1" />
          </div>
          <div>
            <Label className="text-xs font-bold text-[#888888]">SKU</Label>
            <Input data-testid="modal-sku" value={d.sku || ""} onChange={(e) => setD({ ...d, sku: e.target.value })}
                   className="rounded-sm h-9 mt-1 font-mono" />
          </div>
          <div>
            <Label className="text-xs font-bold text-[#888888]">Categoría</Label>
            <Select value={d.category || "__none__"} onValueChange={(v) => setD({ ...d, category: v === "__none__" ? "" : v })}>
              <SelectTrigger data-testid="modal-category" className="rounded-sm h-9 mt-1 text-sm">
                <SelectValue placeholder="Seleccioná una categoría" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">Sin categoría</SelectItem>
                {categories.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs font-bold text-[#888888]">Precio</Label>
            <Input data-testid="modal-price" type="number" step="0.01" min="0"
                   value={d.price ?? ""} onChange={(e) => setD({ ...d, price: e.target.value })}
                   className="rounded-sm h-9 mt-1" />
          </div>
          <div>
            <Label className="text-xs font-bold text-[#888888]">Precio promocional</Label>
            <Input data-testid="modal-promo" type="number" step="0.01" min="0"
                   value={d.promo_price ?? ""} onChange={(e) => setD({ ...d, promo_price: e.target.value })}
                   className="rounded-sm h-9 mt-1" />
          </div>
          <div className="sm:col-span-2 rounded-lg border border-[#E9E6DC] bg-latus-cream/45 p-4">
            <div className="mb-3">
              <p className="text-xs font-bold uppercase tracking-wider text-[#0B1B26]">Vigencia de la promoción</p>
              <p className="mt-1 text-xs text-[#888888]">Podés limitarla por fecha o por cantidad de unidades vendidas.</p>
            </div>
            {d.promo_price === "" || d.promo_price === null || d.promo_price === undefined ? (
              <p className="rounded-md border border-dashed border-[#DCD9CE] bg-white px-3 py-2 text-xs text-[#888888]">Ingresá un precio promocional para configurar su duración.</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label className="text-xs font-bold text-[#888888]">La promoción dura</Label>
                  <Select value={d.promo_limit_type || "none"} onValueChange={(promo_limit_type) => setD({ ...d, promo_limit_type })}>
                    <SelectTrigger data-testid="modal-promo-limit-type" className="mt-1 h-9 bg-white"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Sin límite</SelectItem>
                      <SelectItem value="date">Hasta una fecha</SelectItem>
                      <SelectItem value="units">Por cantidad de unidades</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {d.promo_limit_type === "units" && (
                  <div>
                    <Label className="text-xs font-bold text-[#888888]">Unidades promocionales</Label>
                    <Input data-testid="modal-promo-unit-limit" type="number" min="1" value={d.promo_unit_limit ?? ""} onChange={(event) => setD({ ...d, promo_unit_limit: event.target.value })} className="mt-1 h-9 bg-white" />
                    {Number(d.promo_units_used || 0) > 0 && <p className="mt-1 text-[10px] text-[#888888]">Ya utilizadas: {d.promo_units_used}</p>}
                  </div>
                )}

                {d.promo_limit_type === "date" && (
                  <>
                    <div>
                      <Label className="text-xs font-bold text-[#888888]">Comienza</Label>
                      <Input data-testid="modal-promo-start" type="datetime-local" value={d.promo_start_at || ""} onChange={(event) => setD({ ...d, promo_start_at: event.target.value })} className="mt-1 h-9 bg-white" />
                      <p className="mt-1 text-[10px] text-[#888888]">Opcional; vacío significa desde ahora.</p>
                    </div>
                    <div>
                      <Label className="text-xs font-bold text-[#888888]">Finaliza *</Label>
                      <Input data-testid="modal-promo-end" type="datetime-local" value={d.promo_end_at || ""} onChange={(event) => setD({ ...d, promo_end_at: event.target.value })} className="mt-1 h-9 bg-white" />
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
          <div>
            <Label className="text-xs font-bold text-[#888888]">Moneda</Label>
            <Select value={d.currency || "ARS"} onValueChange={(v) => setD({ ...d, currency: v })}>
              <SelectTrigger data-testid="modal-currency" className="rounded-sm h-9 mt-1 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>{CURRENCIES.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs font-bold text-[#888888]">Stock</Label>
            <Select value={d.stock_status || "consultar"} onValueChange={(v) => setD({ ...d, stock_status: v })}>
              <SelectTrigger data-testid="modal-stock" className="rounded-sm h-9 mt-1 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>{STOCK_OPTS.map(o => <SelectItem key={o.v} value={o.v}>{o.l}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="sm:col-span-2">
            <Label className="text-xs font-bold text-[#888888]">Tags</Label>
            <div className="flex gap-2 mt-1">
              <Input data-testid="modal-tag-input" value={tagDraft} onChange={(e) => setTagDraft(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }}
                     placeholder="Agregar tag y presioná Enter…" className="rounded-sm h-9" />
              <Button type="button" variant="outline" onClick={addTag} className="rounded-sm h-9">
                Agregar
              </Button>
            </div>
            <div className="flex flex-wrap gap-1 mt-2">
              {(d.tags || []).map((t, i) => (
                <span key={i} className="text-xs bg-[#F4F2EC] border border-[#EFE3E1] text-[#0E8DDB] px-2 py-0.5 rounded-sm inline-flex items-center gap-1">
                  {t}
                  <button onClick={() => setD({ ...d, tags: d.tags.filter((_, j) => j !== i) })}>×</button>
                </span>
              ))}
            </div>
          </div>
          <div className="sm:col-span-2">
            <Label className="text-xs font-bold text-[#888888]">Descripción</Label>
            <Textarea data-testid="modal-description" value={d.description || ""}
                      onChange={(e) => setD({ ...d, description: e.target.value })}
                      className="rounded-sm mt-1 min-h-[80px] text-sm" />
          </div>
          <div>
            <Label className="text-xs font-bold text-[#888888]">URL de imagen</Label>
            <Input data-testid="modal-image" value={d.image_url || ""}
                   onChange={(e) => setD({ ...d, image_url: e.target.value })}
                   className="rounded-sm h-9 mt-1 font-mono text-xs"
                   placeholder="https://…" />
            {d.image_url && /^https?:\/\//.test(d.image_url) && (
              <img src={d.image_url} alt="" className="h-12 mt-1 rounded-sm border border-[#E9E6DC]" />
            )}
          </div>
          <div>
            <Label className="text-xs font-bold text-[#888888]">Enlace externo</Label>
            <Input data-testid="modal-link" value={d.external_link || ""}
                   onChange={(e) => setD({ ...d, external_link: e.target.value })}
                   className="rounded-sm h-9 mt-1 font-mono text-xs"
                   placeholder="https://…" />
          </div>
          <div className="sm:col-span-2">
            <Label className="text-xs font-bold text-[#888888]">Condiciones comerciales</Label>
            <Textarea data-testid="modal-conditions" value={d.commercial_conditions || ""}
                      onChange={(e) => setD({ ...d, commercial_conditions: e.target.value })}
                      className="rounded-sm mt-1 min-h-[60px] text-sm" />
          </div>
          <div className="sm:col-span-2 flex items-center gap-2">
            <Switch data-testid="modal-active" checked={!!d.active}
                    onCheckedChange={(v) => setD({ ...d, active: v })} />
            <Label className="text-sm font-bold text-[#0B1B26]">Producto activo</Label>
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-latus-warm-border bg-white px-5 py-4 sm:px-6">
          {isEdit ? (
            <Button data-testid="modal-delete" variant="outline" className="rounded-lg border-red-200 text-red-700 hover:bg-red-50"
                    onClick={() => { if (confirm("¿Eliminar este producto?")) del.mutate(); }}>
              <Trash2 className="h-3.5 w-3.5 mr-1" /> Eliminar
            </Button>
          ) : <div />}
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose} className="rounded-lg border-latus-warm-border">Cancelar</Button>
            <Button data-testid="modal-save" onClick={onSave} disabled={save.isPending}
                    className="rounded-lg bg-latus-blue px-5 text-white hover:bg-latus-blue-deep">
              {save.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
              Guardar
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// CSV import modal
// ---------------------------------------------------------------------------

function ImportModal({ onClose, onDone }) {
  const [file, setFile] = useState(null);
  const [updateExisting, setUpdateExisting] = useState(false);
  const [result, setResult] = useState(null);
  const fileInput = useRef(null);

  const upload = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("update_existing", String(updateExisting));
      const r = await api.post("/catalog/products/import-csv", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return r.data;
    },
    onSuccess: (d) => { setResult(d); onDone(); },
    onError: (e) => toast.error(e?.response?.data?.detail || "Error al importar"),
  });

  const downloadTemplate = () => {
    const headers = "name,sku,category,description,price,currency,stock_status,active,tags,image_url,promo_price,promo_limit_type,promo_start_at,promo_end_at,promo_unit_limit,commercial_conditions,external_link\n";
    const a = document.createElement("a");
    a.href = "data:text/csv;charset=utf-8,\ufeff" + encodeURIComponent(headers);
    a.download = "plantilla_catalogo_latus.csv";
    a.click();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-latus-ink/55 p-3 backdrop-blur-[2px] sm:p-6" onClick={onClose}>
      <div data-testid="catalog-import-modal"
           className="w-full max-w-xl overflow-hidden rounded-2xl border border-white/20 bg-white shadow-[0_30px_90px_rgba(13,31,42,0.28)]"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-latus-warm-border bg-latus-cream/35 px-5 py-4 sm:px-6">
          <div><p className="text-base font-bold text-latus-ink">Importar catálogo desde CSV</p><p className="mt-0.5 text-xs text-latus-muted">Carga o actualiza productos en bloque.</p></div>
          <button onClick={onClose} className="flex h-9 w-9 items-center justify-center rounded-lg text-latus-muted hover:bg-white hover:text-latus-ink" aria-label="Cerrar">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-4 p-5 sm:p-6">
          <div className="cursor-pointer rounded-xl border-2 border-dashed border-latus-warm-border bg-latus-cream/25 p-8 text-center transition hover:border-latus-blue/50 hover:bg-latus-ice/20"
               onClick={() => fileInput.current?.click()}>
            <FileSpreadsheet className="h-7 w-7 text-[#0E8DDB] mx-auto mb-2" />
            {file ? (
              <p className="text-sm font-bold text-[#0B1B26]">{file.name} <span className="text-[#888888]">· {(file.size / 1024).toFixed(1)} KB</span></p>
            ) : (
              <p className="text-sm text-[#888888]">Hacé clic para elegir un archivo .csv (UTF-8, máx 5MB)</p>
            )}
            <input data-testid="import-file-input" ref={fileInput} type="file" accept=".csv" hidden
                   onChange={(e) => { setFile(e.target.files?.[0]); setResult(null); }} />
          </div>
          <div className="flex items-center gap-2">
            <Switch data-testid="import-update-existing" checked={updateExisting} onCheckedChange={setUpdateExisting} />
            <Label className="text-sm">Actualizar productos existentes por SKU</Label>
          </div>
          <button onClick={downloadTemplate}
                  className="text-xs text-[#0E8DDB] hover:underline">
            Descargar plantilla CSV
          </button>
          {result && (
            <div data-testid="import-result"
                 className="bg-latus-cream border border-[#E9E6DC] rounded-sm p-3 text-xs space-y-1">
              <p><b>{result.total_rows}</b> filas leídas · <b>{result.created}</b> creados · <b>{result.updated}</b> actualizados · <b>{result.skipped}</b> omitidos · <b>{result.errors.length}</b> errores</p>
              {result.errors.length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[#DC2626] font-bold">Ver errores</summary>
                  <ul className="mt-1 list-disc pl-4">
                    {result.errors.slice(0, 30).map((e, i) => (
                      <li key={i}>Fila {e.row}: {e.message}{e.sku ? ` (SKU ${e.sku})` : ""}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </div>
        <div className="px-5 py-3 border-t border-[#E9E6DC] flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} className="rounded-sm">
            {result ? "Cerrar" : "Cancelar"}
          </Button>
          <Button data-testid="import-submit"
                  onClick={() => upload.mutate()}
                  disabled={!file || upload.isPending}
                  className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm">
            {upload.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : <Upload className="h-3.5 w-3.5 mr-1" />}
            Subir e importar
          </Button>
        </div>
      </div>
    </div>
  );
}
