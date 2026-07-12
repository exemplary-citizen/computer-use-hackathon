"""Direct Telegram-to-authoring handoff tests."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from automation_foundry.authoring.service import build_authoring_service
from automation_foundry.settings import AppSettings
from automation_foundry.surfaces.telegram import (
    TelegramChatType,
    TelegramInboundUpdate,
    TelegramLearningCoordinator,
    TelegramMediaInboxConfig,
    TelegramUpdateKind,
)

OWNER_ID = 123456789


class TestTelegramLearningCoordinator:
    """Verify one accepted video creates one shared authoring job."""

    def setup_method(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        database_path = root / "foundry.sqlite3"
        self.authoring = build_authoring_service(
            AppSettings(
                data_root=root / "automations",
                database_path=database_path,
                published_skill_root=root / "skills",
            )
        )
        media = TelegramMediaInboxConfig(
            root=root / "telegram-inbox",
            database_path=database_path,
            max_video_bytes=100,
        ).make()
        self.coordinator = TelegramLearningCoordinator(self.authoring, media)

    def teardown_method(self) -> None:
        self.temporary_directory.cleanup()

    def test_video_creates_shared_authoring_manifest_and_source(self) -> None:
        manifest = self.coordinator.accept_video(
            self._update(1, "/learn Update CRM lead"),
            filename="demo.mp4",
            media_type="video/mp4",
            stream=BytesIO(b"video-bytes"),
        )

        assert manifest.name == "Update CRM lead"
        assert manifest.status.value == "processing"
        assert len(manifest.sources) == 1
        assert manifest.sources[0].original_name == "demo.mp4"
        assert self.authoring.store.get_manifest(manifest.id) == manifest

    def test_duplicate_update_returns_original_without_reading_stream(self) -> None:
        update = self._update(1, "/learn Update CRM lead")
        first = self.coordinator.accept_video(
            update,
            filename="demo.mp4",
            media_type="video/mp4",
            stream=BytesIO(b"video-bytes"),
        )

        class FailStream:
            def read(self, _size):
                raise AssertionError("duplicate update must not read media")

        duplicate = self.coordinator.accept_video(
            update,
            filename="demo.mp4",
            media_type="video/mp4",
            stream=FailStream(),
        )

        assert duplicate.id == first.id
        assert len(self.authoring.store.list_automations()) == 1

    @pytest.mark.parametrize("text", ("/learn", "/run Update CRM", "/learn " + "x" * 121))
    def test_invalid_command_creates_no_media_or_automation(self, text: str) -> None:
        with pytest.raises(ValueError):
            self.coordinator.accept_video(
                self._update(1, text),
                filename="demo.mp4",
                media_type="video/mp4",
                stream=BytesIO(b"video-bytes"),
            )

        assert self.authoring.store.list_automations() == []
        assert not tuple(self.coordinator.media.config.root.iterdir())

    @pytest.mark.asyncio
    async def test_processing_uses_existing_fail_closed_pipeline(self) -> None:
        manifest = self.coordinator.accept_video(
            self._update(1, "/learn Update CRM lead"),
            filename="demo.mp4",
            media_type="video/mp4",
            stream=BytesIO(b"video-bytes"),
        )

        await self.coordinator.process(manifest.id)

        failed = self.authoring.store.get_manifest(manifest.id)
        assert failed.status.value == "failed"
        assert self.authoring.processing_error(manifest.id).startswith("Generation is not configured")

    def _update(self, update_id: int, text: str) -> TelegramInboundUpdate:
        return TelegramInboundUpdate(
            update_id=update_id,
            kind=TelegramUpdateKind.MESSAGE,
            user_id=OWNER_ID,
            chat_id=OWNER_ID,
            chat_type=TelegramChatType.PRIVATE,
            text=text,
        )
