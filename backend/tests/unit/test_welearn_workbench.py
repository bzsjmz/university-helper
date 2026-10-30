import json
import threading
import time
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import credential_crypto
from app.services.course.english.clients import PlatformError
from app.services.course.english.manager import EnglishManager
from app.services.course.english.welearn_store import WelearnStore, account_session
from app.services.course.english.welearn_workbench import WelearnWorkbench


@pytest.fixture
def bench(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'STORAGE_BACKEND', 'sqlite')
    monkeypatch.setattr(settings, 'SQLITE_PATH', str(tmp_path / 'isolated.sqlite'))
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', Fernet.generate_key().decode())
    credential_crypto._reset_for_tests()
    engine = EnglishManager()
    engine.execute = Mock(side_effect=lambda request, task=None:
        {'uid': request['credential'].split('=')[-1]} if request['action'] == 'account' else
        {'courses': [{'id': 'c', 'name': 'Course'}, {'id': 'd', 'name': 'Other'}]} if request['action'] == 'connect' else
        {'items': [{'id': '1', 'name': 'One'}, {'id': '2', 'name': 'Two'}, {'id': '3', 'name': 'Three'}]} if request['action'] == 'items' else
        {'status': 'finished', 'message': '平台确认', 'counts': {'platform_completed': 1}})
    manager = WelearnWorkbench(engine)
    yield manager
    manager.shutdown()
    for _ in range(100):
        if not manager.tasks:
            break
        time.sleep(.005)
    credential_crypto._reset_for_tests()


def save(bench, owner='alice', uid='one', aid=''):
    session = bench.engine.adopt_login(owner, 'welearn', {'credential': 'sid=' + uid, 'courses': []})
    return bench.save_session(owner, session['session_id'], 'Account ' + uid, aid)


def prepare(bench, owner='alice', uid='one'):
    account = save(bench, owner, uid)
    bench.courses(owner, account['id'])
    bench.items(owner, account['id'], 'c')
    return account


def config(ids=None):
    return {'selections': [{'course': 'c', 'items': ids or ['1', '3']}], 'seconds': 300,
            'submit': True, 'allow_ai': False, 'recording_bypass': False}


def finished(bench, tid, owner='alice'):
    for _ in range(200):
        records = bench.list_tasks(owner)
        task = next(t for t in records if t['id'] == tid)
        if task['status'] not in ('running', 'stopping'):
            return task
        time.sleep(.005)
    pytest.fail('Task did not finish')


def test_saved_login_survives_new_manager_and_never_returns_secrets(bench):
    account = save(bench)
    restored = WelearnWorkbench(bench.engine, WelearnStore())
    assert restored.accounts('alice')[0]['id'] == account['id']
    assert restored.accounts('bob') == []
    with pytest.raises(PlatformError):
        restored.courses('bob', account['id'])
    raw = bench.store.account('alice', account['id'])
    assert raw['credential_ciphertext'].startswith('fernet:')
    assert credential_crypto.decrypt_str(raw['credential_ciphertext']) == 'sid=one'
    assert 'credential' not in json.dumps(account)
    assert 'account_key' not in json.dumps(account)
    assert bench.engine.sessions == {}
    with account_session() as connection:
        cur = connection.cursor()
        cur.execute('SELECT credential_ciphertext FROM welearn_accounts')
        assert 'sid=one' not in cur.fetchone()['credential_ciphertext']


def test_relogin_updates_same_identity_and_rejects_wrong_account(bench):
    account = save(bench)
    assert save(bench)['id'] == account['id']
    assert len(bench.accounts('alice')) == 1
    with pytest.raises(PlatformError, match='不是原账号'):
        save(bench, uid='other', aid=account['id'])
    assert len(bench.accounts('alice')) == 1


def test_lost_save_reply_can_retry_without_logging_in_again(bench):
    session = bench.engine.adopt_login('alice', 'welearn', {'credential': 'sid=one', 'courses': []})
    first = bench.save_session('alice', session['session_id'], 'Account')
    assert bench.save_session('alice', session['session_id'], 'Account') == first
    assert bench.engine.execute.call_count == 1
    with pytest.raises(PlatformError):
        bench.save_session('bob', session['session_id'], 'Account')


