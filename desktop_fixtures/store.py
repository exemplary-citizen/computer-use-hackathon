"""Shared persistence core for both CRM fixtures.

One canonical record schema backs two different UIs. Field labels differ per
app (the zero-shot transfer point); the equivalence table lives in
``FIELD_LABELS``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AppKey = Literal["a", "b"]

STATUS_VALUES = ("Lead", "Qualified", "Active", "Churned")

FIELD_LABELS: dict[str, dict[AppKey, str]] = {
    "first_name": {"a": "First Name", "b": "Given name"},
    "last_name": {"a": "Last Name", "b": "Family name"},
    "company": {"a": "Company", "b": "Organisation"},
    "phone": {"a": "Phone", "b": "Contact No."},
    "email": {"a": "Email", "b": "E-mail address"},
    "status": {"a": "Status", "b": "Stage"},
    "owner": {"a": "Owner", "b": "Account manager"},
    "notes": {"a": "Notes", "b": "Remarks"},
}
"""Canonical field name -> visible label per app; deliberately different wording."""


class ContactRecord(BaseModel):
    """One CRM contact; the canonical schema shared by both fixtures."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^c[0-9]{3}$")
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    company: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=1, max_length=40)
    email: str = Field(min_length=1, max_length=120)
    status: str = Field(pattern=r"^(Lead|Qualified|Active|Churned)$")
    owner: str = Field(default="Unassigned", min_length=1, max_length=80)
    notes: str = Field(default="", max_length=2_000)

    @property
    def full_name(self) -> str:
        """Return the display name used for lookup in both apps."""
        return f"{self.first_name} {self.last_name}"


class CrmState(BaseModel):
    """Full persisted state of one CRM fixture."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    records: list[ContactRecord] = Field(default_factory=list)


def default_seed() -> CrmState:
    """Return the canonical deterministic seed shared by both applications."""
    return CrmState(
        records=[
            ContactRecord(
                id="c001",
                first_name="Maya",
                last_name="Okafor",
                company="Halcyon Logistics",
                phone="+1 415 555 0117",
                email="maya.okafor@halcyon.example",
                status="Active",
                owner="Ben Alvarez",
                notes="Renewal due in Q3.",
            ),
            ContactRecord(
                id="c006",
                first_name="Sarah",
                last_name="Chen",
                company="Bluepine Media",
                phone="+1 206 555 0173",
                email="sarah.chen@bluepine.example",
                status="Lead",
                owner="Unassigned",
                notes="Canonical eval-case lead (approved_bundle_v1).",
            ),
            ContactRecord(
                id="c002",
                first_name="Jonas",
                last_name="Berg",
                company="Fjordlight AS",
                phone="+47 21 55 01 82",
                email="jonas.berg@fjordlight.example",
                status="Lead",
                notes="Met at the Oslo expo.",
            ),
            ContactRecord(
                id="c003",
                first_name="Priya",
                last_name="Raman",
                company="Cobalt Analytics",
                phone="+1 646 555 0139",
                email="priya.raman@cobalt.example",
                status="Active",
                notes="",
            ),
            ContactRecord(
                id="c004",
                first_name="Diego",
                last_name="Fuentes",
                company="Verdant Foods",
                phone="+34 91 555 0166",
                email="diego.fuentes@verdant.example",
                status="Churned",
                notes="Churned 2026-02; open to win-back.",
            ),
            ContactRecord(
                id="c005",
                first_name="Hana",
                last_name="Sato",
                company="Kitsune Robotics",
                phone="+81 3 5555 0148",
                email="hana.sato@kitsune.example",
                status="Lead",
                notes="Asked for a pilot quote.",
            ),
        ]
    )


def state_path(app: AppKey, data_root: Path | None = None) -> Path:
    """Return the persisted-state file for one app.

    Args:
        app: Fixture key, ``"a"`` or ``"b"``.
        data_root: Override for the state directory; defaults to
            ``FOUNDRY_FIXTURE_DATA_ROOT`` or ``data/desktop_fixtures``.
    """
    root = data_root or Path(os.environ.get("FOUNDRY_FIXTURE_DATA_ROOT", "data/desktop_fixtures"))
    return root / f"crm_{app}.json"


def load_state(path: Path) -> CrmState:
    """Load persisted state, seeding the file first if it does not exist.

    Args:
        path: State file location.
    """
    if not path.is_file():
        state = default_seed()
        write_state_atomic(path, state)
        return state
    return CrmState.model_validate_json(path.read_text(encoding="utf-8"))


def write_state_atomic(path: Path, state: CrmState) -> None:
    """Write the full state file via temp-file-plus-rename; never partial.

    This is the ONLY writer for fixture state. Both apps call it exclusively
    from their explicit Save / Commit Changes action.

    Args:
        path: State file location.
        state: Complete state to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)
