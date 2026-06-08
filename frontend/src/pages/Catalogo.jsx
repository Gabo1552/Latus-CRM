import { useMemo, useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Package, Plus, Upload, Download, Search, RefreshCw, Edit3, Trash2,
  FileSpreadsheet, X,
} from "lucide-react";
import AppLayout from "@/components/AppLayout";
import { useAuth } from "@/context/AuthContext";
import api from "@/lib/api";
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


export default function Catalogo() {
  const { user } = useAuth();
  const perms = user?.permissions || [];
  const canWrite = perms.includes("write_catalog");
  const canRead = !!user; // any authenticated user can read

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
      <div className="space-y-5" data-testid="catalogo-page">
        {/* Header / stats */}
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-sm text-[#888888]">
              {statsQ.data
                ? `${statsQ.data.active} productos activos · ${statsQ.data.out_of_stock} sin stock`
                : "Cargando…"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button data-testid="catalog-reload" variant="outline"
                    onClick={reload} className="rounded-sm">
              <RefreshCw className={`h-4 w-4 mr-1.5 ${listQ.isFetching ? "animate-spin" : ""}`} />
              Actualizar
            </Button>
            {canWrite && (
              <>
                <Button data-testid="catalog-import" variant="outline" onClick={() => setImporting(true)} className="rounded-sm">
                  <Upload className="h-4 w-4 mr-1.5" /> Importar CSV
                </Button>
                <Button data-testid="catalog-export" variant="outline" onClick={onExport} className="rounded-sm">
                  <Download className="h-4 w-4 mr-1.5" /> Exportar CSV
                </Button>
                <Button data-testid="catalog-new" onClick={() => setCreating(true)}
                        className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm">
                  <Plus className="h-4 w-4 mr-1.5" /> Nuevo producto
                </Button>
              </>
            )}
          </div>
        </div>

        {/* Filters */}
        <div className="bg-white border border-[#E9E6DC] rounded-sm p-3 grid grid-cols-1 sm:grid-cols-5 gap-2">
          <div className="sm:col-span-2 relative">
            <Search className="h-4 w-4 absolute left-2.5 top-2.5 text-[#888888]" />
            <Input data-testid="catalog-search"
                   placeholder="Buscar por nombre, SKU o tag…"
                   value={filters.q}
                   onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value, offset: 0 }))}
                   className="rounded-sm h-9 pl-8" />
          </div>
          <Select value={filters.category}
                  onValueChange={(v) => setFilters((f) => ({ ...f, category: v, offset: 0 }))}>
            <SelectTrigger data-testid="catalog-filter-category" className="rounded-sm h-9 text-sm">
              <SelectValue placeholder="Categoría" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas las categorías</SelectItem>
              {(catsQ.data?.categories || []).map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={filters.stock_status}
                  onValueChange={(v) => setFilters((f) => ({ ...f, stock_status: v, offset: 0 }))}>
            <SelectTrigger data-testid="catalog-filter-stock" className="rounded-sm h-9 text-sm">
              <SelectValue placeholder="Stock" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todos los stocks</SelectItem>
              {STOCK_OPTS.map(o => <SelectItem key={o.v} value={o.v}>{o.l}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={filters.sort}
                  onValueChange={(v) => setFilters((f) => ({ ...f, sort: v }))}>
            <SelectTrigger data-testid="catalog-sort" className="rounded-sm h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="name">Nombre (A-Z)</SelectItem>
              <SelectItem value="-name">Nombre (Z-A)</SelectItem>
              <SelectItem value="price">Precio (menor)</SelectItem>
              <SelectItem value="-price">Precio (mayor)</SelectItem>
              <SelectItem value="-created_at">Más recientes</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Empty / table */}
        {items.length === 0 && !listQ.isPending ? (
          <div className="bg-latus-cream border border-dashed border-zinc-300 rounded-sm p-10 text-center">
            <Package className="h-7 w-7 text-[#0E8DDB] mx-auto mb-2" />
            <p className="text-sm font-bold text-[#0B1B26]">Aún no hay productos</p>
            <p className="text-xs text-[#888888] mt-1">
              {canWrite
                ? "Agregá el primero o importá un CSV para empezar."
                : "Pediles a un administrador que cargue el catálogo."}
            </p>
          </div>
        ) : (
          <div className="bg-white border border-[#E9E6DC] rounded-sm overflow-hidden" data-testid="catalog-table">
            <table className="w-full text-sm">
              <thead className="bg-latus-cream text-[10px] uppercase tracking-wider text-[#888888]">
                <tr>
                  <th className="text-left px-3 py-2">Producto</th>
                  <th className="text-left px-3 py-2">SKU</th>
                  <th className="text-left px-3 py-2">Categoría</th>
                  <th className="text-right px-3 py-2">Precio</th>
                  <th className="text-left px-3 py-2">Stock</th>
                  <th className="text-right px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => {
                  const s = STOCK_COLOR[p.stock_status] || STOCK_COLOR.consultar;
                  return (
                    <tr key={p.product_id}
                        data-testid={`catalog-row-${p.product_id}`}
                        className="border-t border-[#E9E6DC] hover:bg-latus-cream cursor-pointer"
                        onClick={() => canWrite && setEditing(p)}>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          {p.image_url ? (
                            <img src={p.image_url} alt="" className="h-8 w-8 object-cover rounded-sm" />
                          ) : (
                            <div className="h-8 w-8 bg-latus-warm-gray rounded-sm flex items-center justify-center">
                              <Package className="h-3.5 w-3.5 text-[#888888]" />
                            </div>
                          )}
                          <div>
                            <p className="font-bold text-[#0B1B26]">{p.name}</p>
                            {Array.isArray(p.tags) && p.tags.length > 0 && (
                              <p className="text-[10px] text-[#888888]">{p.tags.join(" · ")}</p>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{p.sku || "—"}</td>
                      <td className="px-3 py-2">{p.category || "—"}</td>
                      <td className="px-3 py-2 text-right">
                        <span className={p.promo_price ? "line-through text-[#888888]" : "font-bold text-[#0B1B26]"}>
                          {fmtPrice(p.price, p.currency)}
                        </span>
                        {p.promo_price && (
                          <p className="text-xs font-bold text-[#0E8DDB]">{fmtPrice(p.promo_price, p.currency)}</p>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded-sm"
                              style={{ background: s.bg, color: s.color }}>
                          {STOCK_OPTS.find(o => o.v === p.stock_status)?.l || p.stock_status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right">
                        {canWrite && (
                          <Button data-testid={`catalog-edit-${p.product_id}`}
                                  variant="outline" size="sm" className="rounded-sm h-7"
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
            <div className="px-3 py-2 border-t border-[#E9E6DC] flex items-center justify-between text-xs">
              <p className="text-[#888888]">Mostrando {Math.min(filters.offset + 1, total)}–{Math.min(filters.offset + items.length, total)} de {total}</p>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" className="rounded-sm h-7"
                        disabled={filters.offset === 0}
                        onClick={() => setFilters((f) => ({ ...f, offset: Math.max(0, f.offset - 50) }))}>
                  Anterior
                </Button>
                <Button size="sm" variant="outline" className="rounded-sm h-7"
                        disabled={filters.offset + 50 >= total}
                        onClick={() => setFilters((f) => ({ ...f, offset: f.offset + 50 }))}>
                  Siguiente
                </Button>
              </div>
            </div>
          </div>
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
  const [d, setD] = useState(product || {
    name: "", sku: "", category: "", description: "",
    price: "", currency: "ARS", stock_status: "disponible",
    active: true, tags: [], image_url: "", promo_price: "",
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
    if (payload.promo_price === "") delete payload.promo_price;
    save.mutate(payload);
  };

  const addTag = () => {
    const t = tagDraft.trim();
    if (!t) return;
    setD((x) => ({ ...x, tags: [...(x.tags || []), t] }));
    setTagDraft("");
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div data-testid="catalog-product-modal"
           className="bg-white rounded-sm max-w-3xl w-full max-h-[90vh] overflow-auto"
           onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-[#E9E6DC] flex items-center justify-between">
          <p className="text-base font-bold text-[#0B1B26]">
            {isEdit ? "Editar producto" : "Nuevo producto"}
          </p>
          <button onClick={onClose} className="text-[#888888] hover:text-[#0B1B26]">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-5 grid grid-cols-1 sm:grid-cols-2 gap-3">
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
            <Input data-testid="modal-category" list="cat-suggestions" value={d.category || ""}
                   onChange={(e) => setD({ ...d, category: e.target.value })}
                   className="rounded-sm h-9 mt-1" />
            <datalist id="cat-suggestions">
              {categories.map(c => <option key={c} value={c} />)}
            </datalist>
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

        <div className="px-5 py-3 border-t border-[#E9E6DC] flex items-center justify-between">
          {isEdit ? (
            <Button data-testid="modal-delete" variant="outline" className="rounded-sm text-[#DC2626]"
                    onClick={() => { if (confirm("¿Eliminar este producto?")) del.mutate(); }}>
              <Trash2 className="h-3.5 w-3.5 mr-1" /> Eliminar
            </Button>
          ) : <div />}
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose} className="rounded-sm">Cancelar</Button>
            <Button data-testid="modal-save" onClick={onSave} disabled={save.isPending}
                    className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm">
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
    const headers = "name,sku,category,description,price,currency,stock_status,active,tags,image_url,promo_price,commercial_conditions,external_link\n";
    const a = document.createElement("a");
    a.href = "data:text/csv;charset=utf-8,\ufeff" + encodeURIComponent(headers);
    a.download = "plantilla_catalogo_latus.csv";
    a.click();
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div data-testid="catalog-import-modal"
           className="bg-white rounded-sm max-w-lg w-full"
           onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-[#E9E6DC] flex items-center justify-between">
          <p className="text-base font-bold text-[#0B1B26]">Importar catálogo desde CSV</p>
          <button onClick={onClose} className="text-[#888888] hover:text-[#0B1B26]">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-5 space-y-3">
          <div className="border-2 border-dashed border-zinc-300 rounded-sm p-6 text-center cursor-pointer"
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
