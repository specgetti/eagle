import { describe, expect, it } from "vitest";
import { fromTriState, parseApiTime, splitList, toTriState } from "./utils";

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

  it("splits comma lists", () => {
    expect(splitList(" a@x.com, ,b@x.com,")).toEqual(["a@x.com", "b@x.com"]);
  });
});
