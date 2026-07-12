#!/bin/bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"
BACKEND="$REPO_ROOT/.venv/bin/automation-foundry-api"
export FOUNDRY_DESKTOP_APP_ROOT="${FOUNDRY_DESKTOP_APP_ROOT:-/private/tmp/automation-foundry-desktop-apps}"
CRM_A_APP="$FOUNDRY_DESKTOP_APP_ROOT/NorthlightCRM.app"
CRM_B_APP="$FOUNDRY_DESKTOP_APP_ROOT/MeridianContacts.app"
CRM_A="$CRM_A_APP/Contents/MacOS/NorthlightCRM"
CRM_B="$CRM_B_APP/Contents/MacOS/MeridianContacts"
LOG_ROOT="${FOUNDRY_DEMO_LOG_ROOT:-/private/tmp/automation-foundry-demo}"
CHILD_PIDS=()
CLEANING_UP=0

fail() {
    echo "error: $*" >&2
    exit 1
}

port_is_busy() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

cleanup() {
    if [[ "$CLEANING_UP" -eq 1 ]]; then
        return
    fi
    CLEANING_UP=1
    trap - EXIT INT TERM HUP
    echo
    echo "Stopping Automation Foundry demo..."

    for pid in "${CHILD_PIDS[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done

    pkill -TERM -f 'desktop_fixtures\.crm_[ab]' 2>/dev/null || true
    pkill -TERM -f "$FOUNDRY_DESKTOP_APP_ROOT/.+\.app/Contents/MacOS/" 2>/dev/null || true
    pkill -TERM -f "$REPO_ROOT/web/node_modules/.bin/vite" 2>/dev/null || true
    pkill -TERM -f "$BACKEND" 2>/dev/null || true
    pkill -TERM -f 'hai-agent-runtime' 2>/dev/null || true
    sleep 1

    for pid in "${CHILD_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    for pid in $(pgrep -f 'hai-agent-runtime' 2>/dev/null || true); do
        kill -KILL "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo "Stopped. Logs remain in $LOG_ROOT"
}

trap cleanup EXIT
trap 'exit 130' INT TERM HUP

[[ -x "$PYTHON" ]] || fail "missing virtual environment; run UV_PYTHON=3.12 uv sync first"
[[ -x "$BACKEND" ]] || fail "missing automation-foundry-api; run UV_PYTHON=3.12 uv sync first"
[[ -x "$CRM_A" && -x "$CRM_B" ]] || fail "missing CRM app bundles; run ./scripts/build_desktop_apps.sh first"
[[ -d "$REPO_ROOT/web/node_modules" ]] || fail "missing web dependencies; run cd web && npm ci first"
command -v npm >/dev/null 2>&1 || fail "npm is not installed"
command -v security >/dev/null 2>&1 || fail "macOS security command is unavailable"
command -v lsof >/dev/null 2>&1 || fail "lsof is unavailable"

port_is_busy 8000 && fail "port 8000 is already in use; stop the existing backend first"
port_is_busy 5173 && fail "port 5173 is already in use; stop the existing Vite server first"

if [[ -z "${FOUNDRY_OPENROUTER_API_KEY:-}" ]]; then
    FOUNDRY_OPENROUTER_API_KEY="$(
        security find-generic-password -a "$USER" -s automation-foundry-openrouter -w 2>/dev/null
    )" || fail "OpenRouter key not found in Keychain service automation-foundry-openrouter"
fi
export FOUNDRY_OPENROUTER_API_KEY

