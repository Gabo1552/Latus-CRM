import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Users as UsersIcon, MessageSquareText, Plus, MoreHorizontal, Search,
  Copy, RefreshCw, CheckCircle2, AlertTriangle, KeyRound, Trash2, Eye, EyeOff,
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
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-[#52525B]" />
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
        <label className="flex items-center gap-2 text-sm text-[#52525B] select-none">
          <Switch
            data-testid="include-inactive"
            checked={includeInactive}
            onCheckedChange={setIncludeInactive}
            className="data-[state=checked]:bg-[#FF4500]"
          />
          Mostrar inactivos
        </label>
        <div className="flex-1" />
        <Button
          data-testid="new-user-button"
          onClick={() => setModal({ mode: "create" })}
          className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm font-semibold"
        >
          <Plus className="h-4 w-4 mr-1" /> Nuevo usuario
        </Button>
      </div>

      <div className="bg-white border border-zinc-200 rounded-sm overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-50 text-[11px] uppercase tracking-wide text-[#52525B]">
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
              <tr><td colSpan={7} className="px-4 py-6 text-center text-[#52525B]">Cargando…</td></tr>
            ) : users.length === 0 ? (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-[#52525B]">Sin usuarios</td></tr>
            ) : users.map((u) => {
              const isSelf = u.user_id === me?.user_id;
              const ap = u.auth_provider;
              const apLabel = ap === "local" ? "Email y contraseña"
                : ap === "both" ? "Ambos" : "Google";
              return (
                <tr key={u.user_id} data-testid={`user-row-${u.user_id}`}>
                  <td className="px-4 py-3 font-medium text-[#0A0A0A]">
                    {u.name}
                    {isSelf && <span className="ml-2 text-[10px] text-[#52525B] uppercase tracking-wide">Tú</span>}
                  </td>
                  <td className="px-4 py-3 text-[#52525B]">{u.email}</td>
                  <td className="px-4 py-3"><RolePill role={u.role} /></td>
                  <td className="px-4 py-3 text-[#52525B]">{apLabel}</td>
                  <td className="px-4 py-3">
                    {u.is_active ? (
                      <span className="inline-flex items-center gap-1 text-xs font-bold text-[#15803D]">
                        <span className="h-1.5 w-1.5 rounded-full bg-[#15803D]" /> Activo
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs font-bold text-[#52525B]">
                        <span className="h-1.5 w-1.5 rounded-full bg-[#52525B]" /> Inactivo
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-[#52525B] text-xs">
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
            <p className="text-[#52525B] mb-2">
              Compartila con <b>{resetDialog?.user?.name}</b> ({resetDialog?.user?.email})
              por un canal seguro. Al iniciar sesión, recomendales cambiarla.
            </p>
            <div className="flex items-center gap-2 bg-white border border-zinc-200 rounded-sm px-3 py-2">
              <code data-testid="temp-password" className="flex-1 font-mono text-[#0A0A0A] text-sm">{resetDialog?.tempPwd}</code>
              <CopyButton value={resetDialog?.tempPwd || ""} />
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => setResetDialog(null)} className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm">
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
                      ? "bg-[#FF4500] text-white border-[#FF4500]"
                      : "bg-white text-[#52525B] border-zinc-300 hover:border-[#FF4500]"
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
            className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm"
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

  const rotate = useMutation({
    mutationFn: () => api.post("/admin/whatsapp/rotate-verify-token").then((r) => r.data),
    onSuccess: (data) => {
      setRotated(data.verify_token);
      qc.invalidateQueries({ queryKey: ["admin-wa-config"] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || "No se pudo rotar"),
  });

  if (!cfg) {
    return <div className="text-[#52525B]">Cargando…</div>;
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
      <div className="bg-white border border-zinc-200 rounded-sm p-5">
        <h3 className="text-base font-bold tracking-tight text-[#0A0A0A] mb-3">Estado</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <div>
            {cfg.configured ? (
              <span data-testid="wa-cfg-connected" className="inline-flex items-center gap-2 text-sm font-bold text-[#16A34A] bg-[#F0FDF4] border border-[#BBF7D0] rounded-sm px-2.5 py-1">
                <span className="h-2 w-2 rounded-full bg-[#16A34A]" /> Conectado
              </span>
            ) : (
              <span data-testid="wa-cfg-not-configured" className="inline-flex items-center gap-2 text-sm font-bold text-[#FF4500] bg-[#FFF7ED] border border-[#FED7AA] rounded-sm px-2.5 py-1">
                <span className="h-2 w-2 rounded-full bg-[#FF4500]" /> No configurado
              </span>
            )}
            <p className="text-xs text-[#52525B] mt-2">Última actividad</p>
            <p className="text-sm font-mono text-[#0A0A0A]">
              {cfg.last_webhook_at ? new Date(cfg.last_webhook_at).toLocaleString("es-AR") : "Nunca"}
            </p>
            {cfg.last_error && (
              <p className="text-xs text-[#DC2626] mt-1">
                Último error: <span className="font-mono font-bold">#{cfg.last_error.code ?? "—"}</span> {cfg.last_error.message}
              </p>
            )}
          </div>
          <div>
            <p className="text-xs text-[#52525B] mb-1">Webhook URL (configurar en Meta)</p>
            <div className="flex items-center gap-2 bg-zinc-50 border border-zinc-200 rounded-sm px-3 py-2">
              <code data-testid="wa-webhook-url" className="flex-1 font-mono text-xs break-all">{cfg.webhook_url}</code>
              <CopyButton value={cfg.webhook_url} />
            </div>
            <p className="text-xs text-[#52525B] mt-2">Versión API</p>
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
      <div className="bg-white border border-zinc-200 rounded-sm p-5">
        <h3 className="text-base font-bold tracking-tight text-[#0A0A0A] mb-1">Credenciales</h3>
        <p className="text-xs text-[#52525B] mb-4">
          Los valores se guardan cifrados en base de datos y sobreescriben los de las variables de entorno.
          Para limpiar un campo y volver al valor de <code>.env</code>, usá <b>Limpiar</b>.
        </p>
        {!cfg.encryption_available && (
          <div data-testid="wa-no-encryption" className="bg-[#FEF9C3] border-l-4 border-[#EAB308] p-3 text-sm mb-4">
            <p className="font-bold flex items-center gap-2"><AlertTriangle className="h-4 w-4" /> Cifrado no disponible</p>
            <p className="text-[#52525B]">
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
              : { label: "Sin configurar", c: "#52525B", bg: "#F4F4F5" };
            return (
              <div key={f.key} className="border-t border-zinc-100 pt-4 first:border-t-0 first:pt-0">
                <div className="mb-1.5">
                  <Label className="text-sm font-bold text-[#0A0A0A]">{f.label}</Label>
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
                    <span className="font-mono text-xs text-[#52525B]">{meta.masked || "—"}</span>
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
            className="bg-[#FF4500] hover:bg-[#E63E00] rounded-sm font-semibold"
          >
            Guardar cambios
          </Button>
        </div>
      </div>

      {/* Acciones */}
      <div className="bg-white border border-zinc-200 rounded-sm p-5">
        <h3 className="text-base font-bold tracking-tight text-[#0A0A0A] mb-3">Acciones</h3>
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
            className="rounded-sm font-semibold bg-[#0A0A0A] hover:bg-[#27272A] text-white"
          >
            <CheckCircle2 className="h-4 w-4 mr-1" /> Probar conexión
          </Button>
        </div>
        {rotated && (
          <div data-testid="wa-rotated-banner" className="bg-[#FEF9C3] border-l-4 border-[#EAB308] p-3 text-sm mt-4">
            <p className="font-bold flex items-center gap-2 mb-1"><AlertTriangle className="h-4 w-4" /> Nuevo Verify Token generado</p>
            <p className="text-[#52525B] mb-2">
              Copialo y pegalo en Meta Business. <b>Este valor no se vuelve a mostrar.</b>
            </p>
            <div className="flex items-center gap-2 bg-white border border-zinc-200 rounded-sm px-3 py-2">
              <code className="flex-1 font-mono text-xs break-all">{rotated}</code>
              <CopyButton value={rotated} />
            </div>
          </div>
        )}
      </div>

      {/* Instrucciones */}
      <details className="bg-white border border-zinc-200 rounded-sm p-5 group">
        <summary className="font-bold tracking-tight text-[#0A0A0A] cursor-pointer list-none flex items-center justify-between">
          ¿Dónde encuentro estos valores en Meta?
          <span className="text-xs text-[#52525B] group-open:hidden">Mostrar</span>
          <span className="text-xs text-[#52525B] hidden group-open:inline">Ocultar</span>
        </summary>

        {/* Equivalencias de nombres Meta ↔ Latus CRM */}
        <div className="mt-4">
          <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#52525B] mb-2">
            Equivalencias de nombres · Meta ↔ Latus CRM
          </p>
          <div className="border border-zinc-200 rounded-sm overflow-hidden">
            <table data-testid="wa-meta-mapping" className="w-full text-sm">
              <thead className="bg-zinc-50 text-[11px] uppercase tracking-wide text-[#52525B]">
                <tr>
                  <th className="text-left px-3 py-2 font-bold w-1/4">Latus CRM</th>
                  <th className="text-left px-3 py-2 font-bold w-1/4">Nombre en Meta</th>
                  <th className="text-left px-3 py-2 font-bold">Dónde encontrarlo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200">
                {WA_FIELDS.map((f) => (
                  <tr key={f.key}>
                    <td className="px-3 py-2 font-semibold text-[#0A0A0A] align-top">{f.label}</td>
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

        <p className="text-xs uppercase tracking-[0.1em] font-bold text-[#52525B] mt-5 mb-2">
          Pasos de configuración
        </p>
        <ol className="list-decimal pl-5 space-y-2 text-sm text-[#0A0A0A]">
          <li>Iniciá sesión en <a href="https://business.facebook.com" className="text-[#1D4ED8] underline" target="_blank" rel="noreferrer">Meta Business</a> y abrí la app de WhatsApp Business.</li>
          <li>En la sección <b>Configuración &gt; Webhooks</b>, agregá la URL <code className="font-mono bg-zinc-100 px-1">{cfg.webhook_url}</code> y el <b>Verify Token</b> guardado acá.</li>
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
            className="bg-white border border-zinc-200 rounded-sm p-8 text-center"
          >
            <div className="mx-auto h-12 w-12 rounded-sm bg-[#FFF7ED] border border-[#FED7AA] flex items-center justify-center mb-4">
              <AlertTriangle className="h-6 w-6 text-[#FF4500]" />
            </div>
            <h2 className="text-xl font-bold tracking-tight text-[#0A0A0A] mb-1">
              No tenés permisos para acceder a esta sección
            </h2>
            <p className="text-sm text-[#52525B] mb-4">
              La sección <b>Configuración</b> es exclusiva para usuarios con rol{" "}
              <RolePill role="admin" />. Tu cuenta actual tiene rol{" "}
              <RolePill role={user.role} />.
            </p>
            <p className="text-xs text-[#52525B] mb-5">
              Si necesitás acceso, pedile a un administrador que actualice tu rol
              desde <span className="font-mono">/configuracion</span> &gt; Usuarios.
            </p>
            <a
              href="/dashboard"
              className="inline-flex items-center text-sm font-bold text-[#FF4500] hover:underline"
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
        <div className="flex items-center gap-2 mb-6 border-b border-zinc-200">
          <button
            data-testid="tab-users"
            onClick={() => setTab("users")}
            className={`px-4 py-2.5 -mb-px text-sm font-bold border-b-2 transition-colors ${
              tab === "users"
                ? "border-[#FF4500] text-[#0A0A0A]"
                : "border-transparent text-[#52525B] hover:text-[#0A0A0A]"
            }`}
          >
            <UsersIcon className="h-4 w-4 inline mr-1.5" /> Usuarios
          </button>
          <button
            data-testid="tab-whatsapp"
            onClick={() => setTab("whatsapp")}
            className={`px-4 py-2.5 -mb-px text-sm font-bold border-b-2 transition-colors ${
              tab === "whatsapp"
                ? "border-[#FF4500] text-[#0A0A0A]"
                : "border-transparent text-[#52525B] hover:text-[#0A0A0A]"
            }`}
          >
            <MessageSquareText className="h-4 w-4 inline mr-1.5" /> WhatsApp
          </button>
        </div>
        {tab === "users" && <UsersTab me={user} />}
        {tab === "whatsapp" && <WhatsAppTab />}
      </div>
    </AppLayout>
  );
}
