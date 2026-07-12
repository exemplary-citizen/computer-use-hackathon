# Computer-Use Automation Foundry — MVP Specification

## 1. Project overview

Computer-Use Automation Foundry is a local macOS application that learns repeatable desktop workflows from demonstration videos and standard operating procedures (SOPs). It converts those sources into reviewable automation bundles, then delegates execution to H Company's HoloDesktop agent under NemoClaw/Hermes orchestration. Telegram and Gradium are thin input surfaces for the same Hermes orchestrator: Telegram lets the owner submit videos, review and approve generated bundles, provide runtime inputs, and supervise runs from a phone; Gradium provides voice transcription and playback.

The product replaces the usual engineering-heavy automation discovery process with an authoring workflow designed for domain experts. An operations expert demonstrates what to do, reviews the generated procedure and tools, approves a version, and can later invoke it from the dashboard, by voice, or through an allowlisted Telegram direct message.

The MVP proves five capabilities together:

1. Extract a reliable semantic procedure from video and/or written SOP evidence.
2. Produce a reusable, versioned Holo skill and constrained data-processing tools.
3. Execute the learned operation safely in a native desktop application.
4. Transfer the same business operation zero-shot to a second CRM with a different interface.
5. Teach and supervise the same reusable workflow remotely through a private Telegram bot conversation.

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
- One thin local Telegram adapter with one bot and one numerically allowlisted operator account.
- One active HoloDesktop execution at a time.
- No dashboard application-level authentication because the service is loopback-only; Telegram requests are authenticated
  by sender policy and short-lived, action-bound callback tokens.

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
- Support reusable automation authoring and schema-driven runtime input collection through Telegram.
- Demonstrate zero-shot transfer from CRM A to CRM B.

### MVP success bar

- At least 90% aggregate recall of annotated critical ingestion steps.
- 100% recall of annotated confirmation boundaries.
- At least 4 exact successful runs out of 5 on each mock CRM.
- No persistent CRM mutation before approval in all 10 live execution trials.
- At least 9 correct automation-and-input interpretations out of 10 voice trials.
- All Telegram sender, disclosure, deduplication, and button-approval safety cases pass.
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
- extract timestamped audio and visual evidence from video;
- transcribe audio through Gradium with segment timestamps;
- extract normalized SOP text and stable page/section references;
- retain source hashes and preprocessing metadata;
- expose processing progress and actionable failures.

The implementation may reduce redundant video frames, but must preserve enough timestamped evidence to recover all gold critical steps. Frame selection parameters must be recorded with the ingestion job.

### 5.3 Agentic bundle generation

The backend stages the derived evidence in the NemoClaw shared workspace. Hermes, routed to hosted `holo3-122b-a10b`, analyzes evidence in bounded batches and generates a structured bundle.

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

The user selects an approved automation, target app, and runtime inputs. Clicking Run is the start confirmation for dashboard invocation. The system validates inputs and creates a run before Holo gains control.

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

The run has two Holo turns:

1. **Stage turn:** Holo opens the target app, locates the intended record, fills the requested values, visually checks the staged form, and ends the turn without activating Save, Commit, Submit, or an equivalent persistent action.
2. **Commit turn:** after explicit approval, the worker sends a second message in the same Holo session directing it to re-check the staged state, perform the final action, and verify visible success.

The staged-change summary must identify the target app, record, fields, proposed values, and evidence used for the summary. Rejection, cancellation, approval timeout, or loss of the live Holo session ends the run without attempting commit. A lost session requires a fresh run; the system must not create a new session solely to click Save on an unknown screen state.

### 5.9 Cross-app transfer

The repository ships two native PySide6 CRM fixtures:

- CRM A is the taught application shown in the source demonstration.
- CRM B exposes equivalent records and business fields through different navigation, labels, and layout.
- Both fixtures expose explicit text-labeled `Add Record` and `Edit Record` controls. Selecting a record alone never
  enables mutation, and creating or editing remains in memory until the fixture's Save/Commit control is activated.

The same approved bundle must run on either application. A CRM B run receives only the target app name and runtime data; it receives no CRM B demonstration, coordinates, selectors, or precomputed navigation profile.

Both fixtures provide test-only seed, reset, and persisted-state inspection commands. Those commands are for evaluation and must not be exposed to Holo during live execution.

