import { type FormEvent, useEffect, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { getRemoteBranches } from "@/api/projects";
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

export function NewWorkspaceModal({ open, projectId, onClose, onCreated }: Props) {
  const { t } = useI18n();
  const [branch, setBranch] = useState("main");
  const [worktreePath, setWorktreePath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Remote-branches state. `null` = not loaded yet / hidden,
  // `[]` = loaded but the remote advertised zero heads (very rare),
  // a non-empty array = use the combobox path. `head` is the remote's
  // default branch, used to seed `branch` on first open.
  const [branches, setBranches] = useState<string[] | null>(null);
  const [headBranch, setHeadBranch] = useState<string | null>(null);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [branchesError, setBranchesError] = useState<string | null>(null);
  const userTouchedBranch = useRef(false);

  // Prefetch on open. We re-fetch every time the modal opens (cheap
  // thanks to the server-side 60s cache) so a newly-pushed branch
  // shows up on the next attempt without a manual refresh in the
  // common case.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    userTouchedBranch.current = false;
    setBranches(null);
    setHeadBranch(null);
    setBranchesError(null);
    setBranchesLoading(true);
    getRemoteBranches(projectId)
      .then((r) => {
        if (cancelled) return;
        setBranches(r.branches);
        setHeadBranch(r.head);
        // Seed the input with the remote's default branch — unless the
        // user already started typing something else.
        if (!userTouchedBranch.current && r.head) setBranch(r.head);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // The endpoint returns 502 + `git.ls_remote_failed` on remote
        // errors. We surface the reason as a small hint and degrade
        // to a plain Input so the operator can still create the
        // workspace by typing a branch name.
        if (err instanceof ApiError) {
          setBranchesError(err.reason ?? err.message);
        } else {
          setBranchesError(err instanceof Error ? err.message : String(err));
        }
        setBranches([]);
      })
      .finally(() => {
        if (!cancelled) setBranchesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId]);

  function refreshBranches(): void {
    setBranchesError(null);
    setBranchesLoading(true);
    getRemoteBranches(projectId, { refresh: true })
      .then((r) => {
        setBranches(r.branches);
        setHeadBranch(r.head);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) setBranchesError(err.reason ?? err.message);
        else setBranchesError(err instanceof Error ? err.message : String(err));
        setBranches((prev) => prev ?? []);
      })
      .finally(() => setBranchesLoading(false));
  }

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const payload: CreateWorkspaceInput =
      worktreePath.length > 0 ? { branch, worktree_path: worktreePath } : { branch };
    void createWorkspace(projectId, payload)
      .then(onCreated)
      .catch((err: unknown) => {
        if (err instanceof ApiError) setError(`${err.code}: ${err.reason}`);
        else setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setSubmitting(false));
  }

  // Decide which control to render. The combobox is only worth using
  // when we actually have suggestions; otherwise we degrade to a plain
  // Input so the modal stays functional even when the remote rejects
  // ls-remote (private repo without a stored token, DNS hiccup, etc.).
  const showCombobox = branches !== null && (branches.length > 0 || branchesLoading);

  let branchHint: string | null = null;
  if (branchesError) {
    branchHint = `${t("workspaces.create.branch_fallback_hint")} (${branchesError})`;
  } else if (headBranch) {
    branchHint = t("workspaces.create.branch_default_hint").replace("{branch}", headBranch);
  } else if (branchesLoading) {
    branchHint = t("workspaces.create.branch_loading");
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!submitting) onClose();
      }}
      title={t("workspaces.create.title")}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            {t("workspaces.create.cancel")}
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="new-workspace-form"
            disabled={submitting || branch.length === 0}
          >
            {submitting ? t("workspaces.create.creating") : t("workspaces.create.submit")}
          </Button>
        </>
      }
    >
      <form id="new-workspace-form" onSubmit={onSubmit} className="flex flex-col gap-3.5">
        <Field label={t("workspaces.create.branch_label")}>
          {showCombobox ? (
            <Combobox
              value={branch}
              onChange={(next) => {
                userTouchedBranch.current = true;
                setBranch(next);
              }}
              options={branches ?? []}
              loading={branchesLoading}
              placeholder="main"
              required
              maxLength={255}
              hint={branchHint}
              noMatchHint={t("workspaces.create.branch_no_match")}
              trailing={
                <button
                  type="button"
                  onClick={refreshBranches}
                  disabled={branchesLoading}
                  className="ml-1 rounded-md border border-border px-2 py-1 text-[11px] text-fg-muted hover:bg-surface-hover disabled:opacity-50"
                  title={t("workspaces.create.branch_refresh")}
                >
                  {t("workspaces.create.branch_refresh")}
                </button>
              }
            />
          ) : (
            <>
              <Input
                type="text"
                value={branch}
                onChange={(e) => {
                  userTouchedBranch.current = true;
                  setBranch(e.currentTarget.value);
                }}
                required
                maxLength={255}
                placeholder="main"
              />
              {branchHint ? (
                <p className="mt-1 text-[11px] text-fg-subtle">{branchHint}</p>
              ) : null}
            </>
          )}
        </Field>
        <Field label={t("workspaces.create.worktree_label")}>
          <Input
            type="text"
            value={worktreePath}
            onChange={(e) => setWorktreePath(e.currentTarget.value)}
            maxLength={4096}
            placeholder="/workspace (optional)"
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
