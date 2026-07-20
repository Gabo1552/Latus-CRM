import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Users, Target, MessageSquare, KanbanSquare,
  CheckSquare, Shield, Settings, LogOut, DollarSign, Package, Calendar,
  Menu, X, PanelLeftClose, PanelLeftOpen, Sparkles
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { initials, roleLabel } from "@/lib/constants";
import { firstAllowedPath, hasConfigurationAccess, hasPermission } from "@/lib/permissions";
import NotificationBell from "@/components/NotificationBell";

const NAV = [
  { to: "/dashboard", label: "Panel principal", icon: LayoutDashboard, testid: "nav-dashboard", permission: "crm_view" },
  { to: "/inbox", label: "Bandeja", icon: MessageSquare, testid: "nav-inbox", permission: "inbox_view" },
  { to: "/calendario", label: "Calendario", icon: Calendar, testid: "nav-calendario", permission: "calendar_view" },
  { to: "/leads", label: "Leads", icon: Target, testid: "nav-leads", permission: "crm_view" },
  { to: "/pipeline", label: "Pipeline", icon: KanbanSquare, testid: "nav-pipeline", permission: "crm_view" },
  { to: "/contacts", label: "Clientes", icon: Users, testid: "nav-contacts", permission: "crm_view" },
  { to: "/tasks", label: "Tareas", icon: CheckSquare, testid: "nav-tasks", permission: "crm_view" },
];

