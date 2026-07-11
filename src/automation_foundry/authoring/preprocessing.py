"""Deterministic video and SOP preprocessing before LLM generation."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from pydantic import BaseModel, Field

from automation_foundry.authoring.evidence import EvidencePackage, SopEvidence, SopPage, VideoEvidence
from automation_foundry.authoring.transcription import AudioTranscriber
from automation_foundry.contracts import EvidenceSourceType, SourceMetadata
from automation_foundry.storage import ArtifactStore

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class PreprocessingConfig(BaseModel):
    """Limits and binaries used by deterministic evidence preprocessing."""

    ffmpeg_binary: str = "ffmpeg"
    """FFmpeg executable or absolute path."""
    ffprobe_binary: str = "ffprobe"
    """FFprobe executable or absolute path."""
    max_video_seconds: float = Field(default=600, gt=0)
    """Maximum accepted demonstration duration."""
    max_sop_pages: int = Field(default=20, ge=1)
    """Maximum normalized SOP pages."""
    frame_interval_seconds: int = Field(default=1, ge=1, le=10)
    """Periodic frame sample interval before later visual deduplication."""

    def make(
        self,
        store: ArtifactStore,
        *,
        transcriber: AudioTranscriber | None = None,
        command_runner: CommandRunner | None = None,
    ) -> EvidencePreprocessor:
        """Build a preprocessor with provider and command adapters."""
        return EvidencePreprocessor(
            self,
            store,
            transcriber=transcriber,
            command_runner=command_runner,
        )


class EvidencePreprocessor:
    """Turn stored sources into a bounded, versioned evidence package."""

    def __init__(
        self,
        config: PreprocessingConfig,
        store: ArtifactStore,
        *,
        transcriber: AudioTranscriber | None = None,
        command_runner: CommandRunner | None = None,
    ):
        """Initialize deterministic preprocessing dependencies.

        Args:
            config: Validated duration, page, and frame settings.
            store: Artifact store containing canonical uploads.
            transcriber: Optional timestamped audio transcription adapter.
            command_runner: Optional FFmpeg command adapter for tests.
        """
        self.config = config
        self.store = store
        self.transcriber = transcriber
        self.command_runner = command_runner or _run_command

    async def preprocess(self, automation_id: UUID) -> EvidencePackage:
        """Preprocess every uploaded source and persist evidence JSON.

        Args:
            automation_id: Automation whose sources should be processed.

        Returns:
            Persisted evidence package.
        """
        manifest = self.store.get_manifest(automation_id)
        package = EvidencePackage(automation_id=automation_id)
        for source in manifest.sources:
            if source.source_type is EvidenceSourceType.SOP:
                sop = self._preprocess_sop(automation_id, source)
                source.page_count = sop.page_count
                package.sop_documents.append(sop)
            elif source.source_type is EvidenceSourceType.VIDEO:
                video = await self._preprocess_video(automation_id, source)
                source.duration_seconds = video.duration_seconds
                package.videos.append(video)
        if not package.sop_documents and not package.videos:
            raise ValueError("Automation has no processable sources")
        self._write_package(package)
        self.store.save_manifest(manifest)
        return package

    def _preprocess_sop(self, automation_id: UUID, source: SourceMetadata) -> SopEvidence:
        source_path = self.store.automation_root(automation_id) / source.relative_path
        if source_path.suffix.casefold() == ".pdf":
            pages = _extract_pdf_pages(source_path)
        else:
            pages = _extract_text_pages(source_path)
        if len(pages) > self.config.max_sop_pages:
            raise ValueError(f"SOP exceeds {self.config.max_sop_pages} page limit")
        return SopEvidence(
            source_id=source.id,
            page_count=len(pages),
            pages=[SopPage(page=index, text=text) for index, text in enumerate(pages, start=1)],
        )

    async def _preprocess_video(self, automation_id: UUID, source: SourceMetadata) -> VideoEvidence:
        automation_root = self.store.automation_root(automation_id)
        source_path = automation_root / source.relative_path
        evidence_root = automation_root / "evidence"
        frames_root = evidence_root / "frames" / str(source.id)
        audio_root = evidence_root / "audio"
        frames_root.mkdir(parents=True, exist_ok=True)
        audio_root.mkdir(parents=True, exist_ok=True)

        duration = self._probe_duration(source_path)
        if duration <= 0 or duration > self.config.max_video_seconds:
            raise ValueError(f"Video duration {duration:.3f}s is outside the supported range")

        frame_pattern = frames_root / "frame-%06d.jpg"
        self.command_runner(
            [
                self.config.ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_path),
                "-vf",
                f"fps=1/{self.config.frame_interval_seconds},scale='min(1280,iw)':-2",
                "-q:v",
                "3",
                str(frame_pattern),
            ]
        )
        frame_paths = sorted(frames_root.glob("frame-*.jpg"))
        if not frame_paths:
            raise RuntimeError("FFmpeg produced no video frames")

        audio_path = audio_root / f"{source.id}.wav"
        self.command_runner(
            [
                self.config.ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_path),
                "-map",
                "0:a:0?",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(audio_path),
            ]
        )
        transcript = []
        persisted_audio_path: str | None = None
        if audio_path.is_file() and audio_path.stat().st_size > 44:
            persisted_audio_path = audio_path.relative_to(automation_root).as_posix()
            if self.transcriber is None:
                raise RuntimeError("Gradium transcription is not configured for narrated video")
            transcript = await self.transcriber.transcribe(audio_path)
        else:
            audio_path.unlink(missing_ok=True)

        return VideoEvidence(
            source_id=source.id,
            duration_seconds=duration,
            frame_interval_seconds=self.config.frame_interval_seconds,
            frame_paths=[path.relative_to(automation_root).as_posix() for path in frame_paths],
            audio_path=persisted_audio_path,
            transcript=transcript,
        )

    def _probe_duration(self, source_path: Path) -> float:
        result = self.command_runner(
            [
                self.config.ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(source_path),
            ]
        )
        try:
            payload = json.loads(result.stdout)
            return float(payload["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("FFprobe returned an invalid duration payload") from exc

    def _write_package(self, package: EvidencePackage) -> None:
        destination = self.store.automation_root(package.automation_id) / "evidence" / "evidence.json"
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent, prefix=".evidence-", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(package.model_dump_json(indent=2))
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, destination)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise


def _extract_text_pages(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    pages = [page.strip() for page in text.replace("\r\n", "\n").replace("\r", "\n").split("\f")]
    return pages or [""]


def _extract_pdf_pages(path: Path) -> list[str]:
    import pymupdf

    with pymupdf.open(path) as document:
        return [page.get_text().strip() for page in document]


def _run_command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(arguments, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable is unavailable: {arguments[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or "no diagnostic output"
        raise RuntimeError(f"Command failed: {arguments[0]}: {detail}") from exc
