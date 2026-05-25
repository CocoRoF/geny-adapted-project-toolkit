import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowUpFromLine,
  Check,
  GitPullRequest,
  Loader2,
  RotateCcw,
  Send,
} from "lucide-react";

import {
  createPr,
  type CreatePrResponse,
  getGitDiff,
  getGitStatus,
  gitCommit,
  gitPush,
  type GitPushResponse,
  type GitStatusEntry,
  type GitStatusResponse,
} from "@/api/git";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";

interface Props {
  workspaceId: string;
}

type FlashKind = "info" | "error";

/** Source-control panel. Pulls `git status` from inside the
 * workspace sandbox, lets the user check/uncheck files, drafts a
 * commit message, commits, pushes, then optionally fires `gh pr
 * create`. All four actions run as separate, idempotent endpoint
 * calls so a failure mid-flow leaves the repo in a recoverable
 * state. */
export function GitPanel({ workspaceId }: Props) {
  const [status, setStatus] = useState<GitStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [flash, setFlash] = useState<{ kind: FlashKind; text: string } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState("");
  const [committing, setCommitting] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [creatingPr, setCreatingPr] = useState(false);
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [diff, setDiff] = useState<{ path: string; text: string } | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setFlash(null);
    try {
      const s = await getGitStatus(workspaceId);
      setStatus(s);
      // Pre-select all changed files when refreshing — the common
      // case is "commit everything I changed."
      setSelected(new Set(s.entries.map((e) => e.path)));
    } catch (e) {
      setFlash({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const dirty = useMemo(
    () => (status?.entries.length ?? 0) > 0,
    [status?.entries.length],
  );

  const toggle = (path: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const handleViewDiff = useCallback(
    async (path: string) => {
      try {
        const d = await getGitDiff(workspaceId, path);
        setDiff({ path, text: d.diff || "(no diff — file is identical or untracked)" });
      } catch (e) {
        setFlash({ kind: "error", text: e instanceof Error ? e.message : String(e) });
      }
    },
    [workspaceId],
  );

  const handleCommit = useCallback(async () => {
    if (!message.trim()) {
      setFlash({ kind: "error", text: "커밋 메시지를 입력하세요." });
      return;
    }
    setCommitting(true);
    setFlash(null);
    try {
      const r = await gitCommit(workspaceId, {
        message,
        paths: Array.from(selected),
      });
      setFlash({
        kind: "info",
        text: `커밋 ${r.sha}${r.branch ? ` (${r.branch})` : ""}`,
      });
      setMessage("");
      await refresh();
    } catch (e) {
      setFlash({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setCommitting(false);
    }
  }, [message, selected, workspaceId, refresh]);

  const handlePush = useCallback(async () => {
    setPushing(true);
    setFlash(null);
    try {
      const r: GitPushResponse = await gitPush(workspaceId, {});
      setFlash({ kind: "info", text: `푸시 완료 → origin/${r.branch ?? "?"}` });
      await refresh();
    } catch (e) {
      setFlash({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setPushing(false);
    }
  }, [refresh, workspaceId]);

  const handleCreatePr = useCallback(async () => {
    setCreatingPr(true);
    setFlash(null);
    setPrUrl(null);
    try {
      const r: CreatePrResponse = await createPr(workspaceId, {
        title: message.split("\n")[0]?.trim() || "GAPT-authored changes",
        body: message,
        base: "main",
      });
      setPrUrl(r.url);
      setFlash({ kind: "info", text: `PR #${r.number} 생성됨` });
    } catch (e) {
      setFlash({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setCreatingPr(false);
    }
  }, [message, workspaceId]);

  return (
    <div className="grid h-full grid-cols-[minmax(280px,360px)_1fr]">
      <aside className="flex h-full flex-col overflow-hidden border-r border-border bg-bg-elevated">
        <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2 text-[12px] font-semibold text-fg">
          {status?.branch ? (
            <Badge tone="accent">{status.branch}</Badge>
          ) : (
            <Badge tone="neutral">no branch</Badge>
          )}
          {status?.ahead ? (
            <span className="text-[11px] text-fg-muted">↑{status.ahead}</span>
          ) : null}
          {status?.behind ? (
            <span className="text-[11px] text-fg-muted">↓{status.behind}</span>
          ) : null}
          <Button
            variant="ghost"
            onClick={refresh}
            disabled={loading}
            title="새로고침"
            className="ml-auto"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
          </Button>
        </header>
        <div className="flex-1 overflow-y-auto py-1">
          {!status || status.entries.length === 0 ? (
            <p className="px-3 py-3 text-[11px] text-fg-subtle">
              {loading
                ? "상태 불러오는 중…"
                : status?.entries.length === 0
                  ? "변경 사항 없음 — 모두 깨끗."
                  : "?"}
            </p>
          ) : (
            <ul className="space-y-0.5 px-1.5">
              {status.entries.map((e: GitStatusEntry) => (
                <li key={e.path} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={selected.has(e.path)}
                    onChange={() => toggle(e.path)}
                  />
                  <span
                    className="w-7 shrink-0 rounded bg-bg px-1 text-center font-mono text-[10px] text-fg-subtle"
                    title={`porcelain: ${e.status}`}
                  >
                    {e.status.replace(/\s/g, "·").trim() || "·"}
                  </span>
                  <button
                    type="button"
                    onClick={() => handleViewDiff(e.path)}
                    className="flex-1 truncate text-left font-mono text-[12px] text-fg hover:text-accent"
                  >
                    {e.path}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="border-t border-border p-2">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.currentTarget.value)}
            placeholder="커밋 메시지 (첫 줄이 제목)…"
            rows={4}
            className="w-full resize-none rounded-md border border-border bg-bg px-2 py-1 font-mono text-[12px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
          />
          {flash ? (
            <p
              className={
                "mt-1 text-[11px] " +
                (flash.kind === "error" ? "text-danger" : "text-accent")
              }
            >
              {flash.text}
            </p>
          ) : null}
          <div className="mt-1 flex flex-wrap gap-1">
            <Button
              variant="primary"
              onClick={handleCommit}
              disabled={committing || !dirty || !message.trim()}
            >
              {committing ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Check className="mr-1 h-3 w-3" />
              )}
              커밋
            </Button>
            <Button variant="secondary" onClick={handlePush} disabled={pushing}>
              {pushing ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <ArrowUpFromLine className="mr-1 h-3 w-3" />
              )}
              푸시
            </Button>
            <Button
              variant="secondary"
              onClick={handleCreatePr}
              disabled={creatingPr || !message.trim()}
              title="GitHub PR 생성"
            >
              {creatingPr ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <GitPullRequest className="mr-1 h-3 w-3" />
              )}
              PR
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
      <main className="flex h-full flex-col overflow-hidden bg-bg">
        <header className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-3 py-2">
          <span className="font-mono text-[12px] text-fg">
            {diff ? diff.path : "파일을 선택하면 diff가 표시됩니다"}
          </span>
        </header>
        <pre className="flex-1 overflow-auto whitespace-pre-wrap break-all bg-bg px-4 py-3 font-mono text-[11px] leading-relaxed text-fg-muted">
          {diff
            ? diff.text
            : status?.recent_commits.length
              ? "최근 커밋:\n" +
                status.recent_commits.map((c) => `${c.sha}  ${c.message}`).join("\n")
              : ""}
        </pre>
      </main>
    </div>
  );
}
