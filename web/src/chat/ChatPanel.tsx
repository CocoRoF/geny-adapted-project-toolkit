import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type SessionEventKind,
  type SessionResponse,
  archiveSession,
  createSession,
  interruptSession,
  invokeSession,
  listSessions,
} from "@/api/sessions";
import { useI18n } from "@/app/providers/i18n-context";
import { DiffCard, type GaptEditPayload } from "@/chat/DiffCard";
import { ToolCallCard } from "@/chat/ToolCallCard";
import { pairToolEvents, type ToolPair } from "@/chat/tool-pair";
import { type SessionStreamEvent, useSessionStream } from "@/chat/useSessionStream";

type ChatMode = "plan" | "act";

const PLAN_PREFIX = "(Plan mode) Outline the steps without modifying any files:";

interface Props {
  projectId: string;
  workspaceId: string;
}

interface CostSnapshot {
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
}

/** Live chat panel.
 *
 * Lifecycle:
 *   1. mount → list existing sessions for the workspace; reuse the
 *      latest active one if present, otherwise sit at "no session".
 *   2. user clicks "Start session" → POST /api/projects/:pid/sessions
 *      → stream subscribes via `useSessionStream`.
 *   3. user types + submits → POST /:sid/invoke → background task on
 *      the server publishes events the stream relays.
 *   4. "Interrupt" cancels the running invoke; "End session" archives
 *      it server-side and resets the panel.
 *
 * Cycles 3.9 (Plan/Act) / 3.10 (cost panel) wire deeper UI on top —
 * this cycle ships the bone-stock chat loop. */
