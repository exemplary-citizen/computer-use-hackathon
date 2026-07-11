"""FastAPI routes for upload, generation, review, versioning, and approval."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from automation_foundry.authoring.service import (
    AuthoringService,
    ProcessingProgress,
    build_authoring_service,
)
from automation_foundry.authoring.validation import BundleValidationReport
from automation_foundry.contracts import AutomationManifest, AutomationVersion
from automation_foundry.settings import AppSettings

router = APIRouter(prefix="/api/authoring", tags=["authoring"])


class ArtifactEditRequest(BaseModel):
    """User-authored replacement for one version artifact."""

    content: str = Field(max_length=2_000_000)


class ApprovalRequest(BaseModel):
    """Explicit human approval identity."""

    actor: str = Field(min_length=1, max_length=255)


class ValidationResponse(BaseModel):
    """JSON-safe validation response."""

    valid: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


class AutomationDetail(BaseModel):
    """Manifest, current version, artifacts, and validation for review."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    manifest: AutomationManifest
    version: AutomationVersion | None
    artifacts: dict[str, str]
    validation: ValidationResponse | None
    failure: str | None = None
    progress: ProcessingProgress | None = None


@lru_cache(maxsize=1)
def get_authoring_service() -> AuthoringService:
    """Return the process-local authoring service."""
    return build_authoring_service(AppSettings())


Service = Annotated[AuthoringService, Depends(get_authoring_service)]


@router.get("/health")
def authoring_health(service: Service) -> dict[str, str | bool]:
    """Return authoring health without exposing provider credentials."""
    return {"status": "ok", "subsystem": "authoring"}


@router.get("/automations", response_model=list[AutomationManifest])
def list_automations(service: Service) -> list[AutomationManifest]:
    """List persistent automations for the dashboard."""
    return service.store.list_automations()


@router.post("/automations", response_model=AutomationManifest, status_code=status.HTTP_201_CREATED)
async def create_automation(
    background_tasks: BackgroundTasks,
    service: Service,
    name: Annotated[str, Form(min_length=1, max_length=120)],
    provider_disclosure_accepted: Annotated[bool, Form()],
    sources: Annotated[list[UploadFile], File(min_length=1, max_length=2)],
) -> AutomationManifest:
    """Validate/store sources atomically, then start persistent background processing."""
    if not provider_disclosure_accepted:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provider disclosure must be accepted")
    manifest = service.store.create_automation(name)
    try:
        source_types: set[str] = set()
        for upload in sources:
            if not upload.filename or not upload.content_type:
                raise ValueError("Every source requires a filename and media type")
            stored = service.uploads.store_upload(
                manifest.id,
                filename=upload.filename,
                media_type=upload.content_type,
                stream=upload.file,
            )
            if stored.source_type.value in source_types:
                raise ValueError("Upload at most one video and one SOP")
            source_types.add(stored.source_type.value)
    except (ValueError, OSError) as exc:
        service.delete(manifest.id)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    background_tasks.add_task(service.process, manifest.id)
    return service.store.get_manifest(manifest.id)


@router.get("/automations/{automation_id}", response_model=AutomationDetail)
def get_automation(automation_id: UUID, service: Service) -> AutomationDetail:
    """Return all current authoring artifacts and validation findings."""
    try:
        manifest = service.store.get_manifest(automation_id)
        if manifest.current_version is None:
            return AutomationDetail(
                manifest=manifest,
                version=None,
                artifacts={},
                validation=None,
                failure=service.processing_error(automation_id),
                progress=service.processing_progress(automation_id),
            )
        version = service.load_version(automation_id, manifest.current_version)
        artifacts = {
            artifact.name: service.read_artifact(automation_id, version.version, artifact.name)
            for artifact in version.artifacts
        }
        return AutomationDetail(
            manifest=manifest,
            version=version,
            artifacts=artifacts,
            validation=_validation_response(service.validation(automation_id, version.version)),
            failure=service.processing_error(automation_id),
            progress=service.processing_progress(automation_id),
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/automations/{automation_id}/versions/{version_number}/artifacts/{artifact_name}",
    response_class=PlainTextResponse,
)
def get_artifact(
    automation_id: UUID, version_number: int, artifact_name: str, service: Service
) -> str:
    """Read one allowlisted artifact as plain text."""
    try:
        return service.read_artifact(automation_id, version_number, artifact_name)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.put(
    "/automations/{automation_id}/versions/{version_number}/artifacts/{artifact_name}",
    response_model=AutomationVersion,
)
def edit_artifact(
    automation_id: UUID,
    version_number: int,
    artifact_name: str,
    request: ArtifactEditRequest,
    service: Service,
) -> AutomationVersion:
    """Edit a draft, forking an approved version automatically."""
    try:
        version, _ = service.bundles.edit_artifact(
            automation_id, version_number, artifact_name, request.content
        )
        return version
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post(
    "/automations/{automation_id}/versions/{version_number}/validate",
    response_model=ValidationResponse,
)
def validate_version(
    automation_id: UUID, version_number: int, service: Service
) -> ValidationResponse:
    """Re-run fail-closed bundle validation."""
    try:
        return _validation_response(service.validation(automation_id, version_number))
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/automations/{automation_id}/versions/{version_number}/tool-tests",
)
async def run_tool_tests(
    automation_id: UUID, version_number: int, service: Service
) -> dict[str, Any]:
    """Run generated tests in NemoClaw and return the hash-bound result."""
    try:
        result = await service.run_tool_tests(automation_id, version_number)
        return result.model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.post(
    "/automations/{automation_id}/versions/{version_number}/approve",
    response_model=AutomationManifest,
)
def approve_version(
    automation_id: UUID,
    version_number: int,
    request: ApprovalRequest,
    service: Service,
) -> AutomationManifest:
    """Approve exact current bytes and publish only the selected version."""
    try:
        service.bundles.approve(automation_id, version_number, actor=request.actor)
        return service.store.get_manifest(automation_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/automations/{automation_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_automation(
    automation_id: UUID, background_tasks: BackgroundTasks, service: Service
) -> dict[str, str]:
    """Generate a new version from the preserved source evidence."""
    try:
        service.store.get_manifest(automation_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if not service.generation_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Generation is not configured")
    background_tasks.add_task(service.process, automation_id)
    return {"status": "processing"}


@router.post("/automations/{automation_id}/deactivate", response_model=AutomationManifest)
def deactivate_automation(automation_id: UUID, service: Service) -> AutomationManifest:
    """Deactivate an automation without deleting its audit trail."""
    try:
        return service.deactivate(automation_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/automations/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_automation(automation_id: UUID, service: Service) -> None:
    """Delete all local artifacts for one automation."""
    try:
        service.delete(automation_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def _validation_response(report: BundleValidationReport) -> ValidationResponse:
    return ValidationResponse(
        valid=report.valid,
        errors=[issue.__dict__ for issue in report.errors],
        warnings=[issue.__dict__ for issue in report.warnings],
    )
