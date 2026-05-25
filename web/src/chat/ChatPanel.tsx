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
import { CostModal } from "@/chat/CostModal";
import { deriveCostSnapshot, type CostSnapshot as FullCostSnapshot } from "@/chat/cost-snapshot";
import { DiffCard, type GaptEditPayload } from "@/chat/DiffCard";
import { GuardRejectedAlert } from "@/chat/GuardRejectedAlert";
import { ToolCallCard } from "@/chat/ToolCallCard";
import { pairToolEvents, type ToolPair } from "@/chat/tool-pair";
import { type SessionStreamEvent, useSessionStream } from "@/chat/useSessionStream";

type ChatMode = "plan" | "act";

const PLAN_PREFIX = "(Plan mode) Outline the steps without modifying any files:";

interface Props {
  projectId: string;
  workspaceId: string;
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
 * User messages are echoed *locally* (synthetic `user` events with
 * negative seqs) so the chat history doesn't go blank between
 * "send" and "first server event". The negative-seq convention keeps
 * them out of the server's seq space (always positive) — replay /
 * reconnect won't duplicate them. */
export function ChatPanel({ projectId, workspaceId }: Props) {
  const { t } = useI18n();
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<ChatMode>("act");
  const [showCostModal, setShowCostModal] = useState(false);
  const [guardSeq, setGuardSeq] = useState<number | null>(null);
  // Synthetic user-message events kept client-side. Negative seqs so
  // they sort before any server event of the same wall-clock moment.
  const [userEvents, setUserEvents] = useState<SessionStreamEvent[]>([]);
  const userSeqRef = useRef(-1);
  const dismissedGuardSeq = useRef<number | null>(null);
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

  // Merge local user echoes with server events. Stable sort by seq —
  // user events live in the negative space (-1, -2, ...) and server
  // events in the positive (1, 2, ...). To get chronological order
  // we use the `ts` field as the secondary key.
  const allEvents = useMemo<SessionStreamEvent[]>(() => {
    return [...userEvents, ...stream.events].sort((a, b) => a.ts.localeCompare(b.ts));
  }, [userEvents, stream.events]);

  // Auto-scroll on new events.
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [allEvents.length]);

  // Is the assistant currently producing output?  True after a user
  // turn until a `done` event (or `error`) lands. Used to render the
  // "typing…" indicator under the message list.
  const isThinking = useMemo(() => {
    if (userEvents.length === 0) return false;
    const lastUser = userEvents[userEvents.length - 1]!;
    // Any terminal server event with a timestamp newer than the last
    // user message ends the "thinking" state.
    for (let i = stream.events.length - 1; i >= 0; i -= 1) {
      const ev = stream.events[i]!;
      if (ev.kind !== "done" && ev.kind !== "error") continue;
      if (ev.ts > lastUser.ts) return false;
      break;
    }
    return true;
  }, [stream.events, userEvents]);

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
      const display = outgoing;  // what we show locally — *before* prepending PLAN_PREFIX
      if (activeMode === "plan" && outgoing.length > 0) {
        outgoing = `${PLAN_PREFIX}\n\n${outgoing}`;
      }
      // Pure mode-switch commands with no payload — don't fire an
      // empty invoke.
      if (outgoing.length === 0) {
        setPending(false);
        return;
      }
      // Echo the user's message into the local list *before* the POST
      // returns so the bubble appears immediately, even on a slow link.
      const seq = userSeqRef.current;
      userSeqRef.current -= 1;
      setUserEvents((prev) => [
        ...prev,
        {
          seq,
          kind: "text" as SessionEventKind,
          data: { text: display, role: "user" },
          ts: new Date().toISOString(),
        },
      ]);
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

  // Reset the local user echoes when the session changes so a fresh
  // chat doesn't inherit the previous session's bubbles. Also clear
  // them when the user archives mid-conversation.
  useEffect(() => {
    setUserEvents([]);
    userSeqRef.current = -1;
  }, [session?.id]);

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

  const cost = useMemo<FullCostSnapshot>(() => deriveCostSnapshot(stream.events), [stream.events]);

  // Surface `exec.stage.guard_rejected` errors as a modal alert. We
  // track which seq fired so the modal doesn't re-pop if the user
  // dismissed it and another event arrives.
  useEffect(() => {
    for (let i = stream.events.length - 1; i >= 0; i -= 1) {
      const ev = stream.events[i];
      if (ev?.kind !== "error") continue;
      const code = typeof ev.data["exec_code"] === "string" ? ev.data["exec_code"] : "";
      if (code === "exec.stage.guard_rejected" && dismissedGuardSeq.current !== ev.seq) {
        setGuardSeq(ev.seq);
      }
      break; // only care about the most recent error
    }
  }, [stream.events]);

  const guardEvent = useMemo(
    () => (guardSeq != null ? (stream.events.find((e) => e.seq === guardSeq) ?? null) : null),
    [guardSeq, stream.events],
  );

