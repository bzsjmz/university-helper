"""Persisted one-hour retry policy; every execution and credential is simulated."""
import json
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from app.api.v1 import learning_accounts as api
from app.services.course.chaoxing import auto_restart, learning_manager as module
from app.services.course.chaoxing.learning_manager import ChaoxingLearningManager
from app.services.course.chaoxing.managed_accounts import ManagedAccounts

NOW = datetime(2026, 10, 12, 2, tzinfo=UTC)


@pytest.fixture
def setup(monkeypatch):
    rows = {}
    manager = ChaoxingLearningManager.__new__(ChaoxingLearningManager)
    manager._lock = threading.Lock()
    manager._tasks = {}
    manager._loaded_task_users = set()
    manager._deleted_tasks = set()

    def write(kind, row):
        rows[row['task_id']] = deepcopy(row)
        return True

    def delete(kind, task_id, owner):
        row = rows.get(task_id)
        if row and row['user_id'] == owner:
            del rows[task_id]
            return True
        return False

    def read(kind=None, user_id=None, limit=2000, **kwargs):
        return [deepcopy(row) for row in rows.values() if user_id is None or row['user_id'] == user_id][:limit]

    monkeypatch.setattr(module.task_store, 'upsert_task', write)
    monkeypatch.setattr(module.task_store, 'delete_task', delete)
    monkeypatch.setattr(module.task_store, 'list_tasks', read)
    monkeypatch.setattr(module.task_store, 'get_task', lambda task_kind, task_id, user_id: deepcopy(rows.get(task_id)))
    monkeypatch.setattr(module, '_utc_now_iso', lambda: NOW.isoformat())
    monkeypatch.setattr(api, '_get_learning_manager', lambda: manager)
    monkeypatch.setattr(auto_restart, 'learning_manager', manager)
    monkeypatch.setattr(api, 'encrypt_str', lambda s: 'fernet:' + s)
    monkeypatch.setattr(api, 'decrypt_str', lambda s: s[7:])
    accounts = ManagedAccounts()
    accounts.credentials = Mock(return_value=({'account_key': 'fake-account-key', 'account_label': 'masked', 'label': 'name'}, 'fake-user', 'fake-password'))
    monkeypatch.setattr(api, 'managed_accounts', accounts)

    monkeypatch.setattr(manager, '_run_task_worker_guarded', lambda *args: None)
    original = api._start_saved_task('a', api.AccountTask(course_ids=['original-course'], concurrency=4,
                                                       chapter_limits={'original-course': 'chapter-end'}), 'owner')
    manager._fail_task(original, 'simulated failure')
    return manager, rows, accounts, original


def test_one_hour_then_one_attempt_only_with_original_scope(setup):
    manager, rows, accounts, original = setup
    due = NOW + timedelta(hours=1)
    assert rows[original]['auto_restart_at'] == due.isoformat()
    auto_restart.process_due_restarts(threading.Event(), due - timedelta(seconds=1))
    assert set(rows) == {original}
    # Current saved credentials, rather than a copy of the original, are used.
    accounts.credentials.return_value = ({'account_key': 'fake-account-key', 'account_label': 'masked', 'label': 'name'}, 'current-user', 'current-password')
    auto_restart.process_due_restarts(threading.Event(), due)
    assert len(rows) == 1 and original not in rows
    restarted = next(iter(rows))
    assert rows[restarted]['auto_restart_attempts'] == 1
    config = json.loads(api.decrypt_str(rows[restarted]['_restart_config']))
    assert config['course_ids'] == ['original-course']
    assert config['chapter_limits'] == {'original-course': 'chapter-end'}
    assert config['concurrency'] == 4
    manager._fail_task(restarted, 'still fails')
    assert rows[restarted]['auto_restart_state'] == 'exhausted'
    assert rows[restarted]['auto_restart_at'] is None
    auto_restart.process_due_restarts(threading.Event(), due + timedelta(days=1))
    assert set(rows) == {restarted} and rows[restarted]['status'] == 'failed'


def test_schedule_survives_manager_restore(setup):
    manager, rows, accounts, original = setup
    due = rows[original]['auto_restart_at']
    manager._tasks.clear()
    manager._restore_tasks_from_store()
    assert manager.get_task('owner', original)['auto_restart_at'] == due
    auto_restart.process_due_restarts(threading.Event(), NOW + timedelta(hours=1))
    assert original not in rows


def test_no_bulk_restart_of_existing_unscheduled_failures(setup):
    manager, rows, accounts, original = setup
    rows[original].pop('auto_restart_state')
    rows[original].pop('auto_restart_at')
    auto_restart.process_due_restarts(threading.Event(), NOW + timedelta(days=1))
    assert set(rows) == {original}


@pytest.mark.parametrize('action', ['delete', 'stop', 'new-start', 'manual-restart'])
def test_manual_actions_supersede_pending_automatic_restart(setup, action):
    manager, rows, accounts, original = setup
    if action == 'delete':
        manager.delete_task('owner', original)
    elif action == 'stop':
        manager.stop_task('owner', original)
    elif action == 'manual-restart':
        api.restart_saved_task(original, 'owner')
    else:
        api._start_saved_task('a', api.AccountTask(course_ids=['manual-choice']), 'owner')
    before = set(rows)
    auto_restart.process_due_restarts(threading.Event(), NOW + timedelta(hours=1))
    assert set(rows) == before
    if action == 'new-start':
        assert rows[original]['auto_restart_state'] == 'superseded'


