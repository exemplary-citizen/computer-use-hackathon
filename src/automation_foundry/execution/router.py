"""Member 2's execution API: run lifecycle, approvals, and live events.

Registered by Member 1's ``api.py`` (this file's router object is the whole
integration surface). Security posture for a loopback app: localhost is not
a trust boundary for a side-effecting approve endpoint, so mutating routes
require (a) an Origin/Referer that is local when the caller is a browser and
(b) a per-boot token minted at ``GET /api/execution/csrf-token`` — a
cross-origin page can fire a POST but can never read the token.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Body, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from automation_foundry.contracts import InvocationSource
from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.errors import ExecutionFault
from automation_foundry.execution.machine import InputValidationError, RunCoordinator

router = APIRouter(prefix="/api/execution", tags=["execution"])


def _include_voice_router() -> None:
    # Deferred import: voice.py imports helpers from this module.
    from automation_foundry.execution.voice import voice_router

    router.include_router(voice_router)


_FAULT_STATUS = {
    "run_conflict": 409,
    "stale_approval": 409,
    "approval_hash_mismatch": 409,
    "hash_mismatch": 409,
    "holo_unreachable": 503,
    "gradium_unavailable": 503,
}


class PrepareRunBody(BaseModel):
    """Payload for run preparation."""

    target_app: str = Field(min_length=1, max_length=255)
    inputs: dict[str, object]
    invocation_source: InvocationSource = InvocationSource.DASHBOARD
    max_steps: int | None = Field(default=None, ge=1)
    max_time_seconds: int | None = Field(default=None, ge=10)


class CommitDecisionBody(BaseModel):
    """Payload for commit approval or rejection."""

    payload_sha256: str = Field(default="", max_length=64)
    actor: str = Field(default="local-user", min_length=1, max_length=255)
    source: InvocationSource = InvocationSource.DASHBOARD


class CancelBody(BaseModel):
    """Payload for run cancellation."""

    reason: str = Field(default="Cancelled by user.", min_length=1, max_length=1_000)


@router.get("/health")
def execution_health() -> dict[str, str]:
    """Return the execution subsystem health marker."""
    return {"status": "ok", "subsystem": "execution"}


@router.get("/csrf-token")
async def csrf_token(request: Request) -> JSONResponse:
    """Mint-once per-boot token required on every mutating endpoint."""
    guard = _origin_guard(request)
    if guard is not None:
        return guard
    state = await _state(request)
    return JSONResponse({"token": state.api_token})


@router.post("/runs")
async def prepare_run(request: Request, body: PrepareRunBody) -> JSONResponse:
    """Validate inputs against the verified bundle and create a run preview."""
    guard = _mutation_guard(request, await _state(request))
    if guard is not None:
        return guard
    state = await _state(request)
    try:
        preview = await state.coordinator.prepare(
            body.target_app,
            dict(body.inputs),
            body.invocation_source,
            max_steps=body.max_steps,
            max_time_seconds=body.max_time_seconds,
        )
    except InputValidationError as error:
        return JSONResponse({"field_errors": error.field_errors}, status_code=422)
    except ExecutionFault as error:
        return _fault_response(error)
    return JSONResponse(preview.model_dump(mode="json"), status_code=201)


@router.post("/runs/{run_id}/confirm-start")
async def confirm_start(request: Request, run_id: UUID) -> JSONResponse:
    """Record start confirmation (the dashboard's Run click / spoken confirm)."""
    state = await _state(request)
    guard = _mutation_guard(request, state)
    if guard is not None:
        return guard
    try:
        await state.coordinator.confirm_start(run_id)
    except ExecutionFault as error:
        return _fault_response(error)
    except KeyError:
        return _not_found(run_id)
    return JSONResponse({"status": "executing"})


@router.post("/runs/{run_id}/confirm-commit")
async def confirm_commit(request: Request, run_id: UUID, body: CommitDecisionBody) -> JSONResponse:
    """Approve the staged change; commits through the same Holo session."""
    state = await _state(request)
    guard = _mutation_guard(request, state)
    if guard is not None:
        return guard
    try:
        await state.coordinator.approve_commit(run_id, body.payload_sha256, body.source, body.actor)
    except ExecutionFault as error:
        return _fault_response(error)
    except KeyError:
        return _not_found(run_id)
    return JSONResponse({"status": "approved"})


@router.post("/runs/{run_id}/reject-commit")
async def reject_commit(request: Request, run_id: UUID, body: CommitDecisionBody) -> JSONResponse:
    """Reject the staged change; the run cancels without saving."""
    state = await _state(request)
    guard = _mutation_guard(request, state)
    if guard is not None:
        return guard
    try:
        await state.coordinator.reject_commit(run_id, body.source, body.actor)
    except ExecutionFault as error:
        return _fault_response(error)
    except KeyError:
        return _not_found(run_id)
    return JSONResponse({"status": "rejected"})


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: UUID, body: CancelBody | None = Body(default=None)) -> JSONResponse:
    """Cancel from any nonterminal state; never relabels a dispatched commit."""
    state = await _state(request)
    guard = _mutation_guard(request, state)
    if guard is not None:
        return guard
    try:
        await state.coordinator.cancel(run_id, (body or CancelBody()).reason)
    except KeyError:
        return _not_found(run_id)
    return JSONResponse({"status": "cancelling"})


