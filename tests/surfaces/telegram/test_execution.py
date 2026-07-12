"""Telegram approval and generic two-turn execution walkthrough."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from automation_foundry.authoring.generation import GeneratedBundleDraft
from automation_foundry.authoring.service import build_authoring_service
from automation_foundry.contracts import (
    AutomationStatus,
    InputDefinition,
    InvocationSource,
)
from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.holo import TurnOutcome
from automation_foundry.execution.machine import RunCoordinator
from automation_foundry.settings import AppSettings
from automation_foundry.surfaces.telegram.execution import TelegramExecutionCoordinator
from automation_foundry.surfaces.telegram.interactions import (
    TelegramChatType,
    TelegramInboundUpdate,
    TelegramSurfaceConfig,
    TelegramUpdateKind,
)

from tests.authoring.test_bundle_validation_and_approval import valid_draft

OWNER_ID = 123456789


class GenericLiveStandIn:
    """Record the learned stage and commit prompts on one fake live session."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.fail_stage = False

    def start_session(self) -> str:
        return "telegram-live-session"

    def send_message(self, session_reference: str, message: str) -> TurnOutcome:
        assert session_reference == "telegram-live-session"
        self.messages.append(message)
        if len(self.messages) == 1:
            if self.fail_stage:
                return TurnOutcome(answer="No structured stage report.", steps_used=1)
            if "Atlas Returns Desk" in message:
                return TurnOutcome(
                    answer=(
                        '{"record":"RTN-1064","staged_fields":{"internal_decision_note":'
                        '"Customer sounds very frustrated. Initiate return ASAP."},'
                        '"visible_verification":"Atlas shows the staged internal note and Apply Resolution is ready."}'
                    ),
                    steps_used=3,
                )
            return TurnOutcome(
                answer=(
                    "TextEdit is open with a blank unsaved document. The greeting_text value "
                    "Hello from Telegram is staged for approval and has not been typed."
                ),
                steps_used=3,
            )
        if "APPROVED ATLAS COMMIT" in message:
            return TurnOutcome(
                answer="Clicked green Apply Resolution, observed red UPDATED!, and quit Atlas Returns Desk.",
                steps_used=2,
            )
        return TurnOutcome(answer="Typed exact approved greeting and verified it.", steps_used=2)

    def is_alive(self, session_reference: str) -> bool:
        return session_reference == "telegram-live-session"

    def cancel(self, session_reference: str) -> None:
        assert session_reference == "telegram-live-session"


