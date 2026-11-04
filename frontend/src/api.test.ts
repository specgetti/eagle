import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, formatErrorDetail, setUnauthorizedHandler } from "./api";
import { mockFetch } from "./test/utils";

describe("formatErrorDetail", () => {
  it("returns a string detail unchanged", () => {
    expect(formatErrorDetail("Cannot remove the last active admin", "fallback")).toBe(
      "Cannot remove the last active admin",
    );
  });

  it("formats a FastAPI 422 array, dropping the body/query prefix", () => {
    const detail = [
      { loc: ["body", "password"], msg: "String should have at least 12 characters", type: "x" },
      { loc: ["body", "role"], msg: "Input should be 'admin', 'member' or 'viewer'", type: "y" },
      { loc: ["query", "limit"], msg: "Input should be less than or equal to 200", type: "z" },
    ];
    expect(formatErrorDetail(detail, "fallback")).toBe(
      "password: String should have at least 12 characters; " +
        "role: Input should be 'admin', 'member' or 'viewer'; " +
        "limit: Input should be less than or equal to 200",
    );
  });

  it("keeps nested locations and handles items without loc", () => {
    expect(
      formatErrorDetail(
        [{ loc: ["body", "allowed_models", 0], msg: "too short" }, { msg: "bad" }],
        "fallback",
      ),
    ).toBe("allowed_models.0: too short; bad");
  });

  it("falls back for missing or empty details", () => {
    expect(formatErrorDetail(undefined, "Bad Request")).toBe("Bad Request");
    expect(formatErrorDetail([], "Bad Request")).toBe("Bad Request");
    expect(formatErrorDetail("", "Bad Request")).toBe("Bad Request");
  });
});

describe("request", () => {
  afterEach(() => setUnauthorizedHandler(null));

  it("throws ApiError with the formatted 422 detail", async () => {
    mockFetch({
      "POST /users": () => ({
        status: 422,
        body: { detail: [{ loc: ["body", "username"], msg: "Field required" }] },
      }),
    });
    const err = await api.createUser({ username: "", password: "x" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe("username: Field required");
  });

  it("uses the string detail of a 409", async () => {
    mockFetch({
      "DELETE /users/2": () => ({ status: 409, body: { detail: "Cannot remove the last active admin" } }),
    });
    await expect(api.deleteUser(2)).rejects.toThrow("Cannot remove the last active admin");
  });

  it("sends JSON with same-origin credentials and serialises query params", async () => {
    const { fn, calls } = mockFetch({ "GET /reviews": () => ({ items: [], total: 0 }) });
    await api.listReviews({ status: "running", repo: "", mine: true, limit: 20, offset: 40 });
    expect(fn.mock.calls[0][1]?.credentials).toBe("same-origin");
    expect(calls[0].query.get("status")).toBe("running");
    expect(calls[0].query.has("repo")).toBe(false);
    expect(calls[0].query.get("mine")).toBe("true");
    expect(calls[0].query.get("offset")).toBe("40");
  });

  it("returns undefined for 204", async () => {
    mockFetch({ "POST /auth/logout": () => ({ status: 204 }) });
    await expect(api.logout()).resolves.toBeUndefined();
  });

  it("calls the unauthorized handler on 401, but not for a failed login", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockFetch({
      "GET /reviews": () => ({ status: 401, body: { detail: "Not authenticated" } }),
      "POST /auth/login": () => ({ status: 401, body: { detail: "Invalid username or password" } }),
    });
    await expect(api.login("a", "b")).rejects.toThrow("Invalid username or password");
    expect(handler).not.toHaveBeenCalled();
    await expect(api.listReviews()).rejects.toThrow("Not authenticated");
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
