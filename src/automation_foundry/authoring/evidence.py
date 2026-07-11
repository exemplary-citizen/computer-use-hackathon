"""Evidence package contracts produced before agentic bundle generation."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EvidenceModel(BaseModel):
    """Strict base model for durable evidence JSON."""

    model_config = ConfigDict(extra="forbid")


class TranscriptSegment(EvidenceModel):
    """Finalized Gradium transcript segment."""

    text: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    stop_seconds: float = Field(ge=0)


class SopPage(EvidenceModel):
    """Normalized text and stable page reference from an SOP."""

    page: int = Field(ge=1)
    text: str


class SopEvidence(EvidenceModel):
    """Normalized SOP evidence for one uploaded document."""

    source_id: UUID
    page_count: int = Field(ge=1)
    pages: list[SopPage] = Field(min_length=1)


class VideoEvidence(EvidenceModel):
    """Bounded visual and audio evidence for one demonstration video."""

    source_id: UUID
    duration_seconds: float = Field(gt=0)
    frame_interval_seconds: int = Field(default=1, ge=1, le=10)
    frame_paths: list[str] = Field(min_length=1)
    audio_path: str | None = None
    transcript: list[TranscriptSegment] = Field(default_factory=list)


class EvidencePackage(EvidenceModel):
    """All normalized evidence staged for one automation generation job."""

    schema_version: str = "1.0"
    automation_id: UUID
    sop_documents: list[SopEvidence] = Field(default_factory=list)
    videos: list[VideoEvidence] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
