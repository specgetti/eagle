import { describe, expect, it } from "vitest";
import {
  formatTokens,
  fromTriState,
  modelText,
  modelTokensText,
  parseApiTime,
  splitList,
  toTriState,
} from "./utils";

describe("utils", () => {
  it("treats zone-less API timestamps as UTC", () => {
    const iso = "2026-11-03T20:51:02.803119";
    expect(parseApiTime(iso).toISOString()).toBe("2026-11-03T20:51:02.803Z");
    expect(parseApiTime(`${iso}Z`).toISOString()).toBe("2026-11-03T20:51:02.803Z");
    expect(parseApiTime("2026-11-03T22:51:02+02:00").toISOString()).toBe("2026-11-03T20:51:02.000Z");
  });

  it("maps tri-state values", () => {
    expect([toTriState(null), toTriState(true), toTriState(false)]).toEqual(["default", "yes", "no"]);
    expect([fromTriState("default"), fromTriState("yes"), fromTriState("no")]).toEqual([null, true, false]);
  });

  it("describes the model a review used and where it came from", () => {
    const r = (
      model: string | null,
      model_source: string | null,
      reported_model: string | null,
      status = "succeeded" as const,
    ) => modelText({ model, model_source, reported_model, status });
    expect(r("glm-5", "agent_default", null, "queued" as never)).toBe("glm-5 (agent default)");
    expect(r("glm-5", "agent_default", "glm-5")).toBe("glm-5 (agent default)");
    expect(r("glm-5", "agent_default", "glm-5.3-fp8")).toBe("glm-5.3-fp8 (agent default: glm-5)");
    expect(r("opus", "requested", "opus")).toBe("opus");
    expect(r("opus", "requested", "opus-4")).toBe("opus-4 (requested: opus)");
    expect(r(null, null, "claude-sonnet-4-5")).toBe("claude-sonnet-4-5 (CLI default)");
    expect(r(null, null, null)).toBe("CLI default (model not reported)");
    expect(r(null, null, null, "running" as never)).toBe("CLI default");
    // Reviews from before model_source existed.
    expect(r("m-1", null, null)).toBe("m-1");
  });

  it("formats token counts and per-model usage", () => {
    expect([999, 41234, 1_500_000, 2_000_000].map(formatTokens)).toEqual(["999", "41k", "1.5M", "2M"]);
    expect(
      modelTokensText([
        { model: "a", responses: 1, input_tokens: 10, output_tokens: 2 },
        { model: "b" },
      ]),
    ).toBe("a (1 response, 10 in / 2 out); b");
  });

  it("splits comma lists", () => {
    expect(splitList(" a@x.com, ,b@x.com,")).toEqual(["a@x.com", "b@x.com"]);
  });
});
