"""Environment-only local application configuration."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Runtime paths and provider credentials; no dotenv file is loaded implicitly."""

    model_config = SettingsConfigDict(env_prefix="FOUNDRY_", extra="ignore")

    data_root: Path = Path("data/automations")
    database_path: Path = Path("data/automation_foundry.sqlite3")
    published_skill_root: Path = Path("data/holo-skills")
    workspace_mount: Path | None = None
    workspace_require_mount: bool = True
    nemoclaw_sandbox_name: str | None = None
    nemohermes_binary: str = "nemohermes"
    workspace_transfer_timeout_seconds: float = 120
    telegram_bot_token: SecretStr | None = None
    telegram_owner_id: int | None = None
    telegram_inbox_root: Path = Path("data/telegram-inbox")
    gradium_api_key: SecretStr | None = None
    hermes_api_key: SecretStr | None = None
    hermes_base_url: str = "http://127.0.0.1:8642/v1"
    hermes_model: str = "hermes"
