import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Shield, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { ROLES } from "@/lib/constants";
import { Avatar } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/context/AuthContext";

const roleStyle = {
  admin: { color: "#FF4500", bg: "#FFF7ED" },
  supervisor: { color: "#7C3AED", bg: "#F5F3FF" },
  sales_agent: { color: "#1D4ED8", bg: "#EFF6FF" },
};

export default function Admin() {
  const qc = useQueryClient();
  const { user: me } = useAuth();
  const { data: users = [] } = useQuery({ queryKey: ["users"], queryFn: () => api.get("/users").then((r) => r.data) });

  const update = useMutation({
    mutationFn: ({ id, body }) => api.patch(`/users/${id}`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast.success("User updated"); },
    onError: () => toast.error("Update failed"),
  });

  const reseed = useMutation({
    mutationFn: () => api.post("/seed"),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Demo data regenerated"); },
  });

  return (
    <AppLayout
      title="Admin · Team & Roles"
      actions={
        <Button data-testid="reseed-button" onClick={() => reseed.mutate()} disabled={reseed.isPending} variant="outline" className="rounded-sm font-semibold">
          <RefreshCw className={`h-4 w-4 mr-1 ${reseed.isPending ? "animate-spin" : ""}`} /> Regenerate Demo Data
        </Button>
      }
    >
      <div className="p-6 md:p-8 space-y-5 animate-in fade-in duration-300">
        <div className="flex items-center gap-3 bg-[#0A0A0A] text-white rounded-sm p-5">
          <Shield className="h-5 w-5 text-[#FF4500]" />
          <div>
            <p className="font-bold">Role-based access control</p>
            <p className="text-sm text-zinc-400">Admins manage roles. Supervisors oversee the team. Sales agents handle leads & chats.</p>
          </div>
        </div>

        <div className="bg-white border border-zinc-200 rounded-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-200 bg-zinc-50">
                {["Member", "Email", "Role", "Active"].map((h) => (
                  <th key={h} className="text-left px-5 py-3 text-xs tracking-[0.1em] uppercase font-bold text-[#52525B]">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100">
              {users.map((u) => {
                const rs = roleStyle[u.role] || roleStyle.sales_agent;
                const isSelf = u.user_id === me?.user_id;
                return (
                  <tr key={u.user_id} data-testid={`user-row-${u.user_id}`} className="hover:bg-zinc-50 transition-colors">
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <Avatar src={u.picture} name={u.name} size={32} />
                        <span className="font-semibold text-[#0A0A0A]">{u.name}{isSelf && <span className="ml-2 text-xs text-[#FF4500]">(you)</span>}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-[#52525B]">{u.email}</td>
                    <td className="px-5 py-3.5">
                      <Select value={u.role} onValueChange={(v) => update.mutate({ id: u.user_id, body: { role: v } })}>
                        <SelectTrigger data-testid={`role-select-${u.user_id}`} className="w-40 rounded-sm h-9" style={{ color: rs.color, backgroundColor: rs.bg }}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>{ROLES.map((r) => <SelectItem key={r.key} value={r.key}>{r.label}</SelectItem>)}</SelectContent>
                      </Select>
                    </td>
                    <td className="px-5 py-3.5">
                      <Switch
                        data-testid={`active-switch-${u.user_id}`}
                        checked={u.active}
                        disabled={isSelf}
                        onCheckedChange={(v) => update.mutate({ id: u.user_id, body: { role: u.role, active: v } })}
                        className="data-[state=checked]:bg-[#FF4500]"
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </AppLayout>
  );
}
