#!/usr/bin/env python3
"""Context-aware tmux session naming and live Codex/Claude Code transcript sidebar."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import curses
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata


REPO_ROOT = Path(__file__).resolve().parent
ENTRYPOINT = REPO_ROOT / "tmux-context"
APP_DIR = Path.home() / ".tmux-context"
INDEX_PATH = APP_DIR / "index.jsonl"
TMUX_RUNTIME_DIR = APP_DIR / "tmux"
TMUX_CONFIG_PATH = TMUX_RUNTIME_DIR / "tmux.conf"
USER_TMUX_CONFIG_PATH = Path.home() / ".tmux.conf"
MANAGED_BLOCK_START = "# tmux-context start"
MANAGED_BLOCK_END = "# tmux-context end"
CS_MANAGER_BLOCK_START = "# cs-session-manager tmux start"
CS_MANAGER_BLOCK_END = "# cs-session-manager tmux end"
MAX_HISTORY_BYTES = 4 * 1024 * 1024
MAX_TRANSCRIPT_MESSAGE_CHARS = 20000
SIDEBAR_WIDTH_PERCENT = 40
CLAUDE_META_SCAN_LINES = 40
CLAUDE_SCAN_FILE_LIMIT = 40
CLAUDE_SCAN_DIR_LIMIT = 12
CLAUDE_START_WINDOW_SECONDS = 300
CLAUDE_META_CACHE: dict[str, tuple[int, int, str, str, float]] = {}
TITLE_TASK_HINTS = (
    "怎么", "如何", "为什么", "是否", "能否", "查看", "检查", "分析", "修复",
    "实现", "添加", "修改", "删除", "优化", "配置", "适配", "迁移", "打包",
    "重命名", "解耦", "列出", "合入", "获取", "生成", "给出", "调整", "替换",
    "异常", "报错", "失败", "问题", "fix", "add", "remove", "update", "rename",
    "configure", "debug", "build", "test", "install", "package", "migrate", "document",
)
WEAK_TITLE_MESSAGES = {
    "继续", "确认", "采用", "执行", "同意", "可以", "好的", "好", "是", "否",
    "开始", "完成", "重试", "再试一次", "直接改", "检查一下", "再检查一下",
    "查看一下", "看一下", "下一步", "下一步建议", "然后呢", "还有吗", "这个呢",
    "就这个", "没效果", "没有效果", "还是没有", "还是不行", "不行", "可以了",
    "ok", "yes", "no", "codex", "codexa", "codexb", "claude", "ubuntu", "wsl", "bash",
}
COMPLETED_TITLE_CACHE: dict[str, tuple[int, int, str]] = {}
TITLE_PATH_PATTERN = re.compile(
    r"(?:(?:[A-Za-z]:)?[\\/]{1,2})[^\s，。！？；;]*?\."
    r"(?:xml|jsonl?|toml|ya?ml|java|kt|py|js|ts|tsx|jsx|c|cc|cpp|h|hpp|rs|go|sh|md|txt|log|diff|patch)"
    r"(?=[^A-Za-z0-9]|$)",
    re.IGNORECASE,
)
TITLE_COMMAND_PATTERN = re.compile(
    r"^(?:sudo\s+)?(?:tmux-context|cs|tmux|git|python3?|bash|sh|zsh|cd|ls|cat|grep|rg|find|fuser|adb|wsl|curl)\s+",
    re.IGNORECASE,
)


def run_tmux(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def tmux_value(target: str, fmt: str) -> str:
    result = run_tmux("display-message", "-p", "-t", target, fmt)
    return result.stdout.strip() if result.returncode == 0 else ""


def pane_rows(session_id: str) -> list[dict[str, str]]:
    result = run_tmux(
        "list-panes",
        "-s",
        "-t",
        session_id,
        "-F",
        "#{pane_id}\t#{window_id}\t#{pane_active}\t#{@session_context_sidebar}",
    )
    rows = []
    if result.returncode != 0:
        return rows
    for line in result.stdout.splitlines():
        fields = line.split("\t", 3)
        if len(fields) == 4:
            rows.append(
                {
                    "pane_id": fields[0],
                    "window_id": fields[1],
                    "active": fields[2],
                    "sidebar": fields[3],
                }
            )
    return rows


def resolve_source_pane(session_id: str, preferred_pane: str = "") -> str:
    rows = pane_rows(session_id)
    normal_panes = [row for row in rows if row["sidebar"] != "1"]
    if any(row["pane_id"] == preferred_pane for row in normal_panes):
        return preferred_pane

    stored = tmux_value(session_id, "#{@session_context_source_pane}")
    if any(row["pane_id"] == stored for row in normal_panes):
        return stored

    for row in normal_panes:
        if row["active"] == "1":
            return row["pane_id"]
    return normal_panes[0]["pane_id"] if normal_panes else ""


def git_value(cwd: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", cwd, *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def project_context(cwd: str) -> tuple[str, str, str]:
    resolved = str(Path(cwd).resolve()) if cwd else str(Path.home())
    root = git_value(resolved, "rev-parse", "--show-toplevel")
    project_path = root or resolved
    project = Path(project_path).name or "wsl"
    branch = git_value(resolved, "branch", "--show-current")
    if root and not branch:
        branch = git_value(resolved, "rev-parse", "--short", "HEAD")
    return project, branch, project_path


def process_descendants(root_pid: int) -> list[int]:
    result = []
    queue = [root_pid]
    visited = set()
    while queue:
        process_id = queue.pop(0)
        if process_id in visited:
            continue
        visited.add(process_id)
        result.append(process_id)
        children_path = Path(f"/proc/{process_id}/task/{process_id}/children")
        try:
            children = [int(item) for item in children_path.read_text().split()]
        except (OSError, ValueError):
            children = []
        queue.extend(children)
    return result


def process_environment(process_id: int) -> dict[str, str]:
    try:
        data = Path(f"/proc/{process_id}/environ").read_bytes()
    except OSError:
        return {}
    environment = {}
    for item in data.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        environment[key.decode(errors="ignore")] = value.decode(errors="ignore")
    return environment


def process_command(process_id: int) -> str:
    try:
        return Path(f"/proc/{process_id}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
    except OSError:
        return ""


def process_cwd(process_id: int) -> str:
    try:
        return os.path.realpath(os.readlink(f"/proc/{process_id}/cwd"))
    except OSError:
        return ""


def process_start_time(process_id: int) -> float:
    try:
        return Path(f"/proc/{process_id}").stat().st_mtime
    except OSError:
        return 0.0


def file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def open_codex_session_path(process_id: int) -> Path | None:
    fd_dir = Path(f"/proc/{process_id}/fd")
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            target = Path(os.readlink(entry))
        except OSError:
            continue
        if target.suffix == ".jsonl" and "sessions" in target.parts and target.name.startswith("rollout-"):
            return target
    return None


def session_id_from_path(session_path: Path) -> str:
    try:
        with session_path.open() as stream:
            for _ in range(40):
                line = stream.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "session_meta":
                    session_id = (entry.get("payload") or {}).get("id")
                    if session_id:
                        return str(session_id)
    except OSError:
        pass
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
        session_path.name,
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


def codex_home_from_session_path(session_path: Path) -> str:
    for parent in session_path.parents:
        if parent.name == "sessions":
            return str(parent.parent)
    return ""


def find_session_path(codex_home: str, thread_id: str) -> Path | None:
    sessions_dir = Path(codex_home) / "sessions"
    if not codex_home or not thread_id or not sessions_dir.is_dir():
        return None
    try:
        return next(sessions_dir.rglob(f"*{thread_id}.jsonl"), None)
    except OSError:
        return None


def claude_home_from_environment(environment: dict[str, str]) -> Path:
    configured = environment.get("CLAUDE_CONFIG_DIR", "")
    return Path(configured) if configured else Path.home() / ".claude"


def claude_project_slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def claude_session_meta(session_path: Path) -> tuple[str, str, float]:
    """Return (session_id, recorded cwd, first timestamp) from a Claude transcript head."""
    key = str(session_path)
    try:
        stat = session_path.stat()
    except OSError:
        return "", "", 0.0
    cached = CLAUDE_META_CACHE.get(key)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2], cached[3], cached[4]

    session_id = ""
    cwd = ""
    first_timestamp = 0.0
    try:
        with session_path.open(errors="replace") as stream:
            for _ in range(CLAUDE_META_SCAN_LINES):
                line = stream.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = session_id or str(entry.get("sessionId") or "")
                cwd = cwd or str(entry.get("cwd") or "")
                if not first_timestamp:
                    first_timestamp = transcript_timestamp(entry.get("timestamp"))
                if session_id and cwd and first_timestamp:
                    break
    except OSError:
        return "", "", 0.0

    session_id = session_id or session_path.stem
    CLAUDE_META_CACHE[key] = (stat.st_mtime_ns, stat.st_size, session_id, cwd, first_timestamp)
    return session_id, cwd, first_timestamp


def claude_candidate_paths(projects_dir: Path, cwd: str) -> list[Path]:
    slug_dir = projects_dir / claude_project_slug(cwd)
    if slug_dir.is_dir():
        directories = [slug_dir]
    else:
        try:
            entries = [item for item in projects_dir.iterdir() if item.is_dir()]
        except OSError:
            return []
        entries.sort(key=lambda item: file_mtime(item), reverse=True)
        directories = entries[:CLAUDE_SCAN_DIR_LIMIT]
    paths = []
    for directory in directories:
        try:
            paths.extend(item for item in directory.iterdir() if item.suffix == ".jsonl")
        except OSError:
            continue
    paths.sort(key=lambda item: file_mtime(item), reverse=True)
    return paths[:CLAUDE_SCAN_FILE_LIMIT]


def claude_session_path(process_id: int, environment: dict[str, str]) -> Path | None:
    """Claude Code closes its transcript between writes, so match it by cwd and start time."""
    cwd = process_cwd(process_id)
    projects_dir = claude_home_from_environment(environment) / "projects"
    if not cwd or not projects_dir.is_dir():
        return None
    started = process_start_time(process_id)
    fallback = None
    best_path = None
    best_distance = float("inf")
    for path in claude_candidate_paths(projects_dir, cwd):
        _, recorded_cwd, first_timestamp = claude_session_meta(path)
        if recorded_cwd and os.path.realpath(recorded_cwd) != cwd:
            continue
        fallback = fallback or path
        if not first_timestamp or not started:
            continue
        distance = abs(first_timestamp - started)
        if distance <= CLAUDE_START_WINDOW_SECONDS and distance < best_distance:
            best_distance = distance
            best_path = path
    return best_path or fallback


def is_claude_process(command: str) -> bool:
    return any(
        name == "claude" or name.startswith("claude-") or name.startswith("claude.")
        for name in (Path(token).name.lower() for token in command.split() if token)
    )


def codex_process_identity(process_id: int, detected_home: str) -> tuple[tuple[str, str, str] | None, str]:
    environment = process_environment(process_id)
    detected_home = environment.get("CODEX_HOME", "") or detected_home
    thread_id = environment.get("CODEX_THREAD_ID", "")
    session_path = open_codex_session_path(process_id)
    if session_path:
        thread_id = session_id_from_path(session_path)
        codex_home = detected_home or codex_home_from_session_path(session_path)
        if thread_id:
            return (thread_id, codex_home, str(session_path)), detected_home
    if thread_id:
        codex_home = detected_home or str(Path.home() / ".codex")
        session_path = find_session_path(codex_home, thread_id)
        return (thread_id, codex_home, str(session_path or "")), detected_home
    return None, detected_home


def claude_process_identity(process_id: int) -> tuple[str, str, str] | None:
    environment = process_environment(process_id)
    claude_home = claude_home_from_environment(environment)
    session_path = claude_session_path(process_id, environment)
    if not session_path:
        return None
    thread_id, _, _ = claude_session_meta(session_path)
    return thread_id, str(claude_home), str(session_path)


def agent_identity(pane_id: str) -> tuple[str, str, str]:
    pane_pid_text = tmux_value(pane_id, "#{pane_pid}")
    try:
        pane_pid = int(pane_pid_text)
    except ValueError:
        return "", "", ""

    detected_home = ""
    for process_id in reversed(process_descendants(pane_pid)):
        command = process_command(process_id)
        if "codex" in command.lower():
            identity, detected_home = codex_process_identity(process_id, detected_home)
            if identity:
                return identity
            continue
        if is_claude_process(command):
            identity = claude_process_identity(process_id)
            if identity:
                return identity
    return "", detected_home, ""


def shorten_title_path(match: re.Match[str]) -> str:
    normalized_path = match.group(0).replace("\\", "/")
    return f" {normalized_path.rsplit('/', 1)[-1]} "


def title_candidate(text: str) -> str:
    compact = re.sub(r"\[(?:Image|File)[^\]]*\]", " ", text, flags=re.IGNORECASE)
    compact = re.sub(r"</?image\b[^>]*>", " ", compact, flags=re.IGNORECASE)
    compact = TITLE_PATH_PATTERN.sub(shorten_title_path, compact)
    compact = re.sub(r"(?:修改)?这个文件[，,]?", " ", compact)
    compact = re.sub(r"中(?:有没有|是否)(?:添加的)?重复项", " 重复项", compact)
    compact = compact.replace("对应的", "")
    compact = re.sub(r"\s+", " ", compact).strip()
    compact = re.sub(r"^[#>*`\-\s]+", "", compact)
    return compact


def meaningful_title(text: str, limit: int = 34) -> str:
    compact = title_candidate(text)
    normalized = compact.lower().strip(" .,!?:;，。！？：；")
    if not normalized or normalized in WEAK_TITLE_MESSAGES:
        return ""
    if TITLE_COMMAND_PATTERN.match(normalized):
        return ""
    if re.fullmatch(r"(?:请)?(?:继续|确认|采用|执行|同意|重试|直接改|开始|完成)(?:一下)?", normalized):
        return ""
    if re.fullmatch(r"(?:还)?(?:需要)?(?:添加|修改|处理|检查|查看)(?:一下)?(?:这个|这些)?", normalized):
        return ""
    has_task_hint = any(hint in normalized for hint in TITLE_TASK_HINTS)
    visible_length = len(re.sub(r"\W+", "", normalized, flags=re.UNICODE))
    if visible_length < 6 and not has_task_hint:
        return ""
    if not has_task_hint and not re.search(r"[\u4e00-\u9fff]", normalized) \
            and re.fullmatch(r"[./~\\:\w-]+", normalized):
        return ""
    return clean_title(compact, limit)


def clean_title(text: str, limit: int = 34) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    compact = re.sub(r"^[#>*`\-\s]+", "", compact)
    compact = compact.replace(":", " ").replace(".", " ")
    if len(compact) > limit:
        compact = compact[: limit - 1].rstrip() + "…"
    return compact


def read_transcript_lines(raw_path: str, max_bytes: int = MAX_HISTORY_BYTES) -> list[str]:
    try:
        with Path(raw_path).open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            stream.seek(start)
            if start:
                stream.readline()
            return stream.read().decode(errors="replace").splitlines()
    except OSError:
        return []


def completed_context_title(raw_path: str) -> str:
    if not raw_path:
        return ""
    try:
        stat = Path(raw_path).stat()
    except OSError:
        return ""
    cache_key = (stat.st_mtime_ns, stat.st_size)
    cached = COMPLETED_TITLE_CACHE.get(raw_path)
    if cached and cached[:2] == cache_key:
        return cached[2]
    lines = read_transcript_lines(raw_path)

    completed_messages = []
    pending_user_messages: list[str] = []
    for message in transcript_messages(lines, raw_path):
        if message["role"] == "user":
            pending_user_messages.append(message["text"])
        elif is_final_reply(message) and pending_user_messages:
            completed_messages.extend(pending_user_messages)
            pending_user_messages.clear()

    for message in reversed(completed_messages):
        title = meaningful_title(message)
        if title:
            COMPLETED_TITLE_CACHE[raw_path] = (*cache_key, title)
            return title
    COMPLETED_TITLE_CACHE[raw_path] = (*cache_key, "")
    return ""


def load_records() -> list[dict]:
    records = []
    try:
        lines = INDEX_PATH.read_text().splitlines()
    except OSError:
        return records
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("status") == "archived":
            records.append(record)
    records.sort(key=record_timestamp, reverse=True)
    return records


def record_timestamp(record: dict) -> float:
    for key in ("latest_ts", "last_seen_at", "updated_at", "created_at"):
        value = record.get(key)
        if not value:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
        if isinstance(value, str):
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            try:
                return datetime.fromisoformat(normalized).timestamp()
            except ValueError:
                pass
    raw_path = str(record.get("raw_path", ""))
    try:
        return os.path.getmtime(raw_path) if raw_path else 0.0
    except OSError:
        return 0.0


def indexed_ai_title(thread_id: str, records: list[dict] | None = None) -> str:
    if not thread_id:
        return ""
    for record in records if records is not None else load_records():
        if record.get("session_id") == thread_id:
            if record.get("archive_method") not in ("ai", "manual"):
                return ""
            archive = record.get("archive") or {}
            return meaningful_title(str(archive.get("goal", "")))
    return ""


def indexed_title(thread_id: str, records: list[dict] | None = None) -> str:
    if not thread_id:
        return ""
    for record in records if records is not None else load_records():
        if record.get("session_id") == thread_id:
            return meaningful_title(str(record.get("title", "")))
    return ""


def context_title(thread_id: str, raw_path: str, records: list[dict] | None = None) -> str:
    available_records = records if records is not None else load_records()
    title = indexed_ai_title(thread_id, available_records)
    if not title:
        title = completed_context_title(raw_path)
    if not title:
        title = indexed_title(thread_id, available_records)
    return title


def current_context(pane_id: str, records: list[dict] | None = None) -> tuple[str, str, str, str]:
    thread_id, agent_home, raw_path = agent_identity(pane_id)
    title = context_title(thread_id, raw_path, records)
    return thread_id, title, agent_home, raw_path


def agent_label(raw_path: str, agent_home: str = "") -> str:
    if is_claude_transcript(raw_path) or Path(agent_home).name.startswith(".claude"):
        return "Claude"
    if raw_path or agent_home:
        return "Codex"
    return ""


def safe_name(value: str) -> str:
    value = value.replace(":", "-").replace(".", "-").replace("/", "-")
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r"[^\w\-\u4e00-\u9fff…]", "", value, flags=re.UNICODE)
    return value.strip("_-")


def source_name_from_raw_path(raw_path: str) -> str:
    path = Path(raw_path)
    for parent in path.parents:
        if parent.name == "projects" and parent.parent.name.startswith(".claude"):
            home_name = parent.parent.name
            return "claude" if home_name == ".claude" else safe_name(home_name.lstrip("."))
        if parent.name != "sessions":
            continue
        home_name = parent.parent.name
        if home_name == ".codex_a":
            return "codexa"
        if home_name == ".codex_b":
            return "codexb"
        if home_name == ".codex":
            return "codex"
        return safe_name(home_name.lstrip("."))
    return ""


def session_base_name(context: str, launch_name: str, project: str, branch: str) -> str:
    context_name = safe_name(context)
    metadata_parts = [part for part in (safe_name(launch_name), safe_name(project)) if part]
    metadata = "-".join(dict.fromkeys(metadata_parts))
    if branch:
        metadata = f"{metadata}-{safe_name(branch)}" if metadata else safe_name(branch)
    if context_name and metadata:
        return f"{context_name}--{metadata}"
    return context_name or metadata or "wsl"


def unique_session_name(session_id: str, base_name: str) -> str:
    result = run_tmux("list-sessions", "-F", "#{session_id}\t#{session_name}\t#{session_created}")
    if result.returncode != 0:
        return base_name
    sessions = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t", 2)
        if len(fields) >= 2:
            sessions[fields[0]] = (fields[1], fields[2] if len(fields) == 3 else "")
    names = {name for identifier, (name, _) in sessions.items() if identifier != session_id}
    if base_name not in names:
        return base_name

    created = sessions.get(session_id, ("", ""))[1]
    try:
        suffix = datetime.fromtimestamp(int(created)).strftime("%m%d-%H%M")
    except (TypeError, ValueError, OSError):
        suffix = "会话2"
    candidate = f"{clip(base_name, max(8, 69 - len(suffix))).rstrip('_-')}--{suffix}"
    sequence = 2
    while candidate in names:
        numbered_suffix = f"{suffix}-{sequence}"
        candidate = f"{clip(base_name, max(8, 69 - len(numbered_suffix))).rstrip('_-')}--{numbered_suffix}"
        sequence += 1
    return candidate


def update_terminal_title(session_id: str, title: str) -> None:
    terminal_title = re.sub(r"[\x00-\x1f\x7f]+", " ", title).strip()
    if not terminal_title:
        return
    result = run_tmux("list-clients", "-F", "#{session_id}|#{client_pid}|#{client_tty}")
    if result.returncode != 0:
        return
    control_sequence = f"\033]0;{terminal_title}\007\033]2;{terminal_title}\007".encode()
    sent_count = 0
    failed_count = 0
    delivery_methods = []
    for line in result.stdout.splitlines():
        fields = line.split("|", 2)
        if len(fields) != 3:
            continue
        client_session_id, client_pid, client_tty = fields
        if client_session_id != session_id:
            continue
        targets = [(f"/proc/{client_pid}/fd/1", "proc")]
        if client_tty.startswith("/dev/"):
            targets.append((client_tty, "tty"))
        for target, method in targets:
            try:
                terminal_fd = os.open(target, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
                try:
                    os.write(terminal_fd, control_sequence)
                finally:
                    os.close(terminal_fd)
                sent_count += 1
                delivery_methods.append(method)
                break
            except OSError:
                continue
        else:
            failed_count += 1
    methods = ",".join(delivery_methods) or "none"
    status = f"sent={sent_count},failed={failed_count},via={methods}"
    run_tmux("set-option", "-q", "-t", session_id, "@session_context_terminal_title_status", status, capture=False)


def rename_session(session_id: str, pane_id: str = "") -> None:
    source_pane = resolve_source_pane(session_id, pane_id)
    if not source_pane:
        return
    cwd = tmux_value(source_pane, "#{pane_current_path}") or str(Path.home())
    project, branch, _ = project_context(cwd)
    thread_id, live_title, _, raw_path = current_context(source_pane)
    thread_id = thread_id or tmux_value(session_id, "#{@session_context_thread_id}")
    raw_path = raw_path or tmux_value(session_id, "#{@session_context_raw_path}")
    if not live_title:
        live_title = context_title(thread_id, raw_path)
    stored_title = meaningful_title(tmux_value(session_id, "#{@session_context_title}"))
    title = live_title or stored_title
    current_name = tmux_value(session_id, "#{session_name}")
    run_tmux("set-option", "-q", "-t", session_id, "@session_context_source_pane", source_pane, capture=False)
    if thread_id:
        run_tmux("set-option", "-q", "-t", session_id, "@session_context_thread_id", thread_id, capture=False)
    if raw_path:
        run_tmux("set-option", "-q", "-t", session_id, "@session_context_raw_path", raw_path, capture=False)
    if not title:
        if current_name:
            update_terminal_title(session_id, current_name)
        return

    launch_name = safe_name(tmux_value(session_id, "#{@session_context_launch_name}"))
    launch_name = launch_name or source_name_from_raw_path(raw_path)
    base_name = session_base_name(title, launch_name, project, branch)
    base_name = clip(base_name, 72).rstrip("_-") or "wsl"
    name = unique_session_name(session_id, base_name)
    if name and name != current_name:
        run_tmux("rename-session", "-t", session_id, name, capture=False)
    if live_title:
        run_tmux("set-option", "-q", "-t", session_id, "@session_context_title", live_title, capture=False)
    update_terminal_title(session_id, name)


def sidebar_panes(session_id: str, window_id: str = "") -> list[str]:
    return [
        row["pane_id"]
        for row in pane_rows(session_id)
        if row["sidebar"] == "1" and (not window_id or row["window_id"] == window_id)
    ]


def open_sidebar(session_id: str, pane_id: str = "") -> None:
    lock_name = f"session-context-sidebar-{safe_name(session_id)}"
    if run_tmux("wait-for", "-L", lock_name).returncode != 0:
        return
    try:
        source_pane = resolve_source_pane(session_id, pane_id)
        if not source_pane or sidebar_panes(session_id):
            rename_session(session_id, source_pane)
            return

        cwd = tmux_value(source_pane, "#{pane_current_path}") or str(Path.home())
        width_text = tmux_value(source_pane, "#{window_width}")
        try:
            window_width = int(width_text)
        except ValueError:
            window_width = 120
        if window_width < 70:
            run_tmux("display-message", "窗口过窄，未自动打开 Current Session；放大后按 Prefix+H", capture=False)
            rename_session(session_id, source_pane)
            return

        sidebar_width = max(34, min(58, window_width * SIDEBAR_WIDTH_PERCENT // 100))
        command = shlex.join(
            [
                str(ENTRYPOINT),
                "sidebar",
                "--session-id",
                session_id,
                "--source-pane",
                source_pane,
                "--cwd",
                cwd,
            ]
        )
        result = run_tmux(
            "split-window",
            "-h",
            "-l",
            str(sidebar_width),
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            source_pane,
            "-c",
            cwd,
            command,
        )
        sidebar_pane = result.stdout.strip()
        if result.returncode != 0 or not sidebar_pane:
            return
        run_tmux("set-option", "-p", "-q", "-t", sidebar_pane, "@session_context_sidebar", "1", capture=False)
        run_tmux("select-pane", "-t", sidebar_pane, "-T", "Current Session", capture=False)
        run_tmux("set-option", "-q", "-t", session_id, "@session_context_source_pane", source_pane, capture=False)
        run_tmux("select-pane", "-t", source_pane, capture=False)
        rename_session(session_id, source_pane)
    finally:
        run_tmux("wait-for", "-U", lock_name, capture=False)


def toggle_sidebar(session_id: str, pane_id: str = "") -> None:
    source_pane = resolve_source_pane(session_id, pane_id)
    window_id = tmux_value(source_pane or pane_id, "#{window_id}")
    current_sidebars = sidebar_panes(session_id, window_id)
    if current_sidebars:
        for sidebar_pane in current_sidebars:
            run_tmux("kill-pane", "-t", sidebar_pane, capture=False)
        return
    for sidebar_pane in sidebar_panes(session_id):
        run_tmux("kill-pane", "-t", sidebar_pane, capture=False)
    open_sidebar(session_id, source_pane)


def record_text(record: dict) -> str:
    archive = record.get("archive") or {}
    return " ".join(
        [
            str(record.get("title", "")),
            str(record.get("summary", "")),
            str(archive.get("goal", "")),
            " ".join(str(item) for item in archive.get("key_points") or []),
            " ".join(str(item) for item in archive.get("keywords") or []),
            str(record.get("cwd", "")),
            str(record.get("source", "")),
        ]
    ).lower()


def project_records(records: list[dict], project_path: str) -> list[dict]:
    target = os.path.realpath(project_path)
    prefix = target + os.sep
    project_name = Path(target).name.lower()
    matches = []
    for record in records:
        record_cwd = str(record.get("cwd", ""))
        if not record_cwd:
            continue
        resolved = os.path.realpath(record_cwd)
        parent_match = target.startswith(resolved + os.sep) and project_name in record_text(record)
        if resolved == target or resolved.startswith(prefix) or parent_match:
            matches.append(record)
    return matches


def clip(text: object, width: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if width <= 1:
        return ""
    if display_width(value) <= width:
        return value
    result = []
    used = 0
    for character in value:
        character_width = terminal_character_width(character)
        if used + character_width > width - 1:
            break
        result.append(character)
        used += character_width
    return "".join(result).rstrip() + "…"


def terminal_character_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    return 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(terminal_character_width(character) for character in text)


def wrap_display(text: object, width: int) -> list[str]:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return []
    lines = []
    current = []
    used = 0
    for character in value:
        character_width = terminal_character_width(character)
        if current and used + character_width > width:
            lines.append("".join(current))
            current = []
            used = 0
        current.append(character)
        used += character_width
    if current:
        lines.append("".join(current).rstrip())
    return lines


def format_time(record: dict) -> str:
    timestamp = record_timestamp(record)
    if timestamp <= 0:
        return "--/--"
    return time.strftime("%m/%d", time.localtime(timestamp))


def record_detail_lines(record: dict, width: int) -> list[str]:
    archive = record.get("archive") or {}
    fields = [
        ("Title", record.get("title")),
        ("ID", record.get("cs_id")),
        ("Source", record.get("source")),
        ("CWD", record.get("cwd")),
        ("Summary", record.get("summary")),
        ("Goal", archive.get("goal")),
    ]
    lines = []
    content_width = max(12, width - 2)
    for label, value in fields:
        if not value:
            continue
        wrapped = wrap_display(f"{label}: {value}", content_width) or [f"{label}:"]
        lines.extend(wrapped)
        lines.append("")
    points = archive.get("key_points") or []
    if points:
        lines.append("Key points:")
        for point in points:
            lines.extend(wrap_display(f"- {point}", content_width) or ["-"])
        lines.append("")
    keywords = archive.get("keywords") or []
    if keywords:
        lines.extend(wrap_display(f"Keywords: {', '.join(keywords)}", content_width))
    return lines


def add_line(window: curses.window, row: int, text: str, attr: int = 0) -> None:
    height, width = window.getmaxyx()
    if row < 0 or row >= height or width < 2:
        return
    try:
        window.addstr(row, 0, clip(text, width - 1), attr)
    except curses.error:
        pass


class ArchiveSidebar:
    def __init__(self, cwd: str, session_id: str, source_pane: str) -> None:
        self.cwd = cwd
        self.session_id = session_id
        self.source_pane = source_pane
        _, _, self.project_path = project_context(cwd)
        self.all_records: list[dict] = []
        self.records: list[dict] = []
        self.scope = "project"
        self.query = ""
        self.selected = 0
        self.detail = False
        self.detail_offset = 0
        self.scope_fallback = False
        self.visible_start = 0
        self.visible_count = 0
        self.footer_actions: list[tuple[int, int, str]] = []
        self.index_mtime = -1.0
        self.last_context_refresh = 0.0
        self.thread_id = ""
        self.current_title = ""
        self.context_live = False
        self.agent_active = False
        self.agent_label = ""
        self.reload(force=True)

    def reload(self, force: bool = False) -> None:
        try:
            mtime = INDEX_PATH.stat().st_mtime
        except OSError:
            mtime = 0.0
        if force or mtime != self.index_mtime:
            self.index_mtime = mtime
            self.all_records = load_records()
        now = time.monotonic()
        if force or now - self.last_context_refresh >= 2:
            self.last_context_refresh = now
            source_cwd = tmux_value(self.source_pane, "#{pane_current_path}")
            if source_cwd and source_cwd != self.cwd:
                self.cwd = source_cwd
                _, _, self.project_path = project_context(source_cwd)
            live_thread_id, live_title, agent_home, live_raw_path = current_context(self.source_pane, self.all_records)
            self.agent_active = bool(agent_home)
            self.agent_label = agent_label(live_raw_path, agent_home)
            if live_title:
                self.thread_id = live_thread_id
                self.current_title = live_title
                self.context_live = True
            else:
                self.thread_id = tmux_value(self.session_id, "#{@session_context_thread_id}")
                self.current_title = tmux_value(self.session_id, "#{@session_context_title}")
                self.context_live = False
            rename_session(self.session_id, self.source_pane)
        self.apply_filters()

    def apply_filters(self) -> None:
        records = self.all_records
        self.scope_fallback = False
        if self.scope == "project":
            matches = project_records(records, self.project_path)
            self.scope_fallback = not matches
            records = matches if matches else records
        if self.query:
            query = self.query.lower()
            records = [record for record in records if query in record_text(record)]
        self.records = records
        self.selected = min(self.selected, max(0, len(records) - 1))

    def prompt_search(self, window: curses.window) -> None:
        height, width = window.getmaxyx()
        curses.echo()
        curses.curs_set(1)
        try:
            window.timeout(-1)
            window.move(height - 1, 0)
            window.clrtoeol()
            window.addnstr(height - 1, 0, "/", width - 1)
            value = window.getstr(height - 1, 1, max(1, width - 2))
            self.query = value.decode(errors="replace").strip()
            self.selected = 0
            self.detail = False
            self.apply_filters()
        except curses.error:
            pass
        finally:
            window.timeout(50)
            curses.noecho()
            curses.curs_set(0)

    def draw_list(self, window: curses.window) -> None:
        height, width = window.getmaxyx()
        if self.scope == "project" and self.scope_fallback:
            scope = "全部·项目无记录"
        else:
            scope = "当前项目" if self.scope == "project" else "全部"
        add_line(window, 0, f" AI History [{scope}] {len(self.records)}/{len(self.all_records)} ", curses.A_REVERSE)
        if self.current_title:
            label = "当前" if self.context_live else "最近"
            context = self.current_title
        elif self.agent_active:
            label = "当前"
            context = f"{self.agent_label or 'AI CLI'} 已启动，等待首条消息"
        else:
            label = "当前"
            context = "此 tmux session 未运行 Codex / Claude"
        add_line(window, 1, f"{label}: {clip(context, width - 4)}", curses.A_BOLD)
        query_text = f"搜索: {self.query}" if self.query else "搜索: -"
        add_line(window, 2, query_text)

        if not self.records:
            add_line(window, 4, "没有匹配的已归档会话")
            self.draw_action_bar(window, [("Scope", "scope"), ("Search", "search"), ("Refresh", "refresh"), ("Close", "close")])
            return

        visible_count = max(1, (height - 5) // 2)
        start = min(max(0, self.selected - visible_count + 1), max(0, len(self.records) - visible_count))
        self.visible_start = start
        self.visible_count = visible_count
        row = 4
        for index in range(start, min(len(self.records), start + visible_count)):
            record = self.records[index]
            selected = index == self.selected
            current = record.get("session_id") == self.thread_id
            marker = "*" if current else " "
            title = clip(record.get("title") or "(no title)", max(8, width - 19))
            line = f"{marker}{format_time(record)} {record.get('cs_id', '?')} {title}"
            attr = curses.A_REVERSE if selected else (curses.A_BOLD if current else 0)
            add_line(window, row, line, attr)
            archive = record.get("archive") or {}
            summary = archive.get("goal") or record.get("summary") or ""
            add_line(window, row + 1, f"  {clip(summary, width - 3)}", attr)
            row += 2
        self.draw_action_bar(window, [("Scope", "scope"), ("Search", "search"), ("Refresh", "refresh"), ("Close", "close")])

    def draw_detail(self, window: curses.window) -> None:
        height, width = window.getmaxyx()
        if not self.records:
            self.detail = False
            self.draw_list(window)
            return
        record = self.records[self.selected]
        add_line(window, 0, f" {record.get('cs_id', '?')} 会话详情 ", curses.A_REVERSE)
        lines = record_detail_lines(record, width)
        max_offset = max(0, len(lines) - max(1, height - 2))
        self.detail_offset = min(self.detail_offset, max_offset)
        for row, line in enumerate(lines[self.detail_offset : self.detail_offset + height - 2], 1):
            add_line(window, row, line)
        self.draw_action_bar(window, [("Back", "back"), ("Refresh", "refresh"), ("Close", "close")])

    def draw_action_bar(self, window: curses.window, actions: list[tuple[str, str]]) -> None:
        height, _ = window.getmaxyx()
        text = " "
        position = 1
        self.footer_actions = []
        for label, action in actions:
            token = f"[{label}] "
            token_width = display_width(token)
            self.footer_actions.append((position, position + token_width - 1, action))
            text += token
            position += token_width
        add_line(window, height - 1, text, curses.A_REVERSE)

    def run_action(self, action: str, window: curses.window) -> bool:
        if action == "scope":
            self.scope = "all" if self.scope == "project" else "project"
            self.selected = 0
            self.detail = False
            self.apply_filters()
        elif action == "search":
            self.prompt_search(window)
        elif action == "refresh":
            self.reload(force=True)
        elif action == "back":
            self.detail = False
            self.detail_offset = 0
        elif action == "close":
            return True
        return False

    def handle_mouse(self, window: curses.window) -> bool:
        try:
            _, mouse_x, mouse_y, _, button_state = curses.getmouse()
        except curses.error:
            return False

        wheel_up = getattr(curses, "BUTTON4_PRESSED", 0)
        wheel_down = getattr(curses, "BUTTON5_PRESSED", 0)
        left_click = (
            getattr(curses, "BUTTON1_PRESSED", 0)
            | getattr(curses, "BUTTON1_CLICKED", 0)
            | getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
        )
        right_click = getattr(curses, "BUTTON3_PRESSED", 0) | getattr(curses, "BUTTON3_CLICKED", 0)

        if button_state & wheel_up:
            if self.detail:
                self.detail_offset = max(0, self.detail_offset - 2)
            else:
                self.selected = max(0, self.selected - 1)
            return False
        if button_state & wheel_down:
            if self.detail:
                self.detail_offset += 2
            else:
                self.selected = min(max(0, len(self.records) - 1), self.selected + 1)
            return False
        if self.detail and button_state & right_click:
            self.detail = False
            self.detail_offset = 0
            return False
        if not button_state & left_click:
            return False

        height, _ = window.getmaxyx()
        if mouse_y == height - 1:
            for start, end, action in self.footer_actions:
                if start <= mouse_x <= end:
                    return self.run_action(action, window)
            return False
        if not self.detail and mouse_y >= 4:
            visible_index = (mouse_y - 4) // 2
            record_index = self.visible_start + visible_index
            if visible_index < self.visible_count and record_index < len(self.records):
                self.selected = record_index
                self.detail = True
                self.detail_offset = 0
        return False

    def run(self, window: curses.window) -> None:
        curses.curs_set(0)
        curses.mouseinterval(150)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | getattr(curses, "REPORT_MOUSE_POSITION", 0))
        window.keypad(True)
        window.timeout(1000)
        while True:
            self.reload()
            window.erase()
            if self.detail:
                self.draw_detail(window)
            else:
                self.draw_list(window)
            window.refresh()
            try:
                key = window.get_wch()
            except curses.error:
                continue
            if key == curses.KEY_MOUSE:
                if self.handle_mouse(window):
                    return
                continue
            if key in ("q", "Q"):
                return
            if self.detail:
                if key in ("b", "B", "\x1b", curses.KEY_LEFT):
                    self.detail = False
                    self.detail_offset = 0
                elif key in ("j", curses.KEY_DOWN):
                    self.detail_offset += 1
                elif key in ("k", curses.KEY_UP):
                    self.detail_offset = max(0, self.detail_offset - 1)
                elif key == curses.KEY_NPAGE:
                    self.detail_offset += max(1, window.getmaxyx()[0] - 3)
                elif key == curses.KEY_PPAGE:
                    self.detail_offset = max(0, self.detail_offset - max(1, window.getmaxyx()[0] - 3))
                continue
            if key in ("j", curses.KEY_DOWN):
                self.selected = min(max(0, len(self.records) - 1), self.selected + 1)
            elif key in ("k", curses.KEY_UP):
                self.selected = max(0, self.selected - 1)
            elif key in ("\n", "\r", curses.KEY_ENTER, " ") and self.records:
                self.detail = True
                self.detail_offset = 0
            elif key in ("a", "A"):
                self.scope = "all" if self.scope == "project" else "project"
                self.selected = 0
                self.apply_filters()
            elif key == "/":
                self.prompt_search(window)
            elif key in ("c", "C", "\x1b"):
                self.query = ""
                self.selected = 0
                self.apply_filters()
            elif key in ("r", "R"):
                self.reload(force=True)


def is_claude_transcript(raw_path: str) -> bool:
    parts = Path(raw_path).parts
    return "projects" in parts and any(part.startswith(".claude") for part in parts)


def is_final_reply(message: dict) -> bool:
    return message.get("role") == "assistant" and message.get("phase") in ("final", "final_answer")


def clip_transcript_text(text: str) -> str:
    if len(text) > MAX_TRANSCRIPT_MESSAGE_CHARS:
        return text[: MAX_TRANSCRIPT_MESSAGE_CHARS - 1].rstrip() + "…"
    return text


def transcript_messages(lines: Iterable[str], raw_path: str) -> list[dict]:
    parse = claude_transcript_messages if is_claude_transcript(raw_path) else codex_transcript_messages
    return parse(lines)


def codex_transcript_messages(lines: Iterable[str]) -> list[dict]:
    messages = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "response_item":
            continue
        payload = entry.get("payload") or {}
        if payload.get("type") != "message":
            continue
        role = payload.get("role")
        if role not in ("user", "assistant"):
            continue
        chunks = []
        for item in payload.get("content") or []:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
        text = "\n".join(chunks).strip()
        if not text:
            continue
        if role == "user" and text.startswith("# AGENTS.md instructions for ") and "<environment_context>" in text:
            continue
        messages.append(
            {
                "role": role,
                "phase": payload.get("phase") or "",
                "text": clip_transcript_text(text),
                "timestamp": entry.get("timestamp") or "",
            }
        )
    return messages


def claude_block_text(content: object, kinds: tuple[str, ...]) -> str:
    if isinstance(content, str):
        return content.strip() if "text" in kinds else ""
    if not isinstance(content, list):
        return ""
    chunks = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type") or "text"
        if kind not in kinds:
            continue
        text = item.get("thinking") if kind == "thinking" else item.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    return "\n".join(chunks).strip()


CLAUDE_NOISE_USER_PATTERN = re.compile(
    r"^(?:\[Request interrupted|Caveat: The messages below|API Error|<local-command-stdout>)",
)


def clean_claude_user_text(text: str) -> str:
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.DOTALL)
    text = re.sub(r"</?(?:command-name|command-message|command-args|local-command-stdout)>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def claude_transcript_messages(lines: Iterable[str]) -> list[dict]:
    """Claude Code writes one JSON record per line, with tool traffic mixed into the roles."""
    messages = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = entry.get("type")
        if role not in ("user", "assistant") or entry.get("isSidechain") or entry.get("isMeta"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        timestamp = entry.get("timestamp") or ""
        if role == "user":
            raw_text = claude_block_text(message.get("content"), ("text",))
            if not raw_text or CLAUDE_NOISE_USER_PATTERN.match(raw_text):
                continue
            text = clean_claude_user_text(raw_text)
            if not text:
                continue
            messages.append({"role": "user", "phase": "", "text": clip_transcript_text(text), "timestamp": timestamp})
            continue
        reasoning = claude_block_text(message.get("content"), ("thinking",))
        if reasoning:
            messages.append(
                {
                    "role": "assistant",
                    "phase": "commentary",
                    "text": clip_transcript_text(reasoning),
                    "timestamp": timestamp,
                }
            )
        answer = claude_block_text(message.get("content"), ("text",))
        if answer:
            messages.append(
                {
                    "role": "assistant",
                    "phase": "final",
                    "text": clip_transcript_text(answer),
                    "timestamp": timestamp,
                }
            )
    return mark_claude_intermediate_replies(messages)


def mark_claude_intermediate_replies(messages: list[dict]) -> list[dict]:
    """Only the last answer of a turn is the final one; earlier ones narrate tool work."""
    answer_indexes: list[int] = []

    def demote() -> None:
        for index in answer_indexes[:-1]:
            messages[index]["phase"] = "commentary"
        answer_indexes.clear()

    for index, message in enumerate(messages):
        if message["role"] == "user":
            demote()
        elif message["phase"] == "final":
            answer_indexes.append(index)
    demote()
    return messages


def load_current_session_messages(raw_path: str) -> list[dict]:
    if not raw_path:
        return []
    try:
        with Path(raw_path).open(encoding="utf-8", errors="replace") as stream:
            return transcript_messages(stream, raw_path)
    except OSError:
        return []


def group_session_turns(messages: list[dict]) -> list[dict]:
    turns = []
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            turns.append(
                {
                    "key": f"{message.get('timestamp', '')}:{index}",
                    "user": message,
                    "replies": [],
                }
            )
        elif turns:
            turns[-1]["replies"].append(message)
    return turns


def transcript_timestamp(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def transcript_time(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def transcript_header_field_ranges(text: str) -> list[tuple[int, int, str]]:
    match = re.match(
        r"^[▶▼]\s+(?P<number>\d+)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2})$",
        text,
    )
    if not match:
        return []
    return [
        (match.start("number"), match.end("number"), "number"),
        (match.start("date"), match.end("date"), "date"),
        (match.start("time"), match.end("time"), "time"),
    ]


def wrap_transcript_text(text: str, width: int) -> list[str]:
    lines = []
    for source_line in text.splitlines() or [""]:
        if not source_line.strip():
            lines.append("")
            continue
        lines.extend(wrap_display(source_line, width) or [""])
    return lines


def clean_sidebar_selection_text(text: str) -> str:
    sections: list[str] = []
    fragments: list[str] = []

    def finish_section() -> None:
        value = "".join(fragments).strip()
        if value:
            sections.append(value)
        fragments.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"[─━]+", stripped) or re.match(r"^[▶▼]\s+\d+", stripped):
            finish_section()
            continue
        if re.fullmatch(r"│\s*(?:你|AI(?:·过程)?)(?:\s+\d{2}:\d{2})?", line):
            finish_section()
            continue
        if line == "│":
            finish_section()
            continue
        if line.startswith("│   "):
            fragments.append(line[4:])
        elif line.startswith("│ "):
            fragments.append(line[2:])
        else:
            fragments.append(line)
    finish_section()
    return "\n\n".join(sections)


def copy_text_to_clipboard(text: str) -> str:
    if not text:
        return ""
    if os.environ.get("WSL_DISTRO_NAME"):
        windows_powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        if windows_powershell.is_file():
            try:
                result = subprocess.run(
                    [
                        str(windows_powershell),
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false); "
                        "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
                    ],
                    input=text.encode("utf-8"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except OSError:
                pass
            else:
                if result.returncode == 0:
                    return "Windows clipboard"
    commands = []
    windows_clipboard = Path("/mnt/c/Windows/System32/clip.exe")
    if os.environ.get("WSL_DISTRO_NAME") and windows_clipboard.is_file():
        commands.append(([str(windows_clipboard)], "Windows clipboard"))
    if shutil.which("wl-copy"):
        commands.append((["wl-copy"], "Wayland clipboard"))
    if shutil.which("xclip"):
        commands.append((["xclip", "-selection", "clipboard"], "X11 clipboard"))
    for command, destination in commands:
        try:
            result = subprocess.run(
                command,
                input=text,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            continue
        if result.returncode == 0:
            return destination
    try:
        result = subprocess.run(
            ["tmux", "load-buffer", "-"],
            input=text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return ""
    return "tmux buffer" if result.returncode == 0 else ""


def copy_stdin_to_clipboard() -> int:
    text = clean_sidebar_selection_text(sys.stdin.read())
    if not text:
        return 0
    return 0 if copy_text_to_clipboard(text) else 1


def copy_raw_stdin_to_clipboard() -> int:
    text = sys.stdin.read()
    if not text:
        return 0
    return 0 if copy_text_to_clipboard(text) else 1


class Sidebar:
    def __init__(self, cwd: str, session_id: str, source_pane: str) -> None:
        self.cwd = cwd
        self.session_id = session_id
        self.source_pane = source_pane
        self.thread_id = ""
        self.raw_path = ""
        self.current_title = ""
        self.context_live = False
        self.agent_active = False
        self.agent_label = ""
        self.all_messages: list[dict] = []
        self.turns: list[dict] = []
        self.include_commentary = True
        self.query = ""
        self.raw_mtime = -1.0
        self.last_context_refresh = 0.0
        self.scroll_top = 0
        self.max_scroll = 0
        self.follow_latest = True
        self.selected_turn = 0
        self.expanded_turn_key = ""
        self.turn_ranges: list[tuple[int, int, int]] = []
        self.transcript_generation = 0
        self.transcript_cache_key: tuple | None = None
        self.transcript_cache_lines: list[tuple[str, int]] = []
        self.ensure_selection = False
        self.footer_actions: list[tuple[int, int, str]] = []
        self.header_attr = curses.A_REVERSE
        self.title_attr = curses.A_BOLD
        self.meta_attr = 0
        self.footer_attr = curses.A_REVERSE
        self.collapsed_header_attr = curses.A_BOLD
        self.collapsed_body_attr = 0
        self.separator_attr = curses.A_DIM
        self.selected_attr = curses.A_BOLD | curses.A_UNDERLINE
        self.expanded_header_attr = curses.A_BOLD | curses.A_UNDERLINE
        self.user_body_attr = curses.A_DIM
        self.ai_final_attr = curses.A_DIM
        self.ai_commentary_attr = curses.A_DIM
        self.turn_number_attr = curses.A_BOLD
        self.turn_date_attr = curses.A_BOLD | curses.A_UNDERLINE
        self.turn_time_attr = curses.A_BOLD
        self.reload(force=True)

    def init_colors(self) -> None:
        if not curses.has_colors():
            return
        try:
            curses.start_color()
            default_foreground = -1
            default_background = -1
            try:
                curses.use_default_colors()
            except curses.error:
                default_foreground = curses.COLOR_WHITE
                default_background = curses.COLOR_BLACK
            color_specs = (
                (1, default_foreground, default_background),
                (4, default_foreground, curses.COLOR_MAGENTA),
                (8, default_foreground, curses.COLOR_BLUE),
                (9, curses.COLOR_CYAN, default_background),
                (10, curses.COLOR_MAGENTA, default_background),
                (11, curses.COLOR_YELLOW, default_background),
            )
            for pair_id, foreground, background in color_specs:
                curses.init_pair(pair_id, foreground, background)
        except curses.error:
            return
        self.header_attr = curses.color_pair(8) | curses.A_BOLD
        self.title_attr = curses.color_pair(1) | curses.A_BOLD
        self.meta_attr = curses.color_pair(1) | curses.A_DIM
        self.footer_attr = curses.color_pair(8) | curses.A_BOLD
        self.collapsed_header_attr = curses.color_pair(1) | curses.A_BOLD
        self.collapsed_body_attr = curses.color_pair(1)
        self.separator_attr = curses.color_pair(1) | curses.A_DIM
        self.selected_attr = curses.color_pair(1) | curses.A_BOLD | curses.A_UNDERLINE
        self.expanded_header_attr = curses.color_pair(1) | curses.A_BOLD | curses.A_UNDERLINE
        self.user_body_attr = curses.color_pair(1) | curses.A_DIM
        self.ai_final_attr = curses.color_pair(1) | curses.A_DIM
        self.ai_commentary_attr = curses.color_pair(1) | curses.A_DIM
        self.turn_number_attr = curses.color_pair(9) | curses.A_BOLD
        self.turn_date_attr = curses.color_pair(10) | curses.A_BOLD
        self.turn_time_attr = curses.color_pair(11) | curses.A_BOLD

    def reload(self, force: bool = False) -> None:
        now = time.monotonic()
        previous_raw_path = self.raw_path
        if force or now - self.last_context_refresh >= 2:
            self.last_context_refresh = now
            source_cwd = tmux_value(self.source_pane, "#{pane_current_path}")
            if source_cwd:
                self.cwd = source_cwd
            live_thread_id, live_title, agent_home, live_raw_path = current_context(self.source_pane)
            self.agent_active = bool(agent_home)
            self.agent_label = agent_label(live_raw_path or self.raw_path, agent_home)
            if live_thread_id:
                self.thread_id = live_thread_id
            else:
                self.thread_id = tmux_value(self.session_id, "#{@session_context_thread_id}")
            if live_title:
                self.current_title = live_title
                self.context_live = True
            else:
                self.current_title = tmux_value(self.session_id, "#{@session_context_title}")
                self.context_live = False
            if live_raw_path:
                self.raw_path = live_raw_path
            elif not self.raw_path:
                self.raw_path = tmux_value(self.session_id, "#{@session_context_raw_path}")
            rename_session(self.session_id, self.source_pane)

        try:
            raw_mtime = Path(self.raw_path).stat().st_mtime if self.raw_path else 0.0
        except OSError:
            raw_mtime = 0.0
        if force or self.raw_path != previous_raw_path or raw_mtime != self.raw_mtime:
            self.raw_mtime = raw_mtime
            self.all_messages = load_current_session_messages(self.raw_path)
            self.apply_filters()

    def apply_filters(self) -> None:
        turns = group_session_turns(self.all_messages)
        if not self.include_commentary:
            turns = [
                {**turn, "replies": [reply for reply in turn["replies"] if reply.get("phase") != "commentary"]}
                for turn in turns
            ]
        if self.query:
            query = self.query.lower()
            turns = [
                turn
                for turn in turns
                if query
                in " ".join(
                    [str(turn["user"].get("text", ""))]
                    + [str(reply.get("text", "")) for reply in turn["replies"]]
                ).lower()
            ]
        self.turns = turns
        self.transcript_generation += 1
        self.transcript_cache_key = None
        if self.follow_latest and turns:
            self.selected_turn = len(turns) - 1
        else:
            self.selected_turn = min(self.selected_turn, max(0, len(turns) - 1))
        if self.expanded_turn_key and not any(turn["key"] == self.expanded_turn_key for turn in turns):
            self.expanded_turn_key = ""

    def prompt_search(self, window: curses.window) -> None:
        height, width = window.getmaxyx()
        curses.echo()
        curses.curs_set(1)
        try:
            window.timeout(-1)
            window.move(height - 1, 0)
            window.clrtoeol()
            window.addnstr(height - 1, 0, "/", width - 1)
            value = window.getstr(height - 1, 1, max(1, width - 2))
            self.query = value.decode(errors="replace").strip()
            self.follow_latest = False
            self.scroll_top = 0
            self.selected_turn = 0
            self.expanded_turn_key = ""
            self.ensure_selection = True
            self.apply_filters()
        except curses.error:
            pass
        finally:
            window.timeout(50)
            curses.noecho()
            curses.curs_set(0)

    def transcript_lines(self, width: int) -> list[tuple[str, int]]:
        cache_key = (width, self.transcript_generation, self.expanded_turn_key, self.selected_turn)
        if cache_key == self.transcript_cache_key:
            return self.transcript_cache_lines
        lines: list[tuple[str, int]] = []

        def add_transcript_line(text: str, attr: int) -> None:
            lines.append((text, attr))

        self.turn_ranges = []
        content_width = max(8, width - 5)
        separator = "─" * max(1, width - 1)
        number_width = max(2, len(str(len(self.turns))))
        for index, turn in enumerate(self.turns):
            start = len(lines)
            expanded = turn["key"] == self.expanded_turn_key
            selected = index == self.selected_turn
            marker = "▼" if expanded else "▶"
            user = turn["user"]
            timestamp = transcript_time(user.get("timestamp"))
            header = f"{marker} {index + 1:0{number_width}d}  {timestamp}".rstrip()
            if expanded:
                header_attr = self.expanded_header_attr
            elif selected:
                header_attr = self.selected_attr
            else:
                header_attr = self.collapsed_header_attr
            add_transcript_line(header, header_attr)
            user_lines = wrap_transcript_text(str(user.get("text", "")), content_width)
            if expanded:
                add_transcript_line("│ 你", self.user_body_attr | curses.A_UNDERLINE)
                for line in user_lines:
                    add_transcript_line(f"│   {line}" if line else "│", self.user_body_attr)
                for reply in turn["replies"]:
                    phase = reply.get("phase")
                    label = "AI·过程" if phase == "commentary" else "AI"
                    attr = self.ai_commentary_attr if phase == "commentary" else self.ai_final_attr
                    reply_time = transcript_time(reply.get("timestamp"))
                    add_transcript_line(f"│ {label} {reply_time}".rstrip(), attr | curses.A_UNDERLINE)
                    reply_lines = wrap_transcript_text(str(reply.get("text", "")), content_width)
                    for line in reply_lines:
                        add_transcript_line(f"│   {line}" if line else "│", attr)
            else:
                preview = user_lines[:2]
                if len(user_lines) > 2 and preview:
                    preview[-1] = clip(preview[-1] + "…", content_width)
                preview_attr = self.selected_attr if selected else self.collapsed_body_attr
                for line in preview:
                    add_transcript_line(f"│ {line}" if line else "│", preview_attr)
            self.turn_ranges.append((start, len(lines) - 1, index))
            if index < len(self.turns) - 1:
                add_transcript_line(separator, self.separator_attr)
        self.transcript_cache_key = cache_key
        self.transcript_cache_lines = lines
        return lines

    def selected_range(self) -> tuple[int, int] | None:
        for start, end, index in self.turn_ranges:
            if index == self.selected_turn:
                return start, end
        return None

    def move_selection(self, amount: int) -> None:
        if not self.turns:
            return
        self.follow_latest = False
        self.selected_turn = min(max(0, self.selected_turn + amount), len(self.turns) - 1)
        self.ensure_selection = True

    def toggle_selected(self) -> None:
        if not self.turns:
            return
        key = self.turns[self.selected_turn]["key"]
        self.expanded_turn_key = "" if self.expanded_turn_key == key else key
        self.follow_latest = False
        self.ensure_selection = True

    def draw(self, window: curses.window) -> None:
        height, width = window.getmaxyx()
        mode = "含过程" if self.include_commentary else "仅最终"
        short_id = self.thread_id[:8] if self.thread_id else "waiting"
        add_line(window, 0, f" Current Session [{mode}] {short_id} ", self.header_attr)
        if self.current_title:
            label = "当前" if self.context_live else "最近"
            context = self.current_title
        elif self.agent_active:
            label = "当前"
            context = f"{self.agent_label or 'AI CLI'} 已启动，等待首条消息"
        else:
            label = "当前"
            context = "此 tmux session 未运行 Codex / Claude"
        add_line(window, 1, f"{label}: {clip(context, width - 4)}", self.title_attr)
        if self.query:
            query_text = f"搜索: {self.query}  输入: {len(self.turns)}"
        elif self.turns:
            query_text = f"输入: {len(self.turns)}  选中: {self.selected_turn + 1}/{len(self.turns)}"
        else:
            query_text = "输入: 0"
        add_line(window, 2, query_text, self.meta_attr)

        content_height = max(1, height - 4)
        lines = self.transcript_lines(width)
        self.max_scroll = max(0, len(lines) - content_height)
        if self.follow_latest:
            if self.turns:
                self.selected_turn = len(self.turns) - 1
            self.scroll_top = self.max_scroll
        else:
            self.scroll_top = min(max(0, self.scroll_top), self.max_scroll)
        if self.ensure_selection:
            selected_range = self.selected_range()
            if selected_range:
                start, end = selected_range
                if end - start + 1 > content_height:
                    self.scroll_top = start
                elif start < self.scroll_top:
                    self.scroll_top = start
                elif end >= self.scroll_top + content_height:
                    self.scroll_top = max(0, end - content_height + 1)
            self.ensure_selection = False

        if not self.raw_path:
            add_line(window, 4, f"等待当前 {self.agent_label or 'AI CLI'} session 文件…")
        elif not self.turns:
            add_line(window, 4, "当前 session 暂无匹配输入")
        else:
            for row, (line, attr) in enumerate(lines[self.scroll_top : self.scroll_top + content_height], 3):
                self.draw_transcript_line(window, row, line, attr)
        self.draw_action_bar(
            window,
            [
                ("Toggle", "toggle"),
                ("Mode", "mode"),
                ("Search", "search"),
                ("Top", "top"),
                ("Latest", "latest"),
                ("Close", "close"),
            ],
        )

    def draw_action_bar(self, window: curses.window, actions: list[tuple[str, str]]) -> None:
        height, _ = window.getmaxyx()
        text = " "
        position = 1
        self.footer_actions = []
        for label, action in actions:
            token = f"[{label}] "
            token_width = display_width(token)
            self.footer_actions.append((position, position + token_width - 1, action))
            text += token
            position += token_width
        add_line(window, height - 1, text, self.footer_attr)

    def draw_transcript_line(self, window: curses.window, row: int, text: str, attr: int) -> None:
        add_line(window, row, text, attr)
        fields = transcript_header_field_ranges(text)
        if not fields:
            return
        attrs = {
            "number": self.turn_number_attr,
            "date": self.turn_date_attr,
            "time": self.turn_time_attr,
        }
        try:
            window.move(row, 0)
            previous_end = 0
            for start, end, field in fields:
                window.addstr(text[previous_end:start], attr)
                window.addstr(text[start:end], attrs[field])
                previous_end = end
            if previous_end < len(text):
                window.addstr(text[previous_end:], attr)
        except curses.error:
            pass

    def scroll(self, amount: int) -> None:
        self.follow_latest = False
        self.scroll_top = min(max(0, self.scroll_top + amount), self.max_scroll)
        if self.scroll_top >= self.max_scroll:
            self.follow_latest = True

    def run_action(self, action: str, window: curses.window) -> bool:
        if action == "toggle":
            self.toggle_selected()
        elif action == "top":
            self.follow_latest = False
            self.selected_turn = 0
            self.ensure_selection = True
        elif action == "latest":
            self.follow_latest = True
            self.expanded_turn_key = ""
            if self.turns:
                self.selected_turn = len(self.turns) - 1
        elif action == "mode":
            self.include_commentary = not self.include_commentary
            self.ensure_selection = True
            self.apply_filters()
        elif action == "search":
            self.prompt_search(window)
        elif action == "refresh":
            self.reload(force=True)
        elif action == "close":
            return True
        return False

    def handle_mouse(self, window: curses.window) -> bool:
        try:
            _, mouse_x, mouse_y, _, button_state = curses.getmouse()
        except curses.error:
            return False
        wheel_up = getattr(curses, "BUTTON4_PRESSED", 0)
        wheel_down = getattr(curses, "BUTTON5_PRESSED", 0)
        left_released = (
            getattr(curses, "BUTTON1_RELEASED", 0)
            | getattr(curses, "BUTTON1_CLICKED", 0)
            | getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
        )
        if button_state & wheel_up:
            self.scroll(-3)
            return False
        if button_state & wheel_down:
            self.scroll(3)
            return False
        height, _ = window.getmaxyx()
        position = None
        if 3 <= mouse_y < height - 1:
            position = (self.scroll_top + mouse_y - 3, mouse_x)
        if not button_state & left_released:
            return False
        if mouse_y == height - 1:
            for start, end, action in self.footer_actions:
                if start <= mouse_x <= end:
                    return self.run_action(action, window)
            return False
        if position:
            absolute_line = position[0]
            for start, end, index in self.turn_ranges:
                if start <= absolute_line <= end:
                    self.selected_turn = index
                    self.toggle_selected()
                    break
        return False

    def run(self, window: curses.window) -> None:
        self.init_colors()
        curses.curs_set(0)
        curses.mouseinterval(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | getattr(curses, "REPORT_MOUSE_POSITION", 0))
        window.keypad(True)
        window.timeout(50)
        while True:
            self.reload()
            window.erase()
            self.draw(window)
            window.refresh()
            try:
                key = window.get_wch()
            except curses.error:
                continue
            if key == curses.KEY_MOUSE:
                if self.handle_mouse(window):
                    return
            elif key in ("q", "Q"):
                return
            elif key in ("j", curses.KEY_DOWN):
                self.move_selection(1)
            elif key in ("k", curses.KEY_UP):
                self.move_selection(-1)
            elif key in ("\n", "\r", curses.KEY_ENTER, " "):
                self.toggle_selected()
            elif key == curses.KEY_NPAGE:
                self.scroll(max(1, window.getmaxyx()[0] - 5))
            elif key == curses.KEY_PPAGE:
                self.scroll(-max(1, window.getmaxyx()[0] - 5))
            elif key in ("g", curses.KEY_HOME):
                self.follow_latest = False
                self.selected_turn = 0
                self.ensure_selection = True
            elif key in ("G", curses.KEY_END):
                self.run_action("latest", window)
            elif key in ("m", "M"):
                self.run_action("mode", window)
            elif key == "/":
                self.prompt_search(window)
            elif key in ("c", "C", "\x1b"):
                self.query = ""
                self.follow_latest = True
                self.expanded_turn_key = ""
                self.apply_filters()
            elif key in ("r", "R"):
                self.reload(force=True)


def render_transcript_plain(session_id: str, source_pane: str, limit: int = 8) -> None:
    thread_id, _, _, raw_path = current_context(source_pane)
    raw_path = raw_path or tmux_value(session_id, "#{@session_context_raw_path}")
    messages = load_current_session_messages(raw_path)
    print(f"Current Session: {thread_id or 'waiting'}")
    for message in messages[-limit:]:
        label = "USER" if message.get("role") == "user" else "AI"
        print(f"[{label}] {clip(message.get('text', ''), 120)}")


def render_plain(cwd: str, limit: int) -> None:
    _, _, project_path = project_context(cwd)
    records = load_records()
    scoped = project_records(records, project_path) or records
    print(f"AI History: {len(scoped)}/{len(records)} archived sessions")
    for record in scoped[:limit]:
        archive = record.get("archive") or {}
        goal = archive.get("goal") or record.get("summary") or ""
        print(f"[{record.get('cs_id', '?')}] {record.get('title', '(no title)')}")
        if goal:
            print(f"  {goal}")


def strip_managed_block(text: str, start: str, end: str) -> str:
    lines = text.splitlines()
    output = []
    skipping = False
    for line in lines:
        if line == start:
            skipping = True
            continue
        if skipping:
            if line == end:
                skipping = False
            continue
        output.append(line)
    while output and not output[-1].strip():
        output.pop()
    return "\n".join(output) + ("\n" if output else "")


def managed_source_block(config_path: Path | None = None) -> str:
    config_path = config_path or TMUX_CONFIG_PATH
    return (
        f'{MANAGED_BLOCK_START}\n'
        f'source-file "{config_path}"\n'
        f'{MANAGED_BLOCK_END}\n'
    )


def generated_tmux_config(entrypoint: Path = ENTRYPOINT) -> str:
    command = str(entrypoint).replace('"', '\\"')
    return f"""# Generated by tmux-context install. Edit the tmux-context source instead.
