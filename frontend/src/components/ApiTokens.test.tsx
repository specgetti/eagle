import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiToken } from "../api";
import { makeUser, mockFetch, renderApp } from "../test/utils";

const SECRET = "eagle_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";

function setup(role: "member" | "admin" = "member") {
  let tokens: ApiToken[] = [];
  const mock = mockFetch({
    "GET /auth/me": () => makeUser({ role }),
    "GET /auth/config": () => ({ local_login_enabled: true, oidc_enabled: false }),
    "GET /tokens": () => tokens,
    "POST /tokens": (call) => {
      const body = call.body as { name: string; expires_in_days: number | null };
      const token: ApiToken = {
        id: 1,
        name: body.name,
        prefix: SECRET.slice(0, 12),
        username: "alice",
        expires_at: body.expires_in_days ? "2027-01-01T00:00:00Z" : null,
        last_used_at: null,
        created_at: "2026-11-05T00:00:00Z",
      };
      tokens = [token];
      return { status: 201, body: { ...token, token: SECRET } };
    },
    "DELETE /tokens/1": () => {
      tokens = [];
      return { status: 204 };
    },
  });
  return mock;
}

describe("API tokens", () => {
  it("creates a token, shows it once, lists only its prefix and revokes it", async () => {
    const user = userEvent.setup();
    const { calls } = setup();
    renderApp("/account");

    expect(await screen.findByText("No API tokens")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create token" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Name/), "github-actions");
    await user.selectOptions(within(dialog).getByLabelText("Expires after"), "never");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await screen.findByText("This is the only time the token is shown")).toBeInTheDocument();
    expect(screen.getByDisplayValue(SECRET)).toBeInTheDocument();
    const create = calls.find((c) => c.method === "POST" && c.path === "/tokens");
    expect(create?.body).toEqual({ name: "github-actions", expires_in_days: null });

    await user.click(screen.getByRole("button", { name: "Done" }));
    const row = (await screen.findByText("github-actions")).closest("tr")!;
    expect(within(row).getByText(`${SECRET.slice(0, 12)}...`)).toBeInTheDocument();
    expect(screen.queryByDisplayValue(SECRET)).not.toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Revoke" }));
    const confirm = await screen.findByRole("dialog");
    await user.click(within(confirm).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(screen.getByText("No API tokens")).toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/tokens/1")).toBe(true);
  });

  it("admin page lists every user's tokens", async () => {
    const { calls } = setup("admin");
    renderApp("/admin/api-tokens");
    expect(await screen.findByRole("heading", { name: "API tokens" })).toBeInTheDocument();
    await waitFor(() =>
      expect(calls.some((c) => c.path === "/tokens" && c.query.get("all") === "true")).toBe(true),
    );
  });
});
