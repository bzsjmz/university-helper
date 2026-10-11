import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from app.services.course.chaoxing import learning_manager as module
from app.services.course.chaoxing.learning_manager import ChaoxingLearningManager
from app.services.course.chaoxing.task_admission import TaskAlreadyActiveError, TaskCapacityError
from app.services.course.chaoxing.managed_accounts import ManagedAccounts
from fastapi import HTTPException
from app.api.v1 import learning_accounts as api


def manager(monkeypatch):
    instance = ChaoxingLearningManager.__new__(ChaoxingLearningManager)
    instance._lock = threading.Lock()
    instance._tasks = {}
    instance._loaded_task_users = set()
    instance._persist_task_state = Mock(return_value=True)
    monkeypatch.setattr(instance, '_run_task_worker_guarded', lambda *args: None)
    return instance


def payload(account):
    return {'account_id': account, 'account_key': f'key-{account}', 'account_label': '138****0000',
            'account_name': account, 'username': account, 'password': 'dummy-test-password'}


def test_one_owner_can_start_two_independent_accounts(monkeypatch):
    instance = manager(monkeypatch)
    barrier = threading.Barrier(2)
    def start(account):
        barrier.wait()
        return instance.start_task('owner', payload(account))
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(start, ['a', 'b']))
    assert len(set(ids)) == 2
    assert {item['account_id'] for item in instance.list_tasks('owner')} == {'a', 'b'}
    assert all('password' not in item and 'username' not in item for item in instance.list_tasks('owner'))


def test_duplicate_account_is_atomic(monkeypatch):
    instance = manager(monkeypatch)
    def start(_):
        try: return instance.start_task('owner', payload('a'))
        except TaskAlreadyActiveError: return None
    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(start, range(4)))
    assert len([item for item in ids if item]) == 1


def test_same_physical_account_cannot_run_under_two_owners(monkeypatch):
    instance = manager(monkeypatch)
    instance.start_task('owner-a', payload('a'))
    with pytest.raises(TaskAlreadyActiveError):
        instance.start_task('owner-b', payload('a'))


def test_control_only_changes_target_account(monkeypatch):
    instance = manager(monkeypatch)
    first = instance.start_task('owner', payload('a'))
    second = instance.start_task('owner', payload('b'))
    instance._update_task(first, status='running')
    instance._update_task(second, status='running')
    instance.pause_task('owner', first)
    assert not instance._tasks[first]['_pause_event'].is_set()
    assert instance._tasks[second]['_pause_event'].is_set()
    assert instance.pause_task('another-owner', second)['status'] == 'error'
    instance.stop_task('owner', first)
    assert instance._tasks[first]['_stop_event'].is_set()
    assert not instance._tasks[second]['_stop_event'].is_set()


def test_deployment_limit_is_enforced(monkeypatch):
    monkeypatch.setenv('CHAOXING_MAX_ACTIVE_TASKS', '2')
    instance = manager(monkeypatch)
    instance.start_task('owner', payload('a'))
    instance.start_task('owner', payload('b'))
    with pytest.raises(TaskCapacityError): instance.start_task('owner', payload('c'))


def test_zero_limit_allows_more_than_upstream_default(monkeypatch):
    monkeypatch.setenv('CHAOXING_MAX_ACTIVE_TASKS', '0')
    instance = manager(monkeypatch)
    for number in range(module.MAX_ACTIVE_TASKS + 1):
        instance.start_task('owner', payload(str(number)))
    assert len(instance.list_tasks('owner')) == module.MAX_ACTIVE_TASKS + 1


def test_account_browsing_sessions_are_separate(monkeypatch):
    accounts = ManagedAccounts()
    accounts.credentials = lambda owner, account: ({}, account, 'dummy-password')
    clients = []
    def client():
        obj = Mock()
        obj.login.return_value = {'status': True}
        clients.append(obj)
        return obj
    monkeypatch.setattr('app.services.course.chaoxing.managed_accounts.ChaoxingSigninClient', client)
    with accounts.lock('owner', 'a'): a = accounts.client('owner', 'a')
    with accounts.lock('owner', 'b'): b = accounts.client('owner', 'b')
    assert a is not b
    assert a.session is not b.session
    with accounts.lock('owner', 'a'): assert accounts.client('owner', 'a') is a
    assert a.login.call_count == b.login.call_count == 1


def test_learning_login_failure_does_not_expire_management_login(monkeypatch):
    accounts = ManagedAccounts()
    accounts.credentials = lambda owner, account: ({}, account, 'dummy-password')
    client = Mock()
    client.login.return_value = {'status': False}
    monkeypatch.setattr('app.services.course.chaoxing.managed_accounts.ChaoxingSigninClient', lambda: client)
    with pytest.raises(HTTPException) as error:
        accounts.client('owner', 'a')
    assert error.value.status_code == 422
    assert accounts._clients == {}
    client.session.close.assert_called_once()


