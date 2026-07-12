"""Typed error taxonomy for the execution lane.

Every terminal failure carries problem + cause + remediation (and a docs
anchor) so redaction strips content, never actionability. Codes are stable:
the UI, RunResult.error_code, and the eval evidence all key off them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultSpec:
    """Stable description of one failure class."""

    code: str
    message: str
    cause: str
    remediation: str
    docs_anchor: str


class ExecutionFault(Exception):
    """Raised anywhere in the lane; always resolvable to a FaultSpec payload."""

    def __init__(self, spec: FaultSpec, detail: str | None = None):
        """Wrap a catalog entry with optional run-specific detail.

        Args:
            spec: Catalog entry describing the failure class.
            detail: Safe, run-specific context appended to the message.
        """
        super().__init__(f"{spec.code}: {spec.message}" + (f" ({detail})" if detail else ""))
        self.spec = spec
        self.detail = detail

    def payload(self) -> dict[str, str]:
        """Return the structured event/result payload for this fault."""
        payload = {
            "error_code": self.spec.code,
            "message": self.spec.message,
            "cause": self.spec.cause,
            "remediation": self.spec.remediation,
            "docs_anchor": self.spec.docs_anchor,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


def fault(code: str, detail: str | None = None) -> ExecutionFault:
    """Build an ExecutionFault from the catalog.

    Args:
        code: Stable error code registered in ``FAULTS``.
        detail: Safe, run-specific context.
    """
    return ExecutionFault(FAULTS[code], detail)


_SETTINGS_DEEPLINK = "x-apple.systempreferences:com.apple.preference.security"

FAULTS: dict[str, FaultSpec] = {
    spec.code: spec
    for spec in (
        FaultSpec(
            code="permission_accessibility",
            message="macOS Accessibility permission is missing for HoloDesktop.",
            cause="HoloDesktop cannot control the desktop without Accessibility access.",
            remediation=(
                f"Open {_SETTINGS_DEEPLINK}?Privacy_Accessibility, enable HoloDesktop, "
                "then restart HoloDesktop and the terminal that launches it."
            ),
            docs_anchor="evals#3.8-approval-and-cancellation-safety",
        ),
        FaultSpec(
            code="permission_screen_recording",
            message="macOS Screen Recording permission is missing for HoloDesktop.",
            cause="The agent cannot observe the screen without Screen Recording access.",
            remediation=(
                f"Open {_SETTINGS_DEEPLINK}?Privacy_ScreenCapture, enable HoloDesktop, "
                "then restart HoloDesktop after granting."
            ),
            docs_anchor="evals#3.8-approval-and-cancellation-safety",
        ),
        FaultSpec(
            code="permission_input_monitoring",
            message="macOS Input Monitoring permission is missing for HoloDesktop.",
            cause="Keyboard/mouse control requires Input Monitoring access.",
            remediation=(
                f"Open {_SETTINGS_DEEPLINK}?Privacy_ListenEvent, enable HoloDesktop, "
                "then restart HoloDesktop after granting."
            ),
            docs_anchor="evals#3.8-approval-and-cancellation-safety",
        ),
        FaultSpec(
            code="permission_microphone",
            message="Browser microphone permission is missing.",
            cause="Push-to-talk cannot capture audio without microphone access.",
            remediation="Allow microphone access for this site in the browser prompt, then hold push-to-talk again.",
            docs_anchor="spec#5.7-voice-invocation",
        ),
        FaultSpec(
            code="holo_unreachable",
            message="HoloDesktop is not installed, not logged in, or not reachable.",
            cause="The execution worker could not start or attach to a HoloDesktop session.",
            remediation=(
                "Install HoloDesktop per hub.hcompany.ai/holo-desktop-cli, sign in, and run "
                "`uv run python -m automation_foundry.execution.spike probe`."
            ),
            docs_anchor="spec#5.8-safe-two-turn-desktop-execution",
        ),
        FaultSpec(
            code="stale_session",
            message="The Holo session ended before commit approval completed.",
            cause="The live session was lost or expired during the approval wait.",
            remediation="Start a new run. The system never creates a fresh session just to click Save.",
            docs_anchor="spec#5.8-safe-two-turn-desktop-execution",
        ),
        FaultSpec(
            code="session_lost",
            message="The Holo session was lost mid-turn; no persistent action was attempted afterwards.",
            cause="HoloDesktop crashed or the session terminated during execution.",
            remediation="Check HoloDesktop is running, then start a new run.",
            docs_anchor="spec#5.8-safe-two-turn-desktop-execution",
        ),
        FaultSpec(
            code="wrong_app_state",
            message="The target application was not visible or not in a usable state.",
            cause="The requested CRM app bundle was missing, failed to launch, or exited during startup.",
            remediation=("Rebuild the CRM bundles if needed, restart ./scripts/run_demo.sh, and start a new run."),
            docs_anchor="evals#3.7-holo-execution-reliability",
        ),
        FaultSpec(
            code="budget_exceeded",
            message="The run exceeded its step or wall-clock budget and was cancelled.",
            cause="Holo did not finish within the configured max_steps / max_time_seconds.",
            remediation="Retry with a fresh run; raise budgets via the run request only if the task genuinely needs it.",
            docs_anchor="spec#14-edge-cases-and-failure-modes",
        ),
        FaultSpec(
            code="unsafe_stage",
            message="Persistent state changed during the stage turn; the run was stopped before approval.",
            cause="The agent performed a Save-equivalent action in turn one, violating the staging contract.",
            remediation="No approval was requested. Reset the fixture, review the skill wording, and start a new run.",
            docs_anchor="spec#5.8-safe-two-turn-desktop-execution",
        ),
        FaultSpec(
            code="malformed_stage_answer",
            message="The stage turn did not produce a verifiable staged-change report.",
            cause="The agent's answer could not be validated against the staged-change contract.",
            remediation="No commit was attempted. Start a new run; if it recurs, review the skill instructions.",
            docs_anchor="spec#10-public-contracts",
        ),
        FaultSpec(
            code="commit_verify_failed",
            message="Could not verify the saved state after commit.",
            cause="Persisted state after the commit turn did not match the approved staged change.",
            remediation="Inspect the CRM record manually before retrying (crm-fixture dump).",
            docs_anchor="evals#3.7-holo-execution-reliability",
        ),
        FaultSpec(
            code="commit_state_unknown",
            message="The process was interrupted during commit; the CRM may or may not have saved.",
            cause="A restart happened between commit dispatch and result persistence.",
            remediation="Verify the CRM record manually (crm-fixture dump) before starting a new run. Automatic retry is refused.",
            docs_anchor="spec#14-edge-cases-and-failure-modes",
        ),
        FaultSpec(
            code="interrupted_restart",
            message="The run was interrupted by an application restart before any commit was dispatched.",
            cause="The backend process exited while the run was active.",
            remediation="Start a new run; no persistent change was committed by this run.",
            docs_anchor="spec#14-edge-cases-and-failure-modes",
        ),
        FaultSpec(
            code="stale_approval",
            message="The approval arrived after the approval window expired.",
            cause="The run was already cancelled by the approval timeout.",
            remediation="Start a new run and approve within the countdown shown on the approval screen.",
            docs_anchor="spec#5.8-safe-two-turn-desktop-execution",
        ),
        FaultSpec(
            code="approval_hash_mismatch",
            message="The approval did not reference the currently staged change.",
            cause="The approval payload hash differs from the staged change shown to the user.",
            remediation="Refresh the run view and approve the staged change actually displayed.",
            docs_anchor="spec#10-public-contracts",
        ),
        FaultSpec(
            code="hash_mismatch",
            message="The approved bundle failed hash verification and was refused.",
            cause="An artifact's bytes differ from the hashes recorded at approval time.",
            remediation="Re-validate and re-approve the automation version; execution only runs hash-matching bundles.",
            docs_anchor="spec#5.4-review-and-approval",
        ),
        FaultSpec(
            code="run_conflict",
            message="Another run is already active; only one Holo run may execute at a time.",
            cause="A run is in executing, awaiting_commit_approval, or committing state.",
            remediation="Wait for the active run to finish, cancel it, or use the force-release admin command if it is wedged.",
            docs_anchor="spec#8-functional-requirements",
        ),
        FaultSpec(
            code="gradium_unavailable",
            message="Voice services are unavailable; the dashboard flow still works.",
            cause="The Gradium API could not be reached or the API key is missing/invalid.",
            remediation="Set FOUNDRY_GRADIUM_API_KEY and check connectivity, or continue with the dashboard.",
            docs_anchor="spec#5.7-voice-invocation",
        ),
        FaultSpec(
            code="internal_error",
            message="The run failed due to an unexpected internal error; no commit was attempted after the failure.",
            cause="An unhandled exception occurred in the execution worker.",
            remediation="Check backend logs for the stack trace, then start a new run.",
            docs_anchor="evals#6-expected-failure-handling",
        ),
        FaultSpec(
            code="cancelled_by_user",
            message="The run was cancelled before any persistent change was committed.",
            cause="A user cancelled from the dashboard, by voice, or with the kill switch.",
            remediation="Start a new run when ready.",
            docs_anchor="spec#7-flow-e-cancel-or-recover-from-failure",
        ),
        FaultSpec(
            code="commit_rejected",
            message="The staged change was rejected; nothing was saved.",
            cause="The user rejected the commit approval.",
            remediation="Start a new run with corrected inputs if the staged values were wrong.",
            docs_anchor="spec#5.8-safe-two-turn-desktop-execution",
        ),
    )
}