@router.post("/admin/force-release")
async def force_release(request: Request) -> JSONResponse:
    """Fail every active run and free the single-run slot (wedged-run escape)."""
    state = await _state(request)
    guard = _mutation_guard(request, state)
    if guard is not None:
        return guard
    released = await state.coordinator.force_release()
    return JSONResponse({"released": [str(run_id) for run_id in released]})


@router.get("/runs")
async def list_runs(request: Request) -> JSONResponse:
    """List run summaries, newest first."""
    state = await _state(request)
    return JSONResponse({"runs": state.coordinator.list_runs()})


@router.get("/runs/{run_id}")
async def run_status(request: Request, run_id: UUID) -> JSONResponse:
    """Return one run's state, staged change, result, and last event sequence."""
    state = await _state(request)
    try:
        return JSONResponse(state.coordinator.get_status(run_id))
    except KeyError:
        return _not_found(run_id)


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: UUID, after_seq: Annotated[int, Query(ge=-1)] = -1) -> JSONResponse:
    """Replay persisted events with sequence greater than ``after_seq``."""
    state = await _state(request)
    events = state.coordinator.events.replay(run_id, after_seq)
    return JSONResponse({"events": [event.model_dump(mode="json") for event in events]})


@router.websocket("/runs/{run_id}/events/stream")
async def run_event_stream(websocket: WebSocket, run_id: UUID, since_seq: int = -1) -> None:
    """Stream run events live after replaying everything past ``since_seq``."""
    if not _origin_ok(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    state = await _websocket_state(websocket)
    await websocket.accept()
    queue = state.coordinator.events.subscribe(run_id)
    try:
        last_sequence = since_seq
        for event in state.coordinator.events.replay(run_id, since_seq):
            await websocket.send_text(event.model_dump_json())
            last_sequence = event.sequence
        while True:
            event = await queue.get()
            if event.sequence <= last_sequence:
                continue
            await websocket.send_text(event.model_dump_json())
            last_sequence = event.sequence
    except WebSocketDisconnect:
        pass
    finally:
        state.coordinator.events.unsubscribe(run_id, queue)


class _ExecutionState:
    """Per-application singleton: coordinator + per-boot API token."""

    def __init__(self) -> None:
        self.coordinator = RunCoordinator(ExecutionSettings())
        self.api_token = secrets.token_urlsafe(32)
        self.started = False
        self.lock = asyncio.Lock()


async def _state_from_app(app_state: object) -> _ExecutionState:
    state: _ExecutionState | None = getattr(app_state, "execution_state", None)
    if state is None:
        state = _ExecutionState()
        app_state.execution_state = state  # type: ignore[attr-defined]
    async with state.lock:
        if not state.started:
            await state.coordinator.startup()
            state.started = True
    return state


async def _state(request: Request) -> _ExecutionState:
    return await _state_from_app(request.app.state)


async def _websocket_state(websocket: WebSocket) -> _ExecutionState:
    return await _state_from_app(websocket.app.state)


def _origin_ok(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser caller (curl, tests); CSRF is a browser attack
    host = urlsplit(origin).hostname
    return host in ("127.0.0.1", "localhost", "::1")


def _origin_guard(request: Request) -> JSONResponse | None:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if _origin_ok(origin):
        return None
    return JSONResponse({"error_code": "forbidden_origin", "message": "Cross-origin request rejected."}, 403)


def _mutation_guard(request: Request, state: _ExecutionState) -> JSONResponse | None:
    origin_problem = _origin_guard(request)
    if origin_problem is not None:
        return origin_problem
    token = request.headers.get("x-foundry-token")
    if not token or not secrets.compare_digest(token, state.api_token):
        return JSONResponse(
            {
                "error_code": "missing_token",
                "message": "Mutating execution endpoints require the X-Foundry-Token header.",
                "remediation": "GET /api/execution/csrf-token first and send its value as X-Foundry-Token.",
            },
            status_code=403,
        )
    return None


def _fault_response(error: ExecutionFault) -> JSONResponse:
    return JSONResponse(error.payload(), status_code=_FAULT_STATUS.get(error.spec.code, 409))


def _not_found(run_id: UUID) -> JSONResponse:
    return JSONResponse({"error_code": "unknown_run", "message": f"Unknown run: {run_id}"}, status_code=404)


_include_voice_router()
