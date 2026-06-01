import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, ChevronDown, Download } from "lucide-react";

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
import { type ManifestSummary, listManifests } from "@/api/manifests";
import { DiffCard, type GaptEditPayload } from "@/chat/DiffCard";
import { annotateEditGroups } from "@/chat/diff-group";
import { GuardRejectedAlert } from "@/chat/GuardRejectedAlert";
import { ToolCallCard } from "@/chat/ToolCallCard";
import { pairToolEvents, type ToolPair } from "@/chat/tool-pair";
import { TraceStrip } from "@/chat/TraceStrip";
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
const MANIFEST_STORAGE_KEY = "gapt.chat.manifest_id";

function readPersistedManifestId(projectId: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(`${MANIFEST_STORAGE_KEY}.${projectId}`);
  } catch {
    return null;
  }
}

function persistManifestId(projectId: string, id: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(`${MANIFEST_STORAGE_KEY}.${projectId}`, id);
  } catch {
    /* private mode / quota — best-effort */
  }
}

const MODEL_STORAGE_KEY = "gapt.chat.model_override";

function readPersistedModel(projectId: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(`${MODEL_STORAGE_KEY}.${projectId}`);
  } catch {
    return null;
  }
}

function persistModel(projectId: string, model: string | null): void {
  if (typeof window === "undefined") return;
  try {
    const key = `${MODEL_STORAGE_KEY}.${projectId}`;
    if (model) window.localStorage.setItem(key, model);
    else window.localStorage.removeItem(key);
  } catch {
    /* best-effort */
  }
}

/** Phase I.4 — fetch markdown transcript from the session and drop
 *  it into a browser download. We trigger the click on a transient
 *  `<a>` so the file's blob URL is released as soon as the download
 *  starts; no global state, no UI side-effects beyond the download. */
