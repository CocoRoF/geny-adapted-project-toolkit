/**
 * Phase N.1 — collapsible wrapper that groups a run of consecutive
 * tool-call cards under one header.
 *
 * The chat panel previously rendered every tool invocation as its own
 * `ToolCallCard`. An agent turn that runs 15+ tools (e.g. "explore
 * the repo": ls, find, Read, Read, ls, find, …) blew the message
 * column into a wall of nearly-identical strips and pushed the
 * assistant's natural-language reply far off-screen.
 *
 * This wrapper introduces a third visual level on top of the existing
 * card → detail-row hierarchy:
 *
 *   level 1 — Tools (N)        ← collapsed by default; this component
 *   level 2 — Bash / Read / …  ← existing ToolCallCard
 *   level 3 — args / result    ← existing card's expanded detail
 *
 * Status precedence on the header:
 *   any pair errored   → error tone
 *   else any running   → running tone
 *   else (all settled) → success tone
 */

import { useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, Wrench, XCircle } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import { ToolCallCard } from "@/chat/ToolCallCard";
import type { ToolPair } from "@/chat/tool-pair";
import { Badge } from "@/ui/Badge";

interface Props {
  pairs: ToolPair[];
  /** Forces the group to start expanded. The chat panel keeps it
   * closed during normal scroll-back; callers that want auto-expand
   * (e.g. a freshly-arrived in-flight group) pass `defaultOpen` */
  defaultOpen?: boolean;
}

function toolNameOf(pair: ToolPair): string {
  const data = pair.call.data;
  const raw = data["tool"] ?? data["tool_name"];
  return typeof raw === "string" && raw ? raw : "tool";
}

function aggregateStatus(pairs: ToolPair[]): "running" | "error" | "ok" {
  let anyRunning = false;
  for (const p of pairs) {
    if (p.error) return "error";
    if (p.running || (!p.result && !p.error)) anyRunning = true;
  }
  return anyRunning ? "running" : "ok";
}

function summariseNames(pairs: ToolPair[]): string {
  // Show the first ~3 distinct tool names with multiplicity counts:
  //   "Bash ×3, Read ×2, find" — gives a glance at what the group did
  //   without leaking inputs (which can be sensitive).
  const counts = new Map<string, number>();
  const order: string[] = [];
  for (const p of pairs) {
    const name = toolNameOf(p);
    if (!counts.has(name)) order.push(name);
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  const parts = order.slice(0, 4).map((n) => {
    const c = counts.get(n) ?? 1;
    return c > 1 ? `${n} ×${c}` : n;
  });
  if (order.length > 4) parts.push(`+${order.length - 4}`);
  return parts.join(", ");
}

export function ToolCallGroup({ pairs, defaultOpen = false }: Props) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);

  if (pairs.length === 0) return null;
  // Solo tools render flat — no wrapper. Keeps the simple case looking
  // identical to pre-N.1 and avoids a useless one-row "group".
  const solo = pairs.length === 1 ? pairs[0] : undefined;
  if (solo) {
    return <ToolCallCard pair={solo} />;
  }

  const status = aggregateStatus(pairs);
  const statusBadge =
    status === "error" ? (
      <Badge tone="danger">
        <XCircle className="mr-1 h-2.5 w-2.5" />
        {t("chat.tool.error")}
      </Badge>
    ) : status === "running" ? (
      <Badge tone="accent">
        <Loader2 className="mr-1 h-2.5 w-2.5 animate-spin" />
        {t("chat.tool.running")}
      </Badge>
    ) : (
      <Badge tone="success">
        <CheckCircle2 className="mr-1 h-2.5 w-2.5" />
        {t("chat.tool.ok")}
      </Badge>
    );

  return (
    <div
      data-testid="tool-group"
      data-tool-count={pairs.length}
      className={
        status === "error"
          ? "rounded-md border border-danger/40 bg-danger/5"
          : status === "running"
            ? "rounded-md border border-accent/40 bg-accent/5"
            : "rounded-md border border-border bg-bg-elevated"
      }
    >
      {/* Phase N.2.7 — same `min-w-0 + shrink-0` pattern as
          ToolCallCard so the badge stays single-line and the trailing
          names summary is the part that disappears in narrow panels. */}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left hover:bg-bg-subtle/40"
      >
        <span className="grid h-5 w-5 shrink-0 place-items-center rounded text-fg-muted">
          {open ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </span>
        <Wrench className="h-3.5 w-3.5 shrink-0 text-fg-muted" />
        <strong className="shrink-0 font-mono text-[12px] text-fg">
          {t("chat.tool_group.label").replace("{count}", String(pairs.length))}
        </strong>
        <span className="shrink-0">{statusBadge}</span>
        <span className="ml-auto min-w-0 truncate text-[11px] text-fg-muted">
          {summariseNames(pairs)}
        </span>
      </button>
      {open ? (
        <div className="space-y-1.5 border-t border-border px-2 py-2">
          {pairs.map((p) => (
            <ToolCallCard key={`group-pair-${p.call.seq}`} pair={p} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
