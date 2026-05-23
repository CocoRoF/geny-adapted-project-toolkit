import { en } from "@/i18n/en";
import { ko } from "@/i18n/ko";

export type Locale = "en" | "ko";

const catalogs = { en, ko } as const;

export type MessageKey = keyof typeof en;

export function t(key: MessageKey, locale: Locale): string {
  const catalog = catalogs[locale];
  return catalog[key] ?? en[key] ?? key;
}

/** Resolve a stable `exec.*.*` code to a friendly message.
 *
 * - If the catalog has a matching key, returns the localised message.
 * - Otherwise returns the raw code so the UI never hides a missing
 *   translation behind a meaningless fallback. The chat panel surfaces
 *   the raw code anyway so operators can grep for it. */
export function execMessage(code: string, locale: Locale): string {
  const catalog = catalogs[locale];
  // Cast: `code` may be any exec.*.* string at runtime — we accept
  // unknowns and fall through to the raw code.
  const hit = (catalog as Record<string, string>)[code] ?? (en as Record<string, string>)[code];
  return hit ?? code;
}
