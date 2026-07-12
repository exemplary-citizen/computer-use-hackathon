"""macOS application-bundle entry point for CRM A."""

import sys

from desktop_fixtures.app_entry import apply_data_root_argument
from desktop_fixtures.crm_a import main as run_app


def main() -> None:
    """Apply bundle launch arguments, then start CRM A."""
    apply_data_root_argument(sys.argv[1:])
    run_app()


if __name__ == "__main__":
    main()
