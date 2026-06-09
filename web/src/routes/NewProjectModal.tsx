import { type FormEvent, useEffect, useState } from "react";

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
  /** Phase N.4 — when true, hides the git URL field entirely and
   *  hard-pins ``emptyMode`` so the modal becomes the "빈 프로젝트"
   *  entry point. The caller (ProjectsIndex dropdown) flips this
   *  so the operator can't accidentally swap modes mid-form. */
  forceEmpty?: boolean;
}

const GIT_PROVIDERS: GitProvider[] = ["github", "gitlab", "bitbucket", "other"];
const SLUG_PATTERN = /^[a-z0-9](?:[-a-z0-9]{0,118}[a-z0-9])?$/;

/** Modal form for creating a project. Stacks fields vertically so the
 * labels never collide with inputs on small viewports. */
export function NewProjectModal({ open, onClose, onCreated, forceEmpty = false }: Props) {
  const { t } = useI18n();
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [gitRemoteUrl, setGitRemoteUrl] = useState("");
  const [provider, setProvider] = useState<GitProvider>("github");
  // Phase N.4 — "빈 프로젝트" mode. When checked, git URL goes empty
  // and the backend skips the auto ProjectRepository row. The
  // operator then designs the repo layout via ProjectDetail's
  // Repositories section after creation. ``forceEmpty=true`` from
  // the dropdown entry pins this on so the checkbox + URL field
  // are both hidden — the dropdown's "빈 프로젝트" item is itself
  // the mode toggle.
  const [emptyMode, setEmptyMode] = useState(forceEmpty);
  // Keep the controlled state in sync when the modal is reused
  // across different entry points (forceEmpty true vs false).
  useEffect(() => {
    setEmptyMode(forceEmpty);
  }, [forceEmpty]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const payload: CreateProjectInput = {
      slug,
      display_name: displayName,
      git_remote_url: emptyMode ? "" : gitRemoteUrl,
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
  // Phase N.4 — empty mode skips the URL requirement so the operator
  // can land on ProjectDetail's Repositories section to add repos.
  // Non-empty modes (legacy import) still require a URL.
  const canSubmit =
    !submitting &&
    slugValid &&
    displayName.length > 0 &&
    (emptyMode || gitRemoteUrl.length > 0);

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!submitting) onClose();
      }}
      title={forceEmpty ? "빈 프로젝트 만들기" : t("projects.create.title")}
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

        {/* Phase N.4 — "Empty project" UX.
            ``forceEmpty`` from the dropdown's "빈 프로젝트" entry
            replaces the checkbox with a static info banner; the
            operator already opted in by picking that menu item.
            When NOT forced (legacy URL-entry path), the checkbox
            still appears so a single modal serves both flows. */}
        {forceEmpty ? (
          <p className="rounded-md border border-accent/40 bg-accent/5 px-3 py-2 text-[12px] text-fg-muted">
            <strong className="text-fg">빈 프로젝트 모드</strong> — git 레포 없이
            워크스페이스만 만들어요. 생성 후 프로젝트 페이지의 "레포지토리" 섹션에서
            여러 git URL 을 자유롭게 추가할 수 있습니다.
          </p>
        ) : (
          <label className="flex items-center gap-2 rounded-md border border-border bg-bg-elevated px-3 py-2 text-[12px]">
            <input
              type="checkbox"
              checked={emptyMode}
              onChange={(e) => setEmptyMode(e.currentTarget.checked)}
            />
            <span className="font-medium text-fg">빈 프로젝트로 시작</span>
            <span className="text-[11px] text-fg-subtle">
              (git 없는 폴더 + 나중에 레포 추가)
            </span>
          </label>
        )}

        {!emptyMode ? (
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
        ) : null}

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
