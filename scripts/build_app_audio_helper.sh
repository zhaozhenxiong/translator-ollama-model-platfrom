#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE_PATH="$PROJECT_ROOT/native/AppAudioHelper"
HELPER_PATH="$PACKAGE_PATH/.build/release/app-audio-helper"

swift build --package-path "$PACKAGE_PATH" -c release
codesign --force --sign - --identifier "local.live-translator.app-audio-helper" "$HELPER_PATH"
"$HELPER_PATH" self-test --json

printf 'Built %s\n' "$HELPER_PATH"
