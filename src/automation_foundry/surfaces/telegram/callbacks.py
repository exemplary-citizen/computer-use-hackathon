"""Durable, single-use Telegram callback token storage."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, SecretStr

from automation_foundry.contracts import Sha256, SurfaceCallbackAction, SurfaceCallbackGrant


class TelegramCallbackStoreConfig(BaseModel):
    """SQLite location and lifetime policy for Telegram buttons."""

    database_path: Path = Path("data/automation_foundry.sqlite3")
    """Existing Foundry SQLite database used for callback state."""
    default_ttl_seconds: int = Field(default=600, ge=30, le=3_600)
    """Default callback lifetime."""
    token_bytes: int = Field(default=24, ge=16, le=32)
    """Entropy bytes encoded into callback data."""

    def make(self) -> TelegramCallbackStore:
        """Build and initialize the callback store."""
        return TelegramCallbackStore(self)


class IssuedTelegramCallback(BaseModel):
    """Opaque callback data returned only to the Telegram presentation layer."""

    id: UUID
    callback_data: SecretStr
    expires_at: datetime


class TelegramCallbackRejectedError(ValueError):
    """Raised when a callback cannot authorize the requested transition."""


class TelegramCallbackStore:
    """Mint and atomically consume identity/action/hash-bound callbacks."""

    def __init__(self, config: TelegramCallbackStoreConfig):
        """Initialize the durable callback vault.

        Args:
            config: Database, lifetime, and token-entropy policy.
        """
        self.config = config
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def mint(
        self,
        *,
        action: SurfaceCallbackAction,
        telegram_user_id: int,
        telegram_chat_id: int,
        payload_sha256: Sha256,
        automation_id: UUID | None = None,
        run_id: UUID | None = None,
        ttl_seconds: int | None = None,
    ) -> IssuedTelegramCallback:
        """Create one short-lived opaque callback.

        Args:
            action: Exact transition this callback may authorize.
            telegram_user_id: Numerically allowlisted owner identity.
            telegram_chat_id: Owner's direct-message chat identity.
            payload_sha256: Hash of the exact preview or staged payload.
            automation_id: Optional bound automation identifier.
            run_id: Optional bound run identifier.
            ttl_seconds: Optional lifetime override within the configured ceiling.

        Returns:
            Opaque callback data and expiry for one Telegram button.
        """
        lifetime = ttl_seconds if ttl_seconds is not None else self.config.default_ttl_seconds
        if lifetime < 1 or lifetime > self.config.default_ttl_seconds:
            raise ValueError("Callback lifetime must be positive and cannot exceed the configured default")
        issued_at = datetime.now(UTC)
        grant = SurfaceCallbackGrant(
            id=uuid4(),
            action=action,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            payload_sha256=payload_sha256,
            automation_id=automation_id,
            run_id=run_id,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=lifetime),
        )
        callback_data = f"f1_{secrets.token_urlsafe(self.config.token_bytes)}"
        token_sha256 = _token_hash(callback_data)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO telegram_callbacks (
                    id, token_sha256, action, telegram_user_id, telegram_chat_id, payload_sha256,
                    automation_id, run_id, issued_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    str(grant.id),
                    token_sha256,
                    grant.action.value,
                    grant.telegram_user_id,
                    grant.telegram_chat_id,
                    grant.payload_sha256,
                    str(grant.automation_id) if grant.automation_id is not None else None,
                    str(grant.run_id) if grant.run_id is not None else None,
                    grant.issued_at.isoformat(),
                    grant.expires_at.isoformat(),
                ),
            )
        return IssuedTelegramCallback(
            id=grant.id,
            callback_data=SecretStr(callback_data),
            expires_at=grant.expires_at,
        )

    def consume(
        self,
        callback_data: SecretStr,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        expected_action: SurfaceCallbackAction,
        expected_payload_sha256: Sha256,
    ) -> SurfaceCallbackGrant:
        """Atomically consume a callback only when every binding matches.

        Args:
            callback_data: Opaque value received from Telegram.
            telegram_user_id: Authenticated callback sender identity.
            telegram_chat_id: Authenticated direct-message chat identity.
            expected_action: Transition the handler intends to perform.
            expected_payload_sha256: Current host payload hash.

        Returns:
            Consumed immutable grant.

        Raises:
            TelegramCallbackRejectedError: If any binding, expiry, or one-time-use check fails.
        """
        token = callback_data.get_secret_value()
        if len(token) > 64 or not token.startswith("f1_"):
            raise TelegramCallbackRejectedError("Callback is invalid or unavailable")
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, action, telegram_user_id, telegram_chat_id, payload_sha256,
                       automation_id, run_id, issued_at, expires_at, consumed_at
                FROM telegram_callbacks
                WHERE token_sha256 = ?
                """,
                (_token_hash(token),),
            ).fetchone()
            if row is None or not self._matches(
                row, telegram_user_id, telegram_chat_id, expected_action, expected_payload_sha256, now
            ):
                connection.rollback()
                raise TelegramCallbackRejectedError("Callback is invalid or unavailable")
            consumed_at = now.isoformat()
            updated = connection.execute(
                "UPDATE telegram_callbacks SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
                (consumed_at, row[0]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise TelegramCallbackRejectedError("Callback is invalid or unavailable")
            connection.commit()
        return SurfaceCallbackGrant(
            id=UUID(row[0]),
            action=SurfaceCallbackAction(row[1]),
            telegram_user_id=row[2],
            telegram_chat_id=row[3],
            payload_sha256=row[4],
            automation_id=UUID(row[5]) if row[5] is not None else None,
            run_id=UUID(row[6]) if row[6] is not None else None,
            issued_at=datetime.fromisoformat(row[7]),
            expires_at=datetime.fromisoformat(row[8]),
            consumed_at=now,
        )

    def peek(
        self,
        callback_data: SecretStr,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
    ) -> SurfaceCallbackGrant:
        """Read one live callback binding without authorizing its action.

        Args:
            callback_data: Opaque value received from Telegram.
            telegram_user_id: Authenticated callback sender identity.
            telegram_chat_id: Authenticated direct-message chat identity.

        Returns:
            Unconsumed action and resource binding.

        Raises:
            TelegramCallbackRejectedError: If identity, expiry, token, or one-time state is invalid.
        """
        token = callback_data.get_secret_value()
        if len(token) > 64 or not token.startswith("f1_"):
            raise TelegramCallbackRejectedError("Callback is invalid or unavailable")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, action, telegram_user_id, telegram_chat_id, payload_sha256,
                       automation_id, run_id, issued_at, expires_at, consumed_at
                FROM telegram_callbacks
                WHERE token_sha256 = ?
                """,
                (_token_hash(token),),
            ).fetchone()
        now = datetime.now(UTC)
        if (
            row is None
            or row[9] is not None
            or row[2] != telegram_user_id
            or row[3] != telegram_chat_id
            or datetime.fromisoformat(str(row[8])) <= now
        ):
            raise TelegramCallbackRejectedError("Callback is invalid or unavailable")
        return SurfaceCallbackGrant(
            id=UUID(row[0]),
            action=SurfaceCallbackAction(row[1]),
            telegram_user_id=row[2],
            telegram_chat_id=row[3],
            payload_sha256=row[4],
            automation_id=UUID(row[5]) if row[5] is not None else None,
            run_id=UUID(row[6]) if row[6] is not None else None,
            issued_at=datetime.fromisoformat(row[7]),
            expires_at=datetime.fromisoformat(row[8]),
        )

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_callbacks (
                    id TEXT PRIMARY KEY,
                    token_sha256 TEXT NOT NULL UNIQUE,
                    action TEXT NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    telegram_chat_id INTEGER NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    automation_id TEXT,
                    run_id TEXT,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_telegram_callbacks_expiry ON telegram_callbacks(expires_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.config.database_path, timeout=5, isolation_level=None)

    def _matches(
        self,
        row: sqlite3.Row | tuple[object, ...],
        telegram_user_id: int,
        telegram_chat_id: int,
        expected_action: SurfaceCallbackAction,
        expected_payload_sha256: Sha256,
        now: datetime,
    ) -> bool:
        return (
            row[9] is None
            and row[1] == expected_action.value
            and row[2] == telegram_user_id
            and row[3] == telegram_chat_id
            and secrets.compare_digest(str(row[4]), expected_payload_sha256)
            and datetime.fromisoformat(str(row[8])) > now
        )


def _token_hash(callback_data: str) -> str:
    return hashlib.sha256(callback_data.encode()).hexdigest()
