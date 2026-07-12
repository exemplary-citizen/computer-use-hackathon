# AGENTS.md — Computer-Use Automation Foundry

## Project Purpose

This repository is being extended from H Company's computer-use examples into a macOS-local application that learns desktop workflows from videos and SOPs, produces reviewable automation bundles, and executes approved workflows through NemoClaw/Hermes and HoloDesktop. Gradium provides transcription and push-to-talk voice capabilities. An owner-only Telegram surface supports remote video ingestion, review, schema-driven runtime input collection, and button-gated execution through the same Hermes orchestrator.

The existing H Company examples remain useful references and should not be removed or broadly rewritten unless a task explicitly requires it.

## Sources of Truth

- Read `docs/SPEC.md` and `docs/EVALS.md` before substantial implementation work.
- `docs/SPEC.md` defines product behavior, scope, interfaces, safety constraints, and failure handling.
- `docs/EVALS.md` defines the checks required before work can be declared complete.
- `docs/PROJECT_PLAN.md` records the approved project-level plan.
- `docs/PLAN_MEMBER1.md` and `docs/PLAN_MEMBER2.md` define ownership and merge boundaries.
- `docs/PLAN_TELEGRAM_HERMES.md` defines the phased Telegram/Gradium surface integration and existing NemoClaw reuse.
- When an implementation request materially changes product behavior, update the specification and eval criteria before implementing the change.

## Important Directories

Existing directories:

- `examples/` — H Company SDK reference implementations; not the MVP runtime.
- `skills/` — existing H Company integration skills.
- `nemoclaw/` — existing NemoClaw/Hermes integration assets and the future sandbox customization boundary.
- `docs/` — product specification, evals, project plan, and member plans.

Planned application directories must follow the ownership boundaries in the member plans:

- backend authoring modules — upload, evidence, ingestion, versioning, and approval;
- backend execution modules — Holo adapter, run state machine, approvals, and events;
- `src/automation_foundry/surfaces/` — thin Telegram and Gradium adapters; never store bot tokens or downloaded runtime
  media in source directories;
- `web/src/features/authoring/` — Member 1 UI ownership;
- `web/src/features/execution/` — Member 2 UI ownership;
- `desktop_fixtures/` — native CRM A and CRM B fixtures;
- test directories grouped by shared contracts, authoring, execution, desktop fixtures, and end-to-end behavior.

## Architectural Constraints

- The FastAPI service is local-only and binds to `127.0.0.1` by default.
- The NemoClaw sandbox must not receive macOS Accessibility privileges or direct HoloDesktop control.
- Only the trusted host execution worker may invoke HoloDesktop or publish approved Holo skills.
- Telegram and Gradium are unprivileged surfaces and must not receive macOS Accessibility, Screen Recording, Input
  Monitoring, direct HoloDesktop control, or skill-publication authority.
- NemoClaw/Hermes is the sole agentic orchestrator. Telegram- and Gradium-originated generation must use the same
  `WorkspaceBridge`, NemoClaw workspace, sandboxed Hermes/Holo3 route, host validation, and generated-tool runner as
  dashboard-originated generation.
- NemoClaw/Hermes is mandatory for agentic authoring. If the sandbox, mount, Hermes endpoint, or Holo3 route is unavailable,
  fail closed and require an explicit retry; never bypass it with a direct host or surface-owned model call.
- Only approved, hash-matching automation versions may execute.
- Persistent desktop actions require a separate commit approval after staging.
- A lost Holo session must never be replaced solely to click Save or another final action.
- Generated Python tools are pure-data functions executed only inside NemoClaw with restricted imports and resources; they may not use network, subprocess, arbitrary files, dynamic execution, or desktop control.
- Browser code must never receive provider API keys.
- Telegram uses owner-only direct messages with numeric pairing/allowlisting; groups remain disabled for the MVP.
- Telegram text, captions, filenames, media, Gradium transcripts, and callback payloads are untrusted input.
- Telegram automation approval, run start, and commit require distinct short-lived, single-use, identity-bound,
  action-bound, and hash-bound inline-button callbacks. Text messages, reactions, duplicate updates, and stale callbacks
  never imply approval.
- Telegram-originated authoring and execution must call the shared host services and state machine rather than duplicate
  validation, approval, or run-transition logic.
- The Telegram bot token belongs in a user-managed environment value or token file outside the repository; never read,
  print, log, copy, commit, or expose it to the browser, NemoClaw, generated bundles, or Holo.
- Original sources and derived artifacts remain local until explicit deletion, subject to the provider disclosures in `docs/SPEC.md`.
- Preserve existing examples and avoid unrelated repository-wide refactors.

## Frontend Conventions

- Use React, Vite, and TypeScript for the planned local dashboard.
- Keep authoring and execution features in their assigned feature directories.
- Keep provider credentials and privileged operations in the backend.
- Use the shared backend contracts or generated TypeScript equivalents rather than duplicating state strings and payload shapes.
- Partial voice transcripts must never directly trigger or approve an action.

## Required Checks Before Completion

- Run every applicable automated command listed in `docs/EVALS.md`.
- Add or update tests for changed behavior, including failure and safety paths.
- Run `git diff --check` and verify no secrets, generated runtime data, uploads, screenshots, or provider logs are staged.
- For desktop or provider integrations, record the applicable manual/live verification from `docs/EVALS.md`.
- Do not declare completion when required checks are skipped, failing, or not reproducible.
- Keep changes focused and avoid unrelated modifications.

