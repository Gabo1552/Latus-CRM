import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, Banknote, Bot, CalendarDays, CheckSquare2, ChevronRight,
  CircleDollarSign, Clock3, FileText, Mail, MapPin, MessageCircle,
  Package, Phone, ReceiptText, ShoppingBag, Sparkles, UserRound,
} from "lucide-react";

import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { Avatar, EmptyState } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { money } from "@/lib/constants";

const formatDate = (value, withTime = true) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("es-AR", withTime
    ? { dateStyle: "medium", timeStyle: "short" }
    : { dateStyle: "medium" });
};

const STATUS_LABELS = {
  new: "Nuevo", nuevo: "Nuevo", contacted: "Contactado", calificando: "Calificando",
  qualified: "Calificado", calificado: "Calificado", proposal: "Propuesta",
  propuesta_solicitada: "Propuesta solicitada", propuesta_enviada: "Propuesta enviada",
  negotiation: "Negociación", negociacion: "Negociación", won: "Ganado", ganado: "Ganado",
  lost: "Perdido", perdido: "Perdido", open: "Abierta", pending: "Pendiente",
  resolved: "Resuelta", closed: "Cerrada", scheduled: "Programado",
  completed: "Completado", cancelled: "Cancelado", draft: "Borrador",
  confirmed: "Confirmada", paid: "Pagada", partial: "Pago parcial", todo: "Pendiente",
  in_progress: "En curso", done: "Completada", received: "Recibido",
  sent: "Enviado", delivered: "Entregado", read: "Leído", failed: "Falló",
};

const CHANNEL_LABELS = {
  whatsapp: "WhatsApp", webchat: "Chat web", calendar: "Calendario", crm: "CRM",
};

const TYPE_META = {
  message: { icon: MessageCircle, label: "Mensaje", tone: "bg-sky-50 text-sky-700" },
  appointment: { icon: CalendarDays, label: "Turno", tone: "bg-violet-50 text-violet-700" },
  sale: { icon: ShoppingBag, label: "Venta", tone: "bg-emerald-50 text-emerald-700" },
  payment: { icon: Banknote, label: "Pago", tone: "bg-teal-50 text-teal-700" },
  note: { icon: FileText, label: "Nota", tone: "bg-amber-50 text-amber-700" },
  task: { icon: CheckSquare2, label: "Tarea", tone: "bg-orange-50 text-orange-700" },
  bot_event: { icon: Bot, label: "Bot", tone: "bg-indigo-50 text-indigo-700" },
};

function StatusPill({ value }) {
  if (!value) return null;
  return (
    <span className="inline-flex rounded-full border border-latus-warm-border bg-latus-surface px-2 py-0.5 text-[11px] font-bold text-latus-muted">
      {STATUS_LABELS[value] || String(value).replaceAll("_", " ")}
    </span>
  );
}

function Metric({ icon: Icon, label, value, help, tone }) {
  return (
    <div className="rounded-xl border border-latus-warm-border bg-white p-4 shadow-[0_10px_28px_rgba(13,31,42,0.04)]">
      <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${tone}`}><Icon className="h-4 w-4" /></div>
      <p className="mt-3 text-[11px] font-extrabold uppercase tracking-[0.12em] text-latus-muted">{label}</p>
      <p className="mt-1 text-xl font-extrabold text-latus-ink">{value}</p>
      {help && <p className="mt-1 text-xs text-latus-muted">{help}</p>}
    </div>
  );
}

function SectionEmpty({ icon, title, subtitle }) {
  return <EmptyState icon={icon} title={title} subtitle={subtitle} />;
}

function Timeline({ events }) {
  if (!events.length) return <SectionEmpty icon={Clock3} title="Todavía no hay actividad" subtitle="Los mensajes, turnos, ventas y acciones del bot aparecerán acá." />;
  return (
    <div className="divide-y divide-latus-warm-border">
      {events.map((event) => {
        const meta = TYPE_META[event.type] || TYPE_META.note;
        const Icon = meta.icon;
        return (
          <article key={event.id} className="flex gap-3 px-4 py-4 sm:px-5">
            <div className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${meta.tone}`}><Icon className="h-4 w-4" /></div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-bold text-latus-ink">{event.title}</p>
                    <StatusPill value={event.status} />
                  </div>
                  <p className="mt-0.5 text-[11px] font-bold uppercase tracking-wider text-latus-blue">{meta.label}{event.channel ? ` · ${CHANNEL_LABELS[event.channel] || event.channel}` : ""}</p>
                </div>
                <time className="shrink-0 text-xs text-latus-muted">{formatDate(event.occurred_at)}</time>
              </div>
              {event.description && <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-latus-muted">{event.description}</p>}
              {event.actor_name && <p className="mt-1 text-xs text-latus-muted">Por {event.actor_name}</p>}
            </div>
          </article>
        );
      })}
    </div>
  );
}