def test_save_requires_encryption_and_real_welearn_login(bench, monkeypatch):
    import app.services.course.english.welearn_store as module
    monkeypatch.setattr(module, 'encrypt_str', lambda value: value)
    with pytest.raises(PlatformError, match='加密'):
        save(bench)
    assert bench.accounts('alice') == []


def test_no_courses_or_uid_is_not_saved():
    from app.services.course.english.welearn_identity import account_identity
    client = Mock()
    client.courses.return_value = []
    with pytest.raises(PlatformError):
        account_identity(client)
    client.request.assert_not_called()
    client.courses.return_value = [{'id': 'official'}]
    client.request.return_value = '<html>Login</html>'
    with pytest.raises(PlatformError):
        account_identity(client)
    client.request.return_value = '{"uid": 1234}'
    assert account_identity(client) == {'uid': '1234'}
    client.request.assert_called_with('/student/course_info.aspx', params={'cid': 'official'}, html=True)


def test_multiple_courses_use_exact_ids_in_catalog_order_and_persist_receipts(bench):
    account = prepare(bench)
    bench.items('alice', account['id'], 'd')
    request = config(['3', '1'])
    request['recording_bypass'] = True
    request['selections'].append({'course': 'd', 'items': ['2']})
    task = bench.start('alice', account['id'], request)
    result = finished(bench, task['id'])
    calls = [c.args[0] for c in bench.engine.execute.call_args_list if c.args[0]['action'] == 'answer']
    assert [(r['course'], r['item']) for r in calls] == [('c', '1'), ('c', '3'), ('d', '2')]
    assert all(r['recording_bypass'] and r['submit'] and not r['allow_ai'] for r in calls)
    assert result['completed_items'] == result['total_items'] == 3
    assert result['counts']['platform_completed'] == 3
    assert result['status'] == 'finished'
    assert '_config' not in result and 'sid=one' not in json.dumps(result)
    assert WelearnWorkbench(bench.engine).list_tasks('alice') == [result]
    assert not bench.tasks


@pytest.mark.parametrize('selection', [
    [{'course': 'c', 'items': ['missing']}], [{'course': 'c', 'items': ['1', '1']}],
    [{'course': 'missing', 'items': ['1']}], [{'course': 'c', 'items': []}],
    [{'course': 'c', 'items': ['1']}, {'course': 'c', 'items': ['2']}], []])
def test_invalid_selection_does_not_start_or_widen(bench, selection):
    account = prepare(bench)
    request = config()
    request['selections'] = selection
    with pytest.raises(PlatformError):
        bench.start('alice', account['id'], request)
    assert not any(c.args[0]['action'] == 'answer' for c in bench.engine.execute.call_args_list)
    assert bench.list_tasks('alice') == []


def test_independent_accounts_really_run_together_and_duplicates_block(bench):
    a = prepare(bench, uid='a')
    b = prepare(bench, uid='b')
    duplicate = prepare(bench, owner='bob', uid='a')
    barrier = threading.Barrier(3)
    release = threading.Event()
    def execute(request, task=None):
        barrier.wait(timeout=2)
        assert release.wait(2)
        return {'status': 'finished'}
    bench.engine.execute = execute
    try:
        ta = bench.start('alice', a['id'], config(['1']))
        tb = bench.start('alice', b['id'], config(['1']))
        barrier.wait(timeout=2)
        for owner, account in [('alice', a), ('bob', duplicate)]:
            with pytest.raises(PlatformError): bench.start(owner, account['id'], config())
            with pytest.raises(PlatformError): bench.delete_account(owner, account['id'])
            with pytest.raises(PlatformError): bench.rename(owner, account['id'], 'rename')
            with pytest.raises(PlatformError): bench.courses(owner, account['id'])
        assert len(bench.list_tasks('alice')) == 2
        with pytest.raises(PlatformError): bench.stop('bob', ta['id'])
    finally:
        release.set()
    assert finished(bench, ta['id'])['status'] == 'finished'
    assert finished(bench, tb['id'])['status'] == 'finished'


