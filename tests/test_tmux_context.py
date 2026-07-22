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
    completed_context_title,
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
    unique_session_name,
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

    def test_terminal_title_sequence_sanitizes_control_characters(self):
        sequence = terminal_title_sequence("project\ncontext\x00")

        self.assertEqual(sequence, b"\x1b]0;project context\x07\x1b]2;project context\x07")

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


if __name__ == "__main__":
    unittest.main()
