"""Owner-only Telegram polling entrypoint."""

import subprocess
from pathlib import Path

from pydantic import SecretStr

from automation_foundry.authoring.service import build_authoring_service
from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.settings import AppSettings
from automation_foundry.surfaces.telegram.interactions import TelegramSurfaceConfig
from automation_foundry.surfaces.telegram.execution import TelegramExecutionCoordinator
from automation_foundry.surfaces.telegram.learning import TelegramLearningCoordinator
from automation_foundry.surfaces.telegram.media import TelegramMediaInboxConfig
from automation_foundry.surfaces.telegram.transport import build_telegram_runtime

DEFAULT_NEMOCLAW_SANDBOX = "hai-hermes"
DEFAULT_WORKSPACE_MOUNT = Path("data/nemoclaw-workspace")


def resolve_telegram_settings(settings: AppSettings) -> AppSettings:
    """Fill local NemoClaw authoring settings without exposing its gateway token.

    Args:
        settings: Environment-backed application settings.

    Returns:
        Settings configured for the existing local Hermes sandbox.

    Raises:
        RuntimeError: If the local sandbox gateway token cannot be discovered.
    """
    sandbox_name = settings.nemoclaw_sandbox_name or DEFAULT_NEMOCLAW_SANDBOX
    workspace_mount = settings.workspace_mount or DEFAULT_WORKSPACE_MOUNT
    workspace_mount.mkdir(parents=True, exist_ok=True)
    hermes_api_key = settings.hermes_api_key or _discover_gateway_token(settings.nemohermes_binary, sandbox_name)
    return settings.model_copy(
        update={
            "workspace_mount": workspace_mount,
            "workspace_require_mount": False,
            "nemoclaw_sandbox_name": sandbox_name,
            "hermes_api_key": hermes_api_key,
            "allow_untranscribed_audio": settings.gradium_api_key is None,
        }
    )


def main() -> None:
    """Run the local Telegram adapter with environment-backed settings."""
    settings = AppSettings()
    telegram_bot_token = settings.telegram_bot_token
    telegram_owner_id = settings.telegram_owner_id
    if telegram_bot_token is None or telegram_owner_id is None:
        raise RuntimeError("FOUNDRY_TELEGRAM_BOT_TOKEN and FOUNDRY_TELEGRAM_OWNER_ID are required")
    settings = resolve_telegram_settings(settings)
    authoring = build_authoring_service(settings)
    interactions = TelegramSurfaceConfig(
        database_path=settings.database_path,
        owner_user_id=telegram_owner_id,
    ).make()
    media = TelegramMediaInboxConfig(
        root=settings.telegram_inbox_root,
        database_path=settings.database_path,
    ).make()
    learning = TelegramLearningCoordinator(authoring, media)
    execution = TelegramExecutionCoordinator(authoring, interactions, ExecutionSettings(holo_mode="live"))
    build_telegram_runtime(telegram_bot_token, interactions, learning, execution).run()


def _discover_gateway_token(binary: str, sandbox_name: str) -> SecretStr:
    try:
        result = subprocess.run(
            (binary, sandbox_name, "gateway-token", "--quiet"),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Could not read the local NemoClaw Hermes gateway credential") from exc
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise RuntimeError("Could not read the local NemoClaw Hermes gateway credential")
    return SecretStr(token)


if __name__ == "__main__":
    main()
