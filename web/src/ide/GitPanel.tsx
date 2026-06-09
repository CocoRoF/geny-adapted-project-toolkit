import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Check,
  ChevronDown,
  ChevronRight,
  GitBranch,
  GitCommit,
  GitMerge,
  GitPullRequest,
  Inbox,
  Loader2,
  Package,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Trash2,
  X,
} from "lucide-react";

import { SplitHandle } from "@/ide/shell/SplitHandle";
import { ApiError } from "@/api/client";
import {
  type CreatePrResponse,
  type GitBranchInfo,
  type GitBranchesResponse,
  type GitLogCommit,
  type GitLogResponse,
  type GitPushResponse,
  type GitStashEntry,
  type GitStashListResponse,
  type GitStatusEntry,
  type GitStatusResponse,
  type GitSyncResponse,
  createPr,
  getGitBranches,
  getGitLog,
  getGitStashList,
  getGitStatus,
  gitBranchDelete,
  gitCheckout,
  gitCommit,
  gitDiscard,
  gitFetch,
  gitPull,
  gitPush,
  gitStashDrop,
  gitStashPop,
  gitStashPush,
  gitSync,
} from "@/api/git";
import {
  type ProjectRepository,
  listProjectRepositories,
} from "@/api/repositories";
import { rehydrateWorkspace } from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

interface Props {
  workspaceId: string;
  /** Phase N.4 — Source Control fetches the project's repository
   *  list so the operator can choose which one to inspect. Single-
   *  repo projects render without the selector (just the legacy
   *  single-repo view). Empty projects (0 repos) render an empty
   *  state. */
  projectId: string;
  /** Phase F — clicking a changed-file row hands the diff off to the
   *  editor column instead of rendering it inside this sidebar. */
  onOpenDiff: (path: string) => void;
}

type FlashKind = "info" | "error" | "warn";
type Flash = { kind: FlashKind; text: string };

type BusyOp =
  | "fetch"
  | "pull"
  | "sync"
  | "commit"
  | "push"
  | "pr"
  | "discard"
  | "checkout"
  | "branch-delete"
  | "stash"
  | null;

/** Source-control panel — VS Code-class.
 *
 * Header: branch switcher (dropdown of local + remote branches, create-new
 * inline) + sync-state badge + Fetch / Pull / Sync / Refresh.
 *
 * Left aside (collapsible sections):
 *   1. Changes — checklist + click-to-diff + per-file discard.
 *   2. Stash — list, push, pop, drop.
 *   3. History — recent commits with refs as badges, parent-aware
 *      ASCII rail at the left for merge visualisation.
 *
 * Right pane: colored unified diff for the selected file.
 *
 * All endpoints are scoped to one workspace_id; `.gapt/` is filtered
 * server-side so runtime log files never clutter the changes list. */
