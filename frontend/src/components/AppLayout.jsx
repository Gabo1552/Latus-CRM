import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Users, Target, MessageSquare, KanbanSquare,
  CheckSquare, Shield, Settings, LogOut, MessageSquareText, DollarSign, Package,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { initials, roleLabel } from "@/lib/constants";
import NotificationBell from "@/components/NotificationBell";

const NAV = [
  { to: "/dashboard", label: "Panel principal", icon: LayoutDashboard, testid: "nav-dashboard" },
  { to: "/inbox", label: "Bandeja", icon: MessageSquare, testid: "nav-inbox" },
  { to: "/leads", label: "Leads", icon: Target, testid: "nav-leads" },
  { to: "/pipeline", label: "Pipeline", icon: KanbanSquare, testid: "nav-pipeline" },
  { to: "/contacts", label: "Contactos", icon: Users, testid: "nav-contacts" },
  { to: "/tasks", label: "Tareas", icon: CheckSquare, testid: "nav-tasks" },
];

export default function AppLayout({ children, title, actions }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const nav = [...NAV];
  if (user?.role !== "viewer") {
    nav.push({ to: "/catalogo", label: "Catálogo", icon: Package, testid: "nav-catalogo" });
  }
  if (user?.role === "admin") {
    nav.push({ to: "/admin", label: "Administración", icon: Shield, testid: "nav-admin" });
    nav.push({ to: "/consumo-ia", label: "Consumo de IA", icon: DollarSign, testid: "nav-consumo-ia" });
    nav.push({ to: "/configuracion", label: "Configuración", icon: Settings, testid: "nav-configuracion" });
  }

  return (
    <div className="flex h-screen bg-latus-cream overflow-hidden">
      {/* Sidebar */}
      <aside className="w-60 bg-latus-ink flex flex-col shrink-0">
        <div className="h-16 flex items-center gap-2.5 px-5 border-b border-white/10">
          <div className="h-8 w-8 bg-latus-blue flex items-center justify-center rounded-md">
            <MessageSquareText className="h-4.5 w-4.5 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-white font-bold text-lg" style={{letterSpacing: "-0.02em"}}>Latus CRM</span>
        </div>

        <nav className="flex-1 px-3 py-5 space-y-0.5">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              data-testid={item.testid}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-md text-sm font-medium transition-colors relative ${
                  isActive
                    ? "bg-latus-ink-soft text-white"
                    : "text-white/70 hover:text-white hover:bg-white/5"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] bg-latus-blue rounded-r-sm" />
                  )}
                  <item.icon className="h-[18px] w-[18px]" />
                  {item.label}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="p-3 border-t border-white/10">
          <button
            onClick={() => navigate("/profile")}
            className="w-full flex items-center gap-3 px-2 py-2 rounded-md hover:bg-white/5 transition-colors text-left"
            data-testid="sidebar-profile"
          >
            {user?.picture ? (
              <img src={user.picture} alt="" className="h-8 w-8 rounded-md object-cover" />
            ) : (
              <div className="h-8 w-8 rounded-md bg-latus-blue flex items-center justify-center text-white text-xs font-bold">
                {initials(user?.name)}
              </div>
            )}
            <div className="flex-1 min-w-0">
              <p className="text-white text-sm font-semibold truncate">{user?.name}</p>
              <p className="text-white/50 text-xs">{roleLabel(user?.role)}</p>
            </div>
          </button>
          <button
            onClick={logout}
            data-testid="logout-button"
            className="mt-1 w-full flex items-center gap-3 px-2 py-2 rounded-md text-white/70 hover:text-white hover:bg-white/5 text-sm font-medium transition-colors"
          >
            <LogOut className="h-[18px] w-[18px]" />
            Cerrar sesión
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-16 bg-white border-b border-[#E9E6DC] flex items-center justify-between px-6 shrink-0">
          <h1 className="text-xl font-bold tracking-tight text-[#0B1B26]" data-testid="page-title">{title}</h1>
          <div className="flex items-center gap-3">
            {actions}
            <NotificationBell />
          </div>
        </header>
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  );
}
