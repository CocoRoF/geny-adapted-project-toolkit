/**
 * Phase J — list of past + active sessions for one project.
 *
 * Reached from `ProjectDetail` ("세션 히스토리" link) or directly via
 * `/projects/:pid/sessions`. Click any card → `SessionDetail` where
 * the transcript is rendered inline (no .md download needed).
 *
 * What the cards surface:
 *   - first user-message snippet (so "what was this about" is one
 *     glance away)
 *   - manifest, workspace, status pill
 *   - turn count + cost + token totals
 *   - relative timestamp ("3시간 전")
 *
 * Active / archived filter chips at the top — backend supports
 * `include_archived=true` so the toggle is a single query option.
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Archive, ChevronLeft, History, Loader2 } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type AgentSessionStatus,
  type SessionResponse,
  listSessions,
} from "@/api/sessions";
import { useI18n } from "@/app/providers/i18n-context";
import { Badge } from "@/ui/Badge";
import { Card, CardContent } from "@/ui/Card";
import { cn } from "@/ui/cn";

type Filter = "active" | "archived" | "all";

export function SessionsHistory() {
  const { t } = useI18n();
  const { pid } = useParams<{ pid: string }>();
  const projectId = pid ?? "";
  const [filter, setFilter] = useState<Filter>("all");
  const [rows, setRows] = useState<SessionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    setErr(null);
    // Phase J — when the filter is anything other than "active" we
    // need archived rows in the response too. The backend collapses
    // both server-side rather than asking the client to over-fetch.
    listSessions(projectId, { includeArchived: filter !== "active" })
      .then(setRows)
      .catch((e: unknown) => {
        setErr(
          e instanceof ApiError
            ? e.reason
            : e instanceof Error
              ? e.message
              : String(e),
        );
      })
      .finally(() => setLoading(false));
  }, [projectId, filter]);

  const filtered = useMemo(() => {
    if (filter === "active") return rows.filter((r) => r.status === "active");
    if (filter === "archived")
      return rows.filter((r) => r.status === "archived");
    return rows;
  }, [rows, filter]);

  if (!projectId) return null;

  return (
    <div className="mx-auto max-w-[1000px] px-6 py-8">
      <Link
        to={`/projects/${projectId}`}
        className="mb-3 inline-flex items-center gap-1 text-[12px] text-fg-muted hover:text-fg"
      >
        <ChevronLeft className="h-3.5 w-3.5" /> {t("sessions_history.back_to_project")}
      </Link>
      <header className="mb-5 flex items-center gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-bg-subtle">
          <History className="h-4 w-4 text-fg-muted" />
        </div>
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">
            {t("sessions_history.title")}
          </h1>
          <p className="text-[12px] text-fg-muted">
            {t("sessions_history.subtitle")}
          </p>
        </div>
      </header>

      <FilterChips filter={filter} onChange={setFilter} />

      {err ? (
        <Card className="mb-4 border-danger/40">
          <CardContent className="p-3 text-[12px] text-danger">{err}</CardContent>
        </Card>
      ) : null}

      {loading ? (
        <Card>
          <CardContent className="flex items-center gap-2 p-4 text-[12px] text-fg-subtle">
            <Loader2 className="h-3 w-3 animate-spin" /> {t("sessions_history.loading")}
          </CardContent>
        </Card>
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent className="p-6 text-center text-[13px] text-fg-muted">
            {filter === "archived"
              ? t("sessions_history.empty.archived")
              : t("sessions_history.empty.project")}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2.5">
          {filtered.map((s) => (
            <SessionCard key={s.id} projectId={projectId} session={s} />
          ))}
        </div>
      )}
    </div>
  );
}

function FilterChips({
  filter,
  onChange,
}: {
  filter: Filter;
  onChange: (next: Filter) => void;
}) {
  const { t } = useI18n();
  const opts: { value: Filter; label: string }[] = [
    { value: "all", label: t("sessions_history.filter.all") },
    { value: "active", label: t("sessions_history.filter.active") },
    { value: "archived", label: t("sessions_history.filter.archived") },
  ];
  return (
    <div className="mb-4 inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5">
      {opts.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={filter === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded px-3 py-1 text-[12px] font-medium transition-colors",
            filter === o.value
              ? "bg-bg text-fg shadow-sm"
              : "text-fg-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function SessionCard({
  projectId,
  session,
}: {
  projectId: string;
  session: SessionResponse;
}) {
  const { t } = useI18n();
  const snippet = session.first_user_message ?? null;
  const turns = session.turn_count ?? 0;
  const turnLabel = (turns === 1
    ? t("sessions_history.card.turns_one")
    : t("sessions_history.card.turns_other")
  ).replace("{count}", String(turns));
  return (
    <Link
      to={`/projects/${projectId}/sessions/${session.id}`}
      className="block rounded-md border border-border bg-bg transition-colors hover:border-accent/40 hover:bg-bg-subtle"
    >
      <div className="flex items-start gap-3 p-3">
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-bg-subtle">
          {session.status === "archived" ? (
            <Archive className="h-3.5 w-3.5 text-fg-subtle" />
          ) : (
            <History className="h-3.5 w-3.5 text-fg-subtle" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <StatusBadge status={session.status} />
            <span className="font-mono text-[10.5px] text-fg-subtle">
              {session.env_manifest_id}
            </span>
            <span className="text-[10.5px] text-fg-subtle">·</span>
            <span className="text-[10.5px] text-fg-subtle">
              {formatRelative(session.created_at, t)}
            </span>
          </div>
          <p
            className={cn(
              "mt-1 line-clamp-2 text-[12.5px]",
              snippet ? "text-fg" : "italic text-fg-subtle",
            )}
          >
            {snippet ?? t("sessions_history.no_recorded_prompts")}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-mono text-[11px] text-accent tabular-nums">
            ${session.cost_usd.toFixed(4)}
          </p>
          <p className="mt-0.5 text-[10.5px] text-fg-subtle tabular-nums">
            {turnLabel}
          </p>
          <p className="mt-0.5 text-[10.5px] text-fg-subtle tabular-nums">
            ↑{session.input_tokens} ↓{session.output_tokens}
            {/* Phase K.2 — only show cache when non-zero to keep the
                card from getting noisy for tool-heavy turns. */}
            {(session.cache_write_tokens ?? 0) > 0 ? (
              <span title="cache_write tokens">
                {" "}⊕{session.cache_write_tokens}
              </span>
            ) : null}
          </p>
        </div>
      </div>
    </Link>
  );
}

