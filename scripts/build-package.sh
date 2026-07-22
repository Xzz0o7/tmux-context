#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
version=$(tr -d '[:space:]' < "$repo_dir/VERSION")

if ! command -v zip >/dev/null 2>&1; then
    printf '%s\n' "zip is required to build the distribution archive." >&2
    exit 1
fi

mkdir -p "$repo_dir/dist"
archive="$repo_dir/dist/tmux-context-$version.zip"
(
    cd "$repo_dir"
    zip -qr "$archive" \
        AGENTS.md README.md VERSION LICENSE-REQUIRED.md \
        tmux-context tmux-context.ps1 install.sh Install-TmuxContext.ps1 tmux_context.py \
        scripts tests \
        -x '*/__pycache__/*' '*.py[cod]' 'dist/*'
)
printf '%s\n' "$archive"
