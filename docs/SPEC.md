# Computer-Use Automation Foundry — MVP Specification

## 1. Project overview

Computer-Use Automation Foundry is a local macOS application that learns repeatable desktop workflows from demonstration videos and standard operating procedures (SOPs). It converts those sources into reviewable automation bundles, then delegates execution to H Company's HoloDesktop agent under NemoClaw/Hermes orchestration.

The product replaces the usual engineering-heavy automation discovery process with an authoring workflow designed for domain experts. An operations expert demonstrates what to do, reviews the generated procedure and tools, approves a version, and can later invoke it from the dashboard or by voice.

The MVP proves four capabilities together:

1. Extract a reliable semantic procedure from video and/or written SOP evidence.
2. Produce a reusable, versioned Holo skill and constrained data-processing tools.
3. Execute the learned operation safely in a native desktop application.
4. Transfer the same business operation zero-shot to a second CRM with a different interface.

`docs/EVALS.md` defines the checks that determine whether this specification is satisfied.

## 2. Target users

### Primary user

A non-technical operations expert who understands a domain workflow but does not write automation code. The user can record a desktop demonstration, provide an SOP, review generated artifacts, supply runtime inputs, and approve consequential actions.

### Secondary user

An automation engineer who diagnoses failed generations or runs, inspects evidence and logs, refines generated artifacts, and maintains the local environment.

### MVP deployment model

- One local user on macOS.
- One browser session connected to a FastAPI service bound to `127.0.0.1`.
- One configured NemoClaw Hermes sandbox.
- One active HoloDesktop execution at a time.
- No application-level authentication because the service is loopback-only.

## 3. Problem statement

Computer-use automations normally require technical staff to observe a domain expert, reverse-engineer application behavior, encode application-specific steps, and repeatedly repair brittle selectors. This is slow, expensive, and difficult to scale across siloed desktop software.

The MVP must let an operations expert teach a task directly while retaining the safeguards normally provided by an automation engineer: traceable evidence, editable artifacts, validation, version approval, bounded execution, explicit confirmation before persistent changes, and reproducible evals.

## 4. Goals and success criteria

### Product goals

- Produce an editable automation bundle from a supported video, SOP, or both.
- Make every generated procedural claim traceable to a video timestamp or SOP page/section where evidence exists.
- Separate semantic business intent from source-application coordinates, selectors, and layout details.
- Require human approval before a bundle becomes runnable.
- Require a second human approval before a run performs its final persistent side effect.
- Support dashboard and push-to-talk invocation through the same execution state machine.
- Demonstrate zero-shot transfer from CRM A to CRM B.

### MVP success bar

- At least 90% aggregate recall of annotated critical ingestion steps.
- 100% recall of annotated confirmation boundaries.
- At least 4 exact successful runs out of 5 on each mock CRM.
- No persistent CRM mutation before approval in all 10 live execution trials.
- At least 9 correct automation-and-input interpretations out of 10 voice trials.
- All structural, generated-code, security, and failure-handling checks in `docs/EVALS.md` pass.

## 5. MVP scope

### 5.1 Local dashboard

The landing page lists all automations with:

- name and stable identifier;
- lifecycle state;
- current approved version;
- source types;
- last run status and timestamp;
- actions to open, run, deactivate, or delete the automation.

Lifecycle states are `draft`, `processing`, `review_required`, `approved`, `inactive`, and `failed`. Run state is recorded separately.

### 5.2 Source ingestion

The author supplies a task name and at least one source:

- MP4, MOV, or WebM video, at most 10 minutes;
- PDF, Markdown, or plain-text SOP, at most 20 pages after normalization;
- one video and one SOP together.

The host backend must:

- validate extension, MIME type, size, duration, and page limits;
- sanitize filenames and store uploads under a generated automation ID;
- extract timestamped visual evidence and source metadata from video;
- optionally transcribe audio through Gradium when using the legacy workspace ingestion path;
- extract normalized SOP text and stable page/section references;
- retain source hashes and preprocessing metadata;
- expose processing progress and actionable failures.

The implementation may reduce redundant video frames for local review, but the hosted video-ingestion path sends the original accepted video so the model can interpret motion and audio in temporal order. Frame selection parameters must still be recorded with the ingestion job.

### 5.3 Agentic bundle generation

