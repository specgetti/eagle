import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { PageSection } from "@patternfly/react-core";
import { AuthProvider, RequireAuth } from "./auth";
import { ToastProvider } from "./toast";
import { AppLayout } from "./components/AppLayout";
import { Empty } from "./components/States";
import { LoginPage } from "./pages/LoginPage";
import { ReviewsPage } from "./pages/ReviewsPage";
import { ReviewDetailPage } from "./pages/ReviewDetailPage";
import { TrackedPage } from "./pages/TrackedPage";
import { AccountPage } from "./pages/AccountPage";
import { UsersPage } from "./pages/admin/UsersPage";
import { AgentsPage } from "./pages/admin/AgentsPage";
import { CredentialsPage } from "./pages/admin/CredentialsPage";
import { RepositoriesPage } from "./pages/admin/RepositoriesPage";
import { GitHostsPage } from "./pages/admin/GitHostsPage";
import { NotificationsPage } from "./pages/admin/NotificationsPage";
import { SettingsPage } from "./pages/admin/SettingsPage";
import { AuditPage } from "./pages/admin/AuditPage";
import { ApiTokensPage } from "./pages/admin/ApiTokensPage";

function NotFound() {
  return (
    <PageSection>
      <Empty title="Page not found" />
    </PageSection>
  );
}

/** Route table. Separate from App so tests can mount it inside a MemoryRouter. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<ReviewsPage />} />
        <Route path="reviews" element={<Navigate to="/" replace />} />
        <Route path="reviews/:id" element={<ReviewDetailPage />} />
        <Route path="tracked" element={<TrackedPage />} />
        <Route path="account" element={<AccountPage />} />
        <Route
          path="admin"
          element={
            <RequireAuth role="admin">
              <Navigate to="/admin/users" replace />
            </RequireAuth>
          }
        />
        {(
          [
            ["users", <UsersPage />],
            ["agents", <AgentsPage />],
            ["credentials", <CredentialsPage />],
            ["repositories", <RepositoriesPage />],
            ["git-hosts", <GitHostsPage />],
            ["notifications", <NotificationsPage />],
            ["settings", <SettingsPage />],
            ["api-tokens", <ApiTokensPage />],
            ["audit", <AuditPage />],
          ] as const
        ).map(([path, element]) => (
          <Route
            key={path}
            path={`admin/${path}`}
            element={<RequireAuth role="admin">{element}</RequireAuth>}
          />
        ))}
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ToastProvider>
      <AuthProvider>{children}</AuthProvider>
    </ToastProvider>
  );
}

/** Opt in to React Router v7 behaviour now (also silences the v6 warnings). */
export const ROUTER_FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true } as const;

export default function App() {
  return (
    <BrowserRouter future={ROUTER_FUTURE}>
      <Providers>
        <AppRoutes />
      </Providers>
    </BrowserRouter>
  );
}
