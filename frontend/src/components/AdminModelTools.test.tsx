import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Credential, CredentialType } from "../api";
import { mockFetch, renderWithProviders } from "../test/utils";
import { CredentialFormModal } from "./CredentialFormModal";
import { ModelPicker } from "./ModelPicker";
import { TestCredentialModal } from "./TestCredentialModal";

const MAAS: Credential = {
  id: 3,
  name: "enmaas",
  kind: "anthropic_compatible",
  public_config: { base_url: "https://maas.example" },
  secret_fields: { api_key: "****1234" },
  used_by: ["claude"],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const GATEWAY_TYPE: CredentialType = {
  kind: "anthropic_compatible",
  agents: ["claude"],
  subscription: false,
  fields: [
    { name: "base_url", label: "Base URL", secret: false, required: true, help: "Root of the gateway" },
    { name: "api_key", label: "API key", secret: true, required: true },
    {
      name: "auth_style",
      label: "Send the key as",
      secret: false,
      required: false,
      choices: ["x-api-key", "bearer"],
    },
  ],
};

const auth401 = () => ({ status: 401, body: { detail: "x" } });

describe("CredentialFormModal with choice fields", () => {
  it("renders choices as a dropdown with a default option and shows help", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({
      "GET /auth/me": auth401,
      "POST /credentials": () => ({ status: 201, body: MAAS }),
    });
    const onSaved = vi.fn();
    renderWithProviders(
      <CredentialFormModal isOpen types={[GATEWAY_TYPE]} onClose={vi.fn()} onSaved={onSaved} />,
    );
    const select = await screen.findByLabelText(/Send the key as/);
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "Default (x-api-key)" })).toBeInTheDocument();
    expect(screen.getByText("Root of the gateway")).toBeInTheDocument();

    await user.type(screen.getByLabelText(/Name/), "enmaas");
    await user.type(screen.getByLabelText(/Base URL/), "https://maas.example");
    await user.type(screen.getByLabelText(/API key/), "secret");
    await user.selectOptions(select, "bearer");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "enmaas",
      kind: "anthropic_compatible",
      values: { base_url: "https://maas.example", api_key: "secret", auth_style: "bearer" },
    });
  });
});

