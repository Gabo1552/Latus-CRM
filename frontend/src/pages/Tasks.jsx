import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, CheckSquare, Calendar, Check } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { PRIORITIES } from "@/lib/constants";
import { PriorityDot, EmptyState } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function Tasks() {
  const qc = useQueryClient();
  const [tab, setTab] = useState("todo");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", due_date: "", priority: "medium", assigned_to: "", lead_id: "" });

  const { data: tasks = [] } = useQuery({ queryKey: ["tasks"], queryFn: () => api.get("/tasks").then((r) => r.data) });
  const { data: users = [] } = useQuery({ queryKey: ["users"], queryFn: () => api.get("/users").then((r) => r.data) });
  const leadsQ = useQuery({
    queryKey: ["leads", { status: "all", priority: "all", assigned_to: "all" }],
    queryFn: () => api.get("/leads").then((r) => r.data)
  });
  const leads = leadsQ.data || [];

  const create = useMutation({
    mutationFn: () => api.post("/tasks", { ...form, assigned_to: form.assigned_to || null, due_date: form.due_date || null, lead_id: form.lead_id || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      setOpen(false);
      setForm({ title: "", description: "", due_date: "", priority: "medium", assigned_to: "", lead_id: "" });
      toast.success("Tarea creada");
    },
  });
  const toggle = useMutation({
    mutationFn: ({ id, status }) => api.patch(`/tasks/${id}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });

  const userName = (id) => users.find((u) => u.user_id === id)?.name || "Sin asignar";
  const filtered = tasks.filter((t) => (tab === "done" ? t.status === "done" : t.status !== "done"));

  return (
    <AppLayout
      title="Tareas y recordatorios"
      actions={
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button data-testid="new-task-button" className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm font-semibold">
              <Plus className="h-4 w-4 mr-1" /> Nueva tarea
            </Button>
          </DialogTrigger>
          <DialogContent className="rounded-sm">
            <DialogHeader><DialogTitle className="font-heading">Crear tarea</DialogTitle></DialogHeader>
            <div className="space-y-3 py-2">
              <div><Label className="text-xs font-semibold">Título</Label><Input data-testid="task-title-input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="rounded-sm mt-1" /></div>
              <div><Label className="text-xs font-semibold">Descripción</Label><Textarea data-testid="task-desc-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="rounded-sm mt-1" /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label className="text-xs font-semibold">Vencimiento</Label><Input data-testid="task-due-input" type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} className="rounded-sm mt-1" /></div>
                <div>
                  <Label className="text-xs font-semibold">Prioridad</Label>
                  <Select value={form.priority} onValueChange={(v) => setForm({ ...form, priority: v })}>
                    <SelectTrigger className="rounded-sm mt-1"><SelectValue /></SelectTrigger>
                    <SelectContent>{PRIORITIES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </div>
              <div>
                <Label className="text-xs font-semibold">Asignar a</Label>
                <Select value={form.assigned_to} onValueChange={(v) => setForm({ ...form, assigned_to: v })}>
                  <SelectTrigger className="rounded-sm mt-1"><SelectValue placeholder="Yo" /></SelectTrigger>
                  <SelectContent>{users.map((u) => <SelectItem key={u.user_id} value={u.user_id}>{u.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs font-semibold">Vincular a Contacto / Cliente</Label>
                <Select value={form.lead_id || "none"} onValueChange={(v) => setForm({ ...form, lead_id: v === "none" ? "" : v })}>
                  <SelectTrigger className="rounded-sm mt-1"><SelectValue placeholder="Sin vincular (General)" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Sin vincular (General)</SelectItem>
                    {leads.map((l) => (
                      <SelectItem key={l.id} value={l.id}>
                        {l.contact?.name ? `${l.contact.name} (${l.title})` : l.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <Button data-testid="submit-task-button" disabled={!form.title || create.isPending} onClick={() => create.mutate()} className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm w-full font-semibold">Crear tarea</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      }
    >
      <div className="p-6 md:p-8 space-y-5 animate-in fade-in duration-300">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="bg-transparent border-b border-[#E9E6DC] rounded-none h-auto p-0 gap-4">
            <TabsTrigger data-testid="tab-todo" value="todo" className="rounded-none border-b-2 border-transparent data-[state=active]:border-[#0E8DDB] data-[state=active]:bg-transparent data-[state=active]:shadow-none px-1 pb-3 font-semibold">Pendientes</TabsTrigger>
            <TabsTrigger data-testid="tab-done" value="done" className="rounded-none border-b-2 border-transparent data-[state=active]:border-[#0E8DDB] data-[state=active]:bg-transparent data-[state=active]:shadow-none px-1 pb-3 font-semibold">Completadas</TabsTrigger>
          </TabsList>
        </Tabs>

        {filtered.length === 0 ? (
          <div className="bg-white border border-[#E9E6DC] rounded-sm"><EmptyState icon={CheckSquare} title="Nada por aquí" subtitle="Creá una tarea o recordatorio para tu equipo." /></div>
        ) : (
          <div className="space-y-2">
            {filtered.map((t) => (
              <div key={t.id} data-testid={`task-row-${t.id}`} className="bg-white border border-[#E9E6DC] rounded-sm p-4 flex items-center gap-4 hover:border-zinc-300 transition-colors">
                <button
                  onClick={() => toggle.mutate({ id: t.id, status: t.status === "done" ? "todo" : "done" })}
                  data-testid={`task-toggle-${t.id}`}
                  className={`h-5 w-5 rounded-sm border flex items-center justify-center shrink-0 transition-colors ${t.status === "done" ? "bg-[#064E3B] border-[#064E3B]" : "border-zinc-300 hover:border-[#0E8DDB]"}`}
                >
                  {t.status === "done" && <Check className="h-3.5 w-3.5 text-white" />}
                </button>
                <div className="flex-1 min-w-0">
                  <p className={`font-semibold ${t.status === "done" ? "line-through text-latus-muted" : "text-[#0B1B26]"}`}>{t.title}</p>
                  {t.description && <p className="text-sm text-[#888888] truncate">{t.description}</p>}
                  {t.lead && (
                    <p className="text-xs text-[#0E8DDB] mt-0.5">
                      Vinculado: {t.lead.contact?.name ? `${t.lead.contact.name} (${t.lead.title})` : t.lead.title}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-4 shrink-0">
                  {t.due_date && <span className="flex items-center gap-1.5 text-xs text-[#888888]"><Calendar className="h-3.5 w-3.5" /> {t.due_date}</span>}
                  <PriorityDot value={t.priority} />
                  <span className="text-xs text-[#888888] w-28 truncate text-right">{userName(t.assigned_to)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </AppLayout>
  );
}
