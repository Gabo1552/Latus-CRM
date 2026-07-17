import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X, StickyNote, CheckSquare, Phone, Building2, Send, Check } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { LEAD_STATUSES, PRIORITIES, money } from "@/lib/constants";
import { StatusBadge, Avatar } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export default function LeadDrawer({ leadId, onClose, users = [] }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  
  const [selectedCatalogProduct, setSelectedCatalogProduct] = useState("");
  const [manualName, setManualName] = useState("");
  const [manualPrice, setManualPrice] = useState("");
  const [manualQuantity, setManualQuantity] = useState(1);

  const { data: lead } = useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => api.get(`/leads/${leadId}`).then((r) => r.data),
    enabled: !!leadId,
  });

  const catalogQ = useQuery({
    queryKey: ["catalog-products"],
    queryFn: () => api.get("/catalog/products?limit=100").then((r) => r.data),
  });
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get("/settings").then((r) => r.data),
  });

  const tasksQ = useQuery({
    queryKey: ["tasks"],
    queryFn: () => api.get("/tasks").then((r) => r.data),
  });
  const taskStatuses = settingsQ.data?.task_statuses || [
    { key: "todo", label: "Pendiente", is_done: false },
    { key: "done", label: "Completada", is_done: true },
  ];
  const defaultOpenStatus = taskStatuses.find((status) => !status.is_done)?.key || taskStatuses[0]?.key || "todo";
  const defaultDoneStatus = taskStatuses.find((status) => status.is_done)?.key || taskStatuses.at(-1)?.key || "done";
  const isDoneStatus = (status) => taskStatuses.some((item) => item.key === status && item.is_done);
  const nextToggleStatus = (status) => isDoneStatus(status) ? defaultOpenStatus : defaultDoneStatus;

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/leads/${leadId}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead", leadId] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["contacts"] });
      toast.success("Lead actualizado");
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo actualizar el lead"),
  });

  const updateContactSource = useMutation({
    mutationFn: (source) => api.patch(`/contacts/${lead?.contact?.id}`, { lead_source: source }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead", leadId] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["contacts"] });
      toast.success("Origen de contacto actualizado");
    },
    onError: () => toast.error("No se pudo actualizar el origen del contacto"),
  });

  const addNote = useMutation({
    mutationFn: () => api.post("/notes", { lead_id: leadId, body: note }),
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["lead", leadId] });
      toast.success("Nota agregada");
    },
  });

  const toggleTask = useMutation({
    mutationFn: ({ id, status }) => api.patch(`/tasks/${id}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead", leadId] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
    },
  });

  const addTask = useMutation({
    mutationFn: () => api.post("/tasks", { title: taskTitle, lead_id: leadId, status: defaultOpenStatus }),
    onSuccess: () => {
      setTaskTitle("");
      qc.invalidateQueries({ queryKey: ["lead", leadId] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success("Tarea agregada");
    },
  });

  const saleLocked = lead?.status === "won";
  const displayedProducts = saleLocked
    ? (lead.sale_snapshot?.products || lead?.products || [])
    : (lead?.products || []);

  if (!leadId) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="lead-drawer">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full max-w-md bg-white h-full overflow-auto border-l border-[#E9E6DC] animate-in slide-in-from-right duration-300">
        {!lead ? (
          <div className="p-8 flex justify-center"><div className="h-8 w-8 border-2 border-[#0E8DDB] border-t-transparent rounded-full animate-spin" /></div>
        ) : (
          <>
            <div className="p-6 border-b border-[#E9E6DC] flex items-start justify-between">
              <div className="flex items-center gap-3">
                <Avatar src={lead.contact?.avatar} name={lead.contact?.name} size={44} />
                <div>
                  <h3 className="font-bold text-lg tracking-tight text-[#0B1B26]">{lead.title}</h3>
                  <p className="text-sm text-[#888888]">{lead.contact?.name}</p>
                </div>
              </div>
              <button onClick={onClose} data-testid="close-lead-drawer" className="text-latus-muted hover:text-[#0B1B26]"><X className="h-5 w-5" /></button>
            </div>

            <div className="p-6 space-y-5">
              <div className="flex items-center gap-4 text-sm text-[#888888]">
                <span className="flex items-center gap-1.5"><Phone className="h-3.5 w-3.5" /> {lead.contact?.phone}</span>
                <span className="flex items-center gap-1.5"><Building2 className="h-3.5 w-3.5" /> {lead.contact?.company}</span>
              </div>

              <div className="flex items-center gap-2 text-xs">
                <span className="text-[#888888] font-semibold shrink-0">Origen del lead:</span>
                <Select
                  value={lead.contact?.lead_source || "Orgánico"}
                  onValueChange={(val) => updateContactSource.mutate(val)}
                >
                  <SelectTrigger className="w-36 h-7 rounded-sm text-xs bg-white border border-[#E9E6DC]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Meta Ads">Meta Ads</SelectItem>
                    <SelectItem value="WhatsApp">WhatsApp</SelectItem>
                    <SelectItem value="Orgánico">Orgánico</SelectItem>
                    <SelectItem value="Recomendado">Recomendado</SelectItem>
                    <SelectItem value="Otro">Otro</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {lead.contact?.lead_source === "Meta Ads" && (
                <div className="border border-[#E9E6DC] bg-orange-50/20 rounded-sm p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] tracking-[0.15em] uppercase font-extrabold text-[#FF4500]">Origen Meta Ads</span>
                    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold bg-[#EFF6FF] text-[#1D4ED8] border border-[#1D4ED8]/20">
                      Vino por anuncio
                    </span>
                  </div>
                  {lead.contact.meta_ad_title && (
                    <div>
                      <p className="text-[10px] font-bold text-[#888888] uppercase">Título del anuncio</p>
                      <p className="text-sm font-semibold text-[#0B1B26]">{lead.contact.meta_ad_title}</p>
                    </div>
                  )}
                  {lead.contact.meta_ad_body && (
                    <div>
                      <p className="text-[10px] font-bold text-[#888888] uppercase">Texto del anuncio</p>
                      <p className="text-xs text-[#52525B] whitespace-pre-wrap">{lead.contact.meta_ad_body}</p>
                    </div>
                  )}
                  {lead.contact.meta_ad_image_url && (
                    <div>
                      <p className="text-[10px] font-bold text-[#888888] uppercase mb-1">Imagen del anuncio</p>
                      <img src={lead.contact.meta_ad_image_url} alt="Ad Visual" className="max-w-full h-auto rounded-sm border border-[#E9E6DC] object-cover max-h-40" />
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    {lead.contact.meta_ad_id && (
                      <div>
                        <p className="text-[10px] font-bold text-[#888888] uppercase">ID del anuncio</p>
                        <p className="text-[#52525B] font-mono text-[11px] truncate" title={lead.contact.meta_ad_id}>{lead.contact.meta_ad_id}</p>
                      </div>
                    )}
                    {lead.contact.meta_ad_url && (
                      <div>
                        <p className="text-[10px] font-bold text-[#888888] uppercase">Link al anuncio</p>
                        <a href={lead.contact.meta_ad_url} target="_blank" rel="noopener noreferrer" className="text-[#0E8DDB] hover:underline font-semibold block truncate">
                          Ver anuncio &rarr;
                        </a>
                      </div>
                    )}
                  </div>
                  {lead.contact.first_ad_message_at && (
                    <div>
                      <p className="text-[10px] font-bold text-[#888888] uppercase">Fecha del primer mensaje</p>
                      <p className="text-xs text-[#52525B]">{new Date(lead.contact.first_ad_message_at).toLocaleString("es-AR")}</p>
                    </div>
                  )}
                  {lead.contact.first_message_from_ad && (
                    <div>
                      <p className="text-[10px] font-bold text-[#888888] uppercase">Primer mensaje recibido</p>
                      <p className="text-xs bg-white border border-[#E9E6DC] p-2 rounded-sm text-[#0B1B26] italic">
                        "{lead.contact.first_message_from_ad}"
                      </p>
                    </div>
                  )}
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-[#888888] mb-1.5">Estado</p>
                  <Select value={lead.status} onValueChange={(v) => patch.mutate({ status: v })}>
                    <SelectTrigger data-testid="drawer-status-select" className="rounded-sm"><SelectValue /></SelectTrigger>
                    <SelectContent>{LEAD_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-[#888888] mb-1.5">Prioridad</p>
                  <Select value={lead.priority} onValueChange={(v) => patch.mutate({ priority: v })}>
                    <SelectTrigger className="rounded-sm"><SelectValue /></SelectTrigger>
                    <SelectContent>{PRIORITIES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="bg-latus-cream border border-[#E9E6DC] rounded-sm p-3">
                  <p className="text-xs font-bold uppercase tracking-wider text-[#888888]">Valor Total (Ticket)</p>
                  <p className="text-2xl font-extrabold tracking-tighter text-[#0B1B26] mt-1">{money(lead.closed_value ?? lead.value)}</p>
                  {saleLocked && <p className="mt-1 text-[10px] font-bold uppercase tracking-wider text-emerald-700">Registrado al cerrar la venta</p>}
                </div>
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-[#888888] mb-1.5">Responsable</p>
                  <Select value={lead.assigned_to || "unassigned"} onValueChange={(v) => patch.mutate({ assigned_to: v === "unassigned" ? null : v })}>
                    <SelectTrigger className="rounded-sm"><SelectValue placeholder="Asignar" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="unassigned">Sin asignar</SelectItem>
                      {users.map((u) => <SelectItem key={u.user_id} value={u.user_id}>{u.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* Products Section */}
              <div className="border-t border-[#E9E6DC] pt-4">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-[#888888]">Productos ({displayedProducts.length})</p>
                  {saleLocked && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-700">Precios congelados</span>}
                </div>
                {saleLocked && <p className="mb-3 text-xs leading-relaxed text-[#888888]">Estos importes pertenecen al cierre de la venta y no cambian aunque se actualice el catálogo.</p>}
                
                {/* List products already in lead */}
                <div className="space-y-1.5 mb-3">
                  {displayedProducts.length === 0 ? (
                    <p className="text-sm text-latus-muted">No hay productos agregados.</p>
                  ) : (
                    displayedProducts.map((p, idx) => (
                      <div key={idx} className="flex items-center justify-between border border-[#E9E6DC] bg-latus-cream rounded-sm px-3 py-2 text-sm">
                        <div className="min-w-0">
                          <p className="font-semibold text-[#0B1B26] truncate">{p.name}</p>
                          <p className="text-xs text-[#888888]">{p.quantity} x {money(p.unit_price ?? p.price)}</p>
                          {p.promotion_applied && <p className="text-[10px] font-bold uppercase text-[#0E8DDB]">Precio promocional aplicado</p>}
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-[#0B1B26]">{money(p.line_total ?? ((p.unit_price ?? p.price) * p.quantity))}</span>
                          {!saleLocked && <button
                            onClick={() => {
                              const updated = [...lead.products];
                              updated.splice(idx, 1);
                              patch.mutate({ products: updated });
                            }}
                            className="text-red-500 hover:text-red-700 text-xs font-semibold px-1"
                          >
                            Eliminar
                          </button>}
                        </div>
                      </div>
                    ))
                  )}
                </div>

                {/* Add product form */}
                {!saleLocked && <div className="border border-[#E9E6DC] rounded-sm p-3 space-y-3 bg-white">
                  <p className="text-xs font-bold text-[#0B1B26] uppercase">Agregar Producto</p>
                  
                  {/* Select from catalog */}
                  <div>
                    <Label className="text-[10px] font-bold uppercase text-[#888888]">Desde Catálogo</Label>
                    <div className="flex gap-2 mt-1">
                      <Select
                        value={selectedCatalogProduct || ""}
                        onValueChange={(val) => {
                          setSelectedCatalogProduct(val);
                          const prod = catalogQ.data?.items?.find((i) => i.product_id === val);
                          if (prod) {
                            setManualName(prod.name);
                            setManualPrice(String(prod.effective_price ?? prod.price));
                          }
                        }}
                      >
                        <SelectTrigger className="rounded-sm h-9 text-xs flex-1">
                          <SelectValue placeholder="Seleccionar del catálogo..." />
                        </SelectTrigger>
                        <SelectContent>
                          {(catalogQ.data?.items || [])
                            .filter((item) => item.active)
                            .map((item) => (
                              <SelectItem key={item.product_id} value={item.product_id}>
                                {item.name} ({money(item.effective_price ?? item.price)}{item.promo_active ? " · promo" : ""})
                              </SelectItem>
                            ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>

                  {/* Manual entry / confirm details */}
                  <div className="grid grid-cols-3 gap-2">
                    <div className="col-span-2">
                      <Label className="text-[10px] font-bold uppercase text-[#888888]">Nombre</Label>
                      <Input
                        value={manualName}
                        onChange={(e) => setManualName(e.target.value)}
                        placeholder="Ej.: Consultoría"
                        className="rounded-sm h-8 mt-1 text-xs"
                      />
                    </div>
                    <div>
                      <Label className="text-[10px] font-bold uppercase text-[#888888]">Precio</Label>
                      <Input
                        type="number"
                        value={manualPrice}
                        onChange={(e) => setManualPrice(e.target.value)}
                        placeholder="100"
                        className="rounded-sm h-8 mt-1 text-xs"
                      />
                    </div>
                  </div>

                  <div className="flex gap-2 items-center">
                    <div className="w-1/3">
                      <Label className="text-[10px] font-bold uppercase text-[#888888]">Cant.</Label>
                      <Input
                        type="number"
                        min="1"
                        value={manualQuantity}
                        onChange={(e) => setManualQuantity(parseInt(e.target.value) || 1)}
                        className="rounded-sm h-8 mt-1 text-xs"
                      />
                    </div>
                    <div className="w-2/3 pt-4">
                      <Button
                        disabled={!manualName.trim() || !manualPrice}
                        onClick={() => {
                          const catalogProduct = catalogQ.data?.items?.find((item) => item.product_id === selectedCatalogProduct);
                          const newProduct = {
                            id: selectedCatalogProduct || null,
                            name: manualName.trim(),
                            price: parseFloat(manualPrice) || 0.0,
                            quantity: manualQuantity,
                            currency: catalogProduct?.currency || "ARS",
                            list_price: catalogProduct?.price ?? (parseFloat(manualPrice) || 0.0),
                            promotion_applied: !!catalogProduct?.promo_active
                              && Math.abs(Number(manualPrice) - Number(catalogProduct?.promo_price)) < 0.000001,
                          };
                          const updated = [...(lead.products || []), newProduct];
                          patch.mutate({ products: updated });
                          // Reset input fields
                          setManualName("");
                          setManualPrice("");
                          setManualQuantity(1);
                          setSelectedCatalogProduct("");
                        }}
                        className="w-full bg-[#0B1B26] hover:bg-[#0E8DDB] rounded-sm h-8 text-xs font-semibold"
                      >
                        Agregar
                      </Button>
                    </div>
                  </div>
                </div>}
              </div>

              {/* Tasks */}
              <div className="border-t border-[#E9E6DC] pt-4 space-y-4">
                <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-[#888888] mb-1">
                  <CheckSquare className="h-3.5 w-3.5" /> Tareas
                </p>

                {/* Contact Specific Tasks */}
                <div className="space-y-2">
                  <p className="text-[10px] font-bold uppercase text-[#888888] tracking-wider">Tareas del contacto ({(tasksQ.data || []).filter((t) => t.lead_id === leadId).length})</p>
                  <div className="space-y-1.5">
                    {(() => {
                      const contactTasks = (tasksQ.data || []).filter((t) => t.lead_id === leadId);
                      if (contactTasks.length === 0) {
                        return <p className="text-xs text-latus-muted pl-1">No hay tareas específicas para este contacto.</p>;
                      }
                      return contactTasks.map((t) => (
                        <div key={t.id} className="flex items-center justify-between border border-[#E9E6DC] rounded-sm px-3 py-2 text-sm bg-white">
                          <div className="flex items-center gap-2 min-w-0">
                            <button
                              onClick={() => toggleTask.mutate({ id: t.id, status: nextToggleStatus(t.status) })}
                              className={`h-4.5 w-4.5 rounded-sm border flex items-center justify-center shrink-0 transition-colors ${isDoneStatus(t.status) ? "bg-[#064E3B] border-[#064E3B]" : "border-zinc-300 hover:border-[#0E8DDB]"}`}
                            >
                              {isDoneStatus(t.status) && <Check className="h-3 w-3 text-white" />}
                            </button>
                            <span className={isDoneStatus(t.status) ? "line-through text-latus-muted truncate" : "text-[#0B1B26] truncate"}>{t.title}</span>
                          </div>
                        </div>
                      ));
                    })()}
                  </div>

                  <div className="flex gap-2 mt-2">
                    <Input
                      placeholder="Nueva tarea puntual..."
                      value={taskTitle}
                      onChange={(e) => setTaskTitle(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && taskTitle.trim()) { addTask.mutate(); } }}
                      className="rounded-sm h-8 text-xs"
                    />
                    <Button
                      disabled={!taskTitle.trim() || addTask.isPending}
                      onClick={() => { if (taskTitle.trim()) addTask.mutate(); }}
                      className="bg-[#0B1B26] hover:bg-[#0E8DDB] rounded-sm h-8 text-xs shrink-0"
                    >
                      Agregar
                    </Button>
                  </div>
                </div>

                {/* General Tasks */}
                <div className="space-y-2 pt-2 border-t border-[#E9E6DC]/60">
                  <p className="text-[10px] font-bold uppercase text-[#888888] tracking-wider">Tareas generales ({(tasksQ.data || []).filter((t) => !t.lead_id).length})</p>
                  <div className="space-y-1.5 max-h-40 overflow-y-auto pr-1">
                    {(() => {
                      const generalTasks = (tasksQ.data || []).filter((t) => !t.lead_id);
                      if (generalTasks.length === 0) {
                        return <p className="text-xs text-latus-muted pl-1">No hay tareas generales.</p>;
                      }
                      return generalTasks.map((t) => (
                        <div key={t.id} className="flex items-center justify-between border border-[#E9E6DC]/80 rounded-sm px-3 py-1.5 text-xs bg-latus-cream/40">
                          <div className="flex items-center gap-2 min-w-0">
                            <button
                              onClick={() => toggleTask.mutate({ id: t.id, status: nextToggleStatus(t.status) })}
                              className={`h-4 w-4 rounded-sm border flex items-center justify-center shrink-0 transition-colors ${isDoneStatus(t.status) ? "bg-[#064E3B] border-[#064E3B]" : "border-zinc-300 hover:border-[#0E8DDB]"}`}
                            >
                              {isDoneStatus(t.status) && <Check className="h-2.5 w-2.5 text-white" />}
                            </button>
                            <span className={isDoneStatus(t.status) ? "line-through text-latus-muted truncate" : "text-[#0B1B26] truncate"}>{t.title}</span>
                          </div>
                        </div>
                      ));
                    })()}
                  </div>
                </div>
              </div>

              {/* Notes */}
              <div className="border-t border-[#E9E6DC] pt-4">
                <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-[#888888] mb-2"><StickyNote className="h-3.5 w-3.5" /> Notas internas</p>
                <div className="flex gap-2 mb-3">
                  <Textarea data-testid="note-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Agregar una nota interna…" className="rounded-sm text-sm min-h-[60px]" />
                </div>
                <Button data-testid="add-note-button" disabled={!note.trim() || addNote.isPending} onClick={() => addNote.mutate()} size="sm" className="bg-[#0B1B26] hover:bg-[#0E8DDB] rounded-sm w-full font-semibold">
                  <Send className="h-3.5 w-3.5 mr-1" /> Agregar nota
                </Button>
                <div className="space-y-2 mt-4">
                  {(lead.notes || []).map((n) => (
                    <div key={n.id} className="bg-latus-cream border border-[#E9E6DC] rounded-sm p-3">
                      <p className="text-sm text-[#0B1B26]">{n.body}</p>
                      <p className="text-xs text-[#888888] mt-1.5">{n.author_name}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