@pytest.mark.asyncio
async def test_start_uses_stored_credentials_and_trusted_account_identity(monkeypatch):
    monkeypatch.setattr(api, 'encrypt_str', lambda value: 'fernet:test-config')
    store = ManagedAccounts()
    store.credentials = lambda owner, account: ({'account_key': 'stored-key', 'account_label': 'masked', 'label': 'A'}, 'stored-user', 'stored-password')
    instance = Mock()
    instance.start_task.return_value = 'task-a'
    monkeypatch.setattr(api, 'managed_accounts', store)
    monkeypatch.setattr(api, '_get_learning_manager', lambda: instance)
    result = await api.start_task('a', api.AccountTask(course_ids=['course-a'], username='injected', password='injected'), 'owner')
    assert result['task_id'] == 'task-a'
    owner, value = instance.start_task.call_args.args
    assert owner == 'owner'
    assert value['username'] == 'stored-user' and value['password'] == 'stored-password'
    assert value['account_id'] == 'a' and value['account_key'] == 'stored-key'
    assert value['concurrency'] == 1


def test_config_is_persisted_but_never_exposed(monkeypatch):
    instance = manager(monkeypatch)
    task_id = instance.start_task('owner', {**payload('a'), '_restart_config': 'fernet:encrypted-settings'})
    snapshot = instance._persist_task_state.call_args.args[0]
    assert snapshot['_restart_config'] == 'fernet:encrypted-settings'
    assert '_restart_config' not in instance.get_task('owner', task_id)
    assert instance.get_task('owner', task_id)['can_restart'] is True
    restored = manager(monkeypatch)
    restored._merge_task_from_store(snapshot)
    assert restored.restart_config('owner', task_id) == ('a', 'fernet:encrypted-settings')
    with pytest.raises(KeyError): restored.restart_config('stranger', task_id)


def test_delete_terminal_record_removes_store_and_memory_and_blocks_active(monkeypatch):
    instance = manager(monkeypatch)
    instance._tasks['t'] = {'task_id': 't', 'user_id': 'owner', 'status': 'failed'}
    delete = Mock(return_value=True)
    monkeypatch.setattr(module.task_store, 'delete_task', delete)
    assert instance.delete_task('stranger', 't') is False
    delete.assert_not_called()
    instance._tasks['t']['status'] = 'paused'
    with pytest.raises(TaskAlreadyActiveError): instance.delete_task('owner', 't')
    delete.assert_not_called()
    instance._tasks['t']['status'] = 'failed'
    delete.side_effect = RuntimeError('storage unavailable')
    with pytest.raises(RuntimeError): instance.delete_task('owner', 't')
    assert 't' in instance._tasks
    delete.side_effect = None
    assert instance.delete_task('owner', 't') is True
    assert 't' not in instance._tasks
    delete.assert_called_with(module.LEARNING_TASK_KIND, 't', 'owner')
    assert not instance._merge_task_from_store({'task_id': 't', 'user_id': 'owner', 'status': 'failed'})
    assert 't' not in instance._tasks


@pytest.mark.asyncio
async def test_restart_decrypts_config_and_uses_current_saved_account_credentials(monkeypatch):
    import json
    from cryptography.fernet import Fernet
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(api, 'encrypt_str', lambda value: 'fernet:' + cipher.encrypt(value.encode()).decode())
    monkeypatch.setattr(api, 'decrypt_str', lambda value: cipher.decrypt(value[7:].encode()).decode())
    config = api.AccountTask(course_ids=['course-a'], concurrency=4, chapter_limits={'course-a': 'chapter-5'},
                             tiku_config={'token': 'test-token'}).model_dump(exclude={'username', 'password'})
    encrypted = api.encrypt_str(json.dumps(config))
    instance = Mock()
    instance.restart_config.return_value = ('a', encrypted)
    instance.start_task.return_value = 'new-task'
    store = ManagedAccounts()
    store.credentials = lambda owner, account: ({'account_key': 'key', 'account_label': 'masked', 'label': 'A'}, 'current-user', 'rotated-password')
    monkeypatch.setattr(api, 'managed_accounts', store)
    monkeypatch.setattr(api, '_get_learning_manager', lambda: instance)
    assert (await api.restart_task('old-task', 'owner'))['task_id'] == 'new-task'
    sent = instance.start_task.call_args.args[1]
    assert sent['concurrency'] == 4 and sent['chapter_limits'] == {'course-a': 'chapter-5'}
    assert sent['password'] == 'rotated-password' and sent['tiku_config']['token'] == 'test-token'
    assert 'test-token' not in sent['_restart_config']
    instance.restart_config.return_value = ('a', None)
    with pytest.raises(HTTPException) as exc: await api.restart_task('old-task', 'owner')
    assert exc.value.status_code == 409


