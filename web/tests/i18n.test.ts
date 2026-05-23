import { describe, expect, it } from "vitest";

import { en } from "@/i18n/en";
import { ko } from "@/i18n/ko";
import { execMessage, t } from "@/i18n";

describe("i18n", () => {
  it("en and ko have the same key set", () => {
    expect(Object.keys(ko).sort()).toEqual(Object.keys(en).sort());
  });

  it("t() returns the locale translation", () => {
    expect(t("app.title", "ko")).toBe(ko["app.title"]);
    expect(t("app.title", "en")).toBe(en["app.title"]);
  });

  it("every exec.* error code key is present in both locales", () => {
    const execKeys = Object.keys(en).filter((k) => k.startsWith("exec."));
    expect(execKeys.length).toBeGreaterThan(0);
    for (const key of execKeys) {
      expect(ko).toHaveProperty(key);
    }
  });

  it("error code messages are non-empty in both locales", () => {
    const execKeys = Object.keys(en).filter((k) => k.startsWith("exec.")) as Array<keyof typeof en>;
    for (const key of execKeys) {
      expect(en[key].length).toBeGreaterThan(0);
      expect(ko[key].length).toBeGreaterThan(0);
    }
  });

  it("execMessage maps a known code to the catalog entry", () => {
    expect(execMessage("exec.tool.access_denied", "ko")).toBe(ko["exec.tool.access_denied"]);
    expect(execMessage("exec.tool.access_denied", "en")).toBe(en["exec.tool.access_denied"]);
  });

  it("execMessage falls back to the raw code for unknown ids", () => {
    expect(execMessage("exec.future.not_invented_yet", "ko")).toBe("exec.future.not_invented_yet");
  });

  it("catalogs cover all geny-executor exec.* families", () => {
    // Per `geny_executor.errors`, every code starts with a family
    // prefix. The catalog should ship at least one entry per family so
    // the UI never falls back to the raw code for an expected family.
    const families = new Set(
      Object.keys(en)
        .filter((k) => k.startsWith("exec."))
        .map((k) => k.split(".").slice(0, 2).join(".")),
    );
    for (const expected of [
      "exec.api",
      "exec.cli",
      "exec.mcp",
      "exec.mutation",
      "exec.pipeline",
      "exec.session",
      "exec.stage",
      "exec.tool",
    ]) {
      expect(families).toContain(expected);
    }
  });
});
