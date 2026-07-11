"""Approved fixture tools for normalizing a CRM lead update."""


def normalize_lead_update(payload: dict[str, object]) -> dict[str, str]:
    """Normalize the three inputs used by the fixture automation."""
    required = ("lead_name", "lifecycle_status", "owner_name")
    result: dict[str, str] = {}
    for field in required:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        result[field] = " ".join(value.split())
    return result
