import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import {
  type ResolvedTheme,
  type ThemeMode,
  type ThemeSnapshot,
  ThemeContext,
} from "@/app/providers/theme-context";

const STORAGE_KEY = "gapt.theme";

function loadInitial(): ThemeMode {
  if (typeof window === "undefined") return "system";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark" || stored === "system") return stored;
  return "system";
}

function resolveSystem(): ResolvedTheme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Tracks the user's theme preference + the actual rendered theme.
 *
 * `mode = system` follows `prefers-color-scheme` and re-evaluates
 * when the OS-level setting changes. We bind the resolved theme to
 * `<html data-theme>` so CSS can branch with a single attribute
 * selector. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(loadInitial);
  const [systemResolved, setSystemResolved] = useState<ResolvedTheme>(resolveSystem);

  const resolved: ResolvedTheme = mode === "system" ? systemResolved : mode;

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemResolved(mql.matches ? "dark" : "light");
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.dataset["theme"] = resolved;
    document.documentElement.style.colorScheme = resolved;
  }, [resolved]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const value = useMemo<ThemeSnapshot>(
    () => ({ mode, resolved, setMode }),
    [mode, resolved, setMode],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
