#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
chmod +x "$script_dir/tmux-context"
exec "$script_dir/tmux-context" install
