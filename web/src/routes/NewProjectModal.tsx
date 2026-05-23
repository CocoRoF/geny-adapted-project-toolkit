import { type FormEvent, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type CreateProjectInput,
  type GitProvider,
  type ProjectResponse,
  createProject,
} from "@/api/projects";
import type { OrgMembershipSummary } from "@/api/auth";
import { useI18n } from "@/app/providers/i18n-context";

interface Props {
  orgs: OrgMembershipSummary[];
  onClose: () => void;
  onCreated: (project: ProjectResponse) => void;
}

const GIT_PROVIDERS: GitProvider[] = ["github", "gitlab", "bitbucket", "other"];

const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$/;

/** Minimal "create project" modal — manual field entry. The GitHub
 * Device Flow integration (auto-detect compose, repo selector) lands
 * once the backend exposes `/api/integrations/github/*` endpoints (a
 * later cycle of M1-E3). For now the user pastes the remote URL +
 * picks the org. */
export function NewProjectModal({ orgs, onClose, onCreated }: Props) {
  const { t } = useI18n();
  const [orgId, setOrgId] = useState<string>(orgs[0]?.org_id ?? "");
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [gitRemoteUrl, setGitRemoteUrl] = useState("");
  const [provider, setProvider] = useState<GitProvider>("github");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If the user signs in mid-modal-open and orgs arrive late, snap to
  // the first one.
  useEffect(() => {
    if (orgId === "" && orgs[0]) setOrgId(orgs[0].org_id);
  }, [orgs, orgId]);

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const payload: CreateProjectInput = {
      org_id: orgId,
      slug,
      display_name: displayName,
      git_remote_url: gitRemoteUrl,
      git_provider: provider,
    };
    void createProject(payload)
      .then((project) => {
        onCreated(project);
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

  const slugValid = slug.length === 0 || SLUG_PATTERN.test(slug);

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="new-project-title" className="modal">
      <div className="modal-content">
        <h2 id="new-project-title">{t("projects.create.title")}</h2>

        {orgs.length === 0 ? (
          <p role="alert">{t("projects.create.no_orgs")}</p>
        ) : (
          <form onSubmit={onSubmit} className="new-project-form">
            <label>
              <span>{t("projects.create.org_label")}</span>
              <select value={orgId} onChange={(e) => setOrgId(e.currentTarget.value)} required>
                {orgs.map((o) => (
                  <option key={o.org_id} value={o.org_id}>
                    {o.org_slug}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>{t("projects.create.display_name_label")}</span>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.currentTarget.value)}
                required
                maxLength={200}
              />
            </label>

            <label>
              <span>{t("projects.create.slug_label")}</span>
              <input
                type="text"
                value={slug}
                onChange={(e) => setSlug(e.currentTarget.value)}
                required
                maxLength={120}
                aria-invalid={!slugValid}
                pattern={SLUG_PATTERN.source}
              />
              <small>{t("projects.create.slug_hint")}</small>
            </label>

            <label>
              <span>{t("projects.create.git_remote_label")}</span>
              <input
                type="url"
                value={gitRemoteUrl}
                onChange={(e) => setGitRemoteUrl(e.currentTarget.value)}
                required
                maxLength={2048}
                placeholder="https://github.com/owner/repo.git"
              />
            </label>

            <label>
              <span>{t("projects.create.git_provider_label")}</span>
              <select
                value={provider}
                onChange={(e) => setProvider(e.currentTarget.value as GitProvider)}
              >
                {GIT_PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>

            <div className="modal-actions">
              <button type="button" onClick={onClose} disabled={submitting}>
                {t("projects.create.cancel")}
              </button>
              <button
                type="submit"
                disabled={submitting || !slugValid || orgId === "" || displayName.length === 0}
              >
                {submitting ? t("projects.create.creating") : t("projects.create.submit")}
              </button>
            </div>

            {error ? (
              <p role="alert" className="modal-error">
                {error}
              </p>
            ) : null}
          </form>
        )}
      </div>
    </div>
  );
}
