"""Application service for the complete evidence-to-approved-bundle lane."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field

from automation_foundry.authoring.bundles import BundleManager, BundleManagerConfig
from automation_foundry.authoring.generation import BundleGenerator, HermesClientConfig
from automation_foundry.authoring.preprocessing import EvidencePreprocessor, PreprocessingConfig
from automation_foundry.authoring.transcription import GradiumTranscriberConfig
from automation_foundry.authoring.tool_testing import (
    HermesToolTestExecutor,
    SandboxToolTestRunner,
    ToolTestResult,
)
from automation_foundry.authoring.uploads import UploadPolicyConfig, UploadService
from automation_foundry.authoring.validation import BundleValidationReport, REQUIRED_ARTIFACTS
from automation_foundry.authoring.workspace import WorkspaceConfig
from automation_foundry.contracts import AutomationManifest, AutomationStatus, AutomationVersion
from automation_foundry.storage import ArtifactStore, ArtifactStoreConfig

if TYPE_CHECKING:
    from automation_foundry.settings import AppSettings


class ProcessingProgress(BaseModel):
    """Durable stage-level ingestion progress displayed after refresh."""

    stage: str
    percent: int = Field(ge=0, le=100)
    message: str
    updated_at: datetime


@dataclass
class IngestionPipeline:
    """Provider-backed preprocessing and generation pipeline."""

    preprocessor: EvidencePreprocessor
    generator: BundleGenerator
    bundles: BundleManager

    async def run(
        self,
        automation_id: UUID,
        progress: Callable[[str, int, str], None],
    ) -> AutomationVersion:
        """Preprocess stored sources and materialize one generated draft version."""
        progress("preprocessing", 10, "Extracting normalized video and SOP evidence")
        evidence = await self.preprocessor.preprocess(automation_id)
        progress("generation", 45, "Generating the semantic bundle with Hermes and Holo3")
        draft = await self.generator.generate(automation_id, evidence)
        progress("validation", 85, "Validating generated artifacts and safety policies")
        version, _ = self.bundles.create_version(automation_id, draft)
        progress("complete", 100, "Bundle is ready for review")
        return version


class AuthoringService:
    """Narrow orchestration boundary used by FastAPI and deterministic tests."""

    def __init__(
        self,
        store: ArtifactStore,
        uploads: UploadService,
        bundles: BundleManager,
        pipeline: IngestionPipeline | None = None,
        tool_test_runner: SandboxToolTestRunner | None = None,
    ):
        self.store = store
        self.uploads = uploads
        self.bundles = bundles
        self.pipeline = pipeline
        self.tool_test_runner = tool_test_runner

    @property
    def generation_configured(self) -> bool:
        """Return whether provider and workspace adapters are available."""
        return self.pipeline is not None

    async def process(self, automation_id: UUID) -> None:
        """Run ingestion and persist a safe terminal failure on any exception."""
        manifest = self.store.get_manifest(automation_id)
        manifest.status = AutomationStatus.PROCESSING
        self.store.save_manifest(manifest)
        self._write_progress(automation_id, "queued", 0, "Ingestion job accepted")
        try:
            if self.pipeline is None:
                raise RuntimeError(
                    "Generation is not configured; set FOUNDRY_WORKSPACE_MOUNT and FOUNDRY_HERMES_API_KEY"
                )
            await self.pipeline.run(
                automation_id,
                lambda stage, percent, message: self._write_progress(
                    automation_id, stage, percent, message
                ),
            )
        except Exception as exc:
            manifest = self.store.get_manifest(automation_id)
            manifest.status = AutomationStatus.FAILED
            self.store.save_manifest(manifest)
            error_path = self.store.automation_root(automation_id) / "processing_error.json"
            error_path.write_text(
                f"{json.dumps({'error': type(exc).__name__, 'message': _safe_error_message(exc)}, indent=2)}\n",
                encoding="utf-8",
            )
            self._write_progress(automation_id, "failed", 100, _safe_error_message(exc))

    async def run_tool_tests(self, automation_id: UUID, version_number: int) -> ToolTestResult:
        """Run generated tests through the configured NemoClaw-only adapter."""
        if self.tool_test_runner is None:
            raise RuntimeError("NemoClaw tool testing is not configured")
        return await self.tool_test_runner.run(self.version_root(automation_id, version_number))

    def reconcile_startup(self) -> None:
        """Fail interrupted work and reconcile approved hashes after process restart."""
        for manifest in self.store.list_automations():
            if manifest.status is AutomationStatus.PROCESSING:
                manifest.status = AutomationStatus.FAILED
                self.store.save_manifest(manifest)
                error_path = self.store.automation_root(manifest.id) / "processing_error.json"
                error_path.write_text(
                    json.dumps(
                        {
                            "error": "ProcessRestart",
                            "message": "Processing was interrupted by an application restart; retry generation.",
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self._write_progress(
                    manifest.id,
                    "failed",
                    100,
                    "Processing was interrupted by an application restart; retry generation.",
                )
            elif manifest.status is AutomationStatus.APPROVED:
                self.bundles.reconcile(manifest.id)

    def processing_error(self, automation_id: UUID) -> str | None:
        """Return a persisted user-visible processing failure, if present."""
        path = self.store.automation_root(automation_id) / "processing_error.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return str(payload.get("message") or payload.get("error") or "Processing failed")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "Processing failed; retry after checking local provider configuration"

    def processing_progress(self, automation_id: UUID) -> ProcessingProgress | None:
        """Return the latest durable ingestion stage."""
        path = self.store.automation_root(automation_id) / "processing.json"
        if not path.is_file():
            return None
        try:
            return ProcessingProgress.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _write_progress(
        self, automation_id: UUID, stage: str, percent: int, message: str
    ) -> None:
        progress = ProcessingProgress(
            stage=stage,
            percent=percent,
            message=message,
            updated_at=datetime.now(UTC),
        )
        path = self.store.automation_root(automation_id) / "processing.json"
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".processing-",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(progress.model_dump_json(indent=2))
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def load_version(self, automation_id: UUID, version_number: int) -> AutomationVersion:
        """Load persisted version metadata."""
        path = self.version_root(automation_id, version_number) / "version.json"
        if not path.is_file():
            raise KeyError(f"Unknown automation version: {version_number}")
        return AutomationVersion.model_validate_json(path.read_text(encoding="utf-8"))

    def version_root(self, automation_id: UUID, version_number: int) -> Path:
        """Resolve one version directory below the canonical automation root."""
        self.store.get_manifest(automation_id)
        path = self.store.automation_root(automation_id) / "versions" / str(version_number)
        if not path.is_dir():
            raise KeyError(f"Unknown automation version: {version_number}")
        return path

    def read_artifact(self, automation_id: UUID, version_number: int, artifact_name: str) -> str:
        """Read only a named author-editable artifact."""
        if artifact_name not in REQUIRED_ARTIFACTS:
            raise ValueError(f"Artifact is not readable: {artifact_name}")
        return (self.version_root(automation_id, version_number) / artifact_name).read_text(encoding="utf-8")

    def validation(self, automation_id: UUID, version_number: int) -> BundleValidationReport:
        """Return current validation and hash status."""
        return self.bundles.validate_version(automation_id, version_number)

    def deactivate(self, automation_id: UUID) -> AutomationManifest:
        """Make an automation non-runnable while retaining its audit artifacts."""
        manifest = self.store.get_manifest(automation_id)
        manifest.status = AutomationStatus.INACTIVE
        self.bundles.unpublish(manifest.slug)
        self.store.save_manifest(manifest)
        return manifest

    def delete(self, automation_id: UUID) -> None:
        """Delete local artifacts, published skill, and index entry."""
        manifest = self.store.get_manifest(automation_id)
        published = self.bundles.published_skill_root / manifest.slug
        self.store.delete_automation(automation_id)
        if published.is_dir() and published.resolve().is_relative_to(
            self.bundles.published_skill_root.resolve()
        ):
            import shutil

            shutil.rmtree(published)


def build_authoring_service(settings: AppSettings) -> AuthoringService:
    """Compose local services from explicit environment-backed settings."""
    store = ArtifactStoreConfig(
        root=settings.data_root,
        database_path=settings.database_path,
    ).make()
    bundles = BundleManagerConfig(published_skill_root=settings.published_skill_root).make(store)
    pipeline = None
    tool_test_runner = None
    if settings.workspace_mount is not None and settings.hermes_api_key is not None:
        workspace = WorkspaceConfig(
            host_mount=settings.workspace_mount,
            require_mount=settings.workspace_require_mount,
            nemoclaw_sandbox_name=settings.nemoclaw_sandbox_name,
            nemohermes_binary=settings.nemohermes_binary,
            transfer_timeout_seconds=settings.workspace_transfer_timeout_seconds,
        ).make(store)
        workspace.initialize()
        transcriber = None
        if settings.gradium_api_key is not None:
            transcriber = GradiumTranscriberConfig(settings.gradium_api_key.get_secret_value()).make()
        preprocessor = PreprocessingConfig(allow_untranscribed_audio=settings.allow_untranscribed_audio).make(
            store, transcriber=transcriber
        )
        client = HermesClientConfig(
            settings.hermes_api_key.get_secret_value(),
            base_url=settings.hermes_base_url,
            model=settings.hermes_model,
        ).make()
        pipeline = IngestionPipeline(preprocessor, BundleGenerator(workspace, client), bundles)
        tool_test_runner = SandboxToolTestRunner(workspace, HermesToolTestExecutor(client))
    service = AuthoringService(
        store,
        UploadPolicyConfig().make(store),
        bundles,
        pipeline,
        tool_test_runner,
    )
    service.reconcile_startup()
    return service


def _safe_error_message(exc: Exception) -> str:
    """Return actionable local errors without reflecting provider response bodies or credentials."""
    message = str(exc)
    safe_prefixes = (
        "Automation has no processable sources",
        "FFmpeg",
        "FFprobe",
        "Generation is not configured",
        "Hermes returned",
        "NemoClaw workspace",
        "SOP exceeds",
        "Video duration",
    )
    if isinstance(exc, ValueError) or message.startswith(safe_prefixes):
        return message
    return "Provider or sandbox processing failed; check local configuration and retry"
