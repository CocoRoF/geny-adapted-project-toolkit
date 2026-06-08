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
  /** True when no `tool_result` / `error` has arrived yet AND the
   *  session hasn't terminated under it. Pre Phase N.3 this latched
   *  to true forever when the agent died mid-call (budget exhaustion,
   *  crash, kill) — the UI showed "실행 중..." indefinitely for tools
   *  whose result frame would never arrive. The terminal-event
   *  cleanup pass now clears this. */
  running: boolean;
  /** True when the tool call NEVER got a result because the agent
   *  session terminated (DONE / session-level ERROR) while the call
   *  was still open. Distinct from `error` (which carries a real
   *  error payload from the tool itself) — `abandoned` means "we
   *  honestly don't know what happened". UI renders it as a muted
   *  warn state so the operator can tell a stuck call apart from a
   *  successful one. */
  abandoned: boolean;
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

/** Mark every still-pending pair as abandoned and clear the map.
 *  Called when a turn-terminal event (DONE / session-level ERROR)
 *  lands: any tool_call still open at that moment will never receive
 *  its result frame, so the UI must stop showing it as "running". */
function abandonAllPending(pending: Map<string, ToolPair>): void {
  for (const orphan of pending.values()) {
    orphan.running = false;
    orphan.abandoned = true;
  }
  pending.clear();
}

/** Returns a list of tool pairs in the order their `tool_call` events
 * appeared. `tool_call` events without a matching outcome but whose
 * turn has terminated (DONE / unmatched ERROR) show up with
 * `abandoned = true`. Still-open calls in a live turn keep
 * `running = true`. */
export function pairToolEvents(events: SessionStreamEvent[]): ToolPair[] {
  const pairs: ToolPair[] = [];
  const pending: Map<string, ToolPair> = new Map();

  for (const event of events) {
    if (event.kind === "tool_call") {
      const key = keyFor(event);
      const pair: ToolPair = {
        call: event,
        result: null,
        error: null,
        running: true,
        abandoned: false,
      };
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
        continue;
      }
      // Phase N.3 — an `error` event with no matching tool_call is
      // a SESSION-level failure (exec.session.crashed / cancelled /
      // budget_exhausted / ...). It tells us the agent died, so any
      // calls still open from this turn will never finish — mark
      // them abandoned so the UI stops spinning. Stray unmatched
      // `tool_result` frames are ignored (defensive: should not
      // happen, and we'd rather not flip running tools off on a
      // misrouted result).
      if (event.kind === "error") {
        abandonAllPending(pending);
      }
      continue;
    }
    if (event.kind === "done") {
      // Phase N.3 — turn boundary. Every turn ends with either a
      // `done` (clean completion) or an `error` (handled above);
      // any tool_call that didn't receive its result by now is
      // abandoned. A subsequent turn's `tool_call` opens a fresh
      // pending entry (this loop doesn't touch already-completed
      // pairs).
      abandonAllPending(pending);
    }
  }

  return pairs;
}
