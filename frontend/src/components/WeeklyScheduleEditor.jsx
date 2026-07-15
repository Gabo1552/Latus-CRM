import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export const DEFAULT_WEEKLY_SCHEDULE = {
  0: [{ start: "09:00", end: "18:00" }],
  1: [{ start: "09:00", end: "18:00" }],
  2: [{ start: "09:00", end: "18:00" }],
  3: [{ start: "09:00", end: "18:00" }],
  4: [{ start: "09:00", end: "18:00" }],
  5: [],
  6: [],
};

const DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];

export const cloneWeeklySchedule = (value = DEFAULT_WEEKLY_SCHEDULE) =>
  Object.fromEntries(
    DAYS.map((_, day) => [String(day), (value?.[day] || value?.[String(day)] || []).map((window) => ({ ...window }))]),
  );

export default function WeeklyScheduleEditor({ value, onChange, disabled = false }) {
  const schedule = cloneWeeklySchedule(value);

  const updateDay = (day, windows) => onChange({ ...schedule, [String(day)]: windows });

  const toggleDay = (day) => {
    const windows = schedule[String(day)];
    updateDay(day, windows.length ? [] : [{ start: "09:00", end: "18:00" }]);
  };

  const updateWindow = (day, index, field, nextValue) => {
    const windows = schedule[String(day)].map((window, currentIndex) => (
      currentIndex === index ? { ...window, [field]: nextValue } : window
    ));
    updateDay(day, windows);
  };

  return (
    <div className="overflow-hidden rounded-lg border border-latus-warm-border bg-white">
      {DAYS.map((label, day) => {
        const windows = schedule[String(day)];
        const active = windows.length > 0;
        return (
          <div key={label} className="grid gap-3 border-b border-latus-warm-border p-3 last:border-b-0 md:grid-cols-[118px_1fr]">
            <button
              type="button"
              disabled={disabled}
              onClick={() => toggleDay(day)}
              className={`flex h-8 items-center justify-center rounded-md border px-3 text-xs font-bold transition-colors ${
                active
                  ? "border-latus-blue bg-latus-ice text-latus-blue-deep"
                  : "border-latus-warm-border bg-latus-cream/50 text-latus-muted"
              }`}
            >
              {label}
            </button>

            {active ? (
              <div className="space-y-2">
                {windows.map((window, index) => (
                  <div key={`${day}-${index}`} className="flex flex-wrap items-center gap-2">
                    <Input
                      type="time"
                      value={window.start}
                      disabled={disabled}
                      onChange={(event) => updateWindow(day, index, "start", event.target.value)}
                      className="h-8 w-[118px] text-xs"
                      aria-label={`Inicio ${label}`}
                    />
                    <span className="text-xs text-latus-muted">a</span>
                    <Input
                      type="time"
                      value={window.end}
                      disabled={disabled}
                      onChange={(event) => updateWindow(day, index, "end", event.target.value)}
                      className="h-8 w-[118px] text-xs"
                      aria-label={`Fin ${label}`}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      disabled={disabled}
                      onClick={() => updateDay(day, windows.filter((_, currentIndex) => currentIndex !== index))}
                      className="h-8 w-8 text-latus-muted hover:bg-red-50 hover:text-red-700"
                      aria-label={`Eliminar franja de ${label}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
                {windows.length < 3 && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={disabled}
                    onClick={() => updateDay(day, [...windows, { start: "14:00", end: "18:00" }])}
                    className="h-7 px-2 text-xs text-latus-blue"
                  >
                    <Plus className="h-3.5 w-3.5" /> Otra franja
                  </Button>
                )}
              </div>
            ) : (
              <div className="flex h-8 items-center text-xs text-latus-muted">No disponible</div>
            )}
          </div>
        );
      })}
    </div>
  );
}
