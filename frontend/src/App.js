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

function AppRouter() {
  const location = useLocation();
  const { user, loading } = useAuth();
  if (location.hash?.includes("session_id=")) {
    return <AuthCallback />;
  }

  return (
    <Routes>
      <Route
        path="/"
        element={
          loading ? null : user ? <Navigate to="/dashboard" replace /> : <Login />
        }
      />
      <Route path="/dashboard" element={<Protected><Dashboard /></Protected>} />
      <Route path="/inbox" element={<Protected><Inbox /></Protected>} />
      <Route path="/leads" element={<Protected><Leads /></Protected>} />
      <Route path="/pipeline" element={<Protected><Pipeline /></Protected>} />
      <Route path="/calendario" element={<Protected><Calendario /></Protected>} />
      <Route path="/contacts" element={<Protected><Contacts /></Protected>} />
      <Route path="/tasks" element={<Protected><Tasks /></Protected>} />
      <Route path="/admin" element={<Protected><Admin /></Protected>} />
      <Route path="/configuracion" element={<Protected><Configuracion /></Protected>} />
      <Route path="/consumo-ia" element={<Protected><ConsumoIA /></Protected>} />
      <Route path="/catalogo" element={<Protected><Catalogo /></Protected>} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
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
