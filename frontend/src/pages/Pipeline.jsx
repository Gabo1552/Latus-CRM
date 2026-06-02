import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { GripVertical } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { LEAD_STATUSES, money } from "@/lib/constants";
import { Avatar, PriorityDot } from "@/components/Bits";

export default function Pipeline() {
  const qc = useQueryClient();
  const [dragId, setDragId] = useState(null);
  const [overCol, setOverCol] = useState(null);

  const { data: leads = [] } = useQuery({ queryKey: ["leads", { status: "all", priority: "all", assigned_to: "all" }], queryFn: () => api.get("/leads").then((r) => r.data) });

  const move = useMutation({
    mutationFn: ({ id, status }) => api.patch(`/leads/${id}`, { status }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["leads"] }); toast.success("Etapa actualizada"); },
  });

  const onDrop = (status) => {
    if (dragId) {
      const lead = leads.find((l) => l.id === dragId);
      if (lead && lead.status !== status) move.mutate({ id: dragId, status });
    }
    setDragId(null);
    setOverCol(null);
  };

  return (
    <AppLayout title="Pipeline">
      <div className="p-6 md:p-8 h-full animate-in fade-in duration-300">
        <div className="flex gap-4 overflow-x-auto pb-4 h-[calc(100vh-9rem)] items-start">
          {LEAD_STATUSES.map((stage) => {
            const items = leads.filter((l) => l.status === stage.key);
            const total = items.reduce((s, l) => s + (l.value || 0), 0);
            return (
              <div
                key={stage.key}
                data-testid={`pipeline-col-${stage.key}`}
                onDragOver={(e) => { e.preventDefault(); setOverCol(stage.key); }}
                onDrop={() => onDrop(stage.key)}
                className={`w-72 shrink-0 rounded-sm bg-zinc-50 border transition-colors ${overCol === stage.key ? "border-[#FF4500]" : "border-zinc-200"}`}
              >
                <div className="p-4 border-b border-zinc-200 sticky top-0 bg-zinc-50 z-10">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: stage.color }} />
                      <span className="font-bold text-sm text-[#0A0A0A]">{stage.label}</span>
                      <span className="text-xs font-semibold text-[#52525B] bg-white border border-zinc-200 rounded-full px-2">{items.length}</span>
                    </div>
                  </div>
                  <p className="text-xs font-semibold text-[#52525B] mt-1">{money(total)}</p>
                </div>
                <div className="p-3 space-y-3 min-h-[120px]">
                  {items.map((l) => (
                    <div
                      key={l.id}
                      draggable
                      onDragStart={() => setDragId(l.id)}
                      onDragEnd={() => { setDragId(null); setOverCol(null); }}
                      data-testid={`pipeline-card-${l.id}`}
                      className={`group bg-white border border-zinc-200 rounded-sm p-3 cursor-grab active:cursor-grabbing hover:border-zinc-300 transition-colors ${dragId === l.id ? "opacity-50" : ""}`}
                    >
                      <div className="flex items-start gap-2">
                        <GripVertical className="h-4 w-4 text-zinc-300 group-hover:text-zinc-400 shrink-0 mt-0.5" />
                        <div className="flex-1 min-w-0">
                          <p className="font-semibold text-sm text-[#0A0A0A] truncate">{l.title}</p>
                          <div className="flex items-center gap-2 mt-2">
                            <Avatar src={l.contact?.avatar} name={l.contact?.name} size={20} />
                            <span className="text-xs text-[#52525B] truncate">{l.contact?.name}</span>
                          </div>
                          <div className="flex items-center justify-between mt-3">
                            <span className="font-bold text-sm text-[#FF4500]">{money(l.value)}</span>
                            <PriorityDot value={l.priority} />
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </AppLayout>
  );
}
