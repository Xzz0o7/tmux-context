# tmux-context

[English](README.md)

`tmux-context` 会依据已完成的 Codex 或 Claude Code 任务自动命名 tmux 会话，为当前对话显示支持鼠标操作的侧边栏，并同步兼容终端的标签标题。

**作者网站：** [xzzoo7.com](https://xzzoo7.com/)

## 界面预览

![虚构的 tmux-context 当前会话侧边栏示例](docs/images/sidebar-demo.svg)

该示例完全为虚构内容。顶部标签栏展示不同任务对应的会话名称，活动标签则显示其当前会话侧边栏；不含用户名、账号、文件系统路径、主机名、会话 ID 或真实对话内容。

## 支持范围

| 环境 | 支持程度 | 说明 |
|---|---|---|
| Linux 终端 | 完整支持 | 需要 Python 3.10+ 与 tmux 3.2+ |
| Windows Terminal + WSL2 | 完整支持 | 推荐的 Windows 配置 |
| Windows PowerShell | 桥接支持 | PowerShell 在 WSL2 内调用同一套运行时 |
| 未安装 WSL 的原生 PowerShell | 不支持 | tmux、`/proc` 与 curses 需要 POSIX 主机 |

PowerShell 兼容表示可以从 PowerShell 安装、启动、诊断和连接会话，但实际会话仍在 WSL 中运行；本项目不提供独立的 Windows 原生 tmux 实现。

## 支持的 AI CLI

| CLI | 会话记录 | 识别方式 |
|---|---|---|
| Codex | `$CODEX_HOME/sessions/rollout-*.jsonl` | pane 进程打开的文件描述符，或 `CODEX_THREAD_ID` |
| Claude Code | `$CLAUDE_CONFIG_DIR/projects/<cwd 目录名>/<session-id>.jsonl` | pane 进程的工作目录，配合最接近的会话起始时间 |

Claude Code 每次写入后都会关闭会话文件，因此改用记录中的 `cwd` 与进程启动时间来匹配；同一目录下同时运行的多个 Claude 会话也能各自对应到自己的侧边栏。

## 功能

- 会话标题优先采用最近一个已收到完成响应的、有意义的 Codex 或 Claude Code 任务。
- `继续`、原始 shell 命令和图片占位符等弱文本不会触发会话重命名。
- 当前会话侧边栏支持鼠标操作、逐条折叠输入、展开 AI 回复、滚动、筛选，以及隐藏或显示推理过程。
- Claude Code 的思考内容与工具调用之间的中间回复都归为过程输出，「仅最终」视图只保留每轮的收尾回复。
- 单击一条会话可展开或折叠；在展开内容中拖动可仅复制选中的正文，标题、时间戳和分隔线不会被复制。
- 在 WSL 中，选中内容会经由 Windows PowerShell 使用 UTF-8 写入 Windows 剪贴板，保留中文和其他 Unicode 字符。
- 剪贴板写入在后台执行，复制时的选中高亮保持及时响应。
- 在普通 tmux pane 中拖动文本会自动复制并回到原来的显示位置，因此历史输出不会跳动。
- 终端标签使用 tmux 会话名；可通过显式命令配置 Windows Terminal。
- `Ctrl-b H` 切换侧边栏，`Ctrl-b R` 刷新当前会话名称。

## 从 WSL 或 Linux 安装

先安装依赖，再将仓库克隆到 Linux 文件系统中：

```bash
sudo apt update
sudo apt install -y python3 tmux git
git clone https://github.com/<your-account>/tmux-context.git ~/workspace/tmux-context
cd ~/workspace/tmux-context
chmod +x tmux-context install.sh
./install.sh
./tmux-context doctor
```

从普通 shell 启动 AI CLI，使 `tmux-context` 可以创建受管理的会话和标题监听器：

```bash
./tmux-context run --name assistant -- codex
./tmux-context run --name assistant -- claude
```

如果你实际使用的命令既不是 `codex` 也不是 `claude`，请替换成对应的可执行文件及参数。在已有 tmux client 内执行 `tmux-context run` 时，程序不会再嵌套创建 tmux server。

## 从 PowerShell 安装和使用

可将仓库克隆到 WSL 挂载的 Windows 目录（例如 `C:\Users\<user>\source\tmux-context`）或 WSL 文件系统中。在仓库根目录打开 PowerShell 后执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-TmuxContext.ps1 -Distribution Ubuntu
.\tmux-context.ps1 doctor
.\tmux-context.ps1 run --name assistant -- codex
```

仅安装一个发行版时可省略 `-Distribution`。如果仓库在非标准 WSL 路径中，请显式传入路径：

```powershell
.\tmux-context.ps1 -Distribution Ubuntu -WslPath /home/<linux-user>/workspace/tmux-context doctor
```

`Install-TmuxContext.ps1` 会安装 tmux hook，并默认更新 Windows Terminal 设置。使用 `-SkipWindowsTerminal` 可只安装 tmux 集成。

## Windows Terminal 标签标题

在 WSL 中执行：

```bash
./tmux-context enable-windows-title
```

或在 PowerShell 中执行：

```powershell
.\tmux-context.ps1 enable-windows-title
```

该命令会备份 `settings.json`，为 WSL profile 设置 `suppressApplicationTitle: false`，并添加 Windows Terminal 标签改名快捷键 `Ctrl+Shift+F12`。修改设置后请关闭全部 Windows Terminal 窗口。若某个标签此前被手动固定标题：聚焦该标签，按 `Ctrl+Shift+F12`，然后依次按 `Ctrl+A`、`Backspace` 与 `Enter`。

## 命令

| 命令 | 用途 |
|---|---|
| `./tmux-context install` | 生成配置并安装受管理的 tmux hook |
| `./tmux-context uninstall` | 仅移除本工具管理的 tmux hook |
| `./tmux-context doctor` | 检查 tmux、入口脚本、生成配置和 Windows Terminal 发现状态 |
| `./tmux-context run --name <name> -- <command>` | 创建并连接受管理的 tmux 会话 |
| `./tmux-context client <session-id>` | 连接已有会话并启用终端标题监听 |
| `./tmux-context enable-windows-title` | 配置 Windows Terminal 的 WSL 标签标题支持 |

## 测试

```bash
python3 -m unittest discover -s tests -v
git diff --check
```
