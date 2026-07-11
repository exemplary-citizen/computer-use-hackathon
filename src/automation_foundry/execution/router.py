"""Registration boundary for Member 2's execution routes."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/execution", tags=["execution"])


@router.get("/health")
def execution_health() -> dict[str, str]:
    """Return the execution subsystem health marker."""
    return {"status": "ok", "subsystem": "execution"}
