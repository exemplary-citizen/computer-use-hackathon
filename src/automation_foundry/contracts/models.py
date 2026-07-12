"""Strict version-one contracts for automation authoring and execution."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and serializes enum values."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class AutomationStatus(StrEnum):
    """Authoring lifecycle for an automation."""

    DRAFT = "draft"
    PROCESSING = "processing"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    INACTIVE = "inactive"
    FAILED = "failed"


class RunState(StrEnum):
    """Execution lifecycle for a run."""

    PREPARED = "prepared"
    AWAITING_START_CONFIRMATION = "awaiting_start_confirmation"
    EXECUTING = "executing"
    AWAITING_COMMIT_APPROVAL = "awaiting_commit_approval"
    COMMITTING = "committing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvocationSource(StrEnum):
    """Surface that initiated a run."""

    DASHBOARD = "dashboard"
    VOICE = "voice"


class EvidenceSourceType(StrEnum):
    """Supported source categories for evidence references."""

    VIDEO = "video"
    SOP = "sop"
    INFERENCE = "inference"


class ConflictSeverity(StrEnum):
    """Review severity for a source conflict."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ApprovalDecision(StrEnum):
    """Recorded human decision."""

    APPROVED = "approved"
    REJECTED = "rejected"


class SourceMetadata(StrictModel):
    """Immutable metadata for an uploaded source."""

    id: UUID
    source_type: EvidenceSourceType
    original_name: str = Field(min_length=1, max_length=255)
    relative_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=0)
    sha256: Sha256
    duration_seconds: float | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class EvidenceReference(StrictModel):
    """Trace a generated claim to source evidence or a marked inference."""

    source_id: UUID | None = None
    source_type: EvidenceSourceType
    timestamp_seconds: float | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, max_length=255)
    excerpt: str | None = Field(default=None, max_length=2_000)
    frame_path: str | None = None
    inference_reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_locator(self) -> EvidenceReference:
        """Require a source locator or an explicit inference explanation."""
        if self.source_type is EvidenceSourceType.INFERENCE:
            if self.source_id is not None or not self.inference_reason:
                raise ValueError("Inference evidence requires inference_reason and no source_id")
            return self
        if self.source_id is None:
            raise ValueError("Source evidence requires source_id")
        if self.source_type is EvidenceSourceType.VIDEO and self.timestamp_seconds is None:
            raise ValueError("Video evidence requires timestamp_seconds")
        if self.source_type is EvidenceSourceType.SOP and self.page is None and self.section is None:
            raise ValueError("SOP evidence requires a page or section")
        return self


class InputDefinition(StrictModel):
    """Runtime input required by an automation."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    json_type: str = Field(pattern=r"^(string|number|integer|boolean|object|array)$")
    description: str = Field(min_length=1, max_length=1_000)
    required: bool = True
    default: JSONValue = None
    examples: list[JSONValue] = Field(default_factory=list, max_length=10)


class ProcedureStep(StrictModel):
    """Application-independent action with auditable source evidence."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    instruction: str = Field(min_length=1, max_length=4_000)
    critical: bool = True
    persistent_action: bool = False
    requires_confirmation_before: bool = False
    evidence: list[EvidenceReference] = Field(min_length=1)

    @model_validator(mode="after")
    def require_confirmation_for_persistent_action(self) -> ProcedureStep:
        """Prevent generation of an unguarded final side effect."""
        if self.persistent_action and not self.requires_confirmation_before:
            raise ValueError("Persistent actions require confirmation before execution")
        return self


class ProcedureCheck(StrictModel):
    """Evidence-linked precondition or completion check."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=4_000)
    evidence: list[EvidenceReference] = Field(min_length=1)


class ArtifactReference(StrictModel):
    """Path and content hash for a versioned artifact."""

    name: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1)
    sha256: Sha256
    media_type: str = Field(min_length=1, max_length=127)


class GeneratedTool(StrictModel):
    """Approved pure-data generated tool contract."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=1_000)
    entrypoint: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    code_sha256: Sha256
    timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    memory_limit_mb: int = Field(default=128, ge=32, le=512)


class ReviewConflict(StrictModel):
    """Ambiguity or conflict requiring review."""

    id: UUID
    description: str = Field(min_length=1, max_length=4_000)
    severity: ConflictSeverity
    evidence: list[EvidenceReference] = Field(min_length=1)
    resolution: str | None = Field(default=None, max_length=4_000)

    @property
    def blocks_approval(self) -> bool:
        """Return whether this unresolved conflict blocks approval."""
        return self.severity is ConflictSeverity.BLOCKING and not self.resolution


class ApprovalRecord(StrictModel):
    """Human approval bound to an immutable payload hash."""

    id: UUID
    decision: ApprovalDecision
    source: InvocationSource
    payload_sha256: Sha256
    actor: str = Field(min_length=1, max_length=255)
    created_at: datetime = Field(default_factory=utc_now)