### 5.10 Hermes orchestration surfaces

NemoClaw/Hermes is the sole agentic orchestrator for dashboard, Telegram, and Gradium interactions. Telegram and Gradium
are unprivileged surfaces: they may deliver media or transcripts and render deterministic responses, but they may not
interpret evidence into instructions, generate automation artifacts, invoke HoloDesktop directly, publish skills,
bypass validation, or mutate run state outside shared host capabilities.

The Foundry host capability layer exposes typed operations to Hermes for upload ingestion, authoring status, skill
approval, run preparation, run start, and staged-change commit. This layer is local infrastructure rather than a second
orchestrator. It owns deterministic authorization, storage, schema validation, callback consumption, and privileged Holo
dispatch. Hermes may request these operations, but the host independently enforces every precondition and approval.

After deterministic host validation and preprocessing, the existing `WorkspaceBridge` stages bounded evidence in the
NemoClaw workspace. Hermes running inside the NemoClaw sandbox performs evidence analysis and bundle generation, and its
output is treated as untrusted until host validation. The host may expose the workspace through a verified SSHFS mount
or publish each bounded job through NemoClaw's authenticated upload transport. If NemoClaw, its workspace transport,
Hermes, or the configured Holo3 route is unavailable, authoring fails closed with a retryable status and produces no
runnable bundle. No surface or host model substitutes for Hermes.

The Telegram MVP uses a thin deterministic host adapter because Hermes' native plugin API does not expose custom
Telegram callback handlers for Foundry's action/hash-bound buttons. The adapter accepts direct messages only from the
owner's numerically allowlisted Telegram user ID and rejects groups before media download or state lookup. It forwards
conversation turns to Hermes through the authenticated NemoClaw gateway and never performs model reasoning itself. The
bot token remains in a user-managed environment value or token file outside the repository and is never passed to model
context, generated artifacts, NemoClaw, or Holo.

Authoring through Telegram follows this flow:

1. Before the first provider-backed ingestion, the bot presents the provider disclosure and records acceptance through
   an inline button. A valid `/learn` video received before acceptance remains pending without being downloaded or
   processed; accepting the disclosure resumes that same upload without requiring the owner to resend it.
2. The owner sends `/learn <automation name>` with one supported video attachment.
3. The adapter copies the attachment into a host quarantine using an opaque ID and revalidates type, size, duration,
   name, and content instead of trusting Telegram metadata. Hermes may reference only that opaque ID when requesting
   ingestion through a typed Foundry capability.
4. The bot acknowledges the accepted upload without waiting for generation and reports background progress and terminal
   failure using the stable automation ID.
5. When generation and validation finish, the bot sends a compact review summary and bot-native **Review details**
   controls. It includes an **Approve automation** button only when no blocking conflict or validation error remains.
   Editing artifacts or resolving conflicts still happens in the loopback dashboard on the Mac, after which the bot
   refreshes the review against the new version.
6. Pressing the button approves the exact displayed version and artifact hashes. Stale, replayed, mismatched, expired,
   or unauthorized callbacks fail closed.

Running through Telegram follows this flow:

1. The owner sends `/run <automation name>` or selects a runnable automation from bot-provided buttons.
2. The bot asks for the target application and each missing required field from the approved input schema. It validates
   every response through the same normalization and validation path used by the dashboard.
3. The bot shows the complete automation, version, target, and normalized-input preview with **Start** and **Cancel**
   buttons. Text messages alone do not confirm start.
4. **Start** creates or confirms the run through the shared execution state machine. The bot posts status updates while
   Holo performs the stage turn.
5. The bot displays the staged-change summary with **Commit** and **Reject** buttons. Only a valid **Commit** callback
   for the current staged-change hash may resume the same live Holo session.
6. The bot reports the verified terminal result. Timeout, rejection, cancellation, session loss, or callback mismatch
   ends safely without a new commit session or automatic retry.

Telegram button payloads must be opaque references rather than trusted state. Server-side records bind each callback to
the Telegram user, chat, action, automation/version or run, payload hash, expiry, and one-time-use status.

## 6. Explicit non-goals

The MVP does not include:

