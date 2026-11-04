import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockFetch, renderApp } from "../test/utils";
import { ssoRedirect } from "./LoginPage";

function mockConfig(local_login: boolean, oidc: { enabled: boolean; label: string }) {
  return mockFetch({
    "GET /auth/me": () => ({ status: 401, body: { detail: "Not authenticated" } }),
    "GET /auth/config": () => ({ local_login, oidc }),
  });
}

describe("LoginPage", () => {
  it("shows the SSO button with the configured label and navigates the full page", async () => {
    const go = vi.spyOn(ssoRedirect, "go").mockImplementation(() => {});
    const user = userEvent.setup();
    const { fn } = mockConfig(true, { enabled: true, label: "Log in with Red Hat SSO" });
    renderApp("/login");
    const button = await screen.findByRole("button", { name: "Log in with Red Hat SSO" });
    expect(screen.getByLabelText(/Username/)).toBeInTheDocument();
    const fetchesBefore = fn.mock.calls.length;
    await user.click(button);
    expect(go).toHaveBeenCalledWith("/api/v1/auth/oidc/login");
    expect(fn.mock.calls.length).toBe(fetchesBefore);
  });

  it("hides the password form when local login is off", async () => {
    mockConfig(false, { enabled: true, label: "Company SSO" });
    renderApp("/login");
    expect(await screen.findByRole("button", { name: "Company SSO" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/Username/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Log in" })).not.toBeInTheDocument();
  });

  it("shows only the local form when SSO is disabled", async () => {
    mockConfig(true, { enabled: false, label: "" });
    renderApp("/login");
    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("shows the error passed back by the SSO callback", async () => {
    mockConfig(true, { enabled: true, label: "SSO" });
    renderApp("/login?error=Your%20account%20is%20not%20in%20an%20allowed%20group");
    expect(await screen.findByText("Sign-in failed")).toBeInTheDocument();
    expect(screen.getByText("Your account is not in an allowed group")).toBeInTheDocument();
  });

  it("shows the login error from the backend", async () => {
    const user = userEvent.setup();
    mockFetch({
      "GET /auth/me": () => ({ status: 401, body: { detail: "Not authenticated" } }),
      "GET /auth/config": () => ({ local_login: true, oidc: { enabled: false, label: "" } }),
      "POST /auth/login": () => ({ status: 401, body: { detail: "Invalid username or password" } }),
    });
    renderApp("/login");
    await user.type(await screen.findByLabelText(/Username/), "bob");
    await user.type(screen.getByLabelText(/Password/), "wrong");
    await user.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByText("Invalid username or password")).toBeInTheDocument();
  });
});
