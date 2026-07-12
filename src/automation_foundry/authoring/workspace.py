"""NemoClaw shared-workspace staging with a narrow host/sandbox boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from automation_foundry.authoring.evidence import EvidencePackage
from automation_foundry.storage import ArtifactStore

CommandRunner: TypeAlias = Callable[..., subprocess.CompletedProcess[str]]


class WorkspaceConfig(BaseModel):
    """Host staging path and sandbox path for the NemoClaw shared workspace."""

    host_mount: Path
    """Host-side mount or local job-staging directory."""
    sandbox_root: PurePosixPath = PurePosixPath("/sandbox/workspace")
    """Path visible to Hermes inside the sandbox."""
    require_mount: bool = True
    """Require host_mount to be a real mount point outside tests."""
    nemoclaw_sandbox_name: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    """NemoClaw sandbox that receives jobs through authenticated upload transport."""
    nemohermes_binary: str = Field(default="nemohermes", min_length=1)
    """Host NemoHermes executable used only when upload transport is configured."""
    transfer_timeout_seconds: float = Field(default=120, gt=0)
    """Timeout for each bounded workspace transport command."""
    max_result_bytes: int = Field(default=5_000_000, gt=0)
    """Maximum accepted generation result size."""

    def make(self, store: ArtifactStore) -> WorkspaceBridge:
        """Build a workspace bridge for an artifact store."""
        transport = None
        if self.nemoclaw_sandbox_name is not None:
            transport = NemoClawWorkspaceTransportConfig(
                sandbox_name=self.nemoclaw_sandbox_name,
                binary=self.nemohermes_binary,
                timeout_seconds=self.transfer_timeout_seconds,
            ).make()
        return WorkspaceBridge(self, store, transport)


class GenerationJob(BaseModel):
    """Stable host and sandbox locations for one generation job."""

    id: UUID
    automation_id: UUID
    host_root: Path
    sandbox_root: PurePosixPath
    evidence_path: PurePosixPath
    schema_path: PurePosixPath
    output_path: PurePosixPath


class NemoClawWorkspaceTransportConfig(BaseModel):
    """Configuration for authenticated host-to-sandbox job publication."""

    sandbox_name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    """Existing NemoClaw sandbox name."""
    binary: str = Field(default="nemohermes", min_length=1)
    """NemoHermes executable path or command name."""
    timeout_seconds: float = Field(default=120, gt=0)
    """Timeout for each health, directory, or upload command."""

    def make(self) -> NemoClawWorkspaceTransport:
        """Build the authenticated workspace transport."""
        return NemoClawWorkspaceTransport(self)


class NemoClawWorkspaceTransport:
    """Publish one bounded generation job through NemoClaw's upload command."""

    def __init__(self, config: NemoClawWorkspaceTransportConfig, runner: CommandRunner = subprocess.run):
        """Initialize the command adapter.

        Args:
            config: Sandbox, executable, and command timeout settings.
            runner: Injectable subprocess runner used by deterministic tests.
        """
        self.config = config
        self.runner = runner
        self.sandbox_root: PurePosixPath | None = None

    def initialize(self, sandbox_root: PurePosixPath) -> None:
        """Create and verify the sandbox workspace root.

        Args:
            sandbox_root: Absolute path below `/sandbox` visible to Hermes.
        """
        self.sandbox_root = _validate_sandbox_root(sandbox_root)
        self._exec("mkdir", "-p", str(self.sandbox_root / "automation-foundry/jobs"))
        self.ensure_ready()

    def ensure_ready(self) -> None:
        """Fail closed unless the configured sandbox workspace remains writable."""
        if self.sandbox_root is None:
            raise RuntimeError("NemoClaw workspace transport is not initialized")
        self._exec("test", "-d", str(self.sandbox_root))
        self._exec("test", "-w", str(self.sandbox_root))

    def publish(self, job: GenerationJob) -> None:
        """Upload one staged job and verify its required evidence files.

        Args:
            job: Locally staged job with its exact sandbox destination.
        """
        if self.sandbox_root is None:
            raise RuntimeError("NemoClaw workspace transport is not initialized")
        expected_root = self.sandbox_root / "automation-foundry/jobs" / str(job.id)
        if job.sandbox_root != expected_root or not job.host_root.is_dir():
            raise RuntimeError("NemoClaw workspace job path is invalid")
        destination_parent = expected_root.parent
        self._exec("mkdir", "-p", str(destination_parent))
        self._run(
            (
                self.config.binary,
                self.config.sandbox_name,
                "upload",
                str(job.host_root),
                f"{destination_parent}/",
            ),
            "upload",
        )
        self._exec("test", "-s", str(job.evidence_path))
        self._exec("test", "-s", str(job.schema_path))

    def _exec(self, *command: str) -> None:
        self._run(
            (
                self.config.binary,
                self.config.sandbox_name,
                "exec",
                "--timeout",
                str(max(1, int(self.config.timeout_seconds))),
                "--no-tty",
                "--",
                *command,
            ),
            "command",
        )

    def _run(self, command: Sequence[str], operation: str) -> None:
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"NemoClaw workspace {operation} failed") from exc
        if result.returncode != 0:
            raise RuntimeError(f"NemoClaw workspace {operation} failed")


