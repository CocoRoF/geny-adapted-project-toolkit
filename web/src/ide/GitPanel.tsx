import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Check,
  ChevronDown,
  ChevronRight,
  GitBranch,
  GitCommit,
  GitPullRequest,
  Loader2,
  RefreshCw,
  RotateCcw,
  Send,
  Trash2,
  X,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type CreatePrResponse,
  type GitPushResponse,
  type GitStatusEntry,
  type GitStatusResponse,
  type GitSyncResponse,
  createPr,
  getGitDiff,
  getGitStatus,
  gitCommit,
  gitDiscard,
  gitFetch,
  gitPull,
  gitPush,
  gitSync,
} from "@/api/git";
import { useI18n } from "@/app/providers/i18n-context";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

interface Props {
  workspaceId: string;
}

type FlashKind = "info" | "error" | "warn";
type Flash = { kind: FlashKind; text: string };

/** Source-control panel — VS Code-class minus the multi-repo /
 * per-hunk-staging surfaces.
 *
 * Three vertical sections in the left pane:
 *   1. Branch header — branch name, ahead/behind chips, sync buttons
 *      (Fetch / Pull / Sync), refresh button.
 *   2. Changes — checklist with status icon + per-file discard.
 *      Click a row → diff in right pane.
 *   3. Recent commits — last 10 commits on this branch, collapsible.
 *
 * Commit / push / PR actions live in the footer with the message
 * editor. The right pane shows a colored unified diff for the
 * currently-selected file, or recent commits when no file is
 * selected.
 *
 * Architectural notes:
 *   * `.gapt/` is filtered server-side so the panel never shows the
 *     workspace's service log files as untracked. The user never sees
 *     those entries, never accidentally stages them.
 *   * Fetch + Pull are separate from Sync. Sync = fetch + pull + push
 *     in one call (VS Code's circular-arrows button); Pull = fetch +
 *     ff-merge only; Fetch = refs only. Each surfaces as its own
 *     button so the user can choose the granularity. */
