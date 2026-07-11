"""Native mock CRM fixtures for live desktop-execution evals.

Two visually and structurally distinct PySide6 applications expose the same
business records:

- CRM A (``crm_a`` — "Northlight CRM"): the taught application. Left-hand
  contact list, inline detail form, bottom-right "Save" button.
- CRM B (``crm_b`` — "Meridian Contacts"): the zero-shot transfer target.
  Top tab bar, search-driven lookup, modal edit dialog with "Commit Changes".

Persistence contract (load-bearing for the approval-safety evals):

- Apps read their JSON state file once at startup and edit **in memory only**.
- The ONLY code path that writes persisted state is the explicit Save /
  Commit Changes action, which rewrites the whole file atomically
  (temp file + rename). There is no autosave and no write on close.
- The eval harness restarts the app for every trial; ``reset`` while an app
  is running does not change what that running app displays.
- The ``crm-fixture`` CLI (``python -m desktop_fixtures.cli``) provides the
  test-only seed/reset/dump commands. It must never be exposed to Holo.
"""