The default hosted ingestion path sends the accepted original video plus normalized source metadata to `google/gemini-3.5-flash` through OpenRouter and requests a schema-constrained bundle. SOP-only and legacy workspace generation may still use the NemoClaw/Hermes adapter. Holo models and HoloDesktop are reserved for approved desktop execution, not video ingestion.

For combined video and SOP inputs:

- neither source silently overrides the other;
- aligned evidence may be merged;
- contradictions, missing details, and ambiguous branches must appear in `review.json`;
- unresolved material conflicts block approval.

Generated procedural content must be semantic. It may name visible concepts and expected labels, but must not contain screen coordinates, DOM selectors, source-app window geometry, or claims that a target application will share CRM A's layout.

### 5.4 Review and approval

The author can view and edit:

- semantic SOP;
- Holo skill;
- input schema;
- preconditions and completion checks;
- detected conflicts;
- pure-data Python tools and their manifest;
- generated tool tests and example cases.

Validation runs after generation and after every edit. Approval is allowed only when:

- required artifacts exist and match their schemas;
- material conflicts are resolved;
- the Holo skill has valid frontmatter and a non-empty body;
- generated tools pass static restrictions and tests;
- all artifact hashes are current.

Any edit to an approved artifact creates a new draft version or invalidates the existing approval. Only an approved version may be published to Holo or used for a run.

### 5.5 Generated tool capability

Generated Python tools are limited to deterministic, pure-data operations such as parsing, normalization, field mapping, and validation.

They must:

- accept and return JSON-compatible values;
- declare entrypoints and schemas in `tool_manifest.json`;
- execute only inside the NemoClaw sandbox;
- use an explicit import allowlist;
- run under CPU, memory, and wall-clock limits;
- be rejected if their bytes do not match the approved artifact hash.

They must not:

- access the network;
- spawn subprocesses;
- read or write arbitrary files;
- use dynamic evaluation or code loading;
- control the desktop;
- access host credentials.

All desktop interaction belongs to HoloDesktop.

### 5.6 Dashboard invocation

The user selects an approved automation, target app, and runtime inputs. The hackathon demo form defaults to CRM B —
Meridian so the zero-shot target is not accidentally replaced by CRM A — Northlight; Northlight remains available only
when the user deliberately selects it. Clicking Run is the start confirmation for dashboard invocation. The system
validates inputs and creates a run before Holo gains control.

### 5.7 Voice invocation

The browser provides push-to-talk interaction:

1. Microphone audio is proxied by the backend to Gradium realtime STT.
2. Partial and final transcripts are displayed.
3. Hermes identifies the automation, target application, and inputs.
4. Missing or ambiguous information triggers a clarification response.
5. The UI and Gradium TTS present a complete run preview.
6. An explicit confirmation is required before Holo starts.

Partial transcripts, silence, or ambiguous commands must never start a run. Voice is limited to listing or selecting automations, supplying inputs, starting after confirmation, checking status, approving or rejecting the staged change, and cancelling.

### 5.8 Safe two-turn desktop execution

The trusted host worker invokes HoloDesktop through its Python client. Every execution is bounded by configured step and wall-clock limits and supports cancellation and the Holo kill switch.

For the bundled CRM fixtures, a live run shall restart or launch the selected CRM as a named macOS application bundle
with a stable application identity before creating the Holo session. The host requests activation through macOS
LaunchServices, uses the configured fixture data root, and supplies Holo the exact application and window names. Holo
observes the full desktop and may use the normal macOS app switcher to bring that exact window forward, but must not
search Spotlight, Finder, the Dock, Terminal, or Applications for the CRM.

The run has two Holo turns:

1. **Stage turn:** Holo opens the target app, locates the intended record, fills the requested values, visually checks the staged form, and returns its structured result without activating Save, Commit, Submit, or an equivalent persistent action. The editor remains visibly open with the unsaved staged values. After verification, Holo must not press Escape, switch or minimize applications, close the editor, or perform any other desktop action while returning control to the host.
2. **Commit turn:** after explicit approval, the worker sends a second message in the same Holo session directing it to re-check the staged state, perform the final action, and verify visible success.

