import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Users as UsersIcon, MessageSquareText, Plus, MoreHorizontal, Search,
  Copy, RefreshCw, CheckCircle2, AlertTriangle, KeyRound, Trash2, Eye, EyeOff,
  Bot, Sparkles, Lightbulb, Shield, Check, CheckSquare, Package, Building2,
} from "lucide-react";
import AppLayout from "@/components/AppLayout";
import AppointmentSettingsPanel from "@/components/AppointmentSettingsPanel";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import api from "@/lib/api";
import { roleMeta, AUTH_PROVIDERS } from "@/lib/constants";

const DEFAULT_ROLE_OPTIONS = [
  { key: "admin", label: "Administrador" },
  { key: "supervisor", label: "Supervisor" },
  { key: "agent", label: "Agente" },
  { key: "viewer", label: "Consulta" },
];

const ALL_PERMISSIONS = [
  { key: "write_crm", label: "Escritura CRM", desc: "Crear/editar contactos, leads, notas y tareas" },
  { key: "write_catalog", label: "Catálogo", desc: "Administrar productos del catálogo" },
  { key: "manage_users", label: "Usuarios", desc: "Crear, editar, activar/desactivar y borrar usuarios" },
  { key: "configure_whatsapp", label: "WhatsApp", desc: "Configurar credenciales y webhook de WhatsApp" },
  { key: "configure_ai", label: "IA", desc: "Configurar proveedores de IA, bot, modelos y precios" },
  { key: "manage_settings", label: "Ajustes", desc: "Cambiar ajustes generales del CRM" },
  { key: "message_any", label: "Mensajes globales", desc: "Enviar mensajes en cualquier conversación" },
  { key: "trigger_bot_any", label: "Bot global", desc: "Activar bot IA en cualquier conversación" },
];

const TZ_OPTIONS = [
  "America/Argentina/Cordoba",
  "America/Argentina/Buenos_Aires",
  "America/Mexico_City",
  "America/Bogota",
  "Europe/Madrid",
  "UTC",
];

function RolePill({ role }) {
  const m = roleMeta(role);
  return (
    <span
      data-testid={`role-pill-${role}`}
      className="inline-flex items-center text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-sm border"
      style={{ color: m.color, background: m.bg, borderColor: m.color + "33" }}
    >
      {m.label}
    </span>
  );
}

function CopyButton({ value, label = "Copiar" }) {
  return (
    <Button
      type="button"
      variant="outline"
      className="rounded-sm h-8 text-xs"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          toast.success("Copiado al portapapeles");
        } catch {
          toast.error("No se pudo copiar");
        }
      }}
    >
      <Copy className="h-3 w-3 mr-1" /> {label}
    </Button>
  );
}

