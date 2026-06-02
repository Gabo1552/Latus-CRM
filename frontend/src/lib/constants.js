// Shared CRM constants & style helpers (Swiss high-contrast + electric orange)

export const LEAD_STATUSES = [
  { key: "new", label: "Nuevo", color: "#52525B", bg: "#F4F4F5" },
  { key: "contacted", label: "Contactado", color: "#1D4ED8", bg: "#EFF6FF" },
  { key: "qualified", label: "Calificado", color: "#7C3AED", bg: "#F5F3FF" },
  { key: "proposal", label: "Propuesta", color: "#FF4500", bg: "#FFF7ED" },
  { key: "won", label: "Ganado", color: "#064E3B", bg: "#ECFDF5" },
  { key: "lost", label: "Perdido", color: "#DC2626", bg: "#FEF2F2" },
];

export const CONV_STATUSES = [
  { key: "open", label: "Abierta", color: "#064E3B", bg: "#ECFDF5" },
  { key: "pending", label: "Pendiente", color: "#EAB308", bg: "#FEFCE8" },
  { key: "resolved", label: "Resuelta", color: "#52525B", bg: "#F4F4F5" },
];

export const PRIORITIES = [
  { key: "low", label: "Baja", color: "#52525B", bg: "#F4F4F5" },
  { key: "medium", label: "Media", color: "#1D4ED8", bg: "#EFF6FF" },
  { key: "high", label: "Alta", color: "#DC2626", bg: "#FEF2F2" },
];

export const ROLES = [
  { key: "admin", label: "Administrador" },
  { key: "supervisor", label: "Supervisor" },
  { key: "sales_agent", label: "Agente de ventas" },
];

export const roleLabel = (key) => (ROLES.find((r) => r.key === key) || { label: key }).label;

export const statusMeta = (list, key) =>
  list.find((s) => s.key === key) || { label: key, color: "#52525B", bg: "#F4F4F5" };

export const money = (n) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(n || 0);

export const initials = (name = "?") =>
  name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