The local hackathon demo may set `FOUNDRY_AUTO_APPROVE=true`. In that mode, the initial start confirmation authorizes
the host to create an approval record immediately after the staged report passes structural and persisted-state checks;
the host then dispatches the commit turn without waiting for another dashboard or voice action. The approved payload
hash remains bound to the commit, and the same live Holo session must be used.

The demo launcher additionally sets `FOUNDRY_ONE_SHOT_DEMO=true`. This follows H Company's minimal examples pattern:
one selected window, one plain natural-language Holo task, and one final answer. Holo performs the requested edit and
persistent action in a single bounded turn; the host then verifies the exact persisted fixture state before reporting
success. The overlay and staged-answer parser are not involved in this demo path.

The staged-change summary must identify the target app, record, fields, proposed values, and evidence used for the summary. Rejection, cancellation, approval timeout, or loss of the live Holo session ends the run without attempting commit. A lost session requires a fresh run; the system must not create a new session solely to click Save on an unknown screen state.

Every live Holo turn writes a per-run `holo_diagnostics.jsonl` beside the canonical run artifacts. Diagnostics include
turn boundaries, runtime event kinds, tool requests and coordinates, viewport/cursor metadata, state changes, timing,
and the final answer. Raw screenshots, image payloads, authorization headers, tokens, and API keys are excluded. Safe
action summaries are also published as ordered run events so the dashboard shows actual Holo progress between
heartbeats.

For demo visibility, the CRM may render a click-through Holo overlay from sanitized diagnostic metadata. The overlay
draws a red border around the CRM observation surface, the current system pointer, a red crosshair for normalized
pointer targets, and the current tool/element label. It must never consume input, expose screenshots or credentials, or
become a separate focusable macOS application. Keyboard-only actions display their tool label without inventing a
pointer target. The action overlay disappears as soon as the corresponding tool completes so it does not obstruct or
mislead the agent's next observation.

The live launcher must fail before staging when the fixture app bundle is missing, cannot be launched, or its process
exits during startup. Resizing or full-screening a fixture is not required.

### 5.9 Cross-app transfer

The repository ships two native PySide6 CRM fixtures:

- CRM A is the taught application shown in the source demonstration.
- CRM B exposes equivalent records and business fields through different navigation, labels, and layout.

The same approved bundle must run on either application. A CRM B run receives no CRM B demonstration, coordinates, or
selectors. On a demo machine where screen-capture and pointer-event coordinate spaces do not align, the host may supply
the fixture's standard keyboard shortcuts as a coordinate-free navigation fallback.

Execution guidance may describe portable interaction semantics needed across layouts: select the exact matching record,
verify whether its fields are editable, and, when selection exposes only a read-only row or summary, activate a visible
Open Record, Edit, or View Details action before changing fields. CRM B also supports the conventional double-click on
an exact matching result so execution does not depend on a control near a macOS screen corner. Execution must not invoke
Mission Control or interact with the dashboard, browser, ChatGPT, or another unrelated window while operating the CRM.
This guidance must not encode coordinates, row indices, or fixture-specific selectors.

For the live demo, CRM B starts with its exact-search field focused. Submitting one exact name opens that record and
focuses its Family name field with the existing value selected; Enter activates the editor's default commit button.
This lets Holo complete the visible workflow using text input and Enter only, avoiding unreliable pointer coordinates
and the runtime's sticky modifier-key behavior. The Meridian prompt omits conflicting pointer-oriented skill
instructions and uses a reduced step budget so an agent that ignores the text-only contract fails quickly.

CRM B's record editor is application-modal and remains above unrelated applications while it contains unsaved staged
values. This prevents a focus change from redirecting a correctly targeted editor action into the dashboard or ChatGPT;
it does not persist data or bypass commit approval.

Both fixtures provide test-only seed, reset, and persisted-state inspection commands. Those commands are for evaluation and must not be exposed to Holo during live execution.

## 6. Explicit non-goals

The MVP does not include:

- cloud or multi-user deployment;
- authentication, organizations, or role-based access;
- scheduling, recurring runs, or unattended background automation;
- Windows or Linux host support;
- mobile automation;
- always-listening voice interaction;
- arbitrary or network-capable generated code;
- concurrent HoloDesktop executions;
- production integrations with Salesforce, HubSpot, or another real CRM;
- cross-domain transfer between unrelated business processes;
- automatic approval or automatic final submission;
- automatic self-modification from failed runs;
- production compliance certification, enterprise retention controls, or high availability.

