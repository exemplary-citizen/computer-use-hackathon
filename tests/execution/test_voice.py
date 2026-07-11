"""Voice-channel tests with a fake speech service: FR-V01..V05 mechanics."""

from __future__ import annotations

import json
import unittest

from automation_foundry.execution import voice as voice_module
from automation_foundry.execution.intent import RuleBasedIntentResolver, VoiceContext

from tests.execution.test_router import RouterTestBase

_UTTERANCE_RUN = "please set sarah chen to qualified and assign it to priya shah in crm a"


class FakeSpeech:
    """Deterministic speech service: transcripts come from the audio bytes."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def transcribe(self, audio: bytes, input_format: str) -> str:
        del input_format
        return audio.decode("utf-8")

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        self.spoken.append(text)
        return b"FAKEWAV", "wav"


class VoiceTestBase(RouterTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.fake_speech = FakeSpeech()
        self._orig_factory = voice_module.speech_factory
        voice_module.speech_factory = lambda settings: self.fake_speech
        self.addCleanup(self._restore_factory)

    def _restore_factory(self) -> None:
        voice_module.speech_factory = self._orig_factory

    def say(self, ws: object, utterance: str) -> list[dict[str, object]]:
        """Send one PTT utterance and collect frames until tts/ack settles."""
        ws.send_text(json.dumps({"type": "ptt_start", "format": "wav"}))  # type: ignore[attr-defined]
        ws.send_bytes(utterance.encode("utf-8"))  # type: ignore[attr-defined]
        ws.send_text(json.dumps({"type": "ptt_end"}))  # type: ignore[attr-defined]
        frames: list[dict[str, object]] = []
        while True:
            frame = json.loads(ws.receive_text())  # type: ignore[attr-defined]
            frames.append(frame)
            if frame["type"] in ("tts_audio", "voice_error", "voice_disabled"):
                return frames


class VoiceFlowTests(VoiceTestBase):
    def test_full_voice_flow_preview_confirm_approve(self) -> None:
        with self.client.websocket_connect("/api/execution/voice") as ws:
            frames = self.say(ws, _UTTERANCE_RUN)
            kinds = [frame["type"] for frame in frames]
            self.assertIn("final_transcript", kinds)
            self.assertIn("command_preview", kinds)
            preview = next(frame for frame in frames if frame["type"] == "command_preview")
            self.assertEqual(preview["target_app"], "crm_a")
            self.assertTrue(preview["requires_confirmation"])
            run_id = str(preview["run_id"])
            # Nothing starts until the explicit confirmation.
            self.assertEqual(
                self.client.get(f"/api/execution/runs/{run_id}").json()["state"], "awaiting_start_confirmation"
            )
            frames = self.say(ws, "confirm")
            self.assertIn("ack", [frame["type"] for frame in frames])
            self.wait_for_state(run_id, "awaiting_commit_approval")
            frames = self.say(ws, "approve")
            self.assertIn("ack", [frame["type"] for frame in frames])
            final = self.wait_for_state(run_id, "succeeded")
            self.assertEqual(final["state"], "succeeded")

    def test_voice_rejection_saves_nothing(self) -> None:
        with self.client.websocket_connect("/api/execution/voice") as ws:
            preview = next(frame for frame in self.say(ws, _UTTERANCE_RUN) if frame["type"] == "command_preview")
            run_id = str(preview["run_id"])
            self.say(ws, "yes go ahead")
            self.wait_for_state(run_id, "awaiting_commit_approval")
            frames = self.say(ws, "reject that")
            self.assertIn("ack", [frame["type"] for frame in frames])
            final = self.wait_for_state(run_id, "cancelled")
            result = final["result"]
            assert isinstance(result, dict)
            self.assertIn("nothing was saved", str(result["answer"]).lower())


class VoiceSafetyTests(VoiceTestBase):
    def test_audio_outside_ptt_lease_is_discarded(self) -> None:
        with self.client.websocket_connect("/api/execution/voice") as ws:
            ws.send_bytes(b"approve")  # no lease -> must be ignored
            ws.send_text(json.dumps({"type": "ptt_start", "format": "wav"}))
            ws.send_text(json.dumps({"type": "ptt_end"}))
            frames = []
            while True:
                frame = json.loads(ws.receive_text())
                frames.append(frame)
                if frame["type"] == "tts_audio":
                    break
            self.assertIn("clarification", [frame["type"] for frame in frames])
            self.assertEqual(self.client.get("/api/execution/runs").json()["runs"], [])

    def test_ambiguous_or_incomplete_command_never_starts_a_run(self) -> None:
        with self.client.websocket_connect("/api/execution/voice") as ws:
            frames = self.say(ws, "update the lead please")
            self.assertIn("clarification", [frame["type"] for frame in frames])
            self.assertEqual(self.client.get("/api/execution/runs").json()["runs"], [])

    def test_missing_target_app_asks_for_it(self) -> None:
        with self.client.websocket_connect("/api/execution/voice") as ws:
            frames = self.say(ws, "set sarah chen to qualified and assign it to priya shah")
            clarification = next(frame for frame in frames if frame["type"] == "clarification")
            missing = clarification["missing"]
            assert isinstance(missing, list)
            self.assertIn("target_app", missing)

    def test_cancellation_vocabulary_never_approves(self) -> None:
        with self.client.websocket_connect("/api/execution/voice") as ws:
            preview = next(frame for frame in self.say(ws, _UTTERANCE_RUN) if frame["type"] == "command_preview")
            run_id = str(preview["run_id"])
            self.say(ws, "confirm")
            self.wait_for_state(run_id, "awaiting_commit_approval")
            self.say(ws, "cancel")
            final = self.wait_for_state(run_id, "cancelled")
            self.assertEqual(final["state"], "cancelled")

    def test_mixed_approve_cancel_words_ask_for_exact_decision(self) -> None:
        resolver = RuleBasedIntentResolver()
        command = resolver.resolve("approve it... no wait, cancel", VoiceContext(awaiting_commit_approval=True))
        self.assertEqual(command.kind, "cancellation")
        command = resolver.resolve("approve and reject", VoiceContext(awaiting_commit_approval=True))
        self.assertEqual(command.kind, "clarification")

    def test_voice_disabled_degrades_cleanly(self) -> None:
        import os

        os.environ["FOUNDRY_VOICE_ENABLED"] = "false"
        self.addCleanup(lambda: os.environ.pop("FOUNDRY_VOICE_ENABLED", None))
        from fastapi.testclient import TestClient

        from automation_foundry.api import create_app

        with TestClient(create_app()) as client:
            with client.websocket_connect("/api/execution/voice") as ws:
                frame = json.loads(ws.receive_text())
                self.assertEqual(frame["type"], "voice_disabled")
            self.assertEqual(client.get("/api/execution/health").json()["status"], "ok")


class IntentResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = RuleBasedIntentResolver()
        self.context = VoiceContext(known_record_names=["Sarah Chen", "Maya Okafor"])

    def test_paraphrased_full_command_resolves(self) -> None:
        command = self.resolver.resolve(
            "move Sarah Chen to qualified and give it to Priya Shah in Meridian", self.context
        )
        self.assertEqual(command.kind, "run_command")
        self.assertEqual(command.target_app, "crm_b")
        self.assertEqual(
            command.inputs,
            {"lead_name": "Sarah Chen", "lifecycle_status": "Qualified", "owner_name": "Priya Shah"},
        )

    def test_missing_owner_is_clarification(self) -> None:
        command = self.resolver.resolve("set sarah chen to qualified in crm a", self.context)
        self.assertEqual(command.kind, "clarification")
        self.assertEqual(command.missing, ["owner_name"])

    def test_status_query(self) -> None:
        command = self.resolver.resolve("what's the status of the run", self.context)
        self.assertEqual(command.kind, "status_query")

    def test_approval_words_outside_approval_context_do_not_approve(self) -> None:
        command = self.resolver.resolve("approve", VoiceContext(awaiting_commit_approval=False))
        self.assertNotEqual(command.kind, "approval")


if __name__ == "__main__":
    unittest.main()