class TestTelegramExecutionCoordinator:
    """Exercise review, automation approval, Start, and Commit buttons."""

    def setup_method(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        database_path = root / "foundry.sqlite3"
        self.authoring = build_authoring_service(
            AppSettings(
                data_root=root / "automations",
                database_path=database_path,
                published_skill_root=root / "skills",
            )
        )
        self.interactions = TelegramSurfaceConfig(
            database_path=database_path,
            owner_user_id=OWNER_ID,
        ).make()
        self.adapter = GenericLiveStandIn()
        execution_settings = ExecutionSettings(
            holo_mode="live",
            approval_timeout_seconds=5,
            heartbeat_seconds=0.01,
            runs_root=root / "runs",
            execution_database_path=root / "execution.sqlite3",
        )

        def coordinator_factory(settings: ExecutionSettings) -> RunCoordinator:
            return RunCoordinator(settings, adapter_factory=lambda _spec: self.adapter)

        self.execution = TelegramExecutionCoordinator(
            self.authoring,
            self.interactions,
            execution_settings,
            coordinator_factory,
        )
        manifest = self.authoring.store.create_automation("Test Automation")
        draft = self._draft()
        version, report = self.authoring.bundles.create_version(manifest.id, draft)
        assert report.valid
        self.automation_id = manifest.id
        self.version = version.version

    def teardown_method(self) -> None:
        self.temporary_directory.cleanup()

    @pytest.mark.asyncio
    async def test_review_approve_start_and_commit(self) -> None:
        review = self.execution.review(self._message(1, "/review Test Automation"), "Test Automation")
        assert review.buttons[0].label == "Approve automation"

        approved = await self._press(2, review.buttons[0].callback_data)

        assert "Approved" in approved.response.text
        manifest = self.authoring.store.get_manifest(self.automation_id)
        assert manifest.status is AutomationStatus.APPROVED
        version = self.authoring.load_version(self.automation_id, self.version)
        assert version.approval is not None
        assert version.approval.source is InvocationSource.TELEGRAM

        prompt = self.execution.begin_run(self._message(3, "/run Test Automation"), "Test Automation")
        assert "target_app" in prompt.text
        preview = await self.execution.collect_inputs(
            self._message(4, "target_app=TextEdit; greeting_text=Hello from Telegram"),
            "target_app=TextEdit; greeting_text=Hello from Telegram",
        )
        assert preview.buttons[0].label == "Start"

        started = await self._press(5, preview.buttons[0].callback_data)
        assert started.watch_run_id is not None
        staged = await self.execution.wait_for_run(started.watch_run_id)
        assert "without commit" in staged.text
        assert [button.label for button in staged.buttons] == ["Commit", "Reject"]
        assert len(self.adapter.messages) == 1

        committed = await self._press(6, staged.buttons[0].callback_data)
        assert committed.watch_run_id == started.watch_run_id
        assert committed.watch_terminal
        terminal = await self.execution.wait_for_terminal(committed.watch_run_id)

        assert "succeeded" in terminal.text
        assert len(self.adapter.messages) == 2
        assert "Open TextEdit" in self.adapter.messages[0]
        assert "Type the exact greeting_text" in self.adapter.messages[1]

    def test_atlas_review_describes_start_authorized_action(self) -> None:
        manifest = self.authoring.store.create_automation("Atlas Mail Return Triage")
        draft = valid_draft()
        draft.steps[-1].instruction = "Click green Apply Resolution once, verify UPDATED!, quit Atlas, and end."
        _, report = self.authoring.bundles.create_version(manifest.id, draft)
        assert report.valid

        review = self.execution.review(
            self._message(8, "/review Atlas Mail Return Triage"),
            "Atlas Mail Return Triage",
        )

        assert "Start-authorized action: Click green Apply Resolution" in review.text
        assert "Approval-gated action" not in review.text
        assert "stop for explicit approval" not in review.text.casefold()

    @pytest.mark.asyncio
    async def test_atlas_start_stages_and_commits_without_second_button(self) -> None:
        manifest = self.authoring.store.create_automation("Atlas Dynamic Automation")
        version, report = self.authoring.bundles.create_version(manifest.id, valid_draft())
        assert report.valid
        review = self.execution.review(
            self._message(10, "/review Atlas Dynamic Automation"),
            "Atlas Dynamic Automation",
        )
        await self._press(11, review.buttons[0].callback_data)
        self.execution.begin_run(
            self._message(12, "/run Atlas Dynamic Automation"),
            "Atlas Dynamic Automation",
        )
        preview = await self.execution.collect_inputs(
            self._message(13, "target_app=Atlas Returns Desk"),
            "target_app=Atlas Returns Desk",
        )

        assert "complete the Atlas change automatically" in preview.text
        started = await self._press(14, preview.buttons[0].callback_data)
        assert "complete the Atlas change" in started.response.text

        terminal = await self.execution.wait_for_run(started.watch_run_id)

        assert "succeeded" in terminal.text
        assert not terminal.silent
        assert terminal.buttons == ()
        assert len(self.adapter.messages) == 2
        assert "choose the newest matching RTN case" in self.adapter.messages[0]
        assert "Once the chosen email context is captured" in self.adapter.messages[0]
        assert "do not return to Mail" in self.adapter.messages[0]
        assert "Apply Resolution` exactly once" in self.adapter.messages[1]
        assert "red `UPDATED!` directly beneath the button" in self.adapter.messages[1]
        assert "quit Atlas Returns Desk" in self.adapter.messages[1]
        approved = self.authoring.load_version(manifest.id, version.version)
        assert approved.approval is not None

    @pytest.mark.asyncio
    async def test_atlas_terminal_failure_is_marked_silent(self) -> None:
        manifest = self.authoring.store.create_automation("Atlas Failure Automation")
        _, report = self.authoring.bundles.create_version(manifest.id, valid_draft())
        assert report.valid
        review = self.execution.review(
            self._message(20, "/review Atlas Failure Automation"),
            "Atlas Failure Automation",
        )
        await self._press(21, review.buttons[0].callback_data)
        self.execution.begin_run(
            self._message(22, "/run Atlas Failure Automation"),
            "Atlas Failure Automation",
        )
        preview = await self.execution.collect_inputs(
            self._message(23, "target_app=Atlas Returns Desk"),
            "target_app=Atlas Returns Desk",
        )
        self.adapter.fail_stage = True
        started = await self._press(24, preview.buttons[0].callback_data)

        terminal = await self.execution.wait_for_run(started.watch_run_id)

        assert "failed" in terminal.text
        assert terminal.silent

    async def _press(self, update_id: int, callback_data):
        update = self._callback(update_id, callback_data)
        grant = self.interactions.callbacks.peek(
            callback_data,
            telegram_user_id=OWNER_ID,
            telegram_chat_id=OWNER_ID,
        )
        return await self.execution.handle_callback(update, grant)

    def _draft(self) -> GeneratedBundleDraft:
        draft = valid_draft()
        draft.inputs = [
            InputDefinition(
                name="greeting_text",
                json_type="string",
                description="Exact text to type.",
                examples=["Hello from Telegram"],
            )
        ]
        draft.input_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"greeting_text": {"type": "string", "minLength": 1}},
            "required": ["greeting_text"],
        }
        draft.steps[0].instruction = "Open TextEdit and create a blank unsaved document."
        draft.steps[1].instruction = "Type the exact greeting_text value into the document."
        return draft

    def _message(self, update_id: int, text: str) -> TelegramInboundUpdate:
        return TelegramInboundUpdate(
            update_id=update_id,
            kind=TelegramUpdateKind.MESSAGE,
            user_id=OWNER_ID,
            chat_id=OWNER_ID,
            chat_type=TelegramChatType.PRIVATE,
            text=text,
        )

    def _callback(self, update_id: int, callback_data) -> TelegramInboundUpdate:
        return TelegramInboundUpdate(
            update_id=update_id,
            kind=TelegramUpdateKind.CALLBACK,
            user_id=OWNER_ID,
            chat_id=OWNER_ID,
            chat_type=TelegramChatType.PRIVATE,
            callback_data=callback_data,
        )
