import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Bell, MessageSquare, ArrowRightLeft, AlarmClock, Clock, UserX, CheckCheck } from "lucide-react";
import api from "@/lib/api";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

const TYPE_META = {
  new_message: { icon: MessageSquare, color: "#0E8DDB", label: "Nuevo mensaje" },
  handoff_required: { icon: ArrowRightLeft, color: "#DC2626", label: "Requiere atención humana" },
  overdue_task: { icon: AlarmClock, color: "#DC2626", label: "Tarea vencida" },
  task_due_soon: { icon: Clock, color: "#EAB308", label: "Tarea próxima a vencer" },
  lead_no_response: { icon: UserX, color: "#7C3AED", label: "Lead sin respuesta" },
};

function timeAgo(iso) {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "ahora";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  return `hace ${Math.floor(s / 86400)} d`;
}

export default function NotificationBell() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const { data: countData } = useQuery({
    queryKey: ["notif-count"],
    queryFn: () => api.get("/notifications/unread-count").then((r) => r.data),
    refetchInterval: 20000,
  });
  const { data: notifs = [] } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get("/notifications").then((r) => r.data),
    refetchInterval: 20000,
  });

  const unread = countData?.count ?? 0;

  const markRead = useMutation({
    mutationFn: (id) => api.patch(`/notifications/${id}/read`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["notif-count"] });
    },
  });
  const markAll = useMutation({
    mutationFn: () => api.post("/notifications/read-all"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["notif-count"] });
    },
  });

  const handleClick = (n) => {
    if (!n.is_read) markRead.mutate(n.id);
    setOpen(false);
    if (n.related_entity_type === "conversation") navigate("/inbox", { state: { convId: n.related_entity_id } });
    else if (n.related_entity_type === "lead") navigate("/leads", { state: { leadId: n.related_entity_id } });
    else if (n.related_entity_type === "task") navigate("/tasks");
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button data-testid="notification-bell" className="relative h-10 w-10 flex items-center justify-center rounded-sm border border-[#E9E6DC] bg-white hover:border-zinc-300 transition-colors">
          <Bell className="h-[18px] w-[18px] text-[#0B1B26]" />
          {unread > 0 && (
            <span data-testid="notification-unread-count" className="absolute -top-1.5 -right-1.5 h-5 min-w-5 px-1 bg-[#0E8DDB] text-white text-[10px] font-bold rounded-full flex items-center justify-center">
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0 rounded-sm" data-testid="notification-dropdown">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#E9E6DC]">
          <p className="font-bold text-[#0B1B26]">Notificaciones</p>
          {unread > 0 && (
            <button data-testid="mark-all-read" onClick={() => markAll.mutate()} className="flex items-center gap-1 text-xs font-semibold text-[#0E8DDB] hover:underline">
              <CheckCheck className="h-3.5 w-3.5" /> Marcar todo como leído
            </button>
          )}
        </div>
        <div className="max-h-[420px] overflow-auto">
          {notifs.length === 0 ? (
            <div className="py-12 text-center">
              <Bell className="h-7 w-7 text-zinc-300 mx-auto mb-2" />
              <p className="text-sm text-[#888888]">Estás al día</p>
            </div>
          ) : (
            notifs.map((n) => {
              const meta = TYPE_META[n.type] || TYPE_META.new_message;
              const Icon = meta.icon;
              return (
                <button
                  key={n.id}
                  onClick={() => handleClick(n)}
                  data-testid={`notification-item-${n.id}`}
                  className={`w-full flex items-start gap-3 px-4 py-3 text-left border-b border-[#E9E6DC] transition-colors hover:bg-latus-cream ${n.is_read ? "opacity-60" : "bg-[#F4F2EC]/40"}`}
                >
                  <div className="h-8 w-8 rounded-sm flex items-center justify-center shrink-0 mt-0.5" style={{ backgroundColor: meta.color + "1A" }}>
                    <Icon className="h-4 w-4" style={{ color: meta.color }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-semibold text-sm text-[#0B1B26] truncate">{n.title}</p>
                      {!n.is_read && <span className="h-2 w-2 rounded-full bg-[#0E8DDB] shrink-0" />}
                    </div>
                    {n.body && <p className="text-xs text-[#888888] truncate mt-0.5">{n.body}</p>}
                    <p className="text-[11px] text-latus-muted mt-1">{timeAgo(n.created_at)}</p>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
