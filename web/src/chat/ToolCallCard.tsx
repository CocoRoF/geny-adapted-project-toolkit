import { useState } from "react";

import { useI18n } from "@/app/providers/i18n-context";
import type { ToolPair } from "@/chat/tool-pair";

interface Props {
  pair: ToolPair;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function summariseArgs(data: Record<string, unknown>): string {
  // The runtime echoes `args` (Cycle 2.4 tools/call response shape).
  // Older payloads may put them on the top level — accept both.
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

/** Compact tool-call card.
 *
 * Header: tool name + status pill (running / ok / error) + one-line
 * argument summary. Body (expandable): full args + full result JSON
 * or `exec_code` + reason on failure. */
export function ToolCallCard({ pair }: Props) {
  const { t, execMessage } = useI18n();
  const [open, setOpen] = useState(false);

  const tool = asString(pair.call.data["tool"]) || asString(pair.call.data["tool_name"]) || "tool";
  const argsSummary = summariseArgs(pair.call.data);

  let statusKey: "chat.tool.running" | "chat.tool.ok" | "chat.tool.error" = "chat.tool.running";
  let statusClass = "tool-card-status--running";
  if (pair.error) {
    statusKey = "chat.tool.error";
    statusClass = "tool-card-status--error";
  } else if (pair.result) {
    statusKey = "chat.tool.ok";
    statusClass = "tool-card-status--ok";
  }

  const errorCode = pair.error ? asString(pair.error.data["exec_code"], "error") : null;
  const errorReason = pair.error ? asString(pair.error.data["reason"]) : null;

  return (
    <div
      className={`tool-card tool-card--${pair.running ? "running" : pair.error ? "error" : "ok"}`}
      data-testid="tool-card"
      data-tool-name={tool}
    >
      <header className="tool-card-header">
        <strong className="tool-card-tool">{tool}</strong>
        <span className={`tool-card-status ${statusClass}`}>{t(statusKey)}</span>
        {argsSummary ? <code className="tool-card-args-summary">{argsSummary}</code> : null}
        <button
          type="button"
          className="tool-card-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? t("chat.tool.collapse") : t("chat.tool.expand")}
        </button>
      </header>

      {open ? (
        <div className="tool-card-body">
          <h4>{t("chat.tool.args")}</h4>
          <pre className="tool-card-args">{JSON.stringify(pair.call.data, null, 2)}</pre>
          {pair.result ? (
            <>
              <h4>{t("chat.tool_result")}</h4>
              <pre className="tool-card-result">{JSON.stringify(pair.result.data, null, 2)}</pre>
            </>
          ) : null}
          {pair.error && errorCode ? (
            <div role="alert" className="tool-card-error">
              <strong>{errorCode}</strong>
              <p>{execMessage(errorCode)}</p>
              {errorReason ? <pre>{errorReason}</pre> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
