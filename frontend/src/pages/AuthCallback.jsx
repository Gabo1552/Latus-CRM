import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function AuthCallback() {
  const navigate = useNavigate();
  const { setUser } = useAuth();
  const hasProcessed = useRef(false);

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const hash = window.location.hash;
    const sessionId = new URLSearchParams(hash.replace("#", "")).get("session_id");

    const run = async () => {
      try {
        const res = await api.post("/auth/session", { session_id: sessionId });
        setUser(res.data);
        window.history.replaceState(null, "", "/dashboard");
        navigate("/dashboard", { replace: true, state: { user: res.data } });
      } catch {
        navigate("/", { replace: true });
      }
    };
    run();
  }, [navigate, setUser]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#F9F9F7]">
      <div className="flex flex-col items-center gap-4" data-testid="auth-callback-loading">
        <div className="h-10 w-10 border-2 border-[#0E8DDB] border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-[#888888] font-medium">Iniciando sesión…</p>
      </div>
    </div>
  );
}
