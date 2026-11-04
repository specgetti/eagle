import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeUser, mockFetch, renderApp } from "../test/utils";
import { SSO_ROLE_NOTE } from "./admin/UsersPage";

describe("SSO users", () => {
  it("account page hides password change for SSO users", async () => {
    mockFetch({ "GET /auth/me": () => makeUser({ auth_source: "oidc" }) });
    renderApp("/account");
    expect(await screen.findByText("Your account signs in through SSO")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change password" })).not.toBeInTheDocument();
  });

  it("account page offers password change for local users", async () => {
    mockFetch({ "GET /auth/me": () => makeUser() });
    renderApp("/account");
    expect(await screen.findByRole("button", { name: "Change password" })).toBeInTheDocument();
  });

  it("users table labels SSO users, hides reset password and notes role sync", async () => {
    const user = userEvent.setup();
    const admin = makeUser({ id: 1, username: "root", role: "admin" });
    const sso = makeUser({ id: 2, username: "sso-bob", auth_source: "oidc" });
    const local = makeUser({ id: 3, username: "carol" });
    mockFetch({ "GET /auth/me": () => admin, "GET /users": () => [admin, sso, local] });
    renderApp("/admin/users");

    const ssoRow = (await screen.findByText("sso-bob")).closest("tr")!;
    expect(within(ssoRow).getByText("SSO")).toBeInTheDocument();
    const carolRow = screen.getByText("carol").closest("tr")!;
    expect(within(carolRow).queryByText("SSO")).not.toBeInTheDocument();

    await user.click(within(carolRow).getByRole("button", { name: "Kebab toggle" }));
    expect(await screen.findByRole("menuitem", { name: "Reset password" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(within(ssoRow).getByRole("button", { name: "Kebab toggle" }));
    expect(screen.queryByRole("menuitem", { name: "Reset password" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    expect(await screen.findByText(SSO_ROLE_NOTE)).toBeInTheDocument();
  });
});
