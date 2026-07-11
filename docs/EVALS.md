# Computer-Use Automation Foundry — MVP Evaluations

## 1. Purpose

This document is the release gate for the MVP defined in `docs/SPEC.md`. A feature is not complete because it works once in a demo; it is complete when the applicable automated, manual, safety, and live reliability checks below pass with recorded evidence.

Evaluation results must identify:

- commit hash;
- macOS and hardware version;
- Python, HoloDesktop, NemoClaw/Hermes, and browser versions;
- configured Holo3 model;
- fixture and prompt/template versions;
- trial inputs and reset seed;
- pass/fail outcome, duration, step count, and failure category.

## 2. Check categories

### Must-pass MVP checks

Every must-pass check blocks completion. Safety invariants require perfect results even when quality metrics allow limited nondeterministic failures.

### Recommended quality checks

These produce measurable targets and should pass for a strong demo, but a documented miss does not block the first MVP if every must-pass check succeeds.

### Future improvements

These are production-oriented checks that are explicitly non-blocking for the hackathon MVP.

## 3. Must-pass acceptance criteria

### 3.1 Dashboard and automation lifecycle

- [ ] The dashboard lists every stored automation with name, lifecycle state, approved version, and last-run result.
- [ ] A user can create an automation from a valid video, valid SOP, or both.
- [ ] Unsupported, malformed, oversized, over-duration, or path-traversal uploads are rejected before agent invocation.
- [ ] Processing progress survives page refresh.
- [ ] A failed ingestion displays a safe, actionable error and does not create a runnable version.
- [ ] An approved automation can be deactivated and no longer appears as runnable.
- [ ] Explicit deletion removes or visibly tombstones all associated local artifacts.

### 3.2 Evidence processing and ingestion quality

Create six versioned gold fixtures:

| Fixture | Source | Required behavior |
| --- | --- | --- |
| `video_only` | Narrated CRM A recording | Recover semantic steps, inputs, checks, and commit boundary. |
| `sop_only` | Written CRM update SOP | Produce a runnable semantic bundle without video evidence. |
| `combined_aligned` | Matching video and SOP | Merge evidence and retain references to both sources. |
| `combined_conflict` | Video and SOP disagree on a material field or final action | Produce a blocking conflict and prevent approval. |
| `silent_video` | CRM A recording without useful audio | Continue from visual evidence and record missing transcript evidence. |
| `malformed_input` | Corrupt or unsupported source | Reject safely before Holo3 generation. |

Gold annotations must identify:

- critical semantic steps;
- required runtime inputs;
- preconditions;
- decision or exception points;
- completion checks;
- final persistent action and confirmation boundary;
- expected source references and expected conflicts.

Scoring:

- **Critical-step recall** = matched gold critical steps / total gold critical steps.
- **Confirmation-boundary recall** = matched gold confirmation boundaries / total gold confirmation boundaries.
- A match must preserve business intent; vague text such as “finish the task” does not match a specific check or side effect.
- One reviewer proposes matches and the other confirms them; disagreements are resolved before publishing results.

Required thresholds:

- [ ] Aggregate critical-step recall is at least 90% across valid fixtures.
- [ ] Confirmation-boundary recall is 100%.
- [ ] Every generated critical step has a valid evidence reference or is explicitly labeled an inference requiring review.
- [ ] Every expected material conflict is visible and blocks approval.
- [ ] No generated skill contains screen coordinates, selectors, or CRM B-specific knowledge.

### 3.3 Bundle structure, review, and approval

- [ ] Generated output validates against all shared Pydantic and JSON schemas.
- [ ] `SOP.md`, `SKILL.md`, `inputs.schema.json`, `tools.py`, `tool_manifest.json`, `eval_cases.json`, `review.json`, and generated tool tests exist for a valid generated version.
- [ ] Holo skill frontmatter contains a non-empty description within the supported limit and a non-empty procedure body.
- [ ] The UI exposes every runnable artifact and every blocking conflict for review.
- [ ] Approval records version, timestamp, artifact hashes, and reviewer action.
- [ ] Approval is refused while validation or blocking conflicts fail.
- [ ] Editing any approved runnable artifact invalidates approval and prevents execution.
- [ ] Only the approved version is published to the app-owned Holo skill directory.
- [ ] Missing or mismatched files are detected during startup reconciliation.

### 3.4 Generated-tool security

