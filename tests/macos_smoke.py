"""Opt-in macOS integration check; uses only synthetic conversations."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tmux_context as app


def main():
    assert sys.platform == 'darwin'
    tmux = '/opt/homebrew/bin/tmux' if Path('/opt/homebrew/bin/tmux').exists() else 'tmux'
    with tempfile.TemporaryDirectory(prefix='tmux-context-macos-') as temp:
        root = Path(temp).resolve()
        socket = str(root / 'socket')
        def run(*args, capture=True):
            return subprocess.run([tmux, '-S', socket, *args], capture_output=True, text=True, timeout=10)
        app.run_tmux = run
        fixture = root / 'fake_codex.py'
        fixture.write_text('import sys,time\nf=open(sys.argv[1])\ntime.sleep(120)\n')
        config = root / 'tmux.conf'
        config.write_text('set -g default-shell /bin/zsh\nset -g status-right "THEME"\n' +
                          app.generated_tmux_config(Path.home() / '.local/bin/tmux-context'))
        ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222']
        sessions = []
        try:
            for index, thread in enumerate(ids):
                home = root / f'账号 {index}'
                path = home / 'sessions' / f'rollout-{thread}.jsonl'
                path.parent.mkdir(parents=True)
                records = [
                    {'type': 'session_meta', 'payload': {'id': thread}},
                    {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': f'实现 macOS 测试功能 {index}'}]}},
                    {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': '已完成测试。'}]}},
                ]
                path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n')
                command = shlex.join(['env', f'CODEX_HOME={home}', sys.executable, str(fixture), str(path)])
                result = run('-f', str(config), 'new-session', '-d', '-x', '140', '-y', '40', '-P', '-F', '#{session_id}|#{pane_id}', '-s', f'fixture-{index}', command)
                assert result.returncode == 0, result.stderr
                session, pane = result.stdout.strip().split('|')
                sessions.append((session, pane, path))
            for index, (session, pane, path) in enumerate(sessions):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    identity = app.agent_identity(pane)
                    if identity[0] == ids[index]:
                        break
                    time.sleep(.2)
                assert identity[0] == ids[index], ('identity', index, identity)
                assert Path(identity[2]) == path
                app.rename_session(session, pane)
                assert f'macOS_测试功能_{index}' in app.tmux_value(session, '#{session_name}'), app.tmux_value(session, '#{session_name}')
                app.open_sidebar(session, pane)
                assert len(app.sidebar_panes(session)) == 1
                assert app.active_codex_session(ids[index])['session_id'] == session
                app.toggle_sidebar(session, pane)
                assert not app.sidebar_panes(session)
                app.open_sidebar(session, pane)
                sidebar = app.sidebar_panes(session)[0]
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    screen = run('capture-pane', '-p', '-t', sidebar).stdout
                    if 'macOS' in screen:
                        break
                    time.sleep(.2)
                assert 'macOS' in screen, screen
                assert run('show-option', '-gv', 'status-right').stdout.strip() == 'THEME'
            # Unmanaged sessions survive detach cleanup. Managed unattached sessions close.
            first, pane, _ = sessions[0]
            app.close_detached_session(first)
            assert run('has-session', '-t', first).returncode == 0
            run('set-option', '-t', first, '@session_context_managed', '1')
            app.close_detached_session(first)
            assert run('has-session', '-t', first).returncode != 0
            second = sessions[1][0]
            run('set-option', '-t', second, '@session_context_managed', '1')
            app.close_agent_session(second)
            assert run('has-session', '-t', second).returncode != 0
            print('PASS: two concurrent Codex identities, Unicode paths, rename, sidebar content/toggle, live writer lookup, theme preservation, scoped cleanup')
        finally:
            run('kill-server')


if __name__ == '__main__':
    main()
