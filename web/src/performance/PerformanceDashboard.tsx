import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Cpu,
  FileText,
  FolderOpen,
  GitBranch,
  HardDrive,
  MemoryStick,
  RotateCcw,
  Rocket,
  Server,
  ServerCog,
  Skull,
  Square,
  Thermometer,
  Trash2,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type ContainerSample,
  type ContainersResponse,
  type EnvironmentRow,
  type GpusResponse,
  type HostInfo,
  type ProjectRow,
  type WorkspaceRow,
  getGpuInfo,
  getHostInfo,
  killContainer,
  restartContainer,
  stopContainer,
} from "@/api/performance";
import { useI18n } from "@/app/providers/i18n-context";
import { CleanupOrphansModal } from "@/performance/CleanupOrphansModal";
import { LogsModal } from "@/performance/LogsModal";
import { Sparkline } from "@/performance/Sparkline";
import { useContainersStream } from "@/performance/useContainersStream";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

const SPARK_LEN = 40; // ~2 min @ 2s server tick

type Point = { cpu_pct: number; mem_bytes: number };
type SeriesMap = Record<string, Point[]>;

/** ─────────────────── tree building ───────────────────
 *
 * Group containers under a hierarchy:
 *   Project { workspaces: { containers[] }, environments: { containers[] } }
 *   + a top-level "Infra" pseudo-group for `gapt-dev-*`
 *   + a top-level "Other" pseudo-group for stray containers
 *
 * The DB doesn't tell us about archived workspaces — so when a
 * container's workspace_id isn't in the workspaces table we still
 * surface it (in an `orphans` slot per project) so the operator can
 * see and clean up. */
interface TreeWorkspace {
  workspace: WorkspaceRow | null;
  workspace_id: string;
  containers: ContainerSample[];
}
interface TreeEnvironment {
  environment: EnvironmentRow | null;
  environment_id: string;
  containers: ContainerSample[];
}
interface TreeProject {
  project: ProjectRow | null;
  project_id: string | null;
  display_name: string;
  workspaces: TreeWorkspace[];
  environments: TreeEnvironment[];
  /** Containers we couldn't bucket — labelled with a project_id but
   * no matching workspace / environment row. */
  unbucketed: ContainerSample[];
}

function countContainers(p: TreeProject): number {
  return (
    p.workspaces.reduce((n, w) => n + w.containers.length, 0) +
    p.environments.reduce((n, e) => n + e.containers.length, 0) +
    p.unbucketed.length
  );
}

function buildTree(resp: ContainersResponse): {
  /** Projects that have a matching DB row. */
  realProjects: TreeProject[];
  /** Projects we couldn't resolve in the DB (archived row, wiped
   * schema, label/id mismatch). Operator may want to clean these
   * up — surfaced separately so they don't visually pollute the
   * live project list. */
  orphanProjects: TreeProject[];
  infra: ContainerSample[];
  other: ContainerSample[];
} {
  const projectById = new Map(resp.projects.map((p) => [p.id, p]));
  const wsById = new Map(resp.workspaces.map((w) => [w.id, w]));
  const envById = new Map(resp.environments.map((e) => [e.id, e]));

  const projects = new Map<string, TreeProject>();

  const ensureProject = (id: string | null): TreeProject => {
    const key = id ?? "__orphan__";
    let p = projects.get(key);
    if (!p) {
      const row = id ? projectById.get(id) ?? null : null;
      p = {
        project: row,
        project_id: id,
        display_name: row?.display_name ?? "(orphan project)",
        workspaces: [],
        environments: [],
        unbucketed: [],
      };
      projects.set(key, p);
    }
    return p;
  };
  const ensureWs = (proj: TreeProject, ws_id: string): TreeWorkspace => {
    let w = proj.workspaces.find((x) => x.workspace_id === ws_id);
    if (!w) {
      w = { workspace: wsById.get(ws_id) ?? null, workspace_id: ws_id, containers: [] };
      proj.workspaces.push(w);
    }
    return w;
  };
  const ensureEnv = (proj: TreeProject, env_id: string): TreeEnvironment => {
    let e = proj.environments.find((x) => x.environment_id === env_id);
    if (!e) {
      e = {
        environment: envById.get(env_id) ?? null,
        environment_id: env_id,
        containers: [],
      };
      proj.environments.push(e);
    }
    return e;
  };

  const infra: ContainerSample[] = [];
  const other: ContainerSample[] = [];

  for (const s of resp.samples) {
    if (s.summary.category === "infra") {
      infra.push(s);
      continue;
    }
    if (s.summary.category === "other") {
      other.push(s);
      continue;
    }
    if (s.summary.category === "workspace" && s.summary.workspace_id) {
      const proj = ensureProject(s.summary.project_id);
      ensureWs(proj, s.summary.workspace_id).containers.push(s);
      continue;
    }
    if (s.summary.category === "prod" && s.summary.environment_id) {
      const proj = ensureProject(s.summary.project_id);
      ensureEnv(proj, s.summary.environment_id).containers.push(s);
      continue;
    }
    // Last-resort bucket.
    ensureProject(s.summary.project_id).unbucketed.push(s);
  }

  // Sort workspaces by branch and envs by name within each project.
  for (const p of projects.values()) {
    p.workspaces.sort((a, b) =>
      (a.workspace?.branch ?? a.workspace_id).localeCompare(
        b.workspace?.branch ?? b.workspace_id,
      ),
    );
    p.environments.sort((a, b) =>
      (a.environment?.name ?? a.environment_id).localeCompare(
        b.environment?.name ?? b.environment_id,
      ),
    );
  }
  // Split real (has DB row) from orphan (project_id was null OR id
  // didn't resolve to a row). Real first, both sorted alpha by
  // display name; orphan id slug as the tiebreaker.
  const all = Array.from(projects.values());
  const realProjects = all
    .filter((p) => p.project !== null)
    .sort((a, b) => a.display_name.localeCompare(b.display_name));
  const orphanProjects = all
    .filter((p) => p.project === null)
    .sort((a, b) => (a.project_id ?? "").localeCompare(b.project_id ?? ""));
  return { realProjects, orphanProjects, infra, other };
}

