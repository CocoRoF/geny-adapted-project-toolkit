import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  Download,
  ImagePlus,
  Loader2,
  Paperclip,
  PictureInPicture2,
  X,
} from "lucide-react";

import { ApiError } from "@/api/client";
import { ROUTER_BASENAME } from "@/app/basename";
import {
  type InvokeOverrides,
  type SessionEventKind,
  type SessionResponse,
  archiveSession,
  createSession,
  reactivateSession,
  interruptSession,
  type InvokeAttachment,
  invokeSession,
  listSessions,
  patchSessionOverrides,
} from "@/api/sessions";
import { useI18n } from "@/app/providers/i18n-context";
import type { MessageKey } from "@/i18n";
import { CostModal } from "@/chat/CostModal";
import { deriveCostSnapshot, type CostSnapshot as FullCostSnapshot } from "@/chat/cost-snapshot";
import { type ManifestSummary, listManifests } from "@/api/manifests";
import { DiffCard, type GaptEditPayload } from "@/chat/DiffCard";
import { annotateEditGroups } from "@/chat/diff-group";
import { GuardRejectedAlert } from "@/chat/GuardRejectedAlert";
import { ToolCallGroup } from "@/chat/ToolCallGroup";
import { MarkdownText } from "@/ui/MarkdownText";
import { pairToolEvents, type ToolPair } from "@/chat/tool-pair";
import { TraceStrip } from "@/chat/TraceStrip";
import { type SessionStreamEvent, useSessionStream } from "@/chat/useSessionStream";
import { OverflowToolbar } from "@/ui/OverflowToolbar";
import { PreviewableImage } from "@/ui/ImageLightbox";
import { toast } from "@/ui/toast";

type ChatMode = "plan" | "act";

const PLAN_PREFIX = "(Plan mode) Outline the steps without modifying any files:";

// ── composer image attachments ──────────────────────────────────
// Accepted everywhere Claude accepts them; 5 MB is Claude's decoded
// per-image API cap, 6 images keeps a turn well under request limits.
const IMAGE_MEDIA_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"] as const;
type ImageMediaType = (typeof IMAGE_MEDIA_TYPES)[number];
const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const MAX_ATTACHMENTS = 6;

interface PendingAttachment {
  id: string;
  mediaType: ImageMediaType;
  dataBase64: string;
  /** data: URL for the strip / bubble thumbnails. */
  previewUrl: string;
  name: string;
}

function isImageMediaType(v: string): v is ImageMediaType {
  return (IMAGE_MEDIA_TYPES as readonly string[]).includes(v);
}

/** File → PendingAttachment (base64 + preview). Rejects with a
 * user-displayable reason string. `t` is threaded in from the caller
 * (this helper lives outside the component, so it has no hook access). */
function readImageFile(file: File, t: (key: MessageKey) => string): Promise<PendingAttachment> {
  return new Promise((resolve, reject) => {
    if (!isImageMediaType(file.type)) {
      reject(
        new Error(`${file.name || "clipboard image"}: ${t("chat.attach.error.unsupported_type")}`),
      );
      return;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      reject(
        new Error(
          `${file.name || "clipboard image"}: ${(file.size / 1024 / 1024).toFixed(1)}${t("chat.attach.error.too_large")}`,
        ),
      );
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`${file.name}: ${t("chat.attach.error.read_failed")}`));
    reader.onload = () => {
      const url = typeof reader.result === "string" ? reader.result : "";
      const comma = url.indexOf(",");
      if (!url.startsWith("data:") || comma < 0) {
        reject(new Error(`${file.name}: ${t("chat.attach.error.encode_failed")}`));
        return;
      }
      resolve({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        mediaType: file.type as ImageMediaType,
        dataBase64: url.slice(comma + 1),
        previewUrl: url,
        name: file.name || "image",
      });
    };
    reader.readAsDataURL(file);
  });
}

