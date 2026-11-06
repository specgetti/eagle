// Typed client for the Eagle REST API. Types are written by hand from openapi.json.

export type Role = "admin" | "member" | "viewer";
export type AgentKind = "claude" | "codex" | "opencode";
export type ReviewStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type ReviewTrigger = "manual" | "rerun" | "poll";
export type CredentialKind =
  | "anthropic_api_key"
  | "anthropic_compatible"
  | "claude_subscription"
  | "vertex_ai"
  | "openai_api_key"
  | "chatgpt_subscription"
  | "openai_compatible";
export type ChannelKind = "slack" | "smtp";
export type ChannelEvent = "review.succeeded" | "review.failed";

export const AGENTS: AgentKind[] = ["claude", "codex", "opencode"];
export const REVIEW_STATUSES: ReviewStatus[] = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];
export const ROLES: Role[] = ["admin", "member", "viewer"];
export const CHANNEL_EVENTS: ChannelEvent[] = ["review.succeeded", "review.failed"];

export interface User {
  id: number;
  username: string;
  email: string | null;
  role: Role;
  is_active: boolean;
  /** "local" (password) or "oidc" (SSO). Optional for older backends; missing means local. */
  auth_source?: AuthSource;
  created_at: string;
}

export type AuthSource = "local" | "oidc";

export interface AuthConfig {
  local_login: boolean;
  oidc: { enabled: boolean; label: string };
}

export const OIDC_LOGIN_PATH = "/api/v1/auth/oidc/login";

export function isSsoUser(user: Pick<User, "auth_source">): boolean {
  return user.auth_source === "oidc";
}

export interface UserCreate {
  username: string;
  password: string;
  email?: string | null;
  role?: Role;
}

export interface UserUpdate {
  email?: string | null;
  role?: Role | null;
  is_active?: boolean | null;
  password?: string | null;
}

export interface AgentOption {
  agent: AgentKind;
  default_model: string | null;
  allowed_models: string[];
  configured: boolean;
}

export interface ModelTokens {
  model: string;
  responses?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
}

/** Which models answered a review, from the agent's output. */
export interface ModelReport {
  /** The model each API response said it came from (set by the endpoint). */
  served: ModelTokens[];
  /** The agent's own per-model totals, including background requests. */
  usage_by_model: ModelTokens[];
}

export interface Review {
  id: number;
  pr_url: string;
  git_host: string;
  repo: string;
  pr_number: number;
  head_sha: string | null;
  title: string | null;
  author: string | null;
  submitted_by_id: number | null;
  trigger: ReviewTrigger;
  previous_review_id: number | null;
  agent: AgentKind;
  model: string | null;
  /** "requested", "agent_default", or null when the agent CLI picks the model. */
  model_source: string | null;
  /** The model the agent said it used, when it says. */
  reported_model: string | null;
  model_report: ModelReport | null;
  /** A response came from a model other than the requested one. */
  model_mismatch: boolean;
  post_comment: boolean;
  status: ReviewStatus;
  summary: string | null;
  verdict: string | null;
  cost_usd: number | null;
  error: string | null;
  posted_comment_url: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ReviewDetail extends Review {
  custom_prompt: string | null;
  since_sha: string | null;
  base_ref: string | null;
  result_markdown: string | null;
  token_usage: Record<string, unknown> | null;
  delivery_errors: string[] | null;
  submitted_by?: string | null;
}

export interface ReviewPage {
  items: Review[];
  total: number;
}

export interface ReviewCreate {
  pr_url: string;
  agent?: AgentKind | null;
  model?: string | null;
  custom_prompt?: string | null;
  post_comment?: boolean | null;
}

export interface ReviewQuery {
  status?: ReviewStatus | null;
  repo?: string | null;
  pr_url?: string | null;
  mine?: boolean;
  limit?: number;
  offset?: number;
}

export interface PlatformSettings {
  max_concurrent_reviews: number;
  poller_enabled: boolean;
  poll_interval_minutes: number;
  post_comment_default: boolean;
  default_agent: AgentKind;
}

export interface AgentConfig {
  agent: AgentKind;
  enabled: boolean;
  default_model: string | null;
  allowed_models: string[];
  credential_id: number | null;
  instructions: string | null;
  timeout_seconds: number;
  compatible_credential_kinds: CredentialKind[];
}

export interface AgentConfigUpdate {
  enabled?: boolean | null;
  default_model?: string | null;
  allowed_models?: string[] | null;
  credential_id?: number | null;
  instructions?: string | null;
  timeout_seconds?: number | null;
}

export interface CredentialField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  /** When set, the value must be one of these (rendered as a dropdown). */
  choices?: string[] | null;
  help?: string | null;
}