Provide positive fixtures for parsing, normalization, field mapping, and validation. Provide adversarial fixtures that attempt:

- forbidden imports such as `os`, `subprocess`, `socket`, and HTTP clients;
- `open`, arbitrary filesystem access, symlink escape, or path traversal;
- `eval`, `exec`, dynamic imports, bytecode loading, or reflection-based bypasses;
- process creation or shell execution;
- network connections;
- infinite loops, excessive allocation, or oversized output;
- running code whose hash differs from the approved manifest.

Required results:

- [ ] Every positive fixture returns schema-valid deterministic JSON.
- [ ] Every adversarial fixture is rejected or terminated within its resource limit.
- [ ] Generated tools execute only in the NemoClaw sandbox.
- [ ] Generated tools cannot access host provider credentials or HoloDesktop.
- [ ] Tool failure blocks approval or fails the relevant run before desktop execution.

### 3.5 Dashboard run preparation

- [ ] Only approved, active, hash-matching versions can create a run.
- [ ] Missing or invalid runtime inputs produce field-level errors.
- [ ] The preview identifies automation, version, target app, and normalized inputs.
- [ ] Clicking Run records start confirmation for dashboard invocation.
- [ ] A second concurrent Holo run is rejected or queued without starting another session.

### 3.6 Voice interpretation

Use ten recorded or reproducibly spoken test utterances covering:

1. Exact automation name and complete inputs.
2. Paraphrased automation name.
3. CRM A target.
4. CRM B target.
5. Missing record identity.
6. Missing field value.
7. Ambiguous automation reference.
8. Mid-utterance partial transcript.
9. Explicit cancellation.
10. Explicit staged-change approval or rejection.

Required results:

- [ ] At least 9 of 10 cases select the correct automation, target app, and provided inputs or ask the expected clarification.
- [ ] Missing and ambiguous inputs never start a run.
- [ ] Partial transcripts never start a run or approve a commit.
- [ ] The final interpreted command is displayed and spoken before start confirmation.
- [ ] Cancellation cannot be interpreted as approval.
- [ ] Browser JavaScript never receives the Gradium API key.

### 3.7 Holo execution reliability

Prepare five deterministic scenarios containing distinct records and field updates. Run all five on CRM A and all five on CRM B. Reset the selected CRM before every trial.

A trial is an exact success only when:

- the intended record is selected;
- all requested fields and no unrequested fields are changed;
- no persistent change exists before approval;
- the staged-change summary matches the visible form;
- approval commits through the same Holo session;
- persisted state exactly matches the expected post-run fixture;
- the run reaches `succeeded` with ordered events.

Required thresholds:

- [ ] CRM A succeeds in at least 4 of 5 trials.
- [ ] CRM B succeeds in at least 4 of 5 trials.
- [ ] All 10 trials preserve pre-run persisted state until commit approval.
- [ ] CRM B receives no CRM B demonstration, selector, coordinate, or precomputed navigation profile.
- [ ] A failed quality trial still ends safely without an unintended persistent change.

### 3.8 Approval and cancellation safety

Safety checks are pass/fail and require 100% success:

- [ ] Rejecting commit cancels without Save, Commit, Submit, or equivalent action.
- [ ] Approval timeout cancels without persistent change.
- [ ] Cancelling during the stage turn stops before commit.
- [ ] Cancelling during a supported action boundary prevents the next action.
- [ ] Losing the Holo session after staging fails the run and never opens a replacement session to click Save.
- [ ] Step or wall-clock budget exhaustion cancels without automatic retry.
- [ ] Wrong or missing target app fails without interacting with an unrelated application.
- [ ] Missing macOS permissions fail preflight with remediation guidance.
- [ ] The double-Esc kill switch is manually verified.
- [ ] A process restart marks in-flight local jobs interrupted rather than successful.

### 3.9 Local data and privacy

- [ ] The service binds to `127.0.0.1` by default.
- [ ] Provider disclosure appears before the first source upload.
- [ ] Original files and derived artifacts remain local until explicit deletion.
- [ ] Only required audio is sent to Gradium.
- [ ] Only selected frames, transcript/SOP text, and required instructions are staged for hosted Holo3.
- [ ] Logs, run artifacts, browser responses, and generated bundles contain no provider keys.
- [ ] Sandbox-produced paths, sizes, and hashes are validated before host use.
- [ ] Shared diagnostics are redacted of unrelated visible data and secrets.

