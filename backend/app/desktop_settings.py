"""Private, per-user desktop configuration. Never imports server settings."""
import json
import os
import re
import tempfile
import threading
from pathlib import Path

LOCK = threading.RLock()
DEFAULT_ENDPOINT = 'https://api.deepseek.com/chat/completions'
FONT_MAP_LIMIT = 4 * 1024 * 1024


def validate_font_map(raw):
    if len(raw) > FONT_MAP_LIMIT:
        raise ValueError('字体映射文件过大（最多 4 MiB）')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('请选择有效的字体映射 JSON 文件') from None
    if (not isinstance(data, dict) or not 1 <= len(data) <= 100000 or
            any(not isinstance(k, str) or not re.fullmatch(r'uni[0-9A-F]{4,6}', k)
                or int(k[3:], 16) > 0x10ffff or not isinstance(v, str)
                or not re.fullmatch('[0-9a-f]{32}', v) for k, v in data.items())):
        raise ValueError('字体映射格式无效，需要 Unicode 字形与哈希的对应表')
    return data


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
        font_file = directory / 'resource' / 'font_map_table.json'
        font_entries = 0
        try:
            if font_file.is_file() and font_file.stat().st_size <= FONT_MAP_LIMIT:
                font_entries = len(validate_font_map(font_file.read_bytes()))
        except (ValueError, OSError):
            pass
        return {**read_settings(directory), 'font_map_entries': font_entries, **{
            role + '_configured': (directory / name).exists() and bool((directory / name).read_text().strip())
            for role, name in (('primary', 'ai-primary.key'), ('fallback', 'ai-fallback.key'))}}
