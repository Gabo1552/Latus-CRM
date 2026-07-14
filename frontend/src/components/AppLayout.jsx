import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Users, Target, MessageSquare, KanbanSquare,
  CheckSquare, Shield, Settings, LogOut, DollarSign, Package, Calendar,
  Menu, X
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { initials, roleLabel } from "@/lib/constants";
import NotificationBell from "@/components/NotificationBell";

const NAV = [
  { to: "/dashboard", label: "Panel principal", icon: LayoutDashboard, testid: "nav-dashboard" },
  { to: "/inbox", label: "Bandeja", icon: MessageSquare, testid: "nav-inbox" },
  { to: "/calendario", label: "Calendario", icon: Calendar, testid: "nav-calendario" },
  { to: "/leads", label: "Leads", icon: Target, testid: "nav-leads" },
  { to: "/pipeline", label: "Pipeline", icon: KanbanSquare, testid: "nav-pipeline" },
  { to: "/contacts", label: "Clientes", icon: Users, testid: "nav-contacts" },
  { to: "/tasks", label: "Tareas", icon: CheckSquare, testid: "nav-tasks" },
];

export default function AppLayout({ children, title, actions }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  const perms = user?.permissions || [];
  const hasPerm = (permission) => perms.includes(permission);
  const hasAnyAdmin = hasPerm("manage_users") || hasPerm("configure_whatsapp") || hasPerm("configure_ai") || hasPerm("manage_settings");

  const nav = [...NAV];
  if (hasPerm("write_catalog")) {
    nav.push({ to: "/catalogo", label: "Catálogo", icon: Package, testid: "nav-catalogo" });
  }
  if (hasAnyAdmin) {
    nav.push({ to: "/admin", label: "Administración", icon: Shield, testid: "nav-admin" });
    nav.push({ to: "/consumo-ia", label: "Consumo de IA", icon: DollarSign, testid: "nav-consumo-ia" });
    nav.push({ to: "/configuracion", label: "Configuración", icon: Settings, testid: "nav-configuracion" });
  }

  const profile = user?.picture ? (
    <img src={user.picture} alt="" className="h-9 w-9 rounded-full object-cover ring-1 ring-latus-warm-border" />
  ) : (
    <div className="h-9 w-9 rounded-full bg-latus-ink flex items-center justify-center text-white text-xs font-bold">
      {initials(user?.name)}
    </div>
  );

  return (
    <div className="latus-app-shell flex h-screen overflow-hidden bg-latus-cream text-latus-ink">
      {menuOpen && (
        <button
          type="button"
          aria-label="Cerrar navegación"
          data-testid="mobile-nav-backdrop"
          onClick={() => setMenuOpen(false)}
          className="fixed inset-0 z-40 bg-latus-ink/45 lg:hidden"
        />
      )}

      <aside className={`fixed inset-y-0 left-0 z-50 flex w-[17rem] shrink-0 flex-col bg-latus-ink text-white transition-transform duration-300 lg:static lg:translate-x-0 ${menuOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="px-6 pb-5 pt-6">
          <div className="flex min-h-14 items-center justify-between rounded-[14px] border border-dashed border-white/65 px-4">
            <div className="flex items-baseline gap-1.5">
              <span className="latus-wordmark text-[1.65rem] leading-none text-latus-ice">Latus</span>
              <span className="text-lg font-light tracking-tight text-latus-ice">CRM</span>
            </div>
            <button type="button" onClick={() => setMenuOpen(false)} className="p-1 text-white/70 lg:hidden" aria-label="Cerrar menú">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-4 pb-5" aria-label="Navegación principal">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              data-testid={item.testid}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                `group relative flex items-center gap-3 rounded-md px-4 py-3 text-[15px] font-medium transition-colors ${
                  isActive
                    ? "bg-latus-ink-soft text-white"
                    : "text-latus-ice/85 hover:bg-white/[0.06] hover:text-white"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={`h-[19px] w-[19px] ${isActive ? "text-white" : "text-latus-ice/90 group-hover:text-white"}`} strokeWidth={2} />
                  <span>{item.label}</span>
                  {isActive && <span className="absolute inset-y-2 left-0 w-[3px] rounded-r bg-latus-blue" />}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="mx-5 border-t border-white/10 py-5">
          <button
            type="button"
            onClick={logout}
            data-testid="logout-button"
            className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium text-latus-ice/70 transition-colors hover:bg-white/[0.06] hover:text-white"
          >
            <LogOut className="h-[18px] w-[18px]" />
            Cerrar sesión
          </button>
          <div className="mt-4 flex items-baseline justify-center gap-1.5 text-latus-ice">
            <span className="latus-wordmark text-2xl">Latus</span>
            <span className="text-xl font-light">CRM</span>
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1 flex flex-col">
        <header className="latus-grain flex min-h-[72px] shrink-0 items-center justify-between border-b border-latus-warm-border px-4 md:px-7">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              data-testid="mobile-nav-toggle"
              onClick={() => setMenuOpen(true)}
              className="rounded-md border border-latus-warm-border bg-latus-surface p-2 text-latus-ink lg:hidden"
              aria-label="Abrir navegación"
            >
              <Menu className="h-5 w-5" />
            </button>
            <h1 className="truncate text-2xl font-bold tracking-tight text-latus-ink md:text-[1.75rem]" data-testid="page-title">{title}</h1>
          </div>
          <div className="flex items-center gap-2.5 md:gap-4">
            <div className="hidden items-center gap-3 sm:flex">{actions}</div>
            <NotificationBell />
            <button
              type="button"
              onClick={() => navigate("/configuracion")}
              className="flex items-center gap-2 rounded-full"
              data-testid="sidebar-profile"
              title={`${user?.name || "Usuario"} · ${roleLabel(user?.role)}`}
            >
              {profile}
              <span className="sr-only">Abrir perfil</span>
            </button>
          </div>
        </header>
        {actions && <div className="latus-grain border-b border-latus-warm-border px-4 py-2 sm:hidden">{actions}</div>}
        <main className="latus-main-surface flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  );
}
