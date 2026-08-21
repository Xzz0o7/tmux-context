# Roadmap

## Completed

- 2026-08-21: Managed sessions now close when their launch command exits or their last tmux client detaches.
- 2026-08-21: Direct Codex resume detects an existing live writer and activates its mapped tmux session.
- Existing sessions created by `tmux-context run` are migrated from `@session_context_launch_name` to the explicit `@session_context_managed=1` marker during installation.

## Validation

- `python3 -m unittest discover -s tests -v`: 50 tests passed.
- Isolated tmux server: unmanaged detach preserved the session; managed last-client detach, normal agent exit, and fast failure with an active sidebar removed it; one remaining client preserved a shared session.
- `./tmux-context doctor`: all integration checks passed after installation; the live server retained `destroy-unattached off` and loaded the scoped `client-detached` hook.

## Pending

- No pending work in the current managed-session lifecycle scope.