def test_deleted_account_blocks_attempt_and_keeps_failure(setup):
    manager, rows, accounts, original = setup
    accounts.credentials.side_effect = KeyError('account removed')
    auto_restart.process_due_restarts(threading.Event(), NOW + timedelta(hours=1))
    assert set(rows) == {original} and rows[original]['status'] == 'failed'
    # KeyError after a claim must not leave it eligible for another start.
    assert rows[original]['auto_restart_attempts'] == 1
    auto_restart.process_due_restarts(threading.Event(), NOW + timedelta(hours=2))
    assert set(rows) == {original}


def test_claim_persist_failure_never_starts_worker(setup, monkeypatch):
    manager, rows, accounts, original = setup
    monkeypatch.setattr(module.task_store, 'upsert_task', lambda *args: False)
    auto_restart.process_due_restarts(threading.Event(), NOW + timedelta(hours=1))
    assert set(rows) == {original}
    assert manager._tasks[original]['auto_restart_attempts'] == 1


def test_consumed_claim_survives_crash_without_second_attempt(setup):
    manager, rows, accounts, original = setup
    assert manager.claim_auto_restart('owner', original, NOW + timedelta(hours=1))
    manager._tasks.clear()
    manager._restore_tasks_from_store()
    assert rows[original]['auto_restart_state'] == 'blocked'
    assert rows[original]['auto_restart_attempts'] == 1
    auto_restart.process_due_restarts(threading.Event(), NOW + timedelta(days=1))
    assert set(rows) == {original}


def test_two_scans_cannot_claim_same_failure_twice(setup):
    manager, rows, accounts, original = setup
    # The public restart helper uses the same per-account lock as manual starts.
    from concurrent.futures import ThreadPoolExecutor
    def attempt(_):
        try:
            return api.restart_saved_task(original, 'owner', automatic=True, now=NOW + timedelta(hours=1))
        except KeyError:
            return None
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sum(bool(result) for result in outcomes) == 1
    assert len(rows) == 1


def test_shutdown_does_not_start_due_task(setup):
    manager, rows, accounts, original = setup
    stop = threading.Event()
    stop.set()
    auto_restart.process_due_restarts(stop, NOW + timedelta(hours=1))
    assert set(rows) == {original}


@pytest.mark.asyncio
async def test_scheduler_shutdown_joins_inflight_scan(monkeypatch):
    import asyncio
    entered = threading.Event()
    exited = threading.Event()

    def scan(stop):
        entered.set()
        assert stop.wait(2)
        exited.set()

    monkeypatch.setattr(auto_restart, 'process_due_restarts', scan)
    runner = asyncio.create_task(auto_restart.auto_restart_loop())
    for _ in range(200):
        if entered.is_set():
            break
        await asyncio.sleep(0.005)
    assert entered.is_set()
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    assert exited.is_set()


def test_completed_and_cancelled_tasks_have_no_schedule(setup):
    manager, rows, accounts, original = setup
    for status in ['completed', 'cancelled']:
        new_id = api._start_saved_task('b', api.AccountTask(course_ids=['course']), 'owner')
        manager._update_task(new_id, status=status)
        assert rows[new_id]['auto_restart_at'] is None
        assert rows[new_id]['auto_restart_state'] == 'ready'


def test_restore_interrupted_automatic_child_does_not_grant_retry(setup):
    manager, rows, accounts, original = setup
    auto_restart.process_due_restarts(threading.Event(), NOW + timedelta(hours=1))
    restarted = next(iter(rows))
    manager._tasks.clear()
    manager._restore_tasks_from_store()
    assert rows[restarted]['status'] == 'failed'
    assert rows[restarted]['auto_restart_state'] == 'exhausted'
    assert rows[restarted]['auto_restart_at'] is None


def test_claim_rejects_wrong_owner(setup):
    manager, rows, accounts, original = setup
    assert not manager.claim_auto_restart('stranger', original, NOW + timedelta(hours=1))
    assert rows[original]['auto_restart_attempts'] == 0


def test_late_failure_after_user_stop_is_not_restarted(setup):
    manager, rows, accounts, original = setup
    new_id = api._start_saved_task('b', api.AccountTask(course_ids=['course']), 'owner')
    manager.stop_task('owner', new_id)
    manager._fail_task(new_id, 'late failure after cancel')
    assert rows[new_id]['auto_restart_state'] == 'cancelled'
    assert rows[new_id]['auto_restart_at'] is None


def test_restart_while_cancelling_does_not_revive_cancelled_work(setup):
    manager, rows, accounts, original = setup
    new_id = api._start_saved_task('b', api.AccountTask(course_ids=['course']), 'owner')
    manager.stop_task('owner', new_id)
    manager._tasks.clear()
    manager._restore_tasks_from_store()
    assert rows[new_id]['auto_restart_state'] == 'cancelled'
    assert rows[new_id]['auto_restart_at'] is None