class WorkspaceBridge:
    """Stage selected evidence and retrieve bounded untrusted results."""

    def __init__(
        self,
        config: WorkspaceConfig,
        store: ArtifactStore,
        transport: NemoClawWorkspaceTransport | None = None,
    ):
        """Initialize the shared-workspace boundary.

        Args:
            config: Mount paths and result limits.
            store: Canonical host artifact store.
            transport: Optional authenticated upload adapter for an unmounted staging directory.
        """
        self.config = config
        self.store = store
        self.transport = transport

    def initialize(self) -> None:
        """Verify the mount and create the application workspace marker."""
        if not self.config.host_mount.is_dir():
            raise RuntimeError(f"NemoClaw workspace is unavailable: {self.config.host_mount}")
        if self.config.require_mount and not os.path.ismount(self.config.host_mount):
            raise RuntimeError(f"NemoClaw workspace is not mounted: {self.config.host_mount}")
        if not os.access(self.config.host_mount, os.R_OK | os.W_OK | os.X_OK):
            raise RuntimeError(f"NemoClaw workspace is not readable and writable: {self.config.host_mount}")
        application_root = self.config.host_mount / "automation-foundry"
        application_root.mkdir(exist_ok=True)
        marker = application_root / ".workspace-version"
        marker.write_text("1\n", encoding="utf-8")
        if self.transport is not None:
            self.transport.initialize(self.config.sandbox_root)

    def stage_generation(
        self,
        automation_id: UUID,
        evidence: EvidencePackage,
        *,
        result_schema: dict[str, object],
    ) -> GenerationJob:
        """Copy normalized evidence and selected frames into a new job.

        Args:
            automation_id: Owning automation.
            evidence: Persisted normalized evidence package.
            result_schema: JSON schema Hermes must satisfy.

        Returns:
            Stable job paths on both sides of the mount.
        """
        self._assert_ready()
        if evidence.automation_id != automation_id:
            raise ValueError("Evidence package belongs to another automation")
        job_id = uuid4()
        relative_root = Path("automation-foundry") / "jobs" / str(job_id)
        host_root = self.config.host_mount / relative_root
        input_root = host_root / "input"
        output_root = host_root / "output"
        input_root.mkdir(parents=True, exist_ok=False)
        output_root.mkdir()

        evidence_destination = input_root / "evidence.json"
        evidence_destination.write_text(f"{evidence.model_dump_json(indent=2)}\n", encoding="utf-8")
        (input_root / "bundle.schema.json").write_text(
            f"{json.dumps(result_schema, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )
        self._copy_frames(automation_id, evidence, input_root)

        sandbox_job_root = self.config.sandbox_root / PurePosixPath(relative_root.as_posix())
        request = {
            "schema_version": "1.0",
            "job_id": str(job_id),
            "automation_id": str(automation_id),
            "evidence_path": str(sandbox_job_root / "input/evidence.json"),
            "schema_path": str(sandbox_job_root / "input/bundle.schema.json"),
            "output_path": str(sandbox_job_root / "output/bundle.json"),
        }
        (host_root / "request.json").write_text(
            f"{json.dumps(request, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )
        job = GenerationJob(
            id=job_id,
            automation_id=automation_id,
            host_root=host_root,
            sandbox_root=sandbox_job_root,
            evidence_path=sandbox_job_root / "input/evidence.json",
            schema_path=sandbox_job_root / "input/bundle.schema.json",
            output_path=sandbox_job_root / "output/bundle.json",
        )
        if self.transport is not None:
            self.transport.publish(job)
        return job

    def write_result(self, job: GenerationJob, content: str) -> Path:
        """Atomically write an untrusted agent response into its job output."""
        encoded = content.encode("utf-8")
        if len(encoded) > self.config.max_result_bytes:
            raise ValueError("Generation result exceeds configured size limit")
        output = job.host_root / "output" / "bundle.json"
        temporary = output.with_suffix(".json.pending")
        temporary.write_bytes(encoded)
        os.replace(temporary, output)
        return output

    def read_result(self, job: GenerationJob) -> str:
        """Read a bounded result after confirming it remains inside the job."""
        output = (job.host_root / "output" / "bundle.json").resolve()
        job_root = job.host_root.resolve()
        if not output.is_relative_to(job_root):
            raise RuntimeError("Generation output escapes its job directory")
        if not output.is_file():
            raise FileNotFoundError("Hermes generation output is missing")
        if output.stat().st_size > self.config.max_result_bytes:
            raise ValueError("Generation result exceeds configured size limit")
        return output.read_text(encoding="utf-8")

    def _assert_ready(self) -> None:
        marker = self.config.host_mount / "automation-foundry" / ".workspace-version"
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != "1":
            raise RuntimeError("NemoClaw workspace marker is missing or stale")
        if self.config.require_mount and not os.path.ismount(self.config.host_mount):
            raise RuntimeError("NemoClaw workspace mount was lost")
        if self.transport is not None:
            self.transport.ensure_ready()

    def _copy_frames(self, automation_id: UUID, evidence: EvidencePackage, input_root: Path) -> None:
        automation_root = self.store.automation_root(automation_id).resolve()
        frame_root = input_root / "frames"
        for video in evidence.videos:
            destination_root = frame_root / str(video.source_id)
            destination_root.mkdir(parents=True, exist_ok=True)
            for relative_path in video.frame_paths:
                source = (automation_root / relative_path).resolve()
                if not source.is_relative_to(automation_root) or not source.is_file():
                    raise ValueError(f"Unsafe or missing evidence frame: {relative_path}")
                shutil.copy2(source, destination_root / source.name)


def _validate_sandbox_root(sandbox_root: PurePosixPath) -> PurePosixPath:
    if not sandbox_root.is_absolute() or sandbox_root == PurePosixPath("/sandbox"):
        raise ValueError("NemoClaw workspace root must be an absolute path below /sandbox")
    if not sandbox_root.is_relative_to(PurePosixPath("/sandbox")) or ".." in sandbox_root.parts:
        raise ValueError("NemoClaw workspace root must remain below /sandbox")
    return sandbox_root
