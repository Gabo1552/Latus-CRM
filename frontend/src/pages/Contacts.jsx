import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
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
import LeadDrawer from "@/components/LeadDrawer";

export default function Contacts() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", phone: "", email: "", company: "" });
  const [activeTab, setActiveTab] = useState("clients");
  const [selectedLeadId, setSelectedLeadId] = useState(null);

  const contactsQ = useQuery({ queryKey: ["contacts"], queryFn: () => api.get("/contacts").then((r) => r.data) });
  const leadsQ = useQuery({
    queryKey: ["leads", { status: "all", priority: "all", assigned_to: "all" }],
    queryFn: () => api.get("/leads").then((r) => r.data)
  });
  const { data: users = [] } = useQuery({ queryKey: ["users"], queryFn: () => api.get("/users").then((r) => r.data) });

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

  const filtered = listToRender.filter((c) =>
    !search || [c.name, c.company, c.phone].some((f) => f?.toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <AppLayout
      title="Clientes y Contactos"
      actions={
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
      }
    >
      <div className="p-6 md:p-8 space-y-5 animate-in fade-in duration-300">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="bg-transparent border-b border-[#E9E6DC] rounded-none h-auto p-0 gap-4">
              <TabsTrigger data-testid="tab-clients" value="clients" className="rounded-none border-b-2 border-transparent data-[state=active]:border-[#0E8DDB] data-[state=active]:bg-transparent data-[state=active]:shadow-none px-1 pb-3 font-semibold">Clientes</TabsTrigger>
              <TabsTrigger data-testid="tab-contacts" value="contacts" className="rounded-none border-b-2 border-transparent data-[state=active]:border-[#0E8DDB] data-[state=active]:bg-transparent data-[state=active]:shadow-none px-1 pb-3 font-semibold">Contactos</TabsTrigger>
            </TabsList>
          </Tabs>

          <div className="relative max-w-xs w-full sm:w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-latus-muted" />
            <Input data-testid="contacts-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar..." className="pl-9 rounded-sm bg-white" />
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
                onClick={() => {
                  if (c.lead) {
                    setSelectedLeadId(c.lead.id);
                  } else {
                    toast.error("Legajo no disponible");
                  }
                }}
                data-testid={`contact-card-${c.id}`}
                className="bg-white border border-[#E9E6DC] rounded-sm p-5 hover:border-zinc-300 transition-colors cursor-pointer"
              >
                <div className="flex items-center gap-3 mb-4">
                  <Avatar src={c.avatar} name={c.name} size={44} />
                  <div className="min-w-0">
                    <p className="font-bold text-[#0B1B26] truncate">{c.name}</p>
                    <p className="text-xs text-[#888888] truncate">{c.company}</p>
                  </div>
                </div>
                <div className="space-y-1.5 text-sm text-[#888888]">
                  <p className="flex items-center gap-2"><Phone className="h-3.5 w-3.5 text-[#0E8DDB]" /> {c.phone}</p>
                  {c.email && <p className="flex items-center gap-2 truncate"><Mail className="h-3.5 w-3.5 text-[#0E8DDB]" /> {c.email}</p>}
                </div>
                {c.lead && (
                  <div className="mt-3 pt-3 border-t border-[#E9E6DC] flex items-center justify-between text-xs text-[#888888]">
                    <span>Lead: {c.lead.title}</span>
                    <span className="font-bold text-[#0E8DDB]">Ver legajo &rarr;</span>
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

      {selectedLeadId && (
        <LeadDrawer
          leadId={selectedLeadId}
          onClose={() => {
            setSelectedLeadId(null);
            qc.invalidateQueries({ queryKey: ["contacts"] });
            qc.invalidateQueries({ queryKey: ["leads"] });
          }}
          users={users}
        />
      )}
    </AppLayout>
  );
}
