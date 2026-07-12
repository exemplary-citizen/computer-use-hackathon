# Automation Foundry authoring setup

This runbook covers Member 1's upload-to-approved-bundle lane. Holo execution, voice invocation, desktop fixtures, and
the two-turn run state machine remain in Member 2's lane.

## Prerequisites

- macOS with Python 3.12 or newer, `uv`, Node/npm, and FFmpeg/FFprobe;
- a NemoClaw workspace exposed by a verified host mount or the authenticated CLI upload transport;
- a Hermes API forward available at `http://127.0.0.1:8642/v1` by default;
- a Hermes bearer token and, for narrated video, a Gradium API key.

The backend reads configuration from the process environment only. It does not load a `.env` file automatically.

```bash
export FOUNDRY_WORKSPACE_MOUNT=/absolute/host/path/to/sandbox-workspace
export FOUNDRY_HERMES_API_KEY=replace-with-local-secret
export FOUNDRY_GRADIUM_API_KEY=replace-with-local-secret
```

On macOS systems where macFUSE is unavailable, use a local staging directory and the NemoClaw upload transport:

```bash
export FOUNDRY_WORKSPACE_MOUNT="$PWD/data/nemoclaw-workspace"
export FOUNDRY_WORKSPACE_REQUIRE_MOUNT=false
export FOUNDRY_NEMOCLAW_SANDBOX_NAME=hai-hermes
```

Optional settings include `FOUNDRY_HERMES_BASE_URL`, `FOUNDRY_HERMES_MODEL`, `FOUNDRY_DATA_ROOT`,
`FOUNDRY_DATABASE_PATH`, `FOUNDRY_PUBLISHED_SKILL_ROOT`, and `FOUNDRY_WORKSPACE_REQUIRE_MOUNT`.

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
3. Wait for preprocessing and Hermes/Holo3 generation.
4. Review the semantic procedure, Holo skill, schemas, evidence, conflicts, generated code, and tests.
5. Resolve blocking conflicts and fix validation findings.
6. For bundles with generated tools, run the sandbox tool tests.
7. Enter the reviewer name and approve the exact validated version.

Approval binds to hashes for every runnable artifact. Editing an approved artifact forks a new unapproved version.
Startup reconciliation removes published skills when approved bytes are missing or changed.

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