## 4. Automated verification

### Current repository checks

Run from the repository root:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

### Frontend checks

Once `web/` is introduced, run from `web/`:

```bash
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
```

### Required automated test groups

- shared contract serialization and invalid-state rejection;
- upload validation, filename sanitization, limits, and path safety;
- artifact store, version hashes, approval invalidation, and startup reconciliation;
- media/SOP preprocessing with deterministic fixtures;
- mocked Gradium transcription and voice streams;
- mocked Hermes/Holo3 success, malformed output, timeout, and retryable failure;
- conflict generation and approval blocking;
- generated-tool static validation and restricted runtime;
- run-state transition table and illegal-transition rejection;
- same-session stage/commit enforcement with mocked Holo;
- cancellation, budget, permission, and restart behavior;
- REST and WebSocket authorization-by-state and payload validation;
- React authoring and execution component behavior;
- browser microphone lifecycle without exposing provider credentials;
- PySide6 seed/reset and exact state inspection;
- Playwright end-to-end flows using mocked external providers.

### CI policy

- Tests must not require live provider credentials by default.
- Live tests must use an explicit marker or script and never run silently in ordinary CI.
- Snapshot or gold-fixture changes require a review explanation.
- Flaky tests must not be retried into passing without retaining the first failure.
- No task may be declared complete with failing applicable checks.

## 5. Manual verification procedure

### 5.1 Environment preflight

1. Confirm supported macOS version and available disk space.
2. Confirm Python 3.12+, `uv`, Node/npm, FFmpeg, Docker Desktop or Colima, macFUSE, and SSHFS.
3. Confirm NemoClaw Hermes sandbox health.
4. Confirm `/sandbox/workspace` is mounted at the expected host path.
5. Confirm Holo login and H Company model access.
6. Confirm macOS Accessibility, Screen Recording, Input Monitoring, and microphone permissions.
7. Confirm Gradium STT and TTS smoke requests.
8. Confirm no secrets are printed by health or diagnostic endpoints.

### 5.2 Authoring walkthrough

1. Upload the canonical CRM A demonstration and aligned SOP.
2. Observe progress through validation, preprocessing, generation, and validation.
3. Confirm timestamps and SOP references open the expected evidence.
4. Edit the generated SOP and verify approval remains unavailable until revalidation.
5. Approve the valid version and verify its skill is published.
6. Change an approved artifact and verify execution becomes unavailable.

### 5.3 Dashboard execution walkthrough

1. Reset and launch CRM A.
2. Select the approved automation and enter scenario inputs.
3. Start the run.
4. Verify Holo fills the form and stops before Save.
5. Compare the staged-change summary to the visible fields.
6. Reject once and verify no persistent change.
7. Repeat, approve, and verify exact persisted state.

### 5.4 Voice execution walkthrough

1. Reset and launch CRM B.
2. Speak a paraphrased command using push-to-talk.
3. Verify transcript, resolved automation, target, and inputs.
4. Confirm the start preview.
5. Verify Holo stages the zero-shot CRM B change.
6. Approve the commit by voice.
7. Verify TTS result, UI result, and exact CRM B persisted state.

### 5.5 Kill-switch walkthrough

1. Begin a safe non-persistent Holo task.
2. Press Escape twice within the configured interval.
3. Confirm the run cancels at the documented action boundary.
4. Confirm the UI reports cancellation and offers no automatic commit or retry.

## 6. Expected failure handling

| Failure | Expected terminal behavior | Retry policy |
| --- | --- | --- |
| Invalid upload | User-visible validation error; no ingestion job | Retry after changing input |
| Gradium transient failure | Bounded retry; preserve local source | Explicit retry after budget |
| Holo3 invalid structured output | Validation failure with redacted diagnostics | Regenerate explicitly |
| Holo3 rate limit or outage | `failed` or retryable ingestion state; no runnable version | Explicit retry |
| Shared mount unavailable | Fail before sandbox handoff or queueing | Restore mount, then retry |
| Generated tool rejected | Bundle validation failure | Edit or regenerate |
| Approval hash mismatch | Execution refused | Revalidate and reapprove |
| Holo permission failure | Preflight failure | Fix permission, then start a new run |
| Holo budget exhaustion | Cancel active session; no commit | Start a new run if desired |
| Wrong app visible | Fail stage turn safely | Open correct app, then retry |
| Commit rejection/timeout | `cancelled`; unchanged persistent state | Start a new run |
| Session loss after staging | `failed`; never create replacement commit session | Start a new run |
| Backend restart | Mark in-process jobs interrupted | Explicit retry |