  return (
    <div data-panel-kind="chat" className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-border bg-bg-elevated px-3 py-2">
        <span className="truncate text-[12px] font-semibold text-fg">
          {session ? session.env_manifest_id : t("chat.empty").split(".")[0]}
        </span>
        {session ? (
          <>
            <div
              role="group"
              aria-label="chat mode"
              className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
            >
              <button
                type="button"
                aria-pressed={mode === "plan"}
                onClick={() => setMode("plan")}
                className={
                  mode === "plan"
                    ? "rounded bg-bg px-2 py-0.5 text-[11px] font-medium text-fg shadow-sm"
                    : "rounded px-2 py-0.5 text-[11px] font-medium text-fg-muted hover:text-fg"
                }
              >
                {t("chat.mode.plan")}
              </button>
              <button
                type="button"
                aria-pressed={mode === "act"}
                onClick={() => setMode("act")}
                className={
                  mode === "act"
                    ? "rounded bg-bg px-2 py-0.5 text-[11px] font-medium text-fg shadow-sm"
                    : "rounded px-2 py-0.5 text-[11px] font-medium text-fg-muted hover:text-fg"
                }
              >
                {t("chat.mode.act")}
              </button>
            </div>
            <button
              type="button"
              data-testid="chat-cost"
              onClick={() => setShowCostModal(true)}
              aria-haspopup="dialog"
              aria-label={t("cost.open")}
              className="ml-auto inline-flex items-center gap-2 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[11px] font-mono tabular-nums text-fg-muted hover:bg-surface-hover hover:text-fg"
            >
              <span className="text-accent">${cost.cost_usd.toFixed(4)}</span>
              <span>·</span>
              <span>↑{cost.input_tokens}</span>
              <span>↓{cost.output_tokens}</span>
            </button>
          </>
        ) : null}
      </header>
      {session && mode === "plan" ? (
        <p className="border-b border-border bg-accent/5 px-3 py-1.5 text-[11px] text-accent">
          {t("chat.mode.plan_hint")}
        </p>
      ) : null}

      {!session ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-12 text-center">
          <p className="text-[13px] text-fg-muted">{t("chat.empty")}</p>
          <button
            type="button"
            onClick={start}
            disabled={pending}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-accent px-4 text-[13px] font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50"
          >
            {t("chat.start")}
          </button>
          {error ? (
            <p
              role="alert"
              className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
            >
              {error}
            </p>
          ) : null}
        </div>
      ) : (
        <>
          {stream.status === "connecting" ? (
            <p className="px-3 py-1 text-[11px] text-fg-muted">{t("chat.connecting")}</p>
          ) : null}
          {stream.status === "error" && stream.errorReason ? (
            <p
              role="alert"
              className="mx-3 my-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
            >
              {stream.errorReason}
            </p>
          ) : null}

          <div
            ref={scrollRef}
            data-testid="chat-events"
            className="flex-1 space-y-2 overflow-y-auto px-3 py-3"
          >
            {mergeAssistantText(allEvents).map((event) => {
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
                    <div key={`diff-${event.seq}`} data-event-kind="tool_result">
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
            {isThinking ? <TypingIndicator /> : null}
          </div>

          <form onSubmit={onSubmit} className="shrink-0 border-t border-border bg-bg-elevated p-3">
            <textarea
              value={message}
              onChange={(e) => setMessage(e.currentTarget.value)}
              placeholder={t("chat.placeholder")}
              rows={3}
              aria-label={t("chat.placeholder")}
              className="w-full resize-none rounded-md border border-border bg-surface px-2.5 py-2 text-[13px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (message.trim().length === 0) return;
                  setMessage("");
                  send(message.trim());
                }
              }}
            />
            <div className="mt-2 flex items-center justify-between gap-2">
              <p data-testid="chat-shortcut" className="text-[10px] text-fg-subtle">
                {t("chat.shortcut.esc")}
              </p>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={interrupt}
                  className="h-7 rounded-md border border-border bg-surface px-2.5 text-[12px] font-medium text-fg-muted hover:bg-surface-hover hover:text-fg"
                >
                  {t("chat.interrupt")}
                </button>
                <button
                  type="button"
                  onClick={archive}
                  className="h-7 rounded-md border border-border bg-surface px-2.5 text-[12px] font-medium text-fg-muted hover:bg-surface-hover hover:text-fg"
                >
                  {t("chat.archive")}
                </button>
                <button
                  type="submit"
                  disabled={pending || message.trim().length === 0}
                  className="h-7 rounded-md bg-accent px-3 text-[12px] font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50"
                >
                  {t("chat.send")}
                </button>
              </div>
            </div>
          </form>

          {error ? (
            <p
              role="alert"
              className="mx-3 mb-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
            >
              {error}
            </p>
          ) : null}
        </>
      )}

      {showCostModal ? <CostModal snapshot={cost} onClose={() => setShowCostModal(false)} /> : null}

