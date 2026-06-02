// Shared CRM constants & style helpers (Swiss high-contrast + electric orange)

export const LEAD_STATUSES = [
  { key: "new", label: "New", color: "#52525B", bg: "#F4F4F5" },
  { key: "contacted", label: "Contacted", color: "#1D4ED8", bg: "#EFF6FF" },
  { key: "qualified", label: "Qualified", color: "#7C3AED", bg: "#F5F3FF" },
  { key: "proposal", label: "Proposal", color: "#FF4500", bg: "#FFF7ED" },
  { key: "won", label: "Won", color: "#064E3B", bg: "#ECFDF5" },
  { key: "lost", label: "Lost", color: "#DC2626", bg: "#FEF2F2" },
];

export const CONV_STATUSES = [
  { key: "open", label: "Open", color: "#064E3B", bg: "#ECFDF5" },
  { key: "pending", label: "Pending", color: "#EAB308", bg: "#FEFCE8" },
  { key: "resolved", label: "Resolved", color: "#52525B", bg: "#F4F4F5" },
];

export const PRIORITIES = [
  { key: "low", label: "Low", color: "#52525B", bg: "#F4F4F5" },
  { key: "medium", label: "Medium", color: "#1D4ED8", bg: "#EFF6FF" },
  { key: "high", label: "High", color: "#DC2626", bg: "#FEF2F2" },
];

export const ROLES = [
  { key: "admin", label: "Admin" },
  { key: "supervisor", label: "Supervisor" },
  { key: "sales_agent", label: "Sales Agent" },
];

export const statusMeta = (list, key) =>
  list.find((s) => s.key === key) || { label: key, color: "#52525B", bg: "#F4F4F5" };

export const money = (n) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(n || 0);

export const initials = (name = "?") =>
  name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
