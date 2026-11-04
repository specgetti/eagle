import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { Role } from "./api";
import { emptyPage, makeUser, mockFetch, renderApp } from "./test/utils";

function mockAs(role: Role) {
  return mockFetch({
    "GET /auth/me": () => makeUser({ role }),
    "GET /reviews/options": () => [],
    "GET /reviews": () => emptyPage,
  });
}

describe("role based visibility", () => {
  it("viewer sees no submit form and no Admin nav", async () => {
    mockAs("viewer");
    renderApp("/");
    expect(await screen.findByText("No reviews")).toBeInTheDocument();
    expect(screen.queryByText("Request a review")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Pull request URL/)).not.toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main navigation", hidden: true });
    expect(within(nav).getByText("Reviews")).toBeInTheDocument();
    expect(within(nav).queryByText("Admin")).not.toBeInTheDocument();
  });

  it("member sees the submit form but no Admin nav", async () => {
    mockAs("member");
    renderApp("/");
    expect(await screen.findByText("Request a review")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main navigation", hidden: true });
    expect(within(nav).queryByText("Admin")).not.toBeInTheDocument();
    expect(within(nav).queryByText("Credentials")).not.toBeInTheDocument();
  });

  it("admin sees the submit form and the Admin nav group", async () => {
    mockAs("admin");
    renderApp("/");
    expect(await screen.findByText("Request a review")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main navigation", hidden: true });
    expect(within(nav).getByText("Admin")).toBeInTheDocument();
    for (const label of ["Users", "Agents", "Credentials", "Settings", "Audit log"]) {
      expect(within(nav).getByText(label)).toBeInTheDocument();
    }
  });

  it("non-admin opening an admin URL is sent back to reviews", async () => {
    mockAs("member");
    renderApp("/admin/users");
    expect(await screen.findByText("Request a review")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Users" })).not.toBeInTheDocument();
  });

  it("a logged out user is sent to the login page", async () => {
    mockFetch({
      "GET /auth/me": () => ({ status: 401, body: { detail: "Not authenticated" } }),
      "GET /auth/config": () => ({ local_login: true, oidc: { enabled: false, label: "" } }),
    });
    renderApp("/tracked");
    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
  });
});
