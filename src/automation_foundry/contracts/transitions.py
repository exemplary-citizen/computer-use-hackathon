"""Run state-transition rules shared by API and execution implementations."""

from automation_foundry.contracts.models import RunState

ALLOWED_RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PREPARED: frozenset(
        {RunState.AWAITING_START_CONFIRMATION, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.AWAITING_START_CONFIRMATION: frozenset(
        {RunState.EXECUTING, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.EXECUTING: frozenset(
        {RunState.AWAITING_COMMIT_APPROVAL, RunState.SUCCEEDED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.AWAITING_COMMIT_APPROVAL: frozenset(
        {RunState.COMMITTING, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.COMMITTING: frozenset({RunState.SUCCEEDED, RunState.CANCELLED, RunState.FAILED}),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


def require_run_transition(current: RunState, target: RunState) -> None:
    """Raise when a run-state transition violates the safety state machine.

    Args:
        current: Current persisted run state.
        target: Requested next run state.

    Raises:
        ValueError: If the transition is not allowed.
    """
    if target not in ALLOWED_RUN_TRANSITIONS[current]:
        raise ValueError(f"Invalid run transition: {current.value} -> {target.value}")
