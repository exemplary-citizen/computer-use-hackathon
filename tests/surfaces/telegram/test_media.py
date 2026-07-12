"""Telegram media quarantine tests."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from automation_foundry.surfaces.telegram import TelegramMediaInboxConfig

OWNER_ID = 123456789


class TestTelegramMediaInbox:
    """Verify bounded, idempotent, path-confined video storage."""

    def setup_method(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.inbox = TelegramMediaInboxConfig(
            root=root / "inbox",
            database_path=root / "foundry.sqlite3",
            max_video_bytes=16,
        ).make()

    def teardown_method(self) -> None:
        self.temporary_directory.cleanup()

    def test_stores_hashes_and_resolves_one_opaque_video(self) -> None:
        video = self._store(1, b"video-bytes")

        assert video.original_name == "demo.mp4"
        assert video.size_bytes == 11
        assert video.relative_path == f"{video.id}.mp4"
        assert self.inbox.path_for(video).read_bytes() == b"video-bytes"
        assert str(self.inbox.config.root) not in video.model_dump_json()

    def test_duplicate_update_returns_same_video_without_reading_stream(self) -> None:
        first = self._store(1, b"video-bytes")

        class FailStream:
            def read(self, _size):
                raise AssertionError("duplicate update must not read media again")

        duplicate = self.inbox.store_video(
            update_id=1,
            telegram_user_id=OWNER_ID,
            telegram_chat_id=OWNER_ID,
            filename="demo.mp4",
            media_type="video/mp4",
            stream=FailStream(),
        )

        assert duplicate == first
        assert len(tuple(self.inbox.config.root.glob("*.mp4"))) == 1

    @pytest.mark.parametrize(
        ("filename", "media_type"),
        (
            ("../demo.mp4", "video/mp4"),
            ("demo.exe", "video/mp4"),
            ("demo.mp4", "text/plain"),
            ("", "video/mp4"),
        ),
    )
    def test_rejects_unsafe_or_unsupported_metadata(self, filename: str, media_type: str) -> None:
        with pytest.raises(ValueError):
            self.inbox.store_video(
                update_id=1,
                telegram_user_id=OWNER_ID,
                telegram_chat_id=OWNER_ID,
                filename=filename,
                media_type=media_type,
                stream=BytesIO(b"video"),
            )
        assert not tuple(self.inbox.config.root.glob("*.mp4"))

    def test_rejects_oversized_empty_and_non_dm_media_without_artifacts(self) -> None:
        with pytest.raises(ValueError, match="byte limit"):
            self._store(1, b"x" * 17)
        with pytest.raises(ValueError, match="empty"):
            self._store(2, b"")
        with pytest.raises(ValueError, match="owner direct message"):
            self.inbox.store_video(
                update_id=3,
                telegram_user_id=OWNER_ID,
                telegram_chat_id=-100123,
                filename="demo.mp4",
                media_type="video/mp4",
                stream=BytesIO(b"video"),
            )
        assert not tuple(self.inbox.config.root.iterdir())

    def test_conflicting_replay_and_changed_bytes_fail_closed(self) -> None:
        video = self._store(1, b"video-bytes")
        with pytest.raises(ValueError, match="different media"):
            self.inbox.store_video(
                update_id=1,
                telegram_user_id=OWNER_ID,
                telegram_chat_id=OWNER_ID,
                filename="other.mp4",
                media_type="video/mp4",
                stream=BytesIO(b"other"),
            )
        self.inbox.path_for(video).write_bytes(b"changed")
        with pytest.raises(RuntimeError, match="hash changed"):
            self.inbox.path_for(video)

    def _store(self, update_id: int, content: bytes):
        return self.inbox.store_video(
            update_id=update_id,
            telegram_user_id=OWNER_ID,
            telegram_chat_id=OWNER_ID,
            filename="demo.mp4",
            media_type="video/mp4",
            stream=BytesIO(content),
        )
