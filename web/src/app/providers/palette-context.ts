import { createContext, useContext } from "react";

/** Global registry the command palette reads from.
 *
 * Components register actions on mount via `usePaletteAction(...)`.
 * The registry stays flat — each action carries a `section` so the
 * palette can group on render. */

export interface PaletteAction {
  id: string;
  title: string;
  section: string;
  keywords?: string[] | undefined;
  shortcut?: string | undefined;
  run: () => void;
}

export interface PaletteRegistry {
  open: () => void;
  close: () => void;
  isOpen: boolean;
  /** Returns an unsubscribe function. */
  register: (action: PaletteAction) => () => void;
  /** Snapshot for the palette UI. */
  list: () => PaletteAction[];
  /** Re-render hook — palette UI subscribes to mutations. */
  subscribe: (listener: () => void) => () => void;
}

export const PaletteContext = createContext<PaletteRegistry | null>(null);

export function usePalette(): PaletteRegistry {
  const ctx = useContext(PaletteContext);
  if (ctx === null) {
    throw new Error("usePalette must be used within a <PaletteProvider>");
  }
  return ctx;
}
