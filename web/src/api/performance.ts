import { apiFetch, apiGet, apiPost } from "@/api/client";

export type ContainerCategory = "workspace" | "prod" | "infra" | "other";

export interface ContainerSummary {
  id: string;
  name: string;
  image: string;
  category: ContainerCategory;
  workspace_id: string | null;
  project_id: string | null;
  project_slug: string | null;
  project_display_name: string | null;
  workspace_branch: string | null;
  environment_id: string | null;
  environment_name: string | null;
  compose_project: string | null;
  compose_service: string | null;
  status: string;
  started_at: string | null;
  created_at: string | null;
}

export interface ContainerLimits {
  cpu_quota_us: number | null;
  cpu_period_us: number | null;
  nano_cpus: number | null;
  cpus_effective: number | null;
  mem_bytes: number | null;
  memswap_bytes: number | null;
  pids_limit: number | null;
  runtime: string;
  network_mode: string;
  networks: string[];
  mount_count: number;
}

export interface ContainerStats {
  cpu_pct: number;
  online_cpus: number;
  mem_bytes: number;
  mem_limit_bytes: number | null;
  mem_pct: number;
  net_rx_bytes: number;
  net_tx_bytes: number;
  block_rx_bytes: number;
  block_tx_bytes: number;
  pids: number | null;
}

export interface ContainerSample {
  summary: ContainerSummary;
  limits: ContainerLimits;
  stats: ContainerStats | null;
}

export interface ProjectRow {
  id: string;
  slug: string;
  display_name: string;
}

export interface WorkspaceRow {
  id: string;
  project_id: string;
  branch: string;
  status: string;
}

export interface EnvironmentRow {
  id: string;
  project_id: string;
  name: string;
}

export interface ContainersResponse {
  samples: ContainerSample[];
  projects: ProjectRow[];
  workspaces: WorkspaceRow[];
  environments: EnvironmentRow[];
  total_containers: number;
  running_containers: number;
  total_cpu_pct: number;
  total_mem_bytes: number;
}

export interface HostInfo {
  cpus: number;
  mem_total_bytes: number;
  docker_version: string;
  runtime: string;
}

export interface GpuRow {
  index: number;
  name: string;
  driver_version: string;
  utilization_pct: number;
  memory_used_bytes: number;
  memory_total_bytes: number;
  memory_pct: number;
  temperature_c: number | null;
  power_watts: number | null;
}

export interface GpusResponse {
  available: boolean;
  gpus: GpuRow[];
}

export interface LogsResponse {
  container_id: string;
  text: string;
  truncated_to_tail: number;
}

export const listContainers = () =>
  apiGet<ContainersResponse>("/api/performance/containers");

export const getContainer = (id: string) =>
  apiGet<ContainerSample>(`/api/performance/containers/${id}`);

export const getHostInfo = () => apiGet<HostInfo>("/api/performance/host");

export const getGpuInfo = () => apiGet<GpusResponse>("/api/performance/gpu");

export const stopContainer = (id: string) =>
  apiPost<{ container_id: string; action: string; ok: boolean }>(
    `/api/performance/containers/${id}/stop`,
  );

export const killContainer = (id: string) =>
  apiPost<{ container_id: string; action: string; ok: boolean }>(
    `/api/performance/containers/${id}/kill`,
  );

export const restartContainer = (id: string) =>
  apiPost<{ container_id: string; action: string; ok: boolean }>(
    `/api/performance/containers/${id}/restart`,
  );

export const fetchContainerLogs = (id: string, tail = 500) =>
  apiFetch<LogsResponse>(
    `/api/performance/containers/${id}/logs?tail=${tail}`,
    { method: "GET" },
  );

// ───────────────────────────────────── orphan cleanup ──

export interface OrphanTarget {
  container_id: string;
  container_name: string;
  category: ContainerCategory;
  workspace_id: string | null;
  environment_id: string | null;
  worktree_path: string | null;
  status: string;
}

export interface OrphanPlan {
  containers: OrphanTarget[];
  caddy_route_ids: string[];
  worktree_paths: string[];
}

export interface CleanupOutcome {
  container_id: string;
  container_name: string;
  ok: boolean;
  error: string | null;
}

export interface CleanupReport {
  containers: CleanupOutcome[];
  caddy_routes_removed: string[];
  worktrees_removed: string[];
  worktree_errors: { path: string; reason: string }[];
}

export const previewOrphanCleanup = () =>
  apiGet<OrphanPlan>("/api/performance/orphans");

export const cleanupOrphans = (remove_worktrees: boolean) =>
  apiPost<CleanupReport>("/api/performance/cleanup/orphans", {
    remove_worktrees,
  });
