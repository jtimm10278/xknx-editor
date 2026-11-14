#!/usr/bin/env bash
# Build the XKNX Editor as a standalone desktop app with PyInstaller.
#
#   apps/editor-gui/tools/build_desktop.sh [--zip]
#
# Output: apps/editor-gui/dist/  ("XKNX-Editor.app" on macOS, a folder elsewhere).
# PyInstaller is pulled in ephemerally (uv --with), so it is not a project dependency; typer as
# well, which collecting the "mcp" package needs (as the release workflow does).
#
# On macOS the app is built for the Mac's own architecture (arm64 on Apple silicon) and carries
# PyInstaller's ad-hoc signature only (no Apple Developer ID): the script checks that signature.
# --zip also packs it as the release does (ditto), e.g. dist/XKNX-Editor-macos-arm64.zip.
set -euo pipefail

zip=0
for arg in "$@"; do
    case "$arg" in
        --zip) zip=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")/.."  # -> apps/editor-gui

# Freeze the app from the spec (the icons are committed, see tools/make_icon.py).
uv run --package xknxeditor-gui --with pyinstaller --with typer \
    pyinstaller --noconfirm --clean xknx-editor.spec

if [[ "$(uname -s)" == "Darwin" ]]; then
    app="dist/XKNX-Editor.app"
    # Apple silicon runs no unsigned arm64 code; PyInstaller signs the bundle ad hoc.
    codesign --verify --deep --strict "$app"
    echo "Signature checked (ad hoc): $app ($(uname -m))"
    if [[ $zip -eq 1 ]]; then
        archive="dist/XKNX-Editor-macos-$(uname -m).zip"
        rm -f "$archive"
        ditto -c -k --keepParent "$app" "$archive"
        echo "Archive: $(pwd)/$archive"
    fi
    echo "First start: see docs/GETTING_STARTED.md (Gatekeeper)."
elif [[ $zip -eq 1 ]]; then
    echo "--zip is for macOS; the release workflow packs the other systems." >&2
fi

echo
echo "Done. Result in: $(pwd)/dist/"
