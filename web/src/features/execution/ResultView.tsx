import { targetAppLabel, type ExecutionFaultPayload, type RunStatus } from "./api";
import { DiffTable } from "./DiffTable";

/** Error codes where a persistent change may already exist. */
const COMMIT_AMBIGUOUS_CODES = new Set(["commit_state_unknown", "commit_verify_failed"]);

/**
 * Terminal surface for succeeded, failed, and cancelled runs. `fault` carries
 * cause/remediation extracted from the failure event payload when available.
 */
export function ResultView({
  status,
  fault,
  onStartAnother,
}: {
  status: RunStatus;
  fault: ExecutionFaultPayload | null;
  onStartAnother: () => void;
}) {
  const result = status.result;
  const staged = status.staged_change;

  if (status.state === "succeeded") {
    const app = targetAppLabel(status.request.target_app);
    const headline =
      status.request.target_app === "crm_b"
        ? `Zero-shot: ${app} — exactly these fields changed`
        : `${app} — exactly these fields changed`;
    return (
      <div className="result-view">
        <h3>{headline}</h3>
        {staged ? <DiffTable changes={staged.changes} /> : null}
        {result?.verification_summary ? (
          <p className="verification-summary">{result.verification_summary}</p>
        ) : null}
        {result ? <small>Duration: {formatDuration(result.started_at, result.completed_at)}</small> : null}
        <div className="action-bar">
          <span />
          <button type="button" onClick={onStartAnother}>
            Start another run
          </button>
        </div>
      </div>
    );
  }

  const errorCode = result?.error_code ?? fault?.error_code ?? null;
  const commitAmbiguous = errorCode !== null && COMMIT_AMBIGUOUS_CODES.has(errorCode);
  const noChangesSaved = status.state === "cancelled" || !commitAmbiguous;
  const headline = status.state === "cancelled" ? "Run cancelled" : "Run failed";
  const message =
    result?.error_message ??
    result?.answer ??
    fault?.message ??
    (status.state === "cancelled" ? "The run was cancelled." : "The run failed.");

  return (
    <div className="result-view">
      <h3>{headline}</h3>
      <div className={status.state === "failed" ? "callout danger" : "callout"}>
        <strong>{message}</strong>
        {fault?.cause ? <p>Cause: {fault.cause}</p> : null}
        {fault?.remediation ? <p>Remediation: {fault.remediation}</p> : null}
        {errorCode ? <small>Error code: {errorCode}</small> : null}
      </div>
      {noChangesSaved ? <p className="no-changes">No changes were saved.</p> : null}
      {errorCode === "commit_state_unknown" ? (
        <p className="no-changes">Verify the CRM record manually before retrying.</p>
      ) : null}
      <div className="action-bar">
        <span />
        <button type="button" onClick={onStartAnother}>
          Start another run
        </button>
      </div>
    </div>
  );
}

function formatDuration(startedAt: string, completedAt: string): string {
  const elapsedMs = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(elapsedMs) || elapsedMs < 0) return "—";
  return `${(elapsedMs / 1000).toFixed(1)}s`;
}
