import { ReactNode } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AppRoutes, Providers, ROUTER_FUTURE } from "../App";
import { User } from "../api";

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

type Handler = (call: Call) => { status?: number; body?: unknown } | unknown;

/**
 * Replace global fetch with a router keyed by "METHOD /path" (path without
 * /api/v1 and without the query string). A handler may return a plain body
 * (status 200) or {status, body}. Unmatched requests return 404.
 */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = (init?.method ?? "GET").toUpperCase();
    const call: Call = {
      method,
      path,
      query: url.searchParams,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const handler = routes[`${method} ${path}`];
    if (!handler) return jsonResponse(404, { detail: `No mock for ${method} ${path}` });
    const result = handler(call) as { status?: number; body?: unknown } | undefined;
    if (result && typeof result === "object" && "status" in result) {
      return jsonResponse(result.status ?? 200, result.body);
    }
    return jsonResponse(200, result);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export function jsonResponse(status: number, body: unknown): Response {
  if (status === 204 || body === undefined) return new Response(null, { status: status === 200 ? 204 : status });
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    username: "alice",
    email: "alice@example.com",
    role: "member",
    is_active: true,
    auth_source: "local",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]} future={ROUTER_FUTURE}>
      <Providers>
        <AppRoutes />
      </Providers>
    </MemoryRouter>,
  );
}

export function renderWithProviders(ui: ReactNode, path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]} future={ROUTER_FUTURE}>
      <Providers>{ui}</Providers>
    </MemoryRouter>,
  );
}

export const emptyPage = { items: [], total: 0 };
