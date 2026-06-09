import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  FolderGit2,
  GitBranch,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { ApiError } from "@/api/client";
import { getRemoteBranches } from "@/api/projects";
import {
  type ProjectRepository,
  listProjectRepositories,
} from "@/api/repositories";
import {
  type CreateWorkspaceInput,
  type WorkspaceResponse,
  createWorkspace,
} from "@/api/workspaces";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { Combobox } from "@/ui/Combobox";
import { cn } from "@/ui/cn";
import { Field, Input } from "@/ui/Input";
import { Modal } from "@/ui/Modal";

interface Props {
  open: boolean;
  projectId: string;
  onClose: () => void;
  onCreated: (workspace: WorkspaceResponse) => void;
}

/** One per-repo row state. ``include`` decides whether this repo is
 *  cloned into the workspace at all. ``branch`` is the per-repo branch
 *  pick (free-text fallback when ls-remote fails). ``branches`` is the
 *  cached suggestion list — null = not loaded, [] = loaded but the
 *  remote returned nothing. */
interface RepoRowState {
  repo: ProjectRepository;
  include: boolean;
  branch: string;
  branches: string[] | null;
  branchesLoading: boolean;
  branchesError: string | null;
  headBranch: string | null;
}

/** Phase N.5 — workspace creation modal.
 *
 *  Workspace identity is now a user-chosen ``name``, NOT a branch.
 *  Operator picks which of the project's repositories to include and
 *  what branch each is cloned at. Single-repo projects collapse to
 *  one row; empty projects (no repos) create a bare worktree.
 *
 *  Layout is two-column on `≥640px` viewports: name + summary on the
 *  left, the repo list on the right. Repo rows are full-width cards
 *  with the branch picker sitting beneath the include checkbox so the
 *  Combobox has room to render its dropdown without colliding with
 *  the refresh button. */
