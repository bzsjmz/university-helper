"""Optional, owner-scoped read-only status feed for external command clients."""
import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

STATUSES = {'pending', 'running', 'paused', 'cancelling', 'stopping', 'completed', 'cancelled', 'failed', 'error'}
ACTIVE = {'pending', 'running', 'paused', 'cancelling', 'stopping'}


def text(value, limit=120):
    return ' '.join(str(value or '').split())[:limit]


def counter(value):
    try: return max(0, int(value or 0))
    except (ValueError, TypeError, OverflowError): return 0


def build_snapshot(tasks, now=None):
    counts = {status: sum(task.get('status') == status for task in tasks) for status in STATUSES}
    ordered = sorted(tasks, key=lambda task: (task.get('status') in ACTIVE, str(task.get('updated_at') or '')), reverse=True)
    records = []
    for task in ordered[:200]:
        progress = task.get('progress') or {}
        records.append({
            'task_id': text(task.get('task_id'), 64),
            'account_name': text(task.get('account_name'), 64) or '旧任务（账号未记录）',
            'account_label': text(task.get('account_label'), 32),
            'status': task.get('status') if task.get('status') in STATUSES else 'unknown',
            'updated_at': text(task.get('updated_at'), 40),
            'course': text(progress.get('current_course')),
            'chapter': text(progress.get('current_chapter')),
            'courses_done': counter(progress.get('completed')),
            'courses_total': counter(progress.get('total')),
            'chapters_done': counter(progress.get('completed_chapters')),
            'chapters_total': counter(progress.get('total_chapters')),
            'courses_failed': counter(progress.get('failed')),
        })
    return {'version': 1, 'generated_at': (now or datetime.now(UTC)).isoformat(),
            'total': len(tasks), 'counts': counts, 'truncated': len(tasks) > len(records), 'tasks': records}


def write_snapshot(directory, snapshot):
    directory = Path(directory)
    payload = json.dumps(snapshot, ensure_ascii=False).encode()
    with tempfile.NamedTemporaryFile(dir=directory, prefix='.status-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try: os.replace(temporary, directory / 'status.json')
    finally: temporary.unlink(missing_ok=True)


async def status_export_loop(directory, owner):
    from .learning_manager import learning_manager
    while True:
        try:
            tasks = await asyncio.to_thread(learning_manager.list_tasks, owner)
            await asyncio.to_thread(write_snapshot, directory, build_snapshot(tasks))
        except Exception as exc:
            # Keep the previous snapshot; readers enforce freshness and never
            # present it as current after an export/service failure.
            logger.warning('Task status export failed ({})', type(exc).__name__)
        await asyncio.sleep(5)


def start_status_export():
    directory = os.environ.get('CHAOXING_STATUS_EXPORT_DIR', '').strip()
    owner_file = os.environ.get('CHAOXING_STATUS_OWNER_FILE', '').strip()
    if not directory or not owner_file:
        return None
    owner = Path(owner_file).read_text().strip()
    if not owner or not Path(directory).is_dir():
        raise RuntimeError('Task status export requires an owner and existing export directory')
    return asyncio.create_task(status_export_loop(directory, owner))
