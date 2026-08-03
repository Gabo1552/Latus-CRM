import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus, Users, Search, Phone, Mail, Building2 } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { Avatar, EmptyState } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter,
} from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/context/AuthContext";
import { hasPermission } from "@/lib/permissions";

const leadSourceMeta = {
  "Meta Ads": { color: "#1D4ED8", bg: "#EFF6FF", label: "Meta Ads" },
  "WhatsApp": { color: "#15803D", bg: "#F0FDF4", label: "WhatsApp" },
  "Orgánico": { color: "#52525B", bg: "#F4F4F5", label: "Orgánico" },
};

export default function Contacts() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const canUse = hasPermission(user, "crm_use");
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", phone: "", email: "", company: "" });
  const [activeTab, setActiveTab] = useState("clients");
  const [sourceFilter, setSourceFilter] = useState("all");

  const contactsQ = useQuery({ queryKey: ["contacts"], queryFn: () => api.get("/contacts").then((r) => r.data) });
  const leadsQ = useQuery({
    queryKey: ["leads", { status: "all", priority: "all", assigned_to: "all" }],
    queryFn: () => api.get("/leads").then((r) => r.data)
  });

  const create = useMutation({
    mutationFn: () => api.post("/contacts", form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contacts"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      setOpen(false);
      setForm({ name: "", phone: "", email: "", company: "" });
      toast.success("Contacto agregado");
    },
    onError: () => toast.error("No se pudo agregar el contacto"),
  });

  const contacts = contactsQ.data || [];
  const leads = leadsQ.data || [];

  const contactsWithLeads = contacts.map((c) => {
    const linkedLead = leads.find((l) => l.contact_id === c.id);
    return { ...c, lead: linkedLead };
  });

  const clients = contactsWithLeads.filter((c) => {
    const status = c.lead?.status;
    return status === "proposal" || status === "won" || status === "lost";
  });

  const nonClients = contactsWithLeads.filter((c) => {
    const status = c.lead?.status;
    return !status || (status !== "proposal" && status !== "won" && status !== "lost");
  });

  const listToRender = activeTab === "clients" ? clients : nonClients;

  const filtered = listToRender.filter((c) => {
    const matchesSearch = !search || [c.name, c.company, c.phone].some((f) => f?.toLowerCase().includes(search.toLowerCase()));
    let matchesSource = true;
    if (sourceFilter === "meta_ads") {
      matchesSource = c.lead_source === "Meta Ads";
    } else if (sourceFilter === "organic") {
      matchesSource = c.lead_source === "Orgánico" || !c.lead_source;
    }
    return matchesSearch && matchesSource;
  });

  return (
    <AppLayout
      title="Clientes y Contactos"
      actions={canUse ? (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button data-testid="new-contact-button" className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm font-semibold">
              <Plus className="h-4 w-4 mr-1" /> Nuevo contacto
            </Button>
          </DialogTrigger>
          <DialogContent className="rounded-sm">
            <DialogHeader><DialogTitle className="font-heading">Agregar contacto</DialogTitle></DialogHeader>
            <div className="space-y-3 py-2">
              <div><Label className="text-xs font-semibold">Nombre</Label><Input data-testid="contact-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="rounded-sm mt-1" /></div>
              <div><Label className="text-xs font-semibold">Teléfono de WhatsApp</Label><Input data-testid="contact-phone-input" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+54 11 0000 0000" className="rounded-sm mt-1" /></div>
              <div><Label className="text-xs font-semibold">Email</Label><Input data-testid="contact-email-input" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className="rounded-sm mt-1" /></div>
              <div><Label className="text-xs font-semibold">Empresa</Label><Input data-testid="contact-company-input" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className="rounded-sm mt-1" /></div>
            </div>
            <DialogFooter>
              <Button data-testid="submit-contact-button" disabled={!form.name || !form.phone || create.isPending} onClick={() => create.mutate()} className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm w-full font-semibold">Agregar contacto</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    >
      <div className="p-6 md:p-8 space-y-5 animate-in fade-in duration-300">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="bg-transparent border-b border-[#E9E6DC] rounded-none h-auto p-0 gap-4">
              <TabsTrigger data-testid="tab-clients" value="clients" className="rounded-none border-b-2 border-transparent data-[state=active]:border-[#0E8DDB] data-[state=active]:bg-transparent data-[state=active]:shadow-none px-1 pb-3 font-semibold">Clientes</TabsTrigger>
              <TabsTrigger data-testid="tab-contacts" value="contacts" className="rounded-none border-b-2 border-transparent data-[state=active]:border-[#0E8DDB] data-[state=active]:bg-transparent data-[state=active]:shadow-none px-1 pb-3 font-semibold">Contactos</TabsTrigger>
            </TabsList>
          </Tabs>

          <div className="flex items-center gap-2 max-w-md w-full sm:w-auto">
            <Select value={sourceFilter} onValueChange={setSourceFilter}>
              <SelectTrigger className="w-40 rounded-sm bg-white h-9 text-xs">
                <SelectValue placeholder="Origen" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos</SelectItem>
                <SelectItem value="meta_ads">Solo Meta Ads</SelectItem>
                <SelectItem value="organic">Solo Orgánicos</SelectItem>
              </SelectContent>
            </Select>
            <div className="relative flex-1 sm:w-64">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-latus-muted" />
              <Input data-testid="contacts-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar..." className="pl-9 rounded-sm bg-white h-9" />
            </div>
          </div>
        </div>

        {filtered.length === 0 ? (
          <div className="bg-white border border-[#E9E6DC] rounded-sm">
            <EmptyState icon={Users} title={activeTab === "clients" ? "Sin clientes" : "Sin contactos"} subtitle="Cada contacto de WhatsApp vive acá." />
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((c) => (
              <div
                key={c.id}
                onClick={() => navigate(`/contacts/${c.id}`)}
                data-testid={`contact-card-${c.id}`}
                className="bg-white border border-[#E9E6DC] rounded-sm p-5 hover:border-zinc-300 transition-colors cursor-pointer"
              >
                <div className="flex items-start justify-between gap-3 mb-4">
                  <div className="flex items-center gap-3 min-w-0">
                    <Avatar src={c.avatar} name={c.name} size={44} />
                    <div className="min-w-0">
                      <p className="font-bold text-[#0B1B26] truncate">{c.name}</p>
                      <p className="text-xs text-[#888888] truncate">{c.company}</p>
                    </div>
                  </div>
                  {(() => {
                    const sourceMeta = leadSourceMeta[c.lead_source] || leadSourceMeta["Orgánico"];
                    return (
                      <span
                        className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold border shrink-0"
                        style={{ color: sourceMeta.color, backgroundColor: sourceMeta.bg, borderColor: sourceMeta.color + "33" }}
                      >
                        {sourceMeta.label}
                      </span>
                    );
                  })()}
                </div>
                <div className="space-y-1.5 text-sm text-[#888888]">
                  <p className="flex items-center gap-2"><Phone className="h-3.5 w-3.5 text-[#0E8DDB]" /> {c.phone}</p>
                  {c.email && <p className="flex items-center gap-2 truncate"><Mail className="h-3.5 w-3.5 text-[#0E8DDB]" /> {c.email}</p>}
                </div>
                {c.lead && (
                  <div className="mt-3 pt-3 border-t border-[#E9E6DC] flex items-center justify-between text-xs text-[#888888]">
                    <span>Lead: {c.lead.title}</span>
                    <span className="font-bold text-[#0E8DDB]">Ver ficha 360° &rarr;</span>
                  </div>
                )}
                {c.tags?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-4">
                    {c.tags.map((t) => <span key={t} className="text-xs font-semibold bg-latus-warm-gray text-[#888888] rounded-full px-2.5 py-0.5">{t}</span>)}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

    </AppLayout>
  );
}
