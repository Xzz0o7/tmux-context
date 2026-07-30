import os
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

import tmux_context
from tmux_context import (
    claude_project_slug,
    claude_session_path,
    claude_transcript_messages,
    clean_sidebar_selection_text,
    completed_context_title,
    is_claude_process,
    is_claude_transcript,
    load_current_session_messages,
    copy_raw_stdin_to_clipboard,
    copy_stdin_to_clipboard,
    copy_text_to_clipboard,
    configure_windows_terminal_title_settings,
    generated_tmux_config,
    group_session_turns,
    indexed_ai_title,
    indexed_title,
    managed_source_block,
    meaningful_title,
    run_command_in_tmux,
    session_base_name,
    source_name_from_raw_path,
    strip_managed_block,
    terminal_title_sequence,
    title_for_client,
    transcript_header_field_ranges,
    transcript_time,
    unique_session_name,
    wrap_transcript_text,
)


class TmuxContextTest(unittest.TestCase):
    def test_strip_managed_block_preserves_unmanaged_content(self):
        text = "before\n# start\nmanaged\n# end\nafter\n"

        result = strip_managed_block(text, "# start", "# end")

        self.assertEqual(result, "before\nafter\n")

    def test_managed_source_block_uses_generated_config_path(self):
        block = managed_source_block(Path("/home/test/.tmux-context/tmux/tmux.conf"))

        self.assertIn('source-file "/home/test/.tmux-context/tmux/tmux.conf"', block)
        self.assertIn("tmux-context start", block)

    def test_generated_config_routes_all_hooks_through_tmux_context(self):
        config = generated_tmux_config(Path("/opt/tmux-context/tmux-context"))

        self.assertIn('/opt/tmux-context/tmux-context setup', config)
        self.assertIn('/opt/tmux-context/tmux-context rename', config)
        self.assertIn('"/opt/tmux-context/tmux-context" rename', config)
        self.assertNotIn("sync-title", config)
        self.assertIn('"/opt/tmux-context/tmux-context" toggle', config)
        self.assertIn("set-titles-string '#S'", config)
        self.assertIn(
            "bind-key -T root MouseDrag1Pane if-shell -F '#{@session_context_sidebar}' 'copy-mode -M'",
            config,
        )
        self.assertIn('copy-mode MouseDragEnd1Pane if-shell -F \'#{@session_context_sidebar}\'', config)
        self.assertIn('/opt/tmux-context/tmux-context copy-stdin', config)
        self.assertIn('/opt/tmux-context/tmux-context copy-raw-stdin', config)

    def test_terminal_title_sequence_sanitizes_control_characters(self):
        sequence = terminal_title_sequence("project\ncontext\x00")

        self.assertEqual(sequence, b"\x1b]0;project context\x07\x1b]2;project context\x07")

    def test_transcript_time_includes_date_and_time(self):
        timestamp = datetime.now().astimezone()

        self.assertEqual(transcript_time(timestamp.isoformat()), timestamp.strftime("%Y-%m-%d %H:%M"))

    def test_transcript_header_field_ranges_identifies_number_date_and_time(self):
        self.assertEqual(
            transcript_header_field_ranges("▶ 03  2026-07-22 13:49"),
            [(2, 4, "number"), (6, 16, "date"), (17, 22, "time")],
        )

    def test_sidebar_header_fields_are_drawn_in_cursor_order(self):
        sidebar = object.__new__(tmux_context.Sidebar)
        sidebar.turn_number_attr = 11
        sidebar.turn_date_attr = 12
        sidebar.turn_time_attr = 13
        window = mock.Mock()
        window.getmaxyx.return_value = (20, 80)
        header = "▶ 03  2026-07-22 13:49"

        sidebar.draw_transcript_line(window, 3, header, 10)

        window.move.assert_called_once_with(3, 0)
        self.assertEqual(
            window.addstr.call_args_list,
            [
                mock.call(3, 0, "▶ 03 2026-07-22 13:49", 10),
                mock.call("▶ ", 10),
                mock.call("03", 11),
                mock.call("  ", 10),
                mock.call("2026-07-22", 12),
                mock.call(" ", 10),
                mock.call("13:49", 13),
            ],
        )

    def test_wsl_clipboard_uses_utf8_powershell_input(self):
        result = mock.Mock(returncode=0)
        with (
            mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}, clear=False),
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch("tmux_context.shutil.which", return_value=None),
            mock.patch("tmux_context.subprocess.run", return_value=result) as run_command,
        ):
            destination = copy_text_to_clipboard("中文内容")

        command = run_command.call_args.args[0]
        options = run_command.call_args.kwargs
        self.assertEqual(destination, "Windows clipboard")
        self.assertEqual(command[0], "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        self.assertIn("[Console]::InputEncoding", command[-1])
        self.assertEqual(options["input"], "中文内容".encode("utf-8"))

    def test_copy_stdin_uses_existing_clipboard_path(self):
        with (
            mock.patch("sys.stdin", new_callable=__import__("io").StringIO) as stdin,
            mock.patch("tmux_context.copy_text_to_clipboard", return_value="Windows clipboard") as copy_text,
        ):
            stdin.write("中文内容")
            stdin.seek(0)
            result = copy_stdin_to_clipboard()

        self.assertEqual(result, 0)
        copy_text.assert_called_once_with("中文内容")

    def test_copy_raw_stdin_preserves_normal_terminal_text(self):
        with (
            mock.patch("sys.stdin", new_callable=__import__("io").StringIO) as stdin,
            mock.patch("tmux_context.copy_text_to_clipboard", return_value="Windows clipboard") as copy_text,
        ):
            stdin.write("│ 保留正常终端文本\n第二行")
            stdin.seek(0)
            result = copy_raw_stdin_to_clipboard()

        self.assertEqual(result, 0)
        copy_text.assert_called_once_with("│ 保留正常终端文本\n第二行")

    def test_clean_sidebar_selection_removes_decoration_and_wraps(self):
        copied = clean_sidebar_selection_text(
            "▼ 01  11:30\n"
            "│ 你\n"
            "│   第一行内容\n"
            "│   自动换行继续\n"
            "────────────────\n"
            "│ AI 11:31\n"
            "│   回复第一段\n"
            "│   自动换行继续\n"
        )

        self.assertEqual(copied, "第一行内容自动换行继续\n\n回复第一段自动换行继续")

    def test_transcript_wrap_preserves_word_boundary_for_copy(self):
        self.assertEqual(wrap_transcript_text("hello world", 6), ["hello ", "world"])
        self.assertEqual(
            clean_sidebar_selection_text("│   hello \n│   world\n"),
            "hello world",
        )

    def test_current_session_loader_keeps_messages_before_history_window(self):
        def message(text: str) -> dict:
            return {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rollout-session.jsonl"
            entries = [
                message("最早记录"),
                {"type": "event_msg", "payload": {"text": "x" * (tmux_context.MAX_HISTORY_BYTES + 1)}},
                message("最新记录"),
            ]
            with path.open("w", encoding="utf-8") as stream:
                for entry in entries:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

            messages = load_current_session_messages(str(path))

        self.assertEqual([item["text"] for item in messages], ["最早记录", "最新记录"])

    def test_sidebar_mouse_toggles_only_after_left_release(self):
        sidebar = object.__new__(tmux_context.Sidebar)
        sidebar.scroll_top = 0
        sidebar.footer_actions = []
        sidebar.turn_ranges = [(0, 0, 2)]
        sidebar.selected_turn = 0
        sidebar.scroll = mock.Mock()
        sidebar.toggle_selected = mock.Mock()
        window = mock.Mock()
        window.getmaxyx.return_value = (20, 80)
        pressed = getattr(tmux_context.curses, "BUTTON1_PRESSED", 0)
        released = getattr(tmux_context.curses, "BUTTON1_RELEASED", 0)

        with mock.patch("tmux_context.curses.getmouse", return_value=(0, 5, 3, 0, pressed)):
            sidebar.handle_mouse(window)
        sidebar.toggle_selected.assert_not_called()

        with mock.patch("tmux_context.curses.getmouse", return_value=(0, 5, 3, 0, released)):
            sidebar.handle_mouse(window)

        self.assertEqual(sidebar.selected_turn, 2)
        sidebar.toggle_selected.assert_called_once_with()

    def test_sidebar_search_waits_for_user_input(self):
        sidebar = object.__new__(tmux_context.Sidebar)
        sidebar.query = ""
        sidebar.follow_latest = True
        sidebar.scroll_top = 4
        sidebar.selected_turn = 2
        sidebar.expanded_turn_key = "turn-key"
        sidebar.ensure_selection = False
        sidebar.apply_filters = mock.Mock()
        window = mock.Mock()
        window.getmaxyx.return_value = (20, 80)
        window.getstr.return_value = b"search text"

        with mock.patch("tmux_context.curses.echo"), mock.patch(
            "tmux_context.curses.noecho"
        ), mock.patch("tmux_context.curses.curs_set"):
            sidebar.prompt_search(window)

        self.assertEqual(sidebar.query, "search text")
        self.assertFalse(sidebar.follow_latest)
        self.assertEqual(sidebar.scroll_top, 0)
        self.assertEqual(sidebar.selected_turn, 0)
        self.assertEqual(sidebar.expanded_turn_key, "")
        self.assertTrue(sidebar.ensure_selection)
        sidebar.apply_filters.assert_called_once_with()
        window.timeout.assert_has_calls([mock.call(-1), mock.call(50)])

    def test_sidebar_top_action_selects_first_turn(self):
        sidebar = object.__new__(tmux_context.Sidebar)
        sidebar.follow_latest = True
        sidebar.selected_turn = 3
        sidebar.ensure_selection = False

        closed = sidebar.run_action("top", mock.Mock())

        self.assertFalse(closed)
        self.assertFalse(sidebar.follow_latest)
        self.assertEqual(sidebar.selected_turn, 0)
        self.assertTrue(sidebar.ensure_selection)

    def test_title_for_client_matches_real_tty(self):
        result = mock.Mock(returncode=0, stdout="/dev/pts/1|one\n/dev/pts/2|target\n")
        with mock.patch("tmux_context.run_tmux", return_value=result):
            title = title_for_client("/dev/pts/2")

        self.assertEqual(title, "target")

    def test_configure_windows_terminal_title_settings_is_idempotent(self):
        data = {
            "profiles": {
                "list": [
                    {"guid": "wsl-guid", "source": "Microsoft.WSL"},
                    {"guid": "pwsh-guid", "commandline": "pwsh.exe"},
                ],
            },
        }

        changed_profiles, shortcut_added, shortcut_conflict = configure_windows_terminal_title_settings(data)

        self.assertEqual(changed_profiles, ["wsl-guid"])
        self.assertTrue(shortcut_added)
        self.assertFalse(shortcut_conflict)
        self.assertFalse(data["profiles"]["list"][0]["suppressApplicationTitle"])
        self.assertIn(
            {"id": "Terminal.OpenTabRenamer", "keys": "ctrl+shift+f12"},
            data["keybindings"],
        )

        changed_profiles, shortcut_added, shortcut_conflict = configure_windows_terminal_title_settings(data)

        self.assertEqual(changed_profiles, [])
        self.assertFalse(shortcut_added)
        self.assertFalse(shortcut_conflict)

    def test_group_session_turns_attaches_replies_to_previous_user(self):
        turns = group_session_turns([
            {"role": "user", "text": "question", "timestamp": "1"},
            {"role": "assistant", "text": "thinking", "phase": "commentary", "timestamp": "2"},
            {"role": "assistant", "text": "answer", "phase": "final", "timestamp": "3"},
            {"role": "user", "text": "next", "timestamp": "4"},
        ])

        self.assertEqual(len(turns), 2)
        self.assertEqual([reply["text"] for reply in turns[0]["replies"]], ["thinking", "answer"])
        self.assertEqual(turns[1]["user"]["text"], "next")

    def test_meaningful_title_rejects_generic_messages(self):
        self.assertEqual(meaningful_title("codexa"), "")
        self.assertEqual(meaningful_title("[Image #1] 还需要添加这个"), "")
        self.assertEqual(meaningful_title("<image name=[Image #1]></image> 还需要添加这个"), "")
        self.assertEqual(meaningful_title("再检查一下"), "")
        self.assertEqual(
            meaningful_title("tmux-context rename \"$(tmux display-message -p '#{session_id}')\""),
            "",
        )
        self.assertEqual(
            meaningful_title("重命名选项卡时过滤无意义标题"),
            "重命名选项卡时过滤无意义标题",
        )
        path_title = meaningful_title(
            r"\\192.168.1.169\project\config\apns-conf.xml修改这个文件，合入对应的科特迪瓦参数"
        )
        self.assertIn("apns-conf xml", path_title)
        self.assertIn("科特迪瓦", path_title)

    def test_meaningful_title_removes_redundant_file_wording(self):
        self.assertEqual(
            meaningful_title("检查 ecc_list.xml 中有没有添加的重复项"),
            "检查 ecc_list xml 重复项",
        )
        self.assertEqual(
            meaningful_title("apns-conf.xml 修改这个文件，合入对应的科特迪瓦参数"),
            "apns-conf xml 合入科特迪瓦参数",
        )

    def test_session_base_name_places_context_before_metadata(self):
        name = session_base_name("检查 ecc_list 重复项", "codexa", "administrator", "")

        self.assertEqual(name, "检查_ecc_list_重复项--codexa-administrator")

    def test_completed_context_title_waits_for_final_and_skips_weak_turns(self):
        messages = [
            ("user", "", "实现自动修改 tmux 选项卡标题"),
            ("assistant", "commentary", "处理中"),
            ("assistant", "final_answer", "已完成"),
            ("user", "", "继续"),
            ("assistant", "final_answer", "继续完成"),
            ("user", "", "修改尚未完成的当前标题"),
            ("assistant", "commentary", "仍在处理中"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "session.jsonl"
            with raw_path.open("w") as stream:
                for role, phase, text in messages:
                    stream.write(json.dumps({
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": role,
                            "phase": phase,
                            "content": [{"type": "text", "text": text}],
                        },
                    }, ensure_ascii=False) + "\n")

            title = completed_context_title(str(raw_path))

        self.assertEqual(title, "实现自动修改 tmux 选项卡标题")

    def test_indexed_title_prefers_ai_goal_and_filters_generic_title(self):
        records = [{
            "session_id": "thread-id",
            "title": "codexa",
            "archive_method": "ai",
            "archive": {"goal": "修复 Windows Terminal 标签自动命名"},
        }]

        self.assertEqual(indexed_ai_title("thread-id", records), "修复 Windows Terminal 标签自动命名")
        self.assertEqual(indexed_title("thread-id", records), "")

    def test_duplicate_session_name_uses_creation_time_not_tmux_id(self):
        created = 1784552400
        result = mock.Mock(
            returncode=0,
            stdout=f"$1\tproject--title\t{created - 60}\n$2\tinitial\t{created}\n",
        )
        with mock.patch("tmux_context.run_tmux", return_value=result):
            name = unique_session_name("$2", "project--title")

        self.assertEqual(name, f"project--title--{datetime.fromtimestamp(created).strftime('%m%d-%H%M')}")
        self.assertNotIn("$2", name)

    def test_source_name_from_raw_path_uses_codex_account_home(self):
        self.assertEqual(
            source_name_from_raw_path("/home/test/.codex_a/sessions/2026/07/session.jsonl"),
            "codexa",
        )

    def test_install_preserves_unmanaged_config_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / ".tmux-context" / "tmux"
            generated_config = runtime_dir / "tmux.conf"
            user_config = root / ".tmux.conf"
            user_config.write_text("set -g mouse on\n")
            completed = mock.Mock(returncode=1, stdout="")
            patches = (
                mock.patch.object(tmux_context, "TMUX_RUNTIME_DIR", runtime_dir),
                mock.patch.object(tmux_context, "TMUX_CONFIG_PATH", generated_config),
                mock.patch.object(tmux_context, "USER_TMUX_CONFIG_PATH", user_config),
                mock.patch.object(tmux_context, "run_tmux", return_value=completed),
            )
            with patches[0], patches[1], patches[2], patches[3]:
                tmux_context.install_tmux_integration()
                tmux_context.install_tmux_integration()

            installed = user_config.read_text()
            self.assertIn("set -g mouse on", installed)
            self.assertEqual(installed.count(tmux_context.MANAGED_BLOCK_START), 1)
            self.assertIn(str(generated_config), installed)
            self.assertTrue(generated_config.is_file())

    def test_install_migrates_cs_session_manager_managed_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / ".tmux-context" / "tmux"
            generated_config = runtime_dir / "tmux.conf"
            user_config = root / ".tmux.conf"
            user_config.write_text(
                f"{tmux_context.CS_MANAGER_BLOCK_START}\nold\n{tmux_context.CS_MANAGER_BLOCK_END}\n"
            )
            completed = mock.Mock(returncode=1, stdout="")
            patches = (
                mock.patch.object(tmux_context, "TMUX_RUNTIME_DIR", runtime_dir),
                mock.patch.object(tmux_context, "TMUX_CONFIG_PATH", generated_config),
                mock.patch.object(tmux_context, "USER_TMUX_CONFIG_PATH", user_config),
                mock.patch.object(tmux_context, "run_tmux", return_value=completed),
            )
            with patches[0], patches[1], patches[2], patches[3]:
                tmux_context.install_tmux_integration()

            installed = user_config.read_text()
            self.assertNotIn(tmux_context.CS_MANAGER_BLOCK_START, installed)
            self.assertIn(tmux_context.MANAGED_BLOCK_START, installed)

    def test_run_command_creates_detached_session_then_attaches(self):
        responses = [
            mock.Mock(returncode=1, stdout=""),
            mock.Mock(returncode=0, stdout="$9\n"),
            mock.Mock(returncode=0, stdout=""),
        ]
        with mock.patch.dict(os.environ, {"TMUX": ""}), \
                mock.patch.object(tmux_context.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(tmux_context.sys.stdout, "isatty", return_value=True), \
                mock.patch("tmux_context.run_tmux", side_effect=responses) as run_tmux, \
                mock.patch("tmux_context.attach_with_terminal_title", return_value=0) as attach:
            result = run_command_in_tmux("codexa", "/tmp", ["codexa", "resume", "session-id"])

        self.assertEqual(result, 0)
        self.assertEqual(run_tmux.call_args_list[1].args[0], "new-session")
        self.assertIn("codexa resume session-id", run_tmux.call_args_list[1].args[-1])
        self.assertEqual(run_tmux.call_args_list[2].args[-1], "codexa")
        attach.assert_called_once_with("$9")


class ClaudeTranscriptTest(unittest.TestCase):
    def write_transcript(self, directory: Path, name: str, entries: list[dict]) -> Path:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as stream:
            for entry in entries:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path

    def user_entry(self, text: str, **extra: object) -> dict:
        return {"type": "user", "message": {"role": "user", "content": text}, **extra}

    def assistant_entry(self, blocks: list[dict], **extra: object) -> dict:
        return {"type": "assistant", "message": {"role": "assistant", "content": blocks}, **extra}

    def test_claude_project_slug_matches_transcript_directory(self):
        self.assertEqual(claude_project_slug("/home/user/work.repo"), "-home-user-work-repo")

    def test_is_claude_process_ignores_unrelated_commands(self):
        self.assertTrue(is_claude_process("claude "))
        self.assertTrue(is_claude_process("/home/user/.local/bin/claude --resume"))
        self.assertFalse(is_claude_process("/bin/bash -c source /home/user/.claude/shell-snapshots/snapshot-bash.sh"))
        self.assertFalse(is_claude_process("codex resume session-id"))

    def test_is_claude_transcript_detects_claude_project_layout(self):
        self.assertTrue(is_claude_transcript("/home/user/.claude/projects/-home-user/session.jsonl"))
        self.assertFalse(is_claude_transcript("/home/user/.codex/sessions/2026/rollout-session.jsonl"))

    def test_source_name_from_raw_path_reports_claude_home(self):
        self.assertEqual(
            source_name_from_raw_path("/home/user/.claude/projects/-home-user/session.jsonl"),
            "claude",
        )
        self.assertEqual(
            source_name_from_raw_path("/home/user/.claude_work/projects/-home-user/session.jsonl"),
            "claude_work",
        )

    def test_claude_transcript_keeps_only_the_last_answer_of_a_turn(self):
        messages = claude_transcript_messages([
            json.dumps(self.user_entry("适配 Claude Code 侧边栏")),
            json.dumps(self.assistant_entry([{"type": "thinking", "thinking": "先看进程"}])),
            json.dumps(self.assistant_entry([{"type": "text", "text": "我先查一下进程"}])),
            json.dumps(self.assistant_entry([{"type": "tool_use", "name": "Bash", "input": {}}])),
            json.dumps(self.assistant_entry([{"type": "text", "text": "已完成适配"}])),
        ])

        self.assertEqual(
            [(message["role"], message["phase"], message["text"]) for message in messages],
            [
                ("user", "", "适配 Claude Code 侧边栏"),
                ("assistant", "commentary", "先看进程"),
                ("assistant", "commentary", "我先查一下进程"),
                ("assistant", "final", "已完成适配"),
            ],
        )

    def test_claude_transcript_skips_tool_traffic_and_injected_text(self):
        messages = claude_transcript_messages([
            json.dumps(self.user_entry("正文 <system-reminder>忽略我</system-reminder>")),
            json.dumps(self.user_entry("[Request interrupted by user for tool use]")),
            json.dumps(self.user_entry("子代理提问", isSidechain=True)),
            json.dumps(self.user_entry("环境说明", isMeta=True)),
            json.dumps({
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "命令输出"}]},
            }),
            json.dumps({"type": "attachment", "attachment": {"type": "file"}}),
        ])

        self.assertEqual([message["text"] for message in messages], ["正文"])

    def test_completed_context_title_reads_claude_transcript(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_transcript(
                Path(tmpdir) / ".claude" / "projects" / "-home-user",
                "session.jsonl",
                [
                    self.user_entry("适配 Claude Code 的当前会话侧边栏"),
                    self.user_entry("继续"),
                    self.assistant_entry([{"type": "text", "text": "已完成"}]),
                    self.user_entry("尚未回答的提问"),
                ],
            )

            self.assertEqual(
                completed_context_title(str(path)),
                "适配 Claude Code 的当前会话侧边栏",
            )
            self.assertEqual(len(load_current_session_messages(str(path))), 4)

    def test_claude_session_path_prefers_transcript_started_with_the_process(self):
        started = 1785000000.0
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cwd = str(root / "repo")
            projects = root / "projects" / claude_project_slug(cwd)
            for name, offset in (("old.jsonl", -7200), ("current.jsonl", 12)):
                stamp = datetime.fromtimestamp(started + offset).astimezone().isoformat()
                self.write_transcript(projects, name, [{
                    "type": "user",
                    "sessionId": name.removesuffix(".jsonl"),
                    "cwd": cwd,
                    "timestamp": stamp,
                    "message": {"role": "user", "content": "提问"},
                }])
            os.utime(projects / "old.jsonl", (started + 900, started + 900))

            with mock.patch("tmux_context.process_cwd", return_value=cwd), \
                    mock.patch("tmux_context.process_start_time", return_value=started):
                path = claude_session_path(4242, {"CLAUDE_CONFIG_DIR": str(root)})

        self.assertEqual(Path(path).name, "current.jsonl")


if __name__ == "__main__":
    unittest.main()