def test_delete_drains_previous_persist_before_removing_record(monkeypatch):
    instance = manager(monkeypatch)
    sequence = module._TaskPersistSequencer()
    revision = sequence.reserve()
    instance._tasks['t'] = {'task_id': 't', 'user_id': 'owner', 'status': 'failed', '_persist_sequencer': sequence}
    started = threading.Event()
    release = threading.Event()
    operations = []
    def persist():
        started.set()
        assert release.wait(2)
        operations.append('upsert')
    monkeypatch.setattr(module.task_store, 'delete_task', lambda *args: operations.append('delete'))
    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(sequence.run, revision, persist)
        assert started.wait(2)
        remover = pool.submit(instance.delete_task, 'owner', 't')
        release.set()
        writer.result(timeout=2)
        assert remover.result(timeout=2)
    assert operations == ['upsert', 'delete']
    assert 't' not in instance._tasks


@pytest.mark.asyncio
async def test_restart_replaces_only_its_old_record_after_admission(monkeypatch):
    from cryptography.fernet import Fernet
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(api, 'encrypt_str', lambda value: 'fernet:' + cipher.encrypt(value.encode()).decode())
    monkeypatch.setattr(api, 'decrypt_str', lambda value: cipher.decrypt(value[7:].encode()).decode())
    config = api.encrypt_str('{"course_ids":["course-a"],"chapter_limits":{"course-a":"chapter-5"}}')
    instance = Mock()
    instance.restart_config.return_value = ('a', config)
    events = []
    instance.start_task.side_effect = lambda *args: events.append('start') or 'new-task'
    instance.delete_task.side_effect = lambda *args: events.append(('delete', args)) or True
    store = ManagedAccounts()
    store.credentials = lambda *args: ({'account_key': 'key', 'account_label': 'masked', 'label': 'A'}, 'user', 'password')
    monkeypatch.setattr(api, 'managed_accounts', store)
    monkeypatch.setattr(api, '_get_learning_manager', lambda: instance)
    response = await api.restart_task('old-task', 'owner')
    assert events == ['start', ('delete', ('owner', 'old-task'))]
    assert response['previous_task_removed'] is True
    assert response['task_id'] == 'new-task' and response['replaced_task_id'] == 'old-task'

    instance.start_task.side_effect = RuntimeError('thread admission failed')
    instance.delete_task.reset_mock()
    with pytest.raises(RuntimeError):
        await api.restart_task('old-task', 'owner')
    instance.delete_task.assert_not_called()

    instance.start_task.side_effect = None
    instance.start_task.return_value = 'another-task'
    instance.delete_task.side_effect = RuntimeError('storage unavailable')
    response = await api.restart_task('old-task', 'owner')
    assert response['task_id'] == 'another-task'
    assert response['previous_task_removed'] is False


@pytest.mark.asyncio
async def test_concurrent_restart_cannot_replay_replaced_terminal_record(monkeypatch):
    import asyncio
    from cryptography.fernet import Fernet
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(api, 'encrypt_str', lambda value: 'fernet:' + cipher.encrypt(value.encode()).decode())
    monkeypatch.setattr(api, 'decrypt_str', lambda value: cipher.decrypt(value[7:].encode()).decode())
    config = api.encrypt_str('{"course_ids":["course-a"]}')
    instance = Mock()
    old_exists = [True]
    def read(*args):
        if not old_exists[0]: raise KeyError('Task not found')
        return 'a', config
    instance.restart_config.side_effect = read
    instance.start_task.return_value = 'new-task'
    instance.delete_task.side_effect = lambda *args: old_exists.__setitem__(0, False) or True
    store = ManagedAccounts()
    store.credentials = lambda *args: ({'account_key': 'key', 'account_label': 'masked', 'label': 'A'}, 'user', 'password')
    monkeypatch.setattr(api, 'managed_accounts', store)
    monkeypatch.setattr(api, '_get_learning_manager', lambda: instance)
    results = await asyncio.gather(api.restart_task('old-task', 'owner'), api.restart_task('old-task', 'owner'), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert any(isinstance(r, HTTPException) and r.status_code == 404 for r in results)
    assert instance.start_task.call_count == 1


def test_delete_storage_failure_keeps_record_and_restart_config(monkeypatch):
    instance = manager(monkeypatch)
    instance._tasks['old'] = {'task_id': 'old', 'user_id': 'owner', 'account_id': 'a',
                             'status': 'failed', '_restart_config': 'fernet:config'}
    monkeypatch.setattr(module.task_store, 'delete_task', lambda *args: False)
    with pytest.raises(RuntimeError): instance.delete_task('owner', 'old')
    assert instance.restart_config('owner', 'old') == ('a', 'fernet:config')
