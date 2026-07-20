import { createContext, useContext, useEffect, useState, useCallback } from "react";
import api from "@/lib/api";

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [organizations, setOrganizations] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadOrganizations = useCallback(async () => {
    try {
      const res = await api.get("/organizations");
      setOrganizations(Array.isArray(res.data) ? res.data : []);
      return res.data;
    } catch {
      setOrganizations([]);
      return [];
    }
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      const res = await api.get("/auth/me");
      setUser(res.data);
      await loadOrganizations();
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, [loadOrganizations]);

  useEffect(() => {
    // CRITICAL: If returning from OAuth callback, skip the /me check.
    // AuthCallback will exchange the session_id and establish the session first.
    if (window.location.hash?.includes("session_id=")) {
      setLoading(false);
      return;
    }
    checkAuth();
  }, [checkAuth]);

  const login = async (email, password) => {
    const res = await api.post("/auth/login", { email, password });
    setUser(res.data);
    await loadOrganizations();
    return res.data;
  };

  const switchOrganization = async (organizationId) => {
    const res = await api.post(`/organizations/${organizationId}/switch`);
    setUser(res.data);
    await loadOrganizations();
    return res.data;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      // ignore
    }
    setUser(null);
    setOrganizations([]);
    window.location.href = "/";
  };

  return (
    <AuthContext.Provider value={{
      user, setUser, organizations, loading, checkAuth, loadOrganizations,
      switchOrganization, login, logout,
    }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
