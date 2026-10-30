"""Run on Windows as well as POSIX: real pipes, no accounts or network."""
import os
import io
import selectors
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from app.services.course.english.process_runtime import (
    pipe_selector, process_options, windows_environment, worker_command,
)


def test_frozen_worker_dispatch_never_starts_another_http_server(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert worker_command() == [sys.executable, '--english-worker']
    monkeypatch.setattr(sys, 'frozen', False)
    assert worker_command()[-1] == 'app.services.course.english.worker'


def test_environment_and_creation_options(monkeypatch):
    monkeypatch.setenv('MAIN_DB_PASSWORD', 'do-not-forward')
    monkeypatch.setenv('SYSTEMROOT', 'fixture-system')
    result = windows_environment()
    assert result['SYSTEMROOT'] == 'fixture-system'
    assert 'MAIN_DB_PASSWORD' not in result
    if sys.platform == 'win32':
        assert process_options()['creationflags'] & subprocess.CREATE_NO_WINDOW
    else:
        assert process_options() == {'start_new_session': True}


def test_real_pipe_timeout_fragmentation_followup_and_eof():
    process = subprocess.Popen([sys.executable, '-u', '-c',
        "import sys; sys.stdin.readline(); sys.stdout.write('one\\n'); sys.stdout.flush(); "
        "sys.stdin.readline(); sys.stdout.write('two\\n'); sys.stdout.flush()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, **process_options())
    try:
        with pipe_selector() as poller:
            poller.register(process.stdout, selectors.EVENT_READ)
            started = time.monotonic()
            assert not poller.select(timeout=0.01)
            assert time.monotonic() - started < 0.5
            process.stdin.write(b'first\n')
            process.stdin.flush()
            assert poller.select(timeout=5)
            assert os.read(process.stdout.fileno(), 65536).replace(b'\r\n', b'\n') == b'one\n'
        # Login polling creates a fresh selector and must keep the unread reply.
        process.stdin.write(b'next\n')
        process.stdin.flush()
        with pipe_selector() as poller:
            poller.register(process.stdout, selectors.EVENT_READ)
            assert poller.select(timeout=5)
            assert os.read(process.stdout.fileno(), 65536).replace(b'\r\n', b'\n') == b'two\n'
            assert poller.select(timeout=5)
            assert os.read(process.stdout.fileno(), 65536) == b''
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def test_login_wait_keeps_browser_events_running_across_real_pipe_commands_and_eof():
    # Exercise the worker's stdin reader too: parent-only pipe tests missed the
    # Windows socket selector in this path. No browser or platform is contacted.
    code = '''
import sys, time, types
from app.services.course.english.login_handoff import read_login_command
sys.modules['playwright.sync_api'] = types.SimpleNamespace(Error=RuntimeError)
class Page:
    def __init__(self):
        self.context = types.SimpleNamespace(pages=[self])
        self.pumped = False
    def is_closed(self):
        return False
    def wait_for_timeout(self, milliseconds):
        if not self.pumped:
            print('browser-event', flush=True)
            self.pumped = True
        time.sleep(milliseconds / 1000)
page = Page()
while True:
    page.pumped = False
    command = read_login_command(page, time.monotonic() + 15)
    print('reply:' + command.decode().strip(), flush=True)
    if not command:
        break
'''
    process = subprocess.Popen([sys.executable, '-u', '-c', code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **process_options())
    buffered = b''

    def line():
        nonlocal buffered
        deadline = time.monotonic() + 10
        with pipe_selector() as poller:
            poller.register(process.stdout, selectors.EVENT_READ)
            while b'\n' not in buffered:
                assert poller.select(timeout=max(0, deadline - time.monotonic())), 'Login worker stopped responding'
                data = os.read(process.stdout.fileno(), 4096)
                assert data, 'Login worker exited while waiting for a command'
                buffered += data
        result, buffered = buffered.split(b'\n', 1)
        return result.rstrip(b'\r')

    try:
        for command in (b'refresh', b'reload'):
            assert line() == b'browser-event'
            assert process.poll() is None
            process.stdin.write(command + b'\n')
            process.stdin.flush()
            assert line() == b'reply:' + command
        assert line() == b'browser-event'
        process.stdin.close()
        assert line() == b'reply:'
        assert process.wait(timeout=5) == 0
        assert process.stderr.read() == b''
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_login_wait_uses_stream_aware_pipe_selector(monkeypatch):
    from app.services.course.english import login_handoff
    from unittest.mock import MagicMock
    stream = MagicMock()
    stream.readline.return_value = b'command\n'
    selector = MagicMock()
    selector.__enter__.return_value = selector
    selector.select.return_value = [(None, None)]
    monkeypatch.setattr(login_handoff, 'pipe_selector', lambda: selector)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=stream))
    assert login_handoff.read_login_command(None, time.monotonic() + 5) == b'command\n'
    selector.register.assert_called_once_with(stream, selectors.EVENT_READ)
    selector.select.assert_called_once_with(timeout=0)


def test_login_wait_keeps_in_memory_fixture_support(monkeypatch):
    from app.services.course.english.login_handoff import read_login_command
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'fixture\n')))
    assert read_login_command(None, time.monotonic() + 5) == b'fixture\n'


@pytest.mark.parametrize('error', [OSError('private-pipe-detail'), ValueError('private-pipe-detail')])
def test_login_channel_failure_has_a_safe_error_code(monkeypatch, capsys, error):
    import json
    from app.services.course.english import login_browser, login_handoff
    monkeypatch.setitem(sys.modules, 'playwright.sync_api',
        SimpleNamespace(Error=RuntimeError, TimeoutError=TimeoutError))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=SimpleNamespace(fileno=lambda: 0)))
    def broken_selector():
        raise error
    monkeypatch.setattr(login_handoff, 'pipe_selector', broken_selector)
    monkeypatch.setattr(login_browser, '_login_browser',
        lambda request: login_handoff.read_login_command(None, time.monotonic() + 5))
    login_browser.login_browser({})
    assert json.loads(capsys.readouterr().out) == {'ok': False, 'error_code': 'login_channel'}
