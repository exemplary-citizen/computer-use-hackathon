"""Owner-only Telegram update authorization and disclosure flow."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, model_validator

from automation_foundry.contracts import SurfaceCallbackAction
from automation_foundry.surfaces.telegram.callbacks import (
    TelegramCallbackRejectedError,
    TelegramCallbackStore,
    TelegramCallbackStoreConfig,
)

PROVIDER_DISCLOSURE_REVISION = "telegram-provider-disclosure-v1"
PROVIDER_DISCLOSURE_TEXT = (
    "Telegram receives the video you send. Foundry stores a local canonical copy, sends required audio to Gradium, "
    "and sends selected evidence to Holo3 through NemoClaw/Hermes. Press Accept to allow provider-backed processing."
)
PROVIDER_DISCLOSURE_SHA256 = hashlib.sha256(
    f"{PROVIDER_DISCLOSURE_REVISION}\n{PROVIDER_DISCLOSURE_TEXT}".encode()
).hexdigest()


class TelegramChatType(StrEnum):
    """Telegram chat scope relevant to the owner-only MVP."""

    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class TelegramUpdateKind(StrEnum):
    """Inbound event kind accepted by the deterministic adapter."""

    MESSAGE = "message"
    CALLBACK = "callback"


class TelegramSurfaceConfig(BaseModel):
    """Owner identity and durable interaction state."""

    database_path: Path = Path("data/automation_foundry.sqlite3")
    """Foundry database shared with callback and interaction state."""
    owner_user_id: int = Field(gt=0)
    """Single numeric Telegram owner identifier."""

    def make(self) -> TelegramInteractionService:
        """Build and initialize the interaction service."""
        callback_store = TelegramCallbackStoreConfig(database_path=self.database_path).make()
        return TelegramInteractionService(self, callback_store)


class TelegramInboundUpdate(BaseModel):
    """Normalized untrusted Telegram update."""

    update_id: int = Field(ge=0)
    kind: TelegramUpdateKind
    user_id: int
    chat_id: int
    chat_type: TelegramChatType
    text: str | None = Field(default=None, max_length=4_096)
    callback_data: SecretStr | None = None

    @model_validator(mode="after")
    def validate_kind_payload(self) -> TelegramInboundUpdate:
        """Keep message and callback payloads structurally distinct."""
        if self.kind is TelegramUpdateKind.CALLBACK and self.callback_data is None:
            raise ValueError("Callback update requires callback_data")
        if self.kind is TelegramUpdateKind.MESSAGE and self.callback_data is not None:
            raise ValueError("Message update cannot contain callback_data")
        return self


class TelegramButton(BaseModel):
    """One inline button with opaque callback data."""

    label: str = Field(min_length=1, max_length=64)
    callback_data: SecretStr


class TelegramSurfaceResponse(BaseModel):
    """Safe response rendered by the Telegram transport."""

    text: str = Field(min_length=1, max_length=4_096)
    buttons: tuple[TelegramButton, ...] = ()
    acknowledged: bool = True


class TelegramInteractionService:
    """Authorize, deduplicate, and route deterministic Telegram interactions."""

    def __init__(self, config: TelegramSurfaceConfig, callbacks: TelegramCallbackStore):
        """Initialize interaction state.

        Args:
            config: Owner and database settings.
            callbacks: Durable single-use callback vault.
        """
        self.config = config
        self.callbacks = callbacks
        self._initialize_database()

    def handle(self, update: TelegramInboundUpdate) -> TelegramSurfaceResponse:
        """Handle one normalized update without model interpretation.

        Args:
            update: Untrusted event normalized by the transport.

        Returns:
            Safe text and optional opaque buttons.
        """
        if not self._is_owner_dm(update):
            return TelegramSurfaceResponse(text="This bot is private.", acknowledged=False)
        if not self._claim_update(update.update_id):
            return TelegramSurfaceResponse(text="This update was already handled.")
        if update.kind is TelegramUpdateKind.CALLBACK:
            return self._handle_callback(update)
        return self._handle_message(update)

    def disclosure_accepted(self) -> bool:
        """Return whether the owner accepted the current disclosure revision."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT disclosure_sha256 FROM telegram_disclosures WHERE telegram_user_id = ?",
                (self.config.owner_user_id,),
            ).fetchone()
        return row is not None and row[0] == PROVIDER_DISCLOSURE_SHA256

    def _handle_message(self, update: TelegramInboundUpdate) -> TelegramSurfaceResponse:
        text = (update.text or "").strip()
        if not text.startswith("/learn"):
            return TelegramSurfaceResponse(text="Use /learn with a demonstration video to teach an automation.")
        if self.disclosure_accepted():
            return TelegramSurfaceResponse(
                text="Disclosure accepted. Attach one supported demonstration video to /learn."
            )
        issued = self.callbacks.mint(
            action=SurfaceCallbackAction.ACCEPT_DISCLOSURE,
            telegram_user_id=update.user_id,
            telegram_chat_id=update.chat_id,
            payload_sha256=PROVIDER_DISCLOSURE_SHA256,
        )
        return TelegramSurfaceResponse(
            text=PROVIDER_DISCLOSURE_TEXT,
            buttons=(TelegramButton(label="Accept", callback_data=issued.callback_data),),
        )

    def _handle_callback(self, update: TelegramInboundUpdate) -> TelegramSurfaceResponse:
        callback_data = update.callback_data
        if callback_data is None:
            return TelegramSurfaceResponse(text="This button is invalid or unavailable.")
        try:
            self.callbacks.consume(
                callback_data,
                telegram_user_id=update.user_id,
                telegram_chat_id=update.chat_id,
                expected_action=SurfaceCallbackAction.ACCEPT_DISCLOSURE,
                expected_payload_sha256=PROVIDER_DISCLOSURE_SHA256,
            )
        except TelegramCallbackRejectedError:
            return TelegramSurfaceResponse(text="This button is invalid or unavailable.")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO telegram_disclosures (telegram_user_id, disclosure_sha256, accepted_at)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    disclosure_sha256 = excluded.disclosure_sha256,
                    accepted_at = excluded.accepted_at
                """,
                (update.user_id, PROVIDER_DISCLOSURE_SHA256, datetime.now(UTC).isoformat()),
            )
        return TelegramSurfaceResponse(text="Provider disclosure accepted. Send /learn with one demonstration video.")

    def _is_owner_dm(self, update: TelegramInboundUpdate) -> bool:
        return (
            update.chat_type is TelegramChatType.PRIVATE
            and update.user_id == self.config.owner_user_id
            and update.chat_id == update.user_id
        )

    def _claim_update(self, update_id: int) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "INSERT OR IGNORE INTO telegram_updates (update_id, received_at) VALUES (?, ?)",
                (update_id, datetime.now(UTC).isoformat()),
            )
        return result.rowcount == 1

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_updates (
                    update_id INTEGER PRIMARY KEY,
                    received_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_disclosures (
                    telegram_user_id INTEGER PRIMARY KEY,
                    disclosure_sha256 TEXT NOT NULL,
                    accepted_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.config.database_path, timeout=5)