export function NewWorkspaceModal({ open, projectId, onClose, onCreated }: Props) {
  const [name, setName] = useState("");
  const [worktreePath, setWorktreePath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<RepoRowState[]>([]);
  const [reposLoaded, setReposLoaded] = useState(false);
  const userTouchedName = useRef(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    userTouchedName.current = false;
    setName("");
    setError(null);
    setReposLoaded(false);
    setRows([]);
    setWorktreePath("");
    void listProjectRepositories(projectId)
      .then((repos) => {
        if (cancelled) return;
        const initial: RepoRowState[] = repos.map((r) => ({
          repo: r,
          include: true,
          branch: r.default_branch ?? "",
          branches: null,
          branchesLoading: false,
          branchesError: null,
          headBranch: null,
        }));
        setRows(initial);
        setReposLoaded(true);
        if (!userTouchedName.current) {
          const now = new Date();
          const hh = String(now.getHours()).padStart(2, "0");
          const mm = String(now.getMinutes()).padStart(2, "0");
          setName(`ws-${hh}${mm}`);
        }
        initial.forEach((row, idx) => {
          if (!row.include || !row.repo.git_remote_url) return;
          void loadBranchesFor(idx, row.repo.id, false);
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setReposLoaded(true);
        if (err instanceof ApiError) setError(`${err.code}: ${err.reason}`);
        else setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId]);

  function loadBranchesFor(idx: number, repoId: string, refresh: boolean): void {
    setRows((prev) => {
      const next = prev.slice();
      if (!next[idx]) return prev;
      next[idx] = {
        ...next[idx],
        branchesLoading: true,
        branchesError: null,
      };
      return next;
    });
    getRemoteBranches(projectId, { refresh, repoId })
      .then((r) => {
        setRows((prev) => {
          const next = prev.slice();
          if (!next[idx]) return prev;
          const currentBranch = next[idx].branch;
          const shouldSeed =
            !currentBranch || currentBranch === next[idx].repo.default_branch;
          next[idx] = {
            ...next[idx],
            branches: r.branches,
            headBranch: r.head,
            branchesLoading: false,
            branch: shouldSeed && r.head ? r.head : currentBranch,
          };
          return next;
        });
      })
      .catch((err: unknown) => {
        setRows((prev) => {
          const next = prev.slice();
          if (!next[idx]) return prev;
          const msg =
            err instanceof ApiError
              ? err.reason ?? err.message
              : err instanceof Error
                ? err.message
                : String(err);
          next[idx] = {
            ...next[idx],
            branches: [],
            branchesLoading: false,
            branchesError: msg,
          };
          return next;
        });
      });
  }

  function toggleInclude(idx: number): void {
    setRows((prev) => {
      const next = prev.slice();
      if (!next[idx]) return prev;
      const wasIncluded = next[idx].include;
      next[idx] = { ...next[idx], include: !wasIncluded };
      if (
        !wasIncluded &&
        next[idx].branches === null &&
        next[idx].repo.git_remote_url
      ) {
        queueMicrotask(() => loadBranchesFor(idx, next[idx]!.repo.id, false));
      }
      return next;
    });
  }

  function setRowBranch(idx: number, value: string): void {
    setRows((prev) => {
      const next = prev.slice();
      if (!next[idx]) return prev;
      next[idx] = { ...next[idx], branch: value };
      return next;
    });
  }

  function selectAll(included: boolean): void {
    setRows((prev) =>
      prev.map((r, idx) => {
        if (r.include === included) return r;
        const next = { ...r, include: included };
        if (
          included &&
          next.branches === null &&
          next.repo.git_remote_url
        ) {
          queueMicrotask(() => loadBranchesFor(idx, next.repo.id, false));
        }
        return next;
      }),
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!name.trim()) {
      setError("워크스페이스 이름을 입력해주세요");
      return;
    }
    setError(null);
    setSubmitting(true);
    const selections = rows
      .filter((r) => r.include)
      .map((r) => ({
        repository_id: r.repo.id,
        branch: r.repo.git_remote_url ? r.branch : "",
      }));
    const payload: CreateWorkspaceInput = {
      name: name.trim(),
      selections,
      ...(worktreePath ? { worktree_path: worktreePath } : {}),
    };
    void createWorkspace(projectId, payload)
      .then(onCreated)
      .catch((err: unknown) => {
        if (err instanceof ApiError) setError(`${err.code}: ${err.reason}`);
        else setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setSubmitting(false));
  }

  const includedCount = rows.filter((r) => r.include).length;
  const canSubmit = !submitting && name.trim().length > 0;

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!submitting) onClose();
      }}
      title="새 워크스페이스 만들기"
      description="이름을 정하고, 이 워크스페이스에 함께 클론할 레포지토리 + 각각의 브랜치를 고르세요."
      size="2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            취소
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="new-workspace-form"
            disabled={!canSubmit}
          >
            {submitting ? (
              <>
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                만드는 중…
              </>
            ) : (
              <>
                <Check className="mr-1.5 h-3.5 w-3.5" />
                워크스페이스 만들기
                {includedCount > 0 ? (
                  <span className="ml-1.5 text-[11px] opacity-75">
                    · {includedCount}개 레포
                  </span>
                ) : null}
              </>
            )}
          </Button>
        </>
      }
    >
      <form id="new-workspace-form" onSubmit={onSubmit} className="flex flex-col gap-5">
        {/* ── Section 1: identity ────────────────────────────── */}
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_1fr]">
          <Field
            label="워크스페이스 이름"
            hint="이 프로젝트 안에서만 유니크하면 OK — 예: ws-1430, feature-test"
          >
            <Input
              type="text"
              value={name}
              onChange={(e) => {
                userTouchedName.current = true;
                setName(e.currentTarget.value);
              }}
              required
              maxLength={255}
              placeholder="ws-1430"
              autoFocus
            />
          </Field>
          <Field
            label="워크트리 경로 (선택)"
            hint="비워두면 /workspace/<slug>/<id> 로 자동 생성"
          >
            <Input
              type="text"
              value={worktreePath}
              onChange={(e) => setWorktreePath(e.currentTarget.value)}
              maxLength={4096}
              placeholder="/workspace/..."
            />
          </Field>
        </section>

        {/* ── Section 2: repository selection ────────────────── */}
        <section>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <div className="flex items-baseline gap-2">
              <h3 className="text-[13px] font-semibold text-fg">
                포함할 레포지토리
              </h3>
              {rows.length > 0 ? (
                <Badge tone="neutral" className="text-[10px]">
                  {includedCount} / {rows.length}
                </Badge>
              ) : null}
            </div>
            {rows.length > 1 ? (
              <div className="flex items-center gap-1 text-[11px]">
                <button
                  type="button"
                  onClick={() => selectAll(true)}
                  className="rounded px-1.5 py-0.5 text-fg-muted hover:bg-surface-hover hover:text-accent"
                >
                  전체 선택
                </button>
                <span className="text-fg-subtle">·</span>
                <button
                  type="button"
                  onClick={() => selectAll(false)}
                  className="rounded px-1.5 py-0.5 text-fg-muted hover:bg-surface-hover hover:text-accent"
                >
                  전체 해제
                </button>
              </div>
            ) : null}
          </div>

          {!reposLoaded ? (
            <div className="flex items-center gap-2 rounded-lg border border-dashed border-border bg-bg-subtle px-4 py-6 text-[12px] text-fg-muted">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              레포지토리 불러오는 중…
            </div>
          ) : rows.length === 0 ? (
            <div className="flex items-start gap-3 rounded-lg border border-border bg-bg-subtle px-4 py-3 text-[12px] text-fg-muted">
              <FolderGit2 className="mt-0.5 h-4 w-4 shrink-0 text-fg-subtle" />
              <div>
                <p className="font-medium text-fg">
                  이 프로젝트에는 레포가 없어요
                </p>
                <p className="mt-0.5 text-[11.5px]">
                  워크스페이스가 빈 폴더만 가집니다. 프로젝트 페이지에서 레포를 추가하면
                  다음 워크스페이스부터 포함할 수 있어요.
                </p>
              </div>
            </div>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {rows.map((row, idx) => (
                <RepoCard
                  key={row.repo.id}
                  row={row}
                  onToggle={() => toggleInclude(idx)}
                  onBranchChange={(v) => setRowBranch(idx, v)}
                  onRefreshBranches={() =>
                    loadBranchesFor(idx, row.repo.id, true)
                  }
                />
              ))}
            </ul>
          )}
        </section>

        {error ? (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{error}</span>
          </p>
        ) : null}
      </form>
    </Modal>
  );
}

