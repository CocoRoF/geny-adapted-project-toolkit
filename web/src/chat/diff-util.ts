/** Tiny inline-diff renderer.
 *
 * `gapt_edit` is a find-and-replace: the tool result tells us `old`
 * vs `new` plus the file path. The full file isn't echoed, so for
 * the inline preview we render `old` (-) and `new` (+) blocks
 * side-by-side. For the side-by-side mode we feed the same two
 * strings into Monaco's DiffEditor.
 *
 * A line-level LCS would be nicer, but the surface stays useful
 * even without it: the user sees what changed, the path, and a
 * Revert affordance. M1-E4 swaps this for a real diff when the
 * backend grows a "preview before apply" mode. */

export type DiffLine = { kind: "context"; value: string } | { kind: "del" | "add"; value: string };

export interface UnifiedDiff {
  removed: string[];
  added: string[];
}

export function unifiedDiff(oldText: string, newText: string): UnifiedDiff {
  return {
    removed: oldText.split("\n"),
    added: newText.split("\n"),
  };
}

export function countLines(text: string): number {
  if (text === "") return 0;
  return text.split("\n").length;
}

/** Threshold the DiffCard uses to switch between inline view and the
 * heavier Monaco DiffEditor. Plan §3.6: "small changes (<20 lines)
 * inline, larger side-by-side". */
export const INLINE_THRESHOLD_LINES = 20;
