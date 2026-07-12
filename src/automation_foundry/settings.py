"""Environment-only local application configuration."""

from pathlib import Path
from typing import Literal

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
    gradium_api_key: SecretStr | None = None
    hermes_api_key: SecretStr | None = None
    hermes_base_url: str = "http://127.0.0.1:8642/v1"
    hermes_model: str = "hermes"
    generation_provider: Literal["hermes_workspace", "openrouter_video"] = "hermes_workspace"
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-3.5-flash"
