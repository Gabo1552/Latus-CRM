// Shared CRM constants & visual metadata for the Latus editorial palette.

export const LEAD_STATUSES = [
  { key: "new", label: "Nuevo", color: "#13283A", bg: "#E8EDF0" },
  { key: "contacted", label: "Contactado", color: "#0D607A", bg: "#E2F0F3" },
  { key: "qualified", label: "Calificado", color: "#5C9FB8", bg: "#E8F3F6" },
  { key: "proposal", label: "Propuesta", color: "#5EA9CE", bg: "#E7F3F8" },
  { key: "won", label: "Ganado", color: "#1997D7", bg: "#E5F3FA" },
  { key: "lost", label: "Perdido", color: "#F44D2F", bg: "#FDEAE5" },
];

export const CONV_STATUSES = [
  { key: "open", label: "Abierta", color: "#0D607A", bg: "#E2F0F3" },
  { key: "pending", label: "Pendiente", color: "#C56A37", bg: "#F8EADF" },
  { key: "resolved", label: "Resuelta", color: "#52616B", bg: "#E8EDF0" },
];

export const PRIORITIES = [
  { key: "low", label: "Baja", color: "#52525B", bg: "#F4F4F5" },
  { key: "medium", label: "Media", color: "#1D4ED8", bg: "#EFF6FF" },
  { key: "high", label: "Alta", color: "#DC2626", bg: "#FEF2F2" },
];

export const ROLES = [
  { key: "admin", label: "Administrador", color: "#FF4500", bg: "#FFF7ED" },
  { key: "supervisor", label: "Supervisor", color: "#1D4ED8", bg: "#EFF6FF" },
  { key: "agent", label: "Agente", color: "#52525B", bg: "#F4F4F5" },
  { key: "viewer", label: "Consulta", color: "#15803D", bg: "#F0FDF4" },
  // legacy
  { key: "sales_agent", label: "Agente", color: "#52525B", bg: "#F4F4F5" },
];

export const AUTH_PROVIDERS = [
  { key: "google", label: "Google" },
  { key: "local", label: "Email y contraseña" },
  { key: "both", label: "Ambos" },
];

export const roleLabel = (key) => (ROLES.find((r) => r.key === key) || { label: key }).label;
export const roleMeta = (key) => ROLES.find((r) => r.key === key) || { label: key, color: "#52525B", bg: "#F4F4F5" };

export const statusMeta = (list, key) =>
  list.find((s) => s.key === key) || { label: key, color: "#52525B", bg: "#F4F4F5" };

export const money = (n) =>
  new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 0 }).format(n || 0);

export const initials = (name = "?") =>
  name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
