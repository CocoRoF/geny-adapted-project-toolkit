import { createContext, useContext } from "react";

import type { Locale, MessageKey } from "@/i18n";

export interface I18nSnapshot {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey) => string;
  execMessage: (code: string) => string;
}

export const I18nContext = createContext<I18nSnapshot | null>(null);

export function useI18n(): I18nSnapshot {
  const ctx = useContext(I18nContext);
  if (ctx === null) {
    throw new Error("useI18n must be used within an <I18nProvider>");
  }
  return ctx;
}
