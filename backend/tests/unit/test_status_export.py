import json
from datetime import UTC, datetime

from app.services.course.chaoxing.status_export import build_snapshot, write_snapshot


def test_feed_excludes_credentials_logs_owners_and_notifications():
    task = {'task_id': 'a', 'user_id': 'private-owner', 'username': 'private-login', 'password': 'private-password',
            'logs': [{'message': 'private-log'}], 'account_key': 'private-fingerprint', 'notify_config': {'token': 'private-token'},
            'account_name': 'A', 'account_label': '138****0000', 'status': 'running',
            'progress': {'completed_chapters': 5, 'total_chapters': 10, 'current_course': 'Course'}}
    snapshot = build_snapshot([task])
    assert 'private-' not in json.dumps(snapshot)
    assert snapshot['tasks'][0]['chapters_done'] == 5
    assert snapshot['counts']['running'] == 1


def test_bounded_feed_prioritizes_active_tasks_and_preserves_total_counts():
    tasks = [{'task_id': str(n), 'status': 'completed'} for n in range(210)]
    tasks.append({'task_id': 'active', 'status': 'paused'})
    result = build_snapshot(tasks)
    assert result['total'] == 211 and result['counts']['completed'] == 210
    assert result['truncated'] and len(result['tasks']) == 200
    assert result['tasks'][0]['task_id'] == 'active'


def test_atomic_file_replacement_and_permissions(tmp_path):
    snapshot = build_snapshot([], datetime(2026, 10, 10, tzinfo=UTC))
    write_snapshot(tmp_path, snapshot)
    assert json.loads((tmp_path / 'status.json').read_text()) == snapshot
    assert (tmp_path / 'status.json').stat().st_mode & 0o777 == 0o600
    write_snapshot(tmp_path, build_snapshot([{'task_id': 'new', 'status': 'running'}]))
    assert json.loads((tmp_path / 'status.json').read_text())['tasks'][0]['task_id'] == 'new'
    assert list(tmp_path.glob('.status-*')) == []
