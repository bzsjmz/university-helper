"""Bounded local API usage events; never include credentials or question text."""
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

_LOCK = threading.Lock()
_MAX_BYTES = 8 * 1024 * 1024


def record_response(provider, role, attempt, started, response=None):
    filename = os.environ.get('CHAOXING_API_USAGE_FILE', '').strip()
    if not filename:
        return
    status = getattr(response, 'status_code', None)
    event = {'at': datetime.now(UTC).isoformat(), 'provider': provider,
             'key_role': role, 'attempt': attempt,
             'status': status if isinstance(status, int) else None,
             'duration_ms': round((time.monotonic() - started) * 1000, 1)}
    if provider == 'AI' and response is not None:
        try:
            body = response.json()
            usage = body.get('usage', {}) if isinstance(body, dict) else {}
            for field in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
                value = usage.get(field) if isinstance(usage, dict) else None
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    event[field] = value
        except Exception:
            pass
    try:
        path = Path(filename)
        with _LOCK:
            if path.exists() and path.stat().st_size >= _MAX_BYTES:
                os.replace(path, str(path) + '.1')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as stream:
                stream.write(json.dumps(event) + '\n')
    except OSError:
        # Monitoring must never turn a successful answer into a retry.
        pass
