import { type FormEvent, useEffect, useRef, useState } from "react";

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
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
import { Combobox } from "@/ui/Combobox";
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
 *  Operator picks which of the project's repositories to include +
 *  what branch each is cloned at. Single-repo projects collapse to
 *  one row with the branch picker — still feels like the legacy UX.
 *  Empty projects (no repos) create a bare worktree with no clones.
 */
export function NewWorkspaceModal({ open, projectId, onClose, onCreated }: Props) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [worktreePath, setWorktreePath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<RepoRowState[]>([]);
  const [reposLoaded, setReposLoaded] = useState(false);
  // Avoid re-seeding the name field after the operator has typed.
  const userTouchedName = useRef(false);

  // Load repos + autopopulate name when the modal opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    userTouchedName.current = false;
    setName("");
    setError(null);
    setReposLoaded(false);
    setRows([]);
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
        // Auto-suggest a name: short timestamp-ish slug. Operator can
        // overtype before submit. ``ws-<HHmm>`` is short enough to fit
        // the workspace card chip but stable enough across rapid
        // re-creates that two siblings don't collide.
        if (!userTouchedName.current) {
          const now = new Date();
          const hh = String(now.getHours()).padStart(2, "0");
          const mm = String(now.getMinutes()).padStart(2, "0");
          setName(`ws-${hh}${mm}`);
        }
        // Kick off branch ls-remote per included repo in parallel.
        // Failure per row is non-fatal — that row falls back to free-
        // text. We don't wait for these before showing the form.
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
          // If the operator hasn't touched the branch field yet, seed
          // it with the remote's HEAD on first successful fetch.
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
      // Lazy-load branches on first inclusion of a repo whose
      // remote we never hit. ``branches === null`` means "never
      // tried"; ``[]`` means "tried and got nothing".
      if (
        !wasIncluded &&
        next[idx].branches === null &&
        next[idx].repo.git_remote_url
      ) {
        // schedule outside the setter so we don't re-enter
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
        // Empty branch for empty/candidate repos — backend interprets
        // as "skip clone, just make the subdir".
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
      title="새 워크스페이스"
      size="lg"
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
            {submitting ? "만드는 중…" : "워크스페이스 만들기"}
          </Button>
        </>
      }
    >
      <form id="new-workspace-form" onSubmit={onSubmit} className="flex flex-col gap-3.5">
        <Field label="워크스페이스 이름">
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
          />
        </Field>

        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <label className="text-[12px] font-semibold text-fg">
              포함할 레포지토리
            </label>
            <span className="text-[11px] text-fg-subtle">
              {includedCount} / {rows.length}
            </span>
          </div>
          {!reposLoaded ? (
            <p className="px-1 text-[11.5px] text-fg-subtle">불러오는 중…</p>
          ) : rows.length === 0 ? (
            <p className="rounded-md border border-border bg-bg-subtle px-3 py-2 text-[11.5px] text-fg-muted">
              이 프로젝트에는 레포가 없어요 — 워크스페이스가 빈 폴더만 가집니다.
              프로젝트 페이지에서 레포를 추가하면 다음 워크스페이스부터 포함할 수 있어요.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {rows.map((row, idx) => (
                <li
                  key={row.repo.id}
                  className="flex flex-col gap-1.5 rounded-md border border-border bg-bg-elevated px-3 py-2"
                >
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={row.include}
                      onChange={() => toggleInclude(idx)}
                      id={`repo-${row.repo.id}`}
                    />
                    <label
                      htmlFor={`repo-${row.repo.id}`}
                      className="flex-1 truncate text-[12.5px] font-medium text-fg"
                    >
                      {row.repo.display_name}
                      {row.repo.subpath ? (
                        <code className="ml-1.5 text-[11px] text-fg-subtle">
                          {row.repo.subpath}/
                        </code>
                      ) : null}
                    </label>
                    {!row.repo.git_remote_url ? (
                      <span className="text-[10.5px] text-fg-subtle">
                        빈 폴더 (git 없음)
                      </span>
                    ) : null}
                  </div>
                  {row.include && row.repo.git_remote_url ? (
                    <div className="flex items-center gap-1.5 pl-6">
                      <span className="text-[11px] text-fg-subtle shrink-0">
                        브랜치
                      </span>
                      <div className="flex-1 min-w-0">
                        {row.branches !== null && row.branches.length > 0 ? (
                          <Combobox
                            value={row.branch}
                            onChange={(v) => setRowBranch(idx, v)}
                            options={row.branches}
                            loading={row.branchesLoading}
                            placeholder={row.headBranch ?? "main"}
                            maxLength={255}
                          />
                        ) : (
                          <Input
                            type="text"
                            value={row.branch}
                            onChange={(e) => setRowBranch(idx, e.currentTarget.value)}
                            maxLength={255}
                            placeholder={row.headBranch ?? "main"}
                          />
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => loadBranchesFor(idx, row.repo.id, true)}
                        disabled={row.branchesLoading}
                        className="rounded-md border border-border px-2 py-1 text-[10.5px] text-fg-muted hover:bg-surface-hover disabled:opacity-50"
                        title="원격에서 브랜치 다시 불러오기"
                      >
                        ↻
                      </button>
                    </div>
                  ) : null}
                  {row.branchesError ? (
                    <p className="pl-6 text-[10.5px] text-fg-subtle">
                      브랜치 자동 불러오기 실패 — 직접 입력하세요. ({row.branchesError})
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>

        <Field label="워크트리 경로 (선택)">
          <Input
            type="text"
            value={worktreePath}
            onChange={(e) => setWorktreePath(e.currentTarget.value)}
            maxLength={4096}
            placeholder="/workspace (비워두면 자동 생성)"
          />
        </Field>

        {error ? (
          <p
            role="alert"
            className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
          >
            {error}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}
