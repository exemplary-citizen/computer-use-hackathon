"""Owner-only Telegram interaction routing tests."""

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import SecretStr

from automation_foundry.surfaces.telegram import (
    PROVIDER_DISCLOSURE_SHA256,
    TelegramChatType,
    TelegramInboundUpdate,
    TelegramSurfaceConfig,
    TelegramUpdateKind,
)

OWNER_ID = 123456789


class TestTelegramInteractionService:
    """Verify authorization and disclosure before any model or media work."""

    def setup_method(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "foundry.sqlite3"
        self.service = TelegramSurfaceConfig(
            database_path=self.database_path,
            owner_user_id=OWNER_ID,
        ).make()

    def teardown_method(self) -> None:
        self.temporary_directory.cleanup()

    def test_learn_requires_disclosure_button(self) -> None:
        response = self.service.handle(self._message(1, "/learn Update CRM"))

        assert "Gradium" in response.text
        assert "Holo3" in response.text
        assert len(response.buttons) == 1
        assert response.buttons[0].label == "Accept"
        assert "f1_" not in response.model_dump_json()
        assert not self.service.disclosure_accepted()

    def test_disclosure_callback_enables_learn(self) -> None:
        prompt = self.service.handle(self._message(1, "/learn Update CRM"))
        callback = prompt.buttons[0].callback_data

        accepted = self.service.handle(self._callback(2, callback))
        ready = self.service.handle(self._message(3, "/learn Update CRM"))

        assert accepted.text == "Provider disclosure accepted. Send /learn with one demonstration video."
        assert self.service.disclosure_accepted()
        assert ready.buttons == ()
        assert ready.text.startswith("Disclosure accepted")

    def test_text_never_accepts_disclosure(self) -> None:
        response = self.service.handle(self._message(1, "Accept"))

        assert not self.service.disclosure_accepted()
        assert response.buttons == ()

    def test_wrong_sender_and_group_are_rejected_before_callback_consumption(self) -> None:
        prompt = self.service.handle(self._message(1, "/learn Update CRM"))
        callback = prompt.buttons[0].callback_data
        wrong_user = self._callback(2, callback, user_id=OWNER_ID + 1, chat_id=OWNER_ID + 1)
        group = self._callback(3, callback, chat_id=-100123, chat_type=TelegramChatType.SUPERGROUP)

        assert not self.service.handle(wrong_user).acknowledged
        assert not self.service.handle(group).acknowledged
        assert not self.service.disclosure_accepted()

        accepted = self.service.handle(self._callback(4, callback))
        assert accepted.text.startswith("Provider disclosure accepted")

    def test_duplicate_update_does_not_mint_another_callback(self) -> None:
        first = self.service.handle(self._message(7, "/learn Update CRM"))
        duplicate = self.service.handle(self._message(7, "/learn Update CRM"))

        assert len(first.buttons) == 1
        assert duplicate.text == "This update was already handled."
        assert duplicate.buttons == ()
        with sqlite3.connect(self.database_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM telegram_callbacks").fetchone()[0]
        assert count == 1

    def test_callback_replay_and_wrong_action_token_fail_closed(self) -> None:
        prompt = self.service.handle(self._message(1, "/learn Update CRM"))
        callback = prompt.buttons[0].callback_data
        self.service.handle(self._callback(2, callback))

        replay = self.service.handle(self._callback(3, callback))
        invalid = self.service.handle(self._callback(4, SecretStr("f1_invalid")))

        assert replay.text == "This button is invalid or unavailable."
        assert invalid.text == "This button is invalid or unavailable."

    def test_old_disclosure_revision_does_not_authorize_processing(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO telegram_disclosures VALUES (?, ?, datetime('now'))",
                (OWNER_ID, "0" * 64),
            )

        response = self.service.handle(self._message(1, "/learn Update CRM"))

        assert not self.service.disclosure_accepted()
        assert response.buttons
        assert PROVIDER_DISCLOSURE_SHA256 != "0" * 64

    def _message(self, update_id: int, text: str) -> TelegramInboundUpdate:
        return TelegramInboundUpdate(
            update_id=update_id,
            kind=TelegramUpdateKind.MESSAGE,
            user_id=OWNER_ID,
            chat_id=OWNER_ID,
            chat_type=TelegramChatType.PRIVATE,
            text=text,
        )

    def _callback(
        self,
        update_id: int,
        callback_data: SecretStr,
        *,
        user_id: int = OWNER_ID,
        chat_id: int = OWNER_ID,
        chat_type: TelegramChatType = TelegramChatType.PRIVATE,
    ) -> TelegramInboundUpdate:
        return TelegramInboundUpdate(
            update_id=update_id,
            kind=TelegramUpdateKind.CALLBACK,
            user_id=user_id,
            chat_id=chat_id,
            chat_type=chat_type,
            callback_data=callback_data,
        )
