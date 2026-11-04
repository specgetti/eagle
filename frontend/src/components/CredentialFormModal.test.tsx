import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Credential, CredentialType } from "../api";
import { mockFetch, renderWithProviders } from "../test/utils";
import { buildCredentialValues, CredentialFormModal, SUBSCRIPTION_WARNING } from "./CredentialFormModal";

const TYPES: CredentialType[] = [
  {
    kind: "vertex_ai",
    agents: ["claude", "opencode"],
    subscription: false,
    fields: [
      { name: "service_account_json", label: "Service account JSON", secret: true, required: true },
      { name: "project_id", label: "GCP project ID", secret: false, required: true },
      { name: "region", label: "Region", secret: false, required: true },
    ],
  },
  {
    kind: "claude_subscription",
    agents: ["claude"],
    subscription: true,
    fields: [{ name: "oauth_token", label: "OAuth token", secret: true, required: true }],
  },
];

const EXISTING: Credential = {
  id: 5,
  name: "vertex-prod",
  kind: "vertex_ai",
  public_config: { project_id: "proj-a", region: "us-east5" },
  secret_fields: { service_account_json: "****abcd" },
  used_by: ["claude"],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function auth401() {
  return { status: 401, body: { detail: "x" } };
}

describe("CredentialFormModal", () => {
  it("renders fields from the types response, JSON secrets as a textarea", async () => {
    mockFetch({ "GET /auth/me": auth401 });
    renderWithProviders(
      <CredentialFormModal isOpen types={TYPES} onClose={vi.fn()} onSaved={vi.fn()} />,
    );
    const json = await screen.findByLabelText(/Service account JSON/);
    expect(json.tagName).toBe("TEXTAREA");
    expect(screen.getByLabelText(/GCP project ID/)).toHaveAttribute("type", "text");
    expect(screen.getByLabelText(/^Region/)).toBeInTheDocument();
    expect(screen.queryByText(SUBSCRIPTION_WARNING)).not.toBeInTheDocument();
  });

  it("shows the subscription warning and password inputs for a subscription kind", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({
      "GET /auth/me": auth401,
      "POST /credentials": (call) => ({ status: 201, body: { ...EXISTING, id: 9, ...(call.body as object) } }),
    });
    const onSaved = vi.fn();
    renderWithProviders(<CredentialFormModal isOpen types={TYPES} onClose={vi.fn()} onSaved={onSaved} />);
    await user.selectOptions(await screen.findByLabelText(/Kind/), "claude_subscription");
    expect(screen.getByText(SUBSCRIPTION_WARNING)).toBeInTheDocument();
    const token = screen.getByLabelText(/OAuth token/);
    expect(token).toHaveAttribute("type", "password");
    await user.type(screen.getByLabelText(/Name/), "team-sub");
    await user.type(token, "sk-ant-oat-123");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "team-sub",
      kind: "claude_subscription",
      values: { oauth_token: "sk-ant-oat-123" },
    });
  });

  it("on edit, prefills public fields and omits blank secrets", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({
      "GET /auth/me": auth401,
      "PATCH /credentials/5": () => EXISTING,
    });
    const onSaved = vi.fn();
    renderWithProviders(
      <CredentialFormModal isOpen types={TYPES} credential={EXISTING} onClose={vi.fn()} onSaved={onSaved} />,
    );
    const region = await screen.findByLabelText(/^Region/);
    expect(region).toHaveValue("us-east5");
    const secret = screen.getByLabelText(/Service account JSON/);
    expect(secret).toHaveValue("");
    expect(secret).toHaveAttribute("placeholder", expect.stringContaining("Leave blank to keep"));
    expect(screen.getByLabelText(/Kind/)).toBeDisabled();

    await user.clear(region);
    await user.type(region, "europe-west1");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const body = calls.find((c) => c.method === "PATCH")?.body as { name?: string; values: object };
    expect(body.values).toEqual({ project_id: "proj-a", region: "europe-west1" });
    expect(body.values).not.toHaveProperty("service_account_json");
    expect(body.name).toBeUndefined();
  });

  it("buildCredentialValues sends secrets only when filled", () => {
    expect(
      buildCredentialValues(TYPES[0], { service_account_json: "  ", project_id: " p ", region: "" }),
    ).toEqual({ project_id: "p", region: "" });
    expect(
      buildCredentialValues(TYPES[0], { service_account_json: '{"a":1}', project_id: "p", region: "r" }),
    ).toEqual({ service_account_json: '{"a":1}', project_id: "p", region: "r" });
  });
});
