"""Direct Telegram video handoff into the existing authoring service."""

from __future__ import annotations

import sqlite3
from typing import BinaryIO
from uuid import UUID

from automation_foundry.authoring.service import AuthoringService
from automation_foundry.contracts import AutomationManifest
from automation_foundry.surfaces.telegram.interactions import (
    TelegramChatType,
    TelegramInboundUpdate,
    TelegramUpdateKind,
)
from automation_foundry.surfaces.telegram.media import TelegramMediaInbox


class TelegramLearningCoordinator:
    """Create exactly one authoring job for one accepted `/learn` video."""

    def __init__(self, authoring: AuthoringService, media: TelegramMediaInbox):
        """Initialize the direct authoring handoff.

        Args:
            authoring: Existing dashboard authoring service.
            media: Validated Telegram media quarantine.
        """
        self.authoring = authoring
        self.media = media
        self._initialize_database()

    def accept_video(
        self,
        update: TelegramInboundUpdate,
        *,
        filename: str,
        media_type: str,
        stream: BinaryIO,
    ) -> AutomationManifest:
        """Quarantine a video and create its persistent authoring job.

        Args:
            update: Previously authorized owner-DM `/learn` message.
            filename: Telegram attachment filename.
            media_type: Telegram attachment media type.
            stream: Downloaded attachment bytes.

        Returns:
            Stable automation manifest; duplicate updates return the original.
        """
        name = _automation_name(update)
        existing_id = self._existing_automation_id(update.update_id)
        if existing_id is not None:
            return self.authoring.store.get_manifest(existing_id)
        video = self.media.store_video(
            update_id=update.update_id,
            telegram_user_id=update.user_id,
            telegram_chat_id=update.chat_id,
            filename=filename,
            media_type=media_type,
            stream=stream,
        )
        manifest = self.authoring.store.create_automation(name)
        try:
            with self.media.path_for(video).open("rb") as source:
                self.authoring.uploads.store_upload(
                    manifest.id,
                    filename=video.original_name,
                    media_type=video.media_type,
                    stream=source,
                )
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO telegram_authoring_jobs (update_id, automation_id) VALUES (?, ?)",
                    (update.update_id, str(manifest.id)),
                )
        except sqlite3.IntegrityError:
            self.authoring.delete(manifest.id)
            existing_id = self._existing_automation_id(update.update_id)
            if existing_id is None:
                raise RuntimeError("Telegram authoring idempotency failed") from None
            return self.authoring.store.get_manifest(existing_id)
        except Exception:
            self.authoring.delete(manifest.id)
            raise
        return self.authoring.store.get_manifest(manifest.id)

    async def process(self, automation_id: UUID) -> None:
        """Run the shared provider-backed authoring pipeline.

        Args:
            automation_id: Stable ID returned by `accept_video`.
        """
        await self.authoring.process(automation_id)

    def _existing_automation_id(self, update_id: int) -> UUID | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT automation_id FROM telegram_authoring_jobs WHERE update_id = ?",
                (update_id,),
            ).fetchone()
        return UUID(row[0]) if row is not None else None

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_authoring_jobs (
                    update_id INTEGER PRIMARY KEY,
                    automation_id TEXT NOT NULL UNIQUE
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.media.config.database_path, timeout=5)


def _automation_name(update: TelegramInboundUpdate) -> str:
    if (
        update.kind is not TelegramUpdateKind.MESSAGE
        or update.chat_type is not TelegramChatType.PRIVATE
        or update.user_id <= 0
        or update.chat_id != update.user_id
    ):
        raise ValueError("Telegram learning requires an authenticated owner direct message")
    text = (update.text or "").strip()
    command, separator, name = text.partition(" ")
    normalized_name = " ".join(name.split())
    if command != "/learn" or not separator or not normalized_name:
        raise ValueError("Use /learn <automation name> with one video")
    if len(normalized_name) > 120:
        raise ValueError("Automation name cannot exceed 120 characters")
    return normalized_name
