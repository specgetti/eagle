import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentOption } from "../api";
import { mockFetch, renderWithProviders } from "../test/utils";
import { SubmitReviewForm } from "./SubmitReviewForm";

const OPTIONS: AgentOption[] = [
  {
    agent: "claude",
    default_model: "claude-sonnet-4-5",
    allowed_models: [],
    configured: true,
  },
  {
    agent: "codex",
    default_model: "gpt-5",
    allowed_models: ["gpt-5", "gpt-5-mini"],
    configured: false,
  },
];

function setup() {
  const mock = mockFetch({
    "GET /auth/me": () => ({
      status: 401,
      body: { detail: "Not authenticated" },
    }),
    "GET /reviews/options": () => OPTIONS,
    "POST /reviews": (call) => ({
      status: 201,
      body: { id: 7, repo: "org/repo", pr_number: 1, ...(call.body as object) },
    }),
  });
  const onSubmitted = vi.fn();
  renderWithProviders(<SubmitReviewForm onSubmitted={onSubmitted} />);
  return { ...mock, onSubmitted };
}

const submitted = (calls: ReturnType<typeof setup>["calls"]) =>
  calls.filter((c) => c.method === "POST" && c.path === "/reviews");

describe("SubmitReviewForm", () => {
  it("says which model a blank model field means for the chosen agent", async () => {
    const user = userEvent.setup();
    mockFetch({
      "GET /auth/me": () => ({
        status: 401,
        body: { detail: "Not authenticated" },
      }),
      "GET /reviews/options": () => [
        ...OPTIONS,
        {
          agent: "opencode",
          default_model: null,
          allowed_models: [],
          configured: true,
        },
      ],
    });
    renderWithProviders(<SubmitReviewForm onSubmitted={vi.fn()} />);
    await screen.findByRole("option", { name: "Claude Code" });
    const agent = screen.getByLabelText("Agent");
    const model = () => screen.getByLabelText("Model");

    expect(model()).toHaveAttribute("placeholder", "Agent default");

    await user.selectOptions(agent, "claude");
    expect(model()).toHaveAttribute(
      "placeholder",
      "Agent default: claude-sonnet-4-5",
    );
    expect(
      screen.getByText(/use the agent default, claude-sonnet-4-5/),
    ).toBeInTheDocument();

    await user.selectOptions(agent, "opencode");
    expect(model()).toHaveAttribute("placeholder", "CLI default");
    expect(screen.getByText(/the agent CLI picks one/)).toBeInTheDocument();

    await user.selectOptions(agent, "codex");
    expect(
      screen.getByRole("option", { name: "Agent default (gpt-5)" }),
    ).toBeInTheDocument();
  });

  it("sends nulls for blank model, blank context and default post_comment", async () => {
    const user = userEvent.setup();
    const { calls, onSubmitted } = setup();
    await screen.findByRole("option", { name: "Claude Code" });

    await user.type(
      screen.getByLabelText(/Pull request URL/),
      "https://github.com/org/repo/pull/1",
    );
    await user.type(screen.getByLabelText(/Additional context/), "   ");
    await user.click(screen.getByRole("button", { name: "Submit review" }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalled());
    expect(submitted(calls)[0].body).toEqual({
      pr_url: "https://github.com/org/repo/pull/1",
      agent: null,
      model: null,
      custom_prompt: null,
      post_comment: null,
    });
  });

  it("sends the chosen agent, allowed model, context and post_comment yes/no", async () => {
    const user = userEvent.setup();
    const { calls, onSubmitted } = setup();
    await screen.findByRole("option", { name: /Codex/ });

    await user.type(
      screen.getByLabelText(/Pull request URL/),
      "https://github.com/org/repo/pull/2",
    );
    await user.selectOptions(screen.getByLabelText("Agent"), "codex");
    // Codex has no credential: warning label shown, allowed models become a select.
    expect(screen.getByText("No credential configured")).toBeInTheDocument();
    const model = screen.getByLabelText("Model");
    expect(model.tagName).toBe("SELECT");
    expect(
      screen.getByRole("option", { name: "Agent default (gpt-5)" }),
    ).toBeInTheDocument();
    await user.selectOptions(model, "gpt-5-mini");
    await user.type(
      screen.getByLabelText(/Additional context/),
      "Focus on db/",
    );
    expect(screen.getByText("12 / 8000 characters")).toBeInTheDocument();
    await user.selectOptions(
      screen.getByLabelText("Post review as PR comment"),
      "yes",
    );
    await user.click(screen.getByRole("button", { name: "Submit review" }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(1));
    expect(submitted(calls)[0].body).toEqual({
      pr_url: "https://github.com/org/repo/pull/2",
      agent: "codex",
      model: "gpt-5-mini",
      custom_prompt: "Focus on db/",
      post_comment: true,
    });

    await user.type(
      screen.getByLabelText(/Pull request URL/),
      "https://github.com/org/repo/pull/3",
    );
    await user.selectOptions(
      screen.getByLabelText("Post review as PR comment"),
      "no",
    );
    await user.click(screen.getByRole("button", { name: "Submit review" }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(2));
    expect(submitted(calls)[1].body).toMatchObject({ post_comment: false });
  });

  it("uses a free text model with the agent default as placeholder when no allowed list", async () => {
    const user = userEvent.setup();
    const { calls, onSubmitted } = setup();
    await screen.findByRole("option", { name: "Claude Code" });
    await user.selectOptions(screen.getByLabelText("Agent"), "claude");
    const model = screen.getByLabelText("Model");
    expect(model.tagName).toBe("INPUT");
    expect(model).toHaveAttribute("placeholder", "Agent default: claude-sonnet-4-5");
    await user.type(
      screen.getByLabelText(/Pull request URL/),
      "https://github.com/org/repo/pull/4",
    );
    await user.type(model, "  claude-opus-4-1 ");
    await user.click(screen.getByRole("button", { name: "Submit review" }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalled());
    expect(submitted(calls)[0].body).toMatchObject({
      agent: "claude",
      model: "claude-opus-4-1",
    });
  });

  it("shows the backend error message", async () => {
    const user = userEvent.setup();
    mockFetch({
      "GET /auth/me": () => ({ status: 401, body: { detail: "x" } }),
      "GET /reviews/options": () => OPTIONS,
      "POST /reviews": () => ({
        status: 422,
        body: { detail: "Not a pull request URL" },
      }),
    });
    renderWithProviders(<SubmitReviewForm onSubmitted={vi.fn()} />);
    await screen.findByRole("option", { name: "Claude Code" });
    await user.type(
      screen.getByLabelText(/Pull request URL/),
      "https://github.com/org/repo",
    );
    await user.click(screen.getByRole("button", { name: "Submit review" }));
    expect(
      await screen.findByText("Not a pull request URL"),
    ).toBeInTheDocument();
  });
});
