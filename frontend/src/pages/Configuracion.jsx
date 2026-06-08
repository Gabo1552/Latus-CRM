import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Users as UsersIcon, MessageSquareText, Plus, MoreHorizontal, Search,
  Copy, RefreshCw, CheckCircle2, AlertTriangle, KeyRound, Trash2, Eye, EyeOff,
  Bot, Sparkles, Lightbulb,
} from "lucide-react";
import AppLayout from "@/components/AppLayout";
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

const ROLE_OPTIONS = [
  { key: "admin", label: "Administrador" },
  { key: "supervisor", label: "Supervisor" },
  { key: "agent", label: "Agente" },
  { key: "viewer", label: "Consulta" },
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
      setResetDialog({ user, tempPwd: data.temporary_password });
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
            {ROLE_OPTIONS.map((r) => (
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
            <DialogTitle>Contraseña temporal generada</DialogTitle>
          </DialogHeader>
          <div className="bg-[#FEF9C3] border-l-4 border-[#EAB308] p-3 text-sm">
            <p className="font-bold flex items-center gap-2 mb-2"><AlertTriangle className="h-4 w-4" /> Copiala ahora, no se vuelve a mostrar.</p>
            <p className="text-[#888888] mb-2">
              Compartila con <b>{resetDialog?.user?.name}</b> ({resetDialog?.user?.email})
              por un canal seguro. Al iniciar sesión, recomendales cambiarla.
            </p>
            <div className="flex items-center gap-2 bg-white border border-[#E9E6DC] rounded-sm px-3 py-2">
              <code data-testid="temp-password" className="flex-1 font-mono text-[#0B1B26] text-sm">{resetDialog?.tempPwd}</code>
              <CopyButton value={resetDialog?.tempPwd || ""} />
            </div>
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

  useEffect(() => {
    if (open) {
      setName(initialUser?.name || "");
      setEmail(initialUser?.email || "");
      setRole(initialUser?.role || "agent");
      setAuthProvider(initialUser?.auth_provider || "google");
      setPassword("");
    }
  }, [open, initialUser]);

  const save = useMutation({
    mutationFn: async () => {
      if (isEdit) {
        return api.patch(`/admin/users/${initialUser.user_id}`, {
          name, role, auth_provider: authProvider,
        });
      }
      return api.post("/admin/users", {
        name, email, role, auth_provider: authProvider,
        password: ["local", "both"].includes(authProvider) ? password : undefined,
      });
    },
    onSuccess: () => {
      toast.success(isEdit ? "Usuario actualizado" : "Usuario creado");
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
                {ROLE_OPTIONS.map((r) => (
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

  const [draft, setDraft] = useState(null);
  useEffect(() => { if (q.data && draft === null) setDraft({ ...q.data }); }, [q.data, draft]);

  const save = useMutation({
    mutationFn: (payload) => api.patch("/admin/bot-settings", payload),
    onSuccess: (r) => {
      setDraft({ ...r.data });
      qc.invalidateQueries({ queryKey: ["admin-bot-settings"] });
      toast.success("Cambios guardados");
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudieron guardar los cambios"),
  });

  if (q.isPending || aiProviderQ.isPending || !draft) return <div className="text-[#888888]">Cargando…</div>;

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));
  const faqs = Array.isArray(draft.faqs) ? draft.faqs : [];
  const thresh = Number(draft.confidence_threshold ?? 0.7);
  const ctxMax = Number(draft.recent_messages_context_max ?? 12);
  const threshInvalid = !(thresh >= 0 && thresh <= 1);
  const ctxInvalid = !(ctxMax >= 3 && ctxMax <= 50);
  const dirty = JSON.stringify(draft) !== JSON.stringify(q.data);
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
      business_instructions: draft.business_instructions || "",
      handoff_rules: draft.handoff_rules || "",
      tone: draft.tone || "",
      provider: draft.provider || "built_in",
      model: draft.model || "gpt-4o-mini",
      bot_name: (draft.bot_name || "").trim(),
      include_client_info: !!draft.include_client_info,
      faqs: faqs
        .map((f) => ({ q: (f.q || "").trim(), a: (f.a || "").trim() }))
        .filter((f) => f.q && f.a),
    };
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

          {/* Business instructions */}
          <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
            <Label className="text-sm font-bold text-[#0B1B26]">Instrucciones del negocio</Label>
            <p className="text-xs text-[#888888] mt-0.5 mb-2">
              Información que el bot puede usar: productos, precios, condiciones, políticas, horarios.
              Esto se inyecta como sistema en cada llamada al modelo.
            </p>
            <Textarea
              data-testid="bot-setting-instructions"
              value={draft.business_instructions || ""}
              onChange={(e) => set({ business_instructions: e.target.value })}
              className="rounded-sm min-h-[140px] font-mono text-xs"
              placeholder="Ej.: Vendemos planes mensuales desde $X. Envío gratis a CABA y GBA. Horario de atención humana: lun a vie 9–18hs..."
            />
          </div>

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
            onClick={() => setDraft({ ...q.data })}
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

function AIAutoTab() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin-ai-provider"],
    queryFn: () => api.get("/admin/ai-provider").then((r) => r.data),
  });
  const [draft, setDraft] = useState(null);
  const [pendingKey, setPendingKey] = useState(""); // staged new key, sent on save
  const [keyAction, setKeyAction] = useState("keep"); // 'keep' | 'replace' | 'clear'
  useEffect(() => {
    if (q.data && draft === null) {
      setDraft({ ...q.data });
      setPendingKey("");
      setKeyAction("keep");
    }
  }, [q.data, draft]);

  const save = useMutation({
    mutationFn: (payload) => api.put("/admin/ai-provider", payload),
    onSuccess: (r) => {
      setDraft({ ...r.data });
      setPendingKey("");
      setKeyAction("keep");
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
  const keyMissing = needsKey && keyAction !== "replace" && !draft.api_key_configured;

  const onSave = () => {
    if (tempBad)   { toast.error("La temperatura debe estar entre 0 y 2"); return; }
    if (maxTokBad) { toast.error("El máximo de tokens debe estar entre 100 y 4096"); return; }
    if (minConfBad){ toast.error("El umbral de confianza debe estar entre 0 y 1"); return; }
    if (needsKey && keyAction === "clear") {
      toast.error("Para este proveedor necesitás una API Key"); return;
    }
    if (needsKey && !draft.api_key_configured && keyAction !== "replace") {
      toast.error("Ingresá la API Key del proveedor"); return;
    }
    if (needsBaseUrl && !(draft.base_url || "").trim()) {
      toast.error("Para 'Otro (compatible OpenAI)' la URL base es obligatoria"); return;
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
    if (keyAction === "replace") payload.api_key = pendingKey;
    if (keyAction === "clear")   payload.api_key = null;
    save.mutate(payload);
  };

  const dirty = JSON.stringify(draft) !== JSON.stringify(q.data) || keyAction !== "keep";

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

          {/* API key */}
          {needsKey && (
            <div className="p-3 border border-[#E9E6DC] rounded-sm md:col-span-2">
              <Label className="text-sm font-bold text-[#0B1B26]">API Key</Label>
              <p className="text-xs text-[#888888] mt-0.5 mb-2">
                Se guarda cifrada y nunca se muestra en claro. Estado actual:{" "}
                {draft.api_key_configured
                  ? <span className="font-mono text-[#16A34A]">{draft.api_key_masked || "configurada"}</span>
                  : <span className="font-mono text-[#0E8DDB]">no configurada</span>}
              </p>
              <div className="flex gap-2">
                <Input
                  data-testid="ai-setting-apikey"
                  type="password"
                  placeholder={draft.api_key_configured ? "Dejar igual o reemplazar…" : "Pegá la API key del proveedor"}
                  value={pendingKey}
                  onChange={(e) => {
                    setPendingKey(e.target.value);
                    setKeyAction(e.target.value ? "replace" : "keep");
                  }}
                  className="rounded-sm h-9 flex-1 font-mono"
                />
                {draft.api_key_configured && (
                  <Button
                    data-testid="ai-setting-apikey-clear"
                    variant="outline"
                    onClick={() => { setPendingKey(""); setKeyAction("clear"); }}
                    className="rounded-sm h-9"
                  >
                    <Trash2 className="h-3.5 w-3.5 mr-1" /> Limpiar
                  </Button>
                )}
              </div>
              {keyMissing && (
                <p className="text-[11px] text-[#DC2626] mt-1">
                  Este proveedor necesita una API Key.
                </p>
              )}
              {keyAction === "clear" && (
                <p className="text-[11px] text-[#0E8DDB] mt-1">
                  Al guardar se borrará la API Key actual.
                </p>
              )}
            </div>
          )}

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
            disabled={test.isPending || !draft.ai_enabled || keyMissing}
            className="rounded-sm"
          >
            {test.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : <CheckCircle2 className="h-3.5 w-3.5 mr-1" />}
            Probar IA
          </Button>
          <div className="flex items-center gap-2">
            <Button
              data-testid="ai-settings-reset"
              variant="outline"
              onClick={() => { setDraft({ ...q.data }); setPendingKey(""); setKeyAction("keep"); }}
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
// Page shell
// =============================================================================
export default function Configuracion() {
  const { user, loading } = useAuth();
  const [tab, setTab] = useState("users");

  if (loading) return null;
  if (!user) return <Navigate to="/" replace />;
  if (user.role !== "admin") {
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
              La sección <b>Configuración</b> es exclusiva para usuarios con rol{" "}
              <RolePill role="admin" />. Tu cuenta actual tiene rol{" "}
              <RolePill role={user.role} />.
            </p>
            <p className="text-xs text-[#888888] mb-5">
              Si necesitás acceso, pedile a un administrador que actualice tu rol
              desde <span className="font-mono">/configuracion</span> &gt; Usuarios.
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

  return (
    <AppLayout title="Configuración">
      <div className="px-6 py-6 max-w-6xl">
        <div className="flex items-center gap-2 mb-6 border-b border-[#E9E6DC]">
          <button
            data-testid="tab-users"
            onClick={() => setTab("users")}
            className={`px-4 py-2.5 -mb-px text-sm font-bold border-b-2 transition-colors ${
              tab === "users"
                ? "border-[#0E8DDB] text-[#0B1B26]"
                : "border-transparent text-[#888888] hover:text-[#0B1B26]"
            }`}
          >
            <UsersIcon className="h-4 w-4 inline mr-1.5" /> Usuarios
          </button>
          <button
            data-testid="tab-whatsapp"
            onClick={() => setTab("whatsapp")}
            className={`px-4 py-2.5 -mb-px text-sm font-bold border-b-2 transition-colors ${
              tab === "whatsapp"
                ? "border-[#0E8DDB] text-[#0B1B26]"
                : "border-transparent text-[#888888] hover:text-[#0B1B26]"
            }`}
          >
            <MessageSquareText className="h-4 w-4 inline mr-1.5" /> WhatsApp
          </button>
          <button
            data-testid="tab-bot-ia"
            onClick={() => setTab("bot")}
            className={`px-4 py-2.5 -mb-px text-sm font-bold border-b-2 transition-colors ${
              tab === "bot"
                ? "border-[#0E8DDB] text-[#0B1B26]"
                : "border-transparent text-[#888888] hover:text-[#0B1B26]"
            }`}
          >
            <Bot className="h-4 w-4 inline mr-1.5" /> Bot IA
          </button>
          <button
            data-testid="tab-ai-auto"
            onClick={() => setTab("ai")}
            className={`px-4 py-2.5 -mb-px text-sm font-bold border-b-2 transition-colors ${
              tab === "ai"
                ? "border-[#0E8DDB] text-[#0B1B26]"
                : "border-transparent text-[#888888] hover:text-[#0B1B26]"
            }`}
          >
            <Sparkles className="h-4 w-4 inline mr-1.5" /> IA y automatización
          </button>
        </div>
        {tab === "users" && <UsersTab me={user} />}
        {tab === "whatsapp" && <WhatsAppTab />}
        {tab === "bot" && <BotIATab setTab={setTab} />}
        {tab === "ai" && <AIAutoTab />}
      </div>
    </AppLayout>
  );
}
