# tmux-context

## Scope

- This repository is standalone. Do not import `cs_manager` or depend on `~/.cs`.
- The runtime targets Linux, WSL2, and macOS (Codex only). Native PowerShell support is a bridge into WSL, not a native tmux implementation.
- Keep the runtime dependency-free: Python standard library plus `tmux`.
- Codex daemon discovery must verify process identity, open the session index
  read-only, and require a unique exact pane-title/index-path match. Never write
  Codex metadata or fall back to the newest global transcript.

## Layout

- `tmux_context.py` contains the runtime implementation.
- Transcript support is per CLI: `agent_identity` locates the live session, `transcript_messages` dispatches parsing on the transcript path. Add a CLI by extending both, not by branching inside the sidebar.
- `tmux-context` is the POSIX entrypoint; installation creates a Python-pinned launcher.
- `macos_process.py` implements Darwin process inspection with system facilities.
- `tmux-context.ps1` is the PowerShell-to-WSL bridge.
- `Install-TmuxContext.ps1` installs from PowerShell.
- `tests/` covers behavior that does not require a live terminal.

## Validation

Run `python3 -m unittest discover -s tests -v` and `git diff --check` before handoff.
When runtime files change, verify `scripts/build-package.sh` includes all runtime
modules and the documentation linked from README.
Parse PowerShell scripts with `[scriptblock]::Create((Get-Content -Raw <path>))` when Windows PowerShell is available.
