"""Shared argument handling for compiled CRM application bundles."""

import os
from collections.abc import Sequence


def apply_data_root_argument(arguments: Sequence[str]) -> None:
    """Apply the optional LaunchServices data-root argument.

    Args:
        arguments: Application arguments excluding the executable name.

    Raises:
        SystemExit: When ``--data-root`` is present without a value.
    """
    if "--data-root" not in arguments:
        pass
    else:
        index = arguments.index("--data-root")
        if index + 1 >= len(arguments) or not arguments[index + 1].strip():
            raise SystemExit("--data-root requires a path")
        os.environ["FOUNDRY_FIXTURE_DATA_ROOT"] = arguments[index + 1]
    if "--overlay-path" in arguments:
        index = arguments.index("--overlay-path")
        if index + 1 >= len(arguments) or not arguments[index + 1].strip():
            raise SystemExit("--overlay-path requires a path")
        os.environ["FOUNDRY_HOLO_OVERLAY_PATH"] = arguments[index + 1]
