import { FormEvent, useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  ActionGroup,
  Alert,
  Bullseye,
  Button,
  Card,
  CardBody,
  CardTitle,
  Divider,
  Form,
  FormGroup,
  TextInput,
  Title,
} from "@patternfly/react-core";
import { api, AuthConfig, errorMessage, OIDC_LOGIN_PATH } from "../api";
import { FullPageSpinner, useAuth } from "../auth";

/** Used when /auth/config cannot be read: keep the local form so admins are never locked out. */
const FALLBACK_CONFIG: AuthConfig = { local_login: true, oidc: { enabled: false, label: "" } };

/** Full-page navigation to the SSO flow. Exported so tests can stub it. */
export const ssoRedirect = {
  go: (url: string) => {
    window.location.href = url;
  },
};

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const ssoError = searchParams.get("error");
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname || "/";
  const target = from === "/login" ? "/" : from;

  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .authConfig()
      .then((c) => {
        if (cancelled) return;
        setConfig({
          local_login: c?.local_login ?? true,
          oidc: { enabled: !!c?.oidc?.enabled, label: c?.oidc?.label || "Log in with SSO" },
        });
      })
      .catch(() => !cancelled && setConfig(FALLBACK_CONFIG));
    return () => {
      cancelled = true;
    };
  }, []);

  if (user === undefined) return <FullPageSpinner />;
  if (user) return <Navigate to={target} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      navigate(target, { replace: true });
    } catch (err) {
      setError(errorMessage(err, "Login failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Bullseye
      style={{ minHeight: "100vh", background: "var(--pf-v5-global--BackgroundColor--dark-100)" }}
    >
      <Card style={{ width: 400, maxWidth: "92vw" }}>
        <CardTitle>
          <Title headingLevel="h1" size="2xl">
            Eagle
          </Title>
          <div style={{ fontWeight: "normal", fontSize: "var(--pf-v5-global--FontSize--sm)" }}>
            Team pull request reviews by AI agents
          </div>
        </CardTitle>
        <CardBody>
          {ssoError && (
            <Alert
              variant="danger"
              isInline
              title="Sign-in failed"
              style={{ marginBottom: 16 }}
            >
              {ssoError}
            </Alert>
          )}
          {config === null ? (
            <FullPageSpinner />
          ) : (
            <>
              {config.oidc.enabled && (
                <Button
                  variant="primary"
                  isBlock
                  onClick={() => ssoRedirect.go(OIDC_LOGIN_PATH)}
                  style={{ marginBottom: config.local_login ? 16 : 0 }}
                >
                  {config.oidc.label}
                </Button>
              )}
              {config.oidc.enabled && config.local_login && (
                <Divider style={{ marginBottom: 16 }} />
              )}
              {config.local_login && (
                <Form onSubmit={submit} aria-label="Local login">
                  {error && <Alert variant="danger" isInline title={error} />}
                  <FormGroup label="Username" isRequired fieldId="login-username">
                    <TextInput
                      id="login-username"
                      isRequired
                      autoComplete="username"
                      value={username}
                      onChange={(_e, v) => setUsername(v)}
                    />
                  </FormGroup>
                  <FormGroup label="Password" isRequired fieldId="login-password">
                    <TextInput
                      id="login-password"
                      type="password"
                      isRequired
                      autoComplete="current-password"
                      value={password}
                      onChange={(_e, v) => setPassword(v)}
                    />
                  </FormGroup>
                  <ActionGroup>
                    <Button
                      type="submit"
                      variant={config.oidc.enabled ? "secondary" : "primary"}
                      isBlock
                      isLoading={busy}
                      isDisabled={busy || !username || !password}
                    >
                      Log in
                    </Button>
                  </ActionGroup>
                </Form>
              )}
              {!config.local_login && !config.oidc.enabled && (
                <Alert variant="warning" isInline title="No login method is enabled" />
              )}
            </>
          )}
        </CardBody>
      </Card>
    </Bullseye>
  );
}
