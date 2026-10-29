"""Private, per-user desktop configuration. Never imports server settings."""
import json
import os
import tempfile
import threading
from pathlib import Path

LOCK = threading.RLock()
DEFAULT_ENDPOINT = 'https://api.deepseek.com/chat/completions'


def private_write(path, value):
    """Create with private permissions, then atomically replace the old file."""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.settings-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_settings(directory):
    path = Path(directory) / 'desktop-settings.json'
    if not path.exists():
        return {'endpoint': DEFAULT_ENDPOINT, 'model': ''}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) for k in ('endpoint', 'model')):
        raise ValueError('Invalid desktop settings')
    return {key: value[key] for key in ('endpoint', 'model')}


def apply_settings(directory):
    with LOCK:
        directory = Path(directory)
        value = read_settings(directory)
        os.environ['CHAOXING_AI_ENDPOINT'] = value['endpoint']
        os.environ['CHAOXING_AI_MODEL'] = value['model']
        os.environ['CHAOXING_AI_PRIMARY_KEY_FILE'] = str(directory / 'ai-primary.key')
        os.environ['CHAOXING_AI_KEY_FILE'] = str(directory / 'ai-fallback.key')
        for name in ('ai-primary.key', 'ai-fallback.key'):
            if not (directory / name).exists():
                private_write(directory / name, '')


def public_settings(directory):
    with LOCK:
        directory = Path(directory)
        return {**read_settings(directory), **{
            role + '_configured': (directory / name).exists() and bool((directory / name).read_text().strip())
            for role, name in (('primary', 'ai-primary.key'), ('fallback', 'ai-fallback.key'))}}
