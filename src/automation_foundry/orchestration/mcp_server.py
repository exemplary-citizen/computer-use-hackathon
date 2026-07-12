"""Sandboxed MCP tools that relay read-only requests to the trusted host."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID, uuid4

from mcp.server.fastmcp import FastMCP

from automation_foundry.contracts import (
    CapabilityResponseStatus,
    FoundryCapability,
    FoundryCapabilityRequest,
    JSONValue,
)
from automation_foundry.orchestration.capabilities import CapabilityMailbox, CapabilityMailboxConfig

mcp = FastMCP("automation-foundry")


@mcp.tool()
def foundry_health() -> dict[str, JSONValue]:
    """Check whether the trusted Foundry authoring boundary is configured.

    Returns:
        Safe health fields from the trusted host worker.
    """
    return _request(FoundryCapability.HEALTH)


@mcp.tool()
def get_authoring_status(automation_id: str) -> dict[str, JSONValue]:
    """Get safe authoring progress for one stable automation identifier.

    Args:
        automation_id: UUID returned when an authoring job was accepted.

    Returns:
        Safe lifecycle, version, and progress fields from the trusted host.
    """
    try:
        parsed_id = UUID(automation_id)
    except ValueError as exc:
        raise ValueError("automation_id must be a UUID") from exc
    return _request(FoundryCapability.AUTHORING_STATUS, automation_id=parsed_id)


@lru_cache(maxsize=1)
def _mailbox() -> CapabilityMailbox:
    return CapabilityMailboxConfig().make()


def _request(capability: FoundryCapability, automation_id: UUID | None = None) -> dict[str, JSONValue]:
    requested_at = datetime.now(UTC)
    request = FoundryCapabilityRequest(
        id=uuid4(),
        capability=capability,
        automation_id=automation_id,
        requested_at=requested_at,
        expires_at=requested_at + timedelta(seconds=30),
    )
    response = _mailbox().submit(request)
    if response.status is not CapabilityResponseStatus.SUCCEEDED:
        raise RuntimeError(response.error_message or "Foundry capability request failed")
    return response.result


def main() -> None:
    """Run the sandboxed Foundry MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