- cloud or multi-user deployment;
- authentication, organizations, or role-based access;
- scheduling, recurring runs, or unattended desktop execution;
- Windows or Linux host support;
- mobile automation;
- always-listening voice interaction;
- arbitrary or network-capable generated code;
- concurrent HoloDesktop executions;
- production integrations with Salesforce, HubSpot, or another real CRM;
- cross-domain transfer between unrelated business processes;
- automatic approval or automatic final submission;
- automatic self-modification from failed runs;
- Telegram groups, public bots, and messaging channels other than Telegram;
- production compliance certification, enterprise retention controls, or high availability.

## 7. End-to-end user flows

### Flow A: Create an automation

1. User selects New Automation.
2. User enters a task name and uploads supported sources.
3. User accepts the provider-disclosure notice.
4. Backend validates and stores sources.
5. Backend derives transcript, frames, and SOP text.
6. Evidence is staged in the NemoClaw workspace.
7. Hermes/Holo3 generates version 1 of the bundle.
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

### Flow F: Teach and run through Telegram

1. The paired owner accepts the provider disclosure, then sends `/learn <name>` with a demonstration video.
2. The bot acknowledges the durable local upload and returns an automation ID while ingestion continues in the background.
3. The bot reports progress, then presents a validated, bot-native review summary and details.
4. The owner presses **Approve automation** for the exact generated version.
5. Later, the owner sends `/run <name>` and answers the schema-derived target and runtime-input prompts.
6. The owner presses **Start** on the complete preview.
7. Holo stages the operation and the bot presents the staged-change summary.
8. The owner presses **Commit** or **Reject**; commit continues only the same live Holo session.
9. The bot reports the verified result and retains no authority to start another run automatically.

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

### Voice

- **FR-V01:** Provider credentials shall never be sent to browser JavaScript.
- **FR-V02:** Partial transcripts shall never be interpreted as approval.
- **FR-V03:** Ambiguous or incomplete commands shall result in clarification.
- **FR-V04:** The interpreted automation, target app, and inputs shall be previewed before start.
- **FR-V05:** Voice and dashboard invocation shall use the same run contracts and state machine.

### Telegram

- **FR-T01:** Only a paired or numerically allowlisted Telegram owner in a direct message shall be accepted.
- **FR-T02:** The bot shall require provider-disclosure acceptance before provider-backed ingestion.
- **FR-T03:** A `/learn` video shall create a reusable automation and return its stable ID without blocking on generation.
- **FR-T04:** Telegram media shall pass through the same upload validation and local retention rules as dashboard media.
- **FR-T05:** Bundle approval shall require an inline-button callback bound to the displayed version and artifact hashes.
- **FR-T06:** The bot shall collect target application and missing runtime inputs from the approved input schema.
- **FR-T07:** Start shall require an inline-button callback bound to the complete normalized preview.
- **FR-T08:** Commit shall require a separate inline-button callback bound to the current staged-change hash and live session.
- **FR-T09:** Text, media captions, reactions, duplicate updates, and expired or stale callbacks shall never imply approval.
- **FR-T10:** Telegram and dashboard invocation shall use the same authoring services, run contracts, and state machine.
- **FR-T11:** The bot shall expose safe progress, clarification, cancellation, timeout, and terminal-failure responses.

### Cross-app evaluation

- **FR-X01:** Both desktop fixtures shall represent the same CRM operation with different UI structure.
- **FR-X02:** CRM B execution shall not use CRM B-specific demonstration artifacts.
- **FR-X03:** Persisted state shall be inspectable by the test harness but not by Holo.

## 9. Architecture and data flow

### Host components

- **React/Vite frontend:** dashboard, authoring editors, microphone capture, previews, approvals, and live events.
- **FastAPI backend:** REST/WebSocket API, source validation, job orchestration, metadata persistence, provider proxies, and run coordination.
- **Media processor:** frame/audio extraction, transcript coordination, and SOP normalization.
- **Artifact store:** repo-local gitignored source, evidence, bundle, and run directories.
- **SQLite database:** automation index, versions, jobs, approvals, and run metadata.
- **Trusted Holo worker:** the only component allowed to invoke HoloDesktop and publish approved Holo skills.
- **Foundry host capability layer:** typed, loopback-only operations used by Hermes to ingest sources, query authoring
  status, prepare runs, and request approved state transitions. It owns validation and privileged dispatch, not reasoning.
