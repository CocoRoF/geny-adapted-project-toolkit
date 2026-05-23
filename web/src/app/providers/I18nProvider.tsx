import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { execMessage, t, type Locale, type MessageKey } from "@/i18n";
import { I18nContext, type I18nSnapshot } from "@/app/providers/i18n-context";

const STORAGE_KEY = "gapt.locale";

function loadInitial(): Locale {
  if (typeof window === "undefined") return "en";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "en" || stored === "ko") return stored;
  // Default to Korean for `ko-*` browsers, English otherwise — matches
  // the user base today (solo Korean dev) without locking the other way.
  const navLang = (window.navigator.language ?? "").toLowerCase();
  return navLang.startsWith("ko") ? "ko" : "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(loadInitial);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(STORAGE_KEY, locale);
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next: Locale) => setLocaleState(next), []);

  const value = useMemo<I18nSnapshot>(
    () => ({
      locale,
      setLocale,
      t: (key: MessageKey) => t(key, locale),
      execMessage: (code: string) => execMessage(code, locale),
    }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