describe("TestCredentialModal", () => {
  it("prefills the model, offers fetched models, and shows a success result", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({
      "GET /auth/me": auth401,
      "GET /credentials/3/models": () => ({
        models: ["claude-opus-5-5", "rits/zai-org/glm-5-3"],
        source: "provider",
      }),
      "POST /credentials/3/test": () => ({
        ok: true,
        message: "The endpoint answered a request to rits/zai-org/glm-5-3.",
        latency_ms: 420,
      }),
    });
    renderWithProviders(
      <TestCredentialModal credential={MAAS} suggestedModel="claude-opus-5-5" onClose={vi.fn()} />,
    );
    const model = await screen.findByLabelText(/Model/);
    expect(model).toHaveValue("claude-opus-5-5");
    const options = await screen.findByTestId("test-model-options");
    expect(options.querySelectorAll("option")).toHaveLength(2);

    await user.clear(model);
    await user.type(model, "rits/zai-org/glm-5-3");
    await user.click(screen.getByRole("button", { name: "Run test" }));
    expect(await screen.findByText("Works")).toBeInTheDocument();
    expect(screen.getByText(/answered a request/)).toBeInTheDocument();
    expect(screen.getByText("Took 420 ms.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ model: "rits/zai-org/glm-5-3" });
  });

  it("shows failures and not-checked results distinctly, and sends no model when empty", async () => {
    const user = userEvent.setup();
    let ok: boolean | null = false;
    const { calls } = mockFetch({
      "GET /auth/me": auth401,
      "GET /credentials/3/models": () => ({ status: 502, body: { detail: "down" } }),
      "POST /credentials/3/test": () => ({ ok, message: ok === false ? "HTTP 401: the key was rejected" : "Cannot be tested" }),
    });
    renderWithProviders(<TestCredentialModal credential={MAAS} onClose={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "Run test" }));
    expect(await screen.findByText("Failed")).toBeInTheDocument();
    expect(screen.getByText(/key was rejected/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")[0].body).toEqual({ model: null });
    // A model-list failure only removes suggestions; it is not an error in the dialog.
    expect(screen.queryByText("down")).not.toBeInTheDocument();

    ok = null;
    await user.click(screen.getByRole("button", { name: "Run test" }));
    expect(await screen.findByText("Not checked")).toBeInTheDocument();
  });
});

function PickerHarness({ credentialId }: { credentialId: number | null }) {
  const [allowed, setAllowed] = useState<string[]>(["claude-opus-5-5"]);
  return (
    <>
      <ModelPicker
        id="t"
        credentialId={credentialId}
        allowed={allowed}
        onAllowedChange={setAllowed}
      />
      <output data-testid="allowed">{allowed.join(",")}</output>
    </>
  );
}

describe("ModelPicker", () => {
  it("is disabled without a credential", async () => {
    mockFetch({ "GET /auth/me": auth401 });
    renderWithProviders(<PickerHarness credentialId={null} />);
    expect(await screen.findByRole("button", { name: "Fetch models" })).toBeDisabled();
    expect(screen.getByText("Choose a credential first.")).toBeInTheDocument();
  });

  it("fetches models, ticks the allowed ones and toggles selections", async () => {
    const user = userEvent.setup();
    mockFetch({
      "GET /auth/me": auth401,
      "GET /credentials/3/models": () => ({
        models: ["claude-opus-5-5", "rits/zai-org/glm-5-3"],
        source: "provider",
      }),
    });
    renderWithProviders(<PickerHarness credentialId={3} />);
    await user.click(await screen.findByRole("button", { name: "Fetch models" }));
    const opus = await screen.findByRole("checkbox", { name: "claude-opus-5-5" });
    const glm = screen.getByRole("checkbox", { name: "rits/zai-org/glm-5-3" });
    expect(opus).toBeChecked();
    expect(glm).not.toBeChecked();

    await user.click(glm);
    expect(screen.getByTestId("allowed")).toHaveTextContent("claude-opus-5-5,rits/zai-org/glm-5-3");
    await user.click(opus);
    expect(screen.getByTestId("allowed")).toHaveTextContent(/^rits\/zai-org\/glm-5-3$/);
    expect(screen.getByRole("button", { name: "Refresh models" })).toBeInTheDocument();
  });

  it("shows the suggestion notice and fetch errors", async () => {
    const user = userEvent.setup();
    let fail = false;
    mockFetch({
      "GET /auth/me": auth401,
      "GET /credentials/3/models": () =>
        fail
          ? { status: 502, body: { detail: "HTTP 401: the key was rejected" } }
          : { models: ["opus", "sonnet"], source: "suggested", message: "Showing common choices." },
    });
    renderWithProviders(<PickerHarness credentialId={3} />);
    await user.click(await screen.findByRole("button", { name: "Fetch models" }));
    expect(await screen.findByText("Showing common choices.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "sonnet" })).toBeInTheDocument();

    fail = true;
    await user.click(screen.getByRole("button", { name: "Refresh models" }));
    expect(await screen.findByText("HTTP 401: the key was rejected")).toBeInTheDocument();
  });

  it("filters long lists", async () => {
    const user = userEvent.setup();
    const models = Array.from({ length: 12 }, (_, i) => `model-${i}`).concat(["glm-5-3"]);
    mockFetch({
      "GET /auth/me": auth401,
      "GET /credentials/3/models": () => ({ models, source: "provider" }),
    });
    renderWithProviders(<PickerHarness credentialId={3} />);
    await user.click(await screen.findByRole("button", { name: "Fetch models" }));
    await user.type(await screen.findByPlaceholderText("Filter models"), "glm");
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByRole("checkbox", { name: "glm-5-3" })).toBeInTheDocument();
  });
});
