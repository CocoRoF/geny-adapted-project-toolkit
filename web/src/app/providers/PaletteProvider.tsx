import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type PaletteAction,
  type PaletteRegistry,
  PaletteContext,
} from "@/app/providers/palette-context";

/** Owns the action registry + open/close state for the palette.
 *
 * Actions are kept in a Map keyed by `id` so a remount of the same
 * component replaces the prior entry instead of duplicating it. We
 * push a re-render to subscribers (the palette UI) by bumping a
 * version counter whenever the set mutates. */
export function PaletteProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const actions = useRef(new Map<string, PaletteAction>());
  const listeners = useRef(new Set<() => void>());

  const notify = useCallback(() => {
    for (const listener of listeners.current) listener();
  }, []);

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);

  const register = useCallback(
    (action: PaletteAction) => {
      actions.current.set(action.id, action);
      notify();
      return () => {
        actions.current.delete(action.id);
        notify();
      };
    },
    [notify],
  );

  const list = useCallback(() => Array.from(actions.current.values()), []);

  const subscribe = useCallback((listener: () => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  // Cmd/Ctrl+K from anywhere opens the palette. Esc closes it.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const isToggle = (e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey);
      if (isToggle) {
        e.preventDefault();
        setIsOpen((current) => !current);
        return;
      }
      if (e.key === "Escape" && isOpen) {
        e.preventDefault();
        setIsOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen]);

  const value = useMemo<PaletteRegistry>(
    () => ({ open, close, isOpen, register, list, subscribe }),
    [open, close, isOpen, register, list, subscribe],
  );

  return <PaletteContext.Provider value={value}>{children}</PaletteContext.Provider>;
}
