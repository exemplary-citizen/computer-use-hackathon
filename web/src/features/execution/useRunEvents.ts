import { useCallback, useEffect, useRef, useState } from "react";

import type { RunState } from "../../contracts";
import { eventStreamUrl, executionApi, type ExecutionRunEvent, type RunStatus } from "./api";

const TERMINAL_STATES: ReadonlySet<RunState> = new Set(["succeeded", "failed", "cancelled"]);
const POLL_INTERVAL_MS = 2_000;
const RECONNECT_DELAY_MS = 1_500;

export interface RunEventsSnapshot {
  status: RunStatus | null;
  events: ExecutionRunEvent[];
  /**
   * True from the moment the WebSocket drops (or a status fetch fails) until
   * a status refetch confirms state again. While degraded the approval
   * surface must not be trusted: callers disable approval controls and show
   * "Reconnecting…".
   */
  degraded: boolean;
  /** Refetch status now (used right after every mutating call). */
  refresh: () => Promise<void>;
}

/**
 * Live run subscription: WebSocket stream with `since_seq` resume plus a 2s
 * status poll as fallback. Both stop once the run reaches a terminal state.
 */
export function useRunEvents(runId: string | null): RunEventsSnapshot {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [events, setEvents] = useState<ExecutionRunEvent[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [trackedRunId, setTrackedRunId] = useState(runId);
  const lastSequenceRef = useRef(-1);
  const terminalRef = useRef(false);
  const socketRef = useRef<WebSocket | null>(null);

  // Reset the snapshot during render when the subscribed run changes.
  if (trackedRunId !== runId) {
    setTrackedRunId(runId);
    setStatus(null);
    setEvents([]);
    setDegraded(false);
  }

  const appendEvents = useCallback((incoming: ExecutionRunEvent[]) => {
    if (!incoming.length) return;
    setEvents((current) => {
      const merged = [...current];
      for (const event of incoming) {
        if (!merged.some((known) => known.sequence === event.sequence)) merged.push(event);
      }
      merged.sort((a, b) => a.sequence - b.sequence);
      lastSequenceRef.current = Math.max(lastSequenceRef.current, merged[merged.length - 1].sequence);
      return merged;
    });
  }, []);

  const refresh = useCallback(async () => {
    if (!runId) return;
    const next = await executionApi.getStatus(runId);
    setStatus(next);
    setDegraded(false);
    terminalRef.current = TERMINAL_STATES.has(next.state);
    if (next.last_sequence > lastSequenceRef.current) {
      const replay = await executionApi.getEvents(runId, lastSequenceRef.current);
      appendEvents(replay.events);
    }
    if (terminalRef.current) socketRef.current?.close();
  }, [runId, appendEvents]);

  useEffect(() => {
    lastSequenceRef.current = -1;
    terminalRef.current = false;
    if (!runId) return undefined;

    let active = true;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (!active || terminalRef.current || typeof WebSocket === "undefined") return;
      let socket: WebSocket;
      try {
        socket = new WebSocket(eventStreamUrl(runId, lastSequenceRef.current));
      } catch {
        setDegraded(true);
        return;
      }
      socketRef.current = socket;
      socket.onmessage = (message: MessageEvent) => {
        if (!active) return;
        try {
          appendEvents([JSON.parse(String(message.data)) as ExecutionRunEvent]);
        } catch {
          // Ignore malformed frames; the status poll remains authoritative.
        }
      };
      socket.onclose = () => {
        if (!active || terminalRef.current) return;
        setDegraded(true);
        reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };
      socket.onerror = () => {
        socket.close();
      };
    };

    const poll = () => {
      refresh().catch(() => {
        if (active) setDegraded(true);
      });
    };

    connect();
    poll();
    const interval = window.setInterval(() => {
      if (terminalRef.current) {
        window.clearInterval(interval);
        return;
      }
      poll();
    }, POLL_INTERVAL_MS);

    return () => {
      active = false;
      window.clearInterval(interval);
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [runId, appendEvents, refresh]);

  return { status, events, degraded, refresh };
}
