import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { endOfMonth, format, isSameDay, parseISO, startOfMonth } from "date-fns";
import { es } from "date-fns/locale";
import {
  Bot, BriefcaseBusiness, Calendar as CalendarIcon, CalendarClock, CheckCircle, Clock, MapPin, Pencil,
  Phone, Plus, Settings2, Trash2, User, Users, XCircle,
} from "lucide-react";
import AppLayout from "@/components/AppLayout";
import { Calendar } from "@/components/ui/calendar";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import WeeklyScheduleEditor, { cloneWeeklySchedule } from "@/components/WeeklyScheduleEditor";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import api from "@/lib/api";

const STATUS_META = {
  scheduled: { label: "Agendada", className: "border-amber-200 bg-amber-50 text-amber-700" },
  completed: { label: "Completada", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  cancelled: { label: "Cancelada", className: "border-red-200 bg-red-50 text-red-700" },
};

const newEventForm = (date, assignedTo) => ({
  title: "",
  description: "",
  location: "",
  event_type: "appointment",
  date: format(date, "yyyy-MM-dd"),
  start_time: "09:00",
  end_time: "09:30",
  status: "scheduled",
  assigned_to: assignedTo || "",
  service_id: "",
});

const mutationError = (error, fallback) =>
  toast.error(error.response?.data?.detail || fallback);

export default function Calendario() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const role = user?.role === "sales_agent" ? "agent" : user?.role;
  const canViewTeam = role === "admin" || role === "supervisor";
  const canConfigureAgenda = (user?.permissions || []).includes("configure_ai");
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [visibleMonth, setVisibleMonth] = useState(new Date());
  const [teamFilter, setTeamFilter] = useState(canViewTeam ? "all" : user?.user_id || "");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [availabilityOpen, setAvailabilityOpen] = useState(false);
  const [availabilityDraft, setAvailabilityDraft] = useState(null);
  const [editingAppointment, setEditingAppointment] = useState(null);
  const [form, setForm] = useState(() => newEventForm(new Date(), user?.user_id));

  const queryStart = startOfMonth(visibleMonth).toISOString();
  const queryEnd = endOfMonth(visibleMonth).toISOString();

  const { data: appointments = [], isLoading } = useQuery({
    queryKey: ["appointments", queryStart, queryEnd, teamFilter],
    queryFn: () => api.get("/appointments", {
      params: {
        start: queryStart,
        end: queryEnd,
        assigned_to: canViewTeam && teamFilter !== "all" ? teamFilter : undefined,
      },
    }).then((response) => response.data),
  });

  const { data: schedulingConfig } = useQuery({
    queryKey: ["calendar-scheduling-config"],
    queryFn: () => api.get("/calendar/scheduling-config").then((response) => response.data),
  });

  useEffect(() => {
    if (schedulingConfig?.availability && availabilityDraft === null) {
      setAvailabilityDraft({
        ...schedulingConfig.availability,
        weekly_schedule: cloneWeeklySchedule(schedulingConfig.availability.weekly_schedule),
      });
    }
  }, [schedulingConfig, availabilityDraft]);

  const { data: users = [] } = useQuery({
    queryKey: ["calendar-users"],
    queryFn: () => api.get("/users").then((response) => response.data),
    enabled: canViewTeam,
  });

  const activeUsers = useMemo(
    () => users.filter((member) => member.active !== false),
    [users],
  );
  const activeServices = useMemo(
    () => (schedulingConfig?.services || []).filter((service) => service.active !== false),
    [schedulingConfig],
  );

  const refreshAppointments = () => queryClient.invalidateQueries({ queryKey: ["appointments"] });

  const saveAppointment = useMutation({
    mutationFn: ({ id, payload }) => id
      ? api.patch(`/appointments/${id}`, payload)
      : api.post("/appointments", payload),
    onSuccess: (_, variables) => {
      const savedDate = parseISO(variables.payload.start_time);
      setSelectedDate(savedDate);
      setVisibleMonth(savedDate);
      refreshAppointments();
      setDialogOpen(false);
      setEditingAppointment(null);
      toast.success(variables.id ? "Evento actualizado" : "Evento creado");
    },
    onError: (error) => mutationError(error, "No se pudo guardar el evento"),
  });

  const updateStatus = useMutation({
    mutationFn: ({ id, status }) => api.patch(`/appointments/${id}`, { status }),
    onSuccess: () => {
      refreshAppointments();
      toast.success("Estado actualizado");
    },
    onError: (error) => mutationError(error, "No se pudo actualizar el estado"),
  });

  const deleteAppointment = useMutation({
    mutationFn: (id) => api.delete(`/appointments/${id}`),
    onSuccess: () => {
      refreshAppointments();
      toast.success("Evento eliminado");
    },
    onError: (error) => mutationError(error, "No se pudo eliminar el evento"),
  });

  const saveAvailability = useMutation({
    mutationFn: (payload) => api.patch("/calendar/availability", payload),
    onSuccess: (response) => {
      setAvailabilityDraft({
        ...response.data,
        weekly_schedule: cloneWeeklySchedule(response.data.weekly_schedule),
      });
      queryClient.invalidateQueries({ queryKey: ["calendar-scheduling-config"] });
      setAvailabilityOpen(false);
      toast.success("Disponibilidad guardada");
    },
    onError: (error) => mutationError(error, "No se pudo guardar la disponibilidad"),
  });

  const dayAppointments = useMemo(
    () => appointments
      .filter((appointment) => isSameDay(parseISO(appointment.start_time), selectedDate))
      .sort((left, right) => new Date(left.start_time) - new Date(right.start_time)),
    [appointments, selectedDate],
  );

  const appointmentDates = useMemo(
    () => appointments.map((appointment) => parseISO(appointment.start_time)),
    [appointments],
  );

  const selectedOwnerName = teamFilter === "all"
    ? "Todo el equipo"
    : activeUsers.find((member) => member.user_id === teamFilter)?.name || user?.name || "Mi calendario";

  const openCreateDialog = () => {
    const defaultOwner = canViewTeam && teamFilter !== "all" ? teamFilter : user?.user_id;
    setEditingAppointment(null);
    setForm({
      ...newEventForm(selectedDate, defaultOwner),
      service_id: activeServices[0]?.id || "",
    });
    setDialogOpen(true);
  };

  const openEditDialog = (appointment) => {
    const start = parseISO(appointment.start_time);
    const end = parseISO(appointment.end_time);
    setEditingAppointment(appointment);
    setForm({
      title: appointment.title || "",
      description: appointment.description || "",
      location: appointment.location || "",
      event_type: appointment.event_type || "appointment",
      date: format(start, "yyyy-MM-dd"),
      start_time: format(start, "HH:mm"),
      end_time: format(end, "HH:mm"),
      status: appointment.status || "scheduled",
      assigned_to: appointment.assigned_to || user?.user_id || "",
      service_id: appointment.service_id || activeServices[0]?.id || "",
    });
    setDialogOpen(true);
  };

  const submitForm = () => {
    if (!form.title.trim()) {
      toast.error("Ingresá un título");
      return;
    }
    const startTime = new Date(`${form.date}T${form.start_time}:00`);
    const endTime = new Date(`${form.date}T${form.end_time}:00`);
    if (Number.isNaN(startTime.getTime()) || Number.isNaN(endTime.getTime()) || endTime <= startTime) {
      toast.error("La hora de fin debe ser posterior a la hora de inicio");
      return;
    }
    saveAppointment.mutate({
      id: editingAppointment?.id,
      payload: {
        title: form.title.trim(),
        description: form.description.trim() || null,
        location: form.location.trim() || null,
        event_type: form.event_type,
        start_time: startTime.toISOString(),
        end_time: endTime.toISOString(),
        status: form.status,
        assigned_to: form.assigned_to || user?.user_id,
        service_id: form.event_type === "appointment" && schedulingConfig?.mode === "business"
          ? form.service_id || null
          : null,
      },
    });
  };

  const confirmDelete = (appointment) => {
    if (window.confirm(`¿Eliminar "${appointment.title}" del calendario?`)) {
      deleteAppointment.mutate(appointment.id);
    }
  };

  const openAvailability = () => {
    if (schedulingConfig?.availability) {
      setAvailabilityDraft({
        ...schedulingConfig.availability,
        weekly_schedule: cloneWeeklySchedule(schedulingConfig.availability.weekly_schedule),
      });
    }
    setAvailabilityOpen(true);
  };

  const headerActions = (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant="outline"
        onClick={openAvailability}
        className="border-latus-warm-border bg-white text-latus-ink"
      >
        <CalendarClock className="h-4 w-4 text-latus-blue" /> Mi disponibilidad
      </Button>
      {canViewTeam && (
        <div className="flex items-center gap-2 rounded-md border border-latus-warm-border bg-latus-surface px-2.5 py-1.5">
          <Users className="h-4 w-4 text-latus-blue" />
          <select
            value={teamFilter}
            onChange={(event) => setTeamFilter(event.target.value)}
            data-testid="calendar-user-filter"
            className="max-w-[170px] bg-transparent text-xs font-semibold text-latus-ink outline-none"
            aria-label="Filtrar calendario por usuario"
          >
            <option value="all">Todo el equipo</option>
            {activeUsers.map((member) => (
              <option key={member.user_id} value={member.user_id}>{member.name}</option>
            ))}
          </select>
        </div>
      )}
      <Button
        type="button"
        data-testid="new-calendar-event"
        onClick={openCreateDialog}
        className="bg-latus-blue font-semibold text-white hover:bg-latus-blue-deep"
      >
        <Plus className="h-4 w-4" /> Nuevo evento
      </Button>
    </div>
  );

  return (
    <AppLayout title="Calendario" actions={headerActions}>
      <div className="mx-auto flex h-full max-w-[1500px] flex-col gap-5 p-4 md:p-6 lg:flex-row">
        <aside className="shrink-0 space-y-5 lg:w-[370px]">
          <div className="latus-card p-4">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="text-xs font-bold uppercase tracking-[0.12em] text-latus-ink">Seleccionar fecha</h2>
              {canViewTeam && <span className="truncate text-xs text-latus-muted">{selectedOwnerName}</span>}
            </div>
            <Calendar
              mode="single"
              month={visibleMonth}
              onMonthChange={setVisibleMonth}
              selected={selectedDate}
              onSelect={(date) => date && setSelectedDate(date)}
              locale={es}
              modifiers={{ hasEvents: appointmentDates }}
              modifiersClassNames={{ hasEvents: "calendar-has-events" }}
              className="w-full"
              classNames={{
                day_selected: "bg-latus-blue text-white hover:bg-latus-blue hover:text-white focus:bg-latus-blue focus:text-white",
              }}
            />
          </div>

          <div className="latus-card border-latus-blue/20 bg-latus-ice/25 p-5" data-testid="calendar-settings-card">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white shadow-sm">
                <Settings2 className="h-4 w-4 text-latus-blue" />
              </div>
              <div className="min-w-0">
                <h3 className="text-sm font-bold text-latus-ink">Configuración de agenda</h3>
                <p className="mt-1 text-xs leading-relaxed text-latus-muted">
                  {schedulingConfig?.enabled
                    ? `Agenda activa · ${schedulingConfig.mode === "business" ? "servicios del local" : "citas con personas"}`
                    : "Definí tus días y horarios disponibles para recibir citas."}
                </p>
              </div>
            </div>
            <div className="mt-4 grid gap-2">
              <Button type="button" onClick={openAvailability} className="w-full bg-latus-blue text-white hover:bg-latus-blue-deep">
                <CalendarClock className="h-4 w-4" /> Configurar mi horario
              </Button>
              {canConfigureAgenda && (
                <Button type="button" variant="outline" asChild className="w-full border-latus-warm-border bg-white text-latus-ink">
                  <a href="/configuracion?tab=agenda">
                    <BriefcaseBusiness className="h-4 w-4" /> Personas, servicios y cupos
                  </a>
                </Button>
              )}
            </div>
          </div>

          <div className="latus-card p-5">
            <h3 className="text-xs font-bold uppercase tracking-[0.12em] text-latus-ink">Resumen del mes</h3>
            <div className="mt-4 grid grid-cols-3 gap-2">
              <div className="rounded-md bg-latus-ice/65 p-3">
                <p className="latus-editorial text-2xl text-[#123c58]">{appointments.length}</p>
                <p className="text-[11px] text-latus-muted">Total</p>
              </div>
              <div className="rounded-md bg-amber-50 p-3">
                <p className="latus-editorial text-2xl text-amber-700">{appointments.filter((item) => item.status === "scheduled").length}</p>
                <p className="text-[11px] text-latus-muted">Pendientes</p>
              </div>
              <div className="rounded-md bg-emerald-50 p-3">
                <p className="latus-editorial text-2xl text-emerald-700">{appointments.filter((item) => item.status === "completed").length}</p>
                <p className="text-[11px] text-latus-muted">Completadas</p>
              </div>
            </div>
            <p className="mt-4 text-xs leading-relaxed text-latus-muted">
              Los eventos creados por la IA y los cargados manualmente se muestran en la misma agenda.
            </p>
          </div>
        </aside>

        <section className="latus-card flex min-h-[560px] flex-1 flex-col overflow-hidden">
          <div className="flex flex-col justify-between gap-3 border-b border-latus-warm-border bg-latus-surface/80 p-5 sm:flex-row sm:items-center">
            <div>
              <h2 className="flex items-center gap-2 text-xl font-bold tracking-tight text-latus-ink">
                <CalendarIcon className="h-5 w-5 text-latus-blue" />
                {format(selectedDate, "EEEE d 'de' MMMM", { locale: es })}
              </h2>
              <p className="mt-1 text-sm text-latus-muted">
                {dayAppointments.length === 1 ? "1 elemento en la agenda" : `${dayAppointments.length} elementos en la agenda`}
              </p>
            </div>
            <Button type="button" variant="outline" onClick={openCreateDialog} className="border-latus-warm-border bg-white text-latus-ink">
              <Plus className="h-4 w-4" /> Agregar en este día
            </Button>
          </div>

          <div className="flex-1 space-y-3 overflow-auto bg-latus-cream/35 p-4 md:p-5">
            {isLoading ? (
              <div className="py-12 text-center text-sm text-latus-muted">Cargando agenda...</div>
            ) : dayAppointments.length === 0 ? (
              <div className="flex min-h-[380px] flex-col items-center justify-center text-center">
                <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-full bg-latus-ice/65">
                  <CalendarIcon className="h-6 w-6 text-latus-blue" />
                </div>
                <p className="font-semibold text-latus-ink">No hay citas ni eventos para este día</p>
                <p className="mt-1 max-w-sm text-sm text-latus-muted">Podés crear uno manualmente; las citas generadas por la IA también aparecerán acá.</p>
                <Button type="button" onClick={openCreateDialog} className="mt-4 bg-latus-ink text-white hover:bg-latus-ink-soft">
                  <Plus className="h-4 w-4" /> Crear evento
                </Button>
              </div>
            ) : (
              dayAppointments.map((appointment) => {
                const status = STATUS_META[appointment.status] || STATUS_META.scheduled;
                const duration = Math.max(0, Math.round((parseISO(appointment.end_time) - parseISO(appointment.start_time)) / 60000));
                const contact = appointment.lead?.contact || appointment.contact;
                return (
                  <article key={appointment.id} data-testid={`calendar-event-${appointment.id}`} className="rounded-lg border border-latus-warm-border bg-latus-surface p-4 shadow-[0_8px_22px_rgba(13,31,42,0.035)]">
                    <div className="flex flex-col justify-between gap-4 xl:flex-row xl:items-center">
                      <div className="min-w-0 space-y-3">
                        <div className="flex flex-wrap items-center gap-2.5">
                          <span className="rounded-md bg-latus-ice px-2.5 py-1 text-sm font-bold text-[#123c58]">
                            {format(parseISO(appointment.start_time), "HH:mm")}
                          </span>
                          <h3 className="truncate text-base font-bold text-latus-ink">{appointment.title}</h3>
                          <span className="rounded-full border border-latus-blue/20 bg-latus-ice/55 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-[#123c58]">
                            {appointment.event_type === "event" ? "Evento" : "Cita"}
                          </span>
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${status.className}`}>{status.label}</span>
                          <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${appointment.created_by_bot ? "border-violet-200 bg-violet-50 text-violet-700" : "border-latus-warm-border bg-white text-latus-muted"}`}>
                            {appointment.created_by_bot && <Bot className="h-3 w-3" />}
                            {appointment.created_by_bot ? "Creada por IA" : "Manual"}
                          </span>
                        </div>

                        {(appointment.description || appointment.location) && (
                          <div className="space-y-1 text-sm text-latus-muted">
                            {appointment.description && <p>{appointment.description}</p>}
                            {appointment.location && <p className="flex items-center gap-1.5"><MapPin className="h-3.5 w-3.5" />{appointment.location}</p>}
                          </div>
                        )}

                        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-latus-muted">
                          {contact?.name && <span className="flex items-center gap-1.5"><User className="h-3.5 w-3.5" />{contact.name}</span>}
                          {contact?.phone && <span className="flex items-center gap-1.5"><Phone className="h-3.5 w-3.5" />{contact.phone}</span>}
                          <span className="flex items-center gap-1.5"><Clock className="h-3.5 w-3.5" />{duration} min</span>
                          {appointment.service_name && <span className="flex items-center gap-1.5 font-semibold text-latus-ink"><BriefcaseBusiness className="h-3.5 w-3.5 text-latus-blue" />{appointment.service_name}</span>}
                          {appointment.assigned_user?.name && <span className="flex items-center gap-1.5 font-semibold text-latus-ink"><Users className="h-3.5 w-3.5 text-latus-blue" />{appointment.assigned_user.name}</span>}
                        </div>
                      </div>

                      <div className="flex shrink-0 flex-wrap items-center gap-2">
                        {appointment.status === "scheduled" && (
                          <>
                            <Button type="button" variant="outline" size="sm" onClick={() => updateStatus.mutate({ id: appointment.id, status: "completed" })} disabled={updateStatus.isPending} className="border-emerald-200 text-emerald-700 hover:bg-emerald-50">
                              <CheckCircle className="h-3.5 w-3.5" /> Completar
                            </Button>
                            <Button type="button" variant="outline" size="sm" onClick={() => updateStatus.mutate({ id: appointment.id, status: "cancelled" })} disabled={updateStatus.isPending} className="border-red-200 text-red-700 hover:bg-red-50">
                              <XCircle className="h-3.5 w-3.5" /> Cancelar
                            </Button>
                          </>
                        )}
                        <Button type="button" variant="outline" size="sm" data-testid={`edit-calendar-event-${appointment.id}`} onClick={() => openEditDialog(appointment)} className="border-latus-warm-border text-latus-ink">
                          <Pencil className="h-3.5 w-3.5" /> Editar
                        </Button>
                        <Button type="button" variant="ghost" size="icon" onClick={() => confirmDelete(appointment)} disabled={deleteAppointment.isPending} className="text-latus-muted hover:bg-red-50 hover:text-red-700" aria-label={`Eliminar ${appointment.title}`}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  </article>
                );
              })
            )}
          </div>
        </section>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto rounded-xl border-latus-warm-border bg-latus-surface">
          <DialogHeader>
            <DialogTitle className="text-xl text-latus-ink">{editingAppointment ? "Editar cita o evento" : "Nueva cita o evento"}</DialogTitle>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <Label className="text-xs font-semibold">Tipo</Label>
                <Select value={form.event_type} onValueChange={(value) => setForm((current) => ({ ...current, event_type: value }))}>
                  <SelectTrigger data-testid="calendar-event-type" className="mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="appointment">Cita con cliente</SelectItem>
                    <SelectItem value="event">Evento interno</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {canViewTeam && (
                <div>
                  <Label className="text-xs font-semibold">Calendario de</Label>
                  <Select value={form.assigned_to} onValueChange={(value) => setForm((current) => ({ ...current, assigned_to: value }))}>
                    <SelectTrigger data-testid="calendar-event-assignee" className="mt-1"><SelectValue placeholder="Seleccionar usuario" /></SelectTrigger>
                    <SelectContent>
                      {activeUsers.map((member) => <SelectItem key={member.user_id} value={member.user_id}>{member.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>

            <div>
              <Label htmlFor="calendar-title" className="text-xs font-semibold">Título</Label>
              <Input id="calendar-title" data-testid="calendar-event-title" value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} placeholder={form.event_type === "appointment" ? "Ej.: Reunión de seguimiento" : "Ej.: Revisión semanal"} className="mt-1" />
            </div>

            {form.event_type === "appointment" && schedulingConfig?.mode === "business" && (
              <div>
                <Label className="text-xs font-semibold">Servicio del local</Label>
                <Select value={form.service_id} onValueChange={(value) => setForm((current) => ({ ...current, service_id: value }))}>
                  <SelectTrigger data-testid="calendar-event-service" className="mt-1"><SelectValue placeholder="Seleccionar servicio" /></SelectTrigger>
                  <SelectContent>
                    {activeServices.map((service) => (
                      <SelectItem key={service.id} value={service.id}>
                        {service.name} · {service.duration_minutes} min · cupo {service.max_concurrent}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {activeServices.length === 0 && (
                  <p className="mt-1 text-xs text-amber-700">Configurá al menos un servicio activo en Configuración → Bot IA.</p>
                )}
              </div>
            )}

            <div>
              <Label htmlFor="calendar-description" className="text-xs font-semibold">Descripción</Label>
              <Textarea id="calendar-description" data-testid="calendar-event-description" value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} placeholder="Notas, objetivo o información útil" className="mt-1 min-h-20" />
            </div>

            <div>
              <Label htmlFor="calendar-location" className="text-xs font-semibold">Ubicación o enlace</Label>
              <Input id="calendar-location" data-testid="calendar-event-location" value={form.location} onChange={(event) => setForm((current) => ({ ...current, location: event.target.value }))} placeholder="Oficina, teléfono o enlace de videollamada" className="mt-1" />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <Label htmlFor="calendar-date" className="text-xs font-semibold">Fecha</Label>
                <Input id="calendar-date" data-testid="calendar-event-date" type="date" value={form.date} onChange={(event) => setForm((current) => ({ ...current, date: event.target.value }))} className="mt-1" />
              </div>
              <div>
                <Label htmlFor="calendar-start" className="text-xs font-semibold">Inicio</Label>
                <Input id="calendar-start" data-testid="calendar-event-start" type="time" value={form.start_time} onChange={(event) => setForm((current) => ({ ...current, start_time: event.target.value }))} className="mt-1" />
              </div>
              <div>
                <Label htmlFor="calendar-end" className="text-xs font-semibold">Fin</Label>
                <Input id="calendar-end" data-testid="calendar-event-end" type="time" value={form.end_time} onChange={(event) => setForm((current) => ({ ...current, end_time: event.target.value }))} className="mt-1" />
              </div>
            </div>

            <div>
              <Label className="text-xs font-semibold">Estado</Label>
              <Select value={form.status} onValueChange={(value) => setForm((current) => ({ ...current, status: value }))}>
                <SelectTrigger data-testid="calendar-event-status" className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="scheduled">Agendada</SelectItem>
                  <SelectItem value="completed">Completada</SelectItem>
                  <SelectItem value="cancelled">Cancelada</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>Cancelar</Button>
            <Button type="button" data-testid="save-calendar-event" onClick={submitForm} disabled={saveAppointment.isPending || !form.title.trim() || !form.assigned_to || (form.event_type === "appointment" && schedulingConfig?.mode === "business" && !form.service_id)} className="bg-latus-blue text-white hover:bg-latus-blue-deep">
              {saveAppointment.isPending ? "Guardando..." : editingAppointment ? "Guardar cambios" : "Crear evento"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={availabilityOpen} onOpenChange={setAvailabilityOpen}>
        <DialogContent className="max-h-[92vh] max-w-3xl overflow-y-auto rounded-xl border-latus-warm-border bg-latus-surface">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-xl text-latus-ink">
              <Settings2 className="h-5 w-5 text-latus-blue" /> Mi disponibilidad
            </DialogTitle>
          </DialogHeader>

          {availabilityDraft && (
            <div className="space-y-5 py-2">
              <div className="flex items-start justify-between gap-4 rounded-lg border border-latus-warm-border bg-latus-cream/40 p-4">
                <div>
                  <p className="text-sm font-bold text-latus-ink">Aceptar citas en mi agenda</p>
                  <p className="mt-1 text-xs leading-relaxed text-latus-muted">El bot y las citas manuales usarán estos horarios para evitar superposiciones.</p>
                </div>
                <Switch checked={!!availabilityDraft.enabled} onCheckedChange={(enabled) => setAvailabilityDraft((current) => ({ ...current, enabled }))} />
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div>
                  <Label className="text-xs font-semibold">Zona horaria</Label>
                  <Input value={availabilityDraft.timezone || "America/Argentina/Buenos_Aires"} onChange={(event) => setAvailabilityDraft((current) => ({ ...current, timezone: event.target.value }))} className="mt-1" />
                </div>
                <div>
                  <Label className="text-xs font-semibold">Duración habitual</Label>
                  <Input type="number" min="5" max="480" value={availabilityDraft.default_duration_minutes || 30} onChange={(event) => setAvailabilityDraft((current) => ({ ...current, default_duration_minutes: Number(event.target.value) }))} className="mt-1" />
                </div>
                <div>
                  <Label className="text-xs font-semibold">Separación entre citas</Label>
                  <Input type="number" min="0" max="120" value={availabilityDraft.buffer_minutes || 0} onChange={(event) => setAvailabilityDraft((current) => ({ ...current, buffer_minutes: Number(event.target.value) }))} className="mt-1" />
                </div>
              </div>

              <div>
                <div className="mb-2">
                  <p className="text-sm font-bold text-latus-ink">Horario semanal</p>
                  <p className="text-xs text-latus-muted">Podés crear varias franjas por día, por ejemplo mañana y tarde.</p>
                </div>
                <WeeklyScheduleEditor
                  value={availabilityDraft.weekly_schedule}
                  onChange={(weekly_schedule) => setAvailabilityDraft((current) => ({ ...current, weekly_schedule }))}
                  disabled={!availabilityDraft.enabled}
                />
              </div>
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setAvailabilityOpen(false)}>Cancelar</Button>
            <Button type="button" onClick={() => saveAvailability.mutate(availabilityDraft)} disabled={!availabilityDraft || saveAvailability.isPending} className="bg-latus-blue text-white hover:bg-latus-blue-deep">
              {saveAvailability.isPending ? "Guardando..." : "Guardar disponibilidad"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}
