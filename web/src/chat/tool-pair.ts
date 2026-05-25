import type { SessionStreamEvent } from "@/chat/useSessionStream";

/** A tool call together with its (possibly missing) outcome.
 *
 * The SSE stream emits `tool_call` and `tool_result` (or `error`) as
 * separate frames. To render a single "card" per invocation we need
 * to walk the event list and pair them up. The pairing key is the
 * tool name plus call_id when present — the runtime echoes a stable
 * id, but if it doesn't (older payloads) we fall back to "n-th call
 * of tool X". */

export interface ToolPair {
  /** The opening `tool_call` event. */
  call: SessionStreamEvent;
  /** Matching `tool_result` if completed, else null. */
  result: SessionStreamEvent | null;
  /** Matching `error` if the call failed terminally, else null. */
  error: SessionStreamEvent | null;
  /** True when no `tool_result` / `error` has arrived yet. */
  running: boolean;
}

function keyFor(event: SessionStreamEvent): string {
  // The CLI-internal tool path uses `tool_use_id` (Anthropic naming);
  // the geny-executor tool-stage path uses `call_id` / `id`. Accept
  // any of them so both call sources pair cleanly.
  const id =
    event.data["tool_use_id"] ?? event.data["call_id"] ?? event.data["id"];
  if (typeof id === "string" && id.length > 0) return `id:${id}`;
  const tool = event.data["tool"] ?? event.data["tool_name"];
  if (typeof tool === "string") return `name:${tool}`;
  return "name:tool";
}

/** Returns a list of tool pairs in the order their `tool_call` events
 * appeared. `tool_call` events without a matching outcome show up
 * with `running = true`. */
export function pairToolEvents(events: SessionStreamEvent[]): ToolPair[] {
  const pairs: ToolPair[] = [];
  const pending: Map<string, ToolPair> = new Map();

  for (const event of events) {
    if (event.kind === "tool_call") {
      const key = keyFor(event);
      const pair: ToolPair = { call: event, result: null, error: null, running: true };
      pairs.push(pair);
      // The same key may appear again (same tool fired twice in a
      // row without an id). Overwrite the pending pointer so the
      // *next* matching outcome attaches to the freshest call.
      pending.set(key, pair);
      continue;
    }
    if (event.kind === "tool_result" || event.kind === "error") {
      const key = keyFor(event);
      const matched = pending.get(key);
      if (matched) {
        if (event.kind === "tool_result") matched.result = event;
        else matched.error = event;
        matched.running = false;
        pending.delete(key);
      }
      // Errors that arrive without a matching tool_call are left to
      // ChatPanel's plain error renderer.
    }
  }

  return pairs;
}
