"""Hash-verified loading of the approved bundle consumed by execution.

Only a bundle whose artifact bytes, inline skill text, and approval payload
hash all match is allowed to run (FR-E01). Verification happens at prepare
time AND again immediately before a Holo session is created.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from automation_foundry.contracts import ApprovedBundle

from automation_foundry.execution.errors import fault

RECORD_SELECTOR_INPUT = "lead_name"
"""Input naming the record to operate on (not itself a field change)."""

INPUT_FIELD_MAP: dict[str, str] = {
    "first_name": "first_name",
    "last_name": "last_name",
    "company": "company",
    "phone": "phone",
    "email": "email",
    "lifecycle_status": "status",
    "owner_name": "owner",
    "notes": "notes",
    "new_first_name": "first_name",
    "new_last_name": "last_name",
    "new_company": "company",
    "new_phone": "phone",
    "new_email": "email",
    "new_lifecycle_status": "status",
    "new_owner_name": "owner",
    "new_notes": "notes",
}
"""Bundle input name -> canonical fixture field, for the staged-change diff."""

ExecutionOperation = Literal["update", "create"]


@dataclass(frozen=True)
class LoadedBundle:
    """A verified bundle plus the filesystem root its artifacts live under."""

    bundle: ApprovedBundle
    root: Path


def load_verified_bundle(bundle_path: Path) -> LoadedBundle:
    """Load and hash-verify the approved bundle; refuse on any mismatch.

    Args:
        bundle_path: Path to ``approved_bundle.json``; sibling ``versions/``
            directory holds the artifact files.

    Raises:
        ExecutionFault: ``hash_mismatch`` when any artifact's bytes differ
            from the recorded hashes, or the approval hash does not bind them.
    """
    if not bundle_path.is_file():
        raise fault("hash_mismatch", f"bundle file not found: {bundle_path}")
    bundle = ApprovedBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
    root = bundle_path.parent

    for artifact in bundle.version.artifacts:
        artifact_path = root / artifact.relative_path
        if not artifact_path.is_file():
            raise fault("hash_mismatch", f"missing artifact file: {artifact.relative_path}")
        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if digest != artifact.sha256:
            raise fault("hash_mismatch", f"artifact '{artifact.name}' bytes differ from approved hash")

    skill_artifacts = [artifact for artifact in bundle.version.artifacts if artifact.name == "skill"]
    if skill_artifacts:
        skill_path = root / skill_artifacts[0].relative_path
        if skill_path.read_text(encoding="utf-8") != bundle.skill_markdown:
            raise fault("hash_mismatch", "inline skill_markdown differs from the approved SKILL.md artifact")

    approval = bundle.version.approval
    if approval is None or approval.payload_sha256 != _approval_payload_hash(bundle):
        raise fault("hash_mismatch", "approval payload hash does not bind the artifact set")

    return LoadedBundle(bundle=bundle, root=root)


def validate_inputs(bundle: ApprovedBundle, inputs: dict[str, object]) -> dict[str, str]:
    """Validate run inputs against the bundle's input schema.

    Args:
        bundle: Verified bundle whose ``input_schema`` governs run inputs.
        inputs: Raw inputs from the dashboard or voice surface.

    Returns:
        Field-level error messages keyed by input name; empty when valid.
    """
    errors: dict[str, str] = {}
    schema = bundle.input_schema
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for name in required:
        value = inputs.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[name] = "This field is required."
    for name, value in inputs.items():
        if name not in properties:
            errors[name] = "Unknown input."
        elif properties[name].get("type") == "string" and not isinstance(value, str):
            errors[name] = "Must be a string."
    return errors


def execution_operation(bundle: ApprovedBundle) -> ExecutionOperation:
    """Resolve the supported desktop operation from canonical bundle inputs.

    Args:
        bundle: Verified approved bundle.

    Returns:
        ``update`` for record-selector workflows or ``create`` for new-contact workflows.

    Raises:
        ValueError: When the generated input contract does not identify a supported operation.
    """
    properties = bundle.input_schema.get("properties", {})
    if RECORD_SELECTOR_INPUT in properties:
        return "update"
    if "first_name" in properties and "last_name" in properties:
        return "create"
    raise ValueError("Approved bundle must use lead_name for updates or first_name and last_name for contact creation")


def _approval_payload_hash(bundle: ApprovedBundle) -> str:
    payload = [
        {"name": artifact.name, "path": artifact.relative_path, "sha256": artifact.sha256}
        for artifact in sorted(bundle.version.artifacts, key=lambda artifact: artifact.name)
    ]
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
