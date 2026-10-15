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

## 2026-10-15: Codex daemon session discovery diagnosis

- Read-only live checks confirmed source panes `%26`, `%32`, and `%34` return an empty thread ID and transcript path while their sidebars display `waiting` and zero inputs.
- Their Codex 0.161.0 TUI processes expose neither a rollout file descriptor nor `CODEX_THREAD_ID`. The `.codex_a` app-server daemon (0.162.1, PID 3130496, PPID 1) holds the relevant rollout files outside those pane process trees.
- `agent_identity()` scans only pane descendants; `codex_process_identity()` relies on open rollout descriptors or the thread environment variable. This leaves daemon-backed sessions undiscovered. Existing `cgw --no-daemon` pane `%6` still resolves its main transcript.
- `doctor` passes installation checks but does not check live transcript discovery. No runtime fix, restart, session rebinding, or launcher change was applied.
- Pending: add a verified pane-to-thread association for daemon-backed clients; do not select the newest global rollout because multiple clients share one daemon.

## 2026-10-15：daemon 模式兼容修复

- 已实现：在原有 pane 子进程识别失败后，只读验证当前 CODEX_HOME 的 daemon PID、启动标识和所持有的主会话文件；通过 Codex 原生 pane 标题精确匹配 state_5.sqlite 的会话 name/cwd，并核对 rollout 路径。
- 多候选同名、标题不匹配、索引缺失和 daemon 失效时不选择会话；未确认的切换会清除侧栏旧对话。保留原有 no-daemon 路径，不改 Codex 启动参数、配置或数据库。
- 验证：71 项单元测试通过，git diff --check 与 doctor 通过；三个 daemon 侧栏分别恢复 24/12/21 条输入，主 thread 分别为 01a13851、01a13d64、01a13a68，三个源 pane PID 保持不变。对照 cgw no-daemon 主 thread 仍正常识别。
- 仅重新启动侧栏 pane %27/%33/%35；Codex 前台及 daemon 未重启。
- 边界：依赖当前 daemon PID 元数据格式、state_5.sqlite 的 name 字段和原生标题格式；未命名、同名或被手动覆盖的标题可能无法识别，未知格式安全停止匹配。跨版本协议化客户端/thread 绑定仍可作为后续改进。
