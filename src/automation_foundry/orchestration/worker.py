"""Trusted host capability-worker entrypoint."""

from automation_foundry.authoring.service import build_authoring_service
from automation_foundry.orchestration.capabilities import (
    CapabilityWorker,
    FoundryCapabilityProcessor,
    NemoClawCapabilityTransportConfig,
)
from automation_foundry.settings import AppSettings


def main() -> None:
    """Run the trusted host worker until interrupted."""
    settings = AppSettings()
    if settings.nemoclaw_sandbox_name is None:
        raise RuntimeError("FOUNDRY_NEMOCLAW_SANDBOX_NAME is required")
    authoring = build_authoring_service(settings)
    transport = NemoClawCapabilityTransportConfig(
        sandbox_name=settings.nemoclaw_sandbox_name,
        binary=settings.nemohermes_binary,
        command_timeout_seconds=settings.workspace_transfer_timeout_seconds,
    ).make()
    CapabilityWorker(transport, FoundryCapabilityProcessor(authoring)).run_forever()


if __name__ == "__main__":
    main()
