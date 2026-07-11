# Computer-Use Automation Foundry: Specification and Evals

## Summary

Create `docs/SPEC.md`, `docs/EVALS.md`, and extend the existing `AGENTS.md` without implementing the application yet.

The MVP is a macOS-local FastAPI/React application where an operations expert uploads a video and/or SOP, reviews an agent-generated automation bundle, and runs it through NemoClaw/Hermes and HoloDesktop. Gradium provides video transcription and push-to-talk voice control.

## Product Specification

### Core workflows

- Dashboard lists draft, approved, running, failed, and completed automations with last-run status.
- Author names a task and uploads MP4/MOV/WebM video up to 10 minutes and/or PDF/MD/TXT SOP up to 20 pages.
- Host backend extracts timestamped transcript, keyframes, and document text.
- Gradium performs transcription; Hermes running inside NemoClaw uses hosted `holo3-122b-a10b` to convert evidence into a structured automation.
- Author reviews and edits the SOP, Holo skill, input schema, checks, conflicts, generated Python tools, and tests.
- Validation and explicit approval activate a version. Edits invalidate approval.
- Users run an automation from the dashboard or a Gradium push-to-talk conversation.
- Holo stages the CRM change but ends its first turn before Save. The UI and voice assistant show the proposed change; explicit approval sends a second message in the same Holo session to commit it.
- Cancellation, timeout, permission failure, or lost session must stop safely without attempting the final action.

### Cross-app behavior

- Ship two visually and structurally distinct PySide6 desktop CRM fixtures containing equivalent seeded records.
- Teach the workflow using CRM A.
- Execute the same semantic skill zero-shot on CRM B using only its app name and run inputs.
- Skills must describe goals, visible concepts, invariants, and success checks—never coordinates, selectors, or source-app-specific navigation.
- MVP generalization is limited to the same CRM business operation across different desktop CRM interfaces.

### Architecture