export function GitPanel({ workspaceId, projectId, onOpenDiff }: Props) {
  const { t } = useI18n();
  // Phase N.4 — list of repos attached to the project + which one
  // the operator is currently inspecting. ``null`` for selectedRepoId
  // means "use the project's primary" (the server picks the lowest
  // sort_order active row). When ``repos.length <= 1`` the selector
  // chrome is hidden — single-repo projects keep the legacy UX.
  const [repos, setRepos] = useState<ProjectRepository[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null);
  const [status, setStatus] = useState<GitStatusResponse | null>(null);
  const [branchesResp, setBranchesResp] = useState<GitBranchesResponse | null>(null);
  const [stash, setStash] = useState<GitStashListResponse | null>(null);
  const [log, setLog] = useState<GitLogResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activePath, setActivePath] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<BusyOp>(null);
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [openSections, setOpenSections] = useState<{
    stash: boolean;
    history: boolean;
  }>({ stash: false, history: true });
  const [branchMenuOpen, setBranchMenuOpen] = useState(false);
  const [newBranchInput, setNewBranchInput] = useState("");
  const [stashMsgInput, setStashMsgInput] = useState("");
  // VS Code-style draggable section heights. Each is the pixel
  // height of the OPEN section's body — Changes is the residual
  // (`flex-1`), Stash and History have explicit user-controllable
  // heights with a SplitHandle above them. Defaults chosen so the
  // first-open state shows a few entries without dominating Changes.
  const [stashHeight, setStashHeight] = useState(140);
  const [historyHeight, setHistoryHeight] = useState(200);

  // Phase N.4 — fetch the repo list once per project. The selector
  // hides itself when there's only one row (legacy UX), surfaces a
  // dropdown when there are multiple. Empty projects render a tiny
  // "no source control" state in the body below.
  const [reposLoaded, setReposLoaded] = useState(false);
  // Phase N.4 — "this repo isn't on disk in the current workspace"
  // detector. Triggers when the backend's `git status` comes back
  // with "not a git repository" for the selected subpath — happens
  // when the operator added the repo to the project AFTER an
  // existing workspace was created, so the worktree subdir is
  // missing its `.git` marker.
  const [notCloned, setNotCloned] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void listProjectRepositories(projectId)
      .then((rows) => {
        if (cancelled) return;
        setRepos(rows);
        // Phase N.5 — default selection prefers a repo with a remote
        // URL over an empty/git-init candidate. Otherwise the
        // GitPanel snaps to a 0-byte subdir and renders "not cloned"
        // even though sibling repos in the same workspace are
        // perfectly fine. Lowest sort_order wins as the tiebreaker
        // among non-empty repos (matches the legacy primary
        // semantics).
        setSelectedRepoId((current) => {
          if (current) return current;
          const firstWithRemote = rows.find((r) => !!r.git_remote_url);
          return firstWithRemote?.id ?? rows[0]?.id ?? null;
        });
        setReposLoaded(true);
      })
      .catch(() => {
        // 404 / 403 — leave repos empty; the body will fall back to
        // the empty-state. We never want a repo-list failure to break
        // git ops entirely.
        setReposLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const refresh = useCallback(async () => {
    // Phase N.4 — empty project: no repos to query. Bail out before
    // hitting the backend so the operator doesn't see a raw "fatal:
    // not a git repository" surfaced from `git status`.
    if (reposLoaded && repos.length === 0) {
      setStatus(null);
      setBranchesResp(null);
      setStash(null);
      setLog(null);
      setNotCloned(false);
      return;
    }
    setLoading(true);
    setNotCloned(false);
    try {
      const [s, b, st, l] = await Promise.all([
        getGitStatus(workspaceId, selectedRepoId),
        getGitBranches(workspaceId, selectedRepoId),
        getGitStashList(workspaceId, selectedRepoId),
        getGitLog(workspaceId, { limit: 50, all_branches: true }, selectedRepoId),
      ]);
      setStatus(s);
      setBranchesResp(b);
      setStash(st);
      setLog(l);
      setSelected((prev) =>
        prev.size === 0 ? new Set(s.entries.map((e) => e.path)) : prev,
      );
    } catch (e) {
      // Phase N.4 — backend translates "fatal: not a git repository"
      // into structured 412 ``git.repo_not_cloned`` so all 4 parallel
      // git calls fail with the same code instead of fanning out raw
      // 500s into the browser console. We render the friendly empty
      // state and swallow the secondary errors silently.
      //
      // Defensive fallback: a backend that hasn't been redeployed yet
      // still returns 500 with the raw stderr in ``reason``. Match
      // that string too so the panel degrades gracefully — without
      // this, the operator sees the raw "fatal: not a git repository"
      // text as a flash even though the new structured code is in
      // the client.
      const msg = errText(e);
      const isStructured =
        e instanceof ApiError && e.code === "git.repo_not_cloned";
      const isLegacyRawError = msg.includes("not a git repository");
      if (isStructured || isLegacyRawError) {
        setNotCloned(true);
      } else {
        setFlash({ kind: "error", text: msg });
      }
    } finally {
      setLoading(false);
    }
  }, [workspaceId, selectedRepoId, reposLoaded, repos.length]);

  // Phase N.4 — re-clone any project repos that aren't on disk yet.
  // The endpoint is idempotent, so calling it on a fully-cloned
  // workspace is a no-op. We surface success/failure through the
  // flash bar + refresh the panel so the operator sees the changes
  // section come back to life on success.
  const [rehydrating, setRehydrating] = useState(false);
  const onRehydrate = useCallback(async () => {
    setRehydrating(true);
    try {
      const r = await rehydrateWorkspace(workspaceId);
      if (r.outcome === "cloned" || r.outcome === "exists" || r.outcome === "skipped") {
        setNotCloned(false);
        setFlash({ kind: "info", text: `워크스페이스에 레포가 준비됐어요 (${r.outcome})` });
        await refresh();
      } else if (r.outcome === "empty") {
        setNotCloned(false);
        setFlash({ kind: "info", text: "빈 프로젝트 — 클론할 레포가 없어요" });
      } else {
        setFlash({
          kind: "error",
          text: `재클론 실패: ${(r.detail ?? r.outcome).slice(0, 200)}`,
        });
      }
    } catch (e) {
      setFlash({ kind: "error", text: errText(e) });
    } finally {
      setRehydrating(false);
    }
  }, [workspaceId, refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Reset selected files + active diff when switching repos so the
  // changes panel doesn't carry stale highlights from another repo.
  useEffect(() => {
    setSelected(new Set());
    setActivePath(null);
  }, [selectedRepoId]);

  const dirty = (status?.entries.length ?? 0) > 0;
  const stashCount = stash?.entries.length ?? 0;

  // ── change-selection helpers ───────────────────────
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
    setSelected((cur) => (cur.size === all.length ? new Set() : new Set(all)));
  };

  // ── diff ───────────────────────────────────────────
  // Phase F — click hands the path to the editor column. The
  // panel keeps `activePath` only as a visual highlight; the
  // actual diff is rendered by `FileDiffView` inside `EditorArea`.
  const onDiff = useCallback(
    (path: string) => {
      setActivePath(path);
      onOpenDiff(path);
    },
    [onOpenDiff],
  );

  // ── discard ────────────────────────────────────────
  const onDiscard = useCallback(
    async (path: string) => {
      if (!window.confirm(t("git.discard.confirm").replace("{path}", path)))
        return;
      setBusy("discard");
      try {
        const r = await gitDiscard(workspaceId, [path], selectedRepoId);
        setFlash(
          r.ok
            ? { kind: "info", text: t("git.discard.done") }
            : {
                kind: "warn",
                text: `${t("git.discard.partial")} ${r.discarded.length}/${
                  r.discarded.length + r.skipped.length
                }`,
              },
        );
        if (activePath === path) {
          setActivePath(null);
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

  // ── sync trio (fetch / pull / sync) ────────────────
  const runSync = useCallback(
    async (kind: "fetch" | "pull" | "sync", fn: () => Promise<GitSyncResponse>) => {
      setBusy(kind);
      setFlash(null);
      try {
        const r = await fn();
        const label = r.actions.join(" + ");
        setFlash(
          r.ok
            ? { kind: "info", text: `${label || kind} · ↑${r.ahead} ↓${r.behind}` }
            : {
                kind: "error",
                text: `${label || kind} failed — ${(r.error || "see output").slice(0, 200)}`,
              },
        );
        await refresh();
      } catch (e) {
        setFlash({ kind: "error", text: errText(e) });
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  // ── commit ─────────────────────────────────────────
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
      const r = await gitCommit(workspaceId, { message, paths: Array.from(selected) }, selectedRepoId);
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
      const r: GitPushResponse = await gitPush(workspaceId, {}, selectedRepoId);
      setFlash({ kind: "info", text: `${t("git.push.done")} → origin/${r.branch ?? "?"}` });
      await refresh();
    } catch (e) {
      // git push errors are noisy multi-line `hint:` / `error:`
      // blobs. Compress to a one-liner action item; full text
      // available on hover/click.
      const raw = errText(e);
      setFlash({
        kind: "error",
        text: friendlyPushError(raw, t),
      });
    } finally {
      setBusy(null);
    }
  }, [workspaceId, refresh, t]);

  const onPr = useCallback(async () => {
    setBusy("pr");
    setPrUrl(null);
    try {
      const r: CreatePrResponse = await createPr(
        workspaceId,
        {
          title: message.split("\n")[0]?.trim() || "GAPT-authored changes",
          body: message,
          base: "main",
        },
        selectedRepoId,
      );
      setPrUrl(r.url);
      setFlash({ kind: "info", text: `${t("git.pr.done")} #${r.number}` });
    } catch (e) {
      setFlash({ kind: "error", text: errText(e) });
    } finally {
      setBusy(null);
    }
  }, [message, workspaceId, t]);

  // ── branches ───────────────────────────────────────
  const onCheckout = useCallback(
    async (branchName: string, opts: { create?: boolean; startPoint?: string } = {}) => {
      setBusy("checkout");
      try {
        const r = await gitCheckout(
          workspaceId,
          {
            branch: branchName,
            create: opts.create ?? false,
            start_point: opts.startPoint ?? null,
          },
          selectedRepoId,
        );
        if (r.ok) {
          setFlash({ kind: "info", text: `${t("git.checkout.done")} ${branchName}` });
          setBranchMenuOpen(false);
          setNewBranchInput("");
          await refresh();
        } else {
          setFlash({
            kind: "error",
            text: `${t("git.checkout.failed")}: ${(r.error || "").slice(0, 200)}`,
          });
        }
      } catch (e) {
        setFlash({ kind: "error", text: errText(e) });
      } finally {
        setBusy(null);
      }
    },
    [workspaceId, refresh, t],
  );

  const onBranchDelete = useCallback(
    async (branchName: string) => {
      if (!window.confirm(t("git.branch.delete_confirm").replace("{name}", branchName)))
        return;
      setBusy("branch-delete");
      try {
        const r = await gitBranchDelete(
          workspaceId,
          { branch: branchName },
          selectedRepoId,
        );
        if (r.ok) {
          setFlash({ kind: "info", text: `${t("git.branch.deleted")} ${branchName}` });
        } else {
          // Most failures are "not fully merged" — offer force.
          if (
            window.confirm(
              t("git.branch.force_confirm").replace("{name}", branchName) +
                "\n\n" +
                (r.error || "").slice(0, 200),
            )
          ) {
            const r2 = await gitBranchDelete(
              workspaceId,
              { branch: branchName, force: true },
              selectedRepoId,
            );
            if (r2.ok) {
              setFlash({ kind: "info", text: `${t("git.branch.deleted")} ${branchName}` });
            } else {
              setFlash({
                kind: "error",
                text: `${t("git.branch.delete_failed")}: ${(r2.error || "").slice(0, 200)}`,
              });
            }
          }
        }
        await refresh();
      } catch (e) {
        setFlash({ kind: "error", text: errText(e) });
      } finally {
        setBusy(null);
      }
    },
    [workspaceId, refresh, t],
  );

  // ── stash ──────────────────────────────────────────
  const onStashPush = useCallback(async () => {
    setBusy("stash");
    try {
      const r = await gitStashPush(
        workspaceId,
        {
          message: stashMsgInput.trim() || undefined,
          include_untracked: true,
        },
        selectedRepoId,
      );
      setFlash(
        r.ok
          ? { kind: "info", text: t("git.stash.pushed") }
          : { kind: "error", text: `${t("git.stash.push_failed")}: ${(r.error || "").slice(0, 200)}` },
      );
      setStashMsgInput("");
      await refresh();
    } catch (e) {
      setFlash({ kind: "error", text: errText(e) });
    } finally {
      setBusy(null);
    }
  }, [workspaceId, stashMsgInput, refresh, t]);

  const onStashPop = useCallback(
    async (ref: string) => {
      setBusy("stash");
      try {
        const r = await gitStashPop(workspaceId, { ref }, selectedRepoId);
        setFlash(
          r.ok
            ? { kind: "info", text: `${t("git.stash.popped")} ${ref}` }
            : {
                kind: "error",
                text: `${t("git.stash.pop_failed")}: ${(r.error || "").slice(0, 200)}`,
              },
        );
        await refresh();
      } catch (e) {
        setFlash({ kind: "error", text: errText(e) });
      } finally {
        setBusy(null);
      }
    },
    [workspaceId, refresh, t],
  );

  const onStashDrop = useCallback(
    async (ref: string) => {
      if (!window.confirm(t("git.stash.drop_confirm").replace("{ref}", ref))) return;
      setBusy("stash");
      try {
        const r = await gitStashDrop(workspaceId, { ref }, selectedRepoId);
        setFlash(
          r.ok
            ? { kind: "info", text: `${t("git.stash.dropped")} ${ref}` }
            : {
                kind: "error",
                text: `${t("git.stash.drop_failed")}: ${(r.error || "").slice(0, 200)}`,
              },
        );
        await refresh();
      } catch (e) {
        setFlash({ kind: "error", text: errText(e) });
      } finally {
        setBusy(null);
      }
    },
    [workspaceId, refresh, t],
  );

  const syncState = useMemo(() => {
    if (!status) return "unknown" as const;
    // Branch with no upstream tracking — `ahead`/`behind` are
    // meaningless (git couldn't compute them). Distinguish this from
    // the genuine "everything is in sync" case so the badge stops
    // lying about a freshly-scaffolded workspace.
    if (!status.upstream) return "no-upstream" as const;
    if (status.ahead === 0 && status.behind === 0) return "synced" as const;
    if (status.ahead > 0 && status.behind > 0) return "diverged" as const;
    if (status.ahead > 0) return "ahead" as const;
    return "behind" as const;
  }, [status]);

  // Phase N.5 — figure out which "no source control" message (if any)
  // to render in the body. Replaces the pre-N.5 early-return that
  // hid the entire panel including the repo selector — which meant
  // the operator was trapped on a single repo with no way to switch
  // to a sibling that actually had a clone on disk.
  const selectedRepo = repos.find((r) => r.id === selectedRepoId) ?? null;
  const isEmptyProject = reposLoaded && repos.length === 0;
  const isCandidateRepo =
    selectedRepo !== null && !selectedRepo.git_remote_url;
  // `notCloned` only meaningfully applies to repos that DO have a
  // remote — a candidate (no remote) is "empty by design", not "not
  // cloned yet", so we route it to a different sub-state below.
  const showNotCloned = notCloned && !isCandidateRepo;
  const showCandidate = isCandidateRepo;
  const showNormalBody = !isEmptyProject && !showNotCloned && !showCandidate;

  return (
    <div
      className="grid h-full grid-cols-1"
    >
      <aside className="flex h-full flex-col overflow-hidden border-r border-border bg-bg-elevated">
        {/* ── Header (branch · upstream · sync state all on ONE row;
                       action buttons on the row below) ── */}
        <header className="relative flex shrink-0 flex-col gap-1.5 border-b border-border px-3 py-2">
          {/* Phase N.5 — repository selector. Shown whenever the
              workspace carries more than one repo, INCLUDING when
              the panel body is in an empty/notCloned state — without
              this the operator gets stranded on a single repo with
              no way to switch to a sibling that's actually cloned. */}
          {repos.length > 1 ? (
            <div className="flex min-w-0 items-center gap-1.5">
              <Package className="h-3.5 w-3.5 shrink-0 text-fg-muted" strokeWidth={1.5} />
              <select
                value={selectedRepoId ?? ""}
                onChange={(e) => setSelectedRepoId(e.target.value || null)}
                className="min-w-0 flex-1 truncate rounded border border-border bg-bg px-1.5 py-0.5 font-mono text-[11.5px] text-fg focus:outline-none focus:ring-1 focus:ring-accent"
                title="이 패널이 들여다볼 레포지토리를 바꿉니다"
              >
                {repos.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.display_name}
                    {r.subpath ? `  ·  ${r.subpath}/` : ""}
                    {!r.git_remote_url ? "  (빈 폴더)" : ""}
                  </option>
                ))}
              </select>
              <Badge tone="neutral" className="text-[9.5px]">
                {repos.length}
              </Badge>
            </div>
          ) : null}
          {/* Phase N.5 — body fallbacks live INSIDE the panel chrome
              so the repo selector above is always reachable. The
              actual header rows (branch switcher, sync trio, etc.)
              are suppressed when the body is in an empty state. */}
          {showNormalBody ? (
          <>
          <div className="flex min-w-0 items-center gap-1.5">
            <button
              type="button"
              onClick={() => setBranchMenuOpen((v) => !v)}
              className="flex min-w-0 items-center gap-1 rounded px-1.5 py-0.5 hover:bg-bg-subtle"
              title={t("git.branch.switcher")}
            >
              <GitBranch className="h-3.5 w-3.5 shrink-0 text-fg-muted" strokeWidth={1.5} />
              <span className="truncate font-mono text-[12.5px] font-semibold text-fg">
                {status?.branch ?? t("git.branch.detached")}
              </span>
              <ChevronDown className="h-3 w-3 shrink-0 text-fg-subtle" />
            </button>
            {status?.upstream ? (
              <code
                className="min-w-0 truncate text-[10.5px] text-fg-subtle"
                title={`upstream → ${status.upstream}`}
              >
                → {status.upstream}
              </code>
            ) : (
              <span className="truncate text-[10px] text-warn" title={t("git.upstream.none")}>
                ⚠ {t("git.upstream.none_short")}
              </span>
            )}
            <SyncStateBadge
              state={syncState}
              ahead={status?.ahead ?? 0}
              behind={status?.behind ?? 0}
            />
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
          <div className="flex flex-wrap gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => runSync("fetch", () => gitFetch(workspaceId, selectedRepoId))}
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
              onClick={() => runSync("pull", () => gitPull(workspaceId, selectedRepoId))}
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
              onClick={() => runSync("sync", () => gitSync(workspaceId, selectedRepoId))}
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
          {branchMenuOpen ? (
            <BranchMenu
              branches={branchesResp?.branches ?? []}
              busy={busy === "checkout" || busy === "branch-delete"}
              newBranchInput={newBranchInput}
              onNewBranchInput={setNewBranchInput}
              onCheckout={onCheckout}
              onDelete={onBranchDelete}
              onClose={() => setBranchMenuOpen(false)}
            />
          ) : null}
            </>
          ) : null}
        </header>

        {/* Phase N.5 — empty-state body sections. Live INSIDE the
            panel so the repo selector above stays reachable. */}
        {isEmptyProject ? (
          <EmptyStateBody
            icon={<GitBranch className="h-8 w-8 text-fg-subtle" strokeWidth={1.25} />}
            title="레포지토리가 없어요"
            description={
              <>
                이 프로젝트는 빈 상태입니다. 프로젝트 페이지의 "레포지토리" 섹션에서{" "}
                <strong className="text-fg">레포 추가</strong>로 git URL 을 등록하거나,
                URL 없이 빈 폴더만 만들 수도 있어요.
              </>
            }
            footer={
              <Link
                to={`/projects/${projectId}`}
                className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-bg-elevated px-3 text-[11.5px] font-medium text-fg hover:bg-surface-hover"
              >
                프로젝트 페이지로 이동 →
              </Link>
            }
          />
        ) : null}
        {showCandidate ? (
          <EmptyStateBody
            icon={<Package className="h-8 w-8 text-fg-subtle" strokeWidth={1.25} />}
            title={`'${selectedRepo?.display_name}' 은(는) 빈 폴더 레포예요`}
            description={
              <>
                이 레포는 원격 URL 없이 등록돼서 git 추적이 비활성화돼 있어요.
                터미널에서{" "}
                <code className="rounded bg-bg-subtle px-1 py-0.5 text-[11px] text-fg-muted">
                  cd {selectedRepo?.subpath || "."} && git init
                </code>
                {" "}으로 git 추적을 시작하거나, 프로젝트 페이지에서 원격 URL 을 추가하세요.
              </>
            }
          />
        ) : null}
        {showNotCloned ? (
          <EmptyStateBody
            icon={
              <RefreshCw
                className={cn(
                  "h-8 w-8 text-fg-subtle",
                  rehydrating && "animate-spin",
                )}
                strokeWidth={1.25}
              />
            }
            title="이 레포는 아직 워크스페이스에 없어요"
            description={
              <>
                프로젝트에 새로 추가된 레포지토리는 자동 클론이 안 되어 있을 수 있어요.
                아래 버튼으로 지금 바로 가져올 수 있습니다.
              </>
            }
            footer={
              <>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={onRehydrate}
                  disabled={rehydrating}
                >
                  {rehydrating ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <ArrowDownToLine className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  {rehydrating ? "클론 중…" : "지금 클론하기"}
                </Button>
                <Link
                  to={`/projects/${projectId}`}
                  className="text-[11px] text-fg-subtle hover:text-accent"
                >
                  또는 프로젝트 페이지에서 새 워크스페이스 만들기 →
                </Link>
                {flash ? (
                  <p
                    className={cn(
                      "px-2 text-[11px]",
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
              </>
            }
          />
        ) : null}

        {showNormalBody ? (
          <>
        {/* ── Commit composer (VS Code-style: lives ABOVE Changes,
            full-width primary button, dropdown for variants) ── */}
        <div className="shrink-0 space-y-1.5 border-b border-border bg-bg-elevated px-2 py-2">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.currentTarget.value)}
            placeholder={t("git.commit.placeholder_branch").replace(
              "{branch}",
              status?.branch ?? "?",
            )}
            rows={2}
            className="w-full resize-none rounded-md border border-border bg-bg px-2 py-1.5 text-[12px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                if (
                  !(busy !== null || !dirty || !message.trim() || selected.size === 0)
                ) {
                  void onCommit();
                }
              }
            }}
          />
          <Button
            variant="primary"
            onClick={onCommit}
            disabled={
              busy !== null || !dirty || !message.trim() || selected.size === 0
            }
            className="w-full justify-center"
          >
            {busy === "commit" ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Check className="mr-1.5 h-3.5 w-3.5" />
            )}
            <span className="font-semibold">{t("git.commit")}</span>
            {selected.size > 0 && dirty && selected.size < (status?.entries.length ?? 0) ? (
              <span className="ml-1.5 text-[10.5px] opacity-70">
                ({selected.size}/{status?.entries.length})
              </span>
            ) : null}
          </Button>
          <div className="flex gap-1">
            {/* Push button enable logic:
                  * no branch    → disable (detached HEAD or empty repo)
                  * no upstream  → ENABLE (first push will -u set upstream)
                  * upstream + ahead === 0 → disable (nothing to push)
                  * upstream + ahead > 0   → enable
                Pre-fix the button was disabled whenever `ahead === 0`,
                which collapsed the "synced" case and the "no upstream
                yet" case into one — a freshly-scaffolded workspace
                with commits but no tracking branch couldn't push at
                all even though the backend supports `-u` first-push. */}
            <Button
              size="sm"
              variant="ghost"
              onClick={onPush}
              disabled={
                busy !== null ||
                !status?.branch ||
                (!!status?.upstream && (status.ahead ?? 0) === 0)
              }
              title={
                !status?.branch
                  ? t("git.push.no_branch")
                  : !status.upstream
                    ? t("git.push.first")
                    : (status.ahead ?? 0) === 0
                      ? t("git.push.nothing")
                      : t("git.push")
              }
              className="flex-1"
            >
              {busy === "push" ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <ArrowUpFromLine className="mr-1 h-3 w-3" />
              )}
              {t("git.push")}
              {status && status.ahead > 0 ? (
                <span className="ml-1 text-[10px] opacity-70">↑{status.ahead}</span>
              ) : !status?.upstream && status?.branch ? (
                <span className="ml-1 text-[10px] opacity-70">-u</span>
              ) : null}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={onPr}
              disabled={busy !== null || !message.trim()}
              title={t("git.pr.title")}
              className="flex-1"
            >
              {busy === "pr" ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <GitPullRequest className="mr-1 h-3 w-3" />
              )}
              {t("git.pr")}
            </Button>
          </div>
          {flash ? (
            <p
              className={cn(
                "px-1 text-[11px]",
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
          {prUrl ? (
            <a
              href={prUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 px-1 text-[11px] text-accent hover:underline"
            >
              <Send className="h-3 w-3" />
              {prUrl}
            </a>
          ) : null}
        </div>

        {/* ── Changes (VS Code-style collapsible w/ count) ── */}
        <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <header className="flex items-center gap-1.5 border-b border-border bg-bg-subtle/40 px-3 py-1.5">
            <ChevronDown className="h-3 w-3 text-fg-muted" />
            <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
              {t("git.section.changes")}
            </span>
            <span className="ml-auto inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-accent/15 px-1.5 text-[10px] font-semibold text-accent">
              {status?.entries.length ?? 0}
            </span>
            {dirty ? (
              <button
                type="button"
                className="text-[10px] text-fg-subtle hover:text-accent"
                onClick={toggleAll}
                title={
                  selected.size === status?.entries.length
                    ? t("git.deselect_all")
                    : t("git.select_all")
                }
              >
                {selected.size === status?.entries.length ? "☑" : "☐"}
              </button>
            ) : null}
          </header>
          <div className="flex-1 overflow-y-auto py-0.5">
            {!status || status.entries.length === 0 ? (
              <p className="px-3 py-3 text-[11px] text-fg-subtle">
                {loading ? t("git.loading") : t("git.clean")}
              </p>
            ) : (
              <ul className="space-y-px">
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

        {/* ── Stash (resizable when open) ── */}
        {openSections.stash ? (
          <SplitHandle
            axis="vertical"
            value={stashHeight}
            onChange={setStashHeight}
            min={80}
            max={400}
            invert
          />
        ) : null}
        <section
          className="shrink-0 overflow-hidden border-t border-border"
          style={openSections.stash ? { height: stashHeight } : undefined}
        >
          <button
            type="button"
            className="flex w-full shrink-0 items-center gap-1.5 bg-bg-subtle/40 px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider text-fg-muted hover:bg-bg-subtle"
            onClick={() => setOpenSections((s) => ({ ...s, stash: !s.stash }))}
            aria-expanded={openSections.stash}
          >
            {openSections.stash ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            <Package className="h-3 w-3" strokeWidth={1.5} />
            {t("git.section.stash")}
            <span className="ml-auto inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-bg-subtle px-1.5 text-[10px] font-semibold text-fg-subtle">
              {stashCount}
            </span>
          </button>
          {openSections.stash ? (
            <div className="flex h-[calc(100%-30px)] flex-col border-t border-border px-2 py-1.5">
              <div className="mb-1.5 flex gap-1">
                <input
                  value={stashMsgInput}
                  onChange={(e) => setStashMsgInput(e.target.value)}
                  placeholder={t("git.stash.msg_placeholder")}
                  className="flex-1 rounded border border-border bg-bg px-2 py-0.5 text-[11px] text-fg placeholder:text-fg-subtle"
                  disabled={busy !== null}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={onStashPush}
                  disabled={busy !== null || !dirty}
                  title={t("git.stash.push_title")}
                >
                  {busy === "stash" ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Inbox className="h-3 w-3" />
                  )}
                </Button>
              </div>
              {stashCount === 0 ? (
                <p className="px-1 py-1 text-[10.5px] text-fg-subtle">
                  {t("git.stash.empty")}
                </p>
              ) : (
                <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
                  {stash!.entries.map((s) => (
                    <li
                      key={s.ref}
                      className="group flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px]"
                    >
                      <code className="font-mono text-[10.5px] text-fg-subtle">
                        {s.ref}
                      </code>
                      <span className="flex-1 truncate text-fg" title={s.subject}>
                        {s.subject}
                      </span>
                      <button
                        type="button"
                        onClick={() => onStashPop(s.ref)}
                        disabled={busy !== null}
                        className="invisible rounded p-0.5 text-fg-subtle hover:bg-accent/10 hover:text-accent group-hover:visible"
                        title={t("git.stash.pop_title")}
                      >
                        <ArrowDownToLine className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => onStashDrop(s.ref)}
                        disabled={busy !== null}
                        className="invisible rounded p-0.5 text-fg-subtle hover:bg-danger/10 hover:text-danger group-hover:visible"
                        title={t("git.stash.drop_title")}
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </section>

        {/* ── History (commit log with refs + graph hints; resizable) ── */}
        {openSections.history ? (
          <SplitHandle
            axis="vertical"
            value={historyHeight}
            onChange={setHistoryHeight}
            min={80}
            max={500}
            invert
          />
        ) : null}
        <section
          className="flex shrink-0 flex-col overflow-hidden border-t border-border"
          style={openSections.history ? { height: historyHeight } : undefined}
        >
          <button
            type="button"
            className="flex w-full shrink-0 items-center gap-1.5 bg-bg-subtle/40 px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider text-fg-muted hover:bg-bg-subtle"
            onClick={() => setOpenSections((s) => ({ ...s, history: !s.history }))}
            aria-expanded={openSections.history}
          >
            {openSections.history ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            <GitCommit className="h-3 w-3" strokeWidth={1.5} />
            {t("git.section.history")}
            <span className="ml-auto inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-bg-subtle px-1.5 text-[10px] font-semibold text-fg-subtle">
              {log?.commits.length ?? 0}
            </span>
          </button>
          {openSections.history ? (
            <HistoryList
              commits={log?.commits ?? []}
              currentBranch={status?.branch ?? null}
            />
          ) : null}
        </section>
          </>
        ) : null}
      </aside>
    </div>
  );
}

/** Phase N.5 — body fallback rendered when the panel can't show a
 *  normal source-control view: empty project, candidate (no-remote)
 *  repo, or a repo that hasn't been cloned into the workspace yet.
 *
 *  Lives INSIDE the panel chrome so the header's repo selector stays
 *  reachable — operator can switch to a sibling repo that IS cloned
 *  without leaving the panel. */
function EmptyStateBody({
  icon,
  title,
  description,
  footer,
}: {
  icon: React.ReactNode;
  title: string;
  description: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 overflow-auto px-6 py-8 text-center">
      {icon}
      <div className="max-w-[280px]">
        <p className="text-[13px] font-medium text-fg">{title}</p>
        <p className="mt-1 text-[11.5px] text-fg-muted">{description}</p>
      </div>
      {footer}
    </div>
  );
}

// ────────────────────────────────────────── components ──

function BranchMenu({
  branches,
  busy,
  newBranchInput,
  onNewBranchInput,
  onCheckout,
  onDelete,
  onClose,
}: {
  branches: GitBranchInfo[];
  busy: boolean;
  newBranchInput: string;
  onNewBranchInput: (v: string) => void;
  onCheckout: (
    name: string,
    opts?: { create?: boolean; startPoint?: string },
  ) => void;
  onDelete: (name: string) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const locals = branches.filter((b) => b.kind === "local");
  const remotes = branches.filter((b) => b.kind === "remote");
  return (
    <div className="absolute left-3 right-3 top-[calc(100%-2px)] z-10 max-h-[70vh] overflow-y-auto rounded-md border border-border bg-bg-elevated shadow-xl">
      <div className="flex items-center gap-1 border-b border-border bg-bg-subtle/40 px-2 py-1.5">
        <input
          autoFocus
          value={newBranchInput}
          onChange={(e) => onNewBranchInput(e.target.value)}
          placeholder={t("git.branch.create_placeholder")}
          className="flex-1 rounded border border-border bg-bg px-2 py-0.5 text-[11px] text-fg placeholder:text-fg-subtle"
          onKeyDown={(e) => {
            if (e.key === "Enter" && newBranchInput.trim()) {
              onCheckout(newBranchInput.trim(), { create: true });
            } else if (e.key === "Escape") {
              onClose();
            }
          }}
        />
        <Button
          size="sm"
          variant="primary"
          onClick={() =>
            newBranchInput.trim() &&
            onCheckout(newBranchInput.trim(), { create: true })
          }
          disabled={busy || !newBranchInput.trim()}
          title={t("git.branch.create_title")}
        >
          <Plus className="h-3 w-3" />
        </Button>
        <Button size="sm" variant="ghost" onClick={onClose} title="close">
          <X className="h-3 w-3" />
        </Button>
      </div>
      <SectionLabel>{t("git.branch.local")}</SectionLabel>
      {locals.length === 0 ? (
        <p className="px-3 py-1.5 text-[10.5px] text-fg-subtle">
          {t("git.branch.no_local")}
        </p>
      ) : (
        <ul>
          {locals.map((b) => (
            <BranchRow
              key={b.name}
              branch={b}
              busy={busy}
              onClick={() => onCheckout(b.name)}
              onDelete={b.current ? undefined : () => onDelete(b.name)}
            />
          ))}
        </ul>
      )}
      {remotes.length > 0 ? (
        <>
          <SectionLabel>{t("git.branch.remote")}</SectionLabel>
          <ul>
            {remotes.map((b) => {
              // checkout a remote → create a local tracking branch
              // from it. e.g. clicking origin/feat-x creates feat-x
              // local with origin/feat-x as upstream.
              const localName = b.name.replace(/^origin\//, "");
              return (
                <BranchRow
                  key={b.name}
                  branch={b}
                  busy={busy}
                  onClick={() =>
                    onCheckout(localName, { create: true, startPoint: b.name })
                  }
                  trailingHint={t("git.branch.checkout_remote_hint")}
                />
              );
            })}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-b border-border bg-bg px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
      {children}
    </div>
  );
}

function BranchRow({
  branch,
  busy,
  onClick,
  onDelete,
  trailingHint,
}: {
  branch: GitBranchInfo;
  busy: boolean;
  onClick: () => void;
  onDelete?: () => void;
  trailingHint?: string;
}) {
  const { t } = useI18n();
  return (
    <li className="group flex items-center gap-1.5 border-b border-border/40 px-2 py-1 hover:bg-bg-subtle">
      <button
        type="button"
        onClick={onClick}
        disabled={busy || branch.current}
        className={cn(
          "flex flex-1 items-center gap-1.5 truncate text-left",
          branch.current && "cursor-default",
        )}
      >
        {branch.current ? (
          <Check className="h-3 w-3 shrink-0 text-success" />
        ) : (
          <GitBranch className="h-3 w-3 shrink-0 text-fg-subtle" strokeWidth={1.5} />
        )}
        <span
          className={cn(
            "truncate font-mono text-[11.5px]",
            branch.current ? "font-semibold text-fg" : "text-fg-muted",
          )}
        >
          {branch.name}
        </span>
        {branch.upstream ? (
          <code className="shrink-0 text-[9.5px] text-fg-subtle">
            → {branch.upstream}
          </code>
        ) : null}
        {branch.ahead !== null && branch.ahead > 0 ? (
          <span className="text-[9.5px] text-success">↑{branch.ahead}</span>
        ) : null}
        {branch.behind !== null && branch.behind > 0 ? (
          <span className="text-[9.5px] text-warn">↓{branch.behind}</span>
        ) : null}
        {trailingHint ? (
          <span className="shrink-0 text-[9.5px] text-fg-subtle">{trailingHint}</span>
        ) : null}
      </button>
      {onDelete ? (
        <button
          type="button"
          onClick={onDelete}
          disabled={busy}
          className="invisible rounded p-0.5 text-fg-subtle hover:bg-danger/10 hover:text-danger group-hover:visible"
          title={t("git.branch.delete_title")}
        >
          <Trash2 className="h-3 w-3" />
        </button>
      ) : null}
    </li>
  );
}

function HistoryList({
  commits,
  currentBranch,
}: {
  commits: GitLogCommit[];
  currentBranch: string | null;
}) {
  const { t } = useI18n();
  if (commits.length === 0) {
    return (
      <p className="px-3 py-2 text-[10.5px] text-fg-subtle">{t("git.history.empty")}</p>
    );
  }
  return (
    <ul className="min-h-0 flex-1 overflow-y-auto py-1">
      {commits.map((c, i) => {
        const isMerge = c.parents.length > 1;
        const isHead = currentBranch && c.refs.some((r) => r.includes(currentBranch));
        return (
          <li
            key={c.sha}
            className="flex items-baseline gap-1.5 px-3 py-0.5 text-[11.5px]"
            title={`${c.author} <${c.author_email}>  ·  ${new Date(c.iso_date).toLocaleString()}`}
          >
            {/* Simple graph rail — bullet for normal, fork for merge. */}
            <span className="shrink-0 font-mono text-[10px] text-fg-subtle">
              {isMerge ? (
                <GitMerge className="inline h-3 w-3 text-accent" strokeWidth={1.5} />
              ) : (
                <span className={cn("inline-block", isHead && "text-success")}>
                  {i === 0 ? "●" : "│"}
                </span>
              )}
            </span>
            <code className="shrink-0 font-mono text-[10.5px] text-fg-subtle">
              {c.short_sha}
            </code>
            <span className="truncate text-fg">{c.subject}</span>
            {c.refs.length > 0 ? (
              <span className="flex shrink-0 gap-0.5">
                {c.refs.slice(0, 3).map((r) => (
                  <Badge
                    key={r}
                    tone={
                      r === currentBranch || r === `HEAD -> ${currentBranch}`
                        ? "success"
                        : r.startsWith("origin/")
                          ? "neutral"
                          : "accent"
                    }
                    className="text-[9px]"
                    title={r}
                  >
                    {prettyRef(r)}
                  </Badge>
                ))}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function prettyRef(r: string): string {
  // `HEAD -> main` -> show "main"; `tag: v1` -> show "v1"; remote branch
  // shows as-is.
  if (r.startsWith("HEAD -> ")) return r.slice("HEAD -> ".length);
  if (r.startsWith("tag: ")) return r.slice("tag: ".length);
  return r;
}

function SyncStateBadge({
  state,
  ahead,
  behind,
}: {
  state: "unknown" | "synced" | "ahead" | "behind" | "diverged" | "no-upstream";
  ahead: number;
  behind: number;
}) {
  const { t } = useI18n();
  if (state === "unknown") return null;
  if (state === "no-upstream") {
    // Already covered by the "⚠ upstream 없음" pill rendered to the
    // left in the header; suppress the redundant badge so the header
    // doesn't carry two warnings for the same condition.
    return null;
  }
  if (state === "synced") {
    return (
      <Badge tone="success" className="text-[9.5px]">
        {t("git.sync_state.synced")}
      </Badge>
    );
  }
  return (
    <span className="inline-flex items-center gap-0.5 font-mono text-[10.5px]">
      {ahead > 0 ? <span className="text-success">↑{ahead}</span> : null}
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
  // VS Code visually splits filename + folder so the eye locks onto
  // basename first (the part most diffs touch). `path/to/foo.ts` ⇒
  // `foo.ts` bold + `path/to` muted right after.
  const lastSlash = entry.path.lastIndexOf("/");
  const filename = lastSlash >= 0 ? entry.path.slice(lastSlash + 1) : entry.path;
  const dirname = lastSlash >= 0 ? entry.path.slice(0, lastSlash) : "";
  const dotColor =
    tone === "added"
      ? "bg-success"
      : tone === "modified"
        ? "bg-warn"
        : tone === "deleted"
          ? "bg-danger"
          : tone === "untracked"
            ? "bg-success" /* VS Code: untracked = green */
            : tone === "renamed"
              ? "bg-accent"
              : "bg-fg-subtle";
  const statusColor =
    tone === "added"
      ? "text-success"
      : tone === "modified"
        ? "text-warn"
        : tone === "deleted"
          ? "text-danger"
          : tone === "untracked"
            ? "text-success"
            : tone === "renamed"
              ? "text-accent"
              : "text-fg-subtle";
  return (
    <li
      className={cn(
        "group flex items-center gap-2 px-2.5 py-0.5",
        active ? "bg-accent/10" : "hover:bg-bg-subtle",
        !checked && "opacity-60",
      )}
    >
      {/* Status dot — single colored bullet on the left, VS Code's
          most distinctive Source-Control affordance. */}
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", dotColor)}
        title={`porcelain: ${entry.status}`}
      />
      {/* Click body — filename (bold) + dirname (muted) on a single
          truncated line. Click to open diff. */}
      <button
        type="button"
        onClick={onView}
        className="flex min-w-0 flex-1 items-baseline gap-1.5 text-left"
      >
        <span className="shrink-0 text-[12px] font-medium text-fg">
          {filename}
        </span>
        {dirname ? (
          <span
            className="truncate text-[11px] text-fg-subtle"
            title={entry.path}
          >
            {dirname}
          </span>
        ) : null}
      </button>
      {/* Hover-only tools: include-in-commit toggle + discard. */}
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="invisible h-3 w-3 group-hover:visible"
        title={
          checked ? t("git.commit.include_off") : t("git.commit.include_on")
        }
      />
      <button
        type="button"
        onClick={onDiscard}
        disabled={discarding}
        className="invisible shrink-0 rounded p-0.5 text-fg-subtle hover:bg-danger/10 hover:text-danger group-hover:visible"
        title={t("git.discard.title")}
      >
        <Trash2 className="h-3 w-3" />
      </button>
      {/* Status letter on the right — VS Code's M / A / D / U. */}
      <span
        className={cn(
          "shrink-0 w-4 text-center font-mono text-[11px] font-semibold",
          statusColor,
        )}
        title={`porcelain: ${entry.status}`}
      >
        {shortStatus(entry.status)}
      </span>
    </li>
  );
}

function shortStatus(porcelain: string): string {
  const t = porcelain.trim();
  if (t === "??") return "U";
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

/** Compress a raw git push / fetch error into a one-line operator
 * action item. The git CLI dumps multi-line hint blocks ("hint:
 * Updates were rejected because the remote contains work that you
 * do not have…") that scream the same thing as the first
 * `[rejected]` line — surface that line, drop the rest. The full
 * raw text is preserved as the `title` attribute by callers so
 * hover reveals the original.
 *
 * Common patterns:
 *   `[rejected] HEAD -> main (fetch first)` → "원격이 앞서 있음 — Pull 먼저"
 *   `Authentication failed for ...`         → "원격 인증 실패 — 토큰/권한 확인"
 *   `Repository not found`                  → "원격 저장소 못 찾음"
 *   `Everything up-to-date`                 → "변경 없음 — 보낼 커밋 없음"
 *
 * Anything we don't recognise falls back to the first non-empty
 * line of the raw output, capped at 200 chars. */
function friendlyPushError(raw: string, t: (k: never) => string): string {
  const text = raw || "";
  if (/\[rejected\]/.test(text) && /fetch first|non-fast-forward/i.test(text)) {
    return t("git.push.error.rejected_ff" as never);
  }
  if (/Authentication failed|could not read Username|403/i.test(text)) {
    return t("git.push.error.auth" as never);
  }
  if (/Repository not found|404/i.test(text)) {
    return t("git.push.error.not_found" as never);
  }
  if (/Everything up-to-date/i.test(text)) {
    return t("git.push.error.nothing" as never);
  }
  if (/permission denied|forbidden/i.test(text)) {
    return t("git.push.error.permission" as never);
  }
  // Fallback: first non-empty informative line, clipped.
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("hint:") && !l.startsWith("To "));
  return (lines[0] || text).slice(0, 200);
}
