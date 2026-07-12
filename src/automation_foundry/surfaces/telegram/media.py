"""Bounded local quarantine for authenticated Telegram videos."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path, PurePath
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

_CHUNK_SIZE = 1024 * 1024
_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


class TelegramMediaInboxConfig(BaseModel):
    """Quarantine paths and byte limit for owner-submitted videos."""

    root: Path = Path("data/telegram-inbox")
    """Local sensitive-media quarantine outside source directories."""
    database_path: Path = Path("data/automation_foundry.sqlite3")
    """Foundry database used for Telegram update idempotency."""
    max_video_bytes: int = Field(default=1_000_000_000, gt=0)
    """Maximum accepted video size."""

    def make(self) -> TelegramMediaInbox:
        """Build and initialize the media inbox."""
        return TelegramMediaInbox(self)


class QuarantinedTelegramVideo(BaseModel):
    """Internal metadata for one authenticated local video copy."""

    id: UUID
    update_id: int = Field(ge=0)
    telegram_user_id: int = Field(gt=0)
    original_name: str = Field(min_length=1, max_length=255)
    media_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_path: str
    created_at: datetime


class TelegramMediaInbox:
    """Store exactly one validated video for each authenticated update."""

    def __init__(self, config: TelegramMediaInboxConfig):
        """Initialize quarantine storage.

        Args:
            config: Paths and hard byte limit.
        """
        self.config = config
        self.config.root.mkdir(parents=True, exist_ok=True)
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def store_video(
        self,
        *,
        update_id: int,
        telegram_user_id: int,
        telegram_chat_id: int,
        filename: str,
        media_type: str,
        stream: BinaryIO,
    ) -> QuarantinedTelegramVideo:
        """Validate and atomically quarantine one owner-DM video.

        Args:
            update_id: Authenticated Telegram update identifier.
            telegram_user_id: Authenticated numeric owner identity.
            telegram_chat_id: Authenticated direct-message chat identity.
            filename: Untrusted Telegram filename.
            media_type: Untrusted Telegram media type.
            stream: Downloaded video byte stream.

        Returns:
            Internal opaque video metadata.

        Raises:
            ValueError: If identity, metadata, content size, or replay conflicts.
        """
        if update_id < 0 or telegram_user_id <= 0 or telegram_chat_id != telegram_user_id:
            raise ValueError("Telegram video requires an authenticated owner direct message")
        safe_name, normalized_type, extension = _validate_video_metadata(filename, media_type)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT metadata_json FROM telegram_media WHERE update_id = ?",
                (update_id,),
            ).fetchone()
            if existing is not None:
                video = QuarantinedTelegramVideo.model_validate_json(existing[0])
                if video.telegram_user_id != telegram_user_id or video.original_name != safe_name:
                    connection.rollback()
                    raise ValueError("Telegram update was already used with different media")
                connection.commit()
                return video
            video_id = uuid4()
            relative_path = Path(f"{video_id}{extension}")
            destination = self.config.root / relative_path
            temporary_path: Path | None = None
            try:
                digest = hashlib.sha256()
                size_bytes = 0
                with NamedTemporaryFile(dir=self.config.root, prefix=".pending-", delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                    while chunk := stream.read(_CHUNK_SIZE):
                        size_bytes += len(chunk)
                        if size_bytes > self.config.max_video_bytes:
                            raise ValueError("Telegram video exceeds the configured byte limit")
                        digest.update(chunk)
                        temporary.write(chunk)
                    if size_bytes == 0:
                        raise ValueError("Telegram video is empty")
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_path, destination)
                video = QuarantinedTelegramVideo(
                    id=video_id,
                    update_id=update_id,
                    telegram_user_id=telegram_user_id,
                    original_name=safe_name,
                    media_type=normalized_type,
                    size_bytes=size_bytes,
                    sha256=digest.hexdigest(),
                    relative_path=relative_path.as_posix(),
                    created_at=datetime.now(UTC),
                )
                connection.execute(
                    "INSERT INTO telegram_media (update_id, media_id, metadata_json) VALUES (?, ?, ?)",
                    (update_id, str(video.id), video.model_dump_json()),
                )
                connection.commit()
                return video
            except Exception:
                connection.rollback()
                destination.unlink(missing_ok=True)
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
                raise

    def path_for(self, video: QuarantinedTelegramVideo) -> Path:
        """Resolve one internal video path under the quarantine root.

        Args:
            video: Validated metadata returned by this inbox.

        Returns:
            Existing regular file confined to the quarantine root.
        """
        root = self.config.root.resolve()
        candidate = root / video.relative_path
        path = candidate.resolve()
        if candidate.is_symlink() or not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError("Telegram video quarantine entry is missing or unsafe")
        if _file_hash(path) != video.sha256:
            raise RuntimeError("Telegram video quarantine hash changed")
        return path

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_media (
                    update_id INTEGER PRIMARY KEY,
                    media_id TEXT NOT NULL UNIQUE,
                    metadata_json TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.config.database_path, timeout=5, isolation_level=None)


def _validate_video_metadata(filename: str, media_type: str) -> tuple[str, str, str]:
    if not filename or len(filename) > 255 or "\x00" in filename:
        raise ValueError("Telegram filename must contain 1 to 255 safe characters")
    if PurePath(filename).name != filename or filename in {".", ".."}:
        raise ValueError("Telegram filename must not contain a path")
    extension = Path(filename).suffix.casefold()
    expected_type = _VIDEO_TYPES.get(extension)
    if expected_type is None:
        raise ValueError("Telegram video extension is unsupported")
    normalized_type = media_type.casefold().split(";", maxsplit=1)[0].strip()
    if normalized_type != expected_type:
        raise ValueError("Telegram video media type does not match its extension")
    return filename, normalized_type, extension


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
