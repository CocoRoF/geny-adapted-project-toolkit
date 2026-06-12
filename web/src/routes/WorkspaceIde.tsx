import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Archive, ChevronLeft, Code2, GitBranch, Loader2, Rocket } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type WorkspaceResponse,
  deleteWorkspace,
  getWorkspace,
  getWorkspaceCloneLog,
} from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { DeployWorkspace } from "@/ide/DeployWorkspace";
import { IdeShell } from "@/ide/shell/IdeShell";
import { IntrospectionWizard } from "@/ide/IntrospectionWizard";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";

const WIZARD_DISMISS_KEY_PREFIX = "gapt.ide.wizard.dismissed";

// Phase N.3 — "service" was folded into the IDE shell as a sidebar
// view ("서비스" in the ActivityBar) + preview tabs in the editor
// column. Only IDE / deploy remain as workspace-level views.
type WorkspaceView = "ide" | "deploy";

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
  const [view, setView] = useState<WorkspaceView>("ide");
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardToast, setWizardToast] = useState<string | null>(null);

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

  // Auto-open the introspection wizard once the workspace finishes
  // cloning AND the user hasn't dismissed it before. Stored per-
  // workspace in localStorage; manual reopen via the toolbar.
  useEffect(() => {
    if (!wid) return;
    if (workspace?.status !== "running") return;
    if (window.localStorage.getItem(`${WIZARD_DISMISS_KEY_PREFIX}.${wid}`)) return;
    setWizardOpen(true);
  }, [wid, workspace?.status]);

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
              <span className="text-[13px] font-semibold text-fg">{workspace.name}</span>
              {/* Phase N.5 — per-repo branch chips. The workspace's
                  identity is `name`; the chips show which repos are
                  inside and at what branch each. The GitPanel's
                  source-control selector mirrors the same list. */}
              {workspace.selections.length > 0 ? (
                <span className="flex items-center gap-1 text-[10.5px] text-fg-subtle">
                  {workspace.selections.slice(0, 3).map((s, i) => (
                    <span key={s.repository_id ?? i}>
                      {s.display_name}
                      {s.branch ? `@${s.branch}` : ""}
                      {i < Math.min(2, workspace.selections.length - 1) ? "·" : ""}
                    </span>
                  ))}
                  {workspace.selections.length > 3 ? (
                    <span>+{workspace.selections.length - 3}</span>
                  ) : null}
                </span>
              ) : null}
              <Badge tone={STATUS_TONE[workspace.status] ?? "neutral"}>{workspace.status}</Badge>
            </>
          ) : (
            <span className="text-[13px] font-semibold text-fg">{t("workspace.title")}</span>
          )}
        </div>
        {state === "ready" && workspace?.status === "running" ? (
          <div
            role="tablist"
            aria-label={t("workspace.view.aria_label")}
            className="ml-4 flex items-center gap-0.5 rounded-md bg-bg p-0.5"
          >
            <ViewTab
              active={view === "ide"}
              icon={<Code2 className="h-3.5 w-3.5" />}
              label={t("workspace.view.ide")}
              onClick={() => setView("ide")}
            />
            <ViewTab
              active={view === "deploy"}
              icon={<Rocket className="h-3.5 w-3.5" />}
              label={t("workspace.view.deploy")}
              onClick={() => setView("deploy")}
            />
          </div>
        ) : null}
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
      {state === "ready" && workspace?.status === "creating" && wid ? (
        <CloningOverlay workspaceId={wid} elapsedSec={elapsedSec} />
      ) : null}
      {state === "ready" && workspace?.status === "failed" && wid && pid ? (
        <FailedOverlay workspaceId={wid} projectId={pid} onRetry={fetchOnce} />
      ) : null}
      {state === "ready" && workspace?.status === "archived" && pid ? (
        <ArchivedOverlay projectId={pid} />
      ) : null}
      {state === "ready" && wid && pid && workspace?.status === "running" ? (
        <div className="flex-1 overflow-hidden">
          {view === "ide" ? (
            <IdeShell
              workspaceId={wid}
              projectId={pid}
              name={workspace.name}
              workspaceStatus={workspace.status}
            />
          ) : (
            <DeployWorkspace projectId={pid} />
          )}
        </div>
      ) : null}
      {wid && workspace?.status === "running" ? (
        <IntrospectionWizard
          open={wizardOpen}
          workspaceId={wid}
          onClose={() => {
            setWizardOpen(false);
            window.localStorage.setItem(`${WIZARD_DISMISS_KEY_PREFIX}.${wid}`, "1");
          }}
          onApplied={({ actions }) =>
            setWizardToast(actions.length > 0 ? actions.join(" · ") : "감지 결과를 적용했습니다.")
          }
        />
      ) : null}
      {wizardToast ? (
        <div
          role="status"
          className="fixed bottom-4 right-4 z-40 max-w-[480px] rounded-md border border-accent/40 bg-bg-elevated px-3 py-2 text-[12px] text-fg shadow-lg"
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="font-semibold text-accent">자동 감지 적용됨</span>
            <button
              type="button"
              onClick={() => setWizardToast(null)}
              className="text-fg-subtle hover:text-fg"
            >
              ×
            </button>
          </div>
          <p className="whitespace-pre-line break-all text-fg-muted">{wizardToast}</p>
        </div>
      ) : null}
    </section>
  );
}

