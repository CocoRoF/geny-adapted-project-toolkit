import { en } from "@/i18n/en";
import { ko } from "@/i18n/ko";

export type Locale = "en" | "ko";

const catalogs = { en, ko } as const;

export type MessageKey = keyof typeof en;

export function t(key: MessageKey, locale: Locale): string {
  const catalog = catalogs[locale];
  return catalog[key] ?? en[key] ?? key;
}
