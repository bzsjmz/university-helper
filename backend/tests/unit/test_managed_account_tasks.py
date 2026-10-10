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
