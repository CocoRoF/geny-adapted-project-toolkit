import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/api/client";
import { type WorkspaceResponse, getWorkspace } from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { DockviewShell } from "@/ide/DockviewShell";

type LoadState = "loading" | "ready" | "error";

/** `/projects/:pid/w/:wid` — the dockview IDE shell.
 *
 * Cycle 3.3b ships the layout grid + 4 presets + LocalStorage
 * persistence; panel contents are placeholders that subsequent cycles
 * replace one at a time. */
export function WorkspaceIde() {
  const { pid, wid } = useParams();
  const { t } = useI18n();
  const [workspace, setWorkspace] = useState<WorkspaceResponse | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!wid) return;
    setState("loading");
    getWorkspace(wid)
      .then((ws) => {
        setWorkspace(ws);
        setState("ready");
        setError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setError(`${err.code}: ${err.reason}`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
        setState("error");
      });
  }, [wid]);

  return (
    <section className="workspace-ide">
      <header className="workspace-ide-header">
        <Link to={`/projects/${pid ?? ""}`}>{t("nav.back_to_project")}</Link>
        <h2>
          {workspace ? (
            <>
              <span className="workspace-ide-branch">{workspace.branch}</span>
              <span className={`workspace-status workspace-status--${workspace.status}`}>
                {workspace.status}
              </span>
            </>
          ) : (
            t("workspace.title")
          )}
        </h2>
      </header>

      {state === "loading" ? <p>{t("app.loading")}</p> : null}
      {state === "error" ? (
        <p role="alert" className="workspace-ide-error">
          {error}
        </p>
      ) : null}
      {state === "ready" && wid ? <DockviewShell workspaceId={wid} /> : null}
    </section>
  );
}
