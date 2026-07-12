"""Bounded request/response transport between sandboxed Hermes and the host."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Protocol, TypeAlias
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from automation_foundry.authoring.service import AuthoringService
from automation_foundry.contracts import (
    CapabilityResponseStatus,
    FoundryCapability,
    FoundryCapabilityRequest,
    FoundryCapabilityResponse,
)

CommandRunner: TypeAlias = Callable[..., subprocess.CompletedProcess[str]]
DEFAULT_MAX_PAYLOAD_BYTES = 65_536


class CapabilityMailboxConfig(BaseModel):
    """Sandbox-visible filesystem queue settings."""

    root: Path = Path("/sandbox/workspace/automation-foundry/capabilities")
    """Capability mailbox root visible to the sandboxed MCP server."""
    response_timeout_seconds: float = Field(default=30, gt=0, le=120)
    """Maximum time an MCP tool waits for the trusted host response."""
    poll_interval_seconds: float = Field(default=0.1, gt=0, le=5)
    """Delay between response-file checks."""
    max_payload_bytes: int = Field(default=DEFAULT_MAX_PAYLOAD_BYTES, ge=1_024, le=1_000_000)
    """Maximum request or response size."""

    def make(self) -> CapabilityMailbox:
        """Build and initialize a mailbox."""
        mailbox = CapabilityMailbox(self)
        mailbox.initialize()
        return mailbox


class NemoClawCapabilityTransportConfig(BaseModel):
    """Authenticated host transport for a mailbox inside NemoClaw."""

    sandbox_name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    """Existing NemoClaw sandbox name."""
    binary: str = Field(default="nemohermes", min_length=1)
    """NemoHermes executable path or command name."""
    sandbox_root: PurePosixPath = PurePosixPath("/sandbox/workspace/automation-foundry/capabilities")
    """Mailbox root inside the sandbox."""
    command_timeout_seconds: float = Field(default=30, gt=0, le=120)
    """Maximum duration of one authenticated transport command."""
    max_payload_bytes: int = Field(default=DEFAULT_MAX_PAYLOAD_BYTES, ge=1_024, le=1_000_000)
    """Maximum request or response size."""
    max_pending_requests: int = Field(default=100, ge=1, le=1_000)
    """Maximum request filenames accepted from one listing."""

    def make(self) -> NemoClawCapabilityTransport:
        """Build and initialize the authenticated transport."""
        transport = NemoClawCapabilityTransport(self)
        transport.initialize()
        return transport


class CapabilityMailbox:
    """Atomically exchange bounded capability messages through one directory."""

    def __init__(self, config: CapabilityMailboxConfig):
        """Initialize the mailbox adapter.

        Args:
            config: Queue paths, limits, and wait settings.
        """
        self.config = config

    def initialize(self) -> None:
        """Create fixed mailbox directories without following request paths."""
        for name in ("requests", "responses", "processed"):
            (self.config.root / name).mkdir(parents=True, exist_ok=True)

    def submit(self, request: FoundryCapabilityRequest) -> FoundryCapabilityResponse:
        """Write one request and wait for its hash-matching host response.

        Args:
            request: Strict, short-lived capability request.

        Returns:
            Validated response bound to the exact request bytes.

        Raises:
            RuntimeError: If the request conflicts, expires, or times out.
        """
        payload = _serialize_model(request)
        if len(payload) > self.config.max_payload_bytes:
            raise RuntimeError("Foundry capability request exceeds the payload limit")
        request_path = self.config.root / "requests" / f"{request.id}.json"
        processed_path = self.config.root / "processed" / request_path.name
        _write_exclusive_or_match(request_path, processed_path, payload)
        request_sha256 = hashlib.sha256(payload).hexdigest()
        return self._wait_for_response(request, request_sha256)

    def pending_request_ids(self) -> tuple[UUID, ...]:
        """List canonical pending request identifiers for a local mounted mailbox."""
        requests_root = self.config.root / "requests"
        identifiers: list[UUID] = []
        for path in sorted(requests_root.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                identifiers.append(UUID(path.stem))
            except ValueError:
                continue
        return tuple(identifiers)

    @property
    def max_payload_bytes(self) -> int:
        """Return the configured request limit used by the host worker."""
        return self.config.max_payload_bytes

    def read_request_payload(self, request_id: UUID) -> bytes:
        """Read one bounded pending request from a local mounted mailbox.

        Args:
            request_id: Identifier derived from a canonical request filename.

        Returns:
            Raw request bytes, including one overflow byte when oversized.
        """
        path = self.config.root / "requests" / f"{request_id}.json"
        return _read_bounded(path, self.config.max_payload_bytes)

    def write_response(self, response: FoundryCapabilityResponse) -> None:
        """Atomically write a trusted host response.

        Args:
            response: Validated response bound to request bytes.
        """
        payload = _serialize_model(response)
        if len(payload) > self.config.max_payload_bytes:
            raise RuntimeError("Foundry capability response exceeds the payload limit")
        path = self.config.root / "responses" / f"{response.request_id}.json"
        _atomic_write(path, payload)

    def complete_request(self, request_id: UUID) -> None:
        """Move a locally processed request out of the pending directory.

        Args:
            request_id: Processed request identifier.
        """
        source = self.config.root / "requests" / f"{request_id}.json"
        destination = self.config.root / "processed" / source.name
        if source.is_file() and not source.is_symlink():
            os.replace(source, destination)

    def _wait_for_response(self, request: FoundryCapabilityRequest, request_sha256: str) -> FoundryCapabilityResponse:
        remaining = (request.expires_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise RuntimeError("Foundry capability request expired before submission")
        timeout = min(self.config.response_timeout_seconds, remaining)
        deadline = time.monotonic() + timeout
        response_path = self.config.root / "responses" / f"{request.id}.json"
        while time.monotonic() < deadline:
            if response_path.is_file() and not response_path.is_symlink():
                raw = _read_bounded(response_path, self.config.max_payload_bytes)
                if len(raw) > self.config.max_payload_bytes:
                    raise RuntimeError("Foundry capability response exceeds the payload limit")
                response = FoundryCapabilityResponse.model_validate_json(raw)
                if response.request_id != request.id or response.request_sha256 != request_sha256:
                    raise RuntimeError("Foundry capability response does not match the request")
                return response
            time.sleep(self.config.poll_interval_seconds)
        raise RuntimeError("Foundry host capability worker did not respond before timeout")


class NemoClawCapabilityTransport:
    """Read and answer sandbox mailbox requests through authenticated CLI transport."""

    def __init__(
        self,
        config: NemoClawCapabilityTransportConfig,
        runner: CommandRunner = subprocess.run,
    ):
        """Initialize the transport.

        Args:
            config: Sandbox name, paths, limits, and command timeout.
            runner: Injectable command runner for deterministic tests.
        """
        self.config = config
        self.runner = runner
        self.root = _validate_sandbox_root(config.sandbox_root)

    def initialize(self) -> None:
        """Create the fixed mailbox directories inside the sandbox."""
        self._exec("mkdir", "-p", *(str(self.root / name) for name in ("requests", "responses", "processed")))

    def pending_request_ids(self) -> tuple[UUID, ...]:
        """List bounded canonical request identifiers inside NemoClaw."""
        output = self._exec(
            "find",
            str(self.root / "requests"),
            "-maxdepth",
            "1",
            "-type",
            "f",
            "-name",
            "*.json",
            "-print",
        )
        lines = tuple(line for line in output.splitlines() if line)
        if len(lines) > self.config.max_pending_requests:
            raise RuntimeError("NemoClaw capability request listing exceeds its limit")
        identifiers: list[UUID] = []
        requests_root = self.root / "requests"
        for line in lines:
            path = PurePosixPath(line)
            if path.parent != requests_root or path.suffix != ".json":
                raise RuntimeError("NemoClaw capability request path is invalid")
            try:
                identifiers.append(UUID(path.stem))
            except ValueError as exc:
                raise RuntimeError("NemoClaw capability request name is invalid") from exc
        return tuple(sorted(identifiers, key=str))

    @property
    def max_payload_bytes(self) -> int:
        """Return the configured request limit used by the host worker."""
        return self.config.max_payload_bytes

    def read_request_payload(self, request_id: UUID) -> bytes:
        """Read one bounded request without exposing arbitrary sandbox paths.

        Args:
            request_id: Identifier from a validated request listing.

        Returns:
            Raw request bytes, including one overflow byte when oversized.
        """
        path = self.root / "requests" / f"{request_id}.json"
        output = self._exec("head", "-c", str(self.config.max_payload_bytes + 1), str(path))
        return output.encode("utf-8")

    def write_response(self, response: FoundryCapabilityResponse) -> None:
        """Upload one bounded response to the fixed response directory.

        Args:
            response: Validated host response.
        """
        payload = _serialize_model(response)
        if len(payload) > self.config.max_payload_bytes:
            raise RuntimeError("Foundry capability response exceeds the payload limit")
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(prefix=f"{response.request_id}-", suffix=".json", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            destination = self.root / "responses" / f"{response.request_id}.json"
            pending = self.root / "responses" / f".{response.request_id}.pending"
            self._upload(temporary_path, pending)
            self._exec("mv", str(pending), str(destination))
            self._exec("test", "-s", str(destination))
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def complete_request(self, request_id: UUID) -> None:
        """Archive one processed sandbox request.

        Args:
            request_id: Processed request identifier.
        """
        source = self.root / "requests" / f"{request_id}.json"
        destination = self.root / "processed" / source.name
        self._exec("mv", str(source), str(destination))

    def _exec(self, *command: str) -> str:
        return self._run(
            (
                self.config.binary,
                self.config.sandbox_name,
                "exec",
                "--timeout",
                str(max(1, int(self.config.command_timeout_seconds))),
                "--no-tty",
                "--",
                *command,
            ),
            "command",
        )

    def _upload(self, source: Path, destination: PurePosixPath) -> None:
        self._run(
            (
                self.config.binary,
                self.config.sandbox_name,
                "upload",
                str(source),
                str(destination),
            ),
            "upload",
        )

    def _run(self, command: Sequence[str], operation: str) -> str:
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"NemoClaw capability {operation} failed") from exc
        if result.returncode != 0:
            raise RuntimeError(f"NemoClaw capability {operation} failed")
        return result.stdout


class HostCapabilityTransport(Protocol):
    """Operations required by the trusted host worker."""

    def pending_request_ids(self) -> tuple[UUID, ...]:
        """List pending canonical request IDs."""

    @property
    def max_payload_bytes(self) -> int:
        """Return the maximum accepted request size."""

    def read_request_payload(self, request_id: UUID) -> bytes:
        """Read one bounded request payload."""

    def write_response(self, response: FoundryCapabilityResponse) -> None:
        """Write one trusted response."""

    def complete_request(self, request_id: UUID) -> None:
        """Archive one processed request."""


class FoundryCapabilityProcessor:
    """Execute read-only capability requests against trusted authoring services."""

    def __init__(self, authoring: AuthoringService):
        """Initialize the read-only dispatcher.

        Args:
            authoring: Shared host authoring service used by the dashboard.
        """
        self.authoring = authoring

    def process(self, request: FoundryCapabilityRequest, request_sha256: str) -> FoundryCapabilityResponse:
        """Validate freshness and execute one supported read-only operation.

        Args:
            request: Strict request parsed from untrusted sandbox bytes.
            request_sha256: Host-computed hash of the exact request bytes.

        Returns:
            Safe, hash-bound response.
        """
        if request.expires_at <= datetime.now(UTC):
            return _error_response(
                request.id,
                request_sha256,
                CapabilityResponseStatus.REJECTED,
                "request_expired",
                "The Foundry capability request expired",
            )
        if request.capability is FoundryCapability.HEALTH:
            return FoundryCapabilityResponse(
                request_id=request.id,
                request_sha256=request_sha256,
                status=CapabilityResponseStatus.SUCCEEDED,
                result={
                    "subsystem": "authoring",
                    "generation_configured": self.authoring.generation_configured,
                },
            )
        return self._authoring_status(request, request_sha256)

    def _authoring_status(
        self,
        request: FoundryCapabilityRequest,
        request_sha256: str,
    ) -> FoundryCapabilityResponse:
        automation_id = request.automation_id
        if automation_id is None:
            return _error_response(
                request.id,
                request_sha256,
                CapabilityResponseStatus.REJECTED,
                "invalid_request",
                "Authoring status requires an automation identifier",
            )
        try:
            manifest = self.authoring.store.get_manifest(automation_id)
        except KeyError:
            return _error_response(
                request.id,
                request_sha256,
                CapabilityResponseStatus.REJECTED,
                "automation_not_found",
                "The requested automation does not exist",
            )
        progress = self.authoring.processing_progress(automation_id)
        return FoundryCapabilityResponse(
            request_id=request.id,
            request_sha256=request_sha256,
            status=CapabilityResponseStatus.SUCCEEDED,
            result={
                "automation_id": str(manifest.id),
                "name": manifest.name,
                "status": manifest.status.value,
                "current_version": manifest.current_version,
                "approved_version": manifest.approved_version,
                "progress": progress.model_dump(mode="json") if progress is not None else None,
            },
        )


class CapabilityWorker:
    """Consume sandbox requests through a trusted host transport."""

    def __init__(self, transport: HostCapabilityTransport, processor: FoundryCapabilityProcessor):
        """Initialize the worker.

        Args:
            transport: Fixed-path request and response transport.
            processor: Trusted capability dispatcher.
        """
        self.transport = transport
        self.processor = processor

    def run_once(self) -> int:
        """Process each currently pending request at most once.

        Returns:
            Number of requests archived during this pass.
        """
        processed = 0
        for request_id in self.transport.pending_request_ids():
            raw = self.transport.read_request_payload(request_id)
            request_sha256 = hashlib.sha256(raw).hexdigest()
            response = self._response_for_payload(request_id, raw, request_sha256)
            self.transport.write_response(response)
            self.transport.complete_request(request_id)
            processed += 1
        return processed

    def run_forever(self, poll_interval_seconds: float = 0.25) -> None:
        """Poll until the process is interrupted.

        Args:
            poll_interval_seconds: Delay after an empty queue pass.
        """
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        while True:
            if self.run_once() == 0:
                time.sleep(poll_interval_seconds)

    def _response_for_payload(
        self,
        request_id: UUID,
        raw: bytes,
        request_sha256: str,
    ) -> FoundryCapabilityResponse:
        if len(raw) > self.transport.max_payload_bytes:
            return _error_response(
                request_id,
                request_sha256,
                CapabilityResponseStatus.REJECTED,
                "request_too_large",
                "The Foundry capability request exceeds the payload limit",
            )
        try:
            request = FoundryCapabilityRequest.model_validate_json(raw)
        except ValidationError:
            return _error_response(
                request_id,
                request_sha256,
                CapabilityResponseStatus.REJECTED,
                "invalid_request",
                "The Foundry capability request is invalid",
            )
        if request.id != request_id:
            return _error_response(
                request_id,
                request_sha256,
                CapabilityResponseStatus.REJECTED,
                "request_id_mismatch",
                "The Foundry capability request identifier does not match its envelope",
            )
        return self.processor.process(request, request_sha256)


def _serialize_model(model: BaseModel) -> bytes:
    return f"{model.model_dump_json()}\n".encode()


def _write_exclusive_or_match(request_path: Path, processed_path: Path, payload: bytes) -> None:
    existing_path = request_path if request_path.exists() else processed_path
    if existing_path.exists():
        if existing_path.is_symlink() or existing_path.read_bytes() != payload:
            raise RuntimeError("Foundry capability request ID was already used with different bytes")
        return
    descriptor = os.open(request_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        request_path.unlink(missing_ok=True)
        raise


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Foundry capability payload is missing or unsafe")
    with path.open("rb") as stream:
        return stream.read(max_bytes + 1)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_sandbox_root(root: PurePosixPath) -> PurePosixPath:
    sandbox = PurePosixPath("/sandbox")
    if not root.is_absolute() or root == sandbox or not root.is_relative_to(sandbox) or ".." in root.parts:
        raise ValueError("NemoClaw capability root must remain below /sandbox")
    return root


def _error_response(
    request_id: UUID,
    request_sha256: str,
    status: CapabilityResponseStatus,
    error_code: str,
    error_message: str,
) -> FoundryCapabilityResponse:
    return FoundryCapabilityResponse(
        request_id=request_id,
        request_sha256=request_sha256,
        status=status,
        error_code=error_code,
        error_message=error_message,
    )