- **Telegram and Gradium adapters:** receive allowlisted direct messages or voice, forward conversational turns to
  Hermes, and render progress, review, and deterministic approval controls. They do not own agentic reasoning or
  privileged host operations.

### Sandboxed components

- **NemoClaw/OpenShell:** filesystem and network policy boundary.
- **Hermes:** orchestration agent and conversational intent resolver.
- **Holo3-122B-A10B:** hosted multimodal model used by Hermes for evidence interpretation.
- **Automation MCP server:** bounded access to staged evidence, bundle submission, generated-tool validation, and file-based run queues.
- **Generated-tool runner:** restricted subprocess for approved pure-data functions.

### External services

- **H Company Models API:** hosted Holo3 inference.
- **HoloDesktop CLI/runtime:** visible desktop observation and control on macOS.
- **Gradium API:** video transcription, push-to-talk STT, and response TTS.
- **Telegram Bot API:** inbound direct messages, video downloads, progress messages, and inline-button callbacks routed
  through the thin host adapter. Telegram necessarily receives and retains messages and media according to its own
  service behavior before the adapter downloads them.

### Trust boundary

The NemoClaw sandbox and surface adapters must not receive macOS Accessibility privileges or direct control of
HoloDesktop. The host stages bounded jobs below `/sandbox/workspace` through either a verified SSHFS mount or NemoClaw's
authenticated upload transport. Hermes uses authenticated, capability-limited host tools rather than privileged internal
objects. The host treats sandbox output, Telegram content, Gradium transcripts, callback payloads, and surface-provided
media paths as untrusted until their authorization, schema, size, path, and hash checks succeed.

The trusted host coordinates the privilege boundaries: surfaces handle transport, NemoClaw/Hermes owns agentic evidence
reasoning and orchestration, and the Holo worker owns visible desktop execution. No component may silently take over
another component's responsibility when that component is unavailable.

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
- `ChannelInteraction`: channel, sender/chat identity, interaction kind, referenced automation/run, expiry, payload hash,
  one-time-use status, and redacted delivery metadata.

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
- Python 3.12 is the pinned demo interpreter.
- The day-zero desktop-control probe uses the public `hai-agents[desktop]` local-control API before any adapter,
  dashboard, voice, or authoring integration is attempted.
- The probe runs one bounded, non-persistent TextEdit task. It is a feasibility gate, not an alternate execution path,
  and must not bypass the approved-bundle or commit-approval requirements.
- Python dependencies use `uv`; frontend dependencies use npm.
- Backend uses FastAPI, Pydantic, and SQLite.
- Frontend uses React, Vite, and TypeScript.
- Desktop fixtures use PySide6.
- NemoClaw/Hermes requires Docker Desktop or Colima.
- Workspace publication uses either macFUSE/SSHFS with `nemohermes <sandbox> share mount` or the authenticated
  `nemohermes <sandbox> upload` transport. Upload mode stages canonical host data locally and publishes only the bounded
  generation job directory; sandbox output still returns through the size-limited Hermes API response.
- Provider keys are supplied through environment or provider credential stores and are never committed.
- Telegram uses a pinned Bot API client in the thin host adapter. The user configures its bot token outside the
  repository; setup and diagnostics must never print it.
- Telegram ingestion uses polling for the local MVP and requires no public inbound webhook or exposed FastAPI port.
- Every Telegram-originated generation job must use the same NemoClaw workspace marker, transport checks, staged
  request, Hermes endpoint, result bounds, schema validation, and generated-tool sandbox as dashboard-originated
  generation.
- Direct model calls from a surface or the host as a fallback for failed NemoClaw/Hermes generation are prohibited.
- The app is single-process for the MVP; interrupted in-process jobs are marked failed on restart.
- The existing H Company examples remain intact and outside the critical application path.

## 13. Security and privacy requirements

- Bind the application to `127.0.0.1` by default.
- Show provider disclosure before the first upload and retain the user's acknowledgement.
- Keep dashboard uploads and all canonical source/artifact copies local until explicit deletion. A source intentionally
  submitted through Telegram has already traversed Telegram; only the minimum review/status content required for the bot
  workflow is sent back through Telegram.