set -g focus-events on
set -g mouse on
bind-key -T root MouseDrag1Pane if-shell -F '#{{@session_context_sidebar}}' 'copy-mode -M' 'if-shell -F "#{{||:#{{alternate_on}},#{{pane_in_mode}},#{{mouse_any_flag}}}}" "send-keys -M" "copy-mode -M"'
bind-key -T copy-mode MouseDragEnd1Pane if-shell -F '#{{@session_context_sidebar}}' {{ send-keys -X copy-pipe-and-cancel "{command} copy-stdin" }} {{ send-keys -X copy-pipe-and-cancel "{command} copy-raw-stdin" }}
bind-key -T copy-mode-vi MouseDragEnd1Pane if-shell -F '#{{@session_context_sidebar}}' {{ send-keys -X copy-pipe-and-cancel "{command} copy-stdin" }} {{ send-keys -X copy-pipe-and-cancel "{command} copy-raw-stdin" }}
set -g set-titles on
set -g set-titles-string '#S'
set -g status-interval 5
set -g status-left-length 90
set -g status-left '#[bold]#S#[default] '
set -g status-right '#[fg=colour244]%Y-%m-%d %H:%M'

set-hook -g after-new-session 'run-shell -b "{command} setup #{{q:session_id}} #{{q:pane_id}}"'
set-hook -g client-attached 'run-shell -b "{command} setup #{{q:session_id}} #{{q:pane_id}}"'
set-hook -g client-session-changed 'run-shell -b "{command} rename #{{q:session_id}} #{{q:pane_id}}"'
set-hook -g after-select-pane 'run-shell -b "{command} rename #{{q:session_id}} #{{q:pane_id}}"'

