"""Push-to-talk voice channel: browser mic -> backend -> Gradium -> commands.

Transport (T3 decision): audio is proxied through the backend — credentials
never reach the browser (FR-V01). The WebSocket carries JSON control frames
and binary audio frames:

    client -> {"type": "ptt_start", "format": "wav"}
    client -> <binary audio chunks>            (only counted inside the lease)
    client -> {"type": "ptt_end"}
    client -> {"type": "confirm_command"}      (button fallback for spoken confirm)
    server -> {"type": "final_transcript" | "command_preview" | "clarification"
               | "ack" | "status" | "voice_error" | "voice_disabled"
               | "tts_audio", ...}

Safety mechanics: audio outside a push-to-talk lease is discarded server-side;
only final transcripts are resolved; starting a run requires an explicit
confirmation (spoken "confirm"/"yes" or the confirm_command frame) after the
preview; approval/rejection vocabulary only acts when a run is actually
awaiting commit approval. Voice failures degrade to the dashboard — they
never block or break the run itself.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from desktop_fixtures.store import load_state, state_path

from automation_foundry.contracts import InvocationSource
from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.errors import ExecutionFault, fault
from automation_foundry.execution.intent import (
    IntentResolver,
    LlmIntentResolver,
    ResolvedCommand,
    RuleBasedIntentResolver,
    VoiceContext,
)
from automation_foundry.execution.machine import InputValidationError, RunCoordinator

voice_router = APIRouter()

_CONFIRM_START_WORDS = ("confirm", "yes", "start it", "go ahead")
_DISCARD_WORDS = ("cancel", "no", "never mind", "stop")


class SpeechService(Protocol):
    """Speech provider boundary (real Gradium or a test fake)."""

    async def transcribe(self, audio: bytes, input_format: str) -> str:
        """Return the final transcript for one utterance."""
        ...

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return (audio_bytes, format) for a spoken response."""
        ...


