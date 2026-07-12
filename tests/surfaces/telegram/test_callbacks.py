"""Deterministic Telegram callback vault tests."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from pydantic import SecretStr

from automation_foundry.contracts import SurfaceCallbackAction
from automation_foundry.surfaces.telegram import (
    TelegramCallbackRejectedError,
    TelegramCallbackStoreConfig,
)

PAYLOAD_HASH = "a" * 64
OTHER_HASH = "b" * 64
OWNER_ID = 123456789


class TestTelegramCallbackStore:
    """Exercise identity, action, hash, expiry, and replay binding."""

    def setup_method(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "foundry.sqlite3"
        self.store = TelegramCallbackStoreConfig(
            database_path=self.database_path,
            default_ttl_seconds=600,
        ).make()

    def teardown_method(self) -> None:
        self.temporary_directory.cleanup()

    def test_mint_stores_only_hash_and_consume_returns_exact_binding(self) -> None:
        automation_id = uuid4()
        issued = self.store.mint(
            action=SurfaceCallbackAction.APPROVE_AUTOMATION,
            telegram_user_id=OWNER_ID,
            telegram_chat_id=OWNER_ID,
            payload_sha256=PAYLOAD_HASH,
            automation_id=automation_id,
        )
        callback_data = issued.callback_data.get_secret_value()

        assert callback_data.startswith("f1_")
        assert len(callback_data.encode()) <= 64
        assert callback_data not in issued.model_dump_json()
        assert callback_data not in self.database_path.read_bytes().decode(errors="ignore")

        grant = self.store.consume(
            issued.callback_data,
            telegram_user_id=OWNER_ID,
            telegram_chat_id=OWNER_ID,
            expected_action=SurfaceCallbackAction.APPROVE_AUTOMATION,
            expected_payload_sha256=PAYLOAD_HASH,
        )

        assert grant.id == issued.id
        assert grant.automation_id == automation_id
        assert grant.payload_sha256 == PAYLOAD_HASH
        assert grant.consumed_at is not None

    @pytest.mark.parametrize(
        ("user_id", "chat_id", "action", "payload_hash"),
        (
            (OWNER_ID + 1, OWNER_ID, SurfaceCallbackAction.START_RUN, OTHER_HASH),
            (OWNER_ID, OWNER_ID + 1, SurfaceCallbackAction.START_RUN, OTHER_HASH),
            (OWNER_ID, OWNER_ID, SurfaceCallbackAction.COMMIT_RUN, OTHER_HASH),
            (OWNER_ID, OWNER_ID, SurfaceCallbackAction.START_RUN, PAYLOAD_HASH),
        ),
    )
    def test_mismatch_is_generic_and_does_not_consume(
        self,
        user_id: int,
        chat_id: int,
        action: SurfaceCallbackAction,
        payload_hash: str,
    ) -> None:
        issued = self._mint_start()

        with pytest.raises(TelegramCallbackRejectedError, match="invalid or unavailable"):
            self.store.consume(
                issued.callback_data,
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                expected_action=action,
                expected_payload_sha256=payload_hash,
            )

        grant = self.store.consume(
            issued.callback_data,
            telegram_user_id=OWNER_ID,
            telegram_chat_id=OWNER_ID,
            expected_action=SurfaceCallbackAction.START_RUN,
            expected_payload_sha256=OTHER_HASH,
        )
        assert grant.consumed_at is not None

    def test_replay_is_rejected(self) -> None:
        issued = self._mint_start()
        arguments = {
            "telegram_user_id": OWNER_ID,
            "telegram_chat_id": OWNER_ID,
            "expected_action": SurfaceCallbackAction.START_RUN,
            "expected_payload_sha256": OTHER_HASH,
        }
        self.store.consume(issued.callback_data, **arguments)

        with pytest.raises(TelegramCallbackRejectedError, match="invalid or unavailable"):
            self.store.consume(issued.callback_data, **arguments)

    def test_concurrent_consumers_allow_exactly_one(self) -> None:
        issued = self._mint_start()

        def consume() -> bool:
            try:
                self.store.consume(
                    issued.callback_data,
                    telegram_user_id=OWNER_ID,
                    telegram_chat_id=OWNER_ID,
                    expected_action=SurfaceCallbackAction.START_RUN,
                    expected_payload_sha256=OTHER_HASH,
                )
            except TelegramCallbackRejectedError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(lambda _: consume(), range(2)))

        assert sorted(outcomes) == [False, True]

    def test_expired_callback_is_rejected(self) -> None:
        issued = self._mint_start()
        expired_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("UPDATE telegram_callbacks SET expires_at = ?", (expired_at,))

        with pytest.raises(TelegramCallbackRejectedError, match="invalid or unavailable"):
            self.store.consume(
                issued.callback_data,
                telegram_user_id=OWNER_ID,
                telegram_chat_id=OWNER_ID,
                expected_action=SurfaceCallbackAction.START_RUN,
                expected_payload_sha256=OTHER_HASH,
            )

    def test_non_dm_identity_and_invalid_token_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="direct-message chat"):
            self.store.mint(
                action=SurfaceCallbackAction.ACCEPT_DISCLOSURE,
                telegram_user_id=OWNER_ID,
                telegram_chat_id=OWNER_ID + 1,
                payload_sha256=PAYLOAD_HASH,
            )
        with pytest.raises(TelegramCallbackRejectedError, match="invalid or unavailable"):
            self.store.consume(
                SecretStr("not-a-foundry-callback"),
                telegram_user_id=OWNER_ID,
                telegram_chat_id=OWNER_ID,
                expected_action=SurfaceCallbackAction.ACCEPT_DISCLOSURE,
                expected_payload_sha256=PAYLOAD_HASH,
            )

    def test_ttl_cannot_exceed_configured_default(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            self.store.mint(
                action=SurfaceCallbackAction.ACCEPT_DISCLOSURE,
                telegram_user_id=OWNER_ID,
                telegram_chat_id=OWNER_ID,
                payload_sha256=PAYLOAD_HASH,
                ttl_seconds=601,
            )

    def _mint_start(self):
        return self.store.mint(
            action=SurfaceCallbackAction.START_RUN,
            telegram_user_id=OWNER_ID,
            telegram_chat_id=OWNER_ID,
            payload_sha256=OTHER_HASH,
            run_id=uuid4(),
        )
