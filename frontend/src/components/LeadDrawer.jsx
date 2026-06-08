import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X, StickyNote, CheckSquare, Phone, Building2, Send } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { LEAD_STATUSES, PRIORITIES, money } from "@/lib/constants";
import { StatusBadge, Avatar } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export default function LeadDrawer({ leadId, onClose, users = [] }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");

  const { data: lead } = useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => api.get(`/leads/${leadId}`).then((r) => r.data),
    enabled: !!leadId,
  });

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/leads/${leadId}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead", leadId] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      toast.success("Lead actualizado");
    },
  });

  const addNote = useMutation({
    mutationFn: () => api.post("/notes", { lead_id: leadId, body: note }),
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["lead", leadId] });
      toast.success("Nota agregada");
    },
  });

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
                  <p className="text-xs font-bold uppercase tracking-wider text-[#888888]">Valor</p>
                  <p className="text-2xl font-extrabold tracking-tighter text-[#0B1B26] mt-1">{money(lead.value)}</p>
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

              {/* Tasks */}
              <div>
                <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-[#888888] mb-2"><CheckSquare className="h-3.5 w-3.5" /> Tareas</p>
                <div className="space-y-1.5">
                  {(lead.tasks || []).length === 0 && <p className="text-sm text-latus-muted">Aún no hay tareas.</p>}
                  {(lead.tasks || []).map((t) => (
                    <div key={t.id} className="flex items-center gap-2 text-sm border border-[#E9E6DC] rounded-sm px-3 py-2">
                      <span className={`h-2 w-2 rounded-full ${t.status === "done" ? "bg-[#064E3B]" : "bg-[#0E8DDB]"}`} />
                      <span className={t.status === "done" ? "line-through text-latus-muted" : "text-[#0B1B26]"}>{t.title}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Notes */}
              <div>
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
