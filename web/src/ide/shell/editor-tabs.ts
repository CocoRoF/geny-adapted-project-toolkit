/** Phase N.3 — One tab in the editor column.
 *
 * - `file`    : Monaco editor on the workspace file at `path`.
 * - `diff`    : Single-file diff view (working tree vs HEAD) for `path`.
 * - `preview` : Embedded browser-style iframe at `url` (VSCode Simple
 *               Browser parity). Opened from the Services sidebar.
 *
 * `id` is the stable tab identity used for activate/close. For file
 * and diff tabs it's derived from `path` so re-opening the same file
 * activates the existing tab instead of stacking duplicates; for
 * preview tabs it's the URL for the same reason. */
export type EditorTab =
  | { id: string; kind: "file"; path: string }
  | { id: string; kind: "diff"; path: string }
  | { id: string; kind: "preview"; url: string; label: string };

/** Stable id derivation — caller can compare strings without
 * having to know each kind's identity rule. */
export function tabIdFor(kind: EditorTab["kind"], key: string): string {
  return `${kind}:${key}`;
}
