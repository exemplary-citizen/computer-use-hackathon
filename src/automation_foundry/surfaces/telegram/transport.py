"""Thin python-telegram-bot transport for the hackathon demo path."""

from __future__ import annotations

import asyncio
import logging
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, cast

from pydantic import BaseModel, SecretStr
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Video
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from automation_foundry.surfaces.telegram.interactions import (
    PROVIDER_DISCLOSURE_ACCEPTED_TEXT,
    TelegramChatType,
    TelegramInboundUpdate,
    TelegramInteractionService,
    TelegramSurfaceResponse,
    TelegramUpdateKind,
)
from automation_foundry.surfaces.telegram.learning import TelegramLearningCoordinator

logger = logging.getLogger(__name__)


class TelegramTransportConfig(BaseModel):
    """Secret bot credential for one owner-only polling process."""

    bot_token: SecretStr
    """BotFather token exposed only to python-telegram-bot."""


class TelegramBotRuntime:
    """Translate Telegram updates into deterministic Foundry operations."""

    def __init__(
        self,
        config: TelegramTransportConfig,
        interactions: TelegramInteractionService,
        learning: TelegramLearningCoordinator,
    ):
        """Initialize the thin transport.

        Args:
            config: Bot token.
            interactions: Owner, disclosure, deduplication, and callback service.
            learning: Existing AuthoringService handoff.
        """
        self.config = config
        self.interactions = interactions
        self.learning = learning
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._pending_learn: dict[str, tuple[TelegramInboundUpdate, Video]] = {}

    def build_application(self) -> Application[Any, Any, Any, Any, Any, Any]:
        """Build the polling application without starting network traffic."""
        application = Application.builder().token(self.config.bot_token.get_secret_value()).build()
        application.add_handler(CallbackQueryHandler(self.handle_callback))
        application.add_handler(MessageHandler(filters.ALL, self.handle_message))
        application.add_error_handler(self.handle_error)
        return application

    async def handle_message(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Authorize a message, then optionally ingest one `/learn` video.

        Args:
            update: python-telegram-bot update.
            _context: Unused Telegram callback context.
        """
        normalized = _normalize_message(update)
        response = self.interactions.handle(normalized)
        message = update.effective_message
        if message is None:
            return
        video = message.video
        if response.buttons and video is not None and (normalized.text or "").strip().startswith("/learn"):
            callback_data = response.buttons[0].callback_data.get_secret_value()
            self._pending_learn[callback_data] = (normalized, video)
        if not response.acknowledged or response.buttons or response.text == "This update was already handled.":
            await message.reply_text(response.text, reply_markup=_markup(response))
            return
        if video is None or not (normalized.text or "").strip().startswith("/learn"):
            await message.reply_text(response.text)
            return
        await message.reply_text(await self._accept_video(normalized, video))

    async def handle_callback(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Consume one deterministic inline-button callback.

        Args:
            update: python-telegram-bot callback update.
            _context: Unused Telegram callback context.
        """
        query = update.callback_query
        if query is None:
            return
        callback_data = query.data if isinstance(query.data, str) else None
        response = self.interactions.handle(_normalize_callback(update))
        await query.answer()
        if callback_data is not None and response.text == PROVIDER_DISCLOSURE_ACCEPTED_TEXT:
            pending = self._pending_learn.pop(callback_data, None)
            if pending is not None:
                normalized, video = pending
                response = response.model_copy(update={"text": await self._accept_video(normalized, video)})
        if query.message is not None and hasattr(query.message, "reply_text"):
            await cast(Any, query.message).reply_text(response.text, reply_markup=_markup(response))

    async def handle_error(self, _update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log only an exception type so bot-token URLs cannot leak.

        Args:
            _update: Unused update that failed.
            context: Telegram context containing the exception.
        """
        logger.error("Telegram update failed (%s)", type(context.error).__name__)

    def run(self) -> None:
        """Run long polling until the process is interrupted."""
        self.build_application().run_polling(allowed_updates=Update.ALL_TYPES)

    async def _accept_video(self, normalized: TelegramInboundUpdate, video: Video) -> str:
        telegram_file = await video.get_file()
        with SpooledTemporaryFile(max_size=20_000_000, mode="w+b") as stream:
            binary_stream = cast(BinaryIO, stream)
            await telegram_file.download_to_memory(out=binary_stream)
            stream.seek(0)
            manifest = self.learning.accept_video(
                normalized,
                filename=video.file_name or "demonstration.mp4",
                media_type=video.mime_type or "video/mp4",
                stream=binary_stream,
            )
        task = asyncio.create_task(self.learning.process(manifest.id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return f"Accepted `{manifest.name}`. Automation ID: `{manifest.id}`. Processing in background."


def build_telegram_runtime(
    token: SecretStr,
    interactions: TelegramInteractionService,
    learning: TelegramLearningCoordinator,
) -> TelegramBotRuntime:
    """Compose the Telegram transport from already initialized host services.

    Args:
        token: BotFather token.
        interactions: Deterministic interaction service.
        learning: Existing authoring handoff.

    Returns:
        Ready polling runtime.
    """
    return TelegramBotRuntime(TelegramTransportConfig(bot_token=token), interactions, learning)


def _normalize_message(update: Update) -> TelegramInboundUpdate:
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message
    if user is None or chat is None or message is None:
        raise ValueError("Telegram message is missing sender or chat identity")
    return TelegramInboundUpdate(
        update_id=update.update_id,
        kind=TelegramUpdateKind.MESSAGE,
        user_id=user.id,
        chat_id=chat.id,
        chat_type=TelegramChatType(chat.type),
        text=message.text or message.caption,
    )


def _normalize_callback(update: Update) -> TelegramInboundUpdate:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if query is None or user is None or chat is None or not isinstance(query.data, str):
        raise ValueError("Telegram callback is missing sender, chat, or callback data")
    return TelegramInboundUpdate(
        update_id=update.update_id,
        kind=TelegramUpdateKind.CALLBACK,
        user_id=user.id,
        chat_id=chat.id,
        chat_type=TelegramChatType(chat.type),
        callback_data=SecretStr(query.data),
    )


def _markup(response: TelegramSurfaceResponse) -> InlineKeyboardMarkup | None:
    if not response.buttons:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(button.label, callback_data=button.callback_data.get_secret_value())]
            for button in response.buttons
        ]
    )