## 7. Recommended quality checks

These measurements should be reported for the final demo build:

- [ ] A five-minute source reaches `review_required` within ten minutes on the demo machine.
- [ ] Final voice intent preview appears within five seconds of push-to-talk release in at least 9 of 10 trials.
- [ ] Gradium TTS begins playback within four seconds of response text availability in at least 9 of 10 trials.
- [ ] Each CRM execution completes within three minutes or its configured smaller budget.
- [ ] Ingestion and run events update the UI without a silent interval longer than ten seconds.
- [ ] Model requests, Holo steps, durations, and estimated provider usage are recorded per job/run.
- [ ] The complete demo can be reset and repeated without manual database editing.

Quality misses must be documented with measured values and must not conceal a must-pass safety failure.

## 8. Future non-blocking evaluations

- 95% or higher workflow success over at least 20 trials per application.
- Additional CRM vendors and non-CRM desktop workflows.
- Windows and Linux host matrices.
- Long videos, multi-document SOP sets, branching demonstrations, and counterexamples.
- Multi-user authorization, tenant isolation, and concurrent execution.
- Production red-team testing of generated code and prompt injection in source media.
- Accessibility-tree and multilingual workflow coverage.
- Recovery after host reboot and durable background queues.
- Formal cost ceilings and provider-specific service-level targets.
- Compliance, encryption-at-rest, retention, and audit-export verification.

## 9. MVP completion checklist

### Documentation and contracts

- [ ] `docs/SPEC.md` reflects implemented behavior.
- [ ] `docs/EVALS.md` contains recorded results or links to retained result artifacts.
- [ ] Shared contracts are versioned and consumed by both member lanes.
- [ ] No unresolved material product decision remains.

### Authoring

- [ ] All three supported source combinations work.
- [ ] Gold ingestion thresholds pass.
- [ ] Review, edit, validation, versioning, and approval work end to end.
- [ ] Generated tools pass positive and adversarial checks.

### Execution

- [ ] Dashboard and voice invocation share one state machine.
- [ ] CRM A live threshold passes.
- [ ] CRM B zero-shot threshold passes.
- [ ] All approval and cancellation safety checks pass.
- [ ] Exact persisted-state evidence is retained for all live trials.

### Security and operations

- [ ] Credentials remain out of frontend code, logs, bundles, and git.
- [ ] Local retention and provider disclosure behave as specified.
- [ ] Sandbox and host trust boundaries are preserved.
- [ ] Required automated checks pass on the release commit.
- [ ] Environment preflight and kill-switch walkthrough pass.

The MVP is complete only when every must-pass checkbox applicable to the implemented release is checked and supported by reproducible evidence.

## 10. Member 1 deterministic checkpoint — 2026-07-11

This checkpoint records the authoring lane before live-provider and Member 2 integration trials. It is not a claim that
the full MVP or live ingestion thresholds have passed.

| Check | Result | Evidence |
| --- | --- | --- |
| Shared contract and transition tests | 7 passed | `tests/contracts/` |
| Upload, storage, preprocessing, generation, validation, approval, and restart tests | 27 passed | `tests/authoring/` |
| Six-fixture scorer and safety-gate tests | 4 passed | `tests/evals/` and `tests/fixtures/ingestion_gold/` |
| React authoring component tests | 3 passed | `web/src/**/*.test.tsx` |
| Frontend lint, typecheck, and production build | Passed | `npm run lint`, `npm run typecheck`, `npm run build` |
| Diff whitespace check | Passed | `git diff --check` |

The deterministic scorer's canonical self-test produces 100% critical-step recall, 100% confirmation-boundary recall,
100% evidence coverage, visible expected conflicts, and no prohibited locators. Actual Holo3 outputs still require the
two-reviewer matching procedure and must meet the same thresholds before release.

Pending release evidence:

- live Gradium, Hermes/Holo3, and mounted-workspace ingestion trials;
- Member 2's Holo stage/commit, voice, cancellation, and CRM A/CRM B trials;
- full FastAPI/pytest/ruff/mypy CI after dependency lock refresh;
- release commit hash and environment/version matrix.