// =============================================================================
// USERS TAB
// =============================================================================
function UsersTab({ me }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState("__all__");
  const [includeInactive, setIncludeInactive] = useState(false);

  const usersQ = useQuery({
    queryKey: ["admin-users", q, roleFilter, includeInactive],
    queryFn: () => api.get("/admin/users", {
      params: {
        q: q || undefined,
        role: roleFilter === "__all__" ? undefined : roleFilter,
        include_inactive: includeInactive ? true : undefined,
      },
    }).then((r) => r.data),
  });

  const [modal, setModal] = useState(null); // {mode:"create"|"edit", user}
  const [resetDialog, setResetDialog] = useState(null); // {user, tempPwd}
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [confirmToggle, setConfirmToggle] = useState(null);

  const toggleActive = useMutation({
    mutationFn: ({ uid, activate }) => api.post(
      `/admin/users/${uid}/${activate ? "activate" : "deactivate"}`,
    ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      toast.success("Estado actualizado");
      setConfirmToggle(null);
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo actualizar"),
  });

  const delUser = useMutation({
    mutationFn: (uid) => api.delete(`/admin/users/${uid}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      toast.success("Usuario eliminado");
      setConfirmDelete(null);
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo eliminar"),
  });

  const resetPwd = useMutation({
    mutationFn: (uid) => api.post(`/admin/users/${uid}/reset-password`).then((r) => r.data),
    onSuccess: (data, uid) => {
      const user = (usersQ.data || []).find((u) => u.user_id === uid);
      setResetDialog({ user, tempPwd: data.temporary_password, emailSent: data.email_sent });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo resetear"),
  });

  const users = usersQ.data || [];

  return (
    <div data-testid="users-tab" className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-[#888888]" />
          <Input
            data-testid="user-search"
            placeholder="Buscar por nombre o email…"
            className="rounded-sm pl-9"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <Select value={roleFilter} onValueChange={setRoleFilter}>
          <SelectTrigger data-testid="role-filter" className="rounded-sm w-40">
            <SelectValue placeholder="Rol" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">Todos los roles</SelectItem>
            {DEFAULT_ROLE_OPTIONS.map((r) => (
              <SelectItem key={r.key} value={r.key}>{r.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <label className="flex items-center gap-2 text-sm text-[#888888] select-none">
          <Switch
            data-testid="include-inactive"
            checked={includeInactive}
            onCheckedChange={setIncludeInactive}
            className="data-[state=checked]:bg-[#0E8DDB]"
          />
          Mostrar inactivos
        </label>
        <div className="flex-1" />
        <Button
          data-testid="new-user-button"
          onClick={() => setModal({ mode: "create" })}
          className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm font-semibold"
        >
          <Plus className="h-4 w-4 mr-1" /> Nuevo usuario
        </Button>
      </div>

      <div className="bg-white border border-[#E9E6DC] rounded-sm overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-latus-cream text-[11px] uppercase tracking-wide text-[#888888]">
            <tr>
              <th className="text-left px-4 py-2.5 font-bold">Nombre</th>
              <th className="text-left px-4 py-2.5 font-bold">Email</th>
              <th className="text-left px-4 py-2.5 font-bold">Rol</th>
              <th className="text-left px-4 py-2.5 font-bold">Método</th>
              <th className="text-left px-4 py-2.5 font-bold">Estado</th>
              <th className="text-left px-4 py-2.5 font-bold">Último ingreso</th>
              <th className="text-right px-4 py-2.5 font-bold">Acciones</th>
            </tr>
          </thead>
          <tbody data-testid="users-table-body" className="divide-y divide-zinc-200">
            {usersQ.isLoading ? (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-[#888888]">Cargando…</td></tr>
            ) : users.length === 0 ? (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-[#888888]">Sin usuarios</td></tr>
            ) : users.map((u) => {
              const isSelf = u.user_id === me?.user_id;
              const ap = u.auth_provider;
              const apLabel = ap === "local" ? "Email y contraseña"
                : ap === "both" ? "Ambos" : "Google";
              return (
                <tr key={u.user_id} data-testid={`user-row-${u.user_id}`}>
                  <td className="px-4 py-3 font-medium text-[#0B1B26]">
                    {u.name}
                    {isSelf && <span className="ml-2 text-[10px] text-[#888888] uppercase tracking-wide">Tú</span>}
                  </td>
                  <td className="px-4 py-3 text-[#888888]">{u.email}</td>
                  <td className="px-4 py-3"><RolePill role={u.role} /></td>
                  <td className="px-4 py-3 text-[#888888]">{apLabel}</td>
                  <td className="px-4 py-3">
                    {u.is_active ? (
                      <span className="inline-flex items-center gap-1 text-xs font-bold text-[#15803D]">
                        <span className="h-1.5 w-1.5 rounded-full bg-[#15803D]" /> Activo
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs font-bold text-[#888888]">
                        <span className="h-1.5 w-1.5 rounded-full bg-[#888888]" /> Inactivo
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-[#888888] text-xs">
                    {u.last_login_at ? new Date(u.last_login_at).toLocaleString("es-AR") : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button data-testid={`user-actions-${u.user_id}`} variant="ghost" className="h-8 w-8 p-0 rounded-sm">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="rounded-sm">
                        <DropdownMenuItem onClick={() => setModal({ mode: "edit", user: u })}>
                          Editar
                        </DropdownMenuItem>
                        {(ap === "local" || ap === "both") && (
                          <DropdownMenuItem onClick={() => resetPwd.mutate(u.user_id)}>
                            <KeyRound className="h-3 w-3 mr-2" /> Resetear contraseña
                          </DropdownMenuItem>
                        )}
                        <DropdownMenuItem
                          disabled={isSelf}
                          onClick={() => setConfirmToggle({ user: u, activate: !u.is_active })}
                        >
                          {u.is_active ? <><EyeOff className="h-3 w-3 mr-2" /> Desactivar</> : <><Eye className="h-3 w-3 mr-2" /> Activar</>}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          disabled={isSelf}
                          onClick={() => setConfirmDelete(u)}
                          className="text-[#DC2626] focus:text-[#DC2626]"
                        >
                          <Trash2 className="h-3 w-3 mr-2" /> Eliminar
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <UserFormDialog
        open={!!modal}
        mode={modal?.mode}
        initialUser={modal?.user}
        onClose={() => setModal(null)}
        onSaved={() => {
          setModal(null);
          qc.invalidateQueries({ queryKey: ["admin-users"] });
        }}
      />

      {/* Confirm deactivate/activate */}
      <AlertDialog open={!!confirmToggle} onOpenChange={(o) => !o && setConfirmToggle(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmToggle?.activate ? "¿Activar usuario?" : "¿Desactivar usuario?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmToggle?.activate
                ? `${confirmToggle?.user?.name} podrá ingresar nuevamente al sistema.`
                : `${confirmToggle?.user?.name} no podrá ingresar hasta que sea reactivado.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-toggle"
              onClick={() => confirmToggle && toggleActive.mutate({
                uid: confirmToggle.user.user_id, activate: confirmToggle.activate,
              })}
            >
              {confirmToggle?.activate ? "Activar" : "Desactivar"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirm delete */}
      <AlertDialog open={!!confirmDelete} onOpenChange={(o) => !o && setConfirmDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Eliminar usuario?</AlertDialogTitle>
            <AlertDialogDescription>
              Se eliminará a <b>{confirmDelete?.name}</b>. Esta acción no se puede deshacer.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-delete"
              onClick={() => confirmDelete && delUser.mutate(confirmDelete.user_id)}
              className="bg-[#DC2626] hover:bg-[#B91C1C]"
            >
              Eliminar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Reset password banner */}
      <Dialog open={!!resetDialog} onOpenChange={(o) => !o && setResetDialog(null)}>
        <DialogContent className="rounded-sm">
          <DialogHeader>
            <DialogTitle>{resetDialog?.emailSent ? "Email de recuperación enviado" : "Contraseña temporal generada"}</DialogTitle>
          </DialogHeader>
          <div className="bg-[#FEF9C3] border-l-4 border-[#EAB308] p-3 text-sm">
            <p className="font-bold flex items-center gap-2 mb-2">
              <AlertTriangle className="h-4 w-4" />
              {resetDialog?.emailSent ? "Se mandó un enlace seguro por email." : "Copiala ahora, no se vuelve a mostrar."}
            </p>
            <p className="text-[#888888] mb-2">
              {resetDialog?.emailSent
                ? <>Se envió un correo a <b>{resetDialog?.user?.name}</b> ({resetDialog?.user?.email}) para que defina una nueva contraseña.</>
                : <>Compartila con <b>{resetDialog?.user?.name}</b> ({resetDialog?.user?.email}) por un canal seguro. Al iniciar sesión, recomendales cambiarla.</>}
            </p>
            {resetDialog?.tempPwd && (
              <div className="flex items-center gap-2 bg-white border border-[#E9E6DC] rounded-sm px-3 py-2">
                <code data-testid="temp-password" className="flex-1 font-mono text-[#0B1B26] text-sm">{resetDialog?.tempPwd}</code>
                <CopyButton value={resetDialog?.tempPwd || ""} />
              </div>
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => setResetDialog(null)} className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm">
              Entendido
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function UserFormDialog({ open, mode, initialUser, onClose, onSaved }) {
  const isEdit = mode === "edit";
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("agent");
  const [authProvider, setAuthProvider] = useState("google");
  const [password, setPassword] = useState("");
  const [selectedWorkAreas, setSelectedWorkAreas] = useState([]);

  const workAreasQ = useQuery({
    queryKey: ["work-areas"],
    queryFn: () => api.get("/admin/work-areas").then((r) => r.data),
    enabled: open,
  });
  const workAreas = workAreasQ.data || [];

  useEffect(() => {
    if (open) {
      setName(initialUser?.name || "");
      setEmail(initialUser?.email || "");
      setRole(initialUser?.role || "agent");
      setAuthProvider(initialUser?.auth_provider || "google");
      setPassword("");
      setSelectedWorkAreas(initialUser?.work_areas || []);
    }
  }, [open, initialUser]);

  const save = useMutation({
    mutationFn: async () => {
      if (isEdit) {
        return api.patch(`/admin/users/${initialUser.user_id}`, {
          name, role, auth_provider: authProvider,
          work_areas: selectedWorkAreas,
        });
      }
      return api.post("/admin/users", {
        name, email, role, auth_provider: authProvider,
        password: ["local", "both"].includes(authProvider) ? password : undefined,
        work_areas: selectedWorkAreas,
      });
    },
    onSuccess: (res) => {
      const emailSent = !!res?.data?.email_sent;
      toast.success(
        isEdit
          ? "Usuario actualizado"
          : emailSent
            ? "Usuario creado y email enviado"
            : "Usuario creado",
      );
      onSaved?.();
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo guardar"),
  });

  const needsPwd = !isEdit && ["local", "both"].includes(authProvider);
  const pwdValid = !needsPwd || (/^(?=.*[A-Z])(?=.*\d).{8,}$/.test(password));
  const canSave = name.trim() && (isEdit || email.trim().includes("@")) && pwdValid && !save.isPending;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose?.()}>
      <DialogContent className="rounded-sm sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Editar usuario" : "Nuevo usuario"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs font-semibold">Nombre</Label>
            <Input data-testid="user-form-name" value={name} onChange={(e) => setName(e.target.value)} className="rounded-sm mt-1" />
          </div>
          <div>
            <Label className="text-xs font-semibold">Email</Label>
            <Input
              data-testid="user-form-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isEdit}
              className="rounded-sm mt-1"
            />
          </div>
          <div>
            <Label className="text-xs font-semibold">Rol</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger data-testid="user-form-role" className="rounded-sm mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DEFAULT_ROLE_OPTIONS.map((r) => (
                  <SelectItem key={r.key} value={r.key}>{r.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs font-semibold">Método de acceso</Label>
            <div className="grid grid-cols-3 gap-2 mt-1">
              {AUTH_PROVIDERS.map((ap) => (
                <button
                  key={ap.key}
                  type="button"
                  data-testid={`user-form-ap-${ap.key}`}
                  onClick={() => setAuthProvider(ap.key)}
                  className={`px-3 py-2 text-xs font-semibold rounded-sm border text-center ${
                    authProvider === ap.key
                      ? "bg-[#0E8DDB] text-white border-[#0E8DDB]"
                      : "bg-white text-[#888888] border-zinc-300 hover:border-[#0E8DDB]"
                  }`}
                >
                  {ap.label}
                </button>
              ))}
            </div>
          </div>
          {needsPwd && (
            <div>
              <Label className="text-xs font-semibold">Contraseña inicial</Label>
              <Input
                data-testid="user-form-password"
                type="text"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Mín. 8 caracteres, 1 mayúscula, 1 número"
                className="rounded-sm mt-1 font-mono"
              />
              {!pwdValid && password.length > 0 && (
                <p className="text-[11px] text-[#DC2626] mt-1">
                  La contraseña debe tener al menos 8 caracteres, una mayúscula y un número.
                </p>
              )}
            </div>
          )}
          <div>
            <Label className="text-xs font-semibold">Áreas de Trabajo</Label>
            <div className="grid grid-cols-2 gap-2 mt-1.5 p-2.5 border border-[#E9E6DC] rounded-sm max-h-32 overflow-y-auto">
              {workAreas.map((wa) => {
                const isChecked = selectedWorkAreas.includes(wa.id);
                return (
                  <label key={wa.id} className="flex items-center gap-2 text-xs text-[#0B1B26] cursor-pointer">
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => {
                        if (isChecked) {
                          setSelectedWorkAreas(selectedWorkAreas.filter((id) => id !== wa.id));
                        } else {
                          setSelectedWorkAreas([...selectedWorkAreas, wa.id]);
                        }
                      }}
                      className="rounded-sm border-zinc-300 text-[#0E8DDB] focus:ring-[#0E8DDB] h-3.5 w-3.5"
                    />
                    {wa.name}
                  </label>
                );
              })}
              {workAreas.length === 0 && (
                <p className="col-span-2 text-xs text-[#888888] italic">No hay áreas de trabajo creadas.</p>
              )}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="rounded-sm">Cancelar</Button>
          <Button
            data-testid="user-form-save"
            disabled={!canSave}
            onClick={() => save.mutate()}
            className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm"
          >
            {isEdit ? "Guardar" : "Crear"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


// =============================================================================
// WHATSAPP TAB
// =============================================================================
const WA_FIELDS = [
  {
    key: "verify_token",
    label: "Verify Token",
    metaName: "En Meta: \"Token de verificación\" (Verify token)",
    help: "Lo definís vos. Pegá el mismo valor en Meta Business → WhatsApp → Configuración → Webhooks → Token de verificación.",
  },
  {
    key: "access_token",
    label: "Access Token",
    metaName: "En Meta: \"Token de acceso\" (Access token)",
    help: "Meta Business → Configuración del negocio → Usuarios del sistema → Generar token. Usá un token permanente con permisos whatsapp_business_messaging y whatsapp_business_management.",
  },
  {
    key: "phone_number_id",
    label: "Phone Number ID",
    metaName: "En Meta: \"Identificador del número de teléfono\" (Phone number ID)",
    help: "Meta Business → WhatsApp → Configuración de la API → Números de teléfono. Copiá el ID que aparece debajo del número, NO el número en sí.",
  },
  {
    key: "app_secret",
    label: "App Secret",
    metaName: "En Meta: \"Clave secreta de la app\" (App secret)",
    help: "Panel de desarrolladores de Meta → Tu app → Configuración → Básica → Clave secreta de la app. Hacé clic en Mostrar para revelarla.",
  },
  {
    key: "business_account_id",
    label: "Business Account ID",
    metaName: "En Meta: \"Identificador de la cuenta de WhatsApp Business\" (WhatsApp Business Account ID / WABA ID)",
    help: "Meta Business → WhatsApp → Configuración de la API → arriba dice \"WhatsApp Business Account ID\". También aparece en WhatsApp Manager → Configuración.",
  },
];

function WhatsAppTab() {
  const qc = useQueryClient();
  const cfgQ = useQuery({
    queryKey: ["admin-wa-config"],
    queryFn: () => api.get("/admin/whatsapp/config").then((r) => r.data),
    refetchInterval: 30_000,
  });
  const cfg = cfgQ.data;

  const [drafts, setDrafts] = useState({});
  const [apiVersion, setApiVersion] = useState("");
  const [rotated, setRotated] = useState(null);

  useEffect(() => {
    if (cfg?.api_version) setApiVersion(cfg.api_version);
  }, [cfg?.api_version]);

  const save = useMutation({
    mutationFn: (payload) => api.put("/admin/whatsapp/config", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-wa-config"] });
      qc.invalidateQueries({ queryKey: ["wa-status"] });
      setDrafts({});
      toast.success("Configuración guardada");
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo guardar"),
  });

  const testConn = useMutation({
    mutationFn: () => api.post("/admin/whatsapp/test-connection").then((r) => r.data),
    onSuccess: (data) => {
      if (data.ok) {
        toast.success(`Conexión OK${data.display_phone_number ? ` con ${data.display_phone_number}` : ""}${data.verified_name ? ` — ${data.verified_name}` : ""}`);
      } else {
        toast.error(`Error de Meta: ${data.error_code ?? "—"} ${data.error_message || ""}`);
      }
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo probar la conexión"),
  });

  const testWebhook = useMutation({
    mutationFn: () => api.post("/admin/whatsapp/test-webhook-verify").then((r) => r.data),
    onSuccess: (data) => {
      if (data.ok) {
        toast.success("Webhook validado correctamente — podés registrarlo en Meta");
      } else if (data.detail === "verify_token mismatch") {
        toast.error("El Verify Token configurado en Latus no coincide. Revisá que el valor en Meta sea exactamente el mismo.");
      } else if (data.status === 0) {
        toast.error("No se pudo alcanzar la URL del webhook. Verificá que la app esté online y que la URL sea pública.");
      } else {
        toast.error(`Webhook respondió HTTP ${data.status}: ${data.detail || ""}`);
      }
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo probar el webhook"),
  });

  const rotate = useMutation({
    mutationFn: () => api.post("/admin/whatsapp/rotate-verify-token").then((r) => r.data),
    onSuccess: (data) => {
      setRotated(data.verify_token);
      qc.invalidateQueries({ queryKey: ["admin-wa-config"] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo rotar"),
  });

  if (!cfg) {
    return <div className="text-[#888888]">Cargando…</div>;
  }

  const saveChanges = () => {
    const payload = {};
    Object.entries(drafts).forEach(([k, v]) => {
      if (typeof v === "string" && v.trim() === "__CLEAR__") payload[k] = null;
      else if (typeof v === "string" && v.length > 0) payload[k] = v;
    });
    if (apiVersion && apiVersion !== cfg.api_version) payload.api_version = apiVersion;
    if (Object.keys(payload).length === 0) {
      toast.info("Sin cambios para guardar");
      return;
    }
    save.mutate(payload);
  };

  return (
    <div data-testid="whatsapp-tab" className="space-y-5">
      {/* Estado */}
      <div className="bg-white border border-[#E9E6DC] rounded-sm p-5">
        <h3 className="text-base font-bold tracking-tight text-[#0B1B26] mb-3">Estado</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <div>
            {cfg.configured ? (
              <span data-testid="wa-cfg-connected" className="inline-flex items-center gap-2 text-sm font-bold text-[#16A34A] bg-[#F0FDF4] border border-[#BBF7D0] rounded-sm px-2.5 py-1">
                <span className="h-2 w-2 rounded-full bg-[#16A34A]" /> Conectado
              </span>
            ) : (
              <span data-testid="wa-cfg-not-configured" className="inline-flex items-center gap-2 text-sm font-bold text-[#0E8DDB] bg-[#F4F2EC] border border-[#EFE3E1] rounded-sm px-2.5 py-1">
                <span className="h-2 w-2 rounded-full bg-[#0E8DDB]" /> No configurado
              </span>
            )}
            <p className="text-xs text-[#888888] mt-2">Última actividad</p>
            <p className="text-sm font-mono text-[#0B1B26]">
              {cfg.last_webhook_at ? new Date(cfg.last_webhook_at).toLocaleString("es-AR") : "Nunca"}
            </p>
            {cfg.last_error && (
              <p className="text-xs text-[#DC2626] mt-1">
                Último error: <span className="font-mono font-bold">#{cfg.last_error.code ?? "—"}</span> {cfg.last_error.message}
              </p>
            )}
          </div>
          <div>
            <p className="text-xs text-[#888888] mb-1">Webhook URL (configurar en Meta)</p>
            {cfg.webhook_url_warning && (
              <div data-testid="webhook-url-warning" className="bg-[#FEF2F2] border-l-4 border-[#DC2626] p-2.5 text-xs text-[#991B1B] mb-2">
                <p className="font-bold flex items-center gap-1.5"><AlertTriangle className="h-3.5 w-3.5" /> URL pública no disponible</p>
                <p className="mt-0.5">{cfg.webhook_url_warning}</p>
              </div>
            )}
            <div className="flex items-center gap-2 bg-latus-cream border border-[#E9E6DC] rounded-sm px-3 py-2">
              <code data-testid="wa-webhook-url" className="flex-1 font-mono text-xs break-all">{cfg.webhook_url || "—"}</code>
              {cfg.webhook_url && <CopyButton value={cfg.webhook_url} />}
            </div>
            <p className="text-xs text-neutral-600 mt-2 leading-relaxed">
              Pegá esta URL en <b>Meta Business → WhatsApp → Configuración → Webhooks → URL de devolución de llamada</b>.
              Tiene que ser exactamente esta URL (HTTPS, sin <span className="font-mono">localhost</span>).
              Si ves <span className="font-mono">localhost</span> o un dominio interno, configurá{" "}
              <span className="font-mono">PUBLIC_BASE_URL</span> en el backend o pedile a un admin que lo haga.
            </p>
            <p className="text-xs text-[#888888] mt-3 mb-1">Versión API</p>
            <Input
              data-testid="wa-api-version"
              value={apiVersion}
              onChange={(e) => setApiVersion(e.target.value)}
              className="rounded-sm h-9 max-w-[140px] font-mono"
            />
          </div>
        </div>
      </div>

      {/* Credenciales */}
      <div className="bg-white border border-[#E9E6DC] rounded-sm p-5">
        <h3 className="text-base font-bold tracking-tight text-[#0B1B26] mb-1">Credenciales</h3>
        <p className="text-xs text-[#888888] mb-4">
          Los valores se guardan cifrados en base de datos y sobreescriben los de las variables de entorno.
          Para limpiar un campo y volver al valor de <code>.env</code>, usá <b>Limpiar</b>.
        </p>
        {!cfg.encryption_available && (
          <div data-testid="wa-no-encryption" className="bg-[#FEF9C3] border-l-4 border-[#EAB308] p-3 text-sm mb-4">
            <p className="font-bold flex items-center gap-2"><AlertTriangle className="h-4 w-4" /> Cifrado no disponible</p>
            <p className="text-[#888888]">
              <code>APP_ENCRYPTION_KEY</code> no está configurado en el backend. La edición por UI
              está deshabilitada hasta que se genere y se reinicie el backend.
            </p>
          </div>
        )}
        <div className="space-y-5">
          {WA_FIELDS.map((f) => {
            const meta = cfg.fields[f.key] || { source: "none", masked: "", configured: false };
            const pill = meta.source === "db" ? { label: "DB", c: "#16A34A", bg: "#F0FDF4" }
              : meta.source === "env" ? { label: "ENV", c: "#1D4ED8", bg: "#EFF6FF" }
              : { label: "Sin configurar", c: "#888888", bg: "#F4F4F5" };
            return (
              <div key={f.key} className="border-t border-[#E9E6DC] pt-4 first:border-t-0 first:pt-0">
                <div className="mb-1.5">
                  <Label className="text-sm font-bold text-[#0B1B26]">{f.label}</Label>
                  <p className="text-xs italic text-neutral-500 mt-0.5">{f.metaName}</p>
                  <p className="text-xs text-neutral-600 mt-1 leading-relaxed">{f.help}</p>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center">
                  <div className="md:col-span-3 flex items-center gap-2">
                    <span
                      className="inline-flex items-center text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-sm border"
                      style={{ color: pill.c, background: pill.bg, borderColor: pill.c + "33" }}
                    >
                      {pill.label}
                    </span>
                    <span className="font-mono text-xs text-[#888888]">{meta.masked || "—"}</span>
                  </div>
                  <Input
                    data-testid={`wa-input-${f.key}`}
                    value={drafts[f.key] !== undefined && drafts[f.key] !== "__CLEAR__" ? drafts[f.key] : ""}
                    placeholder={meta.masked ? `Reemplazar ${meta.masked}` : "Ingresá un nuevo valor"}
                    onChange={(e) => setDrafts({ ...drafts, [f.key]: e.target.value })}
                    className="md:col-span-7 rounded-sm font-mono text-xs"
                    disabled={!cfg.encryption_available}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    className="md:col-span-2 rounded-sm h-9 text-xs"
                    disabled={!cfg.encryption_available || meta.source !== "db"}
                    onClick={() => setDrafts({ ...drafts, [f.key]: "__CLEAR__" })}
                  >
                    {drafts[f.key] === "__CLEAR__" ? "Se limpiará" : "Limpiar"}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-4 flex justify-end">
          <Button
            data-testid="wa-save-config"
            disabled={save.isPending || !cfg.encryption_available}
            onClick={saveChanges}
            className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm font-semibold"
          >
            Guardar cambios
          </Button>
        </div>
      </div>

      {/* Acciones */}
      <div className="bg-white border border-[#E9E6DC] rounded-sm p-5">
        <h3 className="text-base font-bold tracking-tight text-[#0B1B26] mb-3">Acciones</h3>
        <div className="flex flex-wrap gap-3">
          <Button
            data-testid="wa-rotate-verify"
            onClick={() => rotate.mutate()}
            disabled={rotate.isPending || !cfg.encryption_available}
            variant="outline"
            className="rounded-sm font-semibold"
          >
            <RefreshCw className="h-4 w-4 mr-1" /> Generar nuevo Verify Token
          </Button>
          <Button
            data-testid="wa-test-connection"
            onClick={() => testConn.mutate()}
            disabled={testConn.isPending}
            className="rounded-sm font-semibold bg-[#0B1B26] hover:bg-[#27272A] text-white"
          >
            <CheckCircle2 className="h-4 w-4 mr-1" /> Probar conexión
          </Button>
          <Button
            data-testid="wa-test-webhook"
            onClick={() => testWebhook.mutate()}
            disabled={testWebhook.isPending}
            variant="outline"
            className="rounded-sm font-semibold"
          >
            <CheckCircle2 className="h-4 w-4 mr-1" /> Probar webhook
          </Button>
        </div>
        {rotated && (
          <div data-testid="wa-rotated-banner" className="bg-[#FEF9C3] border-l-4 border-[#EAB308] p-3 text-sm mt-4">
            <p className="font-bold flex items-center gap-2 mb-1"><AlertTriangle className="h-4 w-4" /> Nuevo Verify Token generado</p>
            <p className="text-[#888888] mb-2">
              Copialo y pegalo en Meta Business. <b>Este valor no se vuelve a mostrar.</b>
            </p>
            <div className="flex items-center gap-2 bg-white border border-[#E9E6DC] rounded-sm px-3 py-2">
              <code className="flex-1 font-mono text-xs break-all">{rotated}</code>
              <CopyButton value={rotated} />
            </div>
          </div>
        )}
      </div>

      {/* Instrucciones */}
      <details className="bg-white border border-[#E9E6DC] rounded-sm p-5 group">
        <summary className="font-bold tracking-tight text-[#0B1B26] cursor-pointer list-none flex items-center justify-between">
          ¿Dónde encuentro estos valores en Meta?
          <span className="text-xs text-[#888888] group-open:hidden">Mostrar</span>
          <span className="text-xs text-[#888888] hidden group-open:inline">Ocultar</span>
        </summary>

        {/* Equivalencias de nombres Meta ↔ Latus CRM */}
        <div className="mt-4">
          <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#888888] mb-2">
            Equivalencias de nombres · Meta ↔ Latus CRM
          </p>
          <div className="border border-[#E9E6DC] rounded-sm overflow-hidden">
            <table data-testid="wa-meta-mapping" className="w-full text-sm">
              <thead className="bg-latus-cream text-[11px] uppercase tracking-wide text-[#888888]">
                <tr>
                  <th className="text-left px-3 py-2 font-bold w-1/4">Latus CRM</th>
                  <th className="text-left px-3 py-2 font-bold w-1/4">Nombre en Meta</th>
                  <th className="text-left px-3 py-2 font-bold">Dónde encontrarlo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200">
                {WA_FIELDS.map((f) => (
                  <tr key={f.key}>
                    <td className="px-3 py-2 font-semibold text-[#0B1B26] align-top">{f.label}</td>
                    <td className="px-3 py-2 italic text-neutral-500 align-top">
                      {f.metaName.replace(/^En Meta:\s*/, "")}
                    </td>
                    <td className="px-3 py-2 text-neutral-600 align-top">{f.help}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#888888] mt-5 mb-2">
          Pasos de configuración
        </p>
        <ol className="list-decimal pl-5 space-y-2 text-sm text-[#0B1B26]">
          <li>Iniciá sesión en <a href="https://business.facebook.com" className="text-[#1D4ED8] underline" target="_blank" rel="noreferrer">Meta Business</a> y abrí la app de WhatsApp Business.</li>
          <li>En la sección <b>Configuración &gt; Webhooks</b>, agregá la URL <code className="font-mono bg-latus-warm-gray px-1">{cfg.webhook_url}</code> y el <b>Verify Token</b> guardado acá.</li>
          <li>Suscribite al campo <b>messages</b>.</li>
          <li>En <b>Tokens de acceso</b>, generá un Access Token permanente y pegalo arriba.</li>
          <li>Copiá el <b>Phone Number ID</b> y el <b>WhatsApp Business Account ID</b> al panel.</li>
          <li>Hacé clic en <b>Probar conexión</b> para validar.</li>
        </ol>
      </details>
    </div>
  );
}


// =============================================================================
// BOT IA TAB
// =============================================================================
function BotIATab({ setTab }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin-bot-settings"],
    queryFn: () => api.get("/admin/bot-settings").then((r) => r.data),
  });

  const aiProviderQ = useQuery({
    queryKey: ["admin-ai-provider"],
    queryFn: () => api.get("/admin/ai-provider").then((r) => r.data),
  });

  const usersQ = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get("/users").then((r) => r.data),
  });

  const [draft, setDraft] = useState(null);
  const [pendingKeys, setPendingKeys] = useState({});
  useEffect(() => {
    if (q.data && draft === null) {
      setDraft({ ...q.data });
      setPendingKeys({});
    }
  }, [q.data, draft]);

  const save = useMutation({
    mutationFn: (payload) => api.patch("/admin/bot-settings", payload),
    onSuccess: (r) => {
      setDraft({ ...r.data });
      setPendingKeys({});
      qc.invalidateQueries({ queryKey: ["admin-bot-settings"] });
      toast.success("Cambios guardados");
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudieron guardar los cambios"),
  });

  if (q.isPending || aiProviderQ.isPending || usersQ.isPending || !draft) return <div className="text-[#888888]">Cargando…</div>;

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));
  const faqs = Array.isArray(draft.faqs) ? draft.faqs : [];
  const thresh = Number(draft.confidence_threshold ?? 0.7);
  const ctxMax = Number(draft.recent_messages_context_max ?? 12);
  const threshInvalid = !(thresh >= 0 && thresh <= 1);
  const ctxInvalid = !(ctxMax >= 3 && ctxMax <= 50);
  const dirty = JSON.stringify(draft) !== JSON.stringify(q.data) || Object.keys(pendingKeys).length > 0;
  const providers = aiProviderQ.data?.supported_providers || Object.keys(PROVIDER_LABELS);
  const suggestionsList = (aiProviderQ.data?.model_suggestions || {})[draft.provider || "built_in"] || [];

  const onSave = () => {
    if (threshInvalid) {
      toast.error("La confianza mínima debe estar entre 0 y 1");
      return;
    }
    if (ctxInvalid) {
      toast.error("Los mensajes de contexto deben estar entre 3 y 50");
      return;
    }
    if (!(draft.bot_name || "").trim()) {
      toast.error("El nombre del bot no puede estar vacío");
      return;
    }
    const payload = {
      bot_enabled_default: !!draft.bot_enabled_default,
      confidence_threshold: thresh,
      recent_messages_context_max: ctxMax,
      business_instructions: draft.company_context || draft.business_instructions || "",
      company_context: draft.company_context || "",
      response_instructions: draft.response_instructions || "",
      catalog_reading_enabled: draft.catalog_reading_enabled !== undefined ? !!draft.catalog_reading_enabled : true,
      handoff_rules: draft.handoff_rules || "",
      tone: draft.tone || "",
      provider: draft.provider || "built_in",
      model: draft.model || "gpt-4o-mini",
      bot_name: (draft.bot_name || "").trim(),
      include_client_info: !!draft.include_client_info,
      default_handoff_user_id: draft.default_handoff_user_id || null,
      faqs: faqs
        .map((f) => ({ q: (f.q || "").trim(), a: (f.a || "").trim() }))
        .filter((f) => f.q && f.a),
      appointment_scheduling_enabled: !!draft.appointment_scheduling_enabled,
      appointment_available_days: draft.appointment_available_days || [1, 2, 3, 4, 5],
      appointment_business_hours: draft.appointment_business_hours || "09:00-18:00",
      appointment_duration_minutes: Number(draft.appointment_duration_minutes) || 30,
      appointment_mode: draft.appointment_mode || "people",
      appointment_timezone: draft.appointment_timezone || "America/Argentina/Buenos_Aires",
      appointment_services: Array.isArray(draft.appointment_services) ? draft.appointment_services : [],
    };
    
    // Construct api_keys dictionary to patch
    const apiKeysPayload = {};
    Object.entries(pendingKeys).forEach(([prov, val]) => {
      if (val === null) apiKeysPayload[prov] = null;
      else if (val.trim()) apiKeysPayload[prov] = val.trim();
    });
    if (Object.keys(apiKeysPayload).length > 0) {
      payload.api_keys = apiKeysPayload;
    }
    
    save.mutate(payload);
  };

  return (
    <div className="space-y-6" data-testid="bot-ia-tab">
      <div className="bg-white border border-[#E9E6DC] rounded-sm p-5">
        <div className="flex items-center gap-2 mb-1">
          <Bot className="h-5 w-5 text-[#0E8DDB]" />
          <h2 className="text-xl font-bold tracking-tight text-[#0B1B26]">Asistente de IA</h2>
        </div>
        <p className="text-sm text-[#888888] mb-5">
          Configurá cómo responde el bot a los mensajes entrantes de WhatsApp. Los cambios
          impactan en todas las conversaciones nuevas y en las que tengan el bot habilitado.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {/* Default bot on/off */}
          <div className="flex items-start justify-between gap-4 p-3 border border-[#E9E6DC] rounded-sm">
            <div>
              <Label className="text-sm font-bold text-[#0B1B26]">Bot habilitado por defecto</Label>
              <p className="text-xs text-[#888888] mt-0.5">
                Si está activo, cada conversación nueva arranca con el bot encendido.
              </p>
            </div>
            <Switch
              data-testid="bot-setting-enabled-default"
              checked={!!draft.bot_enabled_default}
              onCheckedChange={(v) => set({ bot_enabled_default: v })}
            />
          </div>

          {/* Provider */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Proveedor</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Elegí el proveedor de IA para el bot de autorespuestas.
            </p>
            <Select value={draft.provider || "built_in"} onValueChange={(v) => {
              const newSuggestions = (aiProviderQ.data?.model_suggestions || {})[v] || [];
              const defaultModel = newSuggestions[0] || "";
              set({ provider: v, model: defaultModel });
            }}>
              <SelectTrigger data-testid="bot-setting-provider" className="rounded-sm h-9 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p} value={p}>{PROVIDER_LABELS[p] || p}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Model */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Modelo</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Seleccioná un modelo o ingresá uno personalizado para el bot.
            </p>
            {suggestionsList.length > 0 ? (
              <div className="space-y-2">
                <Select
                  value={suggestionsList.includes(draft.model) ? draft.model : "custom"}
                  onValueChange={(v) => {
                    if (v === "custom") {
                      if (suggestionsList.includes(draft.model)) {
                        set({ model: "" });
                      }
                    } else {
                      set({ model: v });
                    }
                  }}
                >
                  <SelectTrigger data-testid="bot-setting-model" className="rounded-sm h-9 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {suggestionsList.map((m) => (
                      <SelectItem key={m} value={m}>{m}</SelectItem>
                    ))}
                    <SelectItem value="custom">Otro modelo (personalizado)</SelectItem>
                  </SelectContent>
                </Select>

                {(!suggestionsList.includes(draft.model) || draft.model === "") && (
                  <Input
                    data-testid="bot-setting-model-custom"
                    value={draft.model || ""}
                    onChange={(e) => set({ model: e.target.value })}
                    className="rounded-sm h-9 mt-2 font-mono"
                    placeholder="Escribí el identificador del modelo..."
                  />
                )}
              </div>
            ) : (
              <Input
                data-testid="bot-setting-model"
                value={draft.model || ""}
                onChange={(e) => set({ model: e.target.value })}
                className="rounded-sm h-9 font-mono"
                placeholder="Identificador del modelo"
              />
            )}
          </div>

          {/* Nombre del Bot */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Nombre del Bot</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              El nombre con el que se identificará el asistente en el chat y en las instrucciones.
            </p>
            <Input
              data-testid="bot-setting-name"
              value={draft.bot_name || ""}
              onChange={(e) => set({ bot_name: e.target.value })}
              className="rounded-sm h-9"
              placeholder="Ej.: Asistente Latus, Carlos, etc."
            />
          </div>

          {/* Contexto del cliente enriquecido */}
          <div className="flex items-start justify-between gap-4 p-3 border border-[#E9E6DC] rounded-sm">
            <div>
              <Label className="text-sm font-bold text-[#0B1B26]">Contexto del cliente enriquecido</Label>
              <p className="text-xs text-[#888888] mt-0.5">
                Inyecta el nombre, teléfono, email, notas del CRM y estado del lead al contexto del bot para respuestas personalizadas.
              </p>
            </div>
            <Switch
              data-testid="bot-setting-include-client-info"
              checked={!!draft.include_client_info}
              onCheckedChange={(v) => set({ include_client_info: v })}
            />
          </div>

          {/* Confidence threshold */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <div className="flex items-center justify-between mb-1">
              <Label className="text-sm font-bold text-[#0B1B26]">Confianza mínima</Label>
              <span data-testid="bot-setting-threshold-value" className="text-sm font-mono font-bold text-[#0E8DDB]">
                {thresh.toFixed(2)}
              </span>
            </div>
            <p className="text-xs text-[#888888] mb-2">
              Si la respuesta del bot tiene una confianza menor, deriva a un humano.
            </p>
            <input
              data-testid="bot-setting-threshold-slider"
              type="range" min="0" max="1" step="0.05"
              value={thresh}
              onChange={(e) => set({ confidence_threshold: parseFloat(e.target.value) })}
              className="w-full accent-[#0E8DDB]"
            />
            {threshInvalid && (
              <p className="text-[11px] text-[#DC2626] mt-1">El valor debe estar entre 0 y 1.</p>
            )}
          </div>

          {/* Context max */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Mensajes recientes de contexto</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Cuántos mensajes previos del chat le pasamos al modelo (3 a 50).
            </p>
            <Input
              data-testid="bot-setting-ctxmax"
              type="number" min="3" max="50"
              value={ctxMax}
              onChange={(e) => set({ recent_messages_context_max: parseInt(e.target.value, 10) || 0 })}
              className="rounded-sm h-9"
            />
            {ctxInvalid && (
              <p className="text-[11px] text-[#DC2626] mt-1">Debe estar entre 3 y 50.</p>
            )}
          </div>

          {/* Default Handoff User */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Operador de derivación por defecto</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Se le asignará el lead cuando el bot active una derivación.
            </p>
            <Select
              value={draft.default_handoff_user_id || "unassigned"}
              onValueChange={(v) => set({ default_handoff_user_id: v === "unassigned" ? null : v })}
            >
              <SelectTrigger data-testid="bot-setting-default-handoff-user" className="rounded-sm h-9 text-sm">
                <SelectValue placeholder="Sin asignar" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="unassigned">Sin asignar</SelectItem>
                {(usersQ.data || [])
                  .filter((u) => u.is_active)
                  .map((u) => (
                    <SelectItem key={u.user_id} value={u.user_id}>
                      {u.name || u.email}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>

          {/* Cierre automático por inactividad (horas) */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Cierre automático por inactividad (horas)</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Tiempo en horas de inactividad para cerrar el chat y rearmar el bot.
            </p>
            <Input
              data-testid="bot-setting-inactive-hours"
              type="number" min="1" max="168"
              value={draft.bot_inactive_close_hours ?? 48}
              onChange={(e) => set({ bot_inactive_close_hours: parseInt(e.target.value, 10) || 48 })}
              className="rounded-sm h-9"
            />
          </div>

          {/* Tone */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <Label className="text-sm font-bold text-[#0B1B26]">Tono</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Una línea describiendo cómo querés que suene (ej.: profesional, cercano, conciso).
            </p>
            <Input
              data-testid="bot-setting-tone"
              value={draft.tone || ""}
              onChange={(e) => set({ tone: e.target.value })}
              className="rounded-sm h-9"
              placeholder="profesional, cercano, conciso"
            />
          </div>

          {/* Contexto de la empresa */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <Label className="text-sm font-bold text-[#0B1B26]">Contexto de la empresa</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Información del negocio que el bot usará como contexto: descripción institucional, políticas de la empresa, horarios de atención, etc.
            </p>
            <Textarea
              data-testid="bot-setting-company-context"
              value={draft.company_context || draft.business_instructions || ""}
              onChange={(e) => set({ company_context: e.target.value })}
              className="rounded-sm min-h-[120px] font-mono text-xs"
              placeholder="Ej.: Somos Latus CRM, brindamos software de gestión comercial. Horario de atención humana: lunes a viernes 9 a 18 hs..."
            />
          </div>

          {/* Instrucciones en las respuestas */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <Label className="text-sm font-bold text-[#0B1B26]">Instrucciones en las respuestas</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Indicá pautas de comportamiento para las respuestas automáticas del bot: límites, cosas que debe evitar, formato de las respuestas (ej.: no usar más de 40 palabras, ser amable, etc.).
            </p>
            <Textarea
              data-testid="bot-setting-response-instructions"
              value={draft.response_instructions || ""}
              onChange={(e) => set({ response_instructions: e.target.value })}
              className="rounded-sm min-h-[120px] font-mono text-xs"
              placeholder="Ej.: Respondé siempre con un saludo cálido. Sé extremadamente breve (menos de 50 palabras). Nunca inventes precios de servicios que no figuren en el catálogo..."
            />
          </div>

          {/* Lectura del catálogo */}
          <div className="flex items-start justify-between gap-4 p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <div>
              <Label className="text-sm font-bold text-[#0B1B26]">Lectura del catálogo e inventario</Label>
              <p className="text-xs text-[#888888] mt-0.5">
                Si está habilitado, cuando el bot detecte una intención de compra o consulta comercial (precios, stock, catálogo), buscará automáticamente en el catálogo de productos del CRM y usará la información real para responder, evitando alucinaciones.
              </p>
            </div>
            <Switch
              data-testid="bot-setting-catalog-reading"
              checked={draft.catalog_reading_enabled !== undefined ? !!draft.catalog_reading_enabled : true}
              onCheckedChange={(v) => set({ catalog_reading_enabled: v })}
            />
          </div>

          <AppointmentSettingsPanel draft={draft} onChange={set} users={usersQ.data || []} />


          {/* Handoff rules */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <Label className="text-sm font-bold text-[#0B1B26]">Reglas de derivación a humano</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Cuándo el bot debe pasar el chat a un agente humano.
            </p>
            <Textarea
              data-testid="bot-setting-handoff"
              value={draft.handoff_rules || ""}
              onChange={(e) => set({ handoff_rules: e.target.value })}
              className="rounded-sm min-h-[100px] text-xs"
              placeholder="Ej.: 1) cliente pide hablar con humano; 2) cliente comparte DNI/CBU/tarjeta; ..."
            />
          </div>

          {/* API Keys del Bot (Opcional) */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <details className="group">
              <summary className="cursor-pointer text-sm font-bold text-[#0B1B26] flex items-center justify-between list-none">
                <span className="flex items-center gap-2">
                  <KeyRound className="h-4 w-4 text-[#0E8DDB]" />
                  API Keys del Bot (Opcional)
                </span>
                <span className="text-xs text-[#888888] font-normal group-open:hidden">
                  Ver/Configurar llaves dedicadas
                </span>
                <span className="text-xs text-[#888888] font-normal hidden group-open:inline">
                  Ocultar
                </span>
              </summary>
              
              <div className="mt-3 pt-3 border-t border-[#E9E6DC] space-y-3">
                <p className="text-xs text-[#888888]">
                  Por defecto, el bot de atención utilizará las API Keys configuradas en el <strong>Asistente de IA</strong>. Si deseás que el bot de WhatsApp use claves distintas, podés configurarlas aquí.
                </p>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                  {KEY_REQUIRED_PROVIDERS.map((prov) => {
                    const status = draft.keys_status?.[prov] || { configured: false, masked: "" };
                    const isConfigured = status.configured;
                    const maskedKey = status.masked;
                    
                    const isCleared = pendingKeys[prov] === null;
                    const hasPendingValue = typeof pendingKeys[prov] === "string";
                    const displayConfigured = isConfigured && !isCleared;
                    
                    return (
                      <div key={prov} className="p-3 border border-[#E9E6DC] rounded-sm bg-[#FBFBFA] space-y-2 flex flex-col justify-between">
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-xs font-bold text-[#0B1B26] uppercase tracking-wide">
                              {PROVIDER_LABELS[prov] || prov}
                            </span>
                            <span className="text-[10px]">
                              {displayConfigured ? (
                                <span className="font-mono text-[#16A34A] bg-[#E8F8EE] px-1.5 py-0.5 rounded-sm font-semibold">{maskedKey || "configurada"}</span>
                              ) : (
                                <span className="font-mono text-[#888888] bg-[#F1F0EA] px-1.5 py-0.5 rounded-sm font-semibold">heredada del asistente</span>
                              )}
                            </span>
                          </div>
                        </div>
                        
                        <div className="space-y-1">
                          <div className="flex gap-2">
                            <Input
                              type="password"
                              placeholder={isConfigured && !isCleared ? "Dejar igual o reemplazar…" : "Pegá la API key dedicada aquí"}
                              value={hasPendingValue ? pendingKeys[prov] : ""}
                              onChange={(e) => {
                                const val = e.target.value;
                                setPendingKeys((prev) => ({ ...prev, [prov]: val }));
                              }}
                              className="rounded-sm h-8 text-xs flex-1 font-mono bg-white"
                            />
                            {isConfigured && !isCleared && (
                              <Button
                                variant="outline"
                                onClick={() => {
                                  setPendingKeys((prev) => ({ ...prev, [prov]: null }));
                                }}
                                className="rounded-sm h-8 px-2 text-xs border border-[#E9E6DC]"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            )}
                            {isCleared && (
                              <Button
                                variant="outline"
                                onClick={() => {
                                  setPendingKeys((prev) => {
                                    const copy = { ...prev };
                                    delete copy[prov];
                                    return copy;
                                  });
                                }}
                                className="rounded-sm h-8 px-2 text-xs border border-[#E9E6DC] text-[#0E8DDB]"
                              >
                                Deshacer
                              </Button>
                            )}
                          </div>
                          
                          {isCleared && (
                            <p className="text-[10px] text-[#DC2626]">
                              Al guardar se borrará la API Key dedicada actual y volverá a heredar la del asistente.
                            </p>
                          )}
                          {hasPendingValue && pendingKeys[prov] !== null && pendingKeys[prov].trim() !== "" && (
                            <p className="text-[10px] text-[#0E8DDB]">
                              Nueva API Key dedicada ingresada (sin guardar).
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </details>
          </div>

          {/* FAQs */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <div className="flex items-center justify-between mb-1">
              <Label className="text-sm font-bold text-[#0B1B26] flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5 text-[#0E8DDB]" /> Preguntas frecuentes (FAQ)
              </Label>
              <span className="text-xs text-[#888888]">{faqs.length} item(s)</span>
            </div>
            <p className="text-xs text-[#888888] mb-2">
              Cada par P/R se pasa al modelo para responder con precisión.
            </p>
            <div className="space-y-2">
              {faqs.map((f, i) => (
                <div key={i} className="flex items-start gap-2" data-testid={`faq-row-${i}`}>
                  <Input
                    data-testid={`faq-q-${i}`}
                    placeholder="Pregunta del cliente"
                    value={f.q || ""}
                    onChange={(e) => {
                      const next = [...faqs]; next[i] = { ...next[i], q: e.target.value };
                      set({ faqs: next });
                    }}
                    className="rounded-sm h-9 text-xs"
                  />
                  <Input
                    data-testid={`faq-a-${i}`}
                    placeholder="Respuesta a usar"
                    value={f.a || ""}
                    onChange={(e) => {
                      const next = [...faqs]; next[i] = { ...next[i], a: e.target.value };
                      set({ faqs: next });
                    }}
                    className="rounded-sm h-9 text-xs"
                  />
                  <Button
                    data-testid={`faq-remove-${i}`}
                    variant="outline"
                    onClick={() => set({ faqs: faqs.filter((_, j) => j !== i) })}
                    className="rounded-sm h-9 px-2"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
            </div>
            <Button
              data-testid="faq-add"
              variant="outline"
              onClick={() => set({ faqs: [...faqs, { q: "", a: "" }] })}
              className="rounded-sm mt-2 h-8 text-xs"
            >
              <Plus className="h-3.5 w-3.5 mr-1" /> Agregar pregunta
            </Button>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 mt-6 pt-4 border-t border-[#E9E6DC]">
          <Button
            data-testid="bot-settings-reset"
            variant="outline"
            onClick={() => { setDraft({ ...q.data }); setPendingKeys({}); }}
            disabled={!dirty}
            className="rounded-sm"
          >
            Descartar cambios
          </Button>
          <Button
            data-testid="bot-settings-save"
            onClick={onSave}
            disabled={!dirty || save.isPending}
            className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm"
          >
            {save.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
            Guardar cambios
          </Button>
        </div>
      </div>

      <details className="bg-latus-cream border border-[#E9E6DC] rounded-sm px-4 py-3" data-testid="bot-help-details">
        <summary className="cursor-pointer text-sm font-bold text-[#0B1B26] flex items-center gap-2">
          <Lightbulb className="h-3.5 w-3.5 text-[#0E8DDB]" /> Cómo funciona el bot
        </summary>
        <ul className="list-disc pl-5 space-y-1 mt-3 text-xs text-[#888888]">
          <li>Cada mensaje entrante del cliente en WhatsApp dispara una llamada al modelo configurado en el sistema.</li>
          <li>El bot decide si responder, derivar a humano, actualizar estado del lead, o no hacer nada.</li>
          <li>Si detecta DNI/CBU/tarjeta o si el cliente pide hablar con un humano, deriva sin contestar.</li>
          <li>Si la confianza es menor al umbral configurado, deriva a humano.</li>
          <li>Cada conversación tiene su <b>resumen</b> y <b>intención</b> detectada visibles en la Bandeja.</li>
        </ul>
      </details>
    </div>
  );
}


// =============================================================================
// AI & AUTOMATIZACIÓN TAB (Phase 1 — multi-provider config)
// =============================================================================
const PROVIDER_LABELS = {
  built_in:       "Sistema (incluido)",
  openai:         "OpenAI",
  anthropic:      "Anthropic (Claude)",
  gemini:         "Google Gemini",
  openrouter:     "OpenRouter",
  custom_openai:  "Otro (compatible OpenAI)",
};

const KEY_REQUIRED_PROVIDERS = ["openai", "anthropic", "gemini", "openrouter", "custom_openai"];

function AIAutoTab() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin-ai-provider"],
    queryFn: () => api.get("/admin/ai-provider").then((r) => r.data),
  });
  const [draft, setDraft] = useState(null);
  const [pendingKeys, setPendingKeys] = useState({});
  useEffect(() => {
    if (q.data && draft === null) {
      setDraft({ ...q.data });
      setPendingKeys({});
    }
  }, [q.data, draft]);

  const save = useMutation({
    mutationFn: (payload) => api.put("/admin/ai-provider", payload),
    onSuccess: (r) => {
      setDraft({ ...r.data });
      setPendingKeys({});
      qc.invalidateQueries({ queryKey: ["admin-ai-provider"] });
      toast.success("Cambios guardados");
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudieron guardar los cambios"),
  });

  const test = useMutation({
    mutationFn: () => api.post("/admin/ai-provider/test").then((r) => r.data),
    onSuccess: (d) => {
      if (d.ok) toast.success(`Conexión OK · ${d.latency_ms}ms`);
      else toast.error(`No funcionó: ${d.error}`);
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "Error al probar la conexión"),
  });

  if (q.isPending || !draft) return <div className="text-[#888888]">Cargando…</div>;

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));
  const providers = draft.supported_providers || Object.keys(PROVIDER_LABELS);
  const needsKey = draft.provider !== "built_in";
  const needsBaseUrl = draft.provider === "custom_openai";
  const suggestionsList = (draft.model_suggestions || {})[draft.provider] || [];
  const temp = Number(draft.temperature ?? 0.2);
  const maxTok = Number(draft.max_tokens ?? 900);
  const minConf = Number(draft.min_confidence_for_auto_reply ?? 0.7);
  const tempBad = !(temp >= 0 && temp <= 2);
  const maxTokBad = !(maxTok >= 100 && maxTok <= 4096);
  const minConfBad = !(minConf >= 0 && minConf <= 1);

  const onSave = () => {
    if (tempBad)   { toast.error("La temperatura debe estar entre 0 y 2"); return; }
    if (maxTokBad) { toast.error("El máximo de tokens debe estar entre 100 y 4096"); return; }
    if (minConfBad){ toast.error("El umbral de confianza debe estar entre 0 y 1"); return; }
    if (needsBaseUrl && !(draft.base_url || "").trim()) {
      toast.error("Para 'Otro (compatible OpenAI)' la URL base es obligatoria"); return;
    }
    
    // Check if key is configured for the active provider
    const activeKeyStatus = draft.keys_status?.[draft.provider] || { configured: false };
    const isCleared = pendingKeys[draft.provider] === null;
    const hasPendingValue = typeof pendingKeys[draft.provider] === "string" && pendingKeys[draft.provider].trim() !== "";
    const activeKeyConfigured = activeKeyStatus.configured && !isCleared;
    
    if (needsKey && !activeKeyConfigured && !hasPendingValue) {
      toast.error("Ingresá la API Key del proveedor activo"); return;
    }
    
    const payload = {
      provider: draft.provider,
      model: draft.model,
      base_url: draft.base_url || "",
      temperature: temp,
      max_tokens: maxTok,
      system_prompt_base: draft.system_prompt_base || "",
      ai_enabled: !!draft.ai_enabled,
      whatsapp_auto_reply_enabled: !!draft.whatsapp_auto_reply_enabled,
      auto_handoff_enabled: !!draft.auto_handoff_enabled,
      min_confidence_for_auto_reply: minConf,
    };
    
    // Construct api_keys dictionary to patch
    const apiKeysPayload = {};
    Object.entries(pendingKeys).forEach(([prov, val]) => {
      if (val === null) apiKeysPayload[prov] = null;
      else if (val.trim()) apiKeysPayload[prov] = val.trim();
    });
    if (Object.keys(apiKeysPayload).length > 0) {
      payload.api_keys = apiKeysPayload;
    }
    
    save.mutate(payload);
  };

  const dirty = JSON.stringify(draft) !== JSON.stringify(q.data) || Object.keys(pendingKeys).length > 0;

  return (
    <div className="space-y-6" data-testid="ai-auto-tab">
      <div className="bg-white border border-[#E9E6DC] rounded-sm p-5">
        <div className="flex items-center gap-2 mb-1">
          <Sparkles className="h-5 w-5 text-[#0E8DDB]" />
          <h2 className="text-xl font-bold tracking-tight text-[#0B1B26]">IA y automatización</h2>
        </div>
        <p className="text-sm text-[#888888] mb-5">
          Elegí qué proveedor de IA usa el asistente, ajustá costos/calidad y controlá
          la automatización en WhatsApp.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {/* AI enabled */}
          <div className="flex items-start justify-between gap-4 p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <div>
              <Label className="text-sm font-bold text-[#0B1B26]">IA activa</Label>
              <p className="text-xs text-[#888888] mt-0.5">
                Apaga completamente las llamadas al proveedor (resumen, sugerencias y bot).
              </p>
            </div>
            <Switch
              data-testid="ai-setting-enabled"
              checked={!!draft.ai_enabled}
              onCheckedChange={(v) => set({ ai_enabled: v })}
            />
          </div>

          {/* Provider */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Proveedor</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Por defecto se usa el sistema incorporado con la clave universal. Configurá tu propio
              proveedor para usar tu cuenta directa.
            </p>
            <Select value={draft.provider} onValueChange={(v) => {
              const newSuggestions = (draft.model_suggestions || {})[v] || [];
              const defaultModel = newSuggestions[0] || "";
              set({ provider: v, model: defaultModel });
              setKeyAction("keep");
            }}>
              <SelectTrigger data-testid="ai-setting-provider" className="rounded-sm h-9 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p} value={p}>{PROVIDER_LABELS[p] || p}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Model */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Modelo</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Seleccioná un modelo o ingresá uno personalizado.
            </p>
            {suggestionsList.length > 0 ? (
              <div className="space-y-2">
                <Select
                  value={suggestionsList.includes(draft.model) ? draft.model : "custom"}
                  onValueChange={(v) => {
                    if (v === "custom") {
                      if (suggestionsList.includes(draft.model)) {
                        set({ model: "" });
                      }
                    } else {
                      set({ model: v });
                    }
                  }}
                >
                  <SelectTrigger data-testid="ai-setting-model-select" className="rounded-sm h-9 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {suggestionsList.map((m) => (
                      <SelectItem key={m} value={m}>{m}</SelectItem>
                    ))}
                    <SelectItem value="custom">Otro modelo (personalizado)</SelectItem>
                  </SelectContent>
                </Select>

                {(!suggestionsList.includes(draft.model) || draft.model === "") && (
                  <Input
                    data-testid="ai-setting-model-custom"
                    value={draft.model || ""}
                    onChange={(e) => set({ model: e.target.value })}
                    className="rounded-sm h-9 mt-2 font-mono"
                    placeholder="Escribí el identificador del modelo..."
                  />
                )}
              </div>
            ) : (
              <Input
                data-testid="ai-setting-model"
                value={draft.model || ""}
                onChange={(e) => set({ model: e.target.value })}
                className="rounded-sm h-9 font-mono"
                placeholder="Identificador del modelo"
              />
            )}
          </div>

          {/* API key configuration per provider */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2 space-y-4">
            <div>
              <Label className="text-sm font-bold text-[#0B1B26]">Configuración de API Keys (Múltiples Proveedores)</Label>
              <p className="text-xs text-[#888888] mt-0.5">
                Podés guardar las claves de API de los diferentes proveedores de forma paralela. Las claves se almacenan de manera cifrada y segura.
              </p>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
              {KEY_REQUIRED_PROVIDERS.map((prov) => {
                const status = draft.keys_status?.[prov] || { configured: false, masked: "" };
                const isConfigured = status.configured;
                const maskedKey = status.masked;
                
                const isCleared = pendingKeys[prov] === null;
                const hasPendingValue = typeof pendingKeys[prov] === "string";
                const displayConfigured = isConfigured && !isCleared;
                
                return (
                  <div key={prov} className="p-3 border border-[#E9E6DC] rounded-sm bg-[#FBFBFA] space-y-2 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs font-bold text-[#0B1B26] uppercase tracking-wide">
                          {PROVIDER_LABELS[prov] || prov}
                        </span>
                        <span className="text-[10px]">
                          {displayConfigured ? (
                            <span className="font-mono text-[#16A34A] bg-[#E8F8EE] px-1.5 py-0.5 rounded-sm font-semibold">{maskedKey || "configurada"}</span>
                          ) : (
                            <span className="font-mono text-[#888888] bg-[#F1F0EA] px-1.5 py-0.5 rounded-sm font-semibold">no configurada</span>
                          )}
                        </span>
                      </div>
                    </div>
                    
                    <div className="space-y-1">
                      <div className="flex gap-2">
                        <Input
                          type="password"
                          placeholder={isConfigured && !isCleared ? "Dejar igual o reemplazar…" : "Pegá la API key aquí"}
                          value={hasPendingValue ? pendingKeys[prov] : ""}
                          onChange={(e) => {
                            const val = e.target.value;
                            setPendingKeys((prev) => ({ ...prev, [prov]: val }));
                          }}
                          className="rounded-sm h-8 text-xs flex-1 font-mono bg-white"
                        />
                        {isConfigured && !isCleared && (
                          <Button
                            variant="outline"
                            onClick={() => {
                              setPendingKeys((prev) => ({ ...prev, [prov]: null }));
                            }}
                            className="rounded-sm h-8 px-2 text-xs border border-[#E9E6DC]"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        {isCleared && (
                          <Button
                            variant="outline"
                            onClick={() => {
                              setPendingKeys((prev) => {
                                const copy = { ...prev };
                                delete copy[prov];
                                return copy;
                              });
                            }}
                            className="rounded-sm h-8 px-2 text-xs border border-[#E9E6DC] text-[#0E8DDB]"
                          >
                            Deshacer
                          </Button>
                        )}
                      </div>
                      
                      {isCleared && (
                        <p className="text-[10px] text-[#DC2626]">
                          Al guardar se borrará la API Key actual.
                        </p>
                      )}
                      {hasPendingValue && pendingKeys[prov] !== null && pendingKeys[prov].trim() !== "" && (
                        <p className="text-[10px] text-[#0E8DDB]">
                          Nueva API Key ingresada (sin guardar).
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Base URL */}
          {needsBaseUrl && (
            <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
              <Label className="text-sm font-bold text-[#0B1B26]">URL base</Label>
              <p className="text-xs text-[#888888] mt-0.5 mb-2">
                Endpoint compatible con OpenAI (ej. <span className="font-mono">https://api.together.xyz/v1</span>).
              </p>
              <Input
                data-testid="ai-setting-baseurl"
                value={draft.base_url || ""}
                onChange={(e) => set({ base_url: e.target.value })}
                className="rounded-sm h-9 font-mono"
                placeholder="https://…/v1"
              />
            </div>
          )}

          {/* Temperature */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <div className="flex items-center justify-between mb-1">
              <Label className="text-sm font-bold text-[#0B1B26]">Temperatura</Label>
              <span data-testid="ai-setting-temp-value" className="text-sm font-mono font-bold text-[#0E8DDB]">
                {temp.toFixed(1)}
              </span>
            </div>
            <p className="text-xs text-[#888888] mb-2">
              0 = determinístico, 2 = muy creativo. Recomendado: 0.2 – 0.5.
            </p>
            <input
              data-testid="ai-setting-temp"
              type="range" min="0" max="2" step="0.1"
              value={temp}
              onChange={(e) => set({ temperature: parseFloat(e.target.value) })}
              className="w-full accent-[#0E8DDB]"
            />
            {tempBad && <p className="text-[11px] text-[#DC2626] mt-1">Debe estar entre 0 y 2.</p>}
          </div>

          {/* Max tokens */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm">
            <Label className="text-sm font-bold text-[#0B1B26]">Máximo de tokens por respuesta</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Límite de tokens generados (100 a 4096).
            </p>
            <Input
              data-testid="ai-setting-maxtokens"
              type="number" min="100" max="4096"
              value={maxTok}
              onChange={(e) => set({ max_tokens: parseInt(e.target.value, 10) || 0 })}
              className="rounded-sm h-9"
            />
            {maxTokBad && <p className="text-[11px] text-[#DC2626] mt-1">Debe estar entre 100 y 4096.</p>}
          </div>

          {/* Min confidence */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <div className="flex items-center justify-between mb-1">
              <Label className="text-sm font-bold text-[#0B1B26]">Umbral mínimo de confianza para responder automáticamente</Label>
              <span data-testid="ai-setting-conf-value" className="text-sm font-mono font-bold text-[#0E8DDB]">
                {minConf.toFixed(2)}
              </span>
            </div>
            <p className="text-xs text-[#888888] mb-2">
              Si la confianza del bot es menor a este valor, deriva a humano en vez de responder.
            </p>
            <input
              data-testid="ai-setting-conf"
              type="range" min="0" max="1" step="0.05"
              value={minConf}
              onChange={(e) => set({ min_confidence_for_auto_reply: parseFloat(e.target.value) })}
              className="w-full accent-[#0E8DDB]"
            />
            {minConfBad && <p className="text-[11px] text-[#DC2626] mt-1">Debe estar entre 0 y 1.</p>}
          </div>

          {/* Switches: auto-reply + auto-handoff */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm flex items-start justify-between gap-3">
            <div>
              <Label className="text-sm font-bold text-[#0B1B26]">Respuesta automática por WhatsApp</Label>
              <p className="text-xs text-[#888888] mt-0.5">
                Si está apagado, el bot detecta intent y resume, pero no envía mensajes.
              </p>
            </div>
            <Switch
              data-testid="ai-setting-autoreply"
              checked={!!draft.whatsapp_auto_reply_enabled}
              onCheckedChange={(v) => set({ whatsapp_auto_reply_enabled: v })}
            />
          </div>
          <div className="p-3 border border-[#E9E6DC] rounded-sm flex items-start justify-between gap-3">
            <div>
              <Label className="text-sm font-bold text-[#0B1B26]">Handoff automático a humano</Label>
              <p className="text-xs text-[#888888] mt-0.5">
                Si está apagado, el bot no cierra la conversación al solicitar humano; solo notifica.
              </p>
            </div>
            <Switch
              data-testid="ai-setting-autohandoff"
              checked={!!draft.auto_handoff_enabled}
              onCheckedChange={(v) => set({ auto_handoff_enabled: v })}
            />
          </div>

          {/* System prompt base */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <Label className="text-sm font-bold text-[#0B1B26]">Prompt base del asistente</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Texto que se antepone a las instrucciones del bot. Útil para personalidad/branding global.
            </p>
            <Textarea
              data-testid="ai-setting-promptbase"
              value={draft.system_prompt_base || ""}
              onChange={(e) => set({ system_prompt_base: e.target.value })}
              className="rounded-sm min-h-[110px] font-mono text-xs"
              placeholder="Sos el asistente oficial de Latus. Tu objetivo es ayudar al cliente y derivar a un humano cuando sea necesario."
            />
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 mt-6 pt-4 border-t border-[#E9E6DC]">
          <Button
            data-testid="ai-test-button"
            variant="outline"
            onClick={() => test.mutate()}
            disabled={test.isPending || !draft.ai_enabled || (needsKey && !(draft.keys_status?.[draft.provider]?.configured && pendingKeys[draft.provider] !== null) && !(typeof pendingKeys[draft.provider] === "string" && pendingKeys[draft.provider].trim() !== ""))}
            className="rounded-sm"
          >
            {test.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : <CheckCircle2 className="h-3.5 w-3.5 mr-1" />}
            Probar IA
          </Button>
          <div className="flex items-center gap-2">
            <Button
              data-testid="ai-settings-reset"
              variant="outline"
              onClick={() => { setDraft({ ...q.data }); setPendingKeys({}); }}
              disabled={!dirty}
              className="rounded-sm"
            >
              Descartar cambios
            </Button>
            <Button
              data-testid="ai-settings-save"
              onClick={onSave}
              disabled={!dirty || save.isPending}
              className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm"
            >
              {save.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
              Guardar
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}


// =============================================================================
// WORK AREAS TAB
// =============================================================================
function WorkAreasTab() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newRules, setNewRules] = useState("");
  const [deleteTarget, setDeleteTarget] = useState(null);

  const workAreasQ = useQuery({
    queryKey: ["work-areas"],
    queryFn: () => api.get("/admin/work-areas").then((r) => r.data),
  });

  const createArea = useMutation({
    mutationFn: (payload) => api.post("/admin/work-areas", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["work-areas"] });
      toast.success("Área de trabajo creada con éxito");
      setShowCreate(false);
      setNewId("");
      setNewName("");
      setNewDesc("");
      setNewRules("");
    },
    onError: (err) => {
      toast.error(err.response?.data?.detail || "No se pudo crear el área de trabajo");
    },
  });

  const deleteArea = useMutation({
    mutationFn: (id) => api.delete(`/admin/work-areas/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["work-areas"] });
      toast.success("Área de trabajo eliminada");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err.response?.data?.detail || "No se pudo eliminar el área de trabajo");
    },
  });

  const handleCreateSubmit = (e) => {
    e.preventDefault();
    if (!newId.trim() || !newName.trim()) {
      toast.error("El ID y el Nombre son requeridos");
      return;
    }
    createArea.mutate({
      id: newId.trim(),
      name: newName.trim(),
      description: newDesc.trim(),
      routing_rules: newRules.trim(),
    });
  };

  const areas = workAreasQ.data || [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-[#0B1B26]">Áreas de Trabajo</h2>
          <p className="text-xs text-[#888888]">
            Gestioná las áreas o departamentos de la empresa para la asignación y derivación inteligente de chats.
          </p>
        </div>
        <Button
          onClick={() => setShowCreate(true)}
          className="bg-[#0E8DDB] hover:bg-[#0B72B2] text-white flex items-center gap-1.5 rounded-sm"
          data-testid="create-work-area-btn"
        >
          <Plus className="h-4 w-4" /> Nueva Área
        </Button>
      </div>

      {workAreasQ.isLoading ? (
        <div className="text-center py-8 text-xs text-[#888888]">Cargando áreas de trabajo...</div>
      ) : areas.length === 0 ? (
        <div className="border border-dashed border-[#E9E6DC] rounded-sm p-8 text-center bg-latus-cream">
          <Building2 className="h-8 w-8 mx-auto text-latus-muted mb-2" />
          <p className="text-sm font-semibold text-[#0B1B26]">No hay áreas de trabajo creadas</p>
          <p className="text-xs text-[#888888] mt-1">
            Creá áreas para poder asignar agentes y configurar reglas de derivación específicas para el bot.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {areas.map((wa) => (
            <div key={wa.id} className="border border-[#E9E6DC] bg-white rounded-sm p-4 shadow-sm hover:shadow-md transition-shadow flex flex-col justify-between" data-testid={`work-area-card-${wa.id}`}>
              <div>
                <div className="flex items-center justify-between gap-2 border-b border-[#F4F2EC] pb-2 mb-3">
                  <div>
                    <h3 className="font-bold text-[#0B1B26] text-sm flex items-center gap-1.5">
                      <Building2 className="h-4 w-4 text-[#0E8DDB]" />
                      {wa.name}
                    </h3>
                    <code className="text-[10px] bg-[#F4F2EC] text-[#888888] px-1 py-px rounded font-mono uppercase">
                      ID: {wa.id}
                    </code>
                  </div>
                  <Button
                    variant="ghost"
                    onClick={() => setDeleteTarget(wa.id)}
                    className="h-8 w-8 p-0 text-[#E15151] hover:bg-[#FDF2F2]"
                    data-testid={`delete-work-area-${wa.id}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                <div className="space-y-3">
                  <div>
                    <span className="text-[10px] font-bold text-[#888888] block uppercase">Descripción</span>
                    <p className="text-xs text-[#444444] mt-0.5 line-clamp-2">
                      {wa.description || <span className="italic text-[#888888]">(Sin descripción)</span>}
                    </p>
                  </div>
                  <div>
                    <span className="text-[10px] font-bold text-[#888888] block uppercase">Reglas del Bot</span>
                    <p className="text-xs text-[#444444] mt-0.5 whitespace-pre-line line-clamp-3 bg-latus-cream p-2 rounded-sm border border-[#E9E6DC] italic font-mono text-[11px]">
                      {wa.routing_rules || <span className="text-[#888888]">(Sin reglas específicas. El bot derivará usando las reglas generales)</span>}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* CREATE DIALOG */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-w-md bg-white border border-[#E9E6DC] rounded-sm">
          <DialogHeader>
            <DialogTitle className="text-[#0B1B26] font-bold text-base">Crear Área de Trabajo</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreateSubmit} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-[#0B1B26]">ID del Área (slug único)</Label>
              <Input
                placeholder="ej: administracion, finanzas, soporte"
                value={newId}
                onChange={(e) => setNewId(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
                className="h-9 rounded-sm border-[#E9E6DC]"
                required
                data-testid="work-area-id-input"
              />
              <p className="text-[10px] text-[#888888]">Solo letras minúsculas, números, guiones y guiones bajos.</p>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-[#0B1B26]">Nombre del Área</Label>
              <Input
                placeholder="ej: Administración, Cobranzas y Finanzas"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="h-9 rounded-sm border-[#E9E6DC]"
                required
                data-testid="work-area-name-input"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-[#0B1B26]">Descripción</Label>
              <Textarea
                placeholder="Breve descripción interna..."
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                className="rounded-sm border-[#E9E6DC] resize-none"
                rows={2}
                data-testid="work-area-desc-input"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-[#0B1B26] flex items-center gap-1">
                <Bot className="h-3 w-3 text-[#0E8DDB]" /> Instrucciones de derivación para el Bot IA
              </Label>
              <Textarea
                placeholder="ej: Derivar cuando el cliente pregunte por facturas, estados de cuenta, transferencias, comprobantes de pago o CBU."
                value={newRules}
                onChange={(e) => setNewRules(e.target.value)}
                className="rounded-sm border-[#E9E6DC]"
                rows={3}
                data-testid="work-area-rules-input"
              />
              <p className="text-[10px] text-[#888888]">
                El Bot usará estas instrucciones para identificar consultas correspondientes a este departamento y derivar automáticamente.
              </p>
            </div>
            <DialogFooter className="mt-6">
              <Button
                type="button"
                variant="outline"
                onClick={() => setShowCreate(false)}
                className="rounded-sm h-9 border-[#E9E6DC] text-[#444444]"
              >
                Cancelar
              </Button>
              <Button
                type="submit"
                disabled={createArea.isPending}
                className="bg-[#0E8DDB] hover:bg-[#0B72B2] text-white rounded-sm h-9 px-4"
                data-testid="submit-work-area-btn"
              >
                {createArea.isPending ? "Creando..." : "Crear Área"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* DELETE CONFIRM DIALOG */}
      <AlertDialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <AlertDialogContent className="bg-white border border-[#E9E6DC] rounded-sm max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-[#0B1B26] font-bold text-sm">¿Eliminar área de trabajo?</AlertDialogTitle>
            <AlertDialogDescription className="text-xs text-[#888888]">
              Esta acción no se puede deshacer. El área será removida de todos los usuarios asignados a ella.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-sm h-8 text-xs border-[#E9E6DC]">Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteArea.mutate(deleteTarget)}
              className="bg-[#E15151] hover:bg-[#C93B3B] text-white rounded-sm h-8 text-xs px-4"
              data-testid="confirm-delete-work-area"
            >
              Eliminar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}


// =============================================================================
// ROLES TAB
// =============================================================================
function RolesTab() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newRoleId, setNewRoleId] = useState("");
  const [newRoleName, setNewRoleName] = useState("");
  const [newRolePerms, setNewRolePerms] = useState([]);

  const rolesQ = useQuery({
    queryKey: ["roles"],
    queryFn: () => api.get("/roles").then((r) => r.data),
  });

  const updateRole = useMutation({
    mutationFn: ({ role_id, permissions, name }) =>
      api.put(`/roles/${role_id}`, { permissions, name }),
    onSuccess: () => {
      toast.success("Permisos actualizados");
      qc.invalidateQueries({ queryKey: ["roles"] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "Error al actualizar"),
  });

  const createRole = useMutation({
    mutationFn: (payload) => api.post("/roles", payload),
    onSuccess: () => {
      toast.success("Rol creado");
      qc.invalidateQueries({ queryKey: ["roles"] });
      setShowCreate(false);
      setNewRoleId("");
      setNewRoleName("");
      setNewRolePerms([]);
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "Error al crear rol"),
  });

  const deleteRole = useMutation({
    mutationFn: (role_id) => api.delete(`/roles/${role_id}`),
    onSuccess: () => {
      toast.success("Rol eliminado");
      qc.invalidateQueries({ queryKey: ["roles"] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "Error al eliminar"),
  });

  const togglePerm = (role, perm) => {
    const current = role.permissions || [];
    const next = current.includes(perm)
      ? current.filter((p) => p !== perm)
      : [...current, perm];
    updateRole.mutate({ role_id: role.role_id, permissions: next, name: role.name });
  };

  const roles = rolesQ.data || [];

  return (
    <div data-testid="roles-tab" className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold tracking-tight text-[#0B1B26]">Roles y Accesos</h2>
          <p className="text-sm text-[#888888] mt-0.5">Definí qué puede hacer cada rol en el sistema</p>
        </div>
        <Button
          data-testid="create-role-btn"
          onClick={() => setShowCreate(true)}
          className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm"
        >
          <Plus className="h-4 w-4 mr-1" /> Nuevo rol
        </Button>
      </div>

      {/* Roles Grid */}
      <div className="bg-white border border-[#E9E6DC] rounded-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#E9E6DC] bg-[#F9F8F6]">
                <th className="text-left px-4 py-3 font-bold text-[#0B1B26] text-xs uppercase tracking-wider sticky left-0 bg-[#F9F8F6] z-10 min-w-[160px]">Rol</th>
                {ALL_PERMISSIONS.map((p) => (
                  <th key={p.key} className="text-center px-3 py-3 font-semibold text-[#888888] text-[10px] uppercase tracking-wider min-w-[100px]">
                    <div>{p.label}</div>
                  </th>
                ))}
                <th className="w-12"></th>
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => (
                <tr key={role.role_id} className="border-b border-[#E9E6DC] last:border-0 hover:bg-[#F9F8F6] transition-colors">
                  <td className="px-4 py-3 sticky left-0 bg-white z-10">
                    <div className="font-bold text-[#0B1B26]">{role.name}</div>
                    <div className="text-[10px] text-[#888888] font-mono">{role.role_id}</div>
                  </td>
                  {ALL_PERMISSIONS.map((p) => {
                    const has = (role.permissions || []).includes(p.key);
                    return (
                      <td key={p.key} className="text-center px-3 py-3">
                        <button
                          data-testid={`perm-${role.role_id}-${p.key}`}
                          onClick={() => togglePerm(role, p.key)}
                          className={`h-7 w-7 rounded-sm border inline-flex items-center justify-center transition-all duration-150 ${
                            has
                              ? "bg-[#064E3B] border-[#064E3B] text-white shadow-sm"
                              : "border-zinc-300 text-transparent hover:border-[#0E8DDB] hover:bg-[#EFF6FF]"
                          }`}
                        >
                          <Check className="h-4 w-4" strokeWidth={3} />
                        </button>
                      </td>
                    );
                  })}
                  <td className="px-2 py-3">
                    {!role.is_default && (
                      <button
                        data-testid={`delete-role-${role.role_id}`}
                        onClick={() => deleteRole.mutate(role.role_id)}
                        className="text-zinc-400 hover:text-red-500 transition-colors p-1"
                        title="Eliminar rol"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Permission descriptions */}
      <div className="bg-[#F9F8F6] border border-[#E9E6DC] rounded-sm p-4">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[#888888] mb-3">Referencia de permisos</h3>
        <div className="grid grid-cols-2 gap-2">
          {ALL_PERMISSIONS.map((p) => (
            <div key={p.key} className="flex items-start gap-2">
              <span className="font-mono text-[10px] bg-white px-1.5 py-0.5 border border-[#E9E6DC] rounded-sm text-[#0E8DDB] whitespace-nowrap shrink-0">{p.key}</span>
              <span className="text-xs text-[#888888]">{p.desc}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Create Role Dialog */}
      <Dialog open={showCreate} onOpenChange={(o) => !o && setShowCreate(false)}>
        <DialogContent className="rounded-sm sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Nuevo Rol</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="text-xs font-semibold">Identificador (slug)</Label>
              <Input
                data-testid="new-role-id"
                placeholder="ej: gestor_catalogo"
                value={newRoleId}
                onChange={(e) => setNewRoleId(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))}
                className="rounded-sm mt-1 font-mono"
              />
            </div>
            <div>
              <Label className="text-xs font-semibold">Nombre visible</Label>
              <Input
                data-testid="new-role-name"
                placeholder="ej: Gestor de Catálogo"
                value={newRoleName}
                onChange={(e) => setNewRoleName(e.target.value)}
                className="rounded-sm mt-1"
              />
            </div>
            <div>
              <Label className="text-xs font-semibold">Permisos</Label>
              <div className="mt-2 space-y-1.5">
                {ALL_PERMISSIONS.map((p) => {
                  const active = newRolePerms.includes(p.key);
                  return (
                    <button
                      key={p.key}
                      type="button"
                      data-testid={`new-role-perm-${p.key}`}
                      onClick={() => {
                        setNewRolePerms((prev) =>
                          active ? prev.filter((x) => x !== p.key) : [...prev, p.key]
                        );
                      }}
                      className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-sm border text-left text-sm transition-colors ${
                        active
                          ? "bg-[#064E3B]/5 border-[#064E3B] text-[#064E3B]"
                          : "bg-white border-zinc-200 text-[#888888] hover:border-[#0E8DDB]"
                      }`}
                    >
                      <span className={`h-4 w-4 rounded-sm border flex items-center justify-center shrink-0 ${
                        active ? "bg-[#064E3B] border-[#064E3B]" : "border-zinc-300"
                      }`}>
                        {active && <Check className="h-3 w-3 text-white" strokeWidth={3} />}
                      </span>
                      <span className="font-semibold">{p.label}</span>
                      <span className="text-xs text-[#888888] ml-auto">{p.desc}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
          <DialogFooter className="mt-4">
            <Button variant="outline" className="rounded-sm" onClick={() => setShowCreate(false)}>Cancelar</Button>
            <Button
              data-testid="save-new-role"
              className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm"
              disabled={!newRoleId.trim() || !newRoleName.trim() || createRole.isPending}
              onClick={() => createRole.mutate({ role_id: newRoleId, name: newRoleName, permissions: newRolePerms })}
            >
              {createRole.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
              Crear
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}


// =============================================================================
// TASKS + CATALOG SETTINGS TAB
// =============================================================================
function CRMConfigTab() {
  const qc = useQueryClient();
  const settingsQ = useQuery({
    queryKey: ["admin-settings"],
    queryFn: () => api.get("/admin/settings").then((r) => r.data),
  });
  const [taskDraft, setTaskDraft] = useState([]);
  const [taskLabel, setTaskLabel] = useState("");
  const [taskIsDone, setTaskIsDone] = useState(false);
  const [taskColor, setTaskColor] = useState("#52525B");
  const [categoryDraft, setCategoryDraft] = useState([]);
  const [categoryColorsDraft, setCategoryColorsDraft] = useState({});
  const [categoryName, setCategoryName] = useState("");
  const [categoryColor, setCategoryColor] = useState("#52525B");

  useEffect(() => {
    if (settingsQ.data) {
      setTaskDraft(settingsQ.data.task_statuses || []);
      setCategoryDraft(settingsQ.data.catalog_categories || []);
      setCategoryColorsDraft(settingsQ.data.catalog_category_colors || {});
    }
  }, [settingsQ.data]);

  const save = useMutation({
    mutationFn: () => api.patch("/admin/settings", {
      task_statuses: taskDraft,
      catalog_categories: categoryDraft,
      catalog_category_colors: categoryColorsDraft,
    }),
    onSuccess: () => {
      toast.success("Configuración actualizada");
      qc.invalidateQueries({ queryKey: ["admin-settings"] });
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["catalog-categories"] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo guardar la configuración"),
  });

  const addTaskStatus = () => {
    const label = taskLabel.trim();
    const key = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    if (!label || !key) return;
    if (taskDraft.some((item) => item.key === key)) {
      toast.error("Ese estado ya existe");
      return;
    }
    setTaskDraft((prev) => [...prev, { key, label, is_done: taskIsDone, color: taskColor }]);
    setTaskLabel("");
    setTaskIsDone(false);
    setTaskColor("#52525B");
  };

  const addCategory = () => {
    const value = categoryName.trim();
    if (!value) return;
    if (categoryDraft.some((item) => item.toLowerCase() === value.toLowerCase())) {
      toast.error("Esa categoría ya existe");
      return;
    }
    setCategoryDraft((prev) => [...prev, value].sort((a, b) => a.localeCompare(b)));
    setCategoryColorsDraft((prev) => ({ ...prev, [value]: categoryColor }));
    setCategoryName("");
    setCategoryColor("#52525B");
  };

  const dirty = JSON.stringify(taskDraft) !== JSON.stringify(settingsQ.data?.task_statuses || [])
    || JSON.stringify(categoryDraft) !== JSON.stringify(settingsQ.data?.catalog_categories || [])
    || JSON.stringify(categoryColorsDraft) !== JSON.stringify(settingsQ.data?.catalog_category_colors || {});

  return (
    <div data-testid="crm-config-tab" className="space-y-6">
      <div className="grid gap-6 xl:grid-cols-2">
        <div className="bg-white border border-[#E9E6DC] rounded-sm p-5 space-y-4">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-sm bg-[#EFF6FF] text-[#0E8DDB] flex items-center justify-center">
              <CheckSquare className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-[#0B1B26]">Estados de tareas</h3>
              <p className="text-sm text-[#888888] mt-0.5">Definí los estados disponibles para el equipo y cuáles cuentan como completados.</p>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
            <div>
              <Label className="text-xs font-semibold">Nuevo estado</Label>
              <Input
                data-testid="task-status-name"
                value={taskLabel}
                onChange={(e) => setTaskLabel(e.target.value)}
                placeholder="Ej. En espera de cliente"
                className="rounded-sm mt-1"
              />
            </div>
            <div className="flex flex-col justify-end">
              <Label className="text-xs font-semibold pb-1">Color</Label>
              <div className="flex items-center h-9 mt-1">
                <input
                  type="color"
                  value={taskColor}
                  onChange={(e) => setTaskColor(e.target.value)}
                  className="w-8 h-8 rounded-full cursor-pointer border border-[#E9E6DC] p-0 overflow-hidden bg-transparent [&::-webkit-color-swatch-wrapper]:p-0 [&::-webkit-color-swatch]:border-0 [&::-webkit-color-swatch]:rounded-full shrink-0"
                />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm text-[#888888] self-end pb-2">
              <Switch checked={taskIsDone} onCheckedChange={setTaskIsDone} />
              Marcar como completado
            </label>
          </div>
          <Button type="button" variant="outline" className="rounded-sm" onClick={addTaskStatus}>
            <Plus className="h-4 w-4 mr-1" /> Agregar estado
          </Button>

          <div className="space-y-2">
            {taskDraft.map((item) => (
              <div key={item.key} className="flex items-center gap-3 border border-[#E9E6DC] rounded-sm px-3 py-2">
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-[#0B1B26]">{item.label}</p>
                  <p className="text-[11px] text-[#888888] font-mono">{item.key}</p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="text-xs text-[#888888]">Color:</span>
                  <input
                    type="color"
                    value={item.color || "#52525B"}
                    onChange={(e) => setTaskDraft((prev) => prev.map((current) => current.key === item.key ? { ...current, color: e.target.value } : current))}
                    className="w-7 h-7 rounded-full cursor-pointer border border-[#E9E6DC] p-0 overflow-hidden bg-transparent [&::-webkit-color-swatch-wrapper]:p-0 [&::-webkit-color-swatch]:border-0 [&::-webkit-color-swatch]:rounded-full shrink-0"
                  />
                </div>
                <label className="flex items-center gap-2 text-xs text-[#888888] shrink-0">
                  <Switch
                    checked={!!item.is_done}
                    onCheckedChange={(checked) => setTaskDraft((prev) => prev.map((current) => current.key === item.key ? { ...current, is_done: checked } : current))}
                  />
                  Completado
                </label>
                <button
                  type="button"
                  data-testid={`delete-task-status-${item.key}`}
                  onClick={() => setTaskDraft((prev) => prev.filter((current) => current.key !== item.key))}
                  className="text-zinc-400 hover:text-red-500 transition-colors shrink-0"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-white border border-[#E9E6DC] rounded-sm p-5 space-y-4">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-sm bg-[#FFF7ED] text-[#FF4500] flex items-center justify-center">
              <Package className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-[#0B1B26]">Categorías del catálogo</h3>
              <p className="text-sm text-[#888888] mt-0.5">Mantené una lista unificada para que luego se seleccione en los productos.</p>
            </div>
          </div>

          <div>
            <Label className="text-xs font-semibold">Nueva categoría</Label>
            <div className="flex gap-2 mt-1 items-center">
              <Input
                data-testid="catalog-category-name"
                value={categoryName}
                onChange={(e) => setCategoryName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCategory(); } }}
                placeholder="Ej. Accesorios"
                className="rounded-sm flex-1"
              />
              <input
                type="color"
                value={categoryColor}
                onChange={(e) => setCategoryColor(e.target.value)}
                className="w-8 h-8 rounded-full cursor-pointer border border-[#E9E6DC] p-0 overflow-hidden bg-transparent [&::-webkit-color-swatch-wrapper]:p-0 [&::-webkit-color-swatch]:border-0 [&::-webkit-color-swatch]:rounded-full shrink-0"
              />
              <Button type="button" variant="outline" className="rounded-sm shrink-0" onClick={addCategory}>
                <Plus className="h-4 w-4 mr-1" /> Agregar
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {categoryDraft.map((item) => (
              <span key={item} className="inline-flex items-center gap-2 px-3 py-1.5 rounded-sm bg-[#F9F8F6] border border-[#E9E6DC] text-sm text-[#0B1B26]">
                <input
                  type="color"
                  value={categoryColorsDraft[item] || "#52525B"}
                  onChange={(e) => setCategoryColorsDraft((prev) => ({ ...prev, [item]: e.target.value }))}
                  className="w-4 h-4 rounded-full cursor-pointer border border-[#E9E6DC] p-0 overflow-hidden bg-transparent [&::-webkit-color-swatch-wrapper]:p-0 [&::-webkit-color-swatch]:border-0 [&::-webkit-color-swatch]:rounded-full shrink-0"
                />
                {item}
                <button
                  type="button"
                  data-testid={`delete-category-${item}`}
                  onClick={() => {
                    setCategoryDraft((prev) => prev.filter((current) => current !== item));
                    setCategoryColorsDraft((prev) => {
                      const next = { ...prev };
                      delete next[item];
                      return next;
                    });
                  }}
                  className="text-zinc-400 hover:text-red-500 transition-colors"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          className="rounded-sm"
          disabled={!dirty}
          onClick={() => {
            setTaskDraft(settingsQ.data?.task_statuses || []);
            setCategoryDraft(settingsQ.data?.catalog_categories || []);
            setCategoryColorsDraft(settingsQ.data?.catalog_category_colors || {});
          }}
        >
          Descartar cambios
        </Button>
        <Button
          data-testid="save-crm-config"
          className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm"
          disabled={!dirty || save.isPending || taskDraft.length === 0}
          onClick={() => save.mutate()}
        >
          {save.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
          Guardar configuración
        </Button>
      </div>
    </div>
  );
}


// =============================================================================
// EMAIL SETTINGS TAB
// =============================================================================
function EmailSettingsTab() {
  const qc = useQueryClient();
  const settingsQ = useQuery({
    queryKey: ["admin-settings"],
    queryFn: () => api.get("/admin/settings").then((r) => r.data),
  });
  const [draft, setDraft] = useState(null);
  const [testEmail, setTestEmail] = useState("");

  useEffect(() => {
    if (settingsQ.data) {
      setDraft({
        smtp_enabled: !!settingsQ.data.smtp_enabled,
        smtp_host: settingsQ.data.smtp_host || "",
        smtp_port: settingsQ.data.smtp_port || 587,
        smtp_username: settingsQ.data.smtp_username || "",
        smtp_password: "",
        smtp_from_email: settingsQ.data.smtp_from_email || "",
        smtp_from_name: settingsQ.data.smtp_from_name || "Latus CRM",
        smtp_use_tls: settingsQ.data.smtp_use_tls !== false,
        smtp_use_ssl: !!settingsQ.data.smtp_use_ssl,
        app_base_url: settingsQ.data.app_base_url || "",
        email_notif_unattended_enabled: settingsQ.data.email_notif_unattended_enabled !== false,
        email_report_daily_enabled: settingsQ.data.email_report_daily_enabled !== false,
        email_report_weekly_enabled: settingsQ.data.email_report_weekly_enabled !== false,
        email_report_monthly_enabled: settingsQ.data.email_report_monthly_enabled !== false,
      });
    }
  }, [settingsQ.data]);

  const save = useMutation({
    mutationFn: () => api.patch("/admin/settings", draft),
    onSuccess: () => {
      toast.success("Configuración de email guardada");
      qc.invalidateQueries({ queryKey: ["admin-settings"] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo guardar la configuración SMTP"),
  });
  const sendTest = useMutation({
    mutationFn: () => api.post("/admin/settings/email/test", { to_email: testEmail }),
    onSuccess: () => toast.success("Email de prueba enviado"),
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo enviar el email de prueba"),
  });

  if (!draft) {
    return <div className="text-sm text-[#888888]">Cargando configuración…</div>;
  }

  const dirty = JSON.stringify({
    ...draft,
    smtp_password: "",
  }) !== JSON.stringify({
    smtp_enabled: !!settingsQ.data?.smtp_enabled,
    smtp_host: settingsQ.data?.smtp_host || "",
    smtp_port: settingsQ.data?.smtp_port || 587,
    smtp_username: settingsQ.data?.smtp_username || "",
    smtp_password: "",
    smtp_from_email: settingsQ.data?.smtp_from_email || "",
    smtp_from_name: settingsQ.data?.smtp_from_name || "Latus CRM",
    smtp_use_tls: settingsQ.data?.smtp_use_tls !== false,
    smtp_use_ssl: !!settingsQ.data?.smtp_use_ssl,
    app_base_url: settingsQ.data?.app_base_url || "",
    email_notif_unattended_enabled: settingsQ.data?.email_notif_unattended_enabled !== false,
    email_report_daily_enabled: settingsQ.data?.email_report_daily_enabled !== false,
    email_report_weekly_enabled: settingsQ.data?.email_report_weekly_enabled !== false,
    email_report_monthly_enabled: settingsQ.data?.email_report_monthly_enabled !== false,
  }) || !!draft.smtp_password;

  const setField = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));

  return (
    <div data-testid="email-settings-tab" className="space-y-6">
      <div className="bg-white border border-[#E9E6DC] rounded-sm p-5 space-y-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h3 className="text-lg font-bold text-[#0B1B26]">SMTP del CRM</h3>
            <p className="text-sm text-[#888888] mt-0.5">Usado para bienvenida de usuarios y recuperación de contraseña.</p>
          </div>
          <label className="flex items-center gap-2 text-sm text-[#888888]">
            <Switch checked={draft.smtp_enabled} onCheckedChange={(v) => setField("smtp_enabled", v)} />
            Habilitado
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <Label className="text-xs font-semibold">Host SMTP</Label>
            <Input value={draft.smtp_host} onChange={(e) => setField("smtp_host", e.target.value)} className="rounded-sm mt-1" placeholder="smtp.resend.com" />
          </div>
          <div>
            <Label className="text-xs font-semibold">Puerto</Label>
            <Input type="number" value={draft.smtp_port} onChange={(e) => setField("smtp_port", Number(e.target.value) || 0)} className="rounded-sm mt-1" />
          </div>
          <div>
            <Label className="text-xs font-semibold">Usuario SMTP</Label>
            <Input value={draft.smtp_username} onChange={(e) => setField("smtp_username", e.target.value)} className="rounded-sm mt-1" placeholder="resend" />
          </div>
          <div>
            <Label className="text-xs font-semibold">Nueva contraseña SMTP</Label>
            <Input type="password" value={draft.smtp_password} onChange={(e) => setField("smtp_password", e.target.value)} className="rounded-sm mt-1" placeholder={settingsQ.data?.smtp_password_configured ? "Ya configurada. Escribí solo si querés reemplazarla." : ""} />
          </div>
          <div>
            <Label className="text-xs font-semibold">Email remitente</Label>
            <Input type="email" value={draft.smtp_from_email} onChange={(e) => setField("smtp_from_email", e.target.value)} className="rounded-sm mt-1" placeholder="crm@tuempresa.com" />
          </div>
          <div>
            <Label className="text-xs font-semibold">Nombre remitente</Label>
            <Input value={draft.smtp_from_name} onChange={(e) => setField("smtp_from_name", e.target.value)} className="rounded-sm mt-1" placeholder="Latus CRM" />
          </div>
          <div className="md:col-span-2">
            <Label className="text-xs font-semibold">URL pública del CRM</Label>
            <Input value={draft.app_base_url} onChange={(e) => setField("app_base_url", e.target.value)} className="rounded-sm mt-1" placeholder="https://tu-crm.com" />
            <p className="text-[11px] text-[#888888] mt-1">Se usa para generar el enlace de recuperación que llega por email.</p>
          </div>
        </div>

        <div className="flex items-center gap-4 text-sm text-[#888888]">
          <label className="flex items-center gap-2">
            <Switch
              checked={draft.smtp_use_tls}
              onCheckedChange={(v) => setDraft((prev) => ({ ...prev, smtp_use_tls: v, smtp_use_ssl: v ? false : prev.smtp_use_ssl }))}
            />
            STARTTLS
          </label>
          <label className="flex items-center gap-2">
            <Switch
              checked={draft.smtp_use_ssl}
              onCheckedChange={(v) => setDraft((prev) => ({ ...prev, smtp_use_ssl: v, smtp_use_tls: v ? false : prev.smtp_use_tls }))}
            />
            SSL directo
          </label>
        </div>

        <div className="flex justify-end gap-2">
          <div className="mr-auto flex items-center gap-2">
            <Input
              type="email"
              value={testEmail}
              onChange={(e) => setTestEmail(e.target.value)}
              className="rounded-sm w-72"
              placeholder="email para prueba"
            />
            <Button
              variant="outline"
              className="rounded-sm"
              disabled={!testEmail.trim() || sendTest.isPending}
              onClick={() => sendTest.mutate()}
            >
              {sendTest.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
              Probar envío
            </Button>
          </div>
        </div>

        <hr className="border-[#E9E6DC]" />

        <div className="space-y-4">
          <div>
            <h4 className="text-sm font-bold text-[#0B1B26]">Notificaciones y Reportes por Correo</h4>
            <p className="text-xs text-[#888888] mt-0.5">Configurá las notificaciones automáticas y el envío de reportes de leads.</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label className="flex items-center gap-3 text-sm text-[#334155] border border-[#E9E6DC] p-3 rounded-sm hover:bg-slate-50 cursor-pointer">
              <Switch
                checked={draft.email_notif_unattended_enabled}
                onCheckedChange={(v) => setField("email_notif_unattended_enabled", v)}
              />
              <div>
                <span className="font-semibold block text-[#0B1B26] text-xs">Leads sin atender</span>
                <span className="text-[11px] text-[#888888]">Notificar al mail cuando un cliente espera respuesta</span>
              </div>
            </label>

            <label className="flex items-center gap-3 text-sm text-[#334155] border border-[#E9E6DC] p-3 rounded-sm hover:bg-slate-50 cursor-pointer">
              <Switch
                checked={draft.email_report_daily_enabled}
                onCheckedChange={(v) => setField("email_report_daily_enabled", v)}
              />
              <div>
                <span className="font-semibold block text-[#0B1B26] text-xs">Reporte Diario</span>
                <span className="text-[11px] text-[#888888]">Resumen de leads de las últimas 24 horas</span>
              </div>
            </label>

            <label className="flex items-center gap-3 text-sm text-[#334155] border border-[#E9E6DC] p-3 rounded-sm hover:bg-slate-50 cursor-pointer">
              <Switch
                checked={draft.email_report_weekly_enabled}
                onCheckedChange={(v) => setField("email_report_weekly_enabled", v)}
              />
              <div>
                <span className="font-semibold block text-[#0B1B26] text-xs">Reporte Semanal</span>
                <span className="text-[11px] text-[#888888]">Resumen de leads de los últimos 7 días</span>
              </div>
            </label>

            <label className="flex items-center gap-3 text-sm text-[#334155] border border-[#E9E6DC] p-3 rounded-sm hover:bg-slate-50 cursor-pointer">
              <Switch
                checked={draft.email_report_monthly_enabled}
                onCheckedChange={(v) => setField("email_report_monthly_enabled", v)}
              />
              <div>
                <span className="font-semibold block text-[#0B1B26] text-xs">Reporte Mensual</span>
                <span className="text-[11px] text-[#888888]">Resumen de leads de los últimos 30 días</span>
              </div>
            </label>
          </div>
        </div>

        <hr className="border-[#E9E6DC]" />

        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            className="rounded-sm"
            disabled={!dirty}
            onClick={() => setDraft({
              smtp_enabled: !!settingsQ.data?.smtp_enabled,
              smtp_host: settingsQ.data?.smtp_host || "",
              smtp_port: settingsQ.data?.smtp_port || 587,
              smtp_username: settingsQ.data?.smtp_username || "",
              smtp_password: "",
              smtp_from_email: settingsQ.data?.smtp_from_email || "",
              smtp_from_name: settingsQ.data?.smtp_from_name || "Latus CRM",
              smtp_use_tls: settingsQ.data?.smtp_use_tls !== false,
              smtp_use_ssl: !!settingsQ.data?.smtp_use_ssl,
              app_base_url: settingsQ.data?.app_base_url || "",
              email_notif_unattended_enabled: settingsQ.data?.email_notif_unattended_enabled !== false,
              email_report_daily_enabled: settingsQ.data?.email_report_daily_enabled !== false,
              email_report_weekly_enabled: settingsQ.data?.email_report_weekly_enabled !== false,
              email_report_monthly_enabled: settingsQ.data?.email_report_monthly_enabled !== false,
            })}
          >
            Descartar cambios
          </Button>
          <Button className="bg-[#0E8DDB] hover:bg-[#0a7ab8] rounded-sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
            Guardar cambios
          </Button>
        </div>
      </div>
    </div>
  );
}


// =============================================================================
// Page shell
// =============================================================================
export default function Configuracion() {
  const { user, loading } = useAuth();
  const [tab, setTab] = useState("users");

  if (loading) return null;
  if (!user) return <Navigate to="/" replace />;

  const perms = user.permissions || [];
  const hasPerm = (p) => perms.includes(p);
  const hasAnyAdmin = hasPerm("manage_users") || hasPerm("configure_whatsapp") || hasPerm("configure_ai") || hasPerm("manage_settings");

  if (!hasAnyAdmin) {
    return (
      <AppLayout title="Configuración">
        <div className="px-6 py-6 max-w-2xl">
          <div
            data-testid="config-no-access"
            className="bg-white border border-[#E9E6DC] rounded-sm p-8 text-center"
          >
            <div className="mx-auto h-12 w-12 rounded-sm bg-[#F4F2EC] border border-[#EFE3E1] flex items-center justify-center mb-4">
              <AlertTriangle className="h-6 w-6 text-[#0E8DDB]" />
            </div>
            <h2 className="text-xl font-bold tracking-tight text-[#0B1B26] mb-1">
              No tenés permisos para acceder a esta sección
            </h2>
            <p className="text-sm text-[#888888] mb-4">
              La sección <b>Configuración</b> requiere permisos administrativos.
              Tu cuenta actual tiene rol <RolePill role={user.role} />.
            </p>
            <p className="text-xs text-[#888888] mb-5">
              Si necesitás acceso, pedile a un administrador que actualice tus permisos
              desde <span className="font-mono">/configuracion</span> &gt; Roles y Accesos.
            </p>
            <a
              href="/dashboard"
              className="inline-flex items-center text-sm font-bold text-[#0E8DDB] hover:underline"
            >
              Volver al panel principal
            </a>
          </div>
        </div>
      </AppLayout>
    );
  }

  // Build visible tabs based on permissions
  const tabs = [];
  if (hasPerm("manage_users"))      tabs.push({ key: "users",    label: "Usuarios",            icon: UsersIcon,        testid: "tab-users" });
  if (hasPerm("configure_whatsapp")) tabs.push({ key: "whatsapp", label: "WhatsApp",            icon: MessageSquareText, testid: "tab-whatsapp" });
  if (hasPerm("configure_ai"))      tabs.push({ key: "bot",      label: "Bot IA",              icon: Bot,              testid: "tab-bot-ia" });
  if (hasPerm("configure_ai"))      tabs.push({ key: "ai",       label: "IA y automatización", icon: Sparkles,         testid: "tab-ai-auto" });
  if (hasPerm("manage_users"))      tabs.push({ key: "roles",    label: "Roles y Accesos",     icon: Shield,           testid: "tab-roles" });
  if (hasPerm("manage_users"))      tabs.push({ key: "work-areas", label: "Áreas de Trabajo",   icon: Building2,        testid: "tab-work-areas" });
  if (hasPerm("manage_settings"))   tabs.push({ key: "crm",      label: "Tareas y catálogo",   icon: Package,          testid: "tab-crm-config" });
  if (hasPerm("manage_settings"))   tabs.push({ key: "email",    label: "Email",               icon: MessageSquareText, testid: "tab-email-config" });

  // If current tab is not visible, switch to first available
  const activeTab = tabs.find((t) => t.key === tab) ? tab : (tabs[0]?.key || "users");

  return (
    <AppLayout title="Configuración">
      <div className="px-6 py-6 max-w-6xl">
        <div className="flex items-center gap-2 mb-6 border-b border-[#E9E6DC]">
          {tabs.map((t) => (
            <button
              key={t.key}
              data-testid={t.testid}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2.5 -mb-px text-sm font-bold border-b-2 transition-colors ${
                activeTab === t.key
                  ? "border-[#0E8DDB] text-[#0B1B26]"
                  : "border-transparent text-[#888888] hover:text-[#0B1B26]"
              }`}
            >
              <t.icon className="h-4 w-4 inline mr-1.5" /> {t.label}
            </button>
          ))}
        </div>
        {activeTab === "users" && <UsersTab me={user} />}
        {activeTab === "whatsapp" && <WhatsAppTab />}
        {activeTab === "bot" && <BotIATab setTab={setTab} />}
        {activeTab === "ai" && <AIAutoTab />}
        {activeTab === "roles" && <RolesTab />}
        {activeTab === "work-areas" && <WorkAreasTab />}
        {activeTab === "crm" && <CRMConfigTab />}
        {activeTab === "email" && <EmailSettingsTab />}
      </div>
    </AppLayout>
  );
}
