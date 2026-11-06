import { AgentKind, CredentialKind, ModelTokens, Review, ReviewStatus } from "./api";

export const AGENT_LABELS: Record<AgentKind, string> = {
  claude: "Claude Code",
  codex: "Codex",
  opencode: "OpenCode",
};

export const CREDENTIAL_KIND_LABELS: Record<CredentialKind, string> = {
  anthropic_api_key: "Anthropic API key",
  anthropic_compatible: "Anthropic-compatible (MaaS / gateway)",
  claude_subscription: "Claude subscription (OAuth token)",
  vertex_ai: "Google Vertex AI",
  openai_api_key: "OpenAI API key",
  chatgpt_subscription: "ChatGPT subscription (auth.json)",
  openai_compatible: "OpenAI-compatible (MaaS / gateway)",
};

export const ACTIVE_STATUSES: ReviewStatus[] = ["queued", "running"];

export function isActive(status: ReviewStatus): boolean {
  return ACTIVE_STATUSES.includes(status);
}

/**
 * The model a review used and where it came from. Prefers the model the agent
 * reported over the one Eagle asked for, and notes when they differ.
 */
export function describeReviewModel(
  r: Pick<Review, "model" | "model_source" | "reported_model" | "status">,
): { name: string; note: string | null } {
  const source =
    r.model_source === "agent_default" ? "agent default" : r.model ? null : "CLI default";
  if (r.reported_model) {
    if (r.model && r.model !== r.reported_model) {
      return {
        name: r.reported_model,
        note: `${source ?? "requested"}: ${r.model}`,
      };
    }
    return { name: r.reported_model, note: source };
  }
  if (r.model) return { name: r.model, note: source };
  const finished = r.status === "succeeded" || r.status === "failed";
  return { name: "CLI default", note: finished ? "model not reported" : null };
}

export function modelText(r: Parameters<typeof describeReviewModel>[0]): string {
  const { name, note } = describeReviewModel(r);
  return note ? `${name} (${note})` : name;
}

/** 41234 -> "41k", 1500000 -> "1.5M". */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

/** "glm-5 (12 responses, 41k in / 3k out)" for each model, joined. */
export function modelTokensText(entries: ModelTokens[]): string {
  return entries
    .map((e) => {
      const parts = [];
      if (e.responses) parts.push(`${e.responses} response${e.responses === 1 ? "" : "s"}`);
      if (e.input_tokens || e.output_tokens) {
        const tokensIn = formatTokens(e.input_tokens ?? 0);
        parts.push(`${tokensIn} in / ${formatTokens(e.output_tokens ?? 0)} out`);
      }
      return parts.length ? `${e.model} (${parts.join(", ")})` : e.model;
    })
    .join("; ");
}

/**
 * Parse an API timestamp. Some endpoints return naive ISO strings (no zone) for
 * values that are UTC, so a missing zone designator is treated as UTC.
 */
export function parseApiTime(value: string): Date {
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const isDateTime = /^\d{4}-\d{2}-\d{2}T/.test(value);
  return new Date(isDateTime && !hasZone ? `${value}Z` : value);
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "-";
  const d = parseApiTime(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 8) : "-";
}

export function formatCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined) return "-";
  return `$${cost.toFixed(cost < 1 ? 4 : 2)}`;
}

/** Split "a, b,,c" into ["a", "b", "c"]. */
export function splitList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Map a nullable boolean to the tri-state select value and back. */
export type TriState = "default" | "yes" | "no";

export function toTriState(value: boolean | null | undefined): TriState {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "default";
}

export function fromTriState(value: TriState): boolean | null {
  if (value === "yes") return true;
  if (value === "no") return false;
  return null;
}

export function stringValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}
