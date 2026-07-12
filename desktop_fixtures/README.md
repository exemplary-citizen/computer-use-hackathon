# Desktop fixtures ownership boundary

Member 2 owns the CRM A and CRM B implementations in this directory.

Member 1 may define shared fixture contracts or consume reset/state-inspection commands during final integration, but must not implement the desktop applications or Holo execution behavior here.

## Applications

| | CRM A — "Northlight CRM" (taught) | CRM B — "Meridian Contacts" (zero-shot target) |
|---|---|---|
| Navigation | Left-hand contact list | Top tab bar + search-driven lookup |
| Editing | Inline detail form | Modal "Record" dialog |
| Persist action | Bottom-right **Save** | Dialog **Commit Changes** |
| Labels | First Name / Last Name / Company / Phone / Email / Status / Owner / Notes | Given name / Family name / Organisation / Contact No. / E-mail address / Stage / Account manager / Remarks |

Both apps present the same seeded records (canonical schema in `store.py`; label equivalence in `FIELD_LABELS`). Vision-legibility contract: 14px text, high-contrast light theme forced (independent of macOS dark mode), text-labeled buttons only, fixed 1280×800 window at (80, 60).

Live Holo execution uses compiled macOS application bundles so each fixture has a stable bundle identifier and can be
activated through LaunchServices. Build or refresh them before starting the demo stack:

```bash
./scripts/build_desktop_apps.sh --rebuild
```

Generated bundles live under `/private/tmp/automation-foundry-desktop-apps/` by default, outside macOS's protected
Desktop folder. A rebuild preserves previous bundles under that directory's `backups/` folder. Override the location
with `FOUNDRY_DESKTOP_APP_ROOT` when needed.

When `FOUNDRY_HOLO_OVERLAY_PATH` is configured, the selected fixture renders a click-through red Holo visualization
from sanitized action metadata tagged with that fixture's app key. An open non-target fixture ignores the overlay. The
overlay never accepts input or opens a separate app.

## Persistence contract (safety-relevant)

- Apps read their JSON state file once at startup and edit **in memory only**.
- The explicit Save / Commit Changes action is the **only** writer; it rewrites the whole file atomically (temp + rename). No autosave, no write on close.
- The eval harness restarts the app for every trial; `reset` while an app is running does not change what that running app displays.

## Test-only commands (never expose to Holo — FR-X03)

```bash
uv run python -m desktop_fixtures.cli reset --app a     # restore canonical seed
uv run python -m desktop_fixtures.cli dump  --app a     # persisted state as stable JSON (exit 1 if missing)
uv run python -m desktop_fixtures.cli where --app b     # state-file path
uv run python -m desktop_fixtures.cli launch --app b    # run the application
```

State files default to `data/desktop_fixtures/crm_{a,b}.json` (override with `FOUNDRY_FIXTURE_DATA_ROOT` or `--data-root`). Tests: `uv run pytest tests/desktop_fixtures` (headless via `QT_QPA_PLATFORM=offscreen`).
