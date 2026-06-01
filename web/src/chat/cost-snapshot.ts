import type { SessionStreamEvent } from "@/chat/useSessionStream";

/** Cost snapshot derived from the most recent SSE `cost` event.
 *
 * Mirrors `gapt_server.agent.hooks.cost_hook.CostAccumulator.snapshot()`
 * (Cycle 2.9). All fields default to 0 / empty so the panel still
 * renders before the first cost event arrives. */
export interface CostSnapshot {
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  // Phase K.2 — Anthropic cache tokens (default 0 for legacy events).
  cache_write_tokens: number;
  cache_read_tokens: number;
  tool_calls: number;
  tool_duration_ms: number;
  by_tool: Record<string, number>;
}

const EMPTY: CostSnapshot = {
  cost_usd: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_write_tokens: 0,
  cache_read_tokens: 0,
  tool_calls: 0,
  tool_duration_ms: 0,
  by_tool: {},
};

function num(data: Record<string, unknown>, key: string): number {
  const value = data[key];
  return typeof value === "number" ? value : 0;
}

function byToolFrom(data: Record<string, unknown>): Record<string, number> {
  const raw = data["by_tool"];
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === "number") out[k] = v;
  }
  return out;
}

export function deriveCostSnapshot(events: SessionStreamEvent[]): CostSnapshot {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (ev?.kind !== "cost") continue;
    return {
      cost_usd: num(ev.data, "cost_usd"),
      input_tokens: num(ev.data, "input_tokens"),
      output_tokens: num(ev.data, "output_tokens"),
      cache_write_tokens: num(ev.data, "cache_write_tokens"),
      cache_read_tokens: num(ev.data, "cache_read_tokens"),
      tool_calls: num(ev.data, "tool_calls"),
      tool_duration_ms: num(ev.data, "tool_duration_ms"),
      by_tool: byToolFrom(ev.data),
    };
  }
  return EMPTY;
}

/** Format milliseconds as a compact "1.2 s" / "340 ms" string. */
export function formatMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}
