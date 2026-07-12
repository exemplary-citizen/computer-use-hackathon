#!/bin/bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"
OUTPUT_ROOT="${FOUNDRY_DESKTOP_APP_ROOT:-/private/tmp/automation-foundry-desktop-apps}"
REBUILD=0

if [[ "${1:-}" == "--rebuild" ]]; then
    REBUILD=1
elif [[ "$#" -gt 0 ]]; then
    echo "usage: $0 [--rebuild]" >&2
    exit 2
fi

fail() {
    echo "error: $*" >&2
    exit 1
}

build_app() {
    local entrypoint="$1"
    local output_name="$2"
    local app_name="$3"
    local bundle_id="$4"

    local target_bundle="$OUTPUT_ROOT/$output_name.app"
    if [[ -d "$target_bundle" ]]; then
        if [[ "$REBUILD" -eq 0 ]]; then
            echo "$app_name is already built at $target_bundle"
            return
        fi
        local backup_root="$OUTPUT_ROOT/backups"
        mkdir -p "$backup_root"
        mv "$target_bundle" "$backup_root/$output_name-$(date +%Y%m%d-%H%M%S)-$$-$RANDOM.app"
    fi

    "$PYTHON" -m nuitka \
        --assume-yes-for-downloads \
        --macos-create-app-bundle \
        --macos-app-name="$app_name" \
        --macos-signed-app-name="$bundle_id" \
        --macos-prohibit-multiple-instances \
        --enable-plugins=pyside6 \
        --include-package=desktop_fixtures \
        --output-dir="$OUTPUT_ROOT" \
        --output-filename="$output_name" \
        "$REPO_ROOT/$entrypoint"

    local entrypoint_name
    entrypoint_name="$(basename "$entrypoint" .py)"
    local generated_bundle="$OUTPUT_ROOT/$entrypoint_name.app"
    [[ -d "$generated_bundle" ]] || fail "Nuitka did not create the expected bundle: $generated_bundle"
    mv "$generated_bundle" "$target_bundle"
}

[[ "$(uname -s)" == "Darwin" ]] || fail "desktop app bundles can only be built on macOS"
[[ -x "$PYTHON" ]] || fail "missing virtual environment; run UV_PYTHON=3.12 uv sync first"
"$PYTHON" -c 'import nuitka' >/dev/null 2>&1 || fail "Nuitka is missing; run UV_PYTHON=3.12 uv sync"

mkdir -p "$OUTPUT_ROOT"
build_app desktop_fixtures/crm_a_app.py NorthlightCRM "Northlight CRM" ai.automationfoundry.northlight
build_app desktop_fixtures/crm_b_app.py MeridianContacts "Meridian Contacts" ai.automationfoundry.meridian

echo "Desktop app bundles are ready in $OUTPUT_ROOT"
