import { useEffect, useMemo, useState } from "react";

import type { RunState } from "../../contracts";
import {
  DEFAULT_TARGET_APP,
  ExecutionFaultError,
  InputFieldError,
  executionApi,
  inputLabel,
  targetAppFullLabel,
  targetAppLabel,
  type ExecutionFaultPayload,
  type RunInputs,
  type RunPreview,
  type RunStatus,
  type RunSummary,
} from "./api";
import { ResultView } from "./ResultView";
import { RunConfigForm } from "./RunConfigForm";
import { StagedChangePanel } from "./StagedChangePanel";
import { Timeline } from "./Timeline";
import { useRunEvents } from "./useRunEvents";
import "./execution.css";

const TERMINAL_STATES: ReadonlySet<RunState> = new Set(["succeeded", "failed", "cancelled"]);

export function ExecutionPage() {
  const [runId, setRunId] = useState<string | null>(null);
  const [preview, setPreview] = useState<RunPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [decided, setDecided] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const { status, events, degraded, refresh } = useRunEvents(runId);

  const state: RunState | null = status?.state ?? (preview ? "awaiting_start_confirmation" : null);
  const terminal = state !== null && TERMINAL_STATES.has(state);

  // Cause/remediation ride on failure event payloads; keep the latest one.
  const fault = useMemo<ExecutionFaultPayload | null>(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const payload = events[index].payload;
      if (
        typeof payload.error_code === "string" &&
        typeof payload.message === "string" &&
        typeof payload.cause === "string" &&
        typeof payload.remediation === "string"
      ) {
        return payload as unknown as ExecutionFaultPayload;
      }
    }
    return null;
  }, [events]);

  function reset() {
    setRunId(null);
    setPreview(null);
    setDecided(false);
    setFieldErrors({});
    setActionError(null);
  }

  function resume(id: string) {
    setPreview(null);
    setDecided(false);
    setFieldErrors({});
    setActionError(null);
    setRunId(id);
  }

  async function prepare(targetApp: string, inputs: RunInputs) {
    setBusy(true);
    setActionError(null);
    setFieldErrors({});
    try {
      const next = await executionApi.prepareRun(targetApp, inputs);
      setDecided(false);
      setPreview(next);
      setRunId(next.request.id);
    } catch (reason) {
      if (reason instanceof InputFieldError) setFieldErrors(reason.fieldErrors);
      else setActionError(reason instanceof Error ? reason.message : "Run preparation failed.");
    } finally {
      setBusy(false);
    }
  }

  async function act(operation: () => Promise<unknown>) {
    setBusy(true);
    setActionError(null);
    try {
      await operation();
      await refresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "The action failed.");
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  /** Approval decisions debounce themselves: first click disables both. */
  async function decide(operation: () => Promise<unknown>) {
    setDecided(true);
    setBusy(true);
    setActionError(null);
    try {
      await operation();
      await refresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "The decision failed.");
      const staleApproval =
        reason instanceof ExecutionFaultError && reason.fault.error_code === "stale_approval";
      if (!staleApproval) setDecided(false);
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  function renderSurface() {
    if (!runId) {
      return (
        <>
          <RunConfigForm
            busy={busy}
            fieldErrors={fieldErrors}
            onSubmit={(targetApp, inputs) => void prepare(targetApp, inputs)}
          />
          <RunList onResume={resume} />
        </>
      );
    }
    if (state === "prepared" || state === "awaiting_start_confirmation") {
      return (
        <StartPreview
          busy={busy}
          preview={preview}
          status={status}
          onCancel={() => void act(() => executionApi.cancelRun(runId, "Cancelled before start."))}
          onConfirm={() => void act(() => executionApi.confirmStart(runId))}
        />
      );
    }
    if (state === "executing") {
      return (
        <div className="execution-live">
          <h3>Executing on the desktop</h3>
          <Timeline events={events} />
          <p className="kill-switch-hint">Kill switch: press Esc twice on the desktop.</p>
          <div className="action-bar">
            <span />
            <button
              className="secondary"
              disabled={busy}
              type="button"
              onClick={() => void act(() => executionApi.cancelRun(runId))}
            >
              Cancel run
            </button>
          </div>
        </div>
      );
    }
    if (state === "awaiting_commit_approval") {
      const staged = status?.staged_change ?? null;
      return (
        <>
          <StagedChangePanel
            disabled={busy || decided || degraded}
            staged={staged}
            timeoutSeconds={status?.approval_timeout_seconds ?? 0}
            onApprove={() => {
              if (!staged) return;
              void decide(() => executionApi.confirmCommit(runId, staged.payload_sha256, "local-user"));
            }}
            onReject={() => void decide(() => executionApi.rejectCommit(runId))}
          />
          <details className="timeline-details">
            <summary>Run timeline</summary>
            <Timeline events={events} />
          </details>
        </>
      );
    }
    if (state === "committing") {
      return (
        <>
          <div className="callout" role="status">
            <strong>Committing through the same session…</strong>
          </div>
          <details className="timeline-details">
            <summary>Run timeline</summary>
            <Timeline events={events} />
          </details>
        </>
      );
    }
    if (status && terminal) {
      return <ResultView fault={fault} status={status} onStartAnother={reset} />;
    }
    return <p role="status">Loading run…</p>;
  }

  return (
    <section aria-labelledby="execution-title" className="surface-card execution-page">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Execution</p>
          <h2 id="execution-title">Run an approved automation</h2>
        </div>
        {state ? <span className={`status status-${state}`}>{state.replaceAll("_", " ")}</span> : null}
      </div>
      {actionError ? (
        <div className="callout danger" role="alert">
          {actionError}
        </div>
      ) : null}
      {runId && degraded && !terminal ? (
        <p className="reconnecting" role="status">
          Reconnecting…
        </p>
      ) : null}
      {renderSurface()}
    </section>
  );
}

function StartPreview({
  busy,
  preview,
  status,
  onCancel,
  onConfirm,
}: {
  busy: boolean;
  preview: RunPreview | null;
  status: RunStatus | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const inputs = preview?.normalized_inputs ?? status?.request.inputs ?? {};
  const targetApp = preview?.request.target_app ?? status?.request.target_app ?? DEFAULT_TARGET_APP;
  return (
    <div className="run-preview">
      <h3>Confirm the interpreted command</h3>
      <dl className="preview-grid">
        <div>
          <dt>Automation</dt>
          <dd>{preview?.automation_name ?? "Approved automation"}</dd>
        </div>
        <div>
          <dt>Target app</dt>
          <dd>{targetAppFullLabel(targetApp)}</dd>
        </div>
        {Object.entries(inputs).map(([name, value]) => (
          <div key={name}>
            <dt>{inputLabel(name)}</dt>
            <dd>{value === null || value === undefined || value === "" ? "—" : String(value)}</dd>
          </div>
        ))}
      </dl>
      <p>Nothing runs on the desktop until you confirm.</p>
      <div className="decision-controls">
        <button className="secondary" disabled={busy} type="button" onClick={onCancel}>
          Cancel run
        </button>
        <button disabled={busy} type="button" onClick={onConfirm}>
          Confirm start
        </button>
      </div>
    </div>
  );
}

function RunList({ onResume }: { onResume: (runId: string) => void }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);

  useEffect(() => {
    let active = true;
    executionApi
      .listRuns()
      .then((page) => {
        if (active) setRuns(page.runs);
      })
      .catch(() => {
        if (active) setRuns([]);
      });
    return () => {
      active = false;
    };
  }, []);

  if (runs === null) return <p role="status">Loading previous runs…</p>;
  if (!runs.length) {
    return (
      <div className="empty-state">
        <h3>No runs yet</h3>
        <p>Use the form above to configure and prepare the first run.</p>
      </div>
    );
  }
  return (
    <div className="automation-list">
      {runs.map((run) => (
        <button className="automation-row" key={run.id} type="button" onClick={() => onResume(run.id)}>
          <span>
            <strong>
              {targetAppLabel(run.request.target_app)} · {formatLead(run.request.inputs)}
            </strong>
            <small>{new Date(run.created_at).toLocaleString()}</small>
          </span>
          <span className={`status status-${run.state}`}>{run.state.replaceAll("_", " ")}</span>
        </button>
      ))}
    </div>
  );
}

function formatLead(inputs: Record<string, unknown>): string {
  const lead = inputs.lead_name;
  if (typeof lead === "string" && lead) return lead;
  const firstName = typeof inputs.first_name === "string" ? inputs.first_name : "";
  const lastName = typeof inputs.last_name === "string" ? inputs.last_name : "";
  return `${firstName} ${lastName}`.trim() || "Run";
}
