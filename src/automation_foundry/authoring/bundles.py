"""Version, hash, approve, and publish generated automation bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from pydantic import BaseModel

from automation_foundry.authoring.generation import GeneratedBundleDraft
from automation_foundry.authoring.validation import BundleValidationReport, BundleValidator, REQUIRED_ARTIFACTS
from automation_foundry.contracts import (
    ApprovalDecision,
    ApprovalRecord,
    ArtifactReference,
    AutomationStatus,
    AutomationVersion,
    GeneratedTool,
    InvocationSource,
)
from automation_foundry.storage import ArtifactStore

_MEDIA_TYPES = {
    "SOP.md": "text/markdown",
    "SKILL.md": "text/markdown",
    "checks.json": "application/json",
    "inputs.schema.json": "application/schema+json",
    "procedure.json": "application/json",
    "tools.py": "text/x-python",
    "tool_manifest.json": "application/json",
    "eval_cases.json": "application/json",
    "review.json": "application/json",
    "test_tools.py": "text/x-python",
}


class BundleManagerConfig(BaseModel):
    """Safe publication location for approved Holo skills."""

    published_skill_root: Path = Path("data/holo-skills")

    def make(self, store: ArtifactStore) -> BundleManager:
        """Build a bundle manager for the configured artifact store."""
        return BundleManager(store, self.published_skill_root)


class BundleManager:
    """Own draft materialization and the only authoring approval path."""

    def __init__(self, store: ArtifactStore, published_skill_root: Path):
        self.store = store
        self.published_skill_root = published_skill_root
        self.validator = BundleValidator()

    def create_version(
        self, automation_id: UUID, draft: GeneratedBundleDraft
    ) -> tuple[AutomationVersion, BundleValidationReport]:
        """Materialize the next draft version and hash every runnable artifact."""
        manifest = self.store.get_manifest(automation_id)
        version_number = (manifest.current_version or 0) + 1
        version_root = self.store.create_version_directory(automation_id, version_number)
        code = _newline(draft.tools_code or '"""No generated data tools are required."""')
        code_hash = _sha256_bytes(code.encode("utf-8"))
        tools = [
            GeneratedTool(
                **tool.model_dump(),
                code_sha256=code_hash,
            )
            for tool in draft.tools
        ]
        files = {
            "SOP.md": _newline(draft.sop_markdown),
            "SKILL.md": _newline(draft.skill_markdown),
            "checks.json": _json_text(
                {
                    "preconditions": [
                        check.model_dump(mode="json") for check in draft.preconditions
                    ],
                    "completion_checks": [
                        check.model_dump(mode="json") for check in draft.completion_checks
                    ],
                }
            ),
            "inputs.schema.json": _json_text(draft.input_schema),
            "procedure.json": _json_text(
                {"steps": [step.model_dump(mode="json") for step in draft.steps]}
            ),
            "tools.py": code,
            "tool_manifest.json": _json_text({"tools": [tool.model_dump(mode="json") for tool in tools]}),
            "eval_cases.json": _json_text(draft.eval_cases),
            "review.json": _json_text(
                {"conflicts": [conflict.model_dump(mode="json") for conflict in draft.conflicts]}
            ),
            "test_tools.py": _newline(draft.tool_tests_code),
        }
        for name, content in files.items():
            _atomic_write_text(version_root / name, content)

        report = self.validator.validate(version_root)
        artifacts = self._artifact_references(automation_id, version_number)
        version = AutomationVersion(
            version=version_number,
            artifacts=artifacts,
            inputs=draft.inputs,
            steps=draft.steps,
            preconditions=draft.preconditions,
            completion_checks=draft.completion_checks,
            tools=tools,
            conflicts=draft.conflicts,
            validation_passed=report.valid,
        )
        self._save_version(version_root, version)
        manifest.current_version = version_number
        manifest.approved_version = None
        manifest.status = AutomationStatus.REVIEW_REQUIRED
        self.unpublish(manifest.slug)
        self.store.save_manifest(manifest)
        return version, report

    def validate_version(self, automation_id: UUID, version_number: int) -> BundleValidationReport:
        """Revalidate structure and ensure stored artifact hashes still match bytes."""
        version_root = self._version_root(automation_id, version_number)
        report = self.validator.validate(version_root)
        version = self._load_version(version_root)
        hash_issues = []
        actual = {
            reference.name: reference.sha256
            for reference in self._artifact_references(automation_id, version_number)
        }
        expected = {reference.name: reference.sha256 for reference in version.artifacts}
        if actual != expected:
            from automation_foundry.authoring.validation import ValidationIssue

            hash_issues.append(
                ValidationIssue(
                    "artifact_hash_mismatch",
                    "One or more runnable artifacts changed after version hashing",
                )
            )
        return BundleValidationReport(report.errors + tuple(hash_issues), report.warnings)

    def edit_artifact(
        self, automation_id: UUID, version_number: int, artifact_name: str, content: str
    ) -> tuple[AutomationVersion, BundleValidationReport]:
        """Edit a draft in place, or fork an approved version before editing it."""
        if artifact_name not in REQUIRED_ARTIFACTS:
            raise ValueError(f"Artifact is not editable: {artifact_name}")
        manifest = self.store.get_manifest(automation_id)
        source_root = self._version_root(automation_id, version_number)
        version = self._load_version(source_root)
        if version.approval is not None or manifest.approved_version == version_number:
            next_number = (manifest.current_version or version_number) + 1
            target_root = self.store.create_version_directory(automation_id, next_number)
            for name in REQUIRED_ARTIFACTS:
                shutil.copy2(source_root / name, target_root / name)
            version.version = next_number
            version.approval = None
            version.created_at = datetime.now(UTC)
            version_number = next_number
        else:
            target_root = source_root
        _atomic_write_text(target_root / artifact_name, _newline(content))
        version.artifacts = self._artifact_references(automation_id, version_number)
        report = self.validator.validate(target_root)
        version.validation_passed = report.valid
        version.approval = None
        self._save_version(target_root, version)
        manifest.current_version = version_number
        manifest.approved_version = None
        manifest.status = AutomationStatus.REVIEW_REQUIRED
        self.unpublish(manifest.slug)
        self.store.save_manifest(manifest)
        return version, report

    def approve(self, automation_id: UUID, version_number: int, *, actor: str) -> ApprovalRecord:
        """Bind approval to current bytes and atomically publish the approved skill."""
        actor = " ".join(actor.split())
        if not actor:
            raise ValueError("Approval actor is required")
        report = self.validate_version(automation_id, version_number)
        if not report.valid:
            raise ValueError("Bundle approval is blocked by validation failures")
        version_root = self._version_root(automation_id, version_number)
        version = self._load_version(version_root)
        if any(conflict.blocks_approval for conflict in version.conflicts):
            raise ValueError("Bundle approval is blocked by unresolved conflicts")
        payload_hash = _approval_payload_hash(version.artifacts)
        approval = ApprovalRecord(
            id=uuid4(),
            decision=ApprovalDecision.APPROVED,
            source=InvocationSource.DASHBOARD,
            payload_sha256=payload_hash,
            actor=actor,
        )
        version.approval = approval
        version.validation_passed = True
        _atomic_write_text(version_root / "approval.json", _json_text(approval.model_dump(mode="json")))
        self._save_version(version_root, version)

        manifest = self.store.get_manifest(automation_id)
        manifest.current_version = max(manifest.current_version or 0, version_number)
        manifest.approved_version = version_number
        manifest.status = AutomationStatus.APPROVED
        self._publish_skill(manifest.slug, version_root / "SKILL.md")
        self.store.save_manifest(manifest)
        return approval

    def reconcile(self, automation_id: UUID) -> bool:
        """Invalidate approval if startup checks find missing or changed bytes."""
        manifest = self.store.get_manifest(automation_id)
        if manifest.approved_version is None:
            return True
        try:
            report = self.validate_version(automation_id, manifest.approved_version)
            version = self._load_version(self._version_root(automation_id, manifest.approved_version))
            expected_payload = _approval_payload_hash(version.artifacts)
            if (
                report.valid
                and version.approval is not None
                and version.approval.payload_sha256 == expected_payload
            ):
                return True
        except (KeyError, OSError, ValueError):
            pass
        manifest.approved_version = None
        manifest.status = AutomationStatus.REVIEW_REQUIRED
        self.store.save_manifest(manifest)
        self.unpublish(manifest.slug)
        return False

    def unpublish(self, slug: str) -> None:
        """Remove a formerly approved skill when its runnable approval is invalidated."""
        published = self.published_skill_root / slug / "SKILL.md"
        published.unlink(missing_ok=True)

    def _artifact_references(self, automation_id: UUID, version_number: int) -> list[ArtifactReference]:
        version_root = self._version_root(automation_id, version_number)
        references = []
        for name in sorted(REQUIRED_ARTIFACTS):
            path = version_root / name
            references.append(
                ArtifactReference(
                    name=name,
                    relative_path=(Path("versions") / str(version_number) / name).as_posix(),
                    sha256=_sha256_bytes(path.read_bytes()),
                    media_type=_MEDIA_TYPES[name],
                )
            )
        return references

    def _version_root(self, automation_id: UUID, version_number: int) -> Path:
        root = self.store.automation_root(automation_id) / "versions" / str(version_number)
        if not root.is_dir():
            raise KeyError(f"Unknown automation version: {version_number}")
        return root

    def _load_version(self, root: Path) -> AutomationVersion:
        path = root / "version.json"
        if not path.is_file():
            raise ValueError("Version metadata is missing")
        return AutomationVersion.model_validate_json(path.read_text(encoding="utf-8"))

    def _save_version(self, root: Path, version: AutomationVersion) -> None:
        _atomic_write_text(root / "version.json", f"{version.model_dump_json(indent=2)}\n")

    def _publish_skill(self, slug: str, source: Path) -> None:
        destination = self.published_skill_root / slug / "SKILL.md"
        _atomic_write_text(destination, source.read_text(encoding="utf-8"))


def _approval_payload_hash(artifacts: list[ArtifactReference]) -> str:
    payload = [
        {"name": artifact.name, "path": artifact.relative_path, "sha256": artifact.sha256}
        for artifact in sorted(artifacts, key=lambda item: item.name)
    ]
    return _sha256_bytes(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _newline(content: str) -> str:
    return f"{content.rstrip()}\n" if content.strip() else ""


def _json_text(payload: object) -> str:
    return f"{json.dumps(payload, indent=2, sort_keys=True)}\n"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