interface Props {
  projectId: string;
  workspaceId: string;
  /** True when the panel IS the whole window (the `/chat` popup
   *  route). Hides the pop-out button — there's nothing to pop out
   *  of — everything else behaves identically. */
  standalone?: boolean;
  /** Fired after the pop-out window opened successfully. The IDE
   *  shell uses this to CLOSE the docked panel — popping out MOVES
   *  the chat (devtools-undock semantics), not copies it — and to
   *  watch the handle so the docked panel returns when the popup
   *  closes. Not fired when the browser blocked the popup. */
  onPoppedOut?: (win: Window) => void;
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

/** Phase L.4 — per-project sticky thinking budget (Anthropic extended
 *  thinking). `null` means "manifest decides" — pill shows "off"
 *  unless the manifest itself enables thinking. Operator picks one of
 *  the presets or types a custom token count. */
const THINKING_STORAGE_KEY = "gapt.chat.thinking_budget";

function readPersistedThinking(projectId: string): number | null {
  try {
    const raw = window.localStorage.getItem(`${THINKING_STORAGE_KEY}.${projectId}`);
    if (!raw) return null;
    const n = Number.parseInt(raw, 10);
    return Number.isFinite(n) && n >= 0 ? n : null;
  } catch {
    return null;
  }
}

function persistThinking(projectId: string, budget: number | null): void {
  try {
    const key = `${THINKING_STORAGE_KEY}.${projectId}`;
    if (budget === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(budget));
  } catch {
    /* best-effort */
  }
}

const THINKING_PRESETS: { value: number; label: string }[] = [
  { value: 0, label: "off" },
  { value: 1024, label: "1k" },
  { value: 4096, label: "4k" },
  { value: 16384, label: "16k" },
];

/** Phase I.4 — fetch markdown transcript from the session and drop
 *  it into a browser download. We trigger the click on a transient
 *  `<a>` so the file's blob URL is released as soon as the download
 *  starts; no global state, no UI side-effects beyond the download. */
async function downloadTranscriptMarkdown(sessionId: string): Promise<void> {
  const resp = await fetch(`/_gapt/api/sessions/${sessionId}/transcript?format=markdown`, {
    credentials: "include",
  });
  if (!resp.ok) {
    // Surface the failure in dev console — operator can re-try from
    // the UI button. A toast would be nicer but is overkill for the
    // one error path that exists today (server 5xx or 403).
    console.error("transcript download failed", resp.status, await resp.text().catch(() => ""));
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

export function ChatPanel({ projectId, workspaceId, standalone = false, onPoppedOut }: Props) {
  const { t } = useI18n();
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [message, setMessage] = useState("");
  // Composer image attachments — pasted (Ctrl+V), dropped onto the
  // panel, or picked via the paperclip. Sent as base64 with the next
  // invoke; the executor's multimodal normalizer turns them into
  // Anthropic image blocks so the model actually sees them.
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Phase N.2.7 — cross-device handoff state. When the user sends a
  // message on a session that's already mid-invoke on another tab/PC,
  // the server returns `session.already_invoking` (409). Pre-fix that
  // landed as a raw red error banner with no recovery path. Now we
  // stash the failed payload + the echo seq so we can either retry
  // after an explicit interrupt ("강제로 이어받기") or roll back the
  // optimistic user bubble.
  const [staleInvoke, setStaleInvoke] = useState<{
    message: string;
    mode: ChatMode;
    overrideBody: InvokeOverrides;
    echoSeq: number;
  } | null>(null);
  // Phase N.3 — budget-exhausted banner. When GAPT rejects an invoke
  // with `session.budget_exhausted` (cumulative cost reached the
  // per-session cap), we surface the current totals + a path forward
  // ("새 세션 시작" / Settings 에서 한도 조정) instead of a raw red
  // alert. `echoSeq` is the optimistic user-bubble seq so we can roll
  // it back when the user dismisses the banner.
  const [budgetExhausted, setBudgetExhausted] = useState<{
    cost_usd: number;
    cost_budget_usd: number;
    echoSeq: number;
  } | null>(null);
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
  // Phase L.4 — per-session extended-thinking budget. `null` = inherit
  // (manifest decides). Sticky per project.
  const [thinkingBudget, setThinkingBudget] = useState<number | null>(() =>
    readPersistedThinking(projectId),
  );
  const [thinkingMenuOpen, setThinkingMenuOpen] = useState(false);
  // Phase L.3 — session picker. Holds all sessions for this workspace
  // (active + archived). Refreshes on mount + every time the local
  // session changes so a newly-created/reactivated row shows up.
  const [workspaceSessions, setWorkspaceSessions] = useState<SessionResponse[]>([]);
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
  // Synthetic user-message events kept client-side. Negative seqs so
  // they sort before any server event of the same wall-clock moment.
  const [userEvents, setUserEvents] = useState<SessionStreamEvent[]>([]);
  const userSeqRef = useRef(-1);
  const dismissedGuardSeq = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Phase L.3 — pull every workspace session (active + archived) so
  // the SessionPicker has the full history. We auto-attach to the
  // most recently-active session if one exists; the operator can
  // switch via the picker afterward. URL `?session=<id>` query
  // overrides the auto-select for deep links from SessionDetail.
  useEffect(() => {
    void listSessions(projectId, {
      workspaceId,
      includeArchived: true,
    })
      .then((rows) => {
        setWorkspaceSessions(rows);
        const url = new URL(window.location.href);
        const hinted = url.searchParams.get("session");
        const fromUrl = hinted ? rows.find((s) => s.id === hinted) : undefined;
        const wsActive = rows.find((s) => s.workspace_id === workspaceId && s.status === "active");
        if (fromUrl) setSession(fromUrl);
        else if (wsActive) setSession(wsActive);
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

  // Detach the chat into its own browser popup (devtools-undock
  // style). The popup mounts the standalone `/chat` route — same
  // session cookie, same SSE stream — and carries the current
  // session id so it attaches to exactly what the user was looking
  // at. Named window: re-clicking focuses the existing popup
  // instead of stacking new ones.
  const openPopup = useCallback(() => {
    const query = session ? `?session=${encodeURIComponent(session.id)}` : "";
    const url = `${ROUTER_BASENAME}/projects/${projectId}/w/${workspaceId}/chat${query}`;
    const win = window.open(url, `gapt-chat-${workspaceId}`, "popup=yes,width=560,height=800");
    if (win) {
      win.focus();
      onPoppedOut?.(win);
    }
  }, [onPoppedOut, projectId, session, workspaceId]);

  const onPickModel = useCallback(
    (value: string | null) => {
      setModelOverride(value);
      persistModel(projectId, value);
      setModelMenuOpen(false);
      // Phase M.2 — clear → fire an immediate revert so the pill's
      // visual state matches the runtime. Without this, picking
      // "manifest default" mid-session silently left the previous
      // override in place until the next user message — and even then
      // the override only stuck because we *re-sent* it; clearing
      // never reverted.
      if (value === null && session) {
        void patchSessionOverrides(session.id, { clear: ["model"] }).catch(() => {
          // best-effort — the next invoke also carries the clear and
          // the server is idempotent on already-baseline values.
        });
      }
    },
    [projectId, session],
  );

  // Phase L.4 — pick a thinking budget. null = inherit (manifest).
  const onPickThinking = useCallback(
    (budget: number | null) => {
      setThinkingBudget(budget);
      persistThinking(projectId, budget);
      setThinkingMenuOpen(false);
      if (budget === null && session) {
        void patchSessionOverrides(session.id, { clear: ["thinking"] }).catch(() => {
          // best-effort, same rationale as the model branch above.
        });
      }
    },
    [projectId, session],
  );

  // Phase L.3 — switch the attached session. Clears optimistic user
  // events from the previous session so the new event stream renders
  // cleanly. Reactivates archived sessions inline before attaching so
  // the runtime path can rehydrate prior messages (Phase L.1).
  const onSwitchSession = useCallback((target: SessionResponse) => {
    setSessionMenuOpen(false);
    setUserEvents([]);
    setError(null);
    const attach = (row: SessionResponse) => {
      setSession(row);
      setWorkspaceSessions((rows) => rows.map((r) => (r.id === row.id ? row : r)));
    };
    if (target.status === "archived") {
      void reactivateSession(target.id)
        .then(attach)
        .catch((err: unknown) => {
          setError(
            err instanceof ApiError
              ? `${err.code}: ${err.reason}`
              : err instanceof Error
                ? err.message
                : String(err),
          );
        });
    } else {
      attach(target);
    }
  }, []);

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
        : userEvents.filter((ev) => !backendUserTexts.has(asString(ev.data["text"])));
    return [...filteredUser, ...stream.events].sort((a, b) => a.ts.localeCompare(b.ts));
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
      // Phase L.4 — only thread the thinking knob when the operator
      // explicitly set one. None means "manifest decides".
      ...(thinkingBudget !== null
        ? {
            thinking_enabled: thinkingBudget > 0,
            thinking_budget_tokens: thinkingBudget,
          }
        : {}),
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
  }, [manifestId, modelOverride, thinkingBudget, projectId, workspaceId]);

  // Mirror of `attachments` for synchronous reads inside the async
  // add path — avoids putting side effects in a state updater (React
  // StrictMode runs updaters twice in dev, which was duplicating
  // every pasted image).
  const attachmentsCountRef = useRef(0);
  useEffect(() => {
    attachmentsCountRef.current = attachments.length;
  }, [attachments]);

  const addImageFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      void (async () => {
        const settled = await Promise.allSettled(files.map((f) => readImageFile(f, t)));
        const fresh: PendingAttachment[] = [];
        for (const r of settled) {
          if (r.status === "fulfilled") fresh.push(r.value);
          else toast.error(r.reason instanceof Error ? r.reason.message : String(r.reason));
        }
        if (fresh.length === 0) return;
        const room = MAX_ATTACHMENTS - attachmentsCountRef.current;
        if (room <= 0) {
          toast.error(t("chat.attach.error.max_total").replace("{max}", String(MAX_ATTACHMENTS)));
          return;
        }
        if (fresh.length > room) {
          toast.warning(
            t("chat.attach.error.partial")
              .replace("{count}", String(room))
              .replace("{max}", String(MAX_ATTACHMENTS)),
          );
        }
        // PURE updater: dedup by content + enforce the cap inside, so a
        // StrictMode double-call is a harmless no-op the second time.
        // Content dedup also collapses clipboards that expose the same
        // screenshot through multiple items.
        setAttachments((prev) => {
          const seen = new Set(prev.map((a) => `${a.mediaType}:${a.dataBase64}`));
          const next = [...prev];
          for (const att of fresh) {
            if (next.length >= MAX_ATTACHMENTS) break;
            const key = `${att.mediaType}:${att.dataBase64}`;
            if (seen.has(key)) continue;
            seen.add(key);
            next.push(att);
          }
          return next.length === prev.length ? prev : next;
        });
      })();
    },
    [t],
  );

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const onComposerPaste = useCallback(
    (e: React.ClipboardEvent) => {
      const files: File[] = [];
      for (const item of Array.from(e.clipboardData?.items ?? [])) {
        if (item.kind === "file") {
          const f = item.getAsFile();
          if (f && f.type.startsWith("image/")) files.push(f);
        }
      }
      if (files.length > 0) {
        // Image paste only — text pastes keep default behaviour.
        e.preventDefault();
        addImageFiles(files);
      }
    },
    [addImageFiles],
  );

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
      const display = outgoing; // what we show locally — *before* prepending PLAN_PREFIX
      if (activeMode === "plan" && outgoing.length > 0) {
        outgoing = `${PLAN_PREFIX}\n\n${outgoing}`;
      }
      // Capture this turn's attachments and clear the strip — an
      // image-only turn (no text) is legitimate now.
      const turnAttachments = attachments;
      if (turnAttachments.length > 0) setAttachments([]);
      // Pure mode-switch commands with no payload — don't fire an
      // empty invoke.
      if (outgoing.length === 0 && turnAttachments.length === 0) {
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
          data: {
            text: display,
            role: "user",
            ...(turnAttachments.length > 0
              ? { attachment_previews: turnAttachments.map((a) => a.previewUrl) }
              : {}),
          },
          ts: new Date().toISOString(),
        },
      ]);
      // Phase D.1 — pass the active mode along so the backend policy
      // hook short-circuits mutating tools when mode is "plan".
      //
      // Phase M.2 — every invoke sends the FULL declared intent of
      // the override pills, not just the deltas. If `modelOverride`
      // is null we send `clear: ["model"]` so the runtime is forced
      // back to the manifest baseline; otherwise we send the explicit
      // model. Same for thinking. This makes the override path
      // idempotent + stateless — there's no way for a previous
      // override to silently stick around when the pill shows
      // "inherit". The server-side `apply_per_invoke_overrides` is a
      // no-op when the request matches the current runtime state, so
      // sending clear every turn is cheap.
      const clearTargets: Array<"model" | "thinking"> = [];
      const overrideBody: InvokeOverrides = {};
      if (modelOverride) {
        overrideBody.model = modelOverride;
      } else {
        clearTargets.push("model");
      }
      if (thinkingBudget !== null) {
        overrideBody.thinking_enabled = thinkingBudget > 0;
        overrideBody.thinking_budget_tokens = thinkingBudget;
      } else {
        clearTargets.push("thinking");
      }
      if (clearTargets.length > 0) {
        overrideBody.clear = clearTargets;
      }
      const wireAttachments: InvokeAttachment[] = turnAttachments.map((a) => ({
        kind: "image",
        media_type: a.mediaType,
        data_base64: a.dataBase64,
      }));
      void invokeSession(session.id, outgoing, activeMode, overrideBody, wireAttachments)
        .catch((err: unknown) => {
          if (err instanceof ApiError && err.code === "session.already_invoking") {
            // Phase N.2.7 — cross-device handoff: another tab/PC is
            // still mid-turn on this session. Hold the payload + the
            // optimistic echo so the user can either take over or
            // back out cleanly.
            setStaleInvoke({
              message: outgoing,
              mode: activeMode,
              overrideBody,
              echoSeq: seq,
            });
            return;
          }
          if (err instanceof ApiError && err.code === "session.budget_exhausted") {
            // Phase N.3 — GAPT-side budget cap reached. The server
            // attached the live totals via structured `details` so we
            // can render exact spend + cap in the banner; falling back
            // to 0/0 is safe (the banner just hides the figures).
            const d = err.details;
            const cost = typeof d.cost_usd === "number" ? d.cost_usd : 0;
            const cap = typeof d.cost_budget_usd === "number" ? d.cost_budget_usd : 0;
            setBudgetExhausted({
              cost_usd: cost,
              cost_budget_usd: cap,
              echoSeq: seq,
            });
            return;
          }
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
    [attachments, session, mode, modelOverride, thinkingBudget],
  );

  // Phase N.2.7 — "강제로 이어받기" handler. The server's interrupt
  // route (since N.2.7) waits for the cancelled task's terminal frame
  // before returning, so retrying the invoke immediately after the
  // await won't race the cleanup and re-hit `session.already_invoking`.
  const takeOverStaleInvoke = useCallback(async () => {
    if (!staleInvoke || !session) return;
    setPending(true);
    setError(null);
    try {
      await interruptSession(session.id);
      await invokeSession(
        session.id,
        staleInvoke.message,
        staleInvoke.mode,
        staleInvoke.overrideBody,
      );
      setStaleInvoke(null);
    } catch (err: unknown) {
      setError(
        err instanceof ApiError
          ? `${err.code}: ${err.reason}`
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setPending(false);
    }
  }, [staleInvoke, session]);

  const cancelStaleInvoke = useCallback(() => {
    if (!staleInvoke) return;
    // Roll back the optimistic user-bubble echo since we're abandoning
    // the send. Without this the chat would carry a "ghost" entry for
    // a message that never reached the agent.
    setUserEvents((prev) => prev.filter((e) => e.seq !== staleInvoke.echoSeq));
    setStaleInvoke(null);
  }, [staleInvoke]);

  // Phase N.3 — dismiss budget banner. Same optimistic-echo rollback
  // shape as `cancelStaleInvoke`: the message never reached the agent,
  // so the local bubble is a ghost that has to be cleared.
  const dismissBudgetExhausted = useCallback(() => {
    if (!budgetExhausted) return;
    setUserEvents((prev) => prev.filter((e) => e.seq !== budgetExhausted.echoSeq));
    setBudgetExhausted(null);
  }, [budgetExhausted]);

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const trimmed = message.trim();
    if ((!trimmed && attachments.length === 0) || !session) return;
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
      // Gate on the real "turn in progress" signal (`isThinking`) —
      // the prior check only fired when a tool_call frame existed or
      // `pending` was set, so Esc did nothing during the common case
      // of the assistant streaming plain text with no tool calls yet.
      if (!isThinking && !pending) return;
      e.preventDefault();
      interrupt();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [session, isThinking, pending, interrupt]);

  // Reset the local user echoes when the session changes so a fresh
  // chat doesn't inherit the previous session's bubbles. Also clear
  // them when the user archives mid-conversation.
  //
  // Phase N.2.7 — clear the takeover banner state too. If the user
  // hit a stale-invoke on one session and then switched to another,
  // we'd be falsely offering to "take over" a payload bound to the
  // previous session.id.
  useEffect(() => {
    setUserEvents([]);
    userSeqRef.current = -1;
    setStaleInvoke(null);
    setBudgetExhausted(null);
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
    <div
      data-panel-kind="chat"
      className="relative flex h-full flex-col"
      onDragOver={(e) => {
        if (Array.from(e.dataTransfer?.types ?? []).includes("Files")) {
          e.preventDefault();
          setDragOver(true);
        }
      }}
      onDragLeave={(e) => {
        // Only clear when leaving the panel itself, not a child.
        if (e.currentTarget === e.target) setDragOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const files = Array.from(e.dataTransfer?.files ?? []).filter((f) =>
          f.type.startsWith("image/"),
        );
        if (files.length > 0) addImageFiles(files);
      }}
    >
      {dragOver ? (
        <div className="pointer-events-none absolute inset-0 z-40 grid place-items-center rounded-md border-2 border-dashed border-accent bg-accent/10">
          <p className="flex items-center gap-2 rounded-md bg-bg-elevated px-3 py-2 text-[13px] font-medium text-accent shadow-lg">
            <ImagePlus className="h-4 w-4" />
            {t("chat.attach.drop_hint")}
          </p>
        </div>
      ) : null}
      <header className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-3 py-2">
        {/* The control pills live inside an OverflowToolbar: when the
            panel is too narrow for all of them, the tail collapses
            into a `…` popover instead of being clipped off-screen
            (the old behaviour made the session picker / cost button
            literally unreachable in a narrow chat column). Priority
            = DOM order: manifest → model → thinking → session →
            mode → cost → transcript. */}
        <OverflowToolbar moreLabel={t("toolbar.more")}>
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
          {/* Phase L follow-up — model + thinking pills are never
              locked. The backend mutates `state.model` / `state.thinking_*`
              on each `/invoke` so changes apply to the NEXT turn of the
              existing session, no new-session dance required. */}
          <ModelPill
            locked={false}
            selected={modelOverride}
            manifestModel={selectedManifest?.model ?? null}
            open={modelMenuOpen}
            onToggle={() => setModelMenuOpen((v) => !v)}
            onPick={onPickModel}
          />
          <ThinkingPill
            locked={false}
            selected={thinkingBudget}
            open={thinkingMenuOpen}
            onToggle={() => setThinkingMenuOpen((v) => !v)}
            onPick={onPickThinking}
          />
          {/* Phase L.3 — pick / switch / reactivate sessions. Only shown
              when there's at least one session for this workspace. */}
          {workspaceSessions.length > 0 ? (
            <SessionPicker
              sessions={workspaceSessions}
              current={session}
              open={sessionMenuOpen}
              onToggle={() => setSessionMenuOpen((v) => !v)}
              onPick={onSwitchSession}
            />
          ) : null}
          {session ? (
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
          ) : null}
          {session ? (
            <button
              type="button"
              data-testid="chat-cost"
              onClick={() => setShowCostModal(true)}
              aria-haspopup="dialog"
              aria-label={t("cost.open")}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[11px] font-mono tabular-nums text-fg-muted hover:bg-surface-hover hover:text-fg"
            >
              <span className="text-accent">${cost.cost_usd.toFixed(4)}</span>
              <span>·</span>
              <span>↑{cost.input_tokens}</span>
              <span>↓{cost.output_tokens}</span>
            </button>
          ) : null}
          {/* Phase I.4 — markdown transcript download. Reads
              session_events DB-side so a server restart never loses
              history. */}
          {session ? (
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
          ) : null}
        </OverflowToolbar>
        <span className="sr-only">{session ? session.env_manifest_id : ""}</span>
        {/* Pop-out lives OUTSIDE the overflow toolbar — pinned and
            always reachable regardless of how narrow the panel gets.
            Hidden in the popup itself. */}
        {!standalone ? (
          <button
            type="button"
            data-testid="chat-popout"
            onClick={openPopup}
            aria-label={t("chat.popup.open")}
            title={t("chat.popup.open")}
            className="inline-flex shrink-0 items-center rounded-md border border-border bg-bg-subtle p-1.5 text-fg-muted hover:bg-surface-hover hover:text-fg"
          >
            <PictureInPicture2 className="h-3.5 w-3.5" strokeWidth={1.5} />
          </button>
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
            {(() => {
              // Phase N.1 — pre-group consecutive tool_call events so
              // a turn that fires 15 Bash/Read calls renders as a
              // single collapsible "Tools (15)" container instead of
              // 15 sibling strips that bury the assistant's reply.
              // The grouping walk also marks every tool_call that was
              // absorbed so the inner map can skip emitting a
              // duplicate ToolCallCard at the original event slot.
              type RenderEntry =
                | { kind: "event"; event: SessionStreamEvent }
                | {
                    kind: "tool_group";
                    key: string;
                    pairs: ToolPair[];
                  };
              const merged = mergeAssistantText(allEvents.filter((e) => e.kind !== "step"));
              const entries: RenderEntry[] = [];
              let runPairs: ToolPair[] = [];
              const flushRun = () => {
                if (runPairs.length === 0) return;
                entries.push({
                  kind: "tool_group",
                  key: `tool-group-${runPairs[0]?.call.seq ?? 0}`,
                  pairs: runPairs,
                });
                runPairs = [];
              };
              for (const event of merged) {
                if (event.kind === "tool_call") {
                  const pair = toolPairs.find((p) => p.call.seq === event.seq);
                  if (pair) {
                    runPairs.push(pair);
                  }
                  // No pair = this tool_call was a deduped duplicate
                  // re-emit (the executor fires api.tool_use twice; the
                  // pair rendered at the full-input frame's seq). Skip it
                  // — never fall through to a stray raw tool_call row.
                  continue;
                }
                if (event.kind === "tool_result") {
                  // tool_results that pair with an in-run tool_call
                  // shouldn't break the run (they live INSIDE the
                  // group's cards). gapt_edit results still surface
                  // as DiffCards below — flush the current run first
                  // so the diff appears AFTER the group it belongs to.
                  if (pairedEventSeqs.has(event.seq) && !maybeGaptEditPayload(event.data)) {
                    continue;
                  }
                }
                flushRun();
                entries.push({ kind: "event", event });
              }
              flushRun();

              return entries.map((entry) => {
                if (entry.kind === "tool_group") {
                  // Solo pair renders flat (no group wrapper) inside
                  // `ToolCallGroup` itself.
                  return <ToolCallGroup key={entry.key} pairs={entry.pairs} />;
                }
                const event = entry.event;
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
                    const card = <DiffCard workspaceId={workspaceId} payload={edit} />;
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
              });
            })()}
            <TraceStrip events={allEvents} active={isThinking} />
            {isThinking ? <TypingIndicator /> : null}
          </div>

          <form onSubmit={onSubmit} className="shrink-0 border-t border-border bg-bg-elevated p-3">
            {attachments.length > 0 ? (
              <div
                data-testid="chat-attachments"
                className="mb-2 flex flex-wrap items-center gap-2"
              >
                {attachments.map((a) => (
                  <span key={a.id} className="group relative inline-block">
                    <PreviewableImage
                      src={a.previewUrl}
                      alt={a.name}
                      title={a.name}
                      className="h-14 w-14 rounded-md border border-border object-cover"
                    />
                    <button
                      type="button"
                      aria-label={`remove ${a.name}`}
                      onClick={() => removeAttachment(a.id)}
                      className="absolute -right-1.5 -top-1.5 grid h-4.5 w-4.5 place-items-center rounded-full border border-border bg-bg-elevated text-fg-muted shadow hover:text-danger"
                      style={{ height: 18, width: 18 }}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
            <input
              ref={fileInputRef}
              type="file"
              accept={IMAGE_MEDIA_TYPES.join(",")}
              multiple
              className="hidden"
              onChange={(e) => {
                addImageFiles(Array.from(e.currentTarget.files ?? []));
                e.currentTarget.value = ""; // same file re-pickable
              }}
            />
            <textarea
              value={message}
              onChange={(e) => setMessage(e.currentTarget.value)}
              onPaste={onComposerPaste}
              placeholder={t("chat.placeholder")}
              rows={3}
              aria-label={t("chat.placeholder")}
              className="w-full resize-none rounded-md border border-border bg-surface px-2.5 py-2 text-[13px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (message.trim().length === 0 && attachments.length === 0) return;
                  setMessage("");
                  send(message.trim());
                }
              }}
            />
            <div className="mt-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  data-testid="chat-attach-button"
                  onClick={() => fileInputRef.current?.click()}
                  title={t("chat.attach.add")}
                  aria-label={t("chat.attach.add")}
                  className="grid h-7 w-7 place-items-center rounded-md border border-border bg-surface text-fg-muted hover:bg-surface-hover hover:text-fg"
                >
                  <Paperclip className="h-3.5 w-3.5" />
                </button>
                <p data-testid="chat-shortcut" className="text-[10px] text-fg-subtle">
                  {t("chat.shortcut.esc")}
                </p>
              </div>
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
                  disabled={pending || (message.trim().length === 0 && attachments.length === 0)}
                  className="h-7 rounded-md bg-accent px-3 text-[12px] font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50"
                >
                  {t("chat.send")}
                </button>
              </div>
            </div>
          </form>

          {budgetExhausted ? (
            <div
              role="alert"
              className="mx-3 mb-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2.5 text-[12px] text-danger"
            >
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <div className="flex-1">
                  <p className="font-medium">{t("chat.budget.title")}</p>
                  <p className="mt-0.5 text-[11.5px] opacity-90">
                    {t("chat.budget.spent_label")}{" "}
                    <span className="font-mono">${budgetExhausted.cost_usd.toFixed(4)}</span>
                    {t("chat.budget.cap_label")}
                    <span className="font-mono">${budgetExhausted.cost_budget_usd.toFixed(4)}</span>
                    {t("chat.budget.advice")}
                  </p>
                </div>
              </div>
              <div className="mt-2 flex items-center justify-end gap-1.5">
                <button
                  type="button"
                  onClick={dismissBudgetExhausted}
                  className="h-7 rounded-md border border-border bg-bg px-2.5 text-[11.5px] text-fg hover:bg-bg-subtle"
                >
                  {t("chat.budget.confirm")}
                </button>
              </div>
            </div>
          ) : null}

          {staleInvoke ? (
            <div
              role="alert"
              className="mx-3 mb-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2.5 text-[12px] text-warn"
            >
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <div className="flex-1">
                  <p className="font-medium">{t("chat.takeover.title")}</p>
                  <p className="mt-0.5 text-[11.5px] opacity-90">{t("chat.takeover.body")}</p>
                </div>
              </div>
              <div className="mt-2 flex items-center justify-end gap-1.5">
                <button
                  type="button"
                  onClick={cancelStaleInvoke}
                  disabled={pending}
                  className="h-7 rounded-md border border-border bg-bg px-2.5 text-[11.5px] text-fg hover:bg-bg-subtle disabled:opacity-50"
                >
                  {t("chat.takeover.cancel")}
                </button>
                <button
                  type="button"
                  onClick={() => void takeOverStaleInvoke()}
                  disabled={pending}
                  className="inline-flex h-7 items-center gap-1 rounded-md bg-warn px-2.5 text-[11.5px] font-medium text-bg hover:bg-warn/90 disabled:opacity-50"
                >
                  {pending ? (
                    <>
                      <Loader2 className="h-3 w-3 animate-spin" />
                      {t("chat.takeover.pending")}
                    </>
                  ) : (
                    t("chat.takeover.confirm")
                  )}
                </button>
              </div>
            </div>
          ) : null}

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
                    <span className="flex-1 truncate font-mono text-[12.5px] text-fg">{m.id}</span>
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
                    <span className="text-[11px] text-fg-muted">{m.description}</span>
                  ) : null}
                  {m.model ? (
                    <span className="font-mono text-[10.5px] text-fg-subtle">model: {m.model}</span>
                  ) : null}
                </button>
              </li>
            );
          })}
          {manifests.length === 0 ? (
            <li className="px-3 py-2 text-[11px] text-fg-subtle">No manifests loaded.</li>
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
        <span className={isInherit ? "italic font-mono text-fg-muted" : "font-mono"}>{label}</span>
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
                <span className="text-[10.5px] text-fg-subtle">
                  {p.label.replace(p.value, "").trim()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Phase L.4 — Anthropic extended-thinking budget picker.
 *
 *  `selected = null` means "manifest decides" (typical: off). Picking
 *  a preset persists per-project. Locked while a session is active —
 *  switching mid-conversation would require a new manifest commit. */
function ThinkingPill({
  locked,
  selected,
  open,
  onToggle,
  onPick,
}: {
  locked: boolean;
  selected: number | null;
  open: boolean;
  onToggle: () => void;
  onPick: (value: number | null) => void;
}) {
  const isInherit = selected === null;
  const label =
    selected === null
      ? "auto"
      : selected === 0
        ? "off"
        : selected >= 1024
          ? `${Math.round(selected / 1024)}k`
          : String(selected);
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
            ? "Session active — thinking budget locked. End session to switch."
            : "Set Anthropic extended-thinking budget for the next session"
        }
      >
        <span className="text-fg-subtle">think:</span>
        <span className={isInherit ? "italic font-mono text-fg-muted" : "font-mono"}>{label}</span>
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
              <span className="font-mono text-[12px] italic text-fg-muted">auto</span>
              <span className="text-[10.5px] text-fg-subtle">(manifest decides)</span>
            </button>
          </li>
          {THINKING_PRESETS.map((p) => (
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
                <span className="font-mono text-[12px] text-fg">{p.label}</span>
                <span className="text-[10.5px] text-fg-subtle tabular-nums">
                  {p.value === 0 ? "no thinking" : `${p.value.toLocaleString()} tokens`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Phase L.3 — pick / switch / reactivate session. Workspace-scoped.
 *
 *  Active sessions click straight through; archived sessions hit the
 *  reactivate endpoint first (which restores L.1's conversation
 *  memory on the next invoke). The current session is the first row
 *  so the operator's eye lands there immediately. */
function SessionPicker({
  sessions,
  current,
  open,
  onToggle,
  onPick,
}: {
  sessions: SessionResponse[];
  current: SessionResponse | null;
  open: boolean;
  onToggle: () => void;
  onPick: (target: SessionResponse) => void;
}) {
  const label = current
    ? current.first_user_message?.slice(0, 24) || `session ${current.id.slice(-6)}`
    : "no session";
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        // Phase M.6 — `min-w-0` on the flex container is the missing
        // ingredient that lets the truncate inside the label actually
        // bite. Without it, the flex item's intrinsic min-width is
        // `auto` and the label expands past `max-w-[140px]`, blowing
        // the pill row out and pushing the right-side header
        // controls off-screen on narrow workspaces.
        className="inline-flex min-w-0 items-center gap-1 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[11.5px] text-fg hover:bg-bg max-w-[220px]"
        title="Switch session in this workspace"
      >
        <span className="shrink-0 text-fg-subtle">session:</span>
        <span className="min-w-0 truncate font-mono">{label}</span>
        <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
      </button>
      {open ? (
        <ul
          role="menu"
          className="absolute left-0 top-full z-20 mt-1 max-h-80 w-80 overflow-auto rounded-md border border-border bg-bg-elevated py-1 shadow-lg"
        >
          {sessions.map((s) => {
            const isCurrent = current?.id === s.id;
            const snippet = s.first_user_message?.trim() || "(no recorded prompt)";
            return (
              <li key={s.id}>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => onPick(s)}
                  className={
                    isCurrent
                      ? "flex w-full flex-col items-start gap-0.5 bg-accent/10 px-3 py-1.5 text-left"
                      : "flex w-full flex-col items-start gap-0.5 px-3 py-1.5 text-left hover:bg-bg-subtle"
                  }
                >
                  <span className="flex w-full min-w-0 items-center gap-1.5">
                    <span
                      className={
                        s.status === "active"
                          ? "shrink-0 rounded bg-success/15 px-1 text-[9.5px] uppercase tracking-wider text-success"
                          : "shrink-0 rounded bg-bg-subtle px-1 text-[9.5px] uppercase tracking-wider text-fg-subtle"
                      }
                    >
                      {s.status}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[12px] text-fg">
                      {snippet.length > 50 ? `${snippet.slice(0, 50)}…` : snippet}
                    </span>
                    <span className="shrink-0 font-mono text-[10.5px] tabular-nums text-fg-subtle">
                      ${s.cost_usd.toFixed(4)}
                    </span>
                  </span>
                  <span className="flex w-full min-w-0 items-center gap-2 text-[10px] text-fg-subtle">
                    <span className="min-w-0 truncate font-mono">{s.env_manifest_id}</span>
                    <span className="shrink-0">·</span>
                    <span className="shrink-0">{s.turn_count ?? 0} turns</span>
                    <span className="ml-auto shrink-0 font-mono tabular-nums">
                      {new Date(s.last_active_at).toLocaleString()}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
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
    const isAssistantText = ev.kind === "text" && asString(ev.data["role"]) !== "user";
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
    // Persisted events carry attachment META only (media types, no
    // bytes) — render compact chips. An image-only turn has empty
    // text but must still show its bubble.
    const metaRaw = event.data["attachments"];
    const attachmentMeta = Array.isArray(metaRaw) ? metaRaw : [];
    if (!text && attachmentMeta.length === 0) return null;
    return (
      <div
        data-event-kind="user_message"
        data-role="user"
        className="ml-auto max-w-[85%] rounded-md border border-accent/30 bg-accent/15 px-3 py-2"
      >
        {attachmentMeta.length > 0 ? (
          <div className="mb-1 flex flex-wrap gap-1">
            {attachmentMeta.map((m, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 rounded border border-accent/30 bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent"
              >
                <ImagePlus className="h-3 w-3" />
                {asString((m as Record<string, unknown>)["media_type"], "image")}
              </span>
            ))}
          </div>
        ) : null}
        {text ? (
          <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-fg">
            {text}
          </pre>
        ) : null}
      </div>
    );
  }
  if (kind === "text") {
    // Backend emits `{text: ...}`; legacy stubs / older providers
    // used `{chunk: ...}`. Accept both.
    const text = asString(event.data["text"]) || asString(event.data["chunk"]);
    const isUser = asString(event.data["role"]) === "user";
    // Optimistic local echo carries data-URL previews for this turn's
    // pasted/dropped images — render real thumbnails.
    const previewsRaw = event.data["attachment_previews"];
    const previews = Array.isArray(previewsRaw)
      ? previewsRaw.filter((u): u is string => typeof u === "string")
      : [];
    if (!text && previews.length === 0) return null;
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
        {previews.length > 0 ? (
          <div className="mb-1 flex flex-wrap gap-1.5">
            {previews.map((u, i) => (
              <PreviewableImage
                key={i}
                src={u}
                alt={`attachment ${i + 1}`}
                className="h-20 w-20 rounded-md border border-accent/30 object-cover"
              />
            ))}
          </div>
        ) : null}
        {/* Phase K.1 — assistant responses render as markdown so code
            blocks, inline code, and lists actually look right. User
            echoes stay as raw text — the operator typed it literally,
            it shouldn't get reformatted. */}
        {isUser ? (
          <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-fg">
            {text}
          </pre>
        ) : (
          <MarkdownText>{text}</MarkdownText>
        )}
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
