"""Offline cross-platform packaging gate. Uses only a fresh temporary data dir."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    binary = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'dist' / ('uh-backend.exe' if os.name == 'nt' else 'uh-backend')
    with tempfile.TemporaryDirectory(prefix='uh-desktop-smoke-') as temporary:
        environment = dict(os.environ, UH_DESKTOP_DATA_DIR=temporary)
        process = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=environment)
        output = queue.Queue()
        def read():
            for line in process.stdout:
                if line.startswith('UH_BACKEND_LISTENING '):
                    output.put(line.strip().split()[-1])
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        try:
            port = int(output.get(timeout=120))
            base = f'http://127.0.0.1:{port}'
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urlopen(base + '/health', timeout=2) as reply:
                        assert reply.status == 200
                    break
                except (URLError, TimeoutError):
                    if time.monotonic() >= deadline or process.poll() is not None:
                        raise RuntimeError('Frozen desktop did not become healthy') from None
                    time.sleep(0.2)
            def get(path):
                with urlopen(base + path, timeout=5) as reply:
                    return json.load(reply)
            assert get('/api/v1/runtime')['profile'] == 'local'
            assert get('/api/v1/learning-accounts') == {'accounts': []}
            assert not get('/api/v1/desktop/settings')['primary_configured']
            request = Request(base + '/api/v1/learning-accounts',
                data=json.dumps({'username': 'packaging-fixture', 'password': 'offline-fixture'}).encode(),
                headers={'Content-Type': 'application/json'}, method='POST')
            with urlopen(request, timeout=5) as reply:
                assert json.load(reply)['account']['id']
            assert len(get('/api/v1/learning-accounts')['accounts']) == 1
            with urlopen(base + '/dashboard', timeout=5) as reply:
                assert b'<html' in reply.read().lower()
            # Same executable must dispatch the worker instead of booting another API.
            worker = subprocess.run([str(binary), '--english-worker'], input=b'{"platform":"invalid","action":"invalid"}\n',
                capture_output=True, env=environment, timeout=90, check=True)
            result = json.loads(worker.stdout)
            assert result.get('ok') is False
            probe = subprocess.run([str(binary), '--desktop-probe'], capture_output=True,
                env=environment, timeout=90, check=True)
            assert b'UH_DESKTOP_PROBE_OK' in probe.stdout
            print('PASS: frozen UI, SQLite accounts, desktop settings, worker dispatch, browser and JS assets')
        finally:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    capture_output=True, timeout=10, check=False)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            thread.join(timeout=2)


if __name__ == '__main__':
    main()