export FOUNDRY_GENERATION_PROVIDER="${FOUNDRY_GENERATION_PROVIDER:-openrouter_video}"
export FOUNDRY_OPENROUTER_MODEL="${FOUNDRY_OPENROUTER_MODEL:-google/gemini-3.5-flash}"
export FOUNDRY_DATA_ROOT="${FOUNDRY_DATA_ROOT:-/private/tmp/automation-foundry-authored/automations}"
export FOUNDRY_DATABASE_PATH="${FOUNDRY_DATABASE_PATH:-/private/tmp/automation-foundry-authored/automation_foundry.sqlite3}"
export FOUNDRY_PUBLISHED_SKILL_ROOT="${FOUNDRY_PUBLISHED_SKILL_ROOT:-/private/tmp/automation-foundry-authored/holo-skills}"
export FOUNDRY_WORKSPACE_MOUNT="${FOUNDRY_WORKSPACE_MOUNT:-/private/tmp/automation-foundry-authored/workspace}"
export FOUNDRY_WORKSPACE_REQUIRE_MOUNT="${FOUNDRY_WORKSPACE_REQUIRE_MOUNT:-false}"
export FOUNDRY_BUNDLE_PATH="${FOUNDRY_BUNDLE_PATH:-/private/tmp/automation-foundry-authored/automations/16a9cb6e-feab-44e9-99e9-cf8d5f1acdf1/approved_bundle.json}"
export FOUNDRY_RUNS_ROOT="${FOUNDRY_RUNS_ROOT:-/private/tmp/automation-foundry-live/runs}"
export FOUNDRY_EXECUTION_DATABASE_PATH="${FOUNDRY_EXECUTION_DATABASE_PATH:-/private/tmp/automation-foundry-live/execution.sqlite3}"
export FOUNDRY_FIXTURE_DATA_ROOT="${FOUNDRY_FIXTURE_DATA_ROOT:-/private/tmp/automation-foundry-live/fixtures}"
export FOUNDRY_HOLO_MODE="${FOUNDRY_HOLO_MODE:-live}"
export FOUNDRY_LAUNCH_FIXTURE_ON_RUN="${FOUNDRY_LAUNCH_FIXTURE_ON_RUN:-true}"
export FOUNDRY_ONE_SHOT_DEMO="${FOUNDRY_ONE_SHOT_DEMO:-true}"
export FOUNDRY_VOICE_ENABLED="${FOUNDRY_VOICE_ENABLED:-false}"
unset FOUNDRY_HOLO_OVERLAY_PATH

[[ -f "$FOUNDRY_BUNDLE_PATH" ]] || fail "approved bundle not found: $FOUNDRY_BUNDLE_PATH"
mkdir -p "$LOG_ROOT"

"$BACKEND" >"$LOG_ROOT/backend.log" 2>&1 &
BACKEND_PID=$!
CHILD_PIDS+=("$BACKEND_PID")

(
    cd "$REPO_ROOT/web"
    exec npm run dev -- --host 127.0.0.1
) >"$LOG_ROOT/web.log" 2>&1 &
WEB_PID=$!
CHILD_PIDS+=("$WEB_PID")

backend_ready=0
web_ready=0
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/api/authoring/health >/dev/null 2>&1; then
        backend_ready=1
    fi
    if curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
        web_ready=1
    fi
    if [[ "$backend_ready" -eq 1 && "$web_ready" -eq 1 ]]; then
        break
    fi
    kill -0 "$BACKEND_PID" 2>/dev/null || fail "backend exited; see $LOG_ROOT/backend.log"
    kill -0 "$WEB_PID" 2>/dev/null || fail "web server exited; see $LOG_ROOT/web.log"
    sleep 1
done

[[ "$backend_ready" -eq 1 ]] || fail "backend did not become ready; see $LOG_ROOT/backend.log"
[[ "$web_ready" -eq 1 ]] || fail "dashboard did not become ready; see $LOG_ROOT/web.log"

echo "Automation Foundry is running."
echo "Dashboard: http://127.0.0.1:5173"
echo "Logs: $LOG_ROOT"
echo "Press Ctrl+C to stop the backend, dashboard, selected CRM, and Holo runtime."

while true; do
    kill -0 "$BACKEND_PID" 2>/dev/null || fail "backend exited; see $LOG_ROOT/backend.log"
    kill -0 "$WEB_PID" 2>/dev/null || fail "web server exited; see $LOG_ROOT/web.log"
    sleep 1
done