def test_stop_keeps_account_busy_until_worker_exit_and_does_not_run_next(bench):
    account = prepare(bench)
    entered, release = threading.Event(), threading.Event()
    visited = []
    def execute(request, task=None):
        visited.append(request['item']); entered.set()
        assert release.wait(2)
        return {'status': 'finished'}
    bench.engine.execute = execute
    try:
        task = bench.start('alice', account['id'], config())
        assert entered.wait(2)
        assert bench.stop('alice', task['id'])['status'] == 'stopping'
        with pytest.raises(PlatformError): bench.delete_account('alice', account['id'])
        with pytest.raises(PlatformError): bench.restart('alice', task['id'])
        with pytest.raises(PlatformError): bench.delete_task('alice', task['id'])
    finally:
        release.set()
    assert finished(bench, task['id'])['status'] == 'cancelled'
    assert visited == ['1']
    bench.delete_account('alice', account['id'])
    assert len(bench.list_tasks('alice')) == 1


def test_interrupted_task_restarts_exact_scope_and_current_credentials(bench):
    account = prepare(bench)
    original = {'id': 'old', 'status': 'running', 'started_at': 1, 'account_id': account['id'],
                '_config': credential_crypto.encrypt_str(json.dumps(config(['3'])))}
    bench.store.put_task('alice', original)
    assert bench.list_tasks('alice')[0]['status'] == 'interrupted'
    bench.engine.execute.reset_mock()
    restarted = bench.restart('alice', 'old')
    assert finished(bench, restarted['id'])['total_items'] == 1
    answers = [c.args[0] for c in bench.engine.execute.call_args_list if c.args[0]['action'] == 'answer']
    assert [r['item'] for r in answers] == ['3']
    assert answers[0]['credential'] == 'sid=one'
    bench.delete_task('alice', 'old')
    assert len(bench.list_tasks('alice')) == 1


def test_platform_error_is_not_retried_and_manual_is_not_completion(bench):
    account = prepare(bench)
    bench.engine.execute = Mock(return_value={'status': 'needs_manual', 'message': 'unsupported', 'counts': {'unsupported': 2}})
    task = bench.start('alice', account['id'], config(['1']))
    assert finished(bench, task['id'])['status'] == 'needs_manual'
    bench.engine.execute = Mock(side_effect=PlatformError('cookie=secret-remote-error'))
    task = bench.start('alice', account['id'], config())
    result = finished(bench, task['id'])
    assert result['status'] == 'failed' and 'secret' not in json.dumps(result)
    assert bench.engine.execute.call_count == 1


def test_storage_failure_admits_no_platform_writes(bench, monkeypatch):
    account = prepare(bench)
    monkeypatch.setattr(bench.store, 'put_task', Mock(side_effect=OSError('offline')))
    with pytest.raises(OSError): bench.start('alice', account['id'], config())
    assert not bench.tasks and not bench.busy
    assert not any(c.args[0]['action'] == 'answer' for c in bench.engine.execute.call_args_list)


def test_welearn_api_auth_validation_and_no_secret_reply(bench, monkeypatch):
    from app.api.v1 import welearn
    from app.dependencies import get_current_user_id
    monkeypatch.setattr(welearn, 'welearn_workbench', bench)
    app = FastAPI(); app.include_router(welearn.router)
    client = TestClient(app)
    for path in ['/accounts', '/tasks']:
        assert client.get(path).status_code in (401, 403)
    app.dependency_overrides[get_current_user_id] = lambda: 'alice'
    save(bench)
    response = client.get('/accounts')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert 'ciphertext' not in response.text and 'sid=one' not in response.text
    for data in [{'selections': [], 'submit': 'secret'}, {'session_id': 'secret', 'label': 123}]:
        response = client.post('/accounts' if 'session_id' in data else '/accounts/id/tasks', json=data)
        assert response.status_code == 422 and 'secret' not in response.text
    assert client.patch('/accounts/id', json={'label': '   '}).status_code == 422


def test_api_flags_strict_and_recording_requires_submit():
    from app.api.v1.welearn import TaskRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError): TaskRequest(**{**config(), 'submit': 'true'})
    with pytest.raises(ValidationError): TaskRequest(**{**config(), 'submit': False, 'recording_bypass': True})
    assert not TaskRequest(**config()).recording_bypass
