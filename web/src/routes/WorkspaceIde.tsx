import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, GitBranch } from "lucide-react";

import { ApiError } from "@/api/client";
import { type WorkspaceResponse, getWorkspace } from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { DockviewShell } from "@/ide/DockviewShell";
import { Badge } from "@/ui/Badge";

type LoadState = "loading" | "ready" | "error";

const STATUS_TONE: Record<string, "neutral" | "success" | "warn" | "danger" | "accent"> = {
  running: "success",
  paused: "warn",
  failed: "danger",
  creating: "accent",
  stopped: "neutral",
  archived: "neutral",
};

/** `/projects/:pid/w/:wid` — the dockview IDE shell. */
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
    <section className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-border bg-bg-elevated px-4 py-2">
        <Link
          to={`/projects/${pid ?? ""}`}
          className="inline-flex items-center gap-1 text-[12px] text-fg-muted hover:text-fg"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          {t("nav.back_to_project")}
        </Link>
        <div className="flex items-center gap-2 border-l border-border pl-3">
          {workspace ? (
            <>
              <GitBranch className="h-3.5 w-3.5 text-fg-muted" />
              <span className="text-[13px] font-semibold text-fg">{workspace.branch}</span>
              <Badge tone={STATUS_TONE[workspace.status] ?? "neutral"}>{workspace.status}</Badge>
            </>
          ) : (
            <span className="text-[13px] font-semibold text-fg">{t("workspace.title")}</span>
          )}
        </div>
      </header>

      {state === "loading" ? (
        <p className="px-4 py-6 text-[13px] text-fg-muted">{t("app.loading")}</p>
      ) : null}
      {state === "error" ? (
        <p
          role="alert"
          className="mx-4 my-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] text-danger"
        >
          {error}
        </p>
      ) : null}
      {state === "ready" && wid && pid ? (
        <div className="flex-1 overflow-hidden">
          <DockviewShell workspaceId={wid} projectId={pid} />
        </div>
      ) : null}
    </section>
  );
}