bind-key H run-shell -b '"{command}" toggle #{{q:session_id}} #{{q:pane_id}}'
bind-key R run-shell -b '"{command}" rename #{{q:session_id}} #{{q:pane_id}}'
"""


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(text)
    os.replace(temporary_path, path)


def backup_file(path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    backup_dir = TMUX_RUNTIME_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{label}-{timestamp}"
    shutil.copy2(path, backup_path)
    return backup_path


def install_tmux_integration() -> int:
    TMUX_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    write_text_atomic(TMUX_CONFIG_PATH, generated_tmux_config())

    current = USER_TMUX_CONFIG_PATH.read_text() if USER_TMUX_CONFIG_PATH.exists() else ""
    updated = strip_managed_block(current, CS_MANAGER_BLOCK_START, CS_MANAGER_BLOCK_END)
    updated = strip_managed_block(updated, MANAGED_BLOCK_START, MANAGED_BLOCK_END)
    if updated and not updated.endswith("\n\n"):
        updated = updated.rstrip() + "\n\n"
    updated += managed_source_block()
    backup_path = None
    if updated != current:
        backup_path = backup_file(USER_TMUX_CONFIG_PATH, "tmux.conf")
        write_text_atomic(USER_TMUX_CONFIG_PATH, updated)

    source_result = run_tmux("source-file", str(TMUX_CONFIG_PATH))
    print(f"Installed tmux integration: {TMUX_CONFIG_PATH}")
    print(f"Managed tmux entry: {USER_TMUX_CONFIG_PATH}")
    if backup_path:
        print(f"Backup: {backup_path}")
    if source_result.returncode != 0:
        print("No active tmux server was reloaded; new servers will load the configuration automatically.")
    return 0


def uninstall_tmux_integration() -> int:
    current = USER_TMUX_CONFIG_PATH.read_text() if USER_TMUX_CONFIG_PATH.exists() else ""
    updated = strip_managed_block(current, MANAGED_BLOCK_START, MANAGED_BLOCK_END)
    backup_path = None
    if updated != current:
        backup_path = backup_file(USER_TMUX_CONFIG_PATH, "tmux.conf-uninstall")
        write_text_atomic(USER_TMUX_CONFIG_PATH, updated)
    if TMUX_CONFIG_PATH.exists():
        TMUX_CONFIG_PATH.unlink()
    print("Removed tmux-context integration.")
    if backup_path:
        print(f"Backup: {backup_path}")
    return 0


def windows_terminal_settings_path() -> Path | None:
    override = os.environ.get("TMUX_CONTEXT_WINDOWS_TERMINAL_SETTINGS", "")
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None

    powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if powershell.is_file():
        result = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-Command",
                "[Environment]::GetFolderPath('LocalApplicationData')",
            ],
            cwd="/mnt/c/Windows",
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        local_app_data = result.stdout.strip().replace("\r", "")
        if local_app_data:
            converted = subprocess.run(
                ["wslpath", "-u", local_app_data],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout.strip()
            candidate = Path(converted) / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json"
            if candidate.is_file():
                return candidate

    candidates = sorted(Path("/mnt/c/Users").glob("*/AppData/Local/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json"))
    return candidates[0] if candidates else None


def configure_windows_terminal_title_settings(data: dict) -> tuple[list[str], bool, bool]:
    profiles = (data.get("profiles") or {}).get("list") or []
    changed_profiles = []
    for profile in profiles:
        commandline = str(profile.get("commandline", "")).lower()
        if profile.get("source") == "Microsoft.WSL" or commandline.startswith("wsl.exe"):
            if profile.get("suppressApplicationTitle") is not False:
                profile["suppressApplicationTitle"] = False
                changed_profiles.append(str(profile.get("guid") or profile.get("name") or "WSL"))

    shortcut = "ctrl+shift+f12"
    action_id = "Terminal.OpenTabRenamer"
    keybindings = data.get("keybindings")
    if keybindings is None:
        keybindings = []
        data["keybindings"] = keybindings
    if not isinstance(keybindings, list):
        return changed_profiles, False, True

    matching_key = [
        binding for binding in keybindings
        if isinstance(binding, dict) and str(binding.get("keys", "")).lower() == shortcut
    ]
    if any(binding.get("id") == action_id for binding in matching_key):
        return changed_profiles, False, False
    if matching_key:
        return changed_profiles, False, True
    keybindings.append({"id": action_id, "keys": shortcut})
    return changed_profiles, True, False


def enable_windows_terminal_titles() -> int:
    settings_path = windows_terminal_settings_path()
    if not settings_path:
        print("Windows Terminal settings.json was not found.", file=sys.stderr)
        return 1
    data = json.loads(settings_path.read_text())
    changed_profiles, shortcut_added, shortcut_conflict = configure_windows_terminal_title_settings(data)
    if not changed_profiles and not shortcut_added:
        print(f"Windows Terminal title settings are already configured: {settings_path}")
        if shortcut_conflict:
            print("Ctrl+Shift+F12 is already assigned; reset fixed tab titles from the tab context menu.", file=sys.stderr)
        return 0
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = settings_path.with_name(f"settings.json.bak-{timestamp}")
    shutil.copy2(settings_path, backup_path)
    write_text_atomic(settings_path, json.dumps(data, ensure_ascii=False, indent=4) + "\n")
    if changed_profiles:
        print(f"Updated Windows Terminal profiles: {', '.join(changed_profiles)}")
    if shortcut_added:
        print("Installed tab-title reset shortcut: Ctrl+Shift+F12")
    if shortcut_conflict:
        print("Ctrl+Shift+F12 is already assigned; reset fixed tab titles from the tab context menu.", file=sys.stderr)
    print(f"Backup: {backup_path}")
    print("Close all Windows Terminal windows before testing a new Ubuntu tab.")
    return 0


def terminal_title_sequence(title: str) -> bytes:
    clean_title = re.sub(r"[\x00-\x1f\x7f]+", " ", title).strip()
    return f"\033]0;{clean_title}\007\033]2;{clean_title}\007".encode()


def title_for_client(terminal_tty: str) -> str:
    result = run_tmux("list-clients", "-F", "#{client_tty}|#{session_name}")
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        client_tty, separator, session_name = line.partition("|")
        if separator and client_tty == terminal_tty:
            return session_name
    return ""


def watch_terminal_title(terminal_tty: str, terminal_fd: int, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        title = title_for_client(terminal_tty)
        if title:
            try:
                os.write(terminal_fd, terminal_title_sequence(title))
            except OSError:
                return
        stop_event.wait(2)


def attach_with_terminal_title(session_id: str) -> int:
    try:
        terminal_tty = os.ttyname(sys.stdin.fileno())
        terminal_fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
    except OSError:
        return subprocess.call(["tmux", "attach-session", "-t", session_id])

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=watch_terminal_title,
        args=(terminal_tty, terminal_fd, stop_event),
        daemon=True,
    )
    watcher.start()
    try:
        return subprocess.call(["tmux", "attach-session", "-t", session_id])
    finally:
        stop_event.set()
        watcher.join(timeout=3)
        try:
            os.write(terminal_fd, terminal_title_sequence(os.environ.get("WSL_DISTRO_NAME", "WSL")))
        except OSError:
            pass
        os.close(terminal_fd)


def run_command_in_tmux(name: str, cwd: str, command: list[str]) -> int:
    if not command:
        print("tmux-context run requires a command after --", file=sys.stderr)
        return 2
    if os.environ.get("TMUX") or not sys.stdin.isatty() or not sys.stdout.isatty():
        os.execvpe(command[0], command, os.environ.copy())

    project = re.sub(r"[^\w-]+", "-", Path(cwd).name, flags=re.UNICODE).strip("-") or "wsl"
    prefix = re.sub(r"[^\w-]+", "-", name, flags=re.UNICODE).strip("-") or "cs"
    session_name = f"{prefix}-{project}-{datetime.now().strftime('%H%M%S')}-{os.getpid()}"
    login_shell = os.environ.get("SHELL", "/bin/bash")
    launch_command = f"{shlex.join(command)}; exec {shlex.quote(login_shell)} -l"
    if run_tmux("has-session").returncode == 0:
        run_tmux("source-file", str(TMUX_CONFIG_PATH), capture=False)
    result = run_tmux(
        "new-session",
        "-d",
        "-P",
        "-F",
        "#{session_id}",
        "-s",
        session_name,
        "-c",
        cwd,
        launch_command,
    )
    session_id = result.stdout.strip()
    if result.returncode != 0 or not session_id:
        print("Failed to create tmux session.", file=sys.stderr)
        return 1
    run_tmux(
        "set-option",
        "-q",
        "-t",
        session_id,
        "@session_context_launch_name",
        prefix,
        capture=False,
    )
    return attach_with_terminal_title(session_id)


def doctor_tmux_integration() -> int:
    checks = [
        ("tmux command", bool(shutil.which("tmux"))),
        ("tmux-context entrypoint", ENTRYPOINT.is_file() and os.access(ENTRYPOINT, os.X_OK)),
        ("generated config", TMUX_CONFIG_PATH.is_file()),
        ("managed source", MANAGED_BLOCK_START in (USER_TMUX_CONFIG_PATH.read_text() if USER_TMUX_CONFIG_PATH.exists() else "")),
        ("Windows Terminal settings", windows_terminal_settings_path() is not None),
    ]
    for label, passed in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {label}")
    return 0 if all(passed for _, passed in checks[:4]) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("rename", "setup", "toggle"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("session_id")
        command_parser.add_argument("pane_id", nargs="?", default="")

    sidebar_parser = subparsers.add_parser("sidebar")
    sidebar_parser.add_argument("--cwd", required=True)
    sidebar_parser.add_argument("--session-id", required=True)
    sidebar_parser.add_argument("--source-pane", required=True)

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--cwd", default=os.getcwd())
    render_parser.add_argument("--limit", type=int, default=8)

    subparsers.add_parser("install")
    subparsers.add_parser("uninstall")
    subparsers.add_parser("doctor")
    subparsers.add_parser("enable-windows-title")
    subparsers.add_parser("copy-stdin")
    subparsers.add_parser("copy-raw-stdin")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--name", default="cs")
    run_parser.add_argument("--cwd", default=os.getcwd())
    run_parser.add_argument("launch_command", nargs=argparse.REMAINDER)

    client_parser = subparsers.add_parser("client")
    client_parser.add_argument("session_id")

    args = parser.parse_args(argv)
    if args.command == "rename":
        rename_session(args.session_id, args.pane_id)
    elif args.command == "setup":
        open_sidebar(args.session_id, args.pane_id)
    elif args.command == "toggle":
        toggle_sidebar(args.session_id, args.pane_id)
    elif args.command == "sidebar":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            render_transcript_plain(args.session_id, args.source_pane, 8)
        else:
            curses.wrapper(Sidebar(args.cwd, args.session_id, args.source_pane).run)
    elif args.command == "render":
        render_plain(args.cwd, max(1, args.limit))
    elif args.command == "install":
        return install_tmux_integration()
    elif args.command == "uninstall":
        return uninstall_tmux_integration()
    elif args.command == "doctor":
        return doctor_tmux_integration()
    elif args.command == "enable-windows-title":
        return enable_windows_terminal_titles()
    elif args.command == "copy-stdin":
        return copy_stdin_to_clipboard()
    elif args.command == "copy-raw-stdin":
        return copy_raw_stdin_to_clipboard()
    elif args.command == "run":
        launch_command = list(args.launch_command)
        if launch_command[:1] == ["--"]:
            launch_command = launch_command[1:]
        return run_command_in_tmux(args.name, args.cwd, launch_command)
    elif args.command == "client":
        return attach_with_terminal_title(args.session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