/** One repo as a roomy card.
 *
 *  Row 1: checkbox + repo display_name + subpath chip + optional
 *          "빈 폴더" / "OSS" affordances on the right.
 *  Row 2: full-width branch picker when included AND remote exists.
 *  Row 3: status footer (loading hint, error hint).
 *
 *  The branch picker gets a full row of width so its Combobox dropdown
 *  has the horizontal room it needs without clipping into the refresh
 *  button. Excluded rows collapse to just the header for density. */
function RepoCard({
  row,
  onToggle,
  onBranchChange,
  onRefreshBranches,
}: {
  row: RepoRowState;
  onToggle: () => void;
  onBranchChange: (v: string) => void;
  onRefreshBranches: () => void;
}) {
  const hasRemote = !!row.repo.git_remote_url;
  return (
    <li
      className={cn(
        "rounded-lg border bg-bg-elevated transition-colors",
        row.include
          ? "border-accent/40 ring-1 ring-accent/15"
          : "border-border opacity-75",
      )}
    >
      {/* Header — clickable everywhere except the chips. */}
      <label
        className="flex cursor-pointer items-center gap-3 px-3.5 py-2.5"
        htmlFor={`repo-${row.repo.id}`}
      >
        <input
          id={`repo-${row.repo.id}`}
          type="checkbox"
          checked={row.include}
          onChange={onToggle}
          className="h-4 w-4 shrink-0 cursor-pointer accent-accent"
        />
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <FolderGit2 className="h-4 w-4 shrink-0 text-fg-muted" strokeWidth={1.5} />
          <span className="truncate text-[13px] font-medium text-fg">
            {row.repo.display_name}
          </span>
          {row.repo.subpath ? (
            <code className="rounded bg-bg-subtle px-1.5 py-0.5 text-[10.5px] text-fg-muted">
              {row.repo.subpath}/
            </code>
          ) : (
            <code className="rounded bg-bg-subtle px-1.5 py-0.5 text-[10.5px] text-fg-subtle">
              루트
            </code>
          )}
        </div>
        {!hasRemote ? (
          <Badge tone="warn" className="text-[9.5px]">
            빈 폴더 · git 없음
          </Badge>
        ) : null}
      </label>

      {/* Body — full-width branch picker, only when included + remote. */}
      {row.include && hasRemote ? (
        <div className="border-t border-border/60 px-3.5 py-2.5">
          <div className="mb-1 flex items-center gap-1.5 text-[10.5px] font-medium uppercase tracking-wider text-fg-subtle">
            <GitBranch className="h-3 w-3" strokeWidth={1.5} />
            브랜치
            {row.branchesLoading ? (
              <Loader2 className="h-3 w-3 animate-spin text-fg-subtle" />
            ) : null}
          </div>
          <div className="flex items-stretch gap-2">
            <div className="min-w-0 flex-1">
              {row.branches !== null && row.branches.length > 0 ? (
                <Combobox
                  value={row.branch}
                  onChange={onBranchChange}
                  options={row.branches}
                  loading={row.branchesLoading}
                  placeholder={row.headBranch ?? "main"}
                  maxLength={255}
                />
              ) : (
                <Input
                  type="text"
                  value={row.branch}
                  onChange={(e) => onBranchChange(e.currentTarget.value)}
                  maxLength={255}
                  placeholder={row.headBranch ?? "main"}
                />
              )}
            </div>
            <button
              type="button"
              onClick={onRefreshBranches}
              disabled={row.branchesLoading}
              title="원격에서 브랜치 목록 다시 불러오기"
              className="inline-flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-md border border-border bg-bg text-fg-muted hover:bg-surface-hover hover:text-accent disabled:opacity-50"
            >
              <RefreshCw
                className={cn(
                  "h-3.5 w-3.5",
                  row.branchesLoading && "animate-spin",
                )}
                strokeWidth={1.5}
              />
            </button>
          </div>
          {row.branchesError ? (
            <p className="mt-1.5 flex items-start gap-1.5 text-[10.5px] text-fg-subtle">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warn" />
              <span>
                브랜치 자동 불러오기 실패 — 직접 입력하세요.
                <span className="ml-1 text-fg-subtle/70">({row.branchesError})</span>
              </span>
            </p>
          ) : row.headBranch ? (
            <p className="mt-1.5 text-[10.5px] text-fg-subtle">
              원격 기본 브랜치: <code className="text-fg-muted">{row.headBranch}</code>
            </p>
          ) : null}
        </div>
      ) : null}

      {/* Body — empty/candidate repo state. */}
      {row.include && !hasRemote ? (
        <div className="border-t border-border/60 px-3.5 py-2.5 text-[11px] text-fg-subtle">
          이 레포는 원격 URL이 없어서 클론하지 않고 빈 폴더만 만듭니다.
          워크스페이스 안 터미널에서 <code className="text-fg-muted">git init</code>
          으로 시작할 수 있어요.
        </div>
      ) : null}
    </li>
  );
}
