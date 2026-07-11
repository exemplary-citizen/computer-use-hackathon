"""Upload policy and storage orchestration for authoring sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import BinaryIO
from uuid import UUID

from pydantic import BaseModel, Field

from automation_foundry.contracts import EvidenceSourceType, SourceMetadata
from automation_foundry.storage import ArtifactStore


@dataclass(frozen=True)
class UploadKind:
    """Allowed extension, MIME types, source type, and byte limit."""

    source_type: EvidenceSourceType
    media_types: frozenset[str]
    max_bytes: int


class UploadPolicyConfig(BaseModel):
    """MVP upload limits used before provider calls."""

    max_video_bytes: int = Field(default=1_000_000_000, gt=0)
    """Maximum accepted video size: one decimal gigabyte."""
    max_sop_bytes: int = Field(default=25_000_000, gt=0)
    """Maximum accepted SOP size: twenty-five decimal megabytes."""

    def make(self, store: ArtifactStore) -> UploadService:
        """Build an upload service for the given artifact store."""
        return UploadService(self, store)


class UploadService:
    """Validate upload metadata and stream accepted sources to disk."""

    def __init__(self, config: UploadPolicyConfig, store: ArtifactStore):
        """Initialize upload policy and artifact storage.

        Args:
            config: Size limits for supported sources.
            store: Initialized artifact store.
        """
        self.config = config
        self.store = store
        self._kinds = _supported_uploads(config)

    def store_upload(
        self,
        automation_id: UUID,
        *,
        filename: str,
        media_type: str,
        stream: BinaryIO,
    ) -> SourceMetadata:
        """Validate and persist one authoring source.

        Args:
            automation_id: Owning automation.
            filename: Untrusted client filename.
            media_type: Client-provided MIME type.
            stream: Binary source stream.

        Returns:
            Persisted immutable source metadata.

        Raises:
            ValueError: If metadata violates the upload policy.
        """
        safe_name = _validate_filename(filename)
        extension = Path(safe_name).suffix.casefold()
        kind = self._kinds.get(extension)
        if kind is None:
            raise ValueError(f"Unsupported source extension: {extension or '<none>'}")
        normalized_media_type = media_type.casefold().split(";", maxsplit=1)[0].strip()
        if normalized_media_type not in kind.media_types:
            raise ValueError(f"Unexpected media type {media_type!r} for {extension}")
        return self.store.store_source(
            automation_id,
            original_name=safe_name,
            media_type=normalized_media_type,
            source_type=kind.source_type,
            stream=stream,
            max_bytes=kind.max_bytes,
        )


def _validate_filename(filename: str) -> str:
    if not filename or len(filename) > 255 or "\x00" in filename:
        raise ValueError("Filename must contain 1 to 255 safe characters")
    if PurePath(filename).name != filename or filename in {".", ".."}:
        raise ValueError("Filename must not contain a path")
    return filename


def _supported_uploads(config: UploadPolicyConfig) -> dict[str, UploadKind]:
    video = EvidenceSourceType.VIDEO
    sop = EvidenceSourceType.SOP
    return {
        ".mp4": UploadKind(video, frozenset({"video/mp4"}), config.max_video_bytes),
        ".mov": UploadKind(video, frozenset({"video/quicktime"}), config.max_video_bytes),
        ".webm": UploadKind(video, frozenset({"video/webm"}), config.max_video_bytes),
        ".pdf": UploadKind(sop, frozenset({"application/pdf"}), config.max_sop_bytes),
        ".md": UploadKind(sop, frozenset({"text/markdown", "text/plain"}), config.max_sop_bytes),
        ".txt": UploadKind(sop, frozenset({"text/plain"}), config.max_sop_bytes),
    }
