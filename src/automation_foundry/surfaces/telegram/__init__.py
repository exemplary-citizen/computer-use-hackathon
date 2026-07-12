"""Owner-only Telegram surface contracts."""

from automation_foundry.surfaces.telegram.callbacks import (
    IssuedTelegramCallback,
    TelegramCallbackRejectedError,
    TelegramCallbackStore,
    TelegramCallbackStoreConfig,
)
from automation_foundry.surfaces.telegram.interactions import (
    PROVIDER_DISCLOSURE_SHA256,
    PROVIDER_DISCLOSURE_TEXT,
    TelegramButton,
    TelegramChatType,
    TelegramInboundUpdate,
    TelegramInteractionService,
    TelegramSurfaceConfig,
    TelegramSurfaceResponse,
    TelegramUpdateKind,
)
from automation_foundry.surfaces.telegram.media import (
    QuarantinedTelegramVideo,
    TelegramMediaInbox,
    TelegramMediaInboxConfig,
)

__all__ = [
    "IssuedTelegramCallback",
    "PROVIDER_DISCLOSURE_SHA256",
    "PROVIDER_DISCLOSURE_TEXT",
    "QuarantinedTelegramVideo",
    "TelegramCallbackRejectedError",
    "TelegramCallbackStore",
    "TelegramCallbackStoreConfig",
    "TelegramButton",
    "TelegramChatType",
    "TelegramInboundUpdate",
    "TelegramInteractionService",
    "TelegramMediaInbox",
    "TelegramMediaInboxConfig",
    "TelegramSurfaceConfig",
    "TelegramSurfaceResponse",
    "TelegramUpdateKind",
]
