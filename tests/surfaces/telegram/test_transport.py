"""Mocked python-telegram-bot transport walkthrough."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from automation_foundry.authoring.service import build_authoring_service
from automation_foundry.settings import AppSettings
from automation_foundry.surfaces.telegram import (
    TelegramChatType,
    TelegramInboundUpdate,
    TelegramLearningCoordinator,
    TelegramMediaInboxConfig,
    TelegramSurfaceConfig,
    TelegramUpdateKind,
)
from automation_foundry.surfaces.telegram.transport import TelegramBotRuntime, TelegramTransportConfig

OWNER_ID = 123456789


class FakeTelegramFile:
    """Write fixed video bytes into python-telegram-bot's download target."""

    async def download_to_memory(self, out) -> None:
        out.write(b"video-bytes")


class FakeVideo:
    """Track whether the transport attempted a Telegram download."""

    file_name = "demo.mp4"
    mime_type = "video/mp4"

    def __init__(self) -> None:
        self.download_requested = False

    async def get_file(self) -> FakeTelegramFile:
        self.download_requested = True
        return FakeTelegramFile()


class FakeMessage:
    """Capture Telegram responses without network traffic."""

    def __init__(self, caption: str, video: FakeVideo | None = None) -> None:
        self.text = None
        self.caption = caption
        self.video = video
        self.replies: list[tuple[str, object | None]] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:
        self.replies.append((text, reply_markup))


class TestTelegramBotRuntime:
    """Verify the complete mocked `/learn + video` transport path."""

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
        self.interactions = TelegramSurfaceConfig(
            database_path=database_path,
            owner_user_id=OWNER_ID,
        ).make()
        media = TelegramMediaInboxConfig(
            root=root / "telegram-inbox",
            database_path=database_path,
            max_video_bytes=100,
        ).make()
        learning = TelegramLearningCoordinator(self.authoring, media)
        self.runtime = TelegramBotRuntime(
            TelegramTransportConfig(bot_token=SecretStr("123456:TEST_TOKEN")),
            self.interactions,
            learning,
        )

    def teardown_method(self) -> None:
        self.temporary_directory.cleanup()

    @pytest.mark.asyncio
    async def test_disclosure_blocks_download_then_accepted_video_starts_job(self) -> None:
        blocked_video = FakeVideo()
        blocked_message = FakeMessage("/learn Update CRM", blocked_video)

        await self.runtime.handle_message(self._update(1, blocked_message), SimpleNamespace())

        assert not blocked_video.download_requested
        assert blocked_message.replies[0][1] is not None
        assert self.authoring.store.list_automations() == []

        callback_data = self._disclosure_callback()
        self.interactions.handle(
            TelegramInboundUpdate(
                update_id=2,
                kind=TelegramUpdateKind.CALLBACK,
                user_id=OWNER_ID,
                chat_id=OWNER_ID,
                chat_type=TelegramChatType.PRIVATE,
                callback_data=callback_data,
            )
        )
        accepted_video = FakeVideo()
        accepted_message = FakeMessage("/learn Update CRM", accepted_video)

        await self.runtime.handle_message(self._update(3, accepted_message), SimpleNamespace())
        if self.runtime._background_tasks:
            await asyncio.gather(*self.runtime._background_tasks)

        assert accepted_video.download_requested
        manifests = self.authoring.store.list_automations()
        assert len(manifests) == 1
        assert manifests[0].name == "Update CRM"
        assert str(manifests[0].id) in accepted_message.replies[-1][0]

    @pytest.mark.asyncio
    async def test_wrong_owner_and_group_never_download_video(self) -> None:
        for update_id, user_id, chat_id, chat_type in (
            (1, OWNER_ID + 1, OWNER_ID + 1, "private"),
            (2, OWNER_ID, -100123, "supergroup"),
        ):
            video = FakeVideo()
            message = FakeMessage("/learn Update CRM", video)
            update = self._update(update_id, message, user_id=user_id, chat_id=chat_id, chat_type=chat_type)

            await self.runtime.handle_message(update, SimpleNamespace())

            assert not video.download_requested
            assert message.replies == [("This bot is private.", None)]

    def test_application_builds_without_network_and_masks_token(self) -> None:
        application = self.runtime.build_application()

        assert application.bot.token == "123456:TEST_TOKEN"
        assert "TEST_TOKEN" not in self.runtime.config.model_dump_json()

    def _update(
        self,
        update_id: int,
        message: FakeMessage,
        *,
        user_id: int = OWNER_ID,
        chat_id: int = OWNER_ID,
        chat_type: str = "private",
    ):
        return SimpleNamespace(
            update_id=update_id,
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
            effective_message=message,
        )

    def _disclosure_callback(self) -> SecretStr:
        prompt = self.interactions.handle(
            TelegramInboundUpdate(
                update_id=10,
                kind=TelegramUpdateKind.MESSAGE,
                user_id=OWNER_ID,
                chat_id=OWNER_ID,
                chat_type=TelegramChatType.PRIVATE,
                text="/learn Update CRM",
            )
        )
        return prompt.buttons[0].callback_data