- FastAPI backend, React/Vite/TypeScript frontend, SQLite metadata, and repo-local gitignored artifact storage.
- Python 3.12+, `uv`, and npm.
- NemoClaw Hermes sandbox with a shared `/sandbox/workspace` mounted through `nemohermes … share mount`; macFUSE/SSHFS is an MVP prerequisite.
- A sandboxed stdio MCP server exposes automation-workspace and generated-tool operations to Hermes.
- The trusted macOS worker alone invokes HoloDesktop through its Python client. NemoClaw queues execution requests through the shared workspace; sandbox code never receives macOS control privileges.
- Holo sessions use bounded steps/time, double-Esc kill switch support, cancellation, and multi-turn continuation. [Holo Python integration](https://hub.hcompany.ai/holo-desktop-cli/how-to/embed-with-python)
- Gradium credentials remain backend-only; browser audio is proxied over WebSockets. [Gradium realtime STT](https://docs.gradium.ai/guides/speech-to-text)
- Originals remain local until user deletion. Consent explains that audio is sent to Gradium and selected frames/transcripts to hosted Holo3. [Holo privacy model](https://hub.hcompany.ai/holo-desktop-cli/security-and-privacy)

### Automation bundle

Each automation receives a stable UUID and versioned folder containing:

- `manifest.json`: identity, state, version, source hashes, target policy, input schema, approval metadata, and artifact hashes.
- `source/`: original uploads.
- `evidence/`: transcript, document text, timestamped keyframes, and evidence-linked observations.
- `versions/<n>/SOP.md`: goal, preconditions, semantic steps, decisions, exceptions, confirmation boundary, and completion checks.
- `versions/<n>/SKILL.md`: valid Holo skill frontmatter and app-independent procedure. [Holo skill format](https://hub.hcompany.ai/holo-desktop-cli/how-to/customize)
- `inputs.schema.json`: typed runtime parameters and required-field descriptions.
- `tools.py` and `tool_manifest.json`: deterministic parsing, normalization, mapping, and validation functions.
- `tests/test_tools.py` and `eval_cases.json`.
- `review.json`: unresolved source conflicts and timestamp/page evidence.
- `runs/<run-id>/`: immutable request, event JSONL, staged-change summary, approval record, result, and redacted diagnostics.

Approved skills are atomically published to a stable app-owned directory under `~/.holo/skills/`. Unapproved or hash-mismatched bundles cannot run.

### Interfaces and states

Define Pydantic contracts for:

- `AutomationManifest`, `AutomationVersion`, `EvidenceReference`, `InputDefinition`, `GeneratedTool`, and `ReviewConflict`.
- `RunRequest`, `RunPreview`, `RunEvent`, `StagedChange`, `ApprovalRecord`, and `RunResult`.
- Run states: `prepared`, `awaiting_start_confirmation`, `executing`, `awaiting_commit_approval`, `committing`, `succeeded`, `failed`, and `cancelled`.
- MCP operations for listing automations, reading evidence batches, saving bundles, validating generated tools, preparing runs, queuing runs, reading status, approving commits, and cancelling runs.
- REST/WebSocket endpoints for dashboard CRUD, ingestion progress, artifact editing, approval, run events, microphone streaming, and voice responses.

Generated tools execute only inside NemoClaw with an import allowlist, JSON-only I/O, artifact-hash verification, CPU/memory/time limits, and no network, subprocess, dynamic evaluation, or arbitrary filesystem access.

## Evaluation Specification

### Must-pass MVP checks

- Six gold ingestion fixtures cover video-only, SOP-only, aligned combined input, conflicting sources, silent video, and malformed input.
- Generated output achieves at least 90% aggregate recall of annotated critical steps and 100% recall of confirmation boundaries.
- All generated bundles validate structurally; source conflicts remain visible until resolved.
- Five resettable Holo trials run against each CRM. At least 4/5 must produce the exact intended saved state in each app.
- All 10 CRM trials must leave persistent state unchanged before commit approval.
- Ten voice utterances cover paraphrases, missing inputs, ambiguity, cancellation, and confirmation. At least 9/10 must select the correct automation and inputs; ambiguous or partial speech must never start a run.
- Editing an approved artifact, changing its hash, losing the NemoClaw mount, denying approval, cancelling, or timing out must fail closed.
- Generated-tool adversarial fixtures must reject forbidden imports, file access, network access, subprocesses, dynamic execution, infinite loops, and unapproved code.

### Automated checks

- Backend: Ruff, strict mypy, pytest, schema tests, state-machine tests, mocked Gradium/Holo3/Holo/NemoClaw integrations, and path-security tests.
- Frontend: ESLint, TypeScript checking, Vitest/React Testing Library, production build, and Playwright dashboard/voice flows.
- Desktop fixtures: deterministic seed/reset tests and exact persisted-state assertions.
- Repository commands will include `uv sync`, lint, format-check, mypy, pytest, `npm ci`, frontend lint/typecheck/test/build, and a combined verification command.

### Manual/live checks

- macOS permissions, Docker/NemoClaw health, SSHFS mount, Holo login, microphone access, Gradium streaming, and the double-Esc kill switch.
- End-to-end video ingestion, artifact review/edit/approval, dashboard execution, voice execution, commit confirmation, cancellation, and zero-shot CRM B transfer.
- Record ingestion duration, voice turn latency, Holo steps, execution duration, and API usage. Recommended targets are ready-to-review within twice the source duration, voice preview within five seconds of push-to-talk release, and CRM completion within three minutes.

## Documentation Changes and Boundaries

- `docs/SPEC.md` will contain the full product definition, flows, requirements, architecture, data model, integrations, security, failure modes, non-goals, and deferred ideas.
- `docs/EVALS.md` will separate must-pass checks, recommended quality checks, and future production benchmarks, with reproducible commands and evidence templates.
- Existing `AGENTS.md` will retain current Python conventions while adding the new directories, frontend conventions, architectural boundaries, required documentation reading, generated-code restrictions, and completion checks.
- Existing H Company examples remain intact but are not part of the MVP runtime.
- Non-goals: cloud/multi-user deployment, scheduling, Windows/Linux support, always-listening audio, arbitrary network-capable generated tools, concurrent Holo runs, real CRM production support, and cross-domain workflow transfer.
- The local app binds to `127.0.0.1`, has no user authentication, supports one active Holo run, and retains data until explicit deletion.
- Implementation planning begins only after the specification and eval documents are reviewed and approved.
