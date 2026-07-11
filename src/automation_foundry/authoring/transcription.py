"""Gradium transcription adapter for pre-recorded demonstration audio."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

from automation_foundry.authoring.evidence import TranscriptSegment

_AUDIO_CHUNK_BYTES = 64 * 1024


class AudioTranscriber(Protocol):
    """Adapter boundary used by deterministic preprocessing tests."""

    async def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        """Return finalized timestamped transcript segments."""
        ...


class GradiumTranscriberConfig:
    """Configuration for the Gradium pre-recorded audio adapter."""

    def __init__(self, api_key: str, *, language: str = "en", model_name: str = "default"):
        """Store provider settings without reading environment files.

        Args:
            api_key: Gradium API key supplied by application settings.
            language: Expected source language.
            model_name: Gradium STT model name.
        """
        if not api_key:
            raise ValueError("Gradium API key is required")
        self.api_key = api_key
        self.language = language
        self.model_name = model_name

    def make(self) -> GradiumTranscriber:
        """Build a Gradium transcriber from this config."""
        return GradiumTranscriber(self)


class GradiumTranscriber:
    """Transcribe a finite WAV stream with Gradium's async Python client."""

    def __init__(self, config: GradiumTranscriberConfig):
        """Initialize the adapter.

        Args:
            config: Validated provider settings.
        """
        self.config = config

    async def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        """Transcribe a WAV file into finalized timestamped segments.

        Args:
            audio_path: Local 24 kHz, mono PCM WAV file.

        Returns:
            Ordered finalized transcript segments.
        """
        import gradium

        client = gradium.client.GradiumClient(api_key=self.config.api_key)
        setup = {
            "model_name": self.config.model_name,
            "input_format": "wav",
            "json_config": {"language": self.config.language, "delay_in_frames": 16},
        }
        stream = await client.stt_stream(setup, _audio_chunks(audio_path))
        segments: list[TranscriptSegment] = []
        async for segment in stream.iter_text():
            segments.append(_convert_segment(segment))
        return segments


async def _audio_chunks(audio_path: Path) -> AsyncIterator[bytes]:
    with audio_path.open("rb") as audio_file:
        while chunk := audio_file.read(_AUDIO_CHUNK_BYTES):
            yield chunk


def _convert_segment(segment: Any) -> TranscriptSegment:
    return TranscriptSegment(
        text=str(segment.text).strip(),
        start_seconds=float(segment.start_s),
        stop_seconds=float(segment.stop_s),
    )
