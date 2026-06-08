import { useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  Loader2,
  XCircle,
} from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import type { ToolPair } from "@/chat/tool-pair";
import { Badge } from "@/ui/Badge";

interface Props {
  pair: ToolPair;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function summariseArgs(data: Record<string, unknown>): string {
  const args = (data["args"] ?? data["tool_input"] ?? data) as Record<string, unknown>;
  const entries = Object.entries(args).filter(
    ([k]) => !["tool", "tool_name", "call_id"].includes(k),
  );
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => {
      const repr = typeof v === "string" ? v : JSON.stringify(v);
      const truncated = repr.length > 60 ? `${repr.slice(0, 57)}…` : repr;
      return `${k}=${truncated}`;
    })
    .join("  ");
}

export function ToolCallCard({ pair }: Props) {
  const { t, execMessage } = useI18n();
  const [open, setOpen] = useState(false);

  const tool = asString(pair.call.data["tool"]) || asString(pair.call.data["tool_name"]) || "tool";
  const argsSummary = summariseArgs(pair.call.data);

  const errorCode = pair.error ? asString(pair.error.data["exec_code"], "error") : null;
  const errorReason = pair.error ? asString(pair.error.data["reason"]) : null;

  // Phase N.3 — "abandoned" is distinct from "error" and "running":
  // the call's result frame never arrived because the session
  // terminated under it. Keep it visually quieter (warn-tone, no
  // spinner) so the operator can scan a transcript and recognise the
  // stuck-mid-turn case at a glance.
  const status: "error" | "ok" | "abandoned" | "running" = pair.error
    ? "error"
    : pair.result
      ? "ok"
      : pair.abandoned
        ? "abandoned"
        : "running";
  const statusBadge =
    status === "error" ? (
      <Badge tone="danger">
        <XCircle className="mr-1 h-2.5 w-2.5" />
        {t("chat.tool.error")}
      </Badge>
    ) : status === "ok" ? (
      <Badge tone="success">
        <CheckCircle2 className="mr-1 h-2.5 w-2.5" />
        {t("chat.tool.ok")}
      </Badge>
    ) : status === "abandoned" ? (
      <Badge tone="warn">
        <CircleSlash className="mr-1 h-2.5 w-2.5" />
        {t("chat.tool.abandoned")}
      </Badge>
    ) : (
      <Badge tone="accent">
        <Loader2 className="mr-1 h-2.5 w-2.5 animate-spin" />
        {t("chat.tool.running")}
      </Badge>
    );

  return (
    <div
      data-testid="tool-card"
      data-tool-name={tool}
      data-status={status}
      className={
        status === "error"
          ? "rounded-md border border-danger/40 bg-danger/5"
          : status === "ok"
            ? "rounded-md border border-border bg-bg-elevated"
            : status === "abandoned"
              ? "rounded-md border border-warn/40 bg-warn/5"
              : "rounded-md border border-accent/40 bg-accent/5"
      }
    >
      {/* Phase N.2.7 — `min-w-0` on the flex container so the trailing
          args summary can actually truncate; `shrink-0` on the
          chevron/tool/badge so they keep their natural width while the
          args (which already has `truncate`) absorbs the squeeze. */}
      <header className="flex min-w-0 items-center gap-2 px-3 py-2">
        <button
          type="button"
          aria-expanded={open}
          aria-label={open ? t("chat.tool.collapse") : t("chat.tool.expand")}
          onClick={() => setOpen((v) => !v)}
          className="grid h-5 w-5 shrink-0 place-items-center rounded text-fg-muted hover:bg-surface-hover hover:text-fg"
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </button>
        <strong className="shrink-0 font-mono text-[12px] text-accent">{tool}</strong>
        <span className="shrink-0">{statusBadge}</span>
        {argsSummary ? (
          <code className="ml-auto min-w-0 truncate text-[11px] text-fg-muted">
            {argsSummary}
          </code>
        ) : null}
      </header>

      {open ? (
        <div className="space-y-2 border-t border-border px-3 py-2">
          <div>
            <h4 className="mb-1 text-[10px] uppercase tracking-wide text-fg-muted">
              {t("chat.tool.args")}
            </h4>
            <pre className="max-h-40 overflow-auto rounded bg-bg-subtle p-2 text-[11px] text-fg-muted">
              {JSON.stringify(pair.call.data, null, 2)}
            </pre>
          </div>
          {pair.result ? (
            <div>
              <h4 className="mb-1 text-[10px] uppercase tracking-wide text-fg-muted">
                {t("chat.tool_result")}
              </h4>
              <pre className="max-h-40 overflow-auto rounded bg-bg-subtle p-2 text-[11px] text-fg-muted">
                {JSON.stringify(pair.result.data, null, 2)}
              </pre>
            </div>
          ) : null}
          {status === "abandoned" ? (
            <div className="rounded border border-warn/40 bg-warn/10 p-2 text-[11px] text-warn">
              {t("chat.tool.abandoned_hint")}
            </div>
          ) : null}
          {pair.error && errorCode ? (
            <div role="alert" className="rounded border border-danger/40 bg-danger/10 p-2">
              <strong className="font-mono text-[11px] text-danger">{errorCode}</strong>
              <p className="mt-1 text-[11px] text-danger">{execMessage(errorCode)}</p>
              {errorReason ? (
                <pre className="mt-1 max-h-24 overflow-auto text-[10px] text-danger/80">
                  {errorReason}
                </pre>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
