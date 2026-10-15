import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macos_process as mac
import tmux_context as app


class MacOSProcessTest(unittest.TestCase):
    def test_daemon_identity_requires_exact_birth_time_and_boot(self):
        def inspect(pid, flavor, arg, buffer, size):
            self.assertEqual((pid, flavor, arg), (123, 3, 0))
            info = ctypes.cast(buffer, ctypes.POINTER(mac._ProcBSDInfo)).contents
            info.pbi_pid = 123
            info.pbi_start_tvsec = 100
            info.pbi_start_tvusec = 456
            return size

        library = mock.Mock()
        library.proc_pidinfo.side_effect = inspect
        identity = {'bootId': 'BOOT-ID', 'startSeconds': 100, 'startMicroseconds': 456}
        with mock.patch.object(mac.ctypes, 'CDLL', return_value=library), \
                mock.patch.object(mac, 'query', return_value=b'boot-id\n'):
            self.assertTrue(mac.matches_identity(123, identity))
            for field, value in (('bootId', 'other-boot'), ('startSeconds', 101), ('startMicroseconds', 457)):
                self.assertFalse(mac.matches_identity(123, {**identity, field: value}))

    def test_daemon_identity_fails_closed_on_invalid_or_denied_queries(self):
        identity = {'bootId': 'boot-id', 'startSeconds': 100, 'startMicroseconds': 456}
        for pid, record in ((0, identity), (123, {}), (123, None),
                            (123, {**identity, 'startMicroseconds': 1_000_000})):
            self.assertFalse(mac.matches_identity(pid, record))
        library = mock.Mock()
        library.proc_pidinfo.return_value = ctypes.sizeof(mac._ProcBSDInfo) - 1
        with mock.patch.object(mac.ctypes, 'CDLL', return_value=library):
            self.assertFalse(mac.matches_identity(123, identity))
        with mock.patch.object(mac.ctypes, 'CDLL', side_effect=OSError('denied')):
            self.assertFalse(mac.matches_identity(123, identity))

    @unittest.skipUnless(Path('/bin/zsh').exists(), 'zsh not available')
    def test_launcher_preserves_exit_code_in_zsh(self):
        command = app.tmux_launch_command(['/bin/sh', '-c', 'exit 7'])
        result = subprocess.run(['/bin/zsh', '-f', '-c', 'tmux() { return 0; }; ' + command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertNotIn('read-only', result.stderr)

    def test_procargs_preserves_boundaries_and_filters_environment(self):
        data = (int(3).to_bytes(4, sys.byteorder, signed=True) +
                b'/usr/bin/codex\0\0codex\0\0a b\0' +
                'CODEX_HOME=/tmp/中文 a\0CODEX_THREAD_ID=thread\0SECRET=hidden\0'.encode())
        args, env = mac.parse_procargs(data)
        self.assertEqual(args, ['codex', '', 'a b'])
        self.assertEqual(env, {'CODEX_HOME': '/tmp/中文 a', 'CODEX_THREAD_ID': 'thread'})

    def test_truncated_arguments_fail_closed(self):
        for data in (b'', b'ab', b'\xff' * 4, (2).to_bytes(4, sys.byteorder) + b'codex\0\0one\0'):
            self.assertEqual(mac.parse_procargs(data), ([], {}))

    def test_tree_excludes_unrelated_processes_and_handles_cycles(self):
        with mock.patch.object(mac, 'query', return_value=b'10 1\n11 10\n12 11\n10 12\n20 1\nbad\n'):
            self.assertEqual(mac.descendants(10), [10, 11, 12])

    def test_queries_degrade_on_denial_timeout_and_exit(self):
        for error in (PermissionError(), subprocess.TimeoutExpired('ps', 3)):
            with mock.patch.object(mac.subprocess, 'run', side_effect=error):
                self.assertEqual(mac.query('ps'), b'')
        with mock.patch.object(mac.subprocess, 'run', return_value=mock.Mock(returncode=1, stdout=b'partial')):
            self.assertEqual(mac.query('ps'), b'')

    def test_lsof_nul_filenames(self):
        with mock.patch.object(mac, 'query', return_value='p1\0\nfcwd\0n/tmp/中文 a\0\nf3\0n/tmp/a\nb\0'.encode()):
            self.assertEqual(mac.open_paths(1), ['/tmp/中文 a', '/tmp/a\nb'])

    def test_macos_transcripts_exclude_guardian_and_spawned_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp) / 'sessions'
            sessions.mkdir()
            paths = []
            for index, source in enumerate((
                'cli',
                {'subagent': {'other': 'guardian'}},
                {'subagent': {'thread_spawn': {'parent_thread_id': 'main', 'depth': 1}}},
            )):
                path = sessions / f'rollout-{index}.jsonl'
                path.write_text(json.dumps({'type': 'session_meta', 'payload': {'source': source}}) + '\n')
                os.utime(path, (index + 1, index + 1))
                paths.append(path)
            with mock.patch.object(app.sys, 'platform', 'darwin'), \
                    mock.patch.object(app, 'macos_process', mac, create=True), \
                    mock.patch.object(mac, 'open_paths') as open_paths:
                open_paths.return_value = list(map(str, paths))
                self.assertEqual(app.open_codex_session_path(1), paths[0])
                open_paths.return_value = list(map(str, paths[1:]))
                self.assertIsNone(app.open_codex_session_path(1))
                open_paths.return_value = []
                self.assertIsNone(app.open_codex_session_path(1))

    def test_macos_prefers_recent_main_transcript_after_fork(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp) / 'sessions'
            sessions.mkdir()
            paths = [sessions / f'rollout-{index}.jsonl' for index in range(2)]
            for index, path in enumerate(paths):
                path.write_text(json.dumps({'type': 'session_meta', 'payload': {'source': 'cli'}}) + '\n')
                os.utime(path, (index + 1, index + 1))
            with mock.patch.object(app.sys, 'platform', 'darwin'), \
                    mock.patch.object(app, 'macos_process', mac, create=True), \
                    mock.patch.object(mac, 'open_paths', return_value=list(map(str, paths))):
                self.assertEqual(app.open_codex_session_path(1), paths[1])

    def test_pbcopy_uses_utf8(self):
        with mock.patch.object(app.sys, 'platform', 'darwin'), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(app.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(app.copy_text_to_clipboard('中文 🎉'), 'macOS clipboard')
            self.assertEqual(run.call_args.args[0], ['/usr/bin/pbcopy'])
            self.assertEqual(run.call_args.kwargs['input'], '中文 🎉'.encode())

    def test_installed_launcher_is_pinned_and_refuses_unowned_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(app, 'TMUX_RUNTIME_DIR', root / 'runtime'), \
                    mock.patch.object(app, 'USER_TMUX_CONFIG_PATH', root / '.tmux.conf'):
                entry = app.install_runtime()
                self.assertIn(sys.executable, entry.read_text())
                self.assertTrue((root / '.local/bin/tmux-context').is_symlink())
                self.assertEqual(app.install_runtime(), entry)
                public = root / '.local/bin/tmux-context'
                public.unlink()
                public.write_text('user command')
                with self.assertRaises(RuntimeError):
                    app.install_runtime()
                self.assertEqual(public.read_text(), 'user command')

    def test_theme_not_overridden(self):
        config = app.generated_tmux_config()
        self.assertNotIn('set -g status-left ', config)
        self.assertNotIn('set -g status-right ', config)

    def test_linux_proc_readers_remain_available(self):
        with mock.patch.object(app.sys, 'platform', 'linux'), \
                mock.patch.object(Path, 'read_bytes', return_value=b'CODEX_HOME=/tmp/custom\0'):
            self.assertEqual(app.process_environment(12), {'CODEX_HOME': '/tmp/custom'})

    def test_explicit_thread_resolves_ambiguous_open_files(self):
        with mock.patch.object(app, 'process_environment', return_value={'CODEX_THREAD_ID': 'thread', 'CODEX_HOME': '/tmp/custom'}), \
                mock.patch.object(app, 'open_codex_session_path', return_value=None), \
                mock.patch.object(app, 'find_session_path', return_value=Path('/tmp/custom/sessions/rollout-thread.jsonl')):
            identity, _ = app.codex_process_identity(12, '')
            self.assertEqual(identity, ('thread', '/tmp/custom', '/tmp/custom/sessions/rollout-thread.jsonl'))

    def test_pbcopy_failure_falls_back_to_tmux(self):
        with mock.patch.object(app.sys, 'platform', 'darwin'), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(app.shutil, 'which', return_value=None), \
                mock.patch.object(app.subprocess, 'run', side_effect=[subprocess.TimeoutExpired('pbcopy', 3), mock.Mock(returncode=0)]) as run:
            self.assertEqual(app.copy_text_to_clipboard('中文'), 'tmux buffer')
            self.assertEqual(run.call_args.args[0], ['tmux', 'load-buffer', '-'])

    def test_macos_title_targets_tty_without_proc(self):
        with mock.patch.object(app.sys, 'platform', 'darwin'), \
                mock.patch.object(app, 'run_tmux', return_value=mock.Mock(returncode=0, stdout='$1|22|/dev/ttys123\n')), \
                mock.patch.object(app.os, 'open', return_value=9) as opening, \
                mock.patch.object(app.os, 'write') as writing, mock.patch.object(app.os, 'close'):
            app.update_terminal_title('$1', '测试\x07标题')
            self.assertEqual(opening.call_args.args[0], '/dev/ttys123')
            self.assertEqual(writing.call_args.args[1], app.terminal_title_sequence('测试 标题'))

    def test_macos_attach_saves_and_restores_title(self):
        with mock.patch.object(app.sys, 'platform', 'darwin'), \
                mock.patch.object(app.os, 'ttyname', return_value='/dev/ttys123'), \
                mock.patch.object(app.os, 'open', return_value=9), \
                mock.patch.object(app.os, 'write') as writing, mock.patch.object(app.os, 'close'), \
                mock.patch.object(app.threading, 'Thread'), \
                mock.patch.object(app.subprocess, 'call', return_value=0), \
                mock.patch.object(app.sys, 'stdin'):
            self.assertEqual(app.attach_with_terminal_title('$1'), 0)
            self.assertEqual([call.args[1] for call in writing.call_args_list], [b'\x1b[22;0t', b'\x1b[23;0t'])


class MacOSDaemonTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='tmux-context-macos-daemon-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / 'home'
        (self.home / 'sessions').mkdir(parents=True)
        (self.home / 'app-server-daemon').mkdir()
        self.record = self.home / 'app-server-daemon/daemon.pid'
        self.record.write_text(json.dumps({'pid': 123, 'processIdentity': {}}))
        self.paths = []
        patchers = {
            'platform': mock.patch.object(app.sys, 'platform', 'darwin'),
            'process_module': mock.patch.object(app, 'macos_process', mac, create=True),
            'identity': mock.patch.object(mac, 'matches_identity', return_value=True),
            'args': mock.patch.object(mac, 'procargs', return_value=(['/bin/codex', 'app-server'], {})),
            'open_paths': mock.patch.object(mac, 'open_paths', side_effect=lambda pid: self.paths),
        }
        for name, patcher in patchers.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_daemon_files_exclude_subagents_and_other_homes(self):
        main = self.home / 'sessions/rollout-main.jsonl'
        main.write_text(json.dumps({'type': 'session_meta', 'payload': {'source': 'cli'}}) + '\n')
        child = self.home / 'sessions/rollout-child.jsonl'
        child.write_text(json.dumps({'type': 'session_meta', 'payload': {'source': {'subagent': {'other': 'guardian'}}}}) + '\n')
        outside = self.root / 'rollout-outside.jsonl'
        outside.write_text(main.read_text())
        alias = self.home / 'sessions/rollout-alias.jsonl'
        alias.symlink_to(outside)
        self.paths = list(map(str, (child, outside, alias, main)))
        self.assertEqual(app.daemon_rollout_paths(str(self.home)), [main.resolve()])

    def test_stale_daemon_identity_is_rejected(self):
        self.identity.return_value = False
        self.assertEqual(app.daemon_rollout_paths(str(self.home)), [])
        self.args.assert_not_called()

    def test_daemon_must_be_a_codex_app_server(self):
        for args in ([], ['/bin/other', 'app-server'], ['/bin/codex']):
            with self.subTest(args=args):
                self.args.return_value = (args, {})
                self.assertEqual(app.daemon_rollout_paths(str(self.home)), [])

    def test_malformed_daemon_record_is_nonfatal(self):
        for record in ('not json', '{}', '{"pid": "invalid", "processIdentity": {}}'):
            self.record.write_text(record)
            self.assertEqual(app.daemon_rollout_paths(str(self.home)), [])

    def test_default_codex_home_is_available_to_daemon_fallback(self):
        with mock.patch.object(app, 'process_environment', return_value={}), \
                mock.patch.object(app, 'open_codex_session_path', return_value=None), \
                mock.patch.object(Path, 'home', return_value=self.root):
            identity, detected_home = app.codex_process_identity(123, '')
        self.assertIsNone(identity)
        self.assertEqual(detected_home, str(self.root / '.codex'))

    def test_open_transcript_infers_custom_home_before_default(self):
        path = self.home / 'sessions/rollout-main.jsonl'
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'main', 'source': 'cli'}}) + '\n')
        with mock.patch.object(app, 'process_environment', return_value={}), \
                mock.patch.object(app, 'open_codex_session_path', return_value=path), \
                mock.patch.object(Path, 'home', return_value=self.root):
            identity, _ = app.codex_process_identity(123, '')
        self.assertEqual(identity, ('main', str(self.home), str(path)))


if __name__ == '__main__':
    unittest.main()