## 7. End-to-end user flows

### Flow A: Create an automation

1. User selects New Automation.
2. User enters a task name and uploads supported sources.
3. User accepts the provider-disclosure notice.
4. Backend validates and stores sources.
5. Backend derives transcript, frames, and SOP text.
6. The backend prepares normalized evidence and attaches the original video when present.
7. Gemini through OpenRouter generates version 1 of the bundle.
8. Structural and generated-tool validation runs.
9. Automation moves to `review_required` or `failed` with remediation details.

### Flow B: Review and approve

1. User opens the generated version.
2. UI shows artifacts beside evidence references and conflicts.
3. User edits or resolves issues.
4. Validation reruns after edits.
5. User approves the valid version.
6. Backend records approval identity, timestamp, and artifact hashes.
7. Approved `SKILL.md` is atomically published to the app-owned Holo skill directory.
8. Automation becomes runnable.

### Flow C: Run from dashboard

1. User selects an approved automation, target app, and inputs.
2. Backend validates the approved hashes and inputs.
3. User clicks Run.
4. Holo performs the stage turn.
5. UI presents the staged-change summary.
6. User approves or rejects commit.
7. Approval resumes the same Holo session; rejection cancels it.
8. Backend records and reports the verified result.

### Flow D: Run by voice

1. User holds push-to-talk and speaks a command.
2. UI displays the transcript.
3. Hermes resolves intent and requests clarification if needed.
4. Gradium TTS and the UI present the complete preview.
5. User confirms the preview.
6. The run follows the same stage and commit flow as dashboard invocation.

### Flow E: Cancel or recover from failure

1. User cancels in the UI, speaks a cancellation, or uses the Holo kill switch.
2. Host worker sends cancellation to the active session.
3. Run transitions to `cancelled` or `failed` with its last safe state.
4. No automatic commit or automatic retry occurs.
5. The user may start a fresh run after the cause is addressed.

## 8. Functional requirements

### Authoring

- **FR-A01:** The system shall persist every accepted source under a stable automation ID.
- **FR-A02:** The system shall expose ingestion progress and terminal failure details.
- **FR-A03:** Every generated step shall support zero or more evidence references.
- **FR-A04:** Material source conflicts shall block approval until resolved.
- **FR-A05:** The author shall be able to edit every generated runnable artifact.
- **FR-A06:** Approval shall bind to immutable artifact hashes and a version number.
- **FR-A07:** Edits after approval shall prevent execution until revalidated and reapproved.

### Execution

- **FR-E01:** Only an approved, hash-matching version shall be executable.
- **FR-E02:** The system shall enforce one active Holo run.
- **FR-E03:** Every run shall have explicit step and time budgets.
- **FR-E04:** The first Holo turn shall not perform the final persistent action.
- **FR-E05:** Commit shall require a recorded explicit approval.
- **FR-E06:** Commit shall continue the same live Holo session used for staging.
- **FR-E07:** Cancellation and session loss shall fail closed.
- **FR-E08:** The system shall preserve ordered run events and the terminal result.
- **FR-E09:** Live fixture execution shall activate and verify the selected named macOS app before Holo begins.

### Voice

- **FR-V01:** Provider credentials shall never be sent to browser JavaScript.
- **FR-V02:** Partial transcripts shall never be interpreted as approval.
- **FR-V03:** Ambiguous or incomplete commands shall result in clarification.
- **FR-V04:** The interpreted automation, target app, and inputs shall be previewed before start.
- **FR-V05:** Voice and dashboard invocation shall use the same run contracts and state machine.

### Cross-app evaluation

- **FR-X01:** Both desktop fixtures shall represent the same CRM operation with different UI structure.
- **FR-X02:** Both desktop fixtures shall expose a visible Add Record flow that remains unpersisted until its final
  Add Record, Save, or Commit control is activated.
- **FR-X03:** CRM B execution shall not use CRM B-specific demonstration artifacts.
- **FR-X04:** Persisted state shall be inspectable by the test harness but not by Holo.

## 9. Architecture and data flow

### Host components

