"""Durable automation metadata and versioned artifact storage."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from uuid import UUID, uuid4

from pydantic import BaseModel

from automation_foundry.contracts import AutomationManifest, AutomationStatus, EvidenceSourceType, SourceMetadata

_CHUNK_SIZE = 1024 * 1024
_SAFE_SLUG = re.compile(r"[^a-z0-9]+")


class ArtifactStoreConfig(BaseModel):
    """Filesystem and SQLite locations used by the local artifact store."""

    root: Path = Path("data/automations")
    """Root directory containing versioned automation folders."""
    database_path: Path = Path("data/automation_foundry.sqlite3")
    """SQLite index used by dashboard queries and job coordination."""

    def make(self) -> ArtifactStore:
        """Build and initialize an artifact store from this config."""
        return ArtifactStore(self)


class ArtifactStore:
    """Persist canonical automation files and a queryable SQLite index."""

    def __init__(self, config: ArtifactStoreConfig):
        """Initialize local directories and the SQLite schema.

        Args:
            config: Validated storage paths.
        """
        self.config = config
        self.config.root.mkdir(parents=True, exist_ok=True)
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def create_automation(self, name: str) -> AutomationManifest:
        """Create an empty draft automation and its canonical directory tree.

        Args:
            name: User-visible task name.

        Returns:
            Newly persisted draft manifest.
        """
        normalized_name = " ".join(name.split())
        if not normalized_name:
            raise ValueError("Automation name cannot be empty")
        automation_id = uuid4()
        slug = f"{_slugify(normalized_name)}-{str(automation_id)[:8]}"
        manifest = AutomationManifest(id=automation_id, slug=slug, name=normalized_name)
        automation_root = self.automation_root(automation_id)
        for relative in ("source", "evidence", "versions", "runs"):
            (automation_root / relative).mkdir(parents=True, exist_ok=False)
        self.save_manifest(manifest)
        return manifest

    def list_automations(self) -> list[AutomationManifest]:
        """Return all indexed automations ordered by most recent update."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT manifest_json FROM automations ORDER BY updated_at DESC, name ASC"
            ).fetchall()
        return [AutomationManifest.model_validate_json(row[0]) for row in rows]

    def get_manifest(self, automation_id: UUID) -> AutomationManifest:
        """Load one canonical manifest from disk.

        Args:
            automation_id: Stable automation identifier.

        Raises:
            KeyError: If the automation does not exist.
        """
        path = self.automation_root(automation_id) / "manifest.json"
        if not path.is_file():
            raise KeyError(f"Unknown automation: {automation_id}")
        return AutomationManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def save_manifest(self, manifest: AutomationManifest) -> None:
        """Atomically write a manifest and update its database index.

        Args:
            manifest: Validated manifest to persist.
        """
        manifest.updated_at = datetime.now(UTC)
        root = self.automation_root(manifest.id)
        root.mkdir(parents=True, exist_ok=True)
        manifest_json = manifest.model_dump_json(indent=2)
        _atomic_write_text(root / "manifest.json", f"{manifest_json}\n")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO automations (
                    id, slug, name, status, current_version, approved_version, updated_at, manifest_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    slug = excluded.slug,
                    name = excluded.name,
                    status = excluded.status,
                    current_version = excluded.current_version,
                    approved_version = excluded.approved_version,
                    updated_at = excluded.updated_at,
                    manifest_json = excluded.manifest_json
                """,
                (
                    str(manifest.id),
                    manifest.slug,
                    manifest.name,
                    manifest.status.value,
                    manifest.current_version,
                    manifest.approved_version,
                    manifest.updated_at.isoformat(),
                    manifest_json,
                ),
            )

    def store_source(
        self,
        automation_id: UUID,
        *,
        original_name: str,
        media_type: str,
        source_type: EvidenceSourceType,
        stream: BinaryIO,
        max_bytes: int,
    ) -> SourceMetadata:
        """Stream one validated source into its automation folder atomically.

        Args:
            automation_id: Owning automation.
            original_name: Sanitized client filename.
            media_type: Validated MIME type.
            source_type: Video or SOP source category.
            stream: Binary source positioned at its first byte.
            max_bytes: Hard upload size limit.

        Returns:
            Immutable source metadata including its content hash.

        Raises:
            ValueError: If the stream exceeds its size limit.
            KeyError: If the automation does not exist.
        """
        manifest = self.get_manifest(automation_id)
        source_id = uuid4()
        suffix = Path(original_name).suffix.lower()
        relative_path = Path("source") / f"{source_id}{suffix}"
        destination = self.automation_root(automation_id) / relative_path
        digest = hashlib.sha256()
        size_bytes = 0
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(dir=destination.parent, prefix=".upload-", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                while chunk := stream.read(_CHUNK_SIZE):
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise ValueError(f"Upload exceeds {max_bytes} byte limit")
                    digest.update(chunk)
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, destination)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

        metadata = SourceMetadata(
            id=source_id,
            source_type=source_type,
            original_name=original_name,
            relative_path=relative_path.as_posix(),
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
        )
        manifest.sources.append(metadata)
        manifest.status = AutomationStatus.PROCESSING
        try:
            self.save_manifest(manifest)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return metadata

    def create_version_directory(self, automation_id: UUID, version: int) -> Path:
        """Create a new immutable version directory.

        Args:
            automation_id: Owning automation.
            version: Positive version number.

        Returns:
            Newly created version directory.
        """
        if version < 1:
            raise ValueError("Version must be positive")
        self.get_manifest(automation_id)
        path = self.automation_root(automation_id) / "versions" / str(version)
        path.mkdir(parents=False, exist_ok=False)
        return path

    def automation_root(self, automation_id: UUID) -> Path:
        """Return the canonical directory for an automation ID."""
        return self.config.root / str(automation_id)

    def delete_automation(self, automation_id: UUID) -> None:
        """Delete one automation tree and its index row without following links."""
        self.get_manifest(automation_id)
        root = self.automation_root(automation_id)
        if root.is_symlink() or not root.resolve().is_relative_to(self.config.root.resolve()):
            raise RuntimeError("Refusing to delete an unsafe automation path")
        shutil.rmtree(root)
        with self._connect() as connection:
            connection.execute("DELETE FROM automations WHERE id = ?", (str(automation_id),))

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS automations (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_version INTEGER,
                    approved_version INTEGER,
                    updated_at TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.config.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _slugify(name: str) -> str:
    slug = _SAFE_SLUG.sub("-", name.casefold()).strip("-")
    return slug[:80] or "automation"


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