export interface CredentialTestResult {
  /** null: cannot be checked from the server (not a failure). */
  ok: boolean | null;
  message: string;
  latency_ms?: number | null;
  models_found?: number | null;
}

export interface ModelList {
  models: string[];
  /** "provider": listed by the endpoint; "suggested": built-in common choices. */
  source: "provider" | "suggested";
  message?: string | null;
}

export interface CredentialType {
  kind: CredentialKind;
  fields: CredentialField[];
  agents: AgentKind[];
  subscription: boolean;
}

export interface Credential {
  id: number;
  name: string;
  kind: CredentialKind;
  public_config: Record<string, unknown>;
  secret_fields: Record<string, string>;
  used_by: AgentKind[];
  created_at: string;
  updated_at: string;
}

export interface CredentialCreate {
  name: string;
  kind: CredentialKind;
  values: Record<string, string>;
}

export interface CredentialUpdate {
  name?: string | null;
  values?: Record<string, string>;
}

export interface RepoSettingIn {
  pattern: string;
  instructions?: string | null;
  default_agent?: AgentKind | null;
  post_comment?: boolean | null;
}

export interface RepoSetting {
  id: number;
  pattern: string;
  instructions: string | null;
  default_agent: AgentKind | null;
  post_comment: boolean | null;
  created_at: string;
}

export interface GitHostIn {
  host: string;
  api_url?: string | null;
  token?: string | null;
}

export interface GitHost {
  id: number;
  host: string;
  api_url: string;
  has_token: boolean;
  created_at: string;
}

export interface Channel {
  id: number;
  name: string;
  kind: ChannelKind;
  enabled: boolean;
  events: string[];
  public_config: Record<string, unknown>;
  secret_fields: Record<string, string>;
  created_at: string;
}

export interface ChannelCreate {
  name: string;
  kind: ChannelKind;
  enabled?: boolean;
  events?: ChannelEvent[];
  config: Record<string, unknown>;
}

export interface ChannelUpdate {
  name?: string | null;
  enabled?: boolean | null;
  events?: ChannelEvent[] | null;
  config?: Record<string, unknown> | null;
}

export interface TrackedPR {
  id: number;
  pr_url: string;
  repo: string;
  pr_number: number;
  enabled: boolean;
  poll_interval_minutes: number | null;
  closed: boolean;
  last_reviewed_sha: string | null;
  latest_review_id: number | null;
  last_polled_at: string | null;
}

export interface TrackedPRUpdate {
  enabled?: boolean | null;
  poll_interval_minutes?: number | null;
}

export interface AuditEntry {
  id: number;
  actor_username: string;
  action: string;
  target: string | null;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface ApiToken {
  id: number;
  name: string;
  /** The first characters of the token, to recognise it. */
  prefix: string;
  username: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface ApiTokenCreated extends ApiToken {
  /** The token itself. Only returned once, when it is created. */
  token: string;
}

export interface ApiTokenCreate {
  name: string;
  expires_in_days: number | null;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
}

interface ValidationErrorItem {
  loc?: (string | number)[];
  msg?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Turn a FastAPI `detail` value into a readable message. Handles plain strings and
 * the 422 validation shape: a list of {loc, msg}. The leading "body"/"query"/"path"
 * segment of loc is dropped, so ["body", "password"] reads as "password".
 */
export function formatErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item: ValidationErrorItem | string) => {
        if (typeof item === "string") return item;
        const loc = (item.loc ?? []).filter(
          (part, i) => !(i === 0 && ["body", "query", "path"].includes(String(part))),
        );
        const where = loc.join(".");
        const msg = item.msg ?? "Invalid value";
        return where ? `${where}: ${msg}` : msg;
      })
      .join("; ");
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) return JSON.stringify(detail);
  return fallback;
}

export function errorMessage(err: unknown, fallback = "Something went wrong"): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

/** Register a callback fired on any 401 (except a failed login attempt). */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

type Query = Record<string, string | number | boolean | null | undefined>;

