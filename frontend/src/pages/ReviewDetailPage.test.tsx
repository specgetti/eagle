import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { ModelReport, ReviewDetail } from "../api";
import { makeUser, mockFetch, renderApp } from "../test/utils";

function review(overrides: Partial<ReviewDetail>): ReviewDetail {
  return {
    id: 7,
    pr_url: "https://github.com/org/repo/pull/1",
    git_host: "github.com",
    repo: "org/repo",
    pr_number: 1,
    head_sha: "abcdef1234567890",
    title: "Fix the widget",
    author: "octocat",
    submitted_by_id: 1,
    trigger: "manual",
    previous_review_id: null,
    agent: "claude",
    model: "glm-5",
    model_source: "agent_default",
    reported_model: "glm-5",
    model_report: null,
    model_mismatch: false,
    post_comment: false,
    status: "succeeded",
    summary: "Fine",
    verdict: "approve",
    cost_usd: null,
    error: null,
    posted_comment_url: null,
    created_at: "2026-11-06T10:00:00Z",
    started_at: "2026-11-06T10:00:05Z",
    finished_at: "2026-11-06T10:02:00Z",
    custom_prompt: null,
    since_sha: null,
    base_ref: "main",
    result_markdown: "## Summary\nFine",
    token_usage: null,
    delivery_errors: null,
    ...overrides,
  };
}

function show(r: ReviewDetail) {
  mockFetch({
    "GET /auth/me": () => makeUser({ role: "member" }),
    "GET /auth/config": () => ({ local_login_enabled: true, oidc_enabled: false }),
    // Wrapped: a bare review has a "status" field, which mockFetch reads as the HTTP status.
    "GET /reviews/7": () => ({ status: 200, body: r }),
  });
  renderApp("/reviews/7");
}

const REPORT: ModelReport = {
  served: [
    { model: "glm-5", responses: 12, input_tokens: 41234, output_tokens: 3100 },
    { model: "qwen-3-coder", responses: 2, input_tokens: 900, output_tokens: 120 },
  ],
  usage_by_model: [
    { model: "glm-5", input_tokens: 42000, output_tokens: 3200 },
    { model: "claude-haiku-4-5", input_tokens: 300, output_tokens: 20 },
  ],
};

describe("ReviewDetailPage models", () => {
  it("shows which models answered and warns on a mismatch", async () => {
    show(review({ model_report: REPORT, model_mismatch: true }));
    expect(
      await screen.findByText("The endpoint answered with a model that was not requested"),
    ).toBeInTheDocument();
    const served = "glm-5 (12 responses, 41k in / 3k out); qwen-3-coder (2 responses, 900 in / 120 out)";
    expect(screen.getByText(served)).toBeInTheDocument();
    expect(
      screen.getByText("glm-5 (42k in / 3k out); claude-haiku-4-5 (300 in / 20 out)"),
    ).toBeInTheDocument();
  });

  it("shows the served model without a warning when it matches", async () => {
    show(
      review({
        model_report: {
          served: [{ model: "glm-5-20260101", responses: 3, input_tokens: 10, output_tokens: 5 }],
          usage_by_model: [{ model: "glm-5", input_tokens: 10, output_tokens: 5 }],
        },
      }),
    );
    expect(await screen.findByText("glm-5-20260101 (3 responses, 10 in / 5 out)")).toBeInTheDocument();
    expect(screen.queryByText(/not requested/)).not.toBeInTheDocument();
    expect(screen.queryByText("Usage by model (per the agent)")).not.toBeInTheDocument();
  });
});
