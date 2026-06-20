/** Type-safe helpers for SSE / WebSocket frame parsing.
 *
 * `MessageEvent.data` is `any` and `JSON.parse` returns `any` — every
 * stream consumer used to sprinkle `as` casts and `String(unknown)`
 * coercions (which render objects as "[object Object]"). These two
 * guards centralise the narrow path: bytes → object → typed field. */

/** Parse one JSON frame into a plain object. Returns null for
 * non-string payloads, malformed JSON, and non-object roots — the
 * caller drops the frame instead of crashing the stream. */
export function parseJsonObject(raw: unknown): Record<string, unknown> | null {
  if (typeof raw !== "string") return null;
  try {
    const value: unknown = JSON.parse(raw);
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** Narrow an unknown frame field to string — `fallback` (default "")
 * instead of "[object Object]" for anything non-string. */
export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}
