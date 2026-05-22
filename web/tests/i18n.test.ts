import { describe, expect, it } from "vitest";

import { en } from "@/i18n/en";
import { ko } from "@/i18n/ko";
import { t } from "@/i18n";

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
    const execKeys = Object.keys(en).filter((k) => k.startsWith("exec.")) as Array<
      keyof typeof en
    >;
    for (const key of execKeys) {
      expect(en[key].length).toBeGreaterThan(0);
      expect(ko[key].length).toBeGreaterThan(0);
    }
  });
});
