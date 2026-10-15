# Roadmap

## Current state

- Linux/WSL2 runtime and PowerShell-to-WSL bridge remain supported. macOS runtime
  support is implemented for Codex, including installed launchers, process
  inspection, clipboard integration, terminal titles, and theme preservation.
- Codex direct and daemon-backed sessions use main-transcript filtering.
  Daemon clients use verified process identity and a unique exact native
  title/index match; unknown or ambiguous matches clear stale sidebar content.

## Recently completed

- 2026-10-15 19:14: Integrated upstream daemon compatibility from `9563db7` with
  the macOS adaptation. Added Darwin daemon birth-time/boot-UUID validation and
  default Codex home discovery while preserving transcript-derived custom homes.
  Kept the runtime dependency-free and the Codex database read-only. Updated the
  bilingual installation documentation and distribution archive contents.

## Recent validation

- 2026-10-15 19:14: Python 3.13 unit suite passed all 95 tests, including Linux and
  Darwin main/subagent selection, daemon identity and metadata failures, default
  and custom homes, exact title/index matching, duplicate/unknown titles, stale
  paths, and unresolved switches. `git diff --check` passed.
- 2026-10-15 19:14: Isolated macOS zsh/tmux smoke test passed concurrent Codex
  identities, Unicode paths, completed-task naming, rendered sidebar content,
  sidebar toggling, live-writer lookup, theme preservation, and scoped cleanup.
  Test servers, fake processes, and synthetic transcripts were cleaned up.
- 2026-10-15 19:14: Installed `doctor` passed all applicable checks. Three live
  daemon-backed sidebar panes displayed their respective main thread IDs after
  reload. All foreground pane PIDs, status-bar themes, and the user tmux
  configuration stayed unchanged; Codex foreground processes and the daemon
  were not restarted. Restricted sandbox process access was verified outside
  the sandbox; Windows Terminal checks were skipped on macOS.
- 2026-10-15 19:14: Built an isolated distribution archive and verified its
  integrity, platform runtime modules, bilingual documentation, roadmap, and
  linked preview image. Imported the runtime successfully from the extracted
  archive; temporary files and caches were excluded.

## Remaining work and boundaries

- macOS Claude Code process/cwd/start-time discovery and end-to-end acceptance.
- Interactive iTerm2 title/mouse/clipboard acceptance, real Codex resume/picker
  conflict flows, and multi-client detach acceptance. Clipboard encoding/failure
  handling and title save/restore have mocked coverage; real clipboard contents
  were not changed during validation.
- Linux/WSL live regression for the combined macOS/daemon changes. Linux process
  readers and shared transcript behavior have local mocked regression coverage.
- Daemon binding depends on the current PID identity metadata, session-index
  schema, and native title format. Unnamed, duplicate, truncated, manually
  overridden, or unknown-format titles remain unidentified; future client/thread
  association protocols may replace this boundary.
