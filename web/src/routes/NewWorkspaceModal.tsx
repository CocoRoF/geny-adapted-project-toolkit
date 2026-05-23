import { type FormEvent, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type CreateWorkspaceInput,
  type WorkspaceResponse,
  createWorkspace,
} from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";

interface Props {
  projectId: string;
  onClose: () => void;
  onCreated: (workspace: WorkspaceResponse) => void;
}

export function NewWorkspaceModal({ projectId, onClose, onCreated }: Props) {
  const { t } = useI18n();
  const [branch, setBranch] = useState("main");
  const [worktreePath, setWorktreePath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const payload: CreateWorkspaceInput =
      worktreePath.length > 0 ? { branch, worktree_path: worktreePath } : { branch };
    void createWorkspace(projectId, payload)
      .then((ws) => {
        onCreated(ws);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setError(`${err.code}: ${err.reason}`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        setSubmitting(false);
      });
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="new-workspace-title" className="modal">
      <div className="modal-content">
        <h2 id="new-workspace-title">{t("workspaces.create.title")}</h2>
        <form onSubmit={onSubmit} className="new-workspace-form">
          <label>
            <span>{t("workspaces.create.branch_label")}</span>
            <input
              type="text"
              value={branch}
              onChange={(e) => setBranch(e.currentTarget.value)}
              required
              maxLength={255}
            />
          </label>
          <label>
            <span>{t("workspaces.create.worktree_label")}</span>
            <input
              type="text"
              value={worktreePath}
              onChange={(e) => setWorktreePath(e.currentTarget.value)}
              maxLength={4096}
              placeholder="/workspace"
            />
          </label>
          <div className="modal-actions">
            <button type="button" onClick={onClose} disabled={submitting}>
              {t("workspaces.create.cancel")}
            </button>
            <button type="submit" disabled={submitting || branch.length === 0}>
              {submitting ? t("workspaces.create.creating") : t("workspaces.create.submit")}
            </button>
          </div>
          {error ? (
            <p role="alert" className="modal-error">
              {error}
            </p>
          ) : null}
        </form>
      </div>
    </div>
  );
}
