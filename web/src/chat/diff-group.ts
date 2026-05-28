import type { SessionStreamEvent } from "@/chat/useSessionStream";

import type { GaptEditPayload } from "@/chat/DiffCard";

/** Phase D.2 — client-side grouping of consecutive `gapt_edit`
 * results that target the same file path.
 *
 * The runtime emits one `tool_result` event per edit. Three edits
 * to the same file end up as three separate diff cards in the
 * chat, which becomes noisy fast. This helper returns the same
 * event list but with `groupSize` / `groupIndex` markers attached
 * so the renderer can wrap a run in a single "N edits to <path>"
 * shell.
 *
 * Why client-side: the runtime's tool_result schema is shared with
 * the rest of the agent stack and adding a `group_id` server-side
 * would ripple into the executor protocol. The grouping is purely
 * a presentation concern, so we keep it in the UI layer.
 */

export interface EditGroupMarker {
  /** 1-based count of edits in the run this event belongs to. */
  groupSize: number;
  /** 0-based position within the run. */
  groupIndex: number;
  /** File path the run is targeting — used as the group header. */
  path: string;
}

/** Decorate edit events with grouping markers. Non-edit events get
 *  `null`. Pass the same `extractEdit` you use in render so we agree
 *  on what counts as an edit. */
export function annotateEditGroups(
  events: SessionStreamEvent[],
  extractEdit: (data: Record<string, unknown>) => GaptEditPayload | null,
): Map<number, EditGroupMarker> {
  const markers = new Map<number, EditGroupMarker>();
  let runStart = -1;
  let runPath: string | null = null;
  let runEvents: SessionStreamEvent[] = [];

  const flush = () => {
    if (runStart < 0 || runPath === null) return;
    runEvents.forEach((event, idx) => {
      markers.set(event.seq, {
        groupSize: runEvents.length,
        groupIndex: idx,
        path: runPath!,
      });
    });
  };

  for (const event of events) {
    if (event.kind !== "tool_result") {
      flush();
      runStart = -1;
      runPath = null;
      runEvents = [];
      continue;
    }
    const edit = extractEdit(event.data);
    if (edit === null) {
      flush();
      runStart = -1;
      runPath = null;
      runEvents = [];
      continue;
    }
    if (runPath === edit.path) {
      runEvents.push(event);
      continue;
    }
    flush();
    runStart = event.seq;
    runPath = edit.path;
    runEvents = [event];
  }
  flush();

  return markers;
}
