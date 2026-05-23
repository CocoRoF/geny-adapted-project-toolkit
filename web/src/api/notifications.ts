import { apiGet, apiPost } from "@/api/client";

export type NotificationKind =
  | "deploy.success"
  | "deploy.failed"
  | "deploy.rolled_back"
  | "policy.denied"
  | "ci.green"
  | "ci.failed"
  | "cost.cap_near"
  | "system";

export type NotificationSeverity = "info" | "warn" | "error";

export interface Notification {
  id: string;
  kind: NotificationKind;
  title: string;
  body: string;
  actor_id: string | null;
  project_id: string | null;
  workspace_id: string | null;
  severity: NotificationSeverity;
  ts: number;
  details: Record<string, unknown>;
}

export function listNotifications(limit = 50): Promise<Notification[]> {
  return apiGet<Notification[]>(`/api/notifications?limit=${limit}`);
}

export function emitTestNotification(payload: {
  title?: string;
  body?: string;
  severity?: NotificationSeverity;
}): Promise<Notification> {
  return apiPost<Notification>("/api/notifications/test", payload);
}
