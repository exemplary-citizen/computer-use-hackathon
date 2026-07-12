# Automation Foundry authoring setup

This runbook covers Member 1's upload-to-approved-bundle lane. Holo execution, voice invocation, desktop fixtures, and
the two-turn run state machine remain in Member 2's lane.

## Prerequisites

- macOS with Python 3.12 or newer, `uv`, Node/npm, and FFmpeg/FFprobe;
- an OpenRouter API key with access to `google/gemini-3.5-flash`;
- HoloDesktop credentials only for the later execution phase.

The default hackathon path needs an OpenRouter API key and sends the accepted original video directly to Gemini. The
backend reads configuration from the process environment only. It does not load a `.env` file automatically.

For the hackathon OpenRouter/Gemini path, set:

```bash
export FOUNDRY_GENERATION_PROVIDER=openrouter_video
export FOUNDRY_OPENROUTER_API_KEY="$OPENROUTER_API_KEY"
export FOUNDRY_OPENROUTER_MODEL=google/gemini-3.5-flash
export FOUNDRY_WORKSPACE_REQUIRE_MOUNT=false
```

This mode sends the uploaded video as a base64 `video_url` plus normalized SOP metadata to Gemini. HoloDesktop is
reserved for executing the approved skill on the desktop.

The legacy `hermes_workspace` provider additionally requires a mounted NemoClaw workspace, a Hermes API forward and
bearer token, and a Gradium key for narrated-video transcription. Optional path settings include `FOUNDRY_DATA_ROOT`,
`FOUNDRY_DATABASE_PATH`, and `FOUNDRY_PUBLISHED_SKILL_ROOT`.

## Run locally

From the repository root:

```bash
uv sync
uv run automation-foundry-api
```

In a second terminal:

```bash
cd web
npm ci
npm run dev
```

Both servers bind to loopback. Vite proxies `/api` to the backend on port 8000.

## Authoring flow

1. Create an automation and accept the provider disclosure.
2. Upload one supported video, one supported SOP, or one of each.
3. Wait for preprocessing and OpenRouter/Gemini generation.
4. Review the semantic procedure, Holo skill, schemas, evidence, conflicts, generated code, and tests.
5. Resolve blocking conflicts and fix validation findings.
6. For bundles with generated tools, run the sandbox tool tests.
7. Enter the reviewer name and approve the exact validated version.

Approval binds to hashes for every runnable artifact. Editing an approved artifact forks a new unapproved version.
Startup reconciliation removes published skills when approved bytes are missing or changed.

Approval also atomically writes the execution handoff at
`data/automations/<automation-id>/approved_bundle.json`. Point execution at the selected approved result before a
smoke or live run:

```bash
export FOUNDRY_BUNDLE_PATH=/absolute/path/to/data/automations/<automation-id>/approved_bundle.json
uv run python -m automation_foundry.execution.smoke run --app a
```

Editing, deactivating, or failing reconciliation removes this handoff so execution cannot use stale approval bytes.

## Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest

cd web
npm run lint
npm run typecheck
npm run test
npm run build
```

Live provider tests require explicit credentials and are not part of ordinary deterministic CI. The six ingestion gold
cases live under `tests/fixtures/ingestion_gold/`; reviewer-confirmed observations are scored by
`automation_foundry.evals.ingestion`.