export default function Customer360() {
  const { contactId } = useParams();
  const navigate = useNavigate();
  const customerQ = useQuery({
    queryKey: ["customer-360", contactId],
    queryFn: () => api.get(`/contacts/${contactId}/360`).then((response) => response.data),
    enabled: !!contactId,
    staleTime: 30_000,
  });
  const usersQ = useQuery({ queryKey: ["users"], queryFn: () => api.get("/users").then((response) => response.data), staleTime: 60_000 });

  const data = customerQ.data;
  const userNames = useMemo(() => Object.fromEntries((usersQ.data || []).map((user) => [user.user_id, user.name])), [usersQ.data]);

  if (customerQ.isLoading) {
    return <AppLayout title="Ficha 360°"><div className="flex min-h-[60vh] items-center justify-center"><div className="h-9 w-9 animate-spin rounded-full border-2 border-latus-blue border-t-transparent" /></div></AppLayout>;
  }
  if (customerQ.isError || !data) {
    return (
      <AppLayout title="Ficha 360°">
        <div className="p-6"><div className="rounded-xl border border-rose-200 bg-white p-10 text-center"><p className="font-bold text-latus-ink">No pudimos cargar esta ficha</p><Button variant="outline" className="mt-4" onClick={() => navigate("/contacts")}>Volver a clientes</Button></div></div>
      </AppLayout>
    );
  }

  const { contact, summary, section_access: access } = data;
  const nextAppointment = summary.next_appointment;

  return (
    <AppLayout
      title="Ficha 360°"
      actions={<Button variant="outline" className="rounded-lg" onClick={() => navigate("/contacts")}><ArrowLeft className="mr-1.5 h-4 w-4" /> Clientes</Button>}
    >
      <div className="space-y-5 p-4 sm:p-6 lg:p-8" data-testid="customer-360-page">
        <section className="overflow-hidden rounded-2xl border border-latus-warm-border bg-gradient-to-br from-white via-latus-surface to-latus-ice/40 shadow-[0_18px_45px_rgba(13,31,42,0.06)]">
          <div className="flex flex-col gap-5 p-5 sm:p-6 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 items-center gap-4">
              <Avatar src={contact.avatar} name={contact.name} size={64} />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="truncate text-2xl font-extrabold text-latus-ink">{contact.name}</h1>
                  <span className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-extrabold text-sky-700">{contact.lead_source || "Orgánico"}</span>
                </div>
                <p className="mt-1 text-sm text-latus-muted">Cliente desde {formatDate(contact.created_at, false)}</p>
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-sm text-latus-muted">
                  <span className="flex items-center gap-1.5"><Phone className="h-4 w-4 text-latus-blue" />{contact.phone}</span>
                  {contact.email && <span className="flex items-center gap-1.5"><Mail className="h-4 w-4 text-latus-blue" />{contact.email}</span>}
                  {contact.company && <span className="flex items-center gap-1.5"><UserRound className="h-4 w-4 text-latus-blue" />{contact.company}</span>}
                </div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:flex">
              {access.inbox && <Button variant="outline" className="rounded-lg" onClick={() => navigate("/inbox")}><MessageCircle className="mr-1.5 h-4 w-4" /> Bandeja</Button>}
              {access.calendar && <Button variant="outline" className="rounded-lg" onClick={() => navigate("/calendario")}><CalendarDays className="mr-1.5 h-4 w-4" /> Agenda</Button>}
              <Button className="rounded-lg bg-latus-blue text-white hover:bg-latus-blue-deep" onClick={() => navigate("/ventas")}><ShoppingBag className="mr-1.5 h-4 w-4" /> Venta</Button>
            </div>
          </div>
          {contact.tags?.length > 0 && <div className="flex flex-wrap gap-2 border-t border-latus-warm-border bg-white/70 px-5 py-3 sm:px-6">{contact.tags.map((tag) => <span key={tag} className="rounded-full bg-latus-warm-gray px-2.5 py-1 text-xs font-bold text-latus-muted">{tag}</span>)}</div>}
        </section>

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric icon={CircleDollarSign} label="Valor histórico" value={money(summary.lifetime_value)} help={`${summary.sales_count} ventas confirmadas`} tone="bg-emerald-50 text-emerald-700" />
          <Metric icon={Banknote} label="Saldo pendiente" value={money(summary.balance_due)} help={`${money(summary.amount_paid)} ya abonado`} tone="bg-amber-50 text-amber-700" />
          <Metric icon={MessageCircle} label="Conversaciones" value={summary.conversations_count} help={summary.last_contact_at ? `Último contacto ${formatDate(summary.last_contact_at)}` : "Sin contactos recientes"} tone="bg-sky-50 text-sky-700" />
          <Metric icon={CalendarDays} label="Turnos" value={summary.appointments_count} help={nextAppointment ? `Próximo: ${formatDate(nextAppointment.start_time)}` : `${summary.completed_appointments} completados`} tone="bg-violet-50 text-violet-700" />
        </section>

        <Tabs defaultValue="summary" className="space-y-4">
          <div className="overflow-x-auto pb-1">
            <TabsList className="h-auto min-w-max justify-start rounded-xl border border-latus-warm-border bg-white p-1">
              <TabsTrigger value="summary" className="rounded-lg">Resumen</TabsTrigger>
              <TabsTrigger value="activity" className="rounded-lg">Actividad</TabsTrigger>
              {access.inbox && <TabsTrigger value="conversations" className="rounded-lg">Conversaciones</TabsTrigger>}
              {access.calendar && <TabsTrigger value="appointments" className="rounded-lg">Turnos</TabsTrigger>}
              <TabsTrigger value="sales" className="rounded-lg">Ventas y productos</TabsTrigger>
              <TabsTrigger value="work" className="rounded-lg">Tareas y notas</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="summary" className="grid gap-4 lg:grid-cols-3">
            <div className="rounded-xl border border-latus-warm-border bg-white p-5 lg:col-span-2">
              <div className="flex items-center justify-between"><div><p className="text-xs font-extrabold uppercase tracking-wider text-latus-blue">Actividad reciente</p><h2 className="mt-1 text-lg font-extrabold text-latus-ink">Todo el vínculo en un solo lugar</h2></div><Sparkles className="h-5 w-5 text-latus-blue" /></div>
              <div className="-mx-5 mt-3"><Timeline events={(data.timeline || []).slice(0, 8)} /></div>
            </div>
            <div className="space-y-4">
              <div className="rounded-xl border border-latus-warm-border bg-white p-5">
                <p className="text-xs font-extrabold uppercase tracking-wider text-latus-blue">Estado comercial</p>
                <div className="mt-3 space-y-3">
                  {(data.leads || []).map((lead) => <div key={lead.id} className="rounded-lg border border-latus-warm-border p-3"><div className="flex items-center justify-between gap-2"><p className="truncate text-sm font-bold text-latus-ink">{lead.title}</p><StatusPill value={lead.status} /></div><p className="mt-1 text-xs text-latus-muted">Responsable: {userNames[lead.assigned_to] || "Sin asignar"}</p></div>)}
                  {!data.leads?.length && <p className="text-sm text-latus-muted">No hay oportunidades visibles.</p>}
                </div>
              </div>
              <div className="rounded-xl border border-latus-warm-border bg-white p-5">
                <p className="text-xs font-extrabold uppercase tracking-wider text-latus-blue">Seguimiento</p>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <div className="rounded-lg bg-latus-surface p-3"><p className="text-xl font-extrabold text-latus-ink">{summary.pending_tasks}</p><p className="text-xs text-latus-muted">Tareas pendientes</p></div>
                  <div className="rounded-lg bg-latus-surface p-3"><p className="text-xl font-extrabold text-latus-ink">{summary.handoffs}</p><p className="text-xs text-latus-muted">Derivaciones</p></div>
                  <div className="rounded-lg bg-latus-surface p-3"><p className="text-xl font-extrabold text-latus-ink">{summary.bot_messages}</p><p className="text-xs text-latus-muted">Mensajes del bot</p></div>
                  <div className="rounded-lg bg-latus-surface p-3"><p className="text-xl font-extrabold text-latus-ink">{summary.unread_messages}</p><p className="text-xs text-latus-muted">Sin leer</p></div>
                </div>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="activity" className="overflow-hidden rounded-xl border border-latus-warm-border bg-white"><Timeline events={data.timeline || []} /></TabsContent>

          <TabsContent value="conversations" className="space-y-3">
            {!data.conversations?.length ? <div className="rounded-xl border border-latus-warm-border bg-white"><SectionEmpty icon={MessageCircle} title="Sin conversaciones visibles" subtitle="Cuando el cliente escriba por un canal conectado aparecerá acá." /></div> : data.conversations.map((conversation) => (
              <button key={conversation.id} type="button" onClick={() => navigate("/inbox")} className="flex w-full items-center gap-3 rounded-xl border border-latus-warm-border bg-white p-4 text-left transition hover:border-latus-blue/40">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sky-50 text-sky-700"><MessageCircle className="h-4 w-4" /></div>
                <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="font-bold capitalize text-latus-ink">{conversation.channel || "WhatsApp"}</p><StatusPill value={conversation.status} /></div><p className="mt-1 truncate text-sm text-latus-muted">{conversation.last_message || "Sin mensajes"}</p></div>
                <div className="text-right"><p className="text-xs text-latus-muted">{formatDate(conversation.last_message_at)}</p>{conversation.unread > 0 && <span className="mt-1 inline-flex rounded-full bg-latus-blue px-2 py-0.5 text-[10px] font-bold text-white">{conversation.unread} nuevos</span>}</div><ChevronRight className="h-4 w-4 text-latus-muted" />
              </button>
            ))}
          </TabsContent>

          <TabsContent value="appointments" className="grid gap-3 md:grid-cols-2">
            {!data.appointments?.length ? <div className="rounded-xl border border-latus-warm-border bg-white md:col-span-2"><SectionEmpty icon={CalendarDays} title="Sin turnos visibles" subtitle="Las citas y reuniones asociadas al cliente aparecerán acá." /></div> : data.appointments.map((appointment) => (
              <div key={appointment.id} className="rounded-xl border border-latus-warm-border bg-white p-4"><div className="flex items-start justify-between gap-3"><div><p className="font-bold text-latus-ink">{appointment.title || "Turno"}</p><p className="mt-1 text-sm font-semibold text-latus-blue">{formatDate(appointment.start_time)}</p></div><StatusPill value={appointment.status} /></div>{appointment.service_name && <p className="mt-3 flex items-center gap-2 text-sm text-latus-muted"><Sparkles className="h-4 w-4" />{appointment.service_name}</p>}{appointment.location && <p className="mt-2 flex items-center gap-2 text-sm text-latus-muted"><MapPin className="h-4 w-4" />{appointment.location}</p>}<p className="mt-2 text-xs text-latus-muted">Responsable: {userNames[appointment.assigned_to] || "Sin asignar"}</p></div>
            ))}
          </TabsContent>

          <TabsContent value="sales" className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-latus-warm-border bg-white p-5"><div className="flex items-center gap-2"><ReceiptText className="h-5 w-5 text-latus-blue" /><h2 className="font-extrabold text-latus-ink">Ventas y pagos</h2></div><div className="mt-4 space-y-3">{!data.sales?.length ? <p className="text-sm text-latus-muted">Todavía no hay ventas registradas.</p> : data.sales.map((sale) => <div key={sale.sale_id} className="rounded-lg border border-latus-warm-border p-3"><div className="flex items-center justify-between gap-3"><div><p className="font-bold text-latus-ink">{money(sale.total, sale.currency)}</p><p className="text-xs text-latus-muted">{formatDate(sale.confirmed_at || sale.created_at)}</p></div><StatusPill value={sale.status} /></div><div className="mt-2 flex justify-between text-xs text-latus-muted"><span>{sale.lines?.length || 0} productos</span><span>Saldo: {money(sale.balance_due, sale.currency)}</span></div></div>)}</div></div>
            <div className="rounded-xl border border-latus-warm-border bg-white p-5"><div className="flex items-center gap-2"><Package className="h-5 w-5 text-latus-blue" /><h2 className="font-extrabold text-latus-ink">Productos adquiridos</h2></div><div className="mt-4 space-y-3">{!data.products?.length ? <p className="text-sm text-latus-muted">No hay productos comprados.</p> : data.products.map((product) => <div key={product.product_id || product.name} className="flex items-center justify-between gap-3 rounded-lg bg-latus-surface p-3"><div className="min-w-0"><p className="truncate font-bold text-latus-ink">{product.name}</p><p className="text-xs text-latus-muted">Última compra: {formatDate(product.last_purchase_at, false)}</p></div><div className="text-right"><p className="font-extrabold text-latus-ink">× {product.quantity}</p><p className="text-xs text-latus-muted">{money(product.total_spent, product.currency)}</p></div></div>)}</div></div>
          </TabsContent>

          <TabsContent value="work" className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-latus-warm-border bg-white p-5"><div className="flex items-center gap-2"><CheckSquare2 className="h-5 w-5 text-latus-blue" /><h2 className="font-extrabold text-latus-ink">Tareas</h2></div><div className="mt-4 space-y-3">{!data.tasks?.length ? <p className="text-sm text-latus-muted">No hay tareas asociadas.</p> : data.tasks.map((task) => <div key={task.id} className="rounded-lg border border-latus-warm-border p-3"><div className="flex items-start justify-between gap-2"><p className="font-bold text-latus-ink">{task.title}</p><StatusPill value={task.status} /></div><p className="mt-1 text-xs text-latus-muted">Vence: {formatDate(task.due_date)}</p>{task.description && <p className="mt-2 text-sm text-latus-muted">{task.description}</p>}</div>)}</div></div>
            <div className="rounded-xl border border-latus-warm-border bg-white p-5"><div className="flex items-center gap-2"><FileText className="h-5 w-5 text-latus-blue" /><h2 className="font-extrabold text-latus-ink">Notas internas</h2></div>{contact.notes && <div className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{contact.notes}</div>}<div className="mt-4 space-y-3">{!data.notes?.length && !contact.notes ? <p className="text-sm text-latus-muted">No hay notas registradas.</p> : data.notes?.map((note) => <div key={note.id} className="rounded-lg border border-latus-warm-border p-3"><p className="whitespace-pre-wrap text-sm text-latus-ink">{note.body}</p><p className="mt-2 text-xs text-latus-muted">{note.author_name || "Equipo"} · {formatDate(note.created_at)}</p></div>)}</div></div>
          </TabsContent>
        </Tabs>
      </div>
    </AppLayout>
  );
}
