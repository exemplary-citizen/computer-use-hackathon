"""Execution-lane configuration: every knob named, env-overridable, defaulted.

All variables use the shared ``FOUNDRY_`` prefix (matching Member 1's
``AppSettings``). RunRequest budgets may lower — never exceed — the hard
caps enforced here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

HoloMode = Literal["mock", "live"]


class ExecutionSettings(BaseSettings):
    """Execution runtime knobs; see the integration note for the env table."""

    model_config = SettingsConfigDict(env_prefix="FOUNDRY_", extra="ignore")

    holo_mode: HoloMode = "mock"
    """`mock` drives the scripted fake adapter; `live` requires HoloDesktop."""
    holo_mock_script: str = "stage-ok"
    """Behavior of the fake adapter (see execution.holo.FAKE_SCRIPTS)."""
    approval_timeout_seconds: float = 120.0
    """Commit-approval window; expiry cancels the run without saving."""
    heartbeat_seconds: float = 10.0
    """Max silent interval on the event stream while a Holo turn is in flight."""
    hard_max_steps: int = 60
    """Ceiling a RunRequest.max_steps may never exceed."""
    hard_max_time_seconds: int = 600
    """Ceiling a RunRequest.max_time_seconds may never exceed."""
    voice_enabled: bool = True
    """Off hides voice UI and skips Gradium init; dashboard flow unaffected."""
    bundle_path: Path = Path("tests/fixtures/approved_bundle_v1/approved_bundle.json")
    """Approved bundle consumed by the execution lane (fixed fixture until final integration)."""
    runs_root: Path = Path("data/execution/runs")
    """Per-run directories holding request/events/staged/approval/result records."""
    execution_database_path: Path = Path("data/execution/execution.sqlite3")
    """SQLite index for run rows and the single-active-run guard."""
    fixture_data_root: Path | None = None
    """Override for desktop-fixture state files (tests point this at a temp dir)."""

    def clamp_budgets(self, max_steps: int, max_time_seconds: int) -> tuple[int, int]:
        """Clamp requested budgets to the configured hard caps.

        Args:
            max_steps: Requested step budget.
            max_time_seconds: Requested wall-clock budget.

        Returns:
            The effective (max_steps, max_time_seconds) pair.
        """
        return min(max_steps, self.hard_max_steps), min(max_time_seconds, self.hard_max_time_seconds)