// ─────────────────────────────────────────────────────── page ──

type ViewFilter = "all" | "project" | "orphan" | "infra" | "other";

export function PerformanceDashboard() {
  const { t } = useI18n();
  const [host, setHost] = useState<HostInfo | null>(null);
  const [gpu, setGpu] = useState<GpusResponse | null>(null);
  const [logsFor, setLogsFor] = useState<{ id: string; name: string } | null>(null);
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [view, setView] = useState<ViewFilter>("all");
  const seriesRef = useRef<SeriesMap>({});
  const [, bump] = useState(0);

  // SSE stream — auto-pauses when tab hidden, auto-resumes on focus.
  const { data: resp, state: streamState, error: streamErr, tickCount } =
    useContainersStream();
  const err = streamErr;

  // Build per-container sparkline series from incoming ticks. We
  // can't put this inside the hook because the hook is general-
  // purpose; series accumulation is dashboard-specific.
  useEffect(() => {
    if (!resp) return;
    const next = { ...seriesRef.current };
    for (const s of resp.samples) {
      const id = s.summary.id;
      const arr = next[id] ? [...next[id]] : [];
      arr.push({
        cpu_pct: s.stats?.cpu_pct ?? 0,
        mem_bytes: s.stats?.mem_bytes ?? 0,
      });
      if (arr.length > SPARK_LEN) arr.shift();
      next[id] = arr;
    }
    const alive = new Set(resp.samples.map((s) => s.summary.id));
    for (const k of Object.keys(next)) if (!alive.has(k)) delete next[k];
    seriesRef.current = next;
    bump((n) => n + 1);
  }, [resp]);

  // One-shot fetches: host info + GPU. These don't need streaming —
  // host CPU count + total memory are constants for the process's
  // lifetime, and GPU stats rarely move per-second.
  useEffect(() => {
    void getHostInfo().then(setHost).catch(() => undefined);
    void getGpuInfo().then(setGpu).catch(() => undefined);
  }, []);

  const tree = useMemo(() => (resp ? buildTree(resp) : null), [resp]);

  // Container counts per top-level view so the pills can show
  // `(N)` numbers. Use container counts (not project counts) since
  // that matches the table headers below.
  const counts = useMemo(() => {
    if (!tree) return { all: 0, project: 0, orphan: 0, infra: 0, other: 0 };
    const project = tree.realProjects.reduce((n, p) => n + countContainers(p), 0);
    const orphan = tree.orphanProjects.reduce((n, p) => n + countContainers(p), 0);
    const infra = tree.infra.length;
    const other = tree.other.length;
    return { all: project + orphan + infra + other, project, orphan, infra, other };
  }, [tree]);

  const showProjects = view === "all" || view === "project";
  const showOrphans = view === "all" || view === "orphan";
  const showInfra = view === "all" || view === "infra";
  const showOther = view === "all" || view === "other";

  const onAction = async (
    sample: ContainerSample,
    action: "stop" | "kill" | "restart",
  ) => {
    const label = sample.summary.name;
    const confirmTxt =
      action === "kill"
        ? t("performance.confirm.kill").replace("{name}", label)
        : action === "stop"
          ? t("performance.confirm.stop").replace("{name}", label)
          : t("performance.confirm.restart").replace("{name}", label);
    if (!window.confirm(confirmTxt)) return;
    try {
      if (action === "stop") await stopContainer(sample.summary.id);
      else if (action === "kill") await killContainer(sample.summary.id);
      else await restartContainer(sample.summary.id);
      // No manual refresh — the SSE stream will push the next tick
      // within ~2 s and the table re-renders on its own.
    } catch (e) {
      window.alert(e instanceof ApiError ? e.reason : String(e));
    }
  };

  return (
    <section className="mx-auto w-full max-w-[1400px] px-4 py-5">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[18px] font-semibold text-fg">{t("performance.title")}</h1>
          <p className="mt-0.5 text-[12px] text-fg-muted">{t("performance.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <StreamIndicator state={streamState} tickCount={tickCount} />
        </div>
      </header>

      <FleetTiles resp={resp} host={host} />
      {gpu && gpu.available ? <GpuTiles gpu={gpu} /> : null}

      {err ? (
        <p role="alert" className="my-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger">
          {err}
        </p>
      ) : null}

      {/* Top-level view filter */}
      <div className="mb-3 flex flex-wrap items-center gap-1">
        <ViewPill
          active={view === "all"}
          label={`${t("performance.view.all")}`}
          count={counts.all}
          onClick={() => setView("all")}
        />
        <ViewPill
          active={view === "project"}
          label={t("performance.view.project")}
          count={counts.project}
          onClick={() => setView("project")}
        />
        {counts.orphan > 0 ? (
          <ViewPill
            active={view === "orphan"}
            label={t("performance.view.orphan")}
            count={counts.orphan}
            onClick={() => setView("orphan")}
            tone="warn"
          />
        ) : null}
        <ViewPill
          active={view === "infra"}
          label={t("performance.view.infra")}
          count={counts.infra}
          onClick={() => setView("infra")}
        />
        {counts.other > 0 ? (
          <ViewPill
            active={view === "other"}
            label={t("performance.view.other")}
            count={counts.other}
            onClick={() => setView("other")}
          />
        ) : null}
      </div>

      {/* Tree */}
      <div className="mt-3 space-y-3">
        {showProjects && tree?.realProjects.length
          ? tree.realProjects.map((p) => (
              <ProjectGroup
                key={p.project_id ?? "__orphan__"}
                tree={p}
                series={seriesRef.current}
                onLogs={(s) => setLogsFor({ id: s.summary.id, name: s.summary.name })}
                onAction={onAction}
              />
            ))
          : null}
        {showProjects && tree && tree.realProjects.length === 0 && !err ? (
          <p className="rounded-md border border-border bg-bg-elevated px-4 py-6 text-center text-[12px] text-fg-subtle">
            {t("performance.empty_projects")}
          </p>
        ) : null}

        {showOrphans && tree?.orphanProjects.length ? (
          <OrphanSection onCleanup={() => setCleanupOpen(true)}>
            {tree.orphanProjects.map((p) => (
              <ProjectGroup
                key={`orphan-${p.project_id ?? "null"}`}
                tree={p}
                series={seriesRef.current}
                onLogs={(s) => setLogsFor({ id: s.summary.id, name: s.summary.name })}
                onAction={onAction}
                orphan
              />
            ))}
          </OrphanSection>
        ) : null}

        {showInfra && tree?.infra.length ? (
          <FlatGroup
            title={t("performance.cat.infra")}
            icon={<ServerCog className="h-4 w-4 text-success" strokeWidth={1.5} />}
            samples={tree.infra}
            series={seriesRef.current}
            onLogs={(s) => setLogsFor({ id: s.summary.id, name: s.summary.name })}
            onAction={onAction}
          />
        ) : null}

        {showOther && tree?.other.length ? (
          <FlatGroup
            title={t("performance.cat.other")}
            icon={<Server className="h-4 w-4 text-fg-muted" strokeWidth={1.5} />}
            samples={tree.other}
            series={seriesRef.current}
            onLogs={(s) => setLogsFor({ id: s.summary.id, name: s.summary.name })}
            onAction={onAction}
          />
        ) : null}

        {/* When a filter yields no rows, show a friendly empty state
            so the operator knows the page rendered (vs. a perf bug). */}
        {tree &&
        ((view === "project" && tree.realProjects.length === 0) ||
          (view === "orphan" && tree.orphanProjects.length === 0) ||
          (view === "infra" && tree.infra.length === 0) ||
          (view === "other" && tree.other.length === 0)) ? (
          <p className="rounded-md border border-border bg-bg-elevated px-4 py-6 text-center text-[12px] text-fg-subtle">
            {t("performance.view_empty")}
          </p>
        ) : null}
      </div>

      <p className="mt-4 text-[11px] text-fg-subtle">
        {t("performance.stream_hint")}
      </p>

      {logsFor ? (
        <LogsModal
          containerId={logsFor.id}
          containerName={logsFor.name}
          onClose={() => setLogsFor(null)}
        />
      ) : null}

      {cleanupOpen ? (
        <CleanupOrphansModal
          onClose={() => setCleanupOpen(false)}
          onCleaned={() => {
            // SSE pushes the fresh snapshot ~2 s later — clearing
            // the local sparkline series for the removed
            // containers happens automatically on the next tick.
          }}
        />
      ) : null}
    </section>
  );
}

// ─────────────────────────────────────────────────── tiles ──

function FleetTiles({
  resp,
  host,
}: {
  resp: ContainersResponse | null;
  host: HostInfo | null;
}) {
  const { t } = useI18n();
  // Fleet CPU% capped against host CPU count (so 4 vCPU host shows
  // 200% as "50% of host" — easier to reason about).
  const cpuPctOfHost = useMemo(() => {
    if (!resp || !host || host.cpus === 0) return null;
    return resp.total_cpu_pct / host.cpus;
  }, [resp, host]);
  const memPctOfHost = useMemo(() => {
    if (!resp || !host || host.mem_total_bytes === 0) return null;
    return (resp.total_mem_bytes / host.mem_total_bytes) * 100;
  }, [resp, host]);
  return (
    <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
      <Tile
        icon={<Cpu className="h-3.5 w-3.5" />}
        label={t("performance.fleet.cpu")}
        value={resp ? `${resp.total_cpu_pct.toFixed(1)}%` : "—"}
        bar={cpuPctOfHost}
        barTone="accent"
        hint={
          host && cpuPctOfHost !== null
            ? `${cpuPctOfHost.toFixed(0)}% ${t("performance.of_host")} · ${host.cpus} ${t("performance.cpus")}`
            : ""
        }
      />
      <Tile
        icon={<MemoryStick className="h-3.5 w-3.5" />}
        label={t("performance.fleet.mem")}
        value={resp ? formatBytes(resp.total_mem_bytes) : "—"}
        bar={memPctOfHost}
        barTone="success"
        hint={
          host
            ? `${memPctOfHost?.toFixed(0) ?? "—"}% ${t("performance.of_host")} · ${formatBytes(host.mem_total_bytes)}`
            : ""
        }
      />
      <Tile
        icon={<HardDrive className="h-3.5 w-3.5" />}
        label={t("performance.fleet.containers")}
        value={resp ? `${resp.running_containers} / ${resp.total_containers}` : "—"}
        hint={t("performance.running_total")}
      />
      <Tile
        icon={<Cpu className="h-3.5 w-3.5" />}
        label={t("performance.fleet.runtime")}
        value={host?.runtime ?? "—"}
        hint={host?.docker_version ? `docker ${host.docker_version}` : ""}
      />
    </div>
  );
}

function GpuTiles({ gpu }: { gpu: GpusResponse }) {
  const { t } = useI18n();
  return (
    <div className="mb-3 rounded-md border border-border bg-bg-elevated p-3">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
        {t("performance.gpu.title")}
      </h3>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {gpu.gpus.map((g) => (
          <div
            key={g.index}
            className="rounded-md border border-border bg-bg p-2.5"
          >
            <div className="mb-1 flex items-center gap-1.5">
              <Zap className="h-3.5 w-3.5 text-accent" strokeWidth={1.5} />
              <span className="text-[12px] font-medium text-fg">
                #{g.index} · {g.name}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[11.5px] tabular-nums">
              <Stat label={t("performance.gpu.util")} value={`${g.utilization_pct.toFixed(0)}%`} />
              <Stat
                label={t("performance.gpu.vram")}
                value={`${formatBytes(g.memory_used_bytes)} / ${formatBytes(g.memory_total_bytes)}`}
              />
              {g.temperature_c !== null ? (
                <Stat
                  icon={<Thermometer className="h-3 w-3" />}
                  label={t("performance.gpu.temp")}
                  value={`${g.temperature_c.toFixed(0)}°C`}
                />
              ) : null}
              {g.power_watts !== null ? (
                <Stat
                  label={t("performance.gpu.power")}
                  value={`${g.power_watts.toFixed(0)} W`}
                />
              ) : null}
            </div>
            <p className="mt-1 text-[10.5px] text-fg-subtle">
              {t("performance.gpu.driver")}: {g.driver_version}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Tile({
  icon,
  label,
  value,
  hint,
  bar,
  barTone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  bar?: number | null;
  barTone?: "accent" | "success";
}) {
  const tone = barTone === "success" ? "bg-success" : "bg-accent";
  return (
    <div className="rounded-md border border-border bg-bg-elevated px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-fg-muted">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-[18px] font-semibold tabular-nums text-fg">{value}</div>
      {bar !== undefined && bar !== null ? (
        <div className="mt-1 h-1 w-full overflow-hidden rounded bg-bg">
          <div className={cn("h-full", tone)} style={{ width: `${Math.min(bar, 100)}%` }} />
        </div>
      ) : null}
      {hint ? <div className="mt-0.5 text-[10.5px] text-fg-subtle">{hint}</div> : null}
    </div>
  );
}

function Stat({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-1 text-[10.5px] uppercase tracking-wider text-fg-subtle">
        {icon}
        {label}
      </div>
      <div className="font-medium text-fg">{value}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────── tree groups ──

interface RowHelpers {
  series: SeriesMap;
  onLogs: (s: ContainerSample) => void;
  onAction: (s: ContainerSample, a: "stop" | "kill" | "restart") => Promise<void>;
}

function StreamIndicator({
  state,
  tickCount,
}: {
  state: "idle" | "connecting" | "open" | "paused" | "error";
  tickCount: number;
}) {
  const { t } = useI18n();
  // Pulse the dot on every tick so the operator sees "live" without
  // a separate animation. The `key={tickCount}` resets the CSS
  // transition each tick.
  if (state === "open") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-success/40 bg-success/10 px-2 py-1 text-[11px] font-medium text-success">
        <span
          key={tickCount}
          className="inline-block h-1.5 w-1.5 animate-ping rounded-full bg-success"
        />
        <Wifi className="h-3 w-3" strokeWidth={1.5} />
        {t("performance.stream.live")}
      </span>
    );
  }
  if (state === "paused") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[11px] font-medium text-fg-muted">
        <WifiOff className="h-3 w-3" strokeWidth={1.5} />
        {t("performance.stream.paused")}
      </span>
    );
  }
  if (state === "error") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] font-medium text-warn">
        <WifiOff className="h-3 w-3" strokeWidth={1.5} />
        {t("performance.stream.reconnecting")}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-subtle px-2 py-1 text-[11px] font-medium text-fg-muted">
      <Wifi className="h-3 w-3 animate-pulse" strokeWidth={1.5} />
      {t("performance.stream.connecting")}
    </span>
  );
}

function ViewPill({
  active,
  label,
  count,
  tone,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  tone?: "warn";
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
        active
          ? "bg-bg text-fg shadow-[inset_0_-2px_0_var(--color-accent)]"
          : tone === "warn"
            ? "text-warn hover:bg-surface-hover hover:text-warn"
            : "text-fg-muted hover:bg-surface-hover hover:text-fg",
      )}
    >
      {label}
      <span className="ml-1 text-fg-subtle tabular-nums">({count})</span>
    </button>
  );
}

function OrphanSection({
  children,
  onCleanup,
}: {
  children: React.ReactNode;
  onCleanup: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="rounded-md border border-warn/40 bg-warn/[0.04]">
      <div className="flex items-center gap-2 border-b border-warn/30 bg-warn/10 px-3 py-1.5">
        <strong className="text-[11px] font-semibold uppercase tracking-wider text-warn">
          {t("performance.orphan.header")}
        </strong>
        <span className="flex-1 truncate text-[11px] text-fg-muted">
          {t("performance.orphan.hint")}
        </span>
        <Button
          size="sm"
          variant="danger"
          onClick={onCleanup}
          className="h-6 shrink-0 px-2 text-[11px]"
        >
          <Trash2 className="mr-1 h-3 w-3" />
          {t("performance.orphan.cleanup")}
        </Button>
      </div>
      <div className="space-y-2 p-2">{children}</div>
    </div>
  );
}

function ProjectGroup({
  tree,
  orphan = false,
  ...rest
}: { tree: TreeProject; orphan?: boolean } & RowHelpers) {
  const { t } = useI18n();
  const [open, setOpen] = useState(!orphan);
  // Project-level roll-up
  const allSamples = [
    ...tree.workspaces.flatMap((w) => w.containers),
    ...tree.environments.flatMap((e) => e.containers),
    ...tree.unbucketed,
  ];
  const cpu = allSamples.reduce((s, c) => s + (c.stats?.cpu_pct ?? 0), 0);
  const mem = allSamples.reduce((s, c) => s + (c.stats?.mem_bytes ?? 0), 0);
  const running = allSamples.filter((c) => c.summary.status === "running").length;
  return (
    <section className="overflow-hidden rounded-md border border-border bg-bg-elevated">
      <header
        className="flex cursor-pointer items-center gap-2 border-b border-border bg-bg-subtle px-3 py-2"
        onClick={() => setOpen((x) => !x)}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 text-fg-muted" />
        ) : (
          <ChevronRight className="h-4 w-4 text-fg-muted" />
        )}
        <FolderOpen className="h-4 w-4 text-accent" strokeWidth={1.5} />
        <strong className="text-[13px] text-fg">{tree.display_name}</strong>
        {tree.project ? (
          <code className="text-[10.5px] text-fg-subtle">{tree.project.slug}</code>
        ) : null}
        <span className="ml-auto flex items-center gap-3 text-[11px] tabular-nums text-fg-muted">
          <span>{running} {t("performance.running_lower")}</span>
          <span>· cpu {cpu.toFixed(1)}%</span>
          <span>· mem {formatBytes(mem)}</span>
        </span>
      </header>
      {open ? (
        <div className="divide-y divide-border">
          {tree.workspaces.map((w) => (
            <WorkspaceGroup key={w.workspace_id} ws={w} {...rest} />
          ))}
          {tree.environments.map((e) => (
            <EnvironmentGroup key={e.environment_id} env={e} {...rest} />
          ))}
          {tree.unbucketed.length > 0 ? (
            <div>
              <h4 className="border-b border-border bg-bg px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-fg-subtle">
                {t("performance.unbucketed")}
              </h4>
              {tree.unbucketed.map((s) => (
                <ContainerRow key={s.summary.id} sample={s} {...rest} />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function WorkspaceGroup({ ws, ...rest }: { ws: TreeWorkspace } & RowHelpers) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  return (
    <div className="bg-bg-elevated">
      <header
        className="flex cursor-pointer items-center gap-2 border-b border-border bg-bg px-4 py-1.5"
        onClick={() => setOpen((x) => !x)}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-fg-muted" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-fg-muted" />
        )}
        <GitBranch className="h-3.5 w-3.5 text-fg-muted" strokeWidth={1.5} />
        <span className="text-[12px] font-medium text-fg">
          {ws.workspace?.branch ?? ws.workspace_id}
        </span>
        {ws.workspace ? (
          <Badge tone="neutral" className="text-[9.5px]">
            {ws.workspace.status}
          </Badge>
        ) : (
          <Badge tone="warn" className="text-[9.5px]">
            {t("performance.archived")}
          </Badge>
        )}
        <code className="text-[10px] text-fg-subtle">
          {ws.workspace_id.slice(0, 12).toLowerCase()}…
        </code>
      </header>
      {open
        ? ws.containers.map((s) => <ContainerRow key={s.summary.id} sample={s} {...rest} />)
        : null}
    </div>
  );
}

function EnvironmentGroup({
  env,
  ...rest
}: { env: TreeEnvironment } & RowHelpers) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  return (
    <div className="bg-bg-elevated">
      <header
        className="flex cursor-pointer items-center gap-2 border-b border-border bg-bg px-4 py-1.5"
        onClick={() => setOpen((x) => !x)}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-fg-muted" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-fg-muted" />
        )}
        <Rocket className="h-3.5 w-3.5 text-warn" strokeWidth={1.5} />
        <span className="text-[12px] font-medium text-fg">
          {env.environment?.name ?? env.environment_id} ({t("performance.prod_stack")})
        </span>
        <code className="text-[10px] text-fg-subtle">
          {env.environment_id.slice(0, 12).toLowerCase()}…
        </code>
      </header>
      {open
        ? env.containers.map((s) => <ContainerRow key={s.summary.id} sample={s} {...rest} />)
        : null}
    </div>
  );
}

function FlatGroup({
  title,
  icon,
  samples,
  series,
  onLogs,
  onAction,
}: {
  title: string;
  icon: React.ReactNode;
  samples: ContainerSample[];
} & RowHelpers) {
  const [open, setOpen] = useState(true);
  const cpu = samples.reduce((s, c) => s + (c.stats?.cpu_pct ?? 0), 0);
  const mem = samples.reduce((s, c) => s + (c.stats?.mem_bytes ?? 0), 0);
  return (
    <section className="overflow-hidden rounded-md border border-border bg-bg-elevated">
      <header
        className="flex cursor-pointer items-center gap-2 border-b border-border bg-bg-subtle px-3 py-2"
        onClick={() => setOpen((x) => !x)}
      >
        {open ? <ChevronDown className="h-4 w-4 text-fg-muted" /> : <ChevronRight className="h-4 w-4 text-fg-muted" />}
        {icon}
        <strong className="text-[13px] text-fg">{title}</strong>
        <span className="ml-auto flex items-center gap-3 text-[11px] tabular-nums text-fg-muted">
          <span>{samples.length}</span>
          <span>· cpu {cpu.toFixed(1)}%</span>
          <span>· mem {formatBytes(mem)}</span>
        </span>
      </header>
      {open
        ? samples.map((s) => (
            <ContainerRow
              key={s.summary.id}
              sample={s}
              series={series}
              onLogs={onLogs}
              onAction={onAction}
            />
          ))
        : null}
    </section>
  );
}

// ────────────────────────────────────────────────── container row ──

function ContainerRow({
  sample,
  series,
  onLogs,
  onAction,
}: { sample: ContainerSample } & RowHelpers) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const { summary, limits, stats } = sample;
  const cpuValues = (series[summary.id] ?? []).map((p) => p.cpu_pct);
  const memValues = (series[summary.id] ?? []).map((p) => p.mem_bytes);
  const cpuMax = limits.cpus_effective ? limits.cpus_effective * 100 : null;
  const memMax = stats?.mem_limit_bytes ?? limits.mem_bytes ?? null;
  const isRunning = summary.status === "running";

  return (
    <>
      <div
        className="flex cursor-pointer items-center gap-3 px-4 py-1.5 hover:bg-surface-hover"
        onClick={() => setExpanded((x) => !x)}
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-fg-muted" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-fg-muted" />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[11.5px] font-medium text-fg" title={summary.name}>
            {summary.compose_service ? (
              <>
                <span className="text-fg-muted">{summary.compose_service}</span>{" "}
                <span className="text-fg-subtle">·</span>{" "}
              </>
            ) : null}
            {summary.name}
          </div>
          <div className="truncate text-[10px] text-fg-subtle" title={summary.image}>
            {summary.image}
          </div>
        </div>
        <StatusBadge status={summary.status} />
        <div className="flex w-[180px] items-center gap-1.5">
          <Sparkline values={cpuValues} max={cpuMax} />
          <div className="tabular-nums">
            <div className="text-[11px] font-medium text-fg">
              {stats ? `${stats.cpu_pct.toFixed(1)}%` : "—"}
            </div>
            <div className="text-[10px] text-fg-subtle">
              {limits.cpus_effective
                ? `/ ${limits.cpus_effective.toFixed(2)}`
                : t("performance.unlimited_short")}
            </div>
          </div>
        </div>
        <div className="flex w-[180px] items-center gap-1.5">
          <Sparkline values={memValues} max={memMax} stroke="var(--color-success)" />
          <div className="tabular-nums">
            <div className="text-[11px] font-medium text-fg">
              {stats ? formatBytes(stats.mem_bytes) : "—"}
            </div>
            <div className="text-[10px] text-fg-subtle">
              {memMax ? `/ ${formatBytes(memMax)}` : t("performance.unlimited_short")}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <Button
            size="sm"
            variant="ghost"
            title={t("performance.action.logs")}
            onClick={() => onLogs(sample)}
            className="h-6 w-6 p-0"
          >
            <FileText className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            title={t("performance.action.restart")}
            disabled={!isRunning}
            onClick={() => void onAction(sample, "restart")}
            className="h-6 w-6 p-0"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            title={t("performance.action.stop")}
            disabled={!isRunning}
            onClick={() => void onAction(sample, "stop")}
            className="h-6 w-6 p-0"
          >
            <Square className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            title={t("performance.action.kill")}
            disabled={!isRunning}
            onClick={() => void onAction(sample, "kill")}
            className="h-6 w-6 p-0 text-danger hover:text-danger"
          >
            <Skull className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      {expanded ? (
        <div className="border-t border-border bg-bg px-4 py-3">
          <Drilldown sample={sample} />
        </div>
      ) : null}
    </>
  );
}

function Drilldown({ sample }: { sample: ContainerSample }) {
  const { t } = useI18n();
  const { summary, limits, stats } = sample;
  return (
    <div className="grid grid-cols-1 gap-3 text-[12px] md:grid-cols-3">
      <Card title={t("performance.detail.identity")}>
        <KV k={t("performance.detail.id")} v={summary.id.slice(0, 12)} />
        <KV k={t("performance.detail.name")} v={summary.name} />
        <KV k={t("performance.detail.image")} v={summary.image} />
        <KV k="workspace_id" v={summary.workspace_id || "—"} />
        <KV
          k="compose"
          v={
            summary.compose_project
              ? `${summary.compose_project} / ${summary.compose_service || "—"}`
              : "—"
          }
        />
        <KV k={t("performance.detail.started")} v={summary.started_at?.slice(0, 19) ?? "—"} />
      </Card>
      <Card title={t("performance.detail.limits")}>
        <KV
          k="cpus"
          v={
            limits.cpus_effective ? limits.cpus_effective.toFixed(2) : t("performance.unlimited")
          }
        />
        <KV
          k="cpu_quota"
          v={
            limits.cpu_quota_us
              ? `${limits.cpu_quota_us}μs / ${limits.cpu_period_us}μs`
              : "—"
          }
        />
        <KV
          k="memory"
          v={limits.mem_bytes ? formatBytes(limits.mem_bytes) : t("performance.unlimited")}
        />
        <KV
          k="pids_limit"
          v={limits.pids_limit ? String(limits.pids_limit) : t("performance.unlimited")}
        />
        <KV k="runtime" v={limits.runtime} />
        <KV k="network_mode" v={limits.network_mode} />
        <KV k="networks" v={limits.networks.length ? limits.networks.join(", ") : "—"} />
        <KV k="mounts" v={String(limits.mount_count)} />
      </Card>
      <Card title={t("performance.detail.live")}>
        {stats ? (
          <>
            <KV k="cpu" v={`${stats.cpu_pct.toFixed(2)}% · ${stats.online_cpus} cores`} />
            <KV
              k="memory"
              v={
                stats.mem_limit_bytes
                  ? `${formatBytes(stats.mem_bytes)} / ${formatBytes(stats.mem_limit_bytes)} (${stats.mem_pct.toFixed(1)}%)`
                  : formatBytes(stats.mem_bytes)
              }
            />
            <KV
              k="network"
              v={`↓ ${formatBytes(stats.net_rx_bytes)} ↑ ${formatBytes(stats.net_tx_bytes)}`}
            />
            <KV
              k="block_io"
              v={`r ${formatBytes(stats.block_rx_bytes)} · w ${formatBytes(stats.block_tx_bytes)}`}
            />
            <KV k="pids" v={stats.pids != null ? String(stats.pids) : "—"} />
          </>
        ) : (
          <p className="text-[12px] text-fg-subtle">{t("performance.detail.not_running")}</p>
        )}
      </Card>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-border bg-bg-elevated p-3">
      <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
        {title}
      </h3>
      {children}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/50 py-0.5 last:border-b-0">
      <span className="shrink-0 text-[10.5px] text-fg-subtle">{k}</span>
      <span className="truncate text-right font-mono text-[11.5px] text-fg" title={v}>
        {v}
      </span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "running"
      ? "success"
      : status === "exited" || status === "dead"
        ? "danger"
        : status === "paused"
          ? "warn"
          : "neutral";
  return (
    <Badge tone={tone as "success" | "danger" | "warn" | "neutral"} className="w-16 justify-center text-[10px]">
      {status}
    </Badge>
  );
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}
