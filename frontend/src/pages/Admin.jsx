import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Shield, RefreshCw, Clock, Save } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import { ROLES } from "@/lib/constants";
import { Avatar } from "@/components/Bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/context/AuthContext";

const roleStyle = {
  admin: { color: "#0E8DDB", bg: "#F4F2EC" },
  supervisor: { color: "#7C3AED", bg: "#F5F3FF" },
  sales_agent: { color: "#1D4ED8", bg: "#EFF6FF" },
};

export default function Admin() {
  const qc = useQueryClient();
  const { user: me } = useAuth();
  const { data: users = [] } = useQuery({ queryKey: ["users"], queryFn: () => api.get("/users").then((r) => r.data) });
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: () => api.get("/settings").then((r) => r.data) });
  const { data: waStatus } = useQuery({
    queryKey: ["wa-status"],
    queryFn: () => api.get("/admin/whatsapp/status").then((r) => r.data).catch(() => null),
    refetchInterval: 30_000,
  });

  const [threshold, setThreshold] = useState(2);
  const [enabled, setEnabled] = useState(true);
  const [businessHours, setBusinessHours] = useState(false);
  const [bhStart, setBhStart] = useState("09:00");
  const [bhEnd, setBhEnd] = useState("18:00");
  const [bhDays, setBhDays] = useState([0, 1, 2, 3, 4]);
  const [bhTz, setBhTz] = useState("America/Argentina/Cordoba");

  useEffect(() => {
    if (settings) {
      setThreshold(settings.lead_no_response_threshold_hours);
      setEnabled(settings.lead_no_response_enabled);
      setBusinessHours(settings.lead_no_response_business_hours_only);
      if (settings.business_hours_start) setBhStart(settings.business_hours_start);
      if (settings.business_hours_end) setBhEnd(settings.business_hours_end);
      if (Array.isArray(settings.business_days)) setBhDays(settings.business_days);
      if (settings.business_timezone) setBhTz(settings.business_timezone);
    }
  }, [settings]);

  const toggleDay = (d) => {
    setBhDays((prev) => prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort());
  };

  const update = useMutation({
    mutationFn: ({ id, body }) => api.patch(`/users/${id}`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast.success("Usuario actualizado"); },
    onError: () => toast.error("No se pudo actualizar"),
  });

  const saveSettings = useMutation({
    mutationFn: () => api.patch("/settings", {
      lead_no_response_enabled: enabled,
      lead_no_response_threshold_hours: Number(threshold),
      lead_no_response_business_hours_only: businessHours,
      business_hours_start: bhStart,
      business_hours_end: bhEnd,
      business_days: bhDays,
      business_timezone: bhTz,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); toast.success("Configuración guardada"); },
    onError: () => toast.error("No se pudo guardar"),
  });

  const reseed = useMutation({
    mutationFn: () => api.post("/seed"),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Datos demo regenerados"); },
  });

  return (
    <AppLayout
      title="Administración · Equipo y roles"
      actions={
        <Button data-testid="reseed-button" onClick={() => reseed.mutate()} disabled={reseed.isPending} variant="outline" className="rounded-sm font-semibold">
          <RefreshCw className={`h-4 w-4 mr-1 ${reseed.isPending ? "animate-spin" : ""}`} /> Regenerar datos demo
        </Button>
      }
    >
      <div className="p-6 md:p-8 space-y-5 animate-in fade-in duration-300">
        <div className="flex items-center gap-3 bg-[#0B1B26] text-white rounded-sm p-5">
          <Shield className="h-5 w-5 text-[#0E8DDB]" />
          <div>
            <p className="font-bold">Control de acceso por roles</p>
            <p className="text-sm text-latus-muted">Los administradores gestionan roles. Los supervisores supervisan al equipo. Los agentes de ventas atienden leads y chats.</p>
          </div>
        </div>

        {/* Automatización: Lead sin respuesta */}
        <div className="bg-white border border-[#E9E6DC] rounded-sm p-5" data-testid="lead-no-response-settings">
          <div className="flex items-center gap-2 mb-1">
            <Clock className="h-4 w-4 text-[#0E8DDB]" />
            <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Automatización · Lead sin respuesta</h3>
          </div>
          <p className="text-sm text-[#888888] mb-4">Generá una alerta cuando un cliente escribe y no recibe respuesta dentro del tiempo configurado.</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-5 items-end">
            <div className="flex items-center justify-between sm:flex-col sm:items-start gap-2">
              <Label className="text-xs font-semibold">Activado</Label>
              <Switch data-testid="lnr-enabled-switch" checked={enabled} onCheckedChange={setEnabled} className="data-[state=checked]:bg-[#0E8DDB]" />
            </div>
            <div>
              <Label className="text-xs font-semibold">Umbral (horas)</Label>
              <Input data-testid="lnr-threshold-input" type="number" min={1} value={threshold} onChange={(e) => setThreshold(e.target.value)} className="rounded-sm mt-1" />
            </div>
            <div className="flex items-center justify-between sm:flex-col sm:items-start gap-2">
              <Label className="text-xs font-semibold">Activar horario laboral</Label>
              <Switch data-testid="lnr-business-hours-switch" checked={businessHours} onCheckedChange={setBusinessHours} className="data-[state=checked]:bg-[#0E8DDB]" />
            </div>
          </div>

          {/* Business-hours window — visually de-emphasized when the toggle is off */}
          <div
            data-testid="business-hours-panel"
            className={`mt-5 pt-5 border-t border-[#E9E6DC] transition-opacity ${businessHours ? "opacity-100" : "opacity-50"}`}
          >
            <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#888888] mb-3">Ventana de horario laboral</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-5 items-end">
              <div>
                <Label className="text-xs font-semibold">Hora de inicio</Label>
                <Input
                  data-testid="bh-start-input"
                  type="time"
                  value={bhStart}
                  onChange={(e) => setBhStart(e.target.value)}
                  className="rounded-sm mt-1"
                />
              </div>
              <div>
                <Label className="text-xs font-semibold">Hora de fin</Label>
                <Input
                  data-testid="bh-end-input"
                  type="time"
                  value={bhEnd}
                  onChange={(e) => setBhEnd(e.target.value)}
                  className="rounded-sm mt-1"
                />
              </div>
              <div>
                <Label className="text-xs font-semibold">Zona horaria</Label>
                <Select value={bhTz} onValueChange={setBhTz}>
                  <SelectTrigger data-testid="bh-tz-select" className="rounded-sm mt-1 h-10">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="America/Argentina/Cordoba">America/Argentina/Cordoba</SelectItem>
                    <SelectItem value="America/Argentina/Buenos_Aires">America/Argentina/Buenos_Aires</SelectItem>
                    <SelectItem value="America/Mexico_City">America/Mexico_City</SelectItem>
                    <SelectItem value="America/Bogota">America/Bogota</SelectItem>
                    <SelectItem value="Europe/Madrid">Europe/Madrid</SelectItem>
                    <SelectItem value="UTC">UTC</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="mt-4">
              <Label className="text-xs font-semibold">Días laborales</Label>
              <div data-testid="bh-days-row" className="flex flex-wrap gap-2 mt-2">
                {[
                  { v: 0, label: "Lun" },
                  { v: 1, label: "Mar" },
                  { v: 2, label: "Mié" },
                  { v: 3, label: "Jue" },
                  { v: 4, label: "Vie" },
                  { v: 5, label: "Sáb" },
                  { v: 6, label: "Dom" },
                ].map((d) => {
                  const active = bhDays.includes(d.v);
                  return (
                    <button
                      key={d.v}
                      type="button"
                      data-testid={`bh-day-${d.v}`}
                      onClick={() => toggleDay(d.v)}
                      className={`px-3 py-1.5 text-xs font-semibold rounded-sm border transition-colors ${
                        active
                          ? "bg-[#0E8DDB] text-white border-[#0E8DDB]"
                          : "bg-white text-[#888888] border-zinc-300 hover:border-[#0E8DDB]"
                      }`}
                    >
                      {d.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="mt-5">
            <Button data-testid="save-settings-button" onClick={() => saveSettings.mutate()} disabled={saveSettings.isPending} className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm font-semibold">
              <Save className="h-4 w-4 mr-1" /> Guardar configuración
            </Button>
          </div>
        </div>

        {/* Integración de WhatsApp */}
        <div className="bg-white border border-[#E9E6DC] rounded-sm p-5" data-testid="whatsapp-integration-card">
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center justify-center h-4 w-4 rounded-sm bg-[#0E8DDB] text-white text-[10px] font-bold">W</span>
            <h3 className="text-lg font-bold tracking-tight text-[#0B1B26]">Integración de WhatsApp</h3>
          </div>
          <p className="text-sm text-[#888888] mb-4">
            Estado actual del canal WhatsApp Cloud API. Las credenciales se gestionan vía variables de entorno del servidor.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div>
              <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#888888] mb-2">Estado</p>
              {waStatus === null ? (
                <span className="text-sm text-[#888888]">Sin información</span>
              ) : waStatus?.configured ? (
                <span data-testid="wa-status-configured" className="inline-flex items-center gap-2 text-sm font-bold text-[#16A34A] bg-[#F0FDF4] border border-[#BBF7D0] rounded-sm px-2.5 py-1">
                  <span className="h-2 w-2 rounded-full bg-[#16A34A]" /> WhatsApp conectado
                </span>
              ) : (
                <span data-testid="wa-status-not-configured" className="inline-flex items-center gap-2 text-sm font-bold text-[#0E8DDB] bg-[#F4F2EC] border border-[#EFE3E1] rounded-sm px-2.5 py-1">
                  <span className="h-2 w-2 rounded-full bg-[#0E8DDB]" /> WhatsApp no configurado
                </span>
              )}
              {waStatus?.phone_number_id_masked && (
                <p className="text-[11px] text-[#888888] mt-2">Phone Number ID: <span className="font-mono">{waStatus.phone_number_id_masked}</span></p>
              )}
              {waStatus?.api_version && (
                <p className="text-[11px] text-[#888888] mt-1">API: <span className="font-mono">{waStatus.api_version}</span></p>
              )}
            </div>
            <div>
              <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#888888] mb-2">Variables configuradas</p>
              <ul data-testid="wa-checklist" className="space-y-1 text-sm">
                {[
                  ["phone_number_id", "Phone Number ID"],
                  ["access_token", "Access Token"],
                  ["verify_token", "Verify Token"],
                  ["app_secret", "App Secret"],
                  ["business_account_id", "Business Account ID"],
                ].map(([key, label]) => {
                  const ok = !!waStatus?.checklist?.[key];
                  return (
                    <li key={key} className="flex items-center gap-2">
                      <span className={`inline-flex items-center justify-center h-4 w-4 rounded-sm text-[10px] font-bold ${ok ? "bg-[#DCFCE7] text-[#16A34A]" : "bg-latus-warm-gray text-[#888888]"}`}>
                        {ok ? "✓" : "—"}
                      </span>
                      <span className={ok ? "text-[#0B1B26]" : "text-[#888888]"}>{label} configurado</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
          <div className="mt-5 pt-4 border-t border-[#E9E6DC] grid grid-cols-1 md:grid-cols-2 gap-5">
            <div>
              <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#888888] mb-1">Último webhook recibido</p>
              <p data-testid="wa-last-webhook" className="text-sm text-[#0B1B26] font-mono">
                {waStatus?.last_webhook_at ? new Date(waStatus.last_webhook_at).toLocaleString("es-AR") : "Nunca"}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#888888] mb-1">Último error de WhatsApp</p>
              {waStatus?.last_error ? (
                <div data-testid="wa-last-error" className="text-sm">
                  <span className="font-mono text-[#0E8DDB] font-bold">
                    {waStatus.last_error.code != null ? `#${waStatus.last_error.code}` : "—"}
                  </span>
                  <span className="text-[#0B1B26]"> {(waStatus.last_error.message || "").slice(0, 180)}</span>
                  {waStatus.last_error_at && (
                    <p className="text-[11px] text-[#888888] mt-0.5">{new Date(waStatus.last_error_at).toLocaleString("es-AR")}</p>
                  )}
                </div>
              ) : (
                <p data-testid="wa-last-error" className="text-sm text-[#888888]">Sin errores</p>
              )}
            </div>
          </div>
        </div>

        <div className="bg-white border border-[#E9E6DC] rounded-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#E9E6DC] bg-latus-cream">
                {["Miembro", "Email", "Rol", "Activo"].map((h) => (
                  <th key={h} className="text-left px-5 py-3 text-xs tracking-[0.1em] uppercase font-bold text-[#888888]">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100">
              {users.map((u) => {
                const rs = roleStyle[u.role] || roleStyle.sales_agent;
                const isSelf = u.user_id === me?.user_id;
                return (
                  <tr key={u.user_id} data-testid={`user-row-${u.user_id}`} className="hover:bg-latus-cream transition-colors">
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <Avatar src={u.picture} name={u.name} size={32} />
                        <span className="font-semibold text-[#0B1B26]">{u.name}{isSelf && <span className="ml-2 text-xs text-[#0E8DDB]">(vos)</span>}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-[#888888]">{u.email}</td>
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
                        className="data-[state=checked]:bg-[#0E8DDB]"
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