function ViewTab({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      role="tab"
      type="button"
      aria-selected={active}
      onClick={onClick}
      className={
        active
          ? "inline-flex items-center gap-1.5 rounded-md bg-bg-elevated px-2.5 py-1 text-[12px] font-medium text-fg shadow-sm"
          : "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px] font-medium text-fg-muted hover:bg-surface-hover hover:text-fg"
      }
    >
      {icon}
      {label}
    </button>
  );
}

function CloningOverlay({ workspaceId, elapsedSec }: { workspaceId: string; elapsedSec: number }) {
  const { t } = useI18n();
  const [log, setLog] = useState<string>("");
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let cancelled = false;
    const pull = () => {
      void getWorkspaceCloneLog(workspaceId).then((text) => {
        if (cancelled) return;
        setLog(text);
        // Auto-scroll to bottom only when already near the end so the
        // user can scroll up to inspect without being yanked back.
        const el = logRef.current;
        if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) {
          requestAnimationFrame(() => {
            el.scrollTop = el.scrollHeight;
          });
        }
      });
    };
    pull();
    const id = window.setInterval(pull, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [workspaceId]);

  const mins = Math.floor(elapsedSec / 60);
  const secs = elapsedSec % 60;

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 bg-bg-subtle/40 px-6 py-8">
      <div className="w-full max-w-[720px] rounded-lg border border-border bg-bg-elevated shadow-sm">
        <header className="flex items-center gap-3 border-b border-border px-5 py-4">
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
          <div className="flex-1">
            <h2 className="text-[14px] font-semibold text-fg">{t("workspace.cloning.title")}</h2>
            <p className="mt-0.5 text-[11px] text-fg-muted">{t("workspaces.cloning_hint")}</p>
          </div>
          <span className="font-mono text-[11px] text-fg-subtle">
            {mins > 0 ? `${mins}m ${secs}s` : `${secs}s`}
          </span>
        </header>
        <pre
          ref={logRef}
          data-testid="workspace-clone-log"
          className="h-[320px] overflow-auto whitespace-pre-wrap break-all bg-bg px-4 py-3 font-mono text-[11px] leading-relaxed text-fg-muted"
        >
          {log || t("workspace.cloning.log_waiting")}
        </pre>
        <footer className="flex items-center justify-between gap-3 border-t border-border px-5 py-3 text-[11px] text-fg-subtle">
          <span>{t("workspace.cloning.poll").replace("{n}", "2")}</span>
          <span className="font-mono">{log ? `${log.split("\n").length} lines` : "—"}</span>
        </footer>
      </div>
    </div>
  );
}

function ArchivedOverlay({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 bg-bg-subtle/40 px-6 py-12">
      <div className="w-full max-w-[480px] rounded-lg border border-border bg-bg-elevated p-6 text-center shadow-sm">
        <Archive className="mx-auto mb-3 h-6 w-6 text-fg-muted" />
        <h2 className="text-[15px] font-semibold text-fg">{t("workspace.archived.title")}</h2>
        <p className="mt-1 text-[12px] text-fg-muted">{t("workspace.archived.body")}</p>
        <Link
          to={`/projects/${projectId}`}
          className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-3 text-[12px] font-medium text-accent-fg hover:bg-accent/90"
        >
          {t("workspace.archived.back")}
        </Link>
      </div>
    </div>
  );
}

function FailedOverlay({
  workspaceId,
  projectId,
  onRetry,
}: {
  workspaceId: string;
  projectId: string;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [log, setLog] = useState<string>("");
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    void getWorkspaceCloneLog(workspaceId).then(setLog);
  }, [workspaceId]);

  function handleDelete(): void {
    setDeleting(true);
    void deleteWorkspace(workspaceId)
      .then(() => navigate(`/projects/${projectId}`))
      .finally(() => setDeleting(false));
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 bg-bg-subtle/40 px-6 py-8">
      <div className="w-full max-w-[720px] rounded-lg border border-danger/40 bg-bg-elevated shadow-sm">
        <header className="border-b border-danger/30 px-5 py-4">
          <h2 className="text-[14px] font-semibold text-danger">{t("workspace.failed.title")}</h2>
          <p className="mt-1 text-[11px] text-fg-muted">{t("workspaces.failed_hint")}</p>
        </header>
        {log ? (
          <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap break-all bg-bg px-4 py-3 font-mono text-[11px] leading-relaxed text-fg-muted">
            {log}
          </pre>
        ) : null}
        <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <Button variant="danger" onClick={handleDelete} disabled={deleting}>
            {t("workspace.failed.delete")}
          </Button>
          <Button variant="secondary" onClick={onRetry}>
            {t("workspace.failed.recheck")}
          </Button>
        </footer>
      </div>
    </div>
  );
}