      {guardEvent ? (
        <GuardRejectedAlert
          reason={typeof guardEvent.data["reason"] === "string" ? guardEvent.data["reason"] : null}
          onDismiss={() => {
            dismissedGuardSeq.current = guardEvent.seq;
            setGuardSeq(null);
          }}
        />
      ) : null}
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

function TypingIndicator() {
  return (
    <div
      data-testid="chat-typing"
      className="flex items-center gap-1.5 px-3 py-2 text-[12px] text-fg-muted"
    >
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:120ms]" />
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:240ms]" />
      <span className="ml-1">…</span>
    </div>
  );
}

/** Collapse consecutive assistant `text` chunks into one merged bubble.
 *
 * The executor streams a `text.delta` per chunk (often per few
 * characters); rendering each as its own row makes the assistant's
 * answer look like a vertical column of single words. We group runs
 * with the same `role` (anything other than `"user"` counts as
 * assistant) into one synthetic event whose `data.text` is the
 * concatenation. Non-text events break the run.
 *
 * The merged event keeps the *first* chunk's seq + ts (stable React
 * key + sort position). User echoes (role="user") are never merged
 * into assistant runs. */
function mergeAssistantText(events: SessionStreamEvent[]): SessionStreamEvent[] {
  const out: SessionStreamEvent[] = [];
  let bufferText = "";
  let bufferHead: SessionStreamEvent | null = null;

  function flush() {
    if (bufferHead) {
      out.push({
        ...bufferHead,
        data: { ...bufferHead.data, text: bufferText },
      });
      bufferText = "";
      bufferHead = null;
    }
  }

  for (const ev of events) {
    const isAssistantText =
      ev.kind === "text" && asString(ev.data["role"]) !== "user";
    if (isAssistantText) {
      const chunk = asString(ev.data["text"]) || asString(ev.data["chunk"]);
      if (!bufferHead) bufferHead = ev;
      bufferText += chunk;
      continue;
    }
    flush();
    out.push(ev);
  }
  flush();
  return out;
}

function EventRow({ event, workspaceId }: EventRowProps) {
  const { t } = useI18n();
  const kind: SessionEventKind = event.kind;
  if (kind === "text") {
    // Backend emits `{text: ...}`; legacy stubs / older providers
    // used `{chunk: ...}`. Accept both.
    const text = asString(event.data["text"]) || asString(event.data["chunk"]);
    const isUser = asString(event.data["role"]) === "user";
    if (!text) return null;
    return (
      <div
        data-event-kind="text"
        data-role={isUser ? "user" : "assistant"}
        className={
          isUser
            ? "ml-auto max-w-[85%] rounded-md border border-accent/30 bg-accent/15 px-3 py-2"
            : "mr-auto max-w-[95%] rounded-md border border-border bg-bg-subtle px-3 py-2"
        }
      >
        <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-fg">
          {text}
        </pre>
      </div>
    );
  }
  if (kind === "tool_call") {
    const tool = asString(event.data["tool"]) || asString(event.data["tool_name"]) || "tool";
    return (
      <div
        data-event-kind="tool_call"
        className="rounded-md border border-border bg-bg-elevated px-3 py-1.5 text-[12px] text-fg-muted"
      >
        <strong className="font-mono text-accent">{tool}</strong>
        <span className="ml-2">{t("chat.tool_call").replace("{tool}", "")}</span>
      </div>
    );
  }
  if (kind === "tool_result") {
    const edit = maybeGaptEditPayload(event.data);
    if (edit) {
      return (
        <div data-event-kind="tool_result">
          <DiffCard workspaceId={workspaceId} payload={edit} />
        </div>
      );
    }
    return (
      <div
        data-event-kind="tool_result"
        className="rounded-md border border-border bg-bg-elevated px-3 py-2"
      >
        <strong className="text-[12px] text-fg">{t("chat.tool_result")}</strong>
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-bg-subtle p-2 text-[11px] text-fg-muted">
          {JSON.stringify(event.data, null, 2)}
        </pre>
      </div>
    );
  }
  if (kind === "error") {
    const code = asString(event.data["exec_code"]);
    const reason = asString(event.data["reason"]) || asString(event.data["message"]);
    // Suppress empty errors — a frame with no useful payload is noise.
    // The `Stream interrupted` banner (driven by useSessionStream's
    // status) is the right signal for transport-layer trouble.
    if (!code && !reason) return null;
    return (
      <div
        role="alert"
        data-event-kind="error"
        className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
      >
        {code ? <strong className="font-mono">{code}</strong> : null}
        {reason ? <span className={code ? "ml-2" : ""}>{reason}</span> : null}
      </div>
    );
  }
  // DONE events are noise in the chat panel — the TypingIndicator
  // disappearance and the cost-header update already convey "the
  // assistant is finished." Suppress the centred "완료." row.
  if (kind === "done") return null;
  return null;
}