- **React/Vite frontend:** dashboard, authoring editors, microphone capture, previews, approvals, and live events.
- **FastAPI backend:** REST/WebSocket API, source validation, job orchestration, metadata persistence, provider proxies, and run coordination.
- **Media processor:** frame extraction, optional transcript coordination, and SOP normalization.
- **Artifact store:** repo-local gitignored source, evidence, bundle, and run directories.
- **SQLite database:** automation index, versions, jobs, approvals, and run metadata.
- **Trusted Holo worker:** the only component allowed to invoke HoloDesktop and publish approved Holo skills.

### Sandboxed components

- **NemoClaw/OpenShell:** filesystem and network policy boundary.
- **Hermes:** optional workspace generation adapter and conversational intent resolver.
- **Automation MCP server:** bounded access to staged evidence, bundle submission, generated-tool validation, and file-based run queues.
- **Generated-tool runner:** restricted subprocess for approved pure-data functions.

### External services

- **OpenRouter API:** hosted Gemini video understanding and schema-constrained bundle generation.
- **HoloDesktop CLI/runtime:** visible desktop observation and control on macOS.
- **Gradium API:** optional legacy video transcription, push-to-talk STT, and response TTS.

### Trust boundary

The NemoClaw sandbox must not receive macOS Accessibility privileges or direct control of HoloDesktop. The host and sandbox exchange files and queue records through the mounted `/sandbox/workspace`. The host treats sandbox-produced files as untrusted until schema, size, path, and hash validation succeeds.

## 10. Public contracts

The initial shared models are:

- `AutomationManifest`: ID, name, lifecycle state, source metadata, version pointers, target policy, and timestamps.
- `AutomationVersion`: version number, artifact paths and hashes, validation summary, conflicts, and approval.
- `EvidenceReference`: source ID, source type, timestamp or page/section, and optional excerpt or frame path.
- `InputDefinition`: name, JSON type, description, required flag, constraints, and optional examples.
- `GeneratedTool`: name, description, entrypoint, input/output schemas, code hash, and limits.
- `ReviewConflict`: evidence references, description, severity, resolution, and blocking flag.
- `RunRequest`: automation/version IDs, target app, validated inputs, invocation source, and budgets.
- `RunPreview`: normalized intent, target, inputs, missing fields, and required confirmation.
- `RunEvent`: monotonically increasing sequence, timestamp, type, safe message, and typed payload.
- `StagedChange`: app, record identity, proposed field changes, visible verification, and Holo session ID reference.
- `ApprovalRecord`: run ID, approval type, source, timestamp, approved payload hash, and decision.
- `RunResult`: terminal state, answer, verification summary, timings, and redacted error.

Run states are:

`prepared` → `awaiting_start_confirmation` → `executing` → `awaiting_commit_approval` → `committing` → `succeeded`.

From any nonterminal state, a run may transition to `failed` or `cancelled`. No transition may skip `awaiting_commit_approval` for an action that changes persistent CRM state.

## 11. Automation folder model

```text
data/automations/<automation-id>/
├── manifest.json
├── source/
│   ├── video.<ext>
│   └── sop.<ext>
├── evidence/
│   ├── transcript.json
│   ├── sop.json
│   ├── frames/
│   └── observations.json
├── versions/
│   └── <version>/
│       ├── SOP.md
│       ├── SKILL.md
│       ├── inputs.schema.json
│       ├── tools.py
│       ├── tool_manifest.json
│       ├── eval_cases.json
│       ├── review.json
│       ├── approval.json
│       └── tests/test_tools.py
└── runs/
    └── <run-id>/
        ├── request.json
        ├── events.jsonl
        ├── staged_change.json
        ├── approval.json
        └── result.json
```

The database is the query index and job coordinator. Versioned files are the canonical runnable content. Startup reconciliation must detect database entries whose required files or hashes are missing.

## 12. Technical constraints

- macOS is the only supported MVP host.
- Python 3.12 or newer is required by the HoloDesktop client.
- Python dependencies use `uv`; frontend dependencies use npm.
- Backend uses FastAPI, Pydantic, and SQLite.
- Frontend uses React, Vite, and TypeScript.
- Desktop fixtures use PySide6.
- NemoClaw/Hermes requires Docker Desktop or Colima.
- Bidirectional workspace sharing requires macFUSE/SSHFS and `nemohermes <sandbox> share mount`.
- Provider keys are supplied through environment or provider credential stores and are never committed.
- The app is single-process for the MVP; interrupted in-process jobs are marked failed on restart.
- The existing H Company examples remain intact and outside the critical application path.

