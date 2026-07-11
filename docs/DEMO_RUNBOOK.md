# Demo runbook

This is the shortest path from a clean demo machine to the agreed reject-first presentation. Do not put provider keys
in `.env`, shell history, logs, screenshots, or git; export them interactively in the terminal that launches the app.

## 1. Prepare the machine

1. Install and sign in to HoloDesktop.
2. Install Python 3.12, `uv`, Node/npm, FFmpeg/FFprobe, and the configured NemoClaw workspace runtime.
3. Grant Accessibility, Screen Recording, Input Monitoring, and microphone permissions to HoloDesktop and the terminal.
4. Restart HoloDesktop and the terminal after granting permissions.
5. Export `HAI_API_KEY`, `FOUNDRY_HERMES_API_KEY`, `FOUNDRY_GRADIUM_API_KEY`, and
   `FOUNDRY_WORKSPACE_MOUNT` interactively. Never print their values.

## 2. Install and verify

```bash
UV_PYTHON=3.12 uv sync
UV_PYTHON=3.12 uv run pytest

cd web
npm ci
npm run lint
npm run typecheck
npm test -- --run
cd ..
```

Run the deterministic integration checks:

```bash
UV_PYTHON=3.12 uv run python -m automation_foundry.execution.smoke run --app a
UV_PYTHON=3.12 uv run python -m automation_foundry.execution.smoke run --app a --decision reject
UV_PYTHON=3.12 uv run python -m automation_foundry.execution.smoke run --app b
UV_PYTHON=3.12 uv run python -m automation_foundry.execution.smoke doctor
```

The approve run must show the staged diff, state that persisted data remained unchanged before approval, and finish
with `exec-smoke PASSED`.

## 3. Create the authored handoff

Use the authoring UI to ingest the canonical video and/or SOP, review the generated artifacts, resolve any blocking
conflicts, and approve the selected version. Approval creates:

```text
data/automations/<automation-id>/approved_bundle.json
```

Point execution at that exact file and repeat the three smoke runs:

```bash
export FOUNDRY_BUNDLE_PATH=/absolute/path/to/data/automations/<automation-id>/approved_bundle.json
```

## 4. Resolve the live Holo surface first

Before changing any other execution code on the demo machine:

```bash
UV_PYTHON=3.12 uv run python -m automation_foundry.execution.spike probe
UV_PYTHON=3.12 uv run python -m automation_foundry.execution.spike holo-surface
```

Walk the `holo-two-turn` checklist. Wire `LiveHoloAdapter` only to signatures confirmed by the spike: session creation,
same-session second message, liveness polling, cancellation, and budget keyword arguments. Preserve structural stage
guards, hash verification, approval-hash binding, fail-closed session loss, and the single-active-run invariant.

## 5. Record live evidence

```bash
UV_PYTHON=3.12 uv run python -m automation_foundry.evals.execution trials --app a --live
UV_PYTHON=3.12 uv run python -m automation_foundry.evals.execution trials --app b --live
UV_PYTHON=3.12 uv run python -m automation_foundry.evals.execution voice-cases
```

Keep generated evidence under `data/execution/evals/` and record the results in `docs/EVALS.md`.

## 6. Present reject first

1. Put CRM A on the projected display at its fixed 1280x800 geometry.
2. By voice, request a deliberately wrong owner.
3. Show the staged diff and reject it.
4. Prove nothing was saved with `python -m desktop_fixtures.cli dump --app a`.
5. Retry with the correct owner and approve by voice so focus never leaves the CRM between stage and commit.
6. Close with the zero-shot CRM B run.

Rehearse twice and screen-record the best complete run as the fallback video.