class GradiumSpeech:
    """Real Gradium STT/TTS over the backend-held API key."""

    def __init__(self, api_key: str):
        """Store the backend-only credential.

        Args:
            api_key: Gradium API key (never sent to the browser).
        """
        self._api_key = api_key

    async def transcribe(self, audio: bytes, input_format: str) -> str:
        """Transcribe one buffered utterance.

        Args:
            audio: Raw audio bytes captured during the push-to-talk lease.
            input_format: Container format announced by the client.
        """
        try:
            from gradium.client import GradiumClient
            from gradium.speech import STTSetup, stt

            client = GradiumClient(api_key=self._api_key)
            result = await stt(client, STTSetup(input_format=input_format), audio)
            return str(result.text)
        except Exception as error:
            raise fault("gradium_unavailable", f"stt failed: {type(error).__name__}") from error

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Synthesize a spoken response.

        Args:
            text: Sentence to speak.
        """
        try:
            from gradium.client import GradiumClient
            from gradium.speech import TTSSetup, tts

            client = GradiumClient(api_key=self._api_key)
            result = await tts(client, TTSSetup(output_format="wav"), text)
            audio = result.raw_data if isinstance(result.raw_data, bytes) else bytes(result.raw_data)
            return audio, "wav"
        except Exception as error:
            raise fault("gradium_unavailable", f"tts failed: {type(error).__name__}") from error


def default_speech_factory(settings: ExecutionSettings) -> SpeechService:
    """Build the speech service from configuration.

    Args:
        settings: Execution settings carrying the Gradium credential.
    """
    key = settings.gradium_api_key.get_secret_value() if settings.gradium_api_key else ""
    return GradiumSpeech(key)


speech_factory: Callable[[ExecutionSettings], SpeechService] = default_speech_factory
"""Test seam: tests replace this with a fake speech factory."""


def default_resolver_factory(settings: ExecutionSettings) -> IntentResolver:
    """Build the intent resolver per ``FOUNDRY_INTENT_MODE``.

    Args:
        settings: Execution settings.
    """
    if settings.intent_mode == "llm":
        return LlmIntentResolver(settings.hermes_base_url, None, settings.hermes_model)
    return RuleBasedIntentResolver()


resolver_factory: Callable[[ExecutionSettings], IntentResolver] = default_resolver_factory
"""Test seam: tests replace this with a scripted resolver."""


@voice_router.websocket("/voice")
async def voice_channel(websocket: WebSocket) -> None:
    """Run one push-to-talk voice session over a WebSocket."""
    from automation_foundry.execution.router import _origin_ok, _websocket_state

    if not _origin_ok(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    state = await _websocket_state(websocket)
    settings = state.coordinator.settings
    await websocket.accept()
    if not settings.voice_enabled:
        await _send(
            websocket,
            {
                "type": "voice_disabled",
                "message": "Voice is disabled (FOUNDRY_VOICE_ENABLED=false). The dashboard flow is unaffected.",
            },
        )
        await websocket.close()
        return

    session = _VoiceSession(websocket, state.coordinator, settings)
    try:
        await session.run()
    except WebSocketDisconnect:
        pass


class _VoiceSession:
    """Connection state for one voice client."""

    def __init__(self, websocket: WebSocket, coordinator: RunCoordinator, settings: ExecutionSettings):
        self._websocket = websocket
        self._coordinator = coordinator
        self._settings = settings
        self._speech = speech_factory(settings)
        self._resolver = resolver_factory(settings)
        self._lease_open = False
        self._audio_format = "wav"
        self._buffer = bytearray()
        self._pending_preview_run: UUID | None = None

    async def run(self) -> None:
        while True:
            message = await self._websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            if (data := message.get("bytes")) is not None:
                if self._lease_open:
                    self._buffer.extend(data)
                continue  # audio outside the push-to-talk lease is discarded
            text = message.get("text")
            if not text:
                continue
            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                continue
            await self._handle_frame(frame)

    async def _handle_frame(self, frame: dict[str, object]) -> None:
        kind = frame.get("type")
        if kind == "ptt_start":
            self._lease_open = True
            self._buffer.clear()
            self._audio_format = str(frame.get("format", "wav"))
        elif kind == "ptt_end":
            self._lease_open = False
            await self._process_utterance(bytes(self._buffer))
            self._buffer.clear()
        elif kind == "confirm_command":
            await self._confirm_pending()

    async def _process_utterance(self, audio: bytes) -> None:
        if not audio:
            await self._send_clarification("I didn't catch any audio — hold the button while you speak.")
            return
        try:
            transcript = await self._speech.transcribe(audio, self._audio_format)
        except ExecutionFault as error:
            await _send(self._websocket, {"type": "voice_error", **error.payload()})
            return
        await _send(self._websocket, {"type": "final_transcript", "text": transcript})
        if not transcript.strip():
            await self._send_clarification("I couldn't hear that — try again closer to the microphone.")
            return

        text = transcript.lower()
        if self._pending_preview_run is not None:
            if any(word in text for word in _CONFIRM_START_WORDS) and not any(word in text for word in _DISCARD_WORDS):
                await self._confirm_pending()
                return
            if any(word in text for word in _DISCARD_WORDS):
                await self._coordinator.cancel(self._pending_preview_run, "Voice command discarded before start.")
                self._pending_preview_run = None
                await self._ack("Discarded. Nothing was started.")
                return

        command = self._resolver.resolve(transcript, self._context())
        await self._dispatch(command)

    async def _dispatch(self, command: ResolvedCommand) -> None:
        if command.kind == "clarification":
            await self._send_clarification(command.echo, command.missing)
        elif command.kind == "status_query":
            await self._send_status()
        elif command.kind == "cancellation":
            await self._cancel_active(command.echo)
        elif command.kind == "approval":
            await self._approve()
        elif command.kind == "rejection":
            await self._reject()
        elif command.kind == "run_command":
            await self._prepare_run(command)

    async def _prepare_run(self, command: ResolvedCommand) -> None:
        try:
            preview = await self._coordinator.prepare(
                command.target_app or "",
                dict(command.inputs),
                InvocationSource.VOICE,
            )
        except InputValidationError as error:
            details = "; ".join(f"{name}: {message}" for name, message in sorted(error.field_errors.items()))
            await self._send_clarification(f"I can't start that yet — {details}", sorted(error.field_errors))
            return
        except ExecutionFault as error:
            await _send(self._websocket, {"type": "voice_error", **error.payload()})
            return
        self._pending_preview_run = preview.request.id
        await _send(
            self._websocket,
            {
                "type": "command_preview",
                "run_id": str(preview.request.id),
                "automation_name": preview.automation_name,
                "target_app": preview.request.target_app,
                "inputs": preview.normalized_inputs,
                "requires_confirmation": True,
                "echo": command.echo,
            },
        )
        await self._speak(command.echo)

    async def _confirm_pending(self) -> None:
        if self._pending_preview_run is None:
            await self._send_clarification("There is no previewed command to confirm yet.")
            return
        run_id = self._pending_preview_run
        self._pending_preview_run = None
        try:
            await self._coordinator.confirm_start(run_id)
        except ExecutionFault as error:
            await _send(self._websocket, {"type": "voice_error", **error.payload()})
            return
        await self._ack(f"Started run {str(run_id)[:8]}. I'll read back the staged change before anything is saved.")

    async def _approve(self) -> None:
        run_id = self._awaiting_run()
        if run_id is None:
            await self._send_clarification("There is no staged change awaiting approval.")
            return
        staged = self._coordinator.staged_change(run_id)
        if staged is None:
            await self._send_clarification("The staged change isn't ready yet.")
            return
        try:
            await self._coordinator.approve_commit(run_id, staged.payload_sha256, InvocationSource.VOICE, "voice")
        except ExecutionFault as error:
            await _send(self._websocket, {"type": "voice_error", **error.payload()})
            return
        await self._ack("Approved. Committing through the same session.")

    async def _reject(self) -> None:
        run_id = self._awaiting_run()
        if run_id is None:
            await self._send_clarification("There is no staged change awaiting approval.")
            return
        try:
            await self._coordinator.reject_commit(run_id, InvocationSource.VOICE, "voice")
        except ExecutionFault as error:
            await _send(self._websocket, {"type": "voice_error", **error.payload()})
            return
        await self._ack("Rejected. Nothing was saved.")

    async def _cancel_active(self, echo: str) -> None:
        for summary in self._coordinator.list_runs():
            if str(summary["state"]) not in ("succeeded", "failed", "cancelled"):
                await self._coordinator.cancel(UUID(str(summary["id"])), "Cancelled by voice.")
        self._pending_preview_run = None
        await self._ack(echo or "Cancelled.")

    async def _send_status(self) -> None:
        runs = self._coordinator.list_runs()
        if not runs:
            await self._ack("No runs yet.")
            return
        latest = runs[0]
        await _send(self._websocket, {"type": "status", "run_id": str(latest["id"]), "state": str(latest["state"])})
        await self._speak(f"The latest run is {str(latest['state']).replace('_', ' ')}.")

    async def _send_clarification(self, prompt: str, missing: list[str] | None = None) -> None:
        await _send(self._websocket, {"type": "clarification", "prompt": prompt, "missing": missing or []})
        await self._speak(prompt)

    async def _ack(self, message: str) -> None:
        await _send(self._websocket, {"type": "ack", "message": message})
        await self._speak(message)

    async def _speak(self, text: str) -> None:
        try:
            audio, audio_format = await self._speech.synthesize(text)
        except ExecutionFault as error:
            # TTS failure is never fatal (and never re-labels a run): text was already sent.
            await _send(self._websocket, {"type": "voice_error", **error.payload()})
            return
        await _send(
            self._websocket,
            {"type": "tts_audio", "b64": base64.b64encode(audio).decode("ascii"), "format": audio_format},
        )

    def _awaiting_run(self) -> UUID | None:
        for summary in self._coordinator.list_runs():
            if str(summary["state"]) == "awaiting_commit_approval":
                return UUID(str(summary["id"]))
        return None

    def _context(self) -> VoiceContext:
        names: set[str] = set()
        for app in ("a", "b"):
            path = state_path(app, self._settings.fixture_data_root)
            if path.is_file():
                names.update(record.full_name for record in load_state(path).records)
        return VoiceContext(
            awaiting_commit_approval=self._awaiting_run() is not None,
            known_record_names=sorted(names),
        )


async def _send(websocket: WebSocket, payload: dict[str, object]) -> None:
    await websocket.send_text(json.dumps(payload))
