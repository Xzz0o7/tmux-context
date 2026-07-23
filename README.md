# tmux-context

[中文文档](README.zh-CN.md)

`tmux-context` automatically names tmux sessions from the completed Codex task, shows a mouse-enabled sidebar for the current conversation, and keeps compatible terminal titles in sync.

**Creator website:** [xzzoo7.com](https://xzzoo7.com/)

## Preview

![Fictional tmux-context current-session sidebar example](docs/images/sidebar-demo.svg)

This is a deliberately fictional demonstration. The tab strip shows different task-based session names, while the active tab shows its current-session sidebar. It contains no user name, account, filesystem path, host name, session ID, or real conversation content.

## Support boundary

| Environment | Support | Notes |
|---|---|---|
| Linux terminal | Full | Requires Python 3.10+ and tmux 3.2+ |
| Windows Terminal + WSL2 | Full | Recommended Windows configuration |
| Windows PowerShell | Bridge | PowerShell invokes the same runtime inside WSL2 |
| Native PowerShell without WSL | Unsupported | tmux, `/proc`, and curses require a POSIX host |

PowerShell compatibility means that you can install, launch, diagnose, and attach from PowerShell while the session itself runs in WSL. It does not ship a separate Windows tmux implementation.

## Features

- Session titles prioritize the most recent meaningful task whose Codex response has completed.
- Weak text such as `继续`, raw shell commands, and image placeholders does not rename a session.
- The current-session sidebar supports mouse selection, collapsing one prompt at a time, expanding AI replies, scrolling, filtering, and hiding/showing reasoning output.
- Click a conversation once to expand or collapse it. Drag text in an expanded conversation to copy only the selected body text; headers, timestamps, and separators are excluded.
- On WSL, selected text is copied through Windows PowerShell with UTF-8 input, preserving Chinese and other Unicode text.
- Clipboard writes run in the background, so the selection highlight remains responsive while copying.
- Dragging text in a normal tmux pane copies it and returns to the original position, so historical output stays at its current location.
- Terminal titles use the tmux session name, with Windows Terminal setup available as an explicit command.
- `Ctrl-b H` toggles the sidebar; `Ctrl-b R` refreshes the current session name.

## Install from WSL or Linux

Install prerequisites, then clone the repository into the Linux filesystem:

```bash
sudo apt update
sudo apt install -y python3 tmux git
git clone https://github.com/<your-account>/tmux-context.git ~/workspace/tmux-context
cd ~/workspace/tmux-context
chmod +x tmux-context install.sh
./install.sh
./tmux-context doctor
```

Start an AI CLI from a regular shell so `tmux-context` can create the managed session and title watcher:

```bash
./tmux-context run --name assistant -- codex
```

If `codex` is not the command you use, replace it with the actual executable and arguments. Running `tmux-context run` inside an existing tmux client deliberately does not create a nested tmux server.

## Install and use from PowerShell

Clone the repository either in a Windows folder mounted by WSL (for example `C:\Users\<user>\source\tmux-context`) or in the WSL filesystem. In a PowerShell window opened at the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-TmuxContext.ps1 -Distribution Ubuntu
.\tmux-context.ps1 doctor
.\tmux-context.ps1 run --name assistant -- codex
```

`-Distribution` is optional when only one distribution is installed. If the repository is in a nonstandard WSL path, pass it explicitly:

```powershell
.\tmux-context.ps1 -Distribution Ubuntu -WslPath /home/<linux-user>/workspace/tmux-context doctor
```

`Install-TmuxContext.ps1` installs the tmux hook and, by default, updates Windows Terminal settings. Use `-SkipWindowsTerminal` to only install tmux integration.

## Windows Terminal titles

From WSL:

```bash
./tmux-context enable-windows-title
```

or from PowerShell:

```powershell
.\tmux-context.ps1 enable-windows-title
```

This backs up `settings.json`, sets `suppressApplicationTitle: false` for WSL profiles, and adds `Ctrl+Shift+F12` for the Windows Terminal tab-title editor. Close all Windows Terminal windows after changing settings. For an old manually fixed tab title, focus the tab, press `Ctrl+Shift+F12`, then `Ctrl+A`, `Backspace`, and `Enter`.

## Commands

| Command | Purpose |
|---|---|
| `./tmux-context install` | Generate config and install the managed tmux hook |
| `./tmux-context uninstall` | Remove only this tool's managed tmux hook |
| `./tmux-context doctor` | Check tmux, entrypoint, generated config, and Windows Terminal discovery |
| `./tmux-context run --name <name> -- <command>` | Create and attach a managed tmux session |
| `./tmux-context client <session-id>` | Attach to an existing session with terminal-title monitoring |
| `./tmux-context enable-windows-title` | Configure Windows Terminal WSL title support |

## Tests

```bash
python3 -m unittest discover -s tests -v
git diff --check
```
