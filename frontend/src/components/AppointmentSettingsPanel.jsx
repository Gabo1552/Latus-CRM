import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BriefcaseBusiness, CalendarClock, Plus, Trash2, UserRound } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import WeeklyScheduleEditor, { cloneWeeklySchedule, DEFAULT_WEEKLY_SCHEDULE } from "@/components/WeeklyScheduleEditor";

const makeService = () => ({
  id: `servicio_${Date.now().toString(36)}`,
  name: "Nuevo servicio",
  description: "",
  active: true,
  duration_minutes: 30,
  max_concurrent: 1,
  timezone: "America/Argentina/Buenos_Aires",
  weekly_schedule: cloneWeeklySchedule(DEFAULT_WEEKLY_SCHEDULE),
  sort_order: Date.now(),
});

export default function AppointmentSettingsPanel({ draft, onChange, users = [] }) {
  const queryClient = useQueryClient();
  const mode = draft.appointment_mode || "people";
  const services = Array.isArray(draft.appointment_services) ? draft.appointment_services : [];
  const activeUsers = useMemo(() => users.filter((user) => user.active !== false), [users]);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [personDraft, setPersonDraft] = useState(null);

  const teamAvailabilityQ = useQuery({
    queryKey: ["calendar-team-availability"],
    queryFn: () => api.get("/calendar/team-availability").then((response) => response.data),
    enabled: !!draft.appointment_scheduling_enabled && mode === "people",
  });

  useEffect(() => {
    if (!selectedUserId && teamAvailabilityQ.data?.length) {
      setSelectedUserId(teamAvailabilityQ.data[0].user_id);
    }
  }, [selectedUserId, teamAvailabilityQ.data]);

  useEffect(() => {
    const selected = teamAvailabilityQ.data?.find((person) => person.user_id === selectedUserId);
    if (selected) {
      setPersonDraft({ ...selected, weekly_schedule: cloneWeeklySchedule(selected.weekly_schedule) });
    }
  }, [selectedUserId, teamAvailabilityQ.data]);

  const savePerson = useMutation({
    mutationFn: ({ userId, payload }) => api.patch(`/calendar/team-availability/${userId}`, payload),
    onSuccess: (response) => {
      setPersonDraft({ ...response.data, weekly_schedule: cloneWeeklySchedule(response.data.weekly_schedule) });
      queryClient.invalidateQueries({ queryKey: ["calendar-team-availability"] });
      toast.success("Horario de la persona guardado");
    },
    onError: (error) => toast.error(error.response?.data?.detail || "No se pudo guardar el horario"),
  });

  const setServices = (nextServices) => onChange({ appointment_services: nextServices });
  const updateService = (index, patch) => setServices(
    services.map((service, currentIndex) => currentIndex === index ? { ...service, ...patch } : service),
  );

  return (
    <div className="space-y-4 rounded-sm border border-[#E9E6DC] p-4 md:col-span-2">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Label className="text-sm font-bold text-[#0B1B26]">Agendamiento automático</Label>
          <p className="mt-0.5 text-xs text-[#888888]">El bot verifica horarios, reservas existentes y cupos antes de crear una cita.</p>
        </div>
        <Switch
          data-testid="bot-setting-appointment-enabled"
          checked={!!draft.appointment_scheduling_enabled}
          onCheckedChange={(appointment_scheduling_enabled) => onChange({ appointment_scheduling_enabled })}
        />
      </div>

      {!!draft.appointment_scheduling_enabled && (
        <div className="space-y-5 border-t border-[#E9E6DC] pt-4">
          <div className="grid gap-4 md:grid-cols-[1fr_1fr]">
            <div>
              <Label className="text-xs font-bold text-[#0B1B26]">¿Qué tipo de citas vas a recibir?</Label>
              <Select value={mode} onValueChange={(appointment_mode) => onChange({ appointment_mode })}>
                <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="people">Citas con personas</SelectItem>
                  <SelectItem value="business">Citas en el local</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-xs font-bold text-[#0B1B26]">Zona horaria general</Label>
              <Input
                value={draft.appointment_timezone || "America/Argentina/Buenos_Aires"}
                onChange={(event) => onChange({ appointment_timezone: event.target.value })}
                className="mt-1"
              />
            </div>
          </div>

          {mode === "people" ? (
            <div className="space-y-4 rounded-lg bg-latus-cream/50 p-4">
              <div className="flex items-start gap-3">
                <UserRound className="mt-0.5 h-5 w-5 text-latus-blue" />
                <div>
                  <p className="text-sm font-bold text-latus-ink">Disponibilidad por persona</p>
                  <p className="text-xs text-latus-muted">Cada integrante también puede editar su propio horario desde el Calendario.</p>
                </div>
              </div>

              <div>
                <Label className="text-xs font-semibold">Integrante</Label>
                <Select value={selectedUserId} onValueChange={setSelectedUserId}>
                  <SelectTrigger className="mt-1"><SelectValue placeholder="Seleccionar persona" /></SelectTrigger>
                  <SelectContent>
                    {activeUsers.map((person) => (
                      <SelectItem key={person.user_id} value={person.user_id}>{person.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {personDraft && (
                <div className="space-y-4">
                  <div className="grid gap-3 sm:grid-cols-[1fr_1fr_1fr]">
                    <div className="flex items-center justify-between rounded-md border border-latus-warm-border bg-white px-3 py-2">
                      <span className="text-xs font-semibold">Agenda habilitada</span>
                      <Switch checked={!!personDraft.enabled} onCheckedChange={(enabled) => setPersonDraft((current) => ({ ...current, enabled }))} />
                    </div>
                    <div>
                      <Label className="text-xs font-semibold">Duración habitual</Label>
                      <Input type="number" min="5" max="480" value={personDraft.default_duration_minutes || 30} onChange={(event) => setPersonDraft((current) => ({ ...current, default_duration_minutes: Number(event.target.value) }))} className="mt-1 h-9" />
                    </div>
                    <div>
                      <Label className="text-xs font-semibold">Separación</Label>
                      <Input type="number" min="0" max="120" value={personDraft.buffer_minutes || 0} onChange={(event) => setPersonDraft((current) => ({ ...current, buffer_minutes: Number(event.target.value) }))} className="mt-1 h-9" />
                    </div>
                  </div>
                  <WeeklyScheduleEditor value={personDraft.weekly_schedule} onChange={(weekly_schedule) => setPersonDraft((current) => ({ ...current, weekly_schedule }))} disabled={!personDraft.enabled} />
                  <div className="flex justify-end">
                    <Button type="button" onClick={() => savePerson.mutate({ userId: selectedUserId, payload: personDraft })} disabled={savePerson.isPending} className="bg-latus-blue text-white hover:bg-latus-blue-deep">
                      {savePerson.isPending ? "Guardando..." : "Guardar horario de la persona"}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                <div className="flex items-start gap-3">
                  <BriefcaseBusiness className="mt-0.5 h-5 w-5 text-latus-blue" />
                  <div>
                    <p className="text-sm font-bold text-latus-ink">Servicios del local</p>
                    <p className="text-xs text-latus-muted">Definí duración, cantidad simultánea y horarios para cada rubro o tarea.</p>
                  </div>
                </div>
                <Button type="button" variant="outline" onClick={() => setServices([...services, makeService()])} className="border-latus-warm-border bg-white">
                  <Plus className="h-4 w-4" /> Agregar servicio
                </Button>
              </div>

              {services.length === 0 ? (
                <div className="rounded-lg border border-dashed border-latus-warm-border p-8 text-center">
                  <CalendarClock className="mx-auto h-7 w-7 text-latus-blue" />
                  <p className="mt-2 text-sm font-semibold text-latus-ink">Todavía no hay servicios configurados</p>
                  <p className="mt-1 text-xs text-latus-muted">Creá el primero para que el bot pueda ofrecer turnos en el local.</p>
                </div>
              ) : services.map((service, index) => (
                <div key={service.id || index} className="space-y-4 rounded-lg border border-latus-warm-border bg-latus-cream/35 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <Switch checked={service.active !== false} onCheckedChange={(active) => updateService(index, { active })} />
                      <span className="text-xs font-bold text-latus-ink">{service.active !== false ? "Servicio activo" : "Servicio pausado"}</span>
                    </div>
                    <Button type="button" variant="ghost" size="icon" onClick={() => setServices(services.filter((_, currentIndex) => currentIndex !== index))} className="text-latus-muted hover:bg-red-50 hover:text-red-700" aria-label={`Eliminar ${service.name}`}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div>
                      <Label className="text-xs font-semibold">Nombre del servicio</Label>
                      <Input value={service.name || ""} onChange={(event) => updateService(index, { name: event.target.value })} className="mt-1" placeholder="Ej.: Corte de cabello" />
                    </div>
                    <div>
                      <Label className="text-xs font-semibold">Identificador para el bot</Label>
                      <Input value={service.id || ""} onChange={(event) => updateService(index, { id: event.target.value })} className="mt-1 font-mono text-xs" />
                    </div>
                  </div>
                  <Textarea value={service.description || ""} onChange={(event) => updateService(index, { description: event.target.value })} className="min-h-16" placeholder="Descripción breve para que el bot sepa cuándo ofrecerlo" />
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div>
                      <Label className="text-xs font-semibold">Duración (min)</Label>
                      <Input type="number" min="5" max="480" value={service.duration_minutes || 30} onChange={(event) => updateService(index, { duration_minutes: Number(event.target.value) })} className="mt-1" />
                    </div>
                    <div>
                      <Label className="text-xs font-semibold">Turnos simultáneos</Label>
                      <Input type="number" min="1" max="100" value={service.max_concurrent || 1} onChange={(event) => updateService(index, { max_concurrent: Number(event.target.value) })} className="mt-1" />
                    </div>
                    <div>
                      <Label className="text-xs font-semibold">Zona horaria</Label>
                      <Input value={service.timezone || draft.appointment_timezone || "America/Argentina/Buenos_Aires"} onChange={(event) => updateService(index, { timezone: event.target.value })} className="mt-1" />
                    </div>
                  </div>
                  <div>
                    <p className="mb-2 text-xs font-bold text-latus-ink">Horarios del servicio</p>
                    <WeeklyScheduleEditor value={service.weekly_schedule} onChange={(weekly_schedule) => updateService(index, { weekly_schedule })} disabled={service.active === false} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