class AutomationVersion(StrictModel):
    """Versioned runnable contents and validation state."""

    version: int = Field(ge=1)
    artifacts: list[ArtifactReference] = Field(min_length=1)
    inputs: list[InputDefinition] = Field(default_factory=list)
    steps: list[ProcedureStep] = Field(default_factory=list)
    preconditions: list[ProcedureCheck] = Field(default_factory=list)
    completion_checks: list[ProcedureCheck] = Field(default_factory=list)
    tools: list[GeneratedTool] = Field(default_factory=list)
    conflicts: list[ReviewConflict] = Field(default_factory=list)
    validation_passed: bool = False
    approval: ApprovalRecord | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def is_approved(self) -> bool:
        """Return whether the version is valid, conflict-free, and approved."""
        has_blocker = any(conflict.blocks_approval for conflict in self.conflicts)
        return (
            self.validation_passed
            and not has_blocker
            and self.approval is not None
            and self.approval.decision is ApprovalDecision.APPROVED
        )


class AutomationManifest(StrictModel):
    """Top-level automation metadata and version pointers."""

    schema_version: str = "1.0"
    id: UUID
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=120)
    status: AutomationStatus = AutomationStatus.DRAFT
    sources: list[SourceMetadata] = Field(default_factory=list)
    current_version: int | None = Field(default=None, ge=1)
    approved_version: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_version_pointers(self) -> AutomationManifest:
        """Ensure approval state and version pointers are internally consistent."""
        approved_version = self.approved_version
        current_version = self.current_version
        if approved_version is not None and current_version is None:
            raise ValueError("approved_version requires current_version")
        if approved_version is not None and current_version is not None and approved_version > current_version:
            raise ValueError("approved_version cannot exceed current_version")
        if self.status is AutomationStatus.APPROVED and approved_version is None:
            raise ValueError("approved automation requires approved_version")
        return self


class ApprovedBundle(StrictModel):
    """Complete approved handoff from authoring to execution."""

    schema_version: str = "1.0"
    manifest: AutomationManifest
    version: AutomationVersion
    sop_markdown: str = Field(min_length=1)
    skill_markdown: str = Field(min_length=1)
    input_schema: dict[str, Any]
    tools_code: str = ""
    eval_cases: list[dict[str, JSONValue]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_approved_handoff(self) -> ApprovedBundle:
        """Guarantee execution receives the manifest's approved version only."""
        if self.manifest.status is not AutomationStatus.APPROVED:
            raise ValueError("bundle manifest must be approved")
        if self.manifest.approved_version != self.version.version:
            raise ValueError("bundle version must match manifest.approved_version")
        if not self.version.is_approved:
            raise ValueError("bundle version must pass validation and approval")
        return self


class RunRequest(StrictModel):
    """Validated request to execute an approved automation."""

    id: UUID
    automation_id: UUID
    version: int = Field(ge=1)
    target_app: str = Field(min_length=1, max_length=255)
    inputs: dict[str, JSONValue]
    invocation_source: InvocationSource
    max_steps: int = Field(default=40, ge=1, le=200)
    max_time_seconds: int = Field(default=180, ge=10, le=1_800)
    created_at: datetime = Field(default_factory=utc_now)


class RunPreview(StrictModel):
    """Normalized run intent displayed before start confirmation."""

    request: RunRequest
    automation_name: str = Field(min_length=1)
    normalized_inputs: dict[str, JSONValue]
    missing_fields: list[str] = Field(default_factory=list)
    requires_confirmation: bool = True


class RunEvent(StrictModel):
    """Ordered, user-safe event in a run trajectory."""

    run_id: UUID
    sequence: int = Field(ge=0)
    state: RunState
    event_type: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=4_000)
    payload: dict[str, JSONValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class FieldChange(StrictModel):
    """One proposed persistent field change."""

    field: str = Field(min_length=1, max_length=255)
    before: JSONValue = None
    after: JSONValue


class StagedChange(StrictModel):
    """Visible change staged by Holo before commit approval."""

    run_id: UUID
    target_app: str = Field(min_length=1, max_length=255)
    record_identity: str = Field(min_length=1, max_length=1_000)
    changes: list[FieldChange] = Field(min_length=1)
    visible_verification: str = Field(min_length=1, max_length=4_000)
    session_reference: str = Field(min_length=1, max_length=255)
    payload_sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)


class RunResult(StrictModel):
    """Terminal result and verification summary for a run."""

    run_id: UUID
    state: RunState
    answer: str | None = Field(default=None, max_length=8_000)
    verification_summary: str | None = Field(default=None, max_length=8_000)
    error_code: str | None = Field(default=None, max_length=120)
    error_message: str | None = Field(default=None, max_length=4_000)
    started_at: datetime
    completed_at: datetime = Field(default_factory=utc_now)
    holo_steps: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> RunResult:
        """Require a terminal state and coherent error details."""
        if self.state not in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}:
            raise ValueError("run result state must be terminal")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.state is RunState.FAILED and not self.error_code:
            raise ValueError("failed result requires error_code")
        return self