function StatusBadge({ status }: { status: AgentSessionStatus }) {
  if (status === "active") {
    return (
      <Badge tone="success" className="text-[10px]">
        active
      </Badge>
    );
  }
  if (status === "archived") {
    return (
      <Badge tone="neutral" className="text-[10px]">
        archived
      </Badge>
    );
  }
  return (
    <Badge tone="neutral" className="text-[10px]">
      {status}
    </Badge>
  );
}

/** Tiny relative timestamp without pulling in dayjs/luxon. Takes `t`
 *  as a parameter (not a hook call) so it can be invoked from inside
 *  render flows without a wrapper component. */
function formatRelative(iso: string, t: (key: string) => string): string {
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso;
  const delta = Date.now() - ts;
  const s = Math.floor(delta / 1000);
  if (s < 60) return t("sessions_history.relative.just_now");
  const m = Math.floor(s / 60);
  if (m < 60) return t("sessions_history.relative.minutes_ago").replace("{count}", String(m));
  const h = Math.floor(m / 60);
  if (h < 24) return t("sessions_history.relative.hours_ago").replace("{count}", String(h));
  const d = Math.floor(h / 24);
  if (d < 7) return t("sessions_history.relative.days_ago").replace("{count}", String(d));
  // Beyond a week, show the absolute date — relative loses meaning.
  return new Date(iso).toLocaleDateString();
}
