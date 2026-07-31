import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import AuthCallback from "@/pages/AuthCallback";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Inbox from "@/pages/Inbox";
import Leads from "@/pages/Leads";
import Pipeline from "@/pages/Pipeline";
import Contacts from "@/pages/Contacts";
import Tasks from "@/pages/Tasks";
import Admin from "@/pages/Admin";
import Configuracion from "@/pages/Configuracion";
import ConsumoIA from "@/pages/ConsumoIA";
import Catalogo from "@/pages/Catalogo";
import Calendario from "@/pages/Calendario";
import Suscripcion from "@/pages/Suscripcion";
import Plataforma from "@/pages/Plataforma";
import PublicWebChat from "@/pages/PublicWebChat";
import { firstAllowedPath, hasConfigurationAccess, hasPermission } from "@/lib/permissions";

function Protected({ children }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F9F9F7]">
        <div className="h-10 w-10 border-2 border-[#0E8DDB] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }
  if (!user) return <Navigate to="/" replace />;
  return children;
}

function Permitted({ permission, permissions = [], configuration = false, children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/" replace />;
  const allowed = configuration
    ? hasConfigurationAccess(user)
    : (permission ? hasPermission(user, permission) : permissions.some((item) => hasPermission(user, item)));
  return allowed ? children : <Navigate to={firstAllowedPath(user)} replace />;
}

function NoAccess() {
  const { user } = useAuth();
  return (
    <div className="min-h-screen bg-[#F9F9F7] flex items-center justify-center px-6">
      <div className="max-w-md rounded-xl border border-[#E9E6DC] bg-white p-8 text-center shadow-sm">
        <h1 className="text-xl font-bold text-[#0B1B26]">Tu cuenta todavía no tiene módulos habilitados</h1>
        <p className="mt-2 text-sm text-[#777]">Pedile a un administrador que asigne al menos acceso de visualización a tu rol.</p>
        <p className="mt-5 text-xs font-semibold uppercase tracking-wider text-[#0E8DDB]">{user?.name}</p>
      </div>
    </div>
  );
}

function PlatformAdminOnly({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/" replace />;
  return user.is_platform_admin ? children : <Navigate to={firstAllowedPath(user)} replace />;
}

function AppRouter() {
  const location = useLocation();
  const { user, loading } = useAuth();
  if (location.hash?.includes("session_id=")) {
    return <AuthCallback />;
  }
  if (
    !loading && user && user.subscription_access === false
    && !user.is_platform_admin && location.pathname !== "/suscripcion"
  ) {
    return <Navigate to="/suscripcion" replace />;
  }

  return (
    <Routes>
      <Route
        path="/"
        element={
          loading ? null : user ? <Navigate to={firstAllowedPath(user)} replace /> : <Login />
        }
      />
      <Route path="/dashboard" element={<Permitted permission="crm_view"><Dashboard /></Permitted>} />
      <Route path="/inbox" element={<Permitted permission="inbox_view"><Inbox /></Permitted>} />
      <Route path="/leads" element={<Permitted permission="crm_view"><Leads /></Permitted>} />
      <Route path="/pipeline" element={<Permitted permission="crm_view"><Pipeline /></Permitted>} />
      <Route path="/calendario" element={<Permitted permission="calendar_view"><Calendario /></Permitted>} />
      <Route path="/contacts" element={<Permitted permission="crm_view"><Contacts /></Permitted>} />
      <Route path="/tasks" element={<Permitted permission="crm_view"><Tasks /></Permitted>} />
      <Route path="/admin" element={<Permitted permissions={["users_view", "settings_view", "whatsapp_view"]}><Admin /></Permitted>} />
      <Route path="/configuracion" element={<Permitted configuration><Configuracion /></Permitted>} />
      <Route path="/consumo-ia" element={<Permitted permission="ai_view"><ConsumoIA /></Permitted>} />
      <Route path="/catalogo" element={<Permitted permission="catalog_view"><Catalogo /></Permitted>} />
      <Route path="/suscripcion" element={<Protected><Suscripcion /></Protected>} />
      <Route path="/plataforma" element={<PlatformAdminOnly><Plataforma /></PlatformAdminOnly>} />
      <Route path="/c/:token" element={<PublicWebChat />} />
      <Route path="/chat-web/:token" element={<PublicWebChat />} />
      <Route path="/sin-acceso" element={<Protected><NoAccess /></Protected>} />
      <Route path="*" element={<Navigate to={user ? firstAllowedPath(user) : "/"} replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRouter />
        <Toaster position="top-right" />
      </BrowserRouter>
    </AuthProvider>
  );
}
