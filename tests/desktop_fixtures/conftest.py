"""Test environment for the desktop fixtures.

Adds the repo root to ``sys.path`` (pytest's ``pythonpath`` covers ``src/``
only, and root config is Member 1's to change) and forces Qt offscreen so
widget tests run headless in CI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
