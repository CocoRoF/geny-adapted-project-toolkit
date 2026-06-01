/**
 * Phase J — read-only viewer for one archived session.
 *
 * Reached from `SessionsHistory` cards. Fetches the JSON-format
 * transcript (groupable, not the raw event stream) and renders it
 * turn-by-turn — user bubble, assistant text, tool calls + outputs,
 * per-turn cost — all in the same chrome the live ChatPanel uses
 * so the operator sees one consistent layout whether they're chatting
 * now or browsing history from 3 weeks ago.
 *
 * What we DON'T do here (deliberate):
 *   - Re-render markdown into formatted text. Assistant responses
 *     stay as `<pre>` plain text — adding `react-markdown` for the
 *     1% of sessions with code blocks is a separate PR.
 *   - Re-attach the live SSE stream. This page is purely DB-backed.
 *     "Resume in workspace" navigates back to the IDE, which is what
 *     the live attach happens through.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ChevronLeft,
  Code2,
  Download,
  ExternalLink,
  Loader2,
  Wrench,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type SessionResponse,
  type SessionTranscript,
  type TranscriptTurn,
  getSession,
  getSessionTranscript,
} from "@/api/sessions";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { Card, CardContent } from "@/ui/Card";
import { cn } from "@/ui/cn";
import { MarkdownText } from "@/ui/MarkdownText";

export function SessionDetail() {
  const { pid, sid } = useParams<{ pid: string; sid: string }>();
  const projectId = pid ?? "";
  const sessionId = sid ?? "";
  const [meta, setMeta] = useState<SessionResponse | null>(null);
  const [transcript, setTranscript] = useState<SessionTranscript | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    setErr(null);
    Promise.all([getSession(sessionId), getSessionTranscript(sessionId)])
      .then(([m, t]) => {
        setMeta(m);
        setTranscript(t);
      })
      .catch((e: unknown) => {
        setErr(
          e instanceof ApiError
            ? e.reason
            : e instanceof Error
              ? e.message
              : String(e),
        );
      })
      .finally(() => setLoading(false));
  }, [sessionId]);

  const downloadMarkdown = useCallback(async () => {
    const resp = await fetch(
      `/_gapt/api/sessions/${sessionId}/transcript?format=markdown`,
      { credentials: "include" },
    );
    if (!resp.ok) {
      console.error("transcript download failed", resp.status);
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `session-${sessionId}-transcript.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [sessionId]);

  if (!projectId || !sessionId) return null;

  return (
    <div className="mx-auto max-w-[900px] px-6 py-8">
      <Link
        to={`/projects/${projectId}/sessions`}
        className="mb-3 inline-flex items-center gap-1 text-[12px] text-fg-muted hover:text-fg"
      >
        <ChevronLeft className="h-3.5 w-3.5" /> 세션 목록으로
      </Link>

      {err ? (
        <Card className="mb-4 border-danger/40">
          <CardContent className="p-3 text-[12px] text-danger">{err}</CardContent>
        </Card>
      ) : null}

      {loading ? (
        <Card>
          <CardContent className="flex items-center gap-2 p-4 text-[12px] text-fg-subtle">
            <Loader2 className="h-3 w-3 animate-spin" /> 불러오는 중…
          </CardContent>
        </Card>
      ) : meta && transcript ? (
        <>
          <header className="mb-5 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Badge tone={meta.status === "active" ? "success" : "neutral"}>
                  {meta.status}
                </Badge>
                <span className="font-mono text-[11px] text-fg-subtle">
                  {meta.env_manifest_id}
                </span>
              </div>
              <h1 className="mt-1.5 truncate font-mono text-[13px] text-fg">
                {meta.id}
              </h1>
              <p className="mt-0.5 text-[11px] text-fg-subtle">
                생성: {new Date(meta.created_at).toLocaleString()} · 마지막 활동:{" "}
                {new Date(meta.last_active_at).toLocaleString()}
              </p>
              <p className="mt-1 text-[12px] text-fg-muted">
                <span className="font-mono text-accent">
                  ${transcript.total_cost_usd.toFixed(4)}
                </span>{" "}
                · ↑{transcript.total_input_tokens.toLocaleString()} ↓
                {transcript.total_output_tokens.toLocaleString()}
                {/* Phase K.2 — cache tokens make the "6 tokens but
                    $0.013" mystery readable: the cost includes a
                    cache_write of N thousand tokens. */}
                {transcript.total_cache_write_tokens ? (
                  <>
                    {" "}⊕{transcript.total_cache_write_tokens.toLocaleString()}{" "}
                    <span className="text-[10.5px] text-fg-subtle">
                      cache_write
                    </span>
                  </>
                ) : null}
                {transcript.total_cache_read_tokens ? (
                  <>
                    {" "}⊖{transcript.total_cache_read_tokens.toLocaleString()}{" "}
                    <span className="text-[10.5px] text-fg-subtle">
                      cache_read
                    </span>
                  </>
                ) : null}
                {" "}· {transcript.turns.length} turn
                {transcript.turns.length === 1 ? "" : "s"}
              </p>
            </div>
            <div className="flex shrink-0 flex-col gap-1.5">
              <Button
                variant="ghost"
                onClick={() => void downloadMarkdown()}
                size="sm"
              >
                <Download className="mr-1 h-3 w-3" /> .md
              </Button>
              <Link to={`/projects/${projectId}/w/${meta.workspace_id}`}>
                <Button variant="secondary" size="sm" className="w-full">
                  <ExternalLink className="mr-1 h-3 w-3" /> 워크스페이스 열기
                </Button>
              </Link>
            </div>
          </header>

          {transcript.turns.length === 0 ? (
            <Card>
              <CardContent className="p-6 text-center text-[12.5px] text-fg-muted">
                기록된 turn 이 없습니다.
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {transcript.turns.map((turn, i) => (
                <TurnView key={i} index={i + 1} turn={turn} />
              ))}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

function TurnView({ index, turn }: { index: number; turn: TranscriptTurn }) {
  return (
    <section className="rounded-md border border-border bg-bg-elevated p-3">
      <header className="mb-2 flex items-baseline justify-between">
        <h2 className="text-[12px] font-semibold text-fg">Turn {index}</h2>
        <div className="flex items-center gap-2 text-[10.5px] text-fg-subtle">
          {turn.started_at ? (
            <time className="tabular-nums">
              {new Date(turn.started_at).toLocaleString()}
            </time>
          ) : null}
          {turn.cost_usd ? (
            <span className="font-mono text-accent">
              ${turn.cost_usd.toFixed(6)}
            </span>
          ) : null}
        </div>
      </header>

      {turn.user ? (
        <div className="mb-2 ml-auto max-w-[85%] rounded-md border border-accent/30 bg-accent/10 px-3 py-2">
          <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-fg">
            {turn.user}
          </pre>
        </div>
      ) : (
        <p className="mb-2 text-[11px] italic text-fg-subtle">
          (no recorded prompt — pre-Phase-I session)
        </p>
      )}

      {turn.assistant ? (
        <div className="mr-auto max-w-[95%] rounded-md border border-border bg-bg-subtle px-3 py-2">
          {/* Phase K.1 — markdown render to mirror what the live
              ChatPanel now does, so an archived viewing reads the
              same as the original conversation. */}
          <MarkdownText>{turn.assistant}</MarkdownText>
        </div>
      ) : null}

      {turn.tool_uses.length > 0 ? (
        <div className="mt-3 space-y-2">
          {turn.tool_uses.map((tu, j) => (
            <ToolUseCard key={j} tool={tu} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ToolUseCard({
  tool,
}: {
  tool: TranscriptTurn["tool_uses"][number];
}) {
  const inputText = tool.input ? JSON.stringify(tool.input, null, 2) : "";
  const outputText =
    typeof tool.output === "string"
      ? tool.output
      : tool.output != null
        ? JSON.stringify(tool.output, null, 2)
        : "";
  // Long outputs collapse to scroll; very long ones truncate so the
  // browser doesn't render 100KB into the DOM in one shot.
  const truncatedOutput =
    outputText.length > 8000
      ? outputText.slice(0, 8000) + "\n…(truncated)"
      : outputText;
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2",
        tool.is_error
          ? "border-danger/40 bg-danger/5"
          : "border-border bg-bg-subtle/40",
      )}
    >
      <header className="mb-1 flex items-center gap-1.5">
        <Wrench className="h-3 w-3 text-fg-subtle" />
        <code className="font-mono text-[11.5px] font-semibold text-accent">
          {tool.tool || "(unknown tool)"}
        </code>
        {tool.is_error ? (
          <Badge tone="danger" className="text-[9.5px]">
            error
          </Badge>
        ) : null}
      </header>
      {inputText ? (
        <details className="mb-1">
          <summary className="cursor-pointer text-[10.5px] text-fg-muted hover:text-fg">
            <Code2 className="mr-1 inline-block h-2.5 w-2.5" />
            input
          </summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded border border-border bg-bg p-2 font-mono text-[10.5px] leading-snug text-fg">
            {inputText}
          </pre>
        </details>
      ) : null}
      {outputText ? (
        <details open={tool.is_error}>
          <summary className="cursor-pointer text-[10.5px] text-fg-muted hover:text-fg">
            output
          </summary>
          <pre className="mt-1 max-h-60 overflow-auto rounded border border-border bg-bg p-2 font-mono text-[10.5px] leading-snug text-fg">
            {truncatedOutput}
          </pre>
        </details>
      ) : null}
    </div>
  );
}