export function GitPanel({ workspaceId }: Props) {
  const { t } = useI18n();
  const [status, setStatus] = useState<GitStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activePath, setActivePath] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<
    | "fetch"
    | "pull"
    | "sync"
    | "commit"
    | "push"
    | "pr"
    | "discard"
    | null
  >(null);
  const [diff, setDiff] = useState<{ path: string; text: string } | null>(null);
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [recentOpen, setRecentOpen] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const s = await getGitStatus(workspaceId);
      setStatus(s);
      // Pre-select everything on first load only. After the user
      // unchecks a row we don't auto-recheck it on every refresh —
      // their intent is preserved via the local Set.
      setSelected((prev) =>
        prev.size === 0 ? new Set(s.entries.map((e) => e.path)) : prev,
      );
    } catch (e) {
      setFlash({ kind: "error", text: errText(e) });
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const dirty = (status?.entries.length ?? 0) > 0;

  const toggle = (path: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const toggleAll = () => {
    if (!status) return;
    const all = status.entries.map((e) => e.path);
    setSelected((cur) =>
      cur.size === all.length ? new Set() : new Set(all),
    );
  };

  const onDiff = useCallback(
    async (path: string) => {
      setActivePath(path);
      try {
        const d = await getGitDiff(workspaceId, path);
        setDiff({
          path,
          text: d.diff || t("git.diff.empty"),
        });
      } catch (e) {
        setFlash({ kind: "error", text: errText(e) });
      }
    },
    [workspaceId, t],
  );

  const onDiscard = useCallback(
    async (path: string) => {
      if (
        !window.confirm(t("git.discard.confirm").replace("{path}", path))
      )
        return;
      setBusy("discard");
      try {
        const r = await gitDiscard(workspaceId, [path]);
        if (r.ok) {
          setFlash({ kind: "info", text: t("git.discard.done") });
        } else {
          setFlash({
            kind: "warn",
            text:
              `${t("git.discard.partial")} ${r.discarded.length}/${r.discarded.length + r.skipped.length}` +
              (r.skipped[0] ? ` — ${r.skipped[0].reason.slice(0, 100)}` : ""),
          });
        }
        if (activePath === path) {
          setActivePath(null);
          setDiff(null);
        }
        await refresh();
      } catch (e) {
        setFlash({ kind: "error", text: errText(e) });
      } finally {
        setBusy(null);
      }
    },
    [workspaceId, activePath, refresh, t],
  );

  const runSync = useCallback(
    async (
      kind: "fetch" | "pull" | "sync",
      fn: () => Promise<GitSyncResponse>,
    ) => {
      setBusy(kind);
      setFlash(null);
      try {
        const r = await fn();
        const label = r.actions.join(" + ");
        if (r.ok) {
          setFlash({
            kind: "info",
            text: `${label || kind} · ↑${r.ahead} ↓${r.behind}`,
          });
        } else {
          setFlash({
            kind: "error",
            text: `${label || kind} failed — ${(r.error || "see output").slice(0, 200)}`,
          });
        }
        await refresh();
      } catch (e) {
        setFlash({ kind: "error", text: errText(e) });
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  const onCommit = useCallback(async () => {
    if (!message.trim()) {
      setFlash({ kind: "error", text: t("git.commit.need_message") });
      return;
    }
    if (selected.size === 0) {
      setFlash({ kind: "error", text: t("git.commit.need_files") });
      return;
    }
    setBusy("commit");
    try {
      const r = await gitCommit(workspaceId, {
        message,
        paths: Array.from(selected),
      });
      setFlash({
        kind: "info",
        text: `${t("git.commit.done")} ${r.sha}${r.branch ? ` (${r.branch})` : ""}`,
      });
      setMessage("");
      setSelected(new Set());
      await refresh();
    } catch (e) {
      setFlash({ kind: "error", text: errText(e) });
    } finally {
      setBusy(null);
    }
  }, [message, selected, workspaceId, refresh, t]);

  const onPush = useCallback(async () => {
    setBusy("push");
    try {
      const r: GitPushResponse = await gitPush(workspaceId, {});
      setFlash({
        kind: "info",
        text: `${t("git.push.done")} → origin/${r.branch ?? "?"}`,
      });
      await refresh();
    } catch (e) {
      setFlash({ kind: "error", text: errText(e) });
    } finally {
      setBusy(null);
    }
  }, [workspaceId, refresh, t]);

  const onPr = useCallback(async () => {
    setBusy("pr");
    setPrUrl(null);
    try {
      const r: CreatePrResponse = await createPr(workspaceId, {
        title: message.split("\n")[0]?.trim() || "GAPT-authored changes",
        body: message,
        base: "main",
      });
      setPrUrl(r.url);
      setFlash({
        kind: "info",
        text: `${t("git.pr.done")} #${r.number}`,
      });
    } catch (e) {
      setFlash({ kind: "error", text: errText(e) });
    } finally {
      setBusy(null);
    }
  }, [message, workspaceId, t]);

  const syncState = useMemo(() => {
    if (!status) return "unknown" as const;
    if (status.ahead === 0 && status.behind === 0) return "synced" as const;
    if (status.ahead > 0 && status.behind > 0) return "diverged" as const;
    if (status.ahead > 0) return "ahead" as const;
    return "behind" as const;
  }, [status]);

  return (
    <div className="grid h-full grid-cols-[minmax(300px,380px)_1fr]">
      <aside className="flex h-full flex-col overflow-hidden border-r border-border bg-bg-elevated">
        {/* ── Branch header ── */}
        <header className="flex shrink-0 flex-col gap-1.5 border-b border-border px-3 py-2">
          <div className="flex items-center gap-1.5">
            <GitBranch className="h-3.5 w-3.5 text-fg-muted" strokeWidth={1.5} />
            <span className="font-mono text-[12.5px] font-semibold text-fg">
              {status?.branch ?? t("git.branch.detached")}
            </span>
            <SyncStateBadge state={syncState} ahead={status?.ahead ?? 0} behind={status?.behind ?? 0} />
            <Button
              size="sm"
              variant="ghost"
              onClick={refresh}
              disabled={loading || busy !== null}
              title={t("git.refresh")}
              className="ml-auto"
            >
              {loading ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RotateCcw className="h-3 w-3" />
              )}
            </Button>
          </div>
          {status?.upstream ? (
            <code className="text-[10px] text-fg-subtle">
              → {status.upstream}
            </code>
          ) : (
            <span className="text-[10px] text-warn">
              {t("git.upstream.none")}
            </span>
          )}
          <div className="flex flex-wrap gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => runSync("fetch", () => gitFetch(workspaceId))}
              disabled={busy !== null}
              title={t("git.fetch.title")}
            >
              {busy === "fetch" ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="mr-1 h-3 w-3" />
              )}
              {t("git.fetch")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => runSync("pull", () => gitPull(workspaceId))}
              disabled={busy !== null}
              title={t("git.pull.title")}
            >
              {busy === "pull" ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <ArrowDownToLine className="mr-1 h-3 w-3" />
              )}
              {t("git.pull")}
              {status && status.behind > 0 ? (
                <span className="ml-1 text-[10px] opacity-70">↓{status.behind}</span>
              ) : null}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => runSync("sync", () => gitSync(workspaceId))}
              disabled={busy !== null}
              title={t("git.sync.title")}
            >
              {busy === "sync" ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="mr-1 h-3 w-3" />
              )}
              {t("git.sync")}
            </Button>
          </div>
        </header>

        {/* ── Changes section ── */}
        <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <header className="flex items-center gap-2 border-b border-border bg-bg-subtle/40 px-3 py-1.5">
            <button
              type="button"
              className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted"
              onClick={() => status?.entries.length && toggleAll()}
            >
              {t("git.section.changes")}
            </button>
            <span className="text-[10.5px] text-fg-subtle">
              {status?.entries.length ?? 0}
            </span>
            {dirty ? (
              <button
                type="button"
                className="ml-auto text-[10px] text-fg-subtle hover:text-accent"
                onClick={toggleAll}
              >
                {selected.size === status?.entries.length
                  ? t("git.deselect_all")
                  : t("git.select_all")}
              </button>
            ) : null}
          </header>
          <div className="flex-1 overflow-y-auto py-1">
            {!status || status.entries.length === 0 ? (
              <p className="px-3 py-3 text-[11px] text-fg-subtle">
                {loading ? t("git.loading") : t("git.clean")}
              </p>
            ) : (
              <ul className="space-y-0.5 px-1.5">
                {status.entries.map((e) => (
                  <FileRow
                    key={e.path}
                    entry={e}
                    checked={selected.has(e.path)}
                    active={activePath === e.path}
                    onToggle={() => toggle(e.path)}
                    onView={() => onDiff(e.path)}
                    onDiscard={() => onDiscard(e.path)}
                    discarding={busy === "discard"}
                  />
                ))}
              </ul>
            )}
          </div>
        </section>

        {/* ── Recent commits ── */}
        {status?.recent_commits.length ? (
          <section className="shrink-0 border-t border-border">
            <button
              type="button"
              className="flex w-full items-center gap-1.5 bg-bg-subtle/40 px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider text-fg-muted hover:bg-bg-subtle"
              onClick={() => setRecentOpen((v) => !v)}
              aria-expanded={recentOpen}
            >
              {recentOpen ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              {t("git.section.recent")}
              <span className="text-[10.5px] text-fg-subtle">
                {status.recent_commits.length}
              </span>
            </button>
            {recentOpen ? (
              <ul className="max-h-44 overflow-y-auto py-1">
                {status.recent_commits.map((c) => (
                  <li
                    key={c.sha}
                    className="flex items-baseline gap-2 px-3 py-0.5 text-[11.5px]"
                  >
                    <GitCommit
                      className="h-3 w-3 shrink-0 text-fg-subtle"
                      strokeWidth={1.5}
                    />
                    <code className="font-mono text-[10.5px] text-fg-subtle">
                      {c.sha}
                    </code>
                    <span className="truncate text-fg" title={c.message}>
                      {c.message}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        ) : null}

        {/* ── Commit area ── */}
        <div className="shrink-0 border-t border-border p-2">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.currentTarget.value)}
            placeholder={t("git.commit.placeholder")}
            rows={3}
            className="w-full resize-none rounded-md border border-border bg-bg px-2 py-1 font-mono text-[12px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
          />
          {flash ? (
            <p
              className={cn(
                "mt-1 text-[11px]",
                flash.kind === "error"
                  ? "text-danger"
                  : flash.kind === "warn"
                    ? "text-warn"
                    : "text-accent",
              )}
            >
              {flash.text}
            </p>
          ) : null}
          <div className="mt-1 flex flex-wrap gap-1">
            <Button
              size="sm"
              variant="primary"
              onClick={onCommit}
              disabled={
                busy !== null || !dirty || !message.trim() || selected.size === 0
              }
            >
              {busy === "commit" ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Check className="mr-1 h-3 w-3" />
              )}
              {t("git.commit")}
              {selected.size > 0 && dirty ? (
                <span className="ml-1 text-[10px] opacity-70">
                  ({selected.size})
                </span>
              ) : null}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={onPush}
              disabled={busy !== null}
            >
              {busy === "push" ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <ArrowUpFromLine className="mr-1 h-3 w-3" />
              )}
              {t("git.push")}
              {status && status.ahead > 0 ? (
                <span className="ml-1 text-[10px] opacity-70">↑{status.ahead}</span>
              ) : null}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={onPr}
              disabled={busy !== null || !message.trim()}
              title={t("git.pr.title")}
            >
              {busy === "pr" ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <GitPullRequest className="mr-1 h-3 w-3" />
              )}
              {t("git.pr")}
            </Button>
          </div>
          {prUrl ? (
            <a
              href={prUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
            >
              <Send className="h-3 w-3" />
              {prUrl}
            </a>
          ) : null}
        </div>
      </aside>

      {/* ── Right pane: diff or recent commits ── */}
      <main className="flex h-full flex-col overflow-hidden bg-bg">
        <header className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-3 py-2">
          {diff ? (
            <>
              <span className="font-mono text-[12px] text-fg">{diff.path}</span>
              <button
                type="button"
                className="ml-auto text-fg-subtle hover:text-fg"
                onClick={() => {
                  setDiff(null);
                  setActivePath(null);
                }}
                title={t("git.diff.close")}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </>
          ) : (
            <span className="text-[12px] text-fg-subtle">
              {t("git.diff.placeholder")}
            </span>
          )}
        </header>
        <div className="flex-1 overflow-auto bg-bg">
          {diff ? <ColoredDiff text={diff.text} /> : null}
        </div>
      </main>
    </div>
  );
}

function SyncStateBadge({
  state,
  ahead,
  behind,
}: {
  state: "unknown" | "synced" | "ahead" | "behind" | "diverged";
  ahead: number;
  behind: number;
}) {
  const { t } = useI18n();
  if (state === "unknown") return null;
  if (state === "synced") {
    return (
      <Badge tone="success" className="text-[9.5px]">
        {t("git.sync_state.synced")}
      </Badge>
    );
  }
  return (
    <span className="inline-flex items-center gap-0.5 text-[10.5px] font-mono">
      {ahead > 0 ? (
        <span className="text-success">↑{ahead}</span>
      ) : null}
      {behind > 0 ? <span className="text-warn">↓{behind}</span> : null}
    </span>
  );
}

function FileRow({
  entry,
  checked,
  active,
  onToggle,
  onView,
  onDiscard,
  discarding,
}: {
  entry: GitStatusEntry;
  checked: boolean;
  active: boolean;
  onToggle: () => void;
  onView: () => void;
  onDiscard: () => void;
  discarding: boolean;
}) {
  const { t } = useI18n();
  const tone = statusTone(entry.status);
  return (
    <li
      className={cn(
        "group flex items-center gap-1.5 rounded px-1 py-0.5",
        active && "bg-accent/10",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="h-3 w-3"
      />
      <span
        className={cn(
          "w-6 shrink-0 rounded text-center font-mono text-[10px]",
          tone === "added" && "bg-success/15 text-success",
          tone === "modified" && "bg-accent/15 text-accent",
          tone === "deleted" && "bg-danger/15 text-danger",
          tone === "untracked" && "bg-warn/15 text-warn",
          tone === "renamed" && "bg-accent/15 text-accent",
          tone === "other" && "bg-bg-subtle text-fg-subtle",
        )}
        title={`porcelain: ${entry.status}`}
      >
        {shortStatus(entry.status)}
      </span>
      <button
        type="button"
        onClick={onView}
        className="flex-1 truncate text-left font-mono text-[12px] text-fg hover:text-accent"
      >
        {entry.path}
      </button>
      <button
        type="button"
        onClick={onDiscard}
        disabled={discarding}
        className="invisible shrink-0 rounded p-0.5 text-fg-subtle hover:bg-danger/10 hover:text-danger group-hover:visible"
        title={t("git.discard.title")}
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </li>
  );
}

function ColoredDiff({ text }: { text: string }) {
  // Unified diff line classifier. Two-state coloring (added / removed
  // / context / hunk header) is enough — full git-style intra-line
  // word diff is a stretch goal.
  return (
    <pre className="whitespace-pre px-3 py-2 font-mono text-[11.5px] leading-relaxed">
      {text.split("\n").map((line, i) => {
        let className = "block text-fg-muted";
        if (line.startsWith("+++") || line.startsWith("---")) {
          className = "block text-fg font-semibold";
        } else if (line.startsWith("@@")) {
          className = "block text-accent";
        } else if (line.startsWith("+")) {
          className = "block bg-success/10 text-success";
        } else if (line.startsWith("-")) {
          className = "block bg-danger/10 text-danger";
        } else if (line.startsWith("diff --git")) {
          className = "block text-fg font-semibold";
        }
        return (
          <span key={i} className={className}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}

function shortStatus(porcelain: string): string {
  const t = porcelain.trim();
  if (t === "??") return "U";
  // git porcelain v2 first 2 chars: index, worktree. Show worktree
  // (operator-facing state) by default, fall back to index.
  const wt = porcelain.length >= 2 ? porcelain[1] : porcelain[0] ?? "·";
  const idx = porcelain.length >= 1 ? porcelain[0] : "·";
  const ch = wt && wt.trim() ? wt : idx;
  return ch?.trim() || "·";
}

function statusTone(
  porcelain: string,
): "added" | "modified" | "deleted" | "untracked" | "renamed" | "other" {
  const t = porcelain.trim();
  if (t === "??") return "untracked";
  if (t.startsWith("A") || t.endsWith("A")) return "added";
  if (t.startsWith("M") || t.endsWith("M")) return "modified";
  if (t.startsWith("D") || t.endsWith("D")) return "deleted";
  if (t.startsWith("R")) return "renamed";
  return "other";
}

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.reason;
  if (e instanceof Error) return e.message;
  return String(e);
}
