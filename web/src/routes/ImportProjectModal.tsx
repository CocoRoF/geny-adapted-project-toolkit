import { type FormEvent, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type CreateProjectInput,
  type GitProvider,
  type ProjectResponse,
  createProject,
} from "@/api/projects";
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
import { Field, Input, Select } from "@/ui/Input";
import { Modal } from "@/ui/Modal";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (project: ProjectResponse) => void;
}

const GIT_PROVIDERS: GitProvider[] = ["github", "gitlab", "bitbucket", "other"];
const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$/;

/** Modal form for creating a project. Stacks fields vertically so the
 * labels never collide with inputs on small viewports. */
/** Phase N — renamed from NewProjectModal. Behaviour unchanged: this
 * is the "import an existing GitHub/GitLab/Bitbucket repo" path. The
 * "+ 새 프로젝트" dropdown puts this behind the "불러오기" menu item. */
export function ImportProjectModal({ open, onClose, onCreated }: Props) {
  const { t } = useI18n();
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [gitRemoteUrl, setGitRemoteUrl] = useState("");
  const [provider, setProvider] = useState<GitProvider>("github");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const payload: CreateProjectInput = {
      slug,
      display_name: displayName,
      git_remote_url: gitRemoteUrl,
      git_provider: provider,
    };
    void createProject(payload)
      .then((project) => onCreated(project))
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setError(`${err.code}: ${err.reason}`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => setSubmitting(false));
  }

  const slugValid = slug.length === 0 || SLUG_PATTERN.test(slug);
  const canSubmit =
    !submitting && slugValid && displayName.length > 0 && gitRemoteUrl.length > 0;

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!submitting) onClose();
      }}
      title={t("projects.create.title")}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            {t("projects.create.cancel")}
          </Button>
          <Button variant="primary" type="submit" form="new-project-form" disabled={!canSubmit}>
            {submitting ? t("projects.create.creating") : t("projects.create.submit")}
          </Button>
        </>
      }
    >
      <form id="new-project-form" onSubmit={onSubmit} className="flex flex-col gap-3.5">
        <Field label={t("projects.create.display_name_label")}>
          <Input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.currentTarget.value)}
            required
            maxLength={200}
            placeholder="My Project"
          />
        </Field>

        <Field
          label={t("projects.create.slug_label")}
          hint={t("projects.create.slug_hint")}
          error={!slugValid ? t("projects.create.slug_hint") : null}
        >
          <Input
            type="text"
            value={slug}
            onChange={(e) => setSlug(e.currentTarget.value)}
            required
            maxLength={120}
            aria-invalid={!slugValid}
            pattern={SLUG_PATTERN.source}
            placeholder="my-project"
          />
        </Field>

        <Field label={t("projects.create.git_remote_label")}>
          <Input
            type="url"
            value={gitRemoteUrl}
            onChange={(e) => setGitRemoteUrl(e.currentTarget.value)}
            required
            maxLength={2048}
            placeholder="https://github.com/owner/repo.git"
          />
        </Field>

        <Field label={t("projects.create.git_provider_label")}>
          <Select
            value={provider}
            onChange={(e) => setProvider(e.currentTarget.value as GitProvider)}
          >
            {GIT_PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
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
