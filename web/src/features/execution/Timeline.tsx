import { useMemo } from "react";

import type { ExecutionRunEvent } from "./api";

interface TimelineRow {
  event: ExecutionRunEvent;
  heartbeat: boolean;
}

/**
 * Curated run timeline: one line per event message, with consecutive
 * heartbeats collapsed into a single subtle "Still working…" line.
 */
export function Timeline({ events }: { events: ExecutionRunEvent[] }) {
  const rows = useMemo<TimelineRow[]>(() => {
    const curated: TimelineRow[] = [];
    for (const event of events) {
      const heartbeat = event.event_type === "heartbeat";
      const last = curated[curated.length - 1];
      if (heartbeat && last?.heartbeat) {
        curated[curated.length - 1] = { event, heartbeat: true };
        continue;
      }
      curated.push({ event, heartbeat });
    }
    return curated;
  }, [events]);

  if (!rows.length) {
    return <p role="status">Waiting for the first run event…</p>;
  }
  return (
    <ol aria-label="Run timeline" className="timeline">
      {rows.map(({ event, heartbeat }) =>
        heartbeat ? (
          <li className="timeline-heartbeat" key={`heartbeat-${event.sequence}`}>
            Still working…
          </li>
        ) : (
          <li key={event.sequence}>
            <span>{event.message}</span>
            <small>{formatTime(event.created_at)}</small>
          </li>
        ),
      )}
    </ol>
  );
}

function formatTime(createdAt: string): string {
  const date = new Date(createdAt);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
}
