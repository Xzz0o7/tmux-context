# Roadmap

## Completed

- 2026-09-30: Codex transcript detection excludes guardian and spawned-agent sessions, selects the latest main transcript after a fork, and rejects inherited subagent thread IDs. Internal notifications no longer appear as user tasks; asynchronous question replies retain readable questions and answers without renaming the task. Cached internal prompt titles are cleared.
- 2026-08-21: Managed sessions now close when their launch command exits or their last tmux client detaches.
- 2026-08-21: Direct Codex resume detects an existing live writer and activates its mapped tmux session.
- Existing sessions created by `tmux-context run` are migrated from `@session_context_launch_name` to the explicit `@session_context_managed=1` marker during installation.

## Validation

- `python3 -B -m unittest discover -s tests -v`: 62 tests passed, including 12 Codex identity, injected-message, question-reply, and cached-title regression tests.
- `git diff --check`: passed for the 2026-09-30 compatibility fix.
- Live validation on 2026-09-30: six Codex sessions spanning CLI 0.153.4, 0.156.1, and 0.159.2 now reference main transcripts with `source: cli` and matching thread IDs. Six existing sidebar panes were reloaded; every foreground pane PID remained unchanged. Guardian, notification, and question-reply titles were removed, and three real asynchronous replies remained readable.
- Isolated tmux server: unmanaged detach preserved the session; managed last-client detach, normal agent exit, and fast failure with an active sidebar removed it; one remaining client preserved a shared session.
- `./tmux-context doctor`: all integration checks passed after installation; the live server retained `destroy-unattached off` and loaded the scoped `client-detached` hook.

## Pending

- No pending work in the current managed-session lifecycle or Codex transcript compatibility scope.
