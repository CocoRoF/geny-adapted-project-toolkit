import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, GitBranch, Loader2 } from "lucide-react";

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

/** `/projects/:pid/w/:wid` — the dockview IDE shell. While the
 * background clone is still running (status=creating) we show a
 * cloning overlay instead of an empty IDE because the file tree
 * and editor would just look broken. */
export function WorkspaceIde() {
  const { pid, wid } = useParams();
  const { t } = useI18n();
  const [workspace, setWorkspace] = useState<WorkspaceResponse | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const startedAt = useRef<number>(Date.now());
  const [elapsedSec, setElapsedSec] = useState<number>(0);

  const fetchOnce = useCallback(() => {
    if (!wid) return;
    getWorkspace(wid)
      .then((ws) => {
        setWorkspace(ws);
        setState("ready");
        setError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) setError(`${err.code}: ${err.reason}`);
        else setError(err instanceof Error ? err.message : String(err));
        setState("error");
      });
  }, [wid]);

  useEffect(() => {
    fetchOnce();
  }, [fetchOnce]);

  // Poll while the workspace is still being cloned; ticker also drives
  // the "elapsed" counter shown to the user so they know it's alive.
  useEffect(() => {
    if (workspace?.status !== "creating") return;
    const poll = window.setInterval(fetchOnce, 3000);
    const tick = window.setInterval(
      () => setElapsedSec(Math.round((Date.now() - startedAt.current) / 1000)),
      1000,
    );
    return () => {
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  }, [workspace?.status, fetchOnce]);

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
      {state === "ready" && workspace?.status === "creating" ? (
        <CloningOverlay elapsedSec={elapsedSec} />
      ) : null}
      {state === "ready" && workspace?.status === "failed" ? (
        <FailedOverlay onRetry={fetchOnce} />
      ) : null}
      {state === "ready" && wid && pid && workspace?.status === "running" ? (
        <div className="flex-1 overflow-hidden">
          <DockviewShell workspaceId={wid} projectId={pid} />
        </div>
      ) : null}
    </section>
  );
}

function CloningOverlay({ elapsedSec }: { elapsedSec: number }) {
  const { t } = useI18n();
  const mins = Math.floor(elapsedSec / 60);
  const secs = elapsedSec % 60;
  return (
    <div className="grid flex-1 place-items-center bg-bg-subtle/40 px-6 py-12">
      <div className="w-full max-w-[420px] rounded-lg border border-border bg-bg-elevated p-6 text-center shadow-sm">
        <Loader2 className="mx-auto mb-3 h-6 w-6 animate-spin text-accent" />
        <h2 className="text-[15px] font-semibold text-fg">{t("workspace.cloning.title")}</h2>
        <p className="mt-1 text-[12px] text-fg-muted">{t("workspaces.cloning_hint")}</p>
        <p className="mt-4 font-mono text-[11px] text-fg-subtle">
          {mins > 0 ? `${mins}m ${secs}s` : `${secs}s`} —{" "}
          {t("workspace.cloning.poll").replace("{n}", "3")}
        </p>
      </div>
    </div>
  );
}

function FailedOverlay({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n();
  return (
    <div className="grid flex-1 place-items-center bg-bg-subtle/40 px-6 py-12">
      <div className="w-full max-w-[420px] rounded-lg border border-danger/40 bg-bg-elevated p-6 text-center shadow-sm">
        <h2 className="text-[15px] font-semibold text-danger">{t("workspace.failed.title")}</h2>
        <p className="mt-1 text-[12px] text-fg-muted">{t("workspaces.failed_hint")}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-surface px-3 text-[12px] font-medium text-fg hover:bg-surface-hover"
        >
          {t("workspace.failed.recheck")}
        </button>
      </div>
    </div>
  );
}