async function downloadTranscriptMarkdown(sessionId: string): Promise<void> {
  const resp = await fetch(
    `/_gapt/api/sessions/${sessionId}/transcript?format=markdown`,
    { credentials: "include" },
  );
  if (!resp.ok) {
    // Surface the failure in dev console — operator can re-try from
    // the UI button. A toast would be nicer but is overkill for the
    // one error path that exists today (server 5xx or 403).
    console.error(
      "transcript download failed",
      resp.status,
      await resp.text().catch(() => ""),
    );
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
}

/** Phase G.4 — common model identifiers the pill offers. Bundled
 *  manifests use bare names (sonnet/opus/haiku) which `geny-executor`
 *  routes to the active provider's canonical model id. Operator can
 *  still type a custom value. */
const MODEL_PRESETS: { value: string; label: string }[] = [
  { value: "haiku", label: "haiku (fastest)" },
  { value: "sonnet", label: "sonnet (balanced)" },
  { value: "opus", label: "opus (deepest)" },
];

export function ChatPanel({ projectId, workspaceId }: Props) {
  const { t } = useI18n();
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<ChatMode>("act");
  const [showCostModal, setShowCostModal] = useState(false);
  const [guardSeq, setGuardSeq] = useState<number | null>(null);
  // Phase G.3 — manifest picker. List comes from `/manifests`,
  // selection is sticky per-project (localStorage). Falls back to
  // the server's `default_manifest_id` when no localStorage value.
  const [manifests, setManifests] = useState<ManifestSummary[]>([]);
  const [manifestId, setManifestId] = useState<string | null>(() =>
    readPersistedManifestId(projectId),
  );
  const [manifestMenuOpen, setManifestMenuOpen] = useState(false);
  // Phase G.4 — per-session model override. `null` = inherit
  // (manifest's bundled default + global admin prefs). Sticky per
  // project so the operator doesn't have to reset on every new
  // session.
  const [modelOverride, setModelOverride] = useState<string | null>(() =>
    readPersistedModel(projectId),
  );
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
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

  // Phase G.3 — fetch manifest list when the panel mounts.
  // Workspace_id is passed so workspace-local overrides surface.
  useEffect(() => {
    void listManifests(workspaceId)
      .then((resp) => {
        setManifests(resp.manifests);
        // Initialize selection: localStorage wins, otherwise the
        // server-side default. Only sets if state was still null.
        setManifestId((cur) => cur ?? resp.default_manifest_id);
      })
      .catch(() => {
        /* picker just hides — chat still works on server default */
      });
  }, [workspaceId]);

  const onPickManifest = useCallback(
    (id: string) => {
      setManifestId(id);
      persistManifestId(projectId, id);
      setManifestMenuOpen(false);
    },
    [projectId],
  );

  const selectedManifest = useMemo<ManifestSummary | null>(
    () => manifests.find((m) => m.id === manifestId) ?? null,
    [manifestId, manifests],
  );

  const onPickModel = useCallback(
    (value: string | null) => {
      setModelOverride(value);
      persistModel(projectId, value);
      setModelMenuOpen(false);
    },
    [projectId],
  );

  const stream = useSessionStream(session?.id ?? null);

  // Merge local user echoes with server events. Stable sort by seq —
  // user events live in the negative space (-1, -2, ...) and server
  // events in the positive (1, 2, ...). To get chronological order
  // we use the `ts` field as the secondary key.
  const allEvents = useMemo<SessionStreamEvent[]>(() => {
    // Phase I.2 — the backend now publishes a `user_message` event of
    // its own at the top of each turn. The optimistic bubble we add
    // here (kind="text", role="user", negative seq) covers the slow-
    // network case before the SSE round-trip. To avoid showing the
    // same prompt twice, drop the optimistic echo when its text
    // matches a real backend `user_message`.
    const backendUserTexts = new Set(
      stream.events
        .filter((ev) => ev.kind === "user_message")
        .map((ev) => asString(ev.data["text"])),
    );
    const filteredUser =
      backendUserTexts.size === 0
        ? userEvents
        : userEvents.filter(
            (ev) => !backendUserTexts.has(asString(ev.data["text"])),
          );
    return [...filteredUser, ...stream.events].sort((a, b) =>
      a.ts.localeCompare(b.ts),
    );
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
    // Phase G.3+G.4 — pass the selected manifest id + the optional
    // per-session model override. Each missing field falls through
    // to the global Settings → Pipeline overrides on the server.
    void createSession(projectId, {
      workspace_id: workspaceId,
      ...(manifestId ? { env_id: manifestId } : {}),
      ...(modelOverride ? { model: modelOverride } : {}),
    })
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
  }, [manifestId, modelOverride, projectId, workspaceId]);

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
          kind: "text",
          data: { text: display, role: "user" },
          ts: new Date().toISOString(),
        },
      ]);
      // Phase D.1 — pass the active mode along so the backend policy
      // hook short-circuits mutating tools when mode is "plan". The
      // existing PLAN_PREFIX text prompt is *also* kept so the LLM
      // produces planning-style output, not just so the gate triggers.
      void invokeSession(session.id, outgoing, activeMode)
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

  // Phase D.2 — pre-compute per-event group markers so the render
  // loop can wrap a run of consecutive same-file `gapt_edit` cards
  // under a single header. Keyed by event.seq.
  const editGroupMarkers = useMemo(
    () => annotateEditGroups(stream.events, maybeGaptEditPayload),
    [stream.events],
  );

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
        {/* Phase G.3 — manifest picker. While a session is active the
            picker is read-only (the session's pipeline is already
            committed); before "Start session" the dropdown lets the
            operator pick a different manifest. */}
        <ManifestPill
          session={session}
          manifests={manifests}
          selectedId={manifestId}
          selected={selectedManifest}
          open={manifestMenuOpen}
          onToggle={() => setManifestMenuOpen((v) => !v)}
          onPick={onPickManifest}
        />
        <ModelPill
          locked={session !== null}
          selected={modelOverride}
          manifestModel={selectedManifest?.model ?? null}
          open={modelMenuOpen}
          onToggle={() => setModelMenuOpen((v) => !v)}
          onPick={onPickModel}
        />
        <span className="sr-only">
          {session ? session.env_manifest_id : ""}
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
                title={t("chat.mode.plan.tooltip")}
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
                title={t("chat.mode.act.tooltip")}
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
            {/* Phase I.4 — markdown transcript download. Reads
                session_events DB-side so a server restart never loses
                history. */}
            <button
              type="button"
              data-testid="chat-transcript-download"
              onClick={() => void downloadTranscriptMarkdown(session.id)}
              aria-label={t("chat.transcript.download")}
              title={t("chat.transcript.download")}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[11px] text-fg-muted hover:bg-surface-hover hover:text-fg"
            >
              <Download className="h-3 w-3" />
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
            {mergeAssistantText(allEvents.filter((e) => e.kind !== "step")).map((event) => {
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
                  // Phase D.2 — wrap a run of consecutive same-file
                  // edits in a single group header. We render the
                  // header on the first edit of a run only; the rest
                  // sit inside the same container so they don't each
                  // get their own "file:" line.
                  const marker = editGroupMarkers.get(event.seq);
                  const isGroupStart = marker !== undefined && marker.groupIndex === 0;
                  const cardKey = `diff-${event.seq}`;
                  const card = (
                    <DiffCard workspaceId={workspaceId} payload={edit} />
                  );
                  if (marker && marker.groupSize > 1) {
                    return (
                      <div key={cardKey} data-event-kind="tool_result">
                        {isGroupStart ? (
                          <div
                            data-testid="diff-group-header"
                            className="mb-1 flex items-center gap-2 px-2 text-[11px] text-fg-muted"
                          >
                            <span
                              aria-hidden
                              className="inline-block h-1.5 w-1.5 rounded-full bg-accent"
                            />
                            <span>
                              {t("diff.group.header")
                                .replace("{count}", String(marker.groupSize))
                                .replace("{path}", marker.path)}
                            </span>
                          </div>
                        ) : null}
                        {card}
                      </div>
                    );
                  }
                  return (
                    <div key={cardKey} data-event-kind="tool_result">
                      {card}
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
            <TraceStrip events={allEvents} active={isThinking} />
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

// ─────────────────────────────────────── manifest pill ──

/** Phase G.3 — header pill that doubles as a manifest picker.
 *
 *  - With an active session: read-only — the session's pipeline is
 *    already committed. Shows `env_manifest_id` so the operator
 *    can see what they're running, but disables the dropdown.
 *  - Without an active session: dropdown of bundled + workspace
 *    manifests. Selection is sticky per-project (localStorage)
 *    and gets passed as `env_id` to the next `createSession`. */
function ManifestPill({
  session,
  manifests,
  selectedId,
  selected,
  open,
  onToggle,
  onPick,
}: {
  session: SessionResponse | null;
  manifests: ManifestSummary[];
  selectedId: string | null;
  selected: ManifestSummary | null;
  open: boolean;
  onToggle: () => void;
  onPick: (id: string) => void;
}) {
  const label = session
    ? session.env_manifest_id
    : (selected?.display_name ?? selectedId ?? "gapt_default");
  const locked = session !== null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        disabled={locked || manifests.length === 0}
        className={
          locked
            ? "inline-flex items-center gap-1 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[12px] font-semibold text-fg-muted"
            : "inline-flex items-center gap-1 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[12px] font-semibold text-fg hover:bg-bg"
        }
        title={
          locked
            ? "Session active — manifest locked. End session to switch."
            : "Pick a manifest for the next session"
        }
      >
        <Bot className="h-3 w-3" strokeWidth={1.5} />
        <span className="truncate font-mono">{label}</span>
        {!locked ? <ChevronDown className="h-3 w-3 opacity-60" /> : null}
      </button>
      {open && !locked ? (
        <ul
          role="menu"
          className="absolute left-0 top-full z-20 mt-1 max-h-72 w-72 overflow-auto rounded-md border border-border bg-bg-elevated py-1 shadow-lg"
        >
          {manifests.map((m) => {
            const active = m.id === selectedId;
            return (
              <li key={m.id}>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => onPick(m.id)}
                  className={
                    active
                      ? "flex w-full flex-col items-start gap-0.5 bg-accent/10 px-3 py-1.5 text-left"
                      : "flex w-full flex-col items-start gap-0.5 px-3 py-1.5 text-left hover:bg-bg-subtle"
                  }
                >
                  <span className="flex w-full items-center gap-1.5">
                    <span className="flex-1 truncate font-mono text-[12.5px] text-fg">
                      {m.id}
                    </span>
                    {m.source === "workspace" ? (
                      <span className="rounded bg-accent/15 px-1 text-[9.5px] uppercase tracking-wider text-accent">
                        ws
                      </span>
                    ) : null}
                    {m.provider ? (
                      <span className="rounded bg-bg-subtle px-1 text-[10px] text-fg-subtle">
                        {m.provider}
                      </span>
                    ) : null}
                  </span>
                  {m.description ? (
                    <span className="text-[11px] text-fg-muted">
                      {m.description}
                    </span>
                  ) : null}
                  {m.model ? (
                    <span className="font-mono text-[10.5px] text-fg-subtle">
                      model: {m.model}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
          {manifests.length === 0 ? (
            <li className="px-3 py-2 text-[11px] text-fg-subtle">
              No manifests loaded.
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

/** Phase G.4 — header pill for the per-session model override.
 *
 *  `selected = null` means "inherit" (manifest's bundled default
 *  + global admin prefs win). Selecting a preset overrides ONLY
 *  this and future-new sessions for the same project — the running
 *  session keeps whatever it was created with.
 *
 *  When a session is active the pill is locked (matches the
 *  manifest-pill semantics) so the operator knows changes don't
 *  retroactively apply. */
function ModelPill({
  locked,
  selected,
  manifestModel,
  open,
  onToggle,
  onPick,
}: {
  locked: boolean;
  selected: string | null;
  manifestModel: string | null;
  open: boolean;
  onToggle: () => void;
  onPick: (value: string | null) => void;
}) {
  // Label priority: explicit override → manifest's model → "model".
  // Italic when inheriting so the user can see "this is *not* my
  // active override".
  const label = selected ?? manifestModel ?? "model";
  const isInherit = selected === null;
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        disabled={locked}
        className={
          locked
            ? "inline-flex items-center gap-1 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[11.5px] text-fg-muted"
            : "inline-flex items-center gap-1 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[11.5px] text-fg hover:bg-bg"
        }
        title={
          locked
            ? "Session active — model locked. End session to switch."
            : "Override the model for the next session"
        }
      >
        <span className="text-fg-subtle">model:</span>
        <span className={isInherit ? "italic font-mono text-fg-muted" : "font-mono"}>
          {label}
        </span>
        {!locked ? <ChevronDown className="h-3 w-3 opacity-60" /> : null}
      </button>
      {open && !locked ? (
        <ul
          role="menu"
          className="absolute left-0 top-full z-20 mt-1 w-56 overflow-hidden rounded-md border border-border bg-bg-elevated py-1 shadow-lg"
        >
          <li>
            <button
              type="button"
              role="menuitem"
              onClick={() => onPick(null)}
              className={
                selected === null
                  ? "flex w-full items-baseline gap-2 bg-accent/10 px-3 py-1.5 text-left"
                  : "flex w-full items-baseline gap-2 px-3 py-1.5 text-left hover:bg-bg-subtle"
              }
            >
              <span className="font-mono text-[12px] italic text-fg-muted">inherit</span>
              <span className="text-[10.5px] text-fg-subtle">
                {manifestModel ? `(uses ${manifestModel})` : "(manifest default)"}
              </span>
            </button>
          </li>
          {MODEL_PRESETS.map((p) => (
            <li key={p.value}>
              <button
                type="button"
                role="menuitem"
                onClick={() => onPick(p.value)}
                className={
                  selected === p.value
                    ? "flex w-full items-baseline gap-2 bg-accent/10 px-3 py-1.5 text-left"
                    : "flex w-full items-baseline gap-2 px-3 py-1.5 text-left hover:bg-bg-subtle"
                }
              >
                <span className="font-mono text-[12px] text-fg">{p.value}</span>
                <span className="text-[10.5px] text-fg-subtle">{p.label.replace(p.value, "").trim()}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
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
  // Phase I.2 — `user_message` carries the prompt for this turn.
  // Surfaced as a right-aligned user bubble so a fresh tab replaying
  // history sees the conversation both-sided (the live submit path
  // injects an optimistic user bubble via `localUserMessages`; the
  // replay path only has the persisted events).
  if (kind === "user_message") {
    const text = asString(event.data["text"]);
    if (!text) return null;
    return (
      <div
        data-event-kind="user_message"
        data-role="user"
        className="ml-auto max-w-[85%] rounded-md border border-accent/30 bg-accent/15 px-3 py-2"
      >
        <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-fg">
          {text}
        </pre>
      </div>
    );
  }
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
