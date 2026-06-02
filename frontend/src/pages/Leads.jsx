import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Target, Search } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { LEAD_STATUSES, PRIORITIES, money, statusMeta } from "@/lib/constants";
import { StatusBadge, PriorityDot, Avatar, EmptyState } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import LeadDrawer from "@/components/LeadDrawer";

export default function Leads() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState({ status: "all", priority: "all", assigned_to: "all" });
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(null);

  const params = {};
  Object.entries(filters).forEach(([k, v]) => { if (v !== "all") params[k] = v; });

  const { data: leads = [] } = useQuery({
    queryKey: ["leads", filters],
    queryFn: () => api.get("/leads", { params }).then((r) => r.data),
  });
  const { data: users = [] } = useQuery({ queryKey: ["users"], queryFn: () => api.get("/users").then((r) => r.data) });
  const { data: contacts = [] } = useQuery({ queryKey: ["contacts"], queryFn: () => api.get("/contacts").then((r) => r.data) });

  const userName = (id) => users.find((u) => u.user_id === id)?.name || "Unassigned";

  const filtered = leads.filter((l) =>
    !search || l.title.toLowerCase().includes(search.toLowerCase()) || l.contact?.name?.toLowerCase().includes(search.toLowerCase())
  );

  const [form, setForm] = useState({ contact_id: "", title: "", status: "new", priority: "medium", value: "", assigned_to: "" });
  const createLead = useMutation({
    mutationFn: () => api.post("/leads", { ...form, value: parseFloat(form.value) || 0, assigned_to: form.assigned_to || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      setOpen(false);
      setForm({ contact_id: "", title: "", status: "new", priority: "medium", value: "", assigned_to: "" });
      toast.success("Lead created");
    },
    onError: () => toast.error("Could not create lead"),
  });

  return (
    <AppLayout
      title="Leads"
      actions={
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button data-testid="new-lead-button" className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm font-semibold">
              <Plus className="h-4 w-4 mr-1" /> New Lead
            </Button>
          </DialogTrigger>
          <DialogContent className="rounded-sm">
            <DialogHeader><DialogTitle className="font-heading">Create Lead</DialogTitle></DialogHeader>
            <div className="space-y-4 py-2">
              <div>
                <Label className="text-xs font-semibold">Contact</Label>
                <Select value={form.contact_id} onValueChange={(v) => setForm({ ...form, contact_id: v })}>
                  <SelectTrigger data-testid="lead-contact-select" className="rounded-sm mt-1"><SelectValue placeholder="Select contact" /></SelectTrigger>
                  <SelectContent>{contacts.map((c) => <SelectItem key={c.id} value={c.id}>{c.name} · {c.company}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs font-semibold">Title</Label>
                <Input data-testid="lead-title-input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. Wholesale order" className="rounded-sm mt-1" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs font-semibold">Status</Label>
                  <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                    <SelectTrigger className="rounded-sm mt-1"><SelectValue /></SelectTrigger>
                    <SelectContent>{LEAD_STATUSES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div>
                  <Label className="text-xs font-semibold">Priority</Label>
                  <Select value={form.priority} onValueChange={(v) => setForm({ ...form, priority: v })}>
                    <SelectTrigger className="rounded-sm mt-1"><SelectValue /></SelectTrigger>
                    <SelectContent>{PRIORITIES.map((s) => <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs font-semibold">Deal Value ($)</Label>
                  <Input data-testid="lead-value-input" type="number" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} placeholder="0" className="rounded-sm mt-1" />
                </div>
                <div>
                  <Label className="text-xs font-semibold">Assign to</Label>
                  <Select value={form.assigned_to} onValueChange={(v) => setForm({ ...form, assigned_to: v })}>
                    <SelectTrigger className="rounded-sm mt-1"><SelectValue placeholder="Agent" /></SelectTrigger>
                    <SelectContent>{users.map((u) => <SelectItem key={u.user_id} value={u.user_id}>{u.name}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button data-testid="submit-lead-button" disabled={!form.contact_id || !form.title || createLead.isPending} onClick={() => createLead.mutate()} className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm w-full font-semibold">
                Create Lead
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      }
    >
      <div className="p-6 md:p-8 space-y-5 animate-in fade-in duration-300">
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[200px] max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400" />
            <Input data-testid="leads-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search leads…" className="pl-9 rounded-sm bg-white" />
          </div>
          <FilterSelect testid="filter-status" value={filters.status} onChange={(v) => setFilters({ ...filters, status: v })} placeholder="All Statuses" options={LEAD_STATUSES} />
          <FilterSelect testid="filter-priority" value={filters.priority} onChange={(v) => setFilters({ ...filters, priority: v })} placeholder="All Priorities" options={PRIORITIES} />
          <Select value={filters.assigned_to} onValueChange={(v) => setFilters({ ...filters, assigned_to: v })}>
            <SelectTrigger data-testid="filter-assigned" className="w-44 rounded-sm bg-white"><SelectValue placeholder="All Agents" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Agents</SelectItem>
              {users.map((u) => <SelectItem key={u.user_id} value={u.user_id}>{u.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        {/* Table */}
        <div className="bg-white border border-zinc-200 rounded-sm overflow-hidden">
          {filtered.length === 0 ? (
            <EmptyState icon={Target} title="No leads found" subtitle="Adjust filters or create a new lead to get started." />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-200 bg-zinc-50">
                  {["Lead", "Status", "Priority", "Value", "Owner"].map((h) => (
                    <th key={h} className="text-left px-5 py-3 text-xs tracking-[0.1em] uppercase font-bold text-[#52525B]">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100">
                {filtered.map((l) => (
                  <tr key={l.id} onClick={() => setSelected(l.id)} className="hover:bg-zinc-50 cursor-pointer transition-colors" data-testid={`lead-row-${l.id}`}>
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <Avatar src={l.contact?.avatar} name={l.contact?.name} size={32} />
                        <div>
                          <p className="font-semibold text-[#0A0A0A]">{l.title}</p>
                          <p className="text-xs text-[#52525B]">{l.contact?.name} · {l.contact?.company}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3.5"><StatusBadge list={LEAD_STATUSES} value={l.status} /></td>
                    <td className="px-5 py-3.5"><PriorityDot value={l.priority} /></td>
                    <td className="px-5 py-3.5 font-bold text-[#0A0A0A]">{money(l.value)}</td>
                    <td className="px-5 py-3.5 text-[#52525B]">{userName(l.assigned_to)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <LeadDrawer leadId={selected} onClose={() => setSelected(null)} users={users} />
    </AppLayout>
  );
}

function FilterSelect({ value, onChange, placeholder, options, testid }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger data-testid={testid} className="w-44 rounded-sm bg-white"><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        <SelectItem value="all">{placeholder}</SelectItem>
        {options.map((o) => <SelectItem key={o.key} value={o.key}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}
