"""Run on Windows as well as POSIX: real pipes, no accounts or network."""
import os
import selectors
import subprocess
import sys
import time

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
