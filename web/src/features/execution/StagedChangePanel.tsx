import { useEffect, useState } from "react";

import { targetAppFullLabel, targetAppLabel, type StagedChange } from "./api";
import { DiffTable } from "./DiffTable";

/**
 * The hero approval surface: staged diff, record identity, the agent's
 * visible verification, a client-side countdown, and the decision controls.
 * No <form> wraps the controls, so Enter never triggers Approve.
 */
export function StagedChangePanel({
  staged,
  timeoutSeconds,
  disabled,
  onApprove,
  onReject,
}: {
  staged: StagedChange | null;
  timeoutSeconds: number;
  /** Disables both controls: in-flight call, prior decision, or degraded link. */
  disabled: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const remaining = useCountdown(timeoutSeconds);
  const changeCount = staged?.changes.length ?? 0;
  const approveLabel = staged
    ? `Approve ${changeCount} ${changeCount === 1 ? "change" : "changes"} to ${targetAppLabel(staged.target_app)}`
    : "Approve";

  return (
    <div className="staged-change">
      <h3>Approve the staged change</h3>
      {staged ? (
        <>
          <p className="record-identity">
            <strong>{staged.record_identity}</strong> · {targetAppFullLabel(staged.target_app)}
          </p>
          <DiffTable changes={staged.changes} />
          <blockquote className="verification-quote">{staged.visible_verification}</blockquote>
        </>
      ) : (
        <p role="status">Loading the staged change…</p>
      )}
      <p className="countdown" role="timer">
        Approval window: {remaining}s remaining. Nothing is saved without approval.
      </p>
      <div className="decision-controls">
        <button className="secondary" disabled={disabled} type="button" onClick={onReject}>
          Reject
        </button>
        <button className="danger-button" disabled={disabled || !staged} type="button" onClick={onApprove}>
          {approveLabel}
        </button>
      </div>
    </div>
  );
}

function useCountdown(totalSeconds: number): number {
  const [remaining, setRemaining] = useState(totalSeconds);
  const [trackedTotal, setTrackedTotal] = useState(totalSeconds);
  if (trackedTotal !== totalSeconds) {
    setTrackedTotal(totalSeconds);
    setRemaining(totalSeconds);
  }

  useEffect(() => {
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      const elapsed = Math.floor((Date.now() - startedAt) / 1000);
      setRemaining(Math.max(0, totalSeconds - elapsed));
    }, 250);
    return () => window.clearInterval(timer);
  }, [totalSeconds]);

  return remaining;
}
