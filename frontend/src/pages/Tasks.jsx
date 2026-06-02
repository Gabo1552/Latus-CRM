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
  const [form, setForm] = useState({ title: "", description: "", due_date: "", priority: "medium", assigned_to: "" });

  const { data: tasks = [] } = useQuery({ queryKey: ["tasks"], queryFn: () => api.get("/tasks").then((r) => r.data) });
  const { data: users = [] } = useQuery({ queryKey: ["users"], queryFn: () => api.get("/users").then((r) => r.data) });

  const create = useMutation({
    mutationFn: () => api.post("/tasks", { ...form, assigned_to: form.assigned_to || null, due_date: form.due_date || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      setOpen(false);
      setForm({ title: "", description: "", due_date: "", priority: "medium", assigned_to: "" });
      toast.success("Task created");
    },
  });
  const toggle = useMutation({
    mutationFn: ({ id, status }) => api.patch(`/tasks/${id}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });

  const userName = (id) => users.find((u) => u.user_id === id)?.name || "Unassigned";
  const filtered = tasks.filter((t) => (tab === "done" ? t.status === "done" : t.status !== "done"));

  return (
    <AppLayout
      title="Tasks & Reminders"
      actions={
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button data-testid="new-task-button" className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm font-semibold">
              <Plus className="h-4 w-4 mr-1" /> New Task
            </Button>
          </DialogTrigger>
          <DialogContent className="rounded-sm">
            <DialogHeader><DialogTitle className="font-heading">Create Task</DialogTitle></DialogHeader>
            <div className="space-y-3 py-2">
              <div><Label className="text-xs font-semibold">Title</Label><Input data-testid="task-title-input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="rounded-sm mt-1" /></div>
              <div><Label className="text-xs font-semibold">Description</Label><Textarea data-testid="task-desc-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="rounded-sm mt-1" /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label className="text-xs font-semibold">Due date</Label><Input data-testid="task-due-input" type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} className="rounded-sm mt-1" /></div>
                <div>
                  <Label className="text-xs font-semibold">Priority</Label>
                  <Select value={form.priority} onValueChange={(v) => setForm({ ...form, priority: v })}>
                    <SelectTrigger className="rounded-sm mt-1"><SelectValue /></SelectTrigger>
                    <SelectContent>{PRIORITIES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </div>
              <div>
                <Label className="text-xs font-semibold">Assign to</Label>
                <Select value={form.assigned_to} onValueChange={(v) => setForm({ ...form, assigned_to: v })}>
                  <SelectTrigger className="rounded-sm mt-1"><SelectValue placeholder="Me" /></SelectTrigger>
                  <SelectContent>{users.map((u) => <SelectItem key={u.user_id} value={u.user_id}>{u.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <Button data-testid="submit-task-button" disabled={!form.title || create.isPending} onClick={() => create.mutate()} className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm w-full font-semibold">Create Task</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      }
    >
      <div className="p-6 md:p-8 space-y-5 animate-in fade-in duration-300">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="bg-transparent border-b border-zinc-200 rounded-none h-auto p-0 gap-4">
            <TabsTrigger data-testid="tab-todo" value="todo" className="rounded-none border-b-2 border-transparent data-[state=active]:border-[#FF4500] data-[state=active]:bg-transparent data-[state=active]:shadow-none px-1 pb-3 font-semibold">To Do</TabsTrigger>
            <TabsTrigger data-testid="tab-done" value="done" className="rounded-none border-b-2 border-transparent data-[state=active]:border-[#FF4500] data-[state=active]:bg-transparent data-[state=active]:shadow-none px-1 pb-3 font-semibold">Completed</TabsTrigger>
          </TabsList>
        </Tabs>

        {filtered.length === 0 ? (
          <div className="bg-white border border-zinc-200 rounded-sm"><EmptyState icon={CheckSquare} title="Nothing here" subtitle="Create a task or reminder for your team." /></div>
        ) : (
          <div className="space-y-2">
            {filtered.map((t) => (
              <div key={t.id} data-testid={`task-row-${t.id}`} className="bg-white border border-zinc-200 rounded-sm p-4 flex items-center gap-4 hover:border-zinc-300 transition-colors">
                <button
                  onClick={() => toggle.mutate({ id: t.id, status: t.status === "done" ? "todo" : "done" })}
                  data-testid={`task-toggle-${t.id}`}
                  className={`h-5 w-5 rounded-sm border flex items-center justify-center shrink-0 transition-colors ${t.status === "done" ? "bg-[#064E3B] border-[#064E3B]" : "border-zinc-300 hover:border-[#FF4500]"}`}
                >
                  {t.status === "done" && <Check className="h-3.5 w-3.5 text-white" />}
                </button>
                <div className="flex-1 min-w-0">
                  <p className={`font-semibold ${t.status === "done" ? "line-through text-zinc-400" : "text-[#0A0A0A]"}`}>{t.title}</p>
                  {t.description && <p className="text-sm text-[#52525B] truncate">{t.description}</p>}
                  {t.lead?.title && <p className="text-xs text-[#FF4500] mt-0.5">Linked: {t.lead.title}</p>}
                </div>
                <div className="flex items-center gap-4 shrink-0">
                  {t.due_date && <span className="flex items-center gap-1.5 text-xs text-[#52525B]"><Calendar className="h-3.5 w-3.5" /> {t.due_date}</span>}
                  <PriorityDot value={t.priority} />
                  <span className="text-xs text-[#52525B] w-28 truncate text-right">{userName(t.assigned_to)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </AppLayout>
  );
}
