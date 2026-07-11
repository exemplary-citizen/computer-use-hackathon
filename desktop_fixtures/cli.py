"""Test-only seed/reset/state-inspection CLI for the CRM fixtures.

Run with: ``uv run python -m desktop_fixtures.cli <command>``.

This CLI exists for the eval harness and demo operators. It must never be
referenced in Holo task text or exposed over HTTP (FR-X03). ``reset`` while
an app is running does not change what that running app displays — the
harness restarts the app for every trial.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import tyro

from desktop_fixtures.store import AppKey, default_seed, load_state, state_path, write_state_atomic


def reset(app: AppKey, data_root: Path | None = None) -> None:
    """Restore one CRM's persisted state to the canonical deterministic seed.

    Args:
        app: Fixture key, ``a`` or ``b``.
        data_root: Override for the state directory (tests use a temp dir).
    """
    path = state_path(app, data_root)
    write_state_atomic(path, default_seed())
    print(f"reset crm_{app} -> {path}")


def dump(app: AppKey, data_root: Path | None = None) -> None:
    """Print one CRM's persisted state as validated, stable JSON on stdout.

    Args:
        app: Fixture key, ``a`` or ``b``.
        data_root: Override for the state directory (tests use a temp dir).
    """
    path = state_path(app, data_root)
    if not path.is_file():
        print(f"error: no state file at {path}; run reset first", file=sys.stderr)
        raise SystemExit(1)
    state = load_state(path)
    print(json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True))


def where(app: AppKey, data_root: Path | None = None) -> None:
    """Print the state-file path for one CRM.

    Args:
        app: Fixture key, ``a`` or ``b``.
        data_root: Override for the state directory.
    """
    print(state_path(app, data_root))


def launch(app: AppKey) -> None:
    """Launch one CRM application against its default state file.

    Args:
        app: Fixture key, ``a`` or ``b``.
    """
    if app == "a":
        from desktop_fixtures.crm_a import main as run_app
    else:
        from desktop_fixtures.crm_b import main as run_app
    run_app()


def main() -> None:
    """Dispatch the fixture CLI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    tyro.extras.subcommand_cli_from_dict({"reset": reset, "dump": dump, "where": where, "launch": launch})


if __name__ == "__main__":
    main()