## 13. Security and privacy requirements

- Bind the application to `127.0.0.1` by default.
- Show provider disclosure before the first upload and retain the user's acknowledgement.
- Keep original uploads and derived artifacts local until explicit deletion.
- Send audio to Gradium only when the selected ingestion path explicitly requires transcription.
- Send only the accepted source video, normalized evidence/SOP text, and required instructions to OpenRouter.
- Never expose H Company, OpenRouter, or Gradium credentials to frontend code, logs, generated bundles, or Holo task text.
- Reject unsafe filenames, symlinks, path traversal, oversized files, and unsupported content.
- Validate every sandbox-produced path before reading or copying it on the host.
- Redact secrets and unrelated visible content from shared diagnostics.
- Require approval before skill publication and before every persistent run action.
- Preserve the Holo double-Esc kill switch and provide UI/voice cancellation.
- Do not automatically retry a failed commit turn.
- Do not use the mock CRM state-inspection helpers during Holo execution.

## 14. Edge cases and failure modes

| Condition | Required behavior |
| --- | --- |
| Unsupported, corrupt, oversized, or over-duration source | Reject before agent invocation and preserve no partial runnable version. |
| Video has no audio | Continue with visual evidence and mark the missing transcript. |
| SOP and video conflict | Create blocking review conflicts; do not choose silently. |
| Gradium unavailable | Retry only bounded transient failures for voice or legacy transcription; otherwise fail with remediation. |
| OpenRouter or Gemini unavailable/rate-limited | Preserve local evidence, mark the job failed/retryable, and create no approved bundle. |
| NemoClaw sandbox or shared mount unavailable | Fail closed before generation or run queuing. |
| Generated output violates schema | Reject it, preserve diagnostics, and allow regeneration. |
| Generated tool violates restrictions or times out | Mark validation failed and block approval. |
| Approved artifact hash changes | Invalidate approval and block execution. |
| Target app is absent or wrong | End stage turn as failed without attempting persistent action. |
| Holo permission missing | Fail preflight with macOS remediation instructions. |
| Holo reaches step/time budget | Cancel the session, report failure, and do not commit. |
| User rejects or ignores commit approval | Cancel after the approval timeout and preserve unchanged state. |
| Holo session is lost after staging | Fail the run; never create a new session merely to click Save. |
| App exits during a job | Mark active in-process jobs interrupted on restart; require an explicit retry. |
| Voice transcript is ambiguous or partial | Ask for clarification; never infer confirmation. |

## 15. Observability and retention

- Emit structured job and run events with stable IDs and timestamps.
- Store local model/request metadata necessary to reproduce failures without storing provider keys.
- Record model name, source hashes, prompt/template version, preprocessing parameters, artifact hashes, run budgets, and timing metrics.
- Keep Holo runtime diagnostics local and treat screenshots and event logs as sensitive.
- Delete an automation's uploads, evidence, versions, and runs only after explicit confirmation.
- A deletion failure must leave a visible tombstone or error rather than a partially hidden automation.

## 16. Unresolved decisions

There are no unresolved product decisions blocking MVP implementation. The following are implementation feasibility checks, not product choices:

- verify the configured OpenRouter account can invoke `google/gemini-3.5-flash` with inline video and structured output;
- verify the macOS shared-mount prerequisites on the demo machine;
- verify the chosen Gradium voice ID and H Company account have sufficient credits;
- pin compatible HoloDesktop, NemoClaw, Gradium SDK, and Python versions during the foundation phase.

If a feasibility check fails, implementation stops at the affected boundary and the specification is revised before substituting another provider or weakening the sandbox model.

## 17. Deferred ideas

- Per-automation policies that allow low-risk autonomous commits.
- Additional desktop workflow domains and real application sandboxes.
- Multiple demonstrations, branching workflows, and author-provided counterexamples.
- Scheduled and event-triggered runs.
- Windows and Linux support.
- Team accounts, shared bundle registries, approvals, and audit exports.
- Voice cloning, continuous conversational mode, and telephony channels.
- Production-grade secret management, retention policies, encryption, and compliance controls.
- Outcome-based learning from reviewed run failures.
- Hosted execution and organization-managed Holo environments.
