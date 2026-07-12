"""Credential-gated live verification of the NemoClaw/Hermes authoring boundary."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from automation_foundry.authoring.evidence import EvidencePackage, SopEvidence, SopPage
from automation_foundry.authoring.generation import BundleGenerator, HermesClientConfig
from automation_foundry.authoring.workspace import WorkspaceConfig
from automation_foundry.storage import ArtifactStoreConfig

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("FOUNDRY_RUN_LIVE_NEMOCLAW") != "1",
        reason="set FOUNDRY_RUN_LIVE_NEMOCLAW=1 to run the live NemoClaw smoke",
    ),
]


@pytest.mark.asyncio
async def test_fixture_generates_schema_valid_unapproved_bundle() -> None:
    api_key = os.environ.get("FOUNDRY_HERMES_API_KEY")
    workspace_mount = os.environ.get("FOUNDRY_WORKSPACE_MOUNT")
    sandbox_name = os.environ.get("FOUNDRY_NEMOCLAW_SANDBOX_NAME")
    if not api_key or (not workspace_mount and not sandbox_name):
        pytest.fail("a Hermes key and either a workspace mount or NemoClaw sandbox name are required")

    fixture = Path("tests/fixtures/ingestion_gold/sop_only/source.md").read_text(encoding="utf-8")
    with TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        store = ArtifactStoreConfig(
            root=temporary_root / "automations",
            database_path=temporary_root / "foundry.sqlite3",
        ).make()
        host_workspace = Path(workspace_mount) if workspace_mount else temporary_root / "workspace"
        host_workspace.mkdir(parents=True, exist_ok=True)
        workspace = WorkspaceConfig(
            host_mount=host_workspace,
            require_mount=sandbox_name is None,
            nemoclaw_sandbox_name=sandbox_name,
        ).make(store)
        workspace.initialize()
        automation = store.create_automation("Update a lead")
        evidence = EvidencePackage(
            automation_id=automation.id,
            sop_documents=[
                SopEvidence(
                    source_id=uuid4(),
                    page_count=1,
                    pages=[SopPage(page=1, text=fixture)],
                )
            ],
        )
        client = HermesClientConfig(api_key).make()

        draft = await BundleGenerator(workspace, client).generate(automation.id, evidence)

    assert draft.schema_version == "1.0"
    assert any(step.persistent_action and step.requires_confirmation_before for step in draft.steps)
    assert draft.completion_checks
    assert "coordinate" not in draft.skill_markdown.lower()
