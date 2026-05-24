import { type FormEvent, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type CreateWorkspaceInput,
  type WorkspaceResponse,
  createWorkspace,
} from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
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
          <Input
            type="text"
            value={branch}
            onChange={(e) => setBranch(e.currentTarget.value)}
            required
            maxLength={255}
            placeholder="main"
          />
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
