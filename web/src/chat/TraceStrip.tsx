import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import type { SessionStreamEvent } from "@/chat/useSessionStream";
import { cn } from "@/ui/cn";

interface Props {
  /** All events in the current session (already merged user+server). */
  events: SessionStreamEvent[];
  /** True while the assistant has work in flight. Drives the spinner. */
  active: boolean;
}

/** Compact, collapsible "과정" panel — surfaces the pipeline stages
 * the agent is executing so the user can see *what* is happening
 * between "send" and "first token". Renders the *current* phase
 * inline (one line) when collapsed, the full step list when expanded.
 *
 * Source events: `kind="step"` payloads from `_maybe_step_payload`
 * server-side (stage_enter/exit/bypass + api_* + parse + evaluate +
 * loop + yield + guard + context + ...). Each step has
 * `{phase, stage, event, summary}`. We render at most the last
 * `MAX_STEPS` so a long agent loop doesn't push the chat off-screen.
 */
const MAX_STEPS = 50;

interface Step {
  ts: string;
  phase: string;
  stage: string;
  event: string;
  summary: string;
}

function toStep(ev: SessionStreamEvent): Step | null {
  if (ev.kind !== "step") return null;
  return {
    ts: ev.ts,
    phase: asString(ev.data["phase"]),
    stage: asString(ev.data["stage"]),
    event: asString(ev.data["event"]),
    summary: asString(ev.data["summary"]),
  };
}

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

/** i18n key per phase code. Resolved to a human-readable label via t(). */
const PHASE_LABEL_KEY: Record<string, string> = {
  stage_enter: "trace.phase.stage_enter",
  stage_exit: "trace.phase.stage_exit",
  stage_bypass: "trace.phase.stage_bypass",
  api_request: "trace.phase.api_request",
  api_response: "trace.phase.api_response",
  parse: "trace.phase.parse",
  evaluate_start: "trace.phase.evaluate_start",
  evaluate_complete: "trace.phase.evaluate_complete",
  loop: "trace.phase.loop",
  yield: "trace.phase.yield",
  guard: "trace.phase.guard",
  context: "trace.phase.context",
  system: "trace.phase.system",
  memory: "trace.phase.memory",
  task_registry: "trace.phase.task_registry",
  input: "trace.phase.input",
};

/** Phase-bucket → tailwind colour. Picks 1 of 4 tones so the
 * collapsed strip + expanded list read at a glance. */
function phaseTone(phase: string): string {
  if (phase.startsWith("api")) return "text-accent";
  if (phase.startsWith("evaluate") || phase === "yield") return "text-success";
  if (phase === "guard") return "text-warn";
  if (phase === "stage_bypass") return "text-fg-subtle";
  return "text-fg-muted";
}

export function TraceStrip({ events, active }: Props) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  // Resolve a phase code to its localized label, falling back to the
  // raw code when we have no key for it.
  const phaseLabel = (phase: string): string => {
    const key = PHASE_LABEL_KEY[phase];
    return key ? t(key as Parameters<typeof t>[0]) : phase;
  };

  const steps = useMemo<Step[]>(() => {
    const out: Step[] = [];
    for (const ev of events) {
      const s = toStep(ev);
      if (s) out.push(s);
    }
    return out.slice(-MAX_STEPS);
  }, [events]);

  if (steps.length === 0 && !active) return null;

  // Show the *most recent* step that signals "live activity" — prefer
  // stage_enter over stage_exit (because we want to say "doing X",
  // not "did X"). Fall back to the latest step of any kind.
  const lastLiveStep = (() => {
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      const s = steps[i]!;
      if (s.phase === "stage_enter" || s.phase === "api_request" || s.phase === "evaluate_start") {
        return s;
      }
    }
    return steps[steps.length - 1] ?? null;
  })();

  const headLabel = lastLiveStep
    ? `${phaseLabel(lastLiveStep.phase)}${
        lastLiveStep.stage ? ` · ${lastLiveStep.stage}` : ""
      }${lastLiveStep.summary ? ` · ${lastLiveStep.summary}` : ""}`
    : t("trace.idle");

  return (
    <div
      data-testid="trace-strip"
      className="rounded-md border border-border bg-bg-elevated/60 text-[11px] text-fg-muted"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-surface-hover"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-fg-subtle" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-fg-subtle" />
        )}
        {active ? (
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-accent" />
        ) : (
          <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-fg-subtle" />
        )}
        <span className="truncate">{headLabel}</span>
        <span className="ml-auto shrink-0 font-mono tabular-nums text-fg-subtle">
          {steps.length}
        </span>
      </button>
      {open ? (
        <ol className="max-h-[260px] overflow-y-auto border-t border-border px-2 py-1.5">
          {steps.map((step, i) => (
            <li
              key={`${step.ts}-${i}`}
              className="flex items-center gap-2 py-0.5 font-mono leading-snug"
            >
              <span className="w-16 shrink-0 truncate text-[10px] text-fg-subtle">
                {step.stage || "·"}
              </span>
              <span className={cn("shrink-0", phaseTone(step.phase))}>
                {phaseLabel(step.phase)}
              </span>
              {step.summary ? (
                <span className="truncate text-fg-subtle">{step.summary}</span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