function buildQuery(query?: Query): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  query?: Query,
): Promise<T> {
  const resp = await fetch(`/api/v1${path}${buildQuery(query)}`, {
    method,
    credentials: "same-origin",
    headers:
      body === undefined
        ? { Accept: "application/json" }
        : { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) {
    let message = resp.statusText || `Request failed (${resp.status})`;
    try {
      const data = await resp.json();
      message = formatErrorDetail(data?.detail, message);
    } catch {
      // Not JSON, keep the status text.
    }
    if (resp.status === 401 && path !== "/auth/login" && onUnauthorized) {
      onUnauthorized();
    }
    throw new ApiError(resp.status, message);
  }
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  // Auth
  authConfig: () => request<AuthConfig>("GET", "/auth/config"),
  me: () => request<User>("GET", "/auth/me"),
  login: (username: string, password: string) =>
    request<User>("POST", "/auth/login", { username, password }),
  logout: () => request<void>("POST", "/auth/logout"),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("POST", "/auth/password", { current_password, new_password }),

  // Users
  listUsers: () => request<User[]>("GET", "/users"),
  createUser: (body: UserCreate) => request<User>("POST", "/users", body),
  updateUser: (id: number, body: UserUpdate) => request<User>("PATCH", `/users/${id}`, body),
  deleteUser: (id: number) => request<void>("DELETE", `/users/${id}`),

  // API tokens (need a browser session)
  listTokens: (all = false) => request<ApiToken[]>("GET", "/tokens", undefined, { all: all || null }),
  createToken: (body: ApiTokenCreate) => request<ApiTokenCreated>("POST", "/tokens", body),
  revokeToken: (id: number) => request<void>("DELETE", `/tokens/${id}`),

  // Reviews
  reviewOptions: () => request<AgentOption[]>("GET", "/reviews/options"),
  listReviews: (q: ReviewQuery = {}) => request<ReviewPage>("GET", "/reviews", undefined, { ...q }),
  submitReview: (body: ReviewCreate) => request<ReviewDetail>("POST", "/reviews", body),
  getReview: (id: number) => request<ReviewDetail>("GET", `/reviews/${id}`),
  cancelReview: (id: number) => request<ReviewDetail>("POST", `/reviews/${id}/cancel`),
  rerunReview: (id: number) => request<ReviewDetail>("POST", `/reviews/${id}/rerun`),

  // Settings and agents
  getSettings: () => request<PlatformSettings>("GET", "/settings"),
  updateSettings: (body: PlatformSettings) => request<PlatformSettings>("PUT", "/settings", body),
  listAgents: () => request<AgentConfig[]>("GET", "/agents"),
  updateAgent: (agent: AgentKind, body: AgentConfigUpdate) =>
    request<AgentConfig>("PUT", `/agents/${agent}`, body),

  // Credentials
  credentialTypes: () => request<CredentialType[]>("GET", "/credentials/types"),
  listCredentials: () => request<Credential[]>("GET", "/credentials"),
  createCredential: (body: CredentialCreate) => request<Credential>("POST", "/credentials", body),
  updateCredential: (id: number, body: CredentialUpdate) =>
    request<Credential>("PATCH", `/credentials/${id}`, body),
  deleteCredential: (id: number) => request<void>("DELETE", `/credentials/${id}`),
  testCredential: (id: number, model?: string | null) =>
    request<CredentialTestResult>("POST", `/credentials/${id}/test`, { model: model || null }),
  credentialModels: (id: number) => request<ModelList>("GET", `/credentials/${id}/models`),

  // Repo settings
  listRepoSettings: () => request<RepoSetting[]>("GET", "/repo-settings"),
  createRepoSetting: (body: RepoSettingIn) => request<RepoSetting>("POST", "/repo-settings", body),
  updateRepoSetting: (id: number, body: RepoSettingIn) =>
    request<RepoSetting>("PUT", `/repo-settings/${id}`, body),
  deleteRepoSetting: (id: number) => request<void>("DELETE", `/repo-settings/${id}`),

  // Git hosts
  listGitHosts: () => request<GitHost[]>("GET", "/git-hosts"),
  createGitHost: (body: GitHostIn) => request<GitHost>("POST", "/git-hosts", body),
  updateGitHost: (id: number, body: GitHostIn) => request<GitHost>("PUT", `/git-hosts/${id}`, body),
  deleteGitHost: (id: number) => request<void>("DELETE", `/git-hosts/${id}`),

  // Notifications
  listChannels: () => request<Channel[]>("GET", "/notifications"),
  createChannel: (body: ChannelCreate) => request<Channel>("POST", "/notifications", body),
  updateChannel: (id: number, body: ChannelUpdate) =>
    request<Channel>("PATCH", `/notifications/${id}`, body),
  deleteChannel: (id: number) => request<void>("DELETE", `/notifications/${id}`),
  testChannel: (id: number) => request<void>("POST", `/notifications/${id}/test`),

  // Tracked PRs and audit
  listTracked: (include_closed = false) =>
    request<TrackedPR[]>("GET", "/tracked-prs", undefined, { include_closed }),
  updateTracked: (id: number, body: TrackedPRUpdate) =>
    request<TrackedPR>("PATCH", `/tracked-prs/${id}`, body),
  listAudit: (q: { action?: string | null; limit?: number; offset?: number } = {}) =>
    request<AuditPage>("GET", "/audit", undefined, { ...q }),
};
