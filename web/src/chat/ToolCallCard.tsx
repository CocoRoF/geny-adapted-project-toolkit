import { useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, XCircle } from "lucide-react";

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

  const status = pair.error ? "error" : pair.result ? "ok" : "running";
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
      className={
        status === "error"
          ? "rounded-md border border-danger/40 bg-danger/5"
          : status === "ok"
            ? "rounded-md border border-border bg-bg-elevated"
            : "rounded-md border border-accent/40 bg-accent/5"
      }
    >
      <header className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          aria-expanded={open}
          aria-label={open ? t("chat.tool.collapse") : t("chat.tool.expand")}
          onClick={() => setOpen((v) => !v)}
          className="grid h-5 w-5 place-items-center rounded text-fg-muted hover:bg-surface-hover hover:text-fg"
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </button>
        <strong className="font-mono text-[12px] text-accent">{tool}</strong>
        {statusBadge}
        {argsSummary ? (
          <code className="ml-auto truncate text-[11px] text-fg-muted">{argsSummary}</code>
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
