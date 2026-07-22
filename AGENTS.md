# tmux-context

## Scope

- This repository is standalone. Do not import `cs_manager` or depend on `~/.cs`.
- The runtime targets Linux and WSL2. Native PowerShell support is a bridge into WSL, not a native tmux implementation.
- Keep the runtime dependency-free: Python standard library plus `tmux`.

## Layout

- `tmux_context.py` contains the runtime implementation.
- `tmux-context` is the Linux/WSL entrypoint.
- `tmux-context.ps1` is the PowerShell-to-WSL bridge.
- `Install-TmuxContext.ps1` installs from PowerShell.
- `tests/` covers behavior that does not require a live terminal.

## Validation

Run `python3 -m unittest discover -s tests -v` and `git diff --check` before handoff.
Parse PowerShell scripts with `[scriptblock]::Create((Get-Content -Raw <path>))` when Windows PowerShell is available.
