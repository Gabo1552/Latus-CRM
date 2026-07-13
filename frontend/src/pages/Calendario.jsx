import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { format, isSameDay, parseISO, startOfMonth, endOfMonth } from "date-fns";
import { es } from "date-fns/locale";
import { Calendar as CalendarIcon, Clock, User, Phone, CheckCircle, XCircle } from "lucide-react";
import AppLayout from "@/components/AppLayout";
import { Calendar } from "@/components/ui/calendar";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import api from "@/lib/api";

function Calendario() {
  const qc = useQueryClient();
  const [selectedDate, setSelectedDate] = useState(new Date());
  
  // Rango para cargar citas (podríamos cargar el mes entero por defecto)
  const queryStart = startOfMonth(selectedDate).toISOString();
  const queryEnd = endOfMonth(selectedDate).toISOString();

  const { data: appointments = [], isLoading } = useQuery({
    queryKey: ["appointments", queryStart, queryEnd],
    queryFn: () => api.get(`/appointments?start=${queryStart}&end=${queryEnd}`).then(r => r.data),
  });

  const updateStatus = useMutation({
    mutationFn: ({ id, status }) => api.patch(`/appointments/${id}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["appointments"] });
      toast.success("Estado actualizado");
    },
    onError: () => toast.error("Error al actualizar la cita")
  });

  // Filtrar citas del día seleccionado
  const dayAppointments = appointments.filter(a => isSameDay(parseISO(a.start_time), selectedDate));
  
  // Ordenar por hora de inicio
  dayAppointments.sort((a, b) => new Date(a.start_time) - new Date(b.start_time));

  return (
    <AppLayout title="Calendario">
      <div className="p-6 h-full flex flex-col md:flex-row gap-6 max-w-6xl mx-auto">
        
        {/* Panel izquierdo: Calendario Mensual */}
        <div className="md:w-[350px] shrink-0">
          <div className="bg-white border border-[#E9E6DC] rounded-sm p-4 shadow-sm">
            <h2 className="text-sm font-bold text-[#0B1B26] uppercase tracking-wide mb-4">Seleccionar Fecha</h2>
            <Calendar
              mode="single"
              selected={selectedDate}
              onSelect={(d) => d && setSelectedDate(d)}
              locale={es}
              className="w-full"
              classNames={{
                day_selected: "bg-[#0E8DDB] text-white hover:bg-[#0E8DDB] hover:text-white focus:bg-[#0E8DDB] focus:text-white",
              }}
            />
          </div>
          
          <div className="mt-6 bg-[#FBFBFA] border border-[#E9E6DC] rounded-sm p-4">
            <h3 className="text-xs font-bold text-[#0B1B26] mb-2 uppercase tracking-wide">Resumen del Mes</h3>
            <div className="text-sm text-[#71717A]">
              Total citas este mes: <span className="font-bold text-[#0B1B26]">{appointments.length}</span>
            </div>
          </div>
        </div>

        {/* Panel derecho: Agenda del día */}
        <div className="flex-1 bg-white border border-[#E9E6DC] rounded-sm shadow-sm overflow-hidden flex flex-col">
          <div className="border-b border-[#E9E6DC] p-5 bg-[#FBFBFA]">
            <h2 className="text-xl font-bold tracking-tight text-[#0B1B26] flex items-center gap-2">
              <CalendarIcon className="h-5 w-5 text-[#0E8DDB]" />
              Agenda para el {format(selectedDate, "EEEE d 'de' MMMM", { locale: es })}
            </h2>
          </div>
          
          <div className="flex-1 overflow-auto p-5 space-y-4 bg-[#FAFAF9]">
            {isLoading ? (
              <div className="text-center text-sm text-[#888888] py-10">Cargando citas...</div>
            ) : dayAppointments.length === 0 ? (
              <div className="text-center py-20">
                <div className="h-12 w-12 bg-[#F4F2EC] rounded-full flex items-center justify-center mx-auto mb-3">
                  <CalendarIcon className="h-6 w-6 text-[#A19D93]" />
                </div>
                <p className="text-[#71717A] text-sm">No hay citas agendadas para este día.</p>
              </div>
            ) : (
              dayAppointments.map(appt => (
                <div key={appt.id} className="bg-white border border-[#E9E6DC] rounded-sm p-4 flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center shadow-sm">
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-bold text-[#0E8DDB] bg-[#F4F2EC] px-2 py-0.5 rounded-sm">
                        {format(parseISO(appt.start_time), "HH:mm")}
                      </span>
                      <h3 className="font-bold text-[#0B1B26] text-base">{appt.title}</h3>
                    </div>
                    
                    <div className="flex items-center gap-4 text-xs text-[#71717A]">
                      {appt.lead?.contact?.name && (
                        <div className="flex items-center gap-1.5">
                          <User className="h-3.5 w-3.5" />
                          {appt.lead.contact.name}
                        </div>
                      )}
                      {appt.lead?.contact?.phone && (
                        <div className="flex items-center gap-1.5">
                          <Phone className="h-3.5 w-3.5" />
                          {appt.lead.contact.phone}
                        </div>
                      )}
                      <div className="flex items-center gap-1.5">
                        <Clock className="h-3.5 w-3.5" />
                        {Math.round((parseISO(appt.end_time) - parseISO(appt.start_time)) / 60000)} min
                      </div>
                    </div>
                    
                    <div className="flex items-center gap-2 mt-1">
                      <span className={`text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded-sm border ${
                        appt.status === "scheduled" ? "bg-amber-50 text-amber-600 border-amber-200" :
                        appt.status === "completed" ? "bg-green-50 text-green-600 border-green-200" :
                        "bg-red-50 text-red-600 border-red-200"
                      }`}>
                        {appt.status === "scheduled" ? "Agendada" : appt.status === "completed" ? "Completada" : "Cancelada"}
                      </span>
                      {appt.created_by_bot && (
                        <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded-sm bg-purple-50 text-purple-600 border border-purple-200">
                          🤖 Bot
                        </span>
                      )}
                    </div>
                  </div>
                  
                  {appt.status === "scheduled" && (
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 text-xs text-green-600 border-green-200 hover:bg-green-50 hover:text-green-700"
                        onClick={() => updateStatus.mutate({ id: appt.id, status: "completed" })}
                        disabled={updateStatus.isPending}
                      >
                        <CheckCircle className="h-3.5 w-3.5 mr-1" /> Completar
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 text-xs text-red-600 border-red-200 hover:bg-red-50 hover:text-red-700"
                        onClick={() => updateStatus.mutate({ id: appt.id, status: "cancelled" })}
                        disabled={updateStatus.isPending}
                      >
                        <XCircle className="h-3.5 w-3.5 mr-1" /> Cancelar
                      </Button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
        
      </div>
    </AppLayout>
  );
}

export default Calendario;
