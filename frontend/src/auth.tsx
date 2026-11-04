import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Bullseye, Spinner } from "@patternfly/react-core";
import { Navigate, useLocation } from "react-router-dom";
import { api, ApiError, Role, setUnauthorizedHandler, User } from "./api";

export interface AuthState {
  /** undefined while the session is being checked, null when logged out. */
  user: User | null | undefined;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  setUser: (user: User | null) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    let cancelled = false;
    api
      .me()
      .then((u) => !cancelled && setUser(u))
      .catch((err) => {
        if (cancelled) return;
        // Any failure to read the session means we treat the user as logged out.
        if (!(err instanceof ApiError) || err.status !== 401) console.warn(err);
        setUser(null);
      });
    return () => {
      cancelled = true;
      setUnauthorizedHandler(null);
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const u = await api.login(username, password);
    setUser(u);
    return u;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo(() => ({ user, login, logout, setUser }), [user, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

/** The logged-in user. Only use below RequireAuth. */
export function useUser(): User {
  const { user } = useAuth();
  if (!user) throw new Error("useUser called without a logged-in user");
  return user;
}

export const can = {
  submit: (role: Role) => role === "admin" || role === "member",
  admin: (role: Role) => role === "admin",
};

export function FullPageSpinner() {
  return (
    <Bullseye style={{ minHeight: "60vh" }}>
      <Spinner aria-label="Loading" />
    </Bullseye>
  );
}

export function RequireAuth({ children, role }: { children: ReactNode; role?: Role }) {
  const { user } = useAuth();
  const location = useLocation();
  if (user === undefined) return <FullPageSpinner />;
  if (user === null) return <Navigate to="/login" replace state={{ from: location }} />;
  if (role === "admin" && user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
