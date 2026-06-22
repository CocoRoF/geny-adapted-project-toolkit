import { apiFetch } from "./client";

/** A probed runtime dependency for workspace sandboxes. `state` is
 * "ok" | "missing" | "degraded" — see the backend
 * domains/sandbox/capabilities.py. */
export interface Capability {
  key: string;
  label: string;
  state: "ok" | "missing" | "degraded";
  detail: string;
  remedy: string | null;
}

export interface CapabilityReport {
  /** True only when every required capability is "ok". */
  workspaces_ready: boolean;
  capabilities: Capability[];
}

/** Probe whether the host can run workspace sandboxes (Docker CLI +
 * daemon + sysbox runtime + the workspace image with Claude). */
export function getCapabilities(): Promise<CapabilityReport> {
  return apiFetch<CapabilityReport>("/_gapt/api/system/capabilities");
}