export function ChatPanel({ projectId, workspaceId }: Props) {
  const { t } = useI18n();
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<ChatMode>("act");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Pull existing sessions on mount so a reload doesn't strand the
  // user with a fresh "no session" panel.
  useEffect(() => {
    void listSessions(projectId)
      .then((rows) => {
        const wsActive = rows.find((s) => s.workspace_id === workspaceId && s.status === "active");
        if (wsActive) setSession(wsActive);
      })
      .catch(() => {
        // Silently swallow — the parent shows project-level errors.
      });
  }, [projectId, workspaceId]);

  const stream = useSessionStream(session?.id ?? null);

  // Auto-scroll on new events.
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [stream.events.length]);

  const start = useCallback(() => {
    setError(null);
    setPending(true);
    void createSession(projectId, { workspace_id: workspaceId })
      .then(setSession)
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      })
      .finally(() => setPending(false));
  }, [projectId, workspaceId]);

  const send = useCallback(
    (text: string) => {
      if (!session) return;
      setError(null);
      setPending(true);
      // Slash commands let the user flip modes mid-conversation
      // without reaching for the toggle.
      let outgoing = text;
      let nextMode: ChatMode | null = null;
      if (outgoing.startsWith("/plan")) {
        nextMode = "plan";
        outgoing = outgoing.slice("/plan".length).trim();
      } else if (outgoing.startsWith("/act")) {
        nextMode = "act";
        outgoing = outgoing.slice("/act".length).trim();
      }
      if (nextMode) setMode(nextMode);
      const activeMode = nextMode ?? mode;
      if (activeMode === "plan" && outgoing.length > 0) {
        outgoing = `${PLAN_PREFIX}\n\n${outgoing}`;
      }
      // Pure mode-switch commands with no payload — don't fire an
      // empty invoke.
      if (outgoing.length === 0) {
        setPending(false);
        return;
      }
      void invokeSession(session.id, outgoing)
        .catch((err: unknown) => {
          setError(
            err instanceof ApiError
              ? `${err.code}: ${err.reason}`
              : err instanceof Error
                ? err.message
                : String(err),
          );
        })
        .finally(() => setPending(false));
    },
    [session, mode],
  );

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || !session) return;
    setMessage("");
    send(trimmed);
  }

  const interrupt = useCallback(() => {
    if (!session) return;
    void interruptSession(session.id).catch((err: unknown) => {
      setError(
        err instanceof ApiError
          ? `${err.code}: ${err.reason}`
          : err instanceof Error
            ? err.message
            : String(err),
      );
    });
  }, [session]);

  const archive = useCallback(() => {
    if (!session) return;
    void archiveSession(session.id)
      .then(() => {
        setSession(null);
        stream.reset();
      })
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      });
  }, [session, stream]);

  // Esc anywhere inside the panel cancels the running invocation —
  // matches Cursor / Aider muscle memory.
  useEffect(() => {
    if (!session) return undefined;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const hasInflight = stream.events.some((ev) => ev.kind === "tool_call");
      if (!hasInflight && !pending) return;
      e.preventDefault();
      interrupt();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [session, stream.events, pending, interrupt]);

  // Tool pairs derived from the live event list — drives the
  // tool-call cards inline. We render them in the position of their
  // *call* event so the chronology stays intact.
  const toolPairs = useMemo<ToolPair[]>(() => pairToolEvents(stream.events), [stream.events]);
  const pairedEventSeqs = useMemo(() => {
    const set = new Set<number>();
    for (const pair of toolPairs) {
      set.add(pair.call.seq);
      if (pair.result) set.add(pair.result.seq);
      if (pair.error) set.add(pair.error.seq);
    }
    return set;
  }, [toolPairs]);

  const cost = useMemo<CostSnapshot>(() => {
    // Pull the most recent `cost` event for the live header.
    for (let i = stream.events.length - 1; i >= 0; i -= 1) {
      const ev = stream.events[i];
      if (ev?.kind === "cost") {
        const data = ev.data as Partial<CostSnapshot>;
        return {
          cost_usd: typeof data.cost_usd === "number" ? data.cost_usd : 0,
          input_tokens: typeof data.input_tokens === "number" ? data.input_tokens : 0,
          output_tokens: typeof data.output_tokens === "number" ? data.output_tokens : 0,
        };
      }
    }
    return { cost_usd: 0, input_tokens: 0, output_tokens: 0 };
  }, [stream.events]);

  return (
    <div className="chat-panel" data-panel-kind="chat">
      <header className="chat-panel-header">
        <span className="chat-panel-title">
          {session ? session.env_manifest_id : t("chat.empty").split(".")[0]}
        </span>
        {session ? (
          <>
            <div className="chat-panel-mode" role="group" aria-label="chat mode">
              <button
                type="button"
                aria-pressed={mode === "plan"}
                onClick={() => setMode("plan")}
                className={mode === "plan" ? "is-active" : undefined}
              >
                {t("chat.mode.plan")}
              </button>
              <button
                type="button"
                aria-pressed={mode === "act"}
                onClick={() => setMode("act")}
                className={mode === "act" ? "is-active" : undefined}
              >
                {t("chat.mode.act")}
              </button>
            </div>
            <span className="chat-panel-cost" data-testid="chat-cost">
              {t("chat.cost.live")} ·{" "}
              {t("chat.cost.usd").replace("{amount}", cost.cost_usd.toFixed(4))} ·{" "}
              {t("chat.cost.tokens")
                .replace("{input}", String(cost.input_tokens))
                .replace("{output}", String(cost.output_tokens))}
            </span>
          </>
        ) : null}
      </header>
      {session && mode === "plan" ? (
        <p className="chat-panel-mode-hint">{t("chat.mode.plan_hint")}</p>
      ) : null}
      {session ? (
        <p className="chat-panel-shortcut" data-testid="chat-shortcut">
          {t("chat.shortcut.esc")}
        </p>
      ) : null}

      {!session ? (
        <div className="chat-panel-empty">
          <p>{t("chat.empty")}</p>
          <button type="button" onClick={start} disabled={pending}>
            {t("chat.start")}
          </button>
          {error ? (
            <p role="alert" className="chat-panel-error">
              {error}
            </p>
          ) : null}
        </div>
      ) : (
        <>
          {stream.status === "connecting" ? (
            <p className="chat-panel-status">{t("chat.connecting")}</p>
          ) : null}
          {stream.status === "error" && stream.errorReason ? (
            <p role="alert" className="chat-panel-status chat-panel-status--error">
              {stream.errorReason}
            </p>
          ) : null}

          <div className="chat-panel-events" ref={scrollRef} data-testid="chat-events">
            {stream.events.map((event) => {
              // The call event renders as a ToolCallCard (with its
              // matched outcome folded in). Result/error events that
              // belong to a paired call are suppressed — they live
              // inside the card. gapt_edit's tool_result still gets
              // a DiffCard *in addition* because the diff
              // visualisation is more useful than a JSON dump; the
              // tool card itself shows the call shell.
              if (event.kind === "tool_call") {
                const pair = toolPairs.find((p) => p.call.seq === event.seq);
                if (pair) return <ToolCallCard key={`pair-${event.seq}`} pair={pair} />;
              }
              if (event.kind === "tool_result") {
                const edit = maybeGaptEditPayload(event.data);
                if (edit) {
                  return (
                    <div
                      key={`diff-${event.seq}`}
                      className="chat-event chat-event--tool_result"
                      data-event-kind="tool_result"
                    >
                      <DiffCard workspaceId={workspaceId} payload={edit} />
                    </div>
                  );
                }
                if (pairedEventSeqs.has(event.seq)) return null;
              }
              if (event.kind === "error" && pairedEventSeqs.has(event.seq)) {
                return null;
              }
              return <EventRow key={event.seq} event={event} workspaceId={workspaceId} />;
            })}
          </div>

          <form className="chat-panel-form" onSubmit={onSubmit}>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.currentTarget.value)}
              placeholder={t("chat.placeholder")}
              rows={3}
              aria-label={t("chat.placeholder")}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (message.trim().length === 0) return;
                  setMessage("");
                  send(message.trim());
                }
              }}
            />
            <div className="chat-panel-actions">
              <button type="submit" disabled={pending || message.trim().length === 0}>
                {t("chat.send")}
              </button>
              <button type="button" onClick={interrupt}>
                {t("chat.interrupt")}
              </button>
              <button type="button" onClick={archive}>
                {t("chat.archive")}
              </button>
            </div>
          </form>

          {error ? (
            <p role="alert" className="chat-panel-error">
              {error}
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

interface EventRowProps {
  event: SessionStreamEvent;
  workspaceId: string;
}

function maybeGaptEditPayload(data: Record<string, unknown>): GaptEditPayload | null {
  // The runtime's `gapt_edit` echoes `path`, `old`, `new` into the
  // tool result metadata (runtime/src/gapt_runtime/tools/edit.py).
  // Any other tool_result is left to the generic JSON renderer.
  const tool = asString(data["tool"]) || asString(data["tool_name"]);
  if (tool !== "gapt_edit") return null;
  const meta = data["metadata"];
  if (!meta || typeof meta !== "object") return null;
  const m = meta as Record<string, unknown>;
  if (
    typeof m["path"] !== "string" ||
    typeof m["old"] !== "string" ||
    typeof m["new"] !== "string"
  ) {
    return null;
  }
  return {
    path: m["path"],
    old: m["old"],
    new: m["new"],
    ...(typeof m["replaced"] === "number" ? { replaced: m["replaced"] } : {}),
    ...(typeof m["all"] === "boolean" ? { all: m["all"] } : {}),
  };
}

function EventRow({ event, workspaceId }: EventRowProps) {
  const { t } = useI18n();
  const kind: SessionEventKind = event.kind;
  if (kind === "text") {
    const chunk = asString(event.data["chunk"]);
    return (
      <div className="chat-event chat-event--text" data-event-kind="text">
        <pre>{chunk}</pre>
      </div>
    );
  }
  if (kind === "tool_call") {
    const tool = asString(event.data["tool"]) || asString(event.data["tool_name"]) || "tool";
    return (
      <div className="chat-event chat-event--tool_call" data-event-kind="tool_call">
        <strong>{t("chat.tool_call").replace("{tool}", tool)}</strong>
      </div>
    );
  }
  if (kind === "tool_result") {
    const edit = maybeGaptEditPayload(event.data);
    if (edit) {
      return (
        <div className="chat-event chat-event--tool_result" data-event-kind="tool_result">
          <DiffCard workspaceId={workspaceId} payload={edit} />
        </div>
      );
    }
    return (
      <div className="chat-event chat-event--tool_result" data-event-kind="tool_result">
        <strong>{t("chat.tool_result")}</strong>
        <pre>{JSON.stringify(event.data, null, 2)}</pre>
      </div>
    );
  }
  if (kind === "error") {
    const code = asString(event.data["exec_code"], "error");
    const reason = asString(event.data["reason"]);
    return (
      <div role="alert" className="chat-event chat-event--error" data-event-kind="error">
        <strong>{code}</strong>
        {reason ? <span> {reason}</span> : null}
      </div>
    );
  }
  if (kind === "done") {
    return (
      <div className="chat-event chat-event--done" data-event-kind="done">
        {t("chat.done")}
      </div>
    );
  }
  // cost rows are folded into the header; skip in the list.
  return null;
}