export default function AppLayout({ children, title, actions }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const [navCollapsed, setNavCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem("crm-nav-collapsed") === "true";
    } catch {
      return false;
    }
  });

  const toggleDesktopNav = () => {
    setNavCollapsed((collapsed) => {
      const next = !collapsed;
      try {
        window.localStorage.setItem("crm-nav-collapsed", String(next));
      } catch {
        // La navegación sigue funcionando aunque el navegador bloquee el almacenamiento.
      }
      return next;
    });
  };

  const nav = NAV.filter((item) => hasPermission(user, item.permission));
  if (hasPermission(user, "catalog_view")) {
    nav.push({ to: "/catalogo", label: "Catálogo", icon: Package, testid: "nav-catalogo" });
  }
  if (["users_view", "settings_view", "whatsapp_view"].some((permission) => hasPermission(user, permission))) {
    nav.push({ to: "/admin", label: "Administración", icon: Shield, testid: "nav-admin" });
  }
  if (hasPermission(user, "ai_view")) {
    nav.push({ to: "/consumo-ia", label: "Consumo de IA", icon: DollarSign, testid: "nav-consumo-ia" });
  }
  if (hasConfigurationAccess(user)) {
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

      <aside className={`fixed inset-y-0 left-0 z-50 flex w-[17rem] shrink-0 flex-col bg-latus-ink text-white transition-[width,transform] duration-300 ease-out lg:relative lg:inset-auto lg:translate-x-0 ${navCollapsed ? "lg:w-[5.5rem]" : "lg:w-[17rem]"} ${menuOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <button
          type="button"
          data-testid="desktop-nav-toggle"
          onClick={toggleDesktopNav}
          aria-label={navCollapsed ? "Expandir navegación" : "Contraer navegación"}
          aria-pressed={navCollapsed}
          title={navCollapsed ? "Expandir navegación" : "Contraer navegación"}
          className="absolute -right-3 top-8 z-[60] hidden h-7 w-7 items-center justify-center rounded-full border border-latus-warm-border bg-latus-surface text-latus-ink shadow-[0_5px_16px_rgba(13,31,42,0.22)] transition hover:scale-105 hover:border-latus-blue hover:text-latus-blue lg:flex"
        >
          {navCollapsed ? <PanelLeftOpen className="h-3.5 w-3.5" /> : <PanelLeftClose className="h-3.5 w-3.5" />}
        </button>

        <div className={`px-5 pb-5 pt-5 transition-all duration-300 ${navCollapsed ? "lg:px-3" : "lg:px-5"}`}>
          <div className={`flex min-h-[68px] items-center justify-between rounded-2xl border border-white/10 bg-white/[0.045] px-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.07)] ${navCollapsed ? "lg:justify-center lg:px-2" : ""}`}>
            <div className={`flex min-w-0 items-center gap-3 ${navCollapsed ? "lg:justify-center" : ""}`}>
              <div className="relative flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-[14px] bg-gradient-to-br from-latus-blue via-[#24B7C7] to-latus-coral text-white shadow-[0_9px_24px_rgba(25,151,215,0.28)]">
                <span className="absolute -right-3 -top-3 h-7 w-7 rounded-full bg-white/25 blur-sm" />
                <Sparkles className="relative h-5 w-5" strokeWidth={2.2} />
              </div>
              <div className={`min-w-0 ${navCollapsed ? "lg:hidden" : ""}`}>
                <div className="whitespace-nowrap text-[1.45rem] font-extrabold leading-none tracking-[-0.04em] text-white">
                  <span>Tu</span>{" "}<span className="font-light text-latus-ice">logo</span>
                </div>
                <p className="mt-1.5 whitespace-nowrap text-[8px] font-bold uppercase tracking-[0.24em] text-latus-ice/55">Tu marca, tu espacio</p>
              </div>
            </div>
            <button type="button" onClick={() => setMenuOpen(false)} className="p-1 text-white/70 lg:hidden" aria-label="Cerrar menú">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        <nav className={`flex-1 space-y-1 overflow-y-auto px-4 pb-5 transition-all duration-300 ${navCollapsed ? "lg:px-3" : "lg:px-4"}`} aria-label="Navegación principal">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              data-testid={item.testid}
              onClick={() => setMenuOpen(false)}
              title={navCollapsed ? item.label : undefined}
              aria-label={navCollapsed ? item.label : undefined}
              className={({ isActive }) =>
                `group relative flex items-center gap-3 rounded-lg px-4 py-3 text-[15px] font-medium transition-all ${navCollapsed ? "lg:justify-center lg:gap-0 lg:px-0" : ""} ${
                  isActive
                    ? "bg-latus-ink-soft text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]"
                    : "text-latus-ice/85 hover:bg-white/[0.06] hover:text-white"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={`h-[19px] w-[19px] shrink-0 ${isActive ? "text-white" : "text-latus-ice/90 group-hover:text-white"}`} strokeWidth={2} />
                  <span className={`whitespace-nowrap ${navCollapsed ? "lg:hidden" : ""}`}>{item.label}</span>
                  {isActive && <span className="absolute inset-y-2 left-0 w-[3px] rounded-r bg-latus-blue" />}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className={`mx-5 border-t border-white/10 py-5 transition-all duration-300 ${navCollapsed ? "lg:mx-3" : "lg:mx-5"}`}>
          <button
            type="button"
            onClick={logout}
            data-testid="logout-button"
            title={navCollapsed ? "Cerrar sesión" : undefined}
            aria-label={navCollapsed ? "Cerrar sesión" : undefined}
            className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-latus-ice/70 transition-colors hover:bg-white/[0.06] hover:text-white ${navCollapsed ? "lg:justify-center lg:gap-0 lg:px-0" : ""}`}
          >
            <LogOut className="h-[18px] w-[18px] shrink-0" />
            <span className={`whitespace-nowrap ${navCollapsed ? "lg:hidden" : ""}`}>Cerrar sesión</span>
          </button>
          <div className={`mt-4 flex items-baseline justify-center gap-1 text-latus-ice ${navCollapsed ? "lg:hidden" : ""}`}>
            <span className="text-lg font-extrabold tracking-[-0.04em]">Latus</span>
            <span className="text-lg font-light">CRM</span>
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
              onClick={() => navigate(hasConfigurationAccess(user) ? "/configuracion" : firstAllowedPath(user))}
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
