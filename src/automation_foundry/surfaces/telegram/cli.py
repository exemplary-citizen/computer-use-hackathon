"""Owner-only Telegram polling entrypoint."""

from automation_foundry.authoring.service import build_authoring_service
from automation_foundry.settings import AppSettings
from automation_foundry.surfaces.telegram.interactions import TelegramSurfaceConfig
from automation_foundry.surfaces.telegram.learning import TelegramLearningCoordinator
from automation_foundry.surfaces.telegram.media import TelegramMediaInboxConfig
from automation_foundry.surfaces.telegram.transport import build_telegram_runtime


def main() -> None:
    """Run the local Telegram adapter with environment-backed settings."""
    settings = AppSettings()
    if settings.telegram_bot_token is None or settings.telegram_owner_id is None:
        raise RuntimeError("FOUNDRY_TELEGRAM_BOT_TOKEN and FOUNDRY_TELEGRAM_OWNER_ID are required")
    authoring = build_authoring_service(settings)
    interactions = TelegramSurfaceConfig(
        database_path=settings.database_path,
        owner_user_id=settings.telegram_owner_id,
    ).make()
    media = TelegramMediaInboxConfig(
        root=settings.telegram_inbox_root,
        database_path=settings.database_path,
    ).make()
    learning = TelegramLearningCoordinator(authoring, media)
    build_telegram_runtime(settings.telegram_bot_token, interactions, learning).run()


if __name__ == "__main__":
    main()
