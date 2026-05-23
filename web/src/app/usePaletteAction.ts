import { useEffect } from "react";

import { type PaletteAction, usePalette } from "@/app/providers/palette-context";

/** Register a palette action while the calling component is mounted.
 *
 * Pass a stable `id` (e.g. "ide.layout.focus") — re-registering with
 * the same id replaces the prior entry, so a parent can safely
 * re-render with new `run` closures. */
export function usePaletteAction(action: PaletteAction): void {
  const palette = usePalette();
  const { id, title, section, keywords, shortcut, run } = action;
  useEffect(() => {
    return palette.register({ id, title, section, keywords, shortcut, run });
  }, [palette, id, title, section, keywords, shortcut, run]);
}
