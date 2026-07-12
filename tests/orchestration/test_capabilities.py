"""Hermes-to-host capability mailbox tests."""

import hashlib
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import pytest

from automation_foundry.authoring.service import build_authoring_service
from automation_foundry.contracts import (
    CapabilityResponseStatus,
    FoundryCapability,
    FoundryCapabilityRequest,
    FoundryCapabilityResponse,
)
from automation_foundry.orchestration import (
    CapabilityMailboxConfig,
    CapabilityWorker,
    FoundryCapabilityProcessor,
    NemoClawCapabilityTransport,
    NemoClawCapabilityTransportConfig,
)
from automation_foundry.settings import AppSettings


class TestCapabilityMailbox:
    """Exercise deterministic local request/response behavior."""

    def setup_method(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.mailbox = CapabilityMailboxConfig(
            root=root / "mailbox",
            response_timeout_seconds=2,
            poll_interval_seconds=0.01,
        ).make()
        self.service = build_authoring_service(
            AppSettings(
                data_root=root / "automations",
                database_path=root / "foundry.sqlite3",
                published_skill_root=root / "skills",
            )
        )
        self.worker = CapabilityWorker(self.mailbox, FoundryCapabilityProcessor(self.service))

    def teardown_method(self) -> None:
        self.temporary_directory.cleanup()

    def test_health_round_trip_is_hash_bound(self) -> None:
        request = _request(FoundryCapability.HEALTH)

        with ThreadPoolExecutor(max_workers=1) as executor:
            response_future = executor.submit(self.mailbox.submit, request)
            _wait_for_pending_request(self.mailbox.config.root)
            assert self.worker.run_once() == 1
            response = response_future.result(timeout=2)

        expected_hash = hashlib.sha256(f"{request.model_dump_json()}\n".encode()).hexdigest()
        assert response.status is CapabilityResponseStatus.SUCCEEDED
        assert response.request_sha256 == expected_hash
        assert response.result == {"subsystem": "authoring", "generation_configured": False}
        assert not tuple((self.mailbox.config.root / "requests").iterdir())

    def test_authoring_status_returns_safe_fields_only(self) -> None:
        automation = self.service.store.create_automation("Update a lead")
        request = _request(FoundryCapability.AUTHORING_STATUS, automation.id)

        with ThreadPoolExecutor(max_workers=1) as executor:
            response_future = executor.submit(self.mailbox.submit, request)
            _wait_for_pending_request(self.mailbox.config.root)
            self.worker.run_once()
            response = response_future.result(timeout=2)

        assert response.status is CapabilityResponseStatus.SUCCEEDED
        assert response.result["automation_id"] == str(automation.id)
        assert response.result["name"] == "Update a lead"
        assert response.result["status"] == "draft"
        serialized = response.model_dump_json()
        assert str(self.service.store.config.root) not in serialized
        assert "database" not in serialized

    def test_expired_request_is_rejected_without_state_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        request = _request(FoundryCapability.AUTHORING_STATUS, uuid4(), lifetime_seconds=1)
        request.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        def fail_lookup(_automation_id: UUID):
            raise AssertionError("expired requests must fail before state lookup")

        monkeypatch.setattr(self.service.store, "get_manifest", fail_lookup)
        response = FoundryCapabilityProcessor(self.service).process(request, "0" * 64)

        assert response.status is CapabilityResponseStatus.REJECTED
        assert response.error_code == "request_expired"

    def test_conflicting_replay_is_rejected(self) -> None:
        request = _request(FoundryCapability.HEALTH)
        path = self.mailbox.config.root / "requests" / f"{request.id}.json"
        path.write_text(f"{request.model_dump_json()}\n", encoding="utf-8")
        conflicting = request.model_copy(update={"expires_at": request.expires_at + timedelta(seconds=1)})

        with pytest.raises(RuntimeError, match="already used with different bytes"):
            self.mailbox.submit(conflicting)

    def test_invalid_payload_receives_generic_rejection(self) -> None:
        request_id = uuid4()
        payload = b'{"untrusted":"local/path/or/provider/error"}\n'
        path = self.mailbox.config.root / "requests" / f"{request_id}.json"
        path.write_bytes(payload)

        assert self.worker.run_once() == 1

        response_path = self.mailbox.config.root / "responses" / path.name
        response = FoundryCapabilityResponse.model_validate_json(response_path.read_bytes())
        assert response.status is CapabilityResponseStatus.REJECTED
        assert response.error_code == "invalid_request"
        assert response.error_message == "The Foundry capability request is invalid"
        assert "provider" not in response.model_dump_json()

    def test_contract_rejects_wrong_arguments_and_long_lifetime(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="does not accept automation_id"):
            FoundryCapabilityRequest(
                id=uuid4(),
                capability=FoundryCapability.HEALTH,
                automation_id=uuid4(),
                requested_at=now,
                expires_at=now + timedelta(seconds=30),
            )
        with pytest.raises(ValueError, match="requires automation_id"):
            FoundryCapabilityRequest(
                id=uuid4(),
                capability=FoundryCapability.AUTHORING_STATUS,
                requested_at=now,
                expires_at=now + timedelta(seconds=30),
            )
        with pytest.raises(ValueError, match="between 0 and 120 seconds"):
            FoundryCapabilityRequest(
                id=uuid4(),
                capability=FoundryCapability.HEALTH,
                requested_at=now,
                expires_at=now + timedelta(seconds=121),
            )
        with pytest.raises(ValueError, match="must include a timezone"):
            FoundryCapabilityRequest(
                id=uuid4(),
                capability=FoundryCapability.HEALTH,
                requested_at=datetime.now(),
                expires_at=datetime.now() + timedelta(seconds=30),
            )


class TestNemoClawCapabilityTransport:
    """Exercise fixed CLI commands without touching a live sandbox."""

    def test_lists_reads_uploads_and_archives_only_canonical_ids(self) -> None:
        request_id = uuid4()
        request = _request(FoundryCapability.HEALTH, request_id=request_id)
        request_payload = f"{request.model_dump_json()}\n"
        commands: list[tuple[str, ...]] = []

        def run_command(command, **_kwargs):
            normalized = tuple(command)
            commands.append(normalized)
            if "find" in normalized:
                output = f"/sandbox/workspace/automation-foundry/capabilities/requests/{request_id}.json\n"
            elif "head" in normalized:
                output = request_payload
            else:
                output = ""
            return subprocess.CompletedProcess(command, 0, output, "")

        transport = NemoClawCapabilityTransport(
            NemoClawCapabilityTransportConfig(sandbox_name="hai-hermes"),
            runner=run_command,
        )
        transport.initialize()
        assert transport.pending_request_ids() == (request_id,)
        assert transport.read_request_payload(request_id) == request_payload.encode()
        response = FoundryCapabilityResponse(
            request_id=request_id,
            request_sha256=hashlib.sha256(request_payload.encode()).hexdigest(),
            status=CapabilityResponseStatus.SUCCEEDED,
            result={"subsystem": "authoring"},
        )
        transport.write_response(response)
        transport.complete_request(request_id)

        assert any(command[2] == "upload" for command in commands)
        assert any(".pending" in " ".join(command) for command in commands)
        assert any("mv" in command for command in commands)
        assert all("provider" not in " ".join(command) for command in commands)

    def test_rejects_request_path_outside_fixed_mailbox(self) -> None:
        def run_command(command, **_kwargs):
            output = "/sandbox/workspace/elsewhere/00000000-0000-0000-0000-000000000000.json\n"
            return subprocess.CompletedProcess(command, 0, output, "")

        transport = NemoClawCapabilityTransport(
            NemoClawCapabilityTransportConfig(sandbox_name="hai-hermes"),
            runner=run_command,
        )

        with pytest.raises(RuntimeError, match="path is invalid"):
            transport.pending_request_ids()

    def test_redacts_cli_failure_output(self) -> None:
        def run_command(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, "", "secret provider payload")

        transport = NemoClawCapabilityTransport(
            NemoClawCapabilityTransportConfig(sandbox_name="hai-hermes"),
            runner=run_command,
        )

        with pytest.raises(RuntimeError, match="NemoClaw capability command failed") as error:
            transport.initialize()
        assert "secret" not in str(error.value)


def _request(
    capability: FoundryCapability,
    automation_id: UUID | None = None,
    *,
    request_id: UUID | None = None,
    lifetime_seconds: int = 30,
) -> FoundryCapabilityRequest:
    now = datetime.now(UTC)
    return FoundryCapabilityRequest(
        id=request_id or uuid4(),
        capability=capability,
        automation_id=automation_id,
        requested_at=now,
        expires_at=now + timedelta(seconds=lifetime_seconds),
    )


def _wait_for_pending_request(root: Path) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if tuple((root / "requests").glob("*.json")):
            return
        time.sleep(0.01)
    raise AssertionError("request did not reach the mailbox")