## Working with Users

- **Clarify first**: when requirements are ambiguous or under-specified, ask the user for clarifications before establishing a plan or writing code.

## Development Setup

- **uv** for package management (required version ≥0.9.18)
  - `uv sync` — sync dependencies
  - **Never use `pip` directly** — always use `uv` commands; if you must install a package ad-hoc, use `uv pip install`
- Copy `.env.example` to `.env` and configure
- **Never read `.env`** — it contains credentials; if a value needs to be set or changed, instruct the user to edit `.env` directly

## Code Style

### General

- **Formatter**: Ruff, line length **120**, Google-style docstrings (non-default)
- **Modularity**: keep functions short and single-purpose; use guard clauses at the top to handle edge cases early; split complex logic into well-named helpers
- **Naming**: use explicit, descriptive names; follow standard Python casing — `snake_case` for variables/functions, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants
- **Imports**: keep all imports at the top of the file; only use lazy (inline) imports when there is a clear performance reason (e.g., heavy dependency in a rarely-used code path)
- **Collections for constants**: use tuples for fixed-structure literals that won't be mutated (e.g., pairs iterated together: `((source_a, dest_a), (source_b, dest_b))`). Reserve lists for collections that are actually mutated or semantically variable-length. Tuples signal immutability to the reader and are marginally faster to iterate.
- **Error handling**: no unnecessary `try`/`except` blocks; merge adjacent `try`/`except` blocks in the same function unless they genuinely recover differently. Back-to-back cleanup `except`s that all just log-and-continue (or all just `pass`) should be a single block — separate handlers imply different recovery, so the structure should match the intent.

### Comments & docstrings

- **Comments**: only comment genuinely non-obvious logic — well-named, modular code should not need them
- **Docstrings**: public functions and `__init__` methods need a Google-style `Args:` section (plus `Returns:` / `Raises:` when relevant). A class-level docstring does not substitute for an `__init__` `Args:` block. Trivial private helpers can skip it when the signature is self-explanatory.

### Classes & file structure

- **Classes**: only use classes when truly relevant — prefer plain functions for stateless logic; no attribute-less classes; prefer module-level functions over methods that don't use `self`
- **Private placement**: class methods prefixed `_` go at the **end** of the class; module-level private classes/functions go **after** all public ones (see file structure below)
- **File structure**: enforce this top-to-bottom order in every Python file:
  1. Module docstring
  2. Imports (stdlib → third-party → local)
  3. Constants
  4. Public classes
  5. Public functions
  6. Private classes
  7. Private functions
  8. `main` (if applicable) — placed immediately above the `if __name__ == "__main__":` block so the entry point sits next to its invocation
  9. `if __name__ == "__main__":` (if applicable)

### Pydantic configs

When a runtime class takes non-trivial configuration — a tyro CLI, many fields, or sub-configs to compose — pair it with a Pydantic config and a `.make()` factory:

```python
class MyClassConfig(BaseModel):
    name: str
    """Display name used in logs."""
    sub_config: SomeOtherConfig | None = None
    """Optional helper config; when set, builds MyClass.helper."""

    def make(self) -> MyClass:
        return MyClass(self)

class MyClass:
    def __init__(self, config: MyClassConfig):
        self.config = config
        self.helper = config.sub_config.make() if config.sub_config else None
```

- **Single `config` arg**: `__init__` takes only `config: MyClassConfig` and stores it as `self.config` — do not unwrap fields into separate `__init__` args.
- **Defaults live on the config**: all argument definitions and defaults go on the `BaseModel`, never on `__init__`.
- **Sub-configs via `.make()`**: in `__init__`, call `.make()` on each sub-config and attach the result as an attribute.
- **`make` signature**: always `def make(self) -> MyClass: return MyClass(self)`.
- **Field docstrings**: triple-quoted strings under each field (shown above), not `# inline comments`.
- **Skip the pattern for small classes**: if the class has only 1–2 parameters, no tyro CLI, and no sub-configs, pass the parameters directly to `__init__`. A config whose fields get unpacked at the call site adds indirection without value.

### CLI scripts

- **Parsers**: `tyro` is preferred over `click`; never use `argparse`
- **Two valid shapes**:
  - **Function-based** (preferred for short demo CLIs) — pass plain functions to `tyro.extras.subcommand_cli_from_dict({"name": fn, ...})`. Each subcommand is one top-level function; tyro derives the parser from its signature and docstring. Used by every CLI in this repo.
  - **Pydantic-config** (use when a CLI has many options, sub-configs, or composes with non-CLI callers) — define a `BaseModel` config, parse with `tyro.cli(Config)`, hand the config to the runtime class via `.make()`. File order in that case: module docstring → config model → `tyro.cli()` → `logging` setup.
- **Output**: use `logging` for operational messages and `print` only to display results to the user

## Useful Commands

| Task          | Command                      |
| ------------- | ---------------------------- |
| Sync deps     | `uv sync`                    |
| Lint check    | `ruff check .`               |
| Lint fix      | `ruff check --fix .`         |
| Format        | `ruff format .`              |
| Type check    | `mypy`                       |
| Run tests     | `pytest <path>`              |
| Pre-commit    | `pre-commit run --all-files` |
