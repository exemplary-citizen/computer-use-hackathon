"""NemoClaw shared-workspace staging with a narrow host/sandbox boundary."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from automation_foundry.authoring.evidence import EvidencePackage
from automation_foundry.storage import ArtifactStore


class WorkspaceConfig(BaseModel):
    """Host mount and sandbox path for the NemoClaw shared workspace."""

    host_mount: Path
    """Host-side SSHFS mount corresponding to the sandbox workspace."""
    sandbox_root: PurePosixPath = PurePosixPath("/sandbox/workspace")
    """Path visible to Hermes inside the sandbox."""
    require_mount: bool = True
    """Require host_mount to be a real mount point outside tests."""
    max_result_bytes: int = Field(default=5_000_000, gt=0)
    """Maximum accepted generation result size."""

    def make(self, store: ArtifactStore) -> WorkspaceBridge:
        """Build a workspace bridge for an artifact store."""
        return WorkspaceBridge(self, store)


class GenerationJob(BaseModel):
    """Stable host and sandbox locations for one generation job."""

    id: UUID
    automation_id: UUID
    host_root: Path
    sandbox_root: PurePosixPath
    evidence_path: PurePosixPath
    schema_path: PurePosixPath
    output_path: PurePosixPath


class WorkspaceBridge:
    """Stage selected evidence and retrieve bounded untrusted results."""

    def __init__(self, config: WorkspaceConfig, store: ArtifactStore):
        """Initialize the shared-workspace boundary.

        Args:
            config: Mount paths and result limits.
            store: Canonical host artifact store.
        """
        self.config = config
        self.store = store

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
        return GenerationJob(
            id=job_id,
            automation_id=automation_id,
            host_root=host_root,
            sandbox_root=sandbox_job_root,
            evidence_path=sandbox_job_root / "input/evidence.json",
            schema_path=sandbox_job_root / "input/bundle.schema.json",
            output_path=sandbox_job_root / "output/bundle.json",
        )

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