- Send only audio required for transcription to Gradium.
- Send only selected frames, transcripts, SOP text, and instructions required for generation to hosted Holo3.
- Never expose H Company or Gradium credentials to frontend code, logs, generated bundles, or Holo task text.
- Never expose the Telegram bot token to models, NemoClaw, Foundry host capabilities or frontend, Holo, logs, callback
  data, or generated bundles; only the thin Telegram transport process may receive it.
- Include Telegram media/message handling in the provider disclosure before provider-backed processing begins.
- Restrict Telegram to owner-only direct messages; disable groups and reject every non-allowlisted sender before media copy.
- Treat Telegram captions, filenames, video content, and Gradium transcripts as untrusted input, never as privileged instructions.
- Make every approval callback short-lived, single-use, action-specific, identity-bound, and hash-bound.
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
| Gradium unavailable | Retry only bounded transient failures; otherwise fail ingestion or voice turn with remediation. |
| Holo3 unavailable or rate-limited | Preserve staged evidence, mark job failed/retryable, and create no approved bundle. |
| NemoClaw sandbox or workspace transport unavailable | Fail closed before generation or run queuing. |
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
| Telegram sender is not paired or allowlisted | Ignore or return a generic denial without copying media or revealing state. |
| Provider disclosure has not been accepted | Quarantine or reject the local attachment; do not begin provider processing. |
| Telegram video is missing, unsupported, corrupt, oversized, or too long | Reject before ingestion with a safe remediation message. |
| Telegram update is duplicated or delivered out of order | Deduplicate by update/message ID and preserve monotonic interaction state. |
| Runtime input is missing or invalid | Ask for the specific field again; do not create or start a run. |
| Approval callback is stale, replayed, expired, unauthorized, or hash-mismatched | Reject it without changing bundle or run state. |
| Telegram is unavailable during ingestion | Continue the local job and deliver current status after reconnection. |
| Telegram is unavailable while awaiting approval | Do not infer approval; let the configured approval timeout cancel safely. |
| NemoClaw, workspace transport, Hermes, or Holo3 route is unavailable | Fail Telegram ingestion closed; retain safe local source state and offer explicit retry without generating elsewhere. |

## 15. Observability and retention

- Emit structured job and run events with stable IDs and timestamps.
- Store local model/request metadata necessary to reproduce failures without storing provider keys.
- Record model name, source hashes, prompt/template version, preprocessing parameters, artifact hashes, run budgets, and timing metrics.
- Keep Holo runtime diagnostics local and treat screenshots and event logs as sensitive.
- Delete an automation's uploads, evidence, versions, and runs only after explicit confirmation.
- A deletion failure must leave a visible tombstone or error rather than a partially hidden automation.
- Persist Telegram update IDs, safe interaction state, callback consumption, and delivery status without storing bot tokens.
- Treat downloaded Telegram videos and chat metadata as sensitive local source data covered by the automation deletion flow.

## 16. Unresolved decisions

There are no unresolved product decisions blocking MVP implementation. The following are implementation feasibility checks, not product choices:

- verify the installed NemoClaw/Hermes version can pass local image evidence to the configured Holo3 endpoint;
- verify the public `hai-agents[desktop]` local-control example completes three consecutive bounded TextEdit runs on
  the demo machine before wiring a live execution adapter;
- verify one supported workspace transport on the demo machine;
- verify the chosen Gradium voice ID and H Company account have sufficient credits;
- verify the pinned Telegram client can receive video attachments and render inline buttons on the demo machine;
- pin compatible HoloDesktop, NemoClaw, Gradium SDK, and Python versions during the foundation phase.

If a feasibility check fails, implementation stops at the affected boundary and the specification is revised before substituting another provider or weakening the sandbox model.

## 17. Deferred ideas

- Per-automation policies that allow low-risk autonomous commits.
- Additional desktop workflow domains and real application sandboxes.
- Multiple demonstrations, branching workflows, and author-provided counterexamples.
- Scheduled and noninteractive event-triggered runs.
- Windows and Linux support.
- Team accounts, shared bundle registries, approvals, and audit exports.
- Voice cloning, continuous conversational mode, and telephony channels.
- Production-grade secret management, retention policies, encryption, and compliance controls.
- Outcome-based learning from reviewed run failures.
- Hosted execution and organization-managed Holo environments.
- WhatsApp and other messaging surfaces.
