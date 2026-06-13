import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/** Guard against the i18n regression this sweep fixed: user-facing
 * Korean string literals that bypass t() and leak into the English
 * locale. We strip comments (Korean comments are fine) then flag any
 * remaining Hangul. The two intentional exceptions are the i18n
 * catalogs themselves and StackRerouteHelpModal, whose long-form help
 * ships as parallel locale-gated `BodyKo()/BodyEn()` trees (Korean is
 * only ever rendered when locale === "ko"). */

const SRC = join(__dirname, "..", "src");
const HANGUL = /[가-힣]/;
const ALLOW = new Set(["i18n/en.ts", "i18n/ko.ts", "ide/StackRerouteHelpModal.tsx"]);

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

/** Remove // line comments, /* block comments, and JSX {/* … *​/}
 * comments so only real code/markup remains. Crude but sufficient —
 * false negatives (Korean in a weird spot) are acceptable; we only
 * need to catch the common "hardcoded JSX/label/string" leak. */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "") // block + JSX comments
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1"); // line comments (not URLs)
}

describe("no hardcoded Korean in components", () => {
  const files = walk(SRC);

  it("scans a non-trivial number of source files", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  for (const file of files) {
    const rel = file.slice(SRC.length + 1).replace(/\\/g, "/");
    if (ALLOW.has(rel)) continue;
    it(`${rel} has no user-facing Korean`, () => {
      const stripped = stripComments(readFileSync(file, "utf-8"));
      const offending = stripped
        .split("\n")
        .map((line, i) => ({ line: line.trim(), n: i + 1 }))
        .filter((x) => HANGUL.test(x.line));
      expect(
        offending,
        `Hangul outside t() in ${rel} — route it through the i18n catalog:\n` +
          offending.map((x) => `  L${x.n}: ${x.line.slice(0, 80)}`).join("\n"),
      ).toEqual([]);
    });
  }
});
