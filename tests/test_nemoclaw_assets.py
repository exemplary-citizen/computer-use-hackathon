"""Regression checks for repository-owned NemoClaw deployment assets."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NEMOCLAW_README = REPOSITORY_ROOT / "nemoclaw" / "README.md"
HAI_POLICY = REPOSITORY_ROOT / "nemoclaw" / "policies" / "hai-agent-platform.yaml"


def test_runbook_uses_stock_managed_hermes_image() -> None:
    content = NEMOCLAW_README.read_text(encoding="utf-8")

    assert "google/gemini-3.5-flash" in content
    assert "nemohermes onboard" in content
    assert "onboard --from" not in content


def test_hai_policy_covers_regions_with_observed_hermes_runtime() -> None:
    content = HAI_POLICY.read_text(encoding="utf-8")

    assert "host: agp.eu.hcompany.ai" in content
    assert "host: agp.hcompany.ai" in content
    assert "path: /opt/hermes/.venv/bin/python }" in content
    assert "/opt/hermes/.venv/bin/python3" not in content
