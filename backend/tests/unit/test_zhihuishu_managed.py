from copy import deepcopy
import json
import threading
import time
from urllib.parse import quote

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core import credential_crypto
from app.services.course.zhihuishu.adapter import ZhihuishuAdapter
from app.services.course.zhihuishu.managed import (
    ACCOUNT_KIND, QUEUE_KIND, ManagedError, ZhihuishuManager, restore_adapter, session_data,
)
from app.storage.sqlite import SqliteStorage


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', Fernet.generate_key().decode())
    credential_crypto._reset_for_tests()
    storage = SqliteStorage(str(tmp_path / 'zhihuishu.sqlite'))
    manager = ZhihuishuManager(store=storage.tasks)
    yield manager
    manager.shutdown()
    storage.tasks.close()
    credential_crypto._reset_for_tests()


def logged_in(identity='platform-user-1'):
    adapter = ZhihuishuAdapter()
    cookies = {'CASLOGC': quote(json.dumps({'uuid': identity})), 'session_cookie': 'private_cookie_value'}
    adapter.auth.cookies = cookies
    for name, value in cookies.items():
        adapter.auth.session.cookies.set(name, value, domain='.zhihuishu.com', path='/', secure=True)
    adapter._init_services(cookies)
    adapter.learning.session.cookies.clear()
    adapter.learning.session.cookies.update(adapter.auth.session.cookies)
    return adapter


def account(manager, owner='owner-a', identity='platform-user-1', label='账号一'):
    return manager.save_login(owner, logged_in(identity), label)


def configure(manager, row, owner='owner-a', watch=None):
    adapter = manager.adapter(owner, row['account_id'])
    adapter.learning.get_course_list = lambda **kw: [
        {'courseId': 'one', 'courseName': '课程一', 'secret': 'never_public'},
        {'courseId': 'two', 'courseName': '课程二'},
    ]
    adapter.learning.get_video_list = lambda rac: {'courseId': 'one' if rac == 'never_public' else rac, 'recruitId': 'recruit',
        'videoChapterDtos': [{'id': 'chapter', 'videoLessons': [{'id': 'lesson',
            'videoId': 'video', 'videoSec': 1, 'name': '视频'}]}]}
    adapter.learning.query_study_info = lambda *args: {}
    adapter.learning.watch_video = watch or (lambda *args, **kwargs: True)
    return adapter


def wait_queue(manager, queue_id, status=None):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        row = manager.queues[queue_id]
        if status is not None and row['status'] == status:
            return deepcopy(row)
        if status is None and not manager.workers[queue_id].is_alive():
            return deepcopy(row)
        time.sleep(0.02)
    pytest.fail('queue did not reach expected state')


def test_session_is_encrypted_and_public_records_have_no_credentials(manager):
    row = account(manager)
    raw = manager.store.get_task(ACCOUNT_KIND, row['account_id'], 'owner-a')
    assert raw['session_ciphertext'].startswith('fernet:')
    assert 'private_cookie_value' not in json.dumps(raw)
    assert set(row) == {'account_id', 'label', 'status', 'created_at', 'updated_at'}
    assert 'account_key' not in json.dumps(manager.accounts('owner-a'))


def test_restores_after_new_manager_and_keeps_cookie_domains(manager):
    row = account(manager)
    restored = ZhihuishuManager(store=manager.store).adapter('owner-a', row['account_id'])
    assert restored.auth.uuid == 'platform-user-1'
    assert any(c.name == 'session_cookie' and c.domain == '.zhihuishu.com' and c.secure
               for c in restored.learning.session.cookies)


def test_cookies_cannot_be_restored_for_external_domains():
    data = session_data(logged_in())
    data['cookies'][0]['domain'] = '.example.com'
    with pytest.raises(ManagedError):
        restore_adapter(data)


def test_export_excludes_external_domain_cookie():
    adapter = logged_in()
    adapter.learning.session.cookies.set('external', 'never_export', domain='example.com')
    assert 'never_export' not in json.dumps(session_data(adapter))


def test_identity_mismatch_fails_closed():
    data = session_data(logged_in())
    data['uuid'] = 'another-platform-user'
    with pytest.raises(ManagedError):
        restore_adapter(data)


def test_same_identity_relogin_updates_instead_of_creating_duplicate(manager):
    one = account(manager)
    two = account(manager, label='新备注')
    assert one['account_id'] == two['account_id']
    assert manager.accounts('owner-a')[0]['label'] == '新备注'


def test_owner_isolation_and_missing_account(manager):
    row = account(manager)
    assert manager.accounts('owner-b') == []
    for method in (manager.adapter, manager.delete):
        with pytest.raises(ManagedError) as error:
            method('owner-b', row['account_id'])
        assert error.value.status_code == 404


def test_updating_selected_account_rejects_wrong_identity(manager):
    row = account(manager)
    with pytest.raises(ManagedError):
        manager.save_login('owner-a', logged_in('other'), account_id=row['account_id'])
    assert len(manager.accounts('owner-a')) == 1


def test_encryption_missing_refuses_plaintext(manager, monkeypatch):
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY')
    monkeypatch.setenv('ENV', 'dev')
    credential_crypto._reset_for_tests()
    with pytest.raises(ManagedError):
        account(manager)
    assert manager.accounts('owner-a') == []


def test_corrupt_encrypted_session_refuses_restore(manager):
    row = account(manager)
    raw = manager.store.get_task(ACCOUNT_KIND, row['account_id'], 'owner-a')
    raw['session_ciphertext'] = 'fernet:invalid'
    manager.store.upsert_task(ACCOUNT_KIND, raw)
    manager.adapters.clear()
    with pytest.raises(ManagedError) as error:
        manager.adapter('owner-a', row['account_id'])
    assert error.value.status_code == 401


def test_catalogue_is_private_and_session_errors_are_sanitized(manager):
    row = account(manager)
    adapter = configure(manager, row)
    assert 'never_public' not in json.dumps(manager.courses('owner-a', row['account_id']))
    def expired(**kwargs):
        raise ValueError('Cookie=private_cookie_value https://example.com/?token=secret')
    adapter.learning.get_course_list = expired
    with pytest.raises(ManagedError) as error:
        manager.courses('owner-a', row['account_id'])
    assert 'private_cookie_value' not in str(error.value)
    assert manager.accounts('owner-a')[0]['status'] == 'needs_login'


@pytest.mark.parametrize('selection', [[], [{'course_id': 'unknown'}], [{'course_id': 'one'}, {'course_id': 'one'}]])
def test_invalid_course_selection_does_not_start(manager, selection):
    row = account(manager)
    configure(manager, row)
    with pytest.raises(ManagedError):
        manager.start_queue('owner-a', row['account_id'], selection)
    assert not manager.queues


def test_multiple_courses_execute_serially_with_scope_and_mode(manager):
    row = account(manager)
    watched = []
    adapter = configure(manager, row, watch=lambda video, **kwargs: watched.append(video['course_id']) or True)
    q = manager.start_queue('owner-a', row['account_id'], [
        {'course_id': 'one', 'end_chapter_id': 'chapter'}, {'course_id': 'two'}], speed=2, task_type='ai-course')
    result = wait_queue(manager, q['task_id'])
    assert result['status'] == 'completed'
    assert watched == ['one', 'two']
    assert [c['status'] for c in result['courses']] == ['completed', 'completed']
    assert adapter.ai_config['enabled'] is True
    assert manager.list_queues('owner-b') == []
    saved = manager.list_queues('owner-a')[0]
    assert saved['courses'][0]['end_chapter_id'] == 'chapter'


def test_first_course_failure_stops_following_course(manager):
    row = account(manager)
    watched = []
    configure(manager, row, watch=lambda video, **kwargs: watched.append(video['course_id']) and False)
    q = manager.start_queue('owner-a', row['account_id'], [{'course_id': 'one'}, {'course_id': 'two'}])
    result = wait_queue(manager, q['task_id'])
    assert result['status'] == 'failed'
    assert result['courses'][1]['status'] == 'waiting'
    assert watched == ['one']


def test_empty_video_list_stops_queue(manager):
    row = account(manager)
    adapter = configure(manager, row)
    adapter.learning.get_video_list = lambda rac: {}
    q = manager.start_queue('owner-a', row['account_id'], [{'course_id': 'one'}, {'course_id': 'two'}])
    assert wait_queue(manager, q['task_id'])['status'] == 'failed'


def test_invalid_chapter_scope_does_not_expand_to_whole_course(manager):
    row = account(manager)
    configure(manager, row)
    q = manager.start_queue('owner-a', row['account_id'], [{'course_id': 'one', 'end_chapter_id': 'missing'}])
    assert wait_queue(manager, q['task_id'])['status'] == 'failed'


def test_cancel_holds_real_account_lock_until_worker_exits(manager):
    row = account(manager)
    entered = threading.Event()
    release = threading.Event()
    def watch(*args, **kwargs):
        entered.set()
        release.wait(8)
        return True
    configure(manager, row, watch=watch)
    q = manager.start_queue('owner-a', row['account_id'], [{'course_id': 'one'}, {'course_id': 'two'}])
    assert entered.wait(5)
    try:
        manager.control('owner-a', q['task_id'], 'cancel')
        for action in (lambda: manager.delete('owner-a', row['account_id']),
                       lambda: manager.save_login('owner-a', logged_in()),
                       lambda: manager.start_queue('owner-a', row['account_id'], [{'course_id': 'two'}])):
            with pytest.raises(ManagedError) as error:
                action()
            assert error.value.status_code == 409
    finally:
        release.set()
    result = wait_queue(manager, q['task_id'])
    assert result['status'] == 'cancelled'
    assert result['courses'][1]['status'] == 'waiting'
    manager.delete('owner-a', row['account_id'])
    assert manager.accounts('owner-a') == []


def test_same_real_account_saved_by_another_owner_cannot_run_twice(manager):
    a = account(manager)
    b = account(manager, owner='owner-b')
    entered, release = threading.Event(), threading.Event()
    configure(manager, a, watch=lambda *a, **kw: entered.set() or release.wait(8))
    configure(manager, b, owner='owner-b')
    q = manager.start_queue('owner-a', a['account_id'], [{'course_id': 'one'}])
    assert entered.wait(5)
    try:
        with pytest.raises(ManagedError) as error:
            manager.start_queue('owner-b', b['account_id'], [{'course_id': 'two'}])
        assert error.value.status_code == 409
    finally:
        release.set()
    wait_queue(manager, q['task_id'])


def test_recovery_marks_interrupted_without_starting_threads(manager):
    row = account(manager)
    raw = {'task_id': 'queue', 'user_id': 'owner-a', 'account_id': row['account_id'],
           'status': 'paused', 'courses': [{'course_id': 'one', 'status': 'running'},
                                         {'course_id': 'two', 'status': 'waiting'}]}
    manager.store.upsert_task(QUEUE_KIND, raw)
    new_manager = ZhihuishuManager(store=manager.store)
    new_manager.recover()
    recovered = new_manager.list_queues('owner-a')[0]
    assert recovered['status'] == 'interrupted'
    assert recovered['courses'][0]['status'] == 'interrupted'
    assert not new_manager.workers


def test_paused_queue_does_not_start_next_course_until_resumed(manager):
    row = account(manager)
    entered, release = threading.Event(), threading.Event()
    watched = []
    def watch(video, **kwargs):
        watched.append(video['course_id'])
        if video['course_id'] == 'one':
            entered.set()
            release.wait(8)
        return True
    configure(manager, row, watch=watch)
    q = manager.start_queue('owner-a', row['account_id'], [{'course_id': 'one'}, {'course_id': 'two'}])
    assert entered.wait(5)
    manager.control('owner-a', q['task_id'], 'pause')
    release.set()
    time.sleep(1.2)
    assert watched == ['one']
    assert manager.queues[q['task_id']]['status'] == 'paused'
    manager.control('owner-a', q['task_id'], 'resume')
    assert wait_queue(manager, q['task_id'])['status'] == 'completed'
    assert watched == ['one', 'two']


def test_expired_qr_cleanup_signals_worker(manager):
    cancel = threading.Event()
    manager.qr['old'] = {'owner': 'owner-a', 'created': time.time()-601, 'cancel': cancel}
    manager.cleanup()
    assert cancel.is_set()
    assert not manager.qr


def test_qr_success_saves_account_and_does_not_expose_cookie(manager):
    class QRAdapter:
        def login_with_qr(self, callback, cancel_event=None):
            callback(b'fixture image')
            source = logged_in()
            self.auth, self.learning = source.auth, source.learning
    manager.adapter_factory = QRAdapter
    first = manager.qr_login('owner-a', '扫码账号')
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        status = manager.qr_status('owner-a', first['session_id'])
        if status['status'] == 'success':
            break
        time.sleep(0.02)
    assert status['status'] == 'success'
    assert status['account']['label'] == '扫码账号'
    assert 'private_cookie_value' not in json.dumps(status)


def test_owner_cannot_control_other_owners_queue(manager):
    manager.queues['queue'] = {'task_id': 'queue', 'user_id': 'owner-a'}
    with pytest.raises(ManagedError) as error:
        manager.control('owner-b', 'queue', 'cancel')
    assert error.value.status_code == 404
    manager.queues.clear()


def test_qr_owner_and_expiry_are_checked(manager):
    manager.qr['qr'] = {'owner': 'owner-a', 'status': 'pending', 'created': time.time()-601,
                        'cancel': threading.Event()}
    with pytest.raises(ManagedError) as error:
        manager.qr_status('owner-b', 'qr')
    assert error.value.status_code == 404
    with pytest.raises(ManagedError) as error:
        manager.qr_status('owner-a', 'qr')
    assert error.value.status_code == 410


def test_api_requires_auth_and_validates_selections(manager, monkeypatch):
    import app.api.v1.zhihuishu_managed as api
    from app.dependencies import get_current_user_id
    monkeypatch.setattr(api, 'zhihuishu_manager', manager)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.get('/accounts').status_code == 401
    app.dependency_overrides[get_current_user_id] = lambda: 'owner-a'
    assert client.get('/accounts').json()['data'] == []
    row = account(manager)
    assert client.get('/accounts').json()['data'][0]['account_id'] == row['account_id']
    assert client.post('/accounts/unknown/queue', json={'courses': []}).status_code == 422
    assert client.post('/accounts/unknown/queue', json={'courses': [{'course_id': 'one'}], 'speed': 4}).status_code == 422
    assert client.get('/accounts/unknown/courses').status_code == 404


def test_valid_multi_course_request_starts_queue_through_api(manager, monkeypatch):
    import app.api.v1.zhihuishu_managed as api
    from app.dependencies import get_current_user_id
    monkeypatch.setattr(api, 'zhihuishu_manager', manager)
    row = account(manager)
    watched = []
    configure(manager, row, watch=lambda video, **kwargs: watched.append(video['course_id']) or True)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_user_id] = lambda: 'owner-a'
    with TestClient(app) as client:
        response = client.post('/accounts/' + row['account_id'] + '/queue', json={
            'courses': [{'course_id': 'one', 'end_chapter_id': 'chapter'}, {'course_id': 'two'}],
            'speed': 2, 'task_type': 'ai-course', 'auto_answer': True})
    assert response.status_code == 200
    queue = wait_queue(manager, response.json()['data']['task_id'])
    assert queue['status'] == 'completed'
    assert watched == ['one', 'two']
    assert queue['speed'] == 2
    assert queue['task_type'] == 'ai-course'
    assert queue['courses'][0]['end_chapter_id'] == 'chapter'
    assert manager.list_queues('owner-b') == []


def test_unexpected_api_error_logs_only_operation_and_exception_class(manager, monkeypatch, caplog):
    import app.api.v1.zhihuishu_managed as api
    from app.dependencies import get_current_user_id
    monkeypatch.setattr(api, 'zhihuishu_manager', manager)
    def broken(owner):
        raise RuntimeError('Cookie=private_session_value https://example.com/?secret=do_not_log')
    monkeypatch.setattr(manager, 'accounts', broken)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_user_id] = lambda: 'owner-a'
    response = TestClient(app).get('/accounts')
    assert response.status_code == 503
    assert 'RuntimeError' in caplog.text
    assert 'broken' in caplog.text
    for secret in ('private_session_value', 'do_not_log', 'https://example.com'):
        assert secret not in caplog.text
        assert secret not in response.text


def test_actual_app_startup_recovers_queues_and_serves_authenticated_accounts(manager, monkeypatch):
    import app.main as main
    import app.api.v1.zhihuishu_managed as managed_api
    import app.services.course.chaoxing.status_export as exports
    from app.core.security import create_access_token
    row = account(manager)
    manager.store.upsert_task(QUEUE_KIND, {'task_id': 'boot-queue', 'user_id': 'owner-a',
        'account_id': row['account_id'], 'status': 'running',
        'courses': [{'course_id': 'one', 'status': 'running'}]})
    monkeypatch.setattr(managed_api, 'zhihuishu_manager', manager)
    monkeypatch.setattr(exports, 'start_status_export', lambda: None)
    token = create_access_token({'user_id': 'owner-a', 'tenant_db_name': 'tenant_fixture'})
    with TestClient(main.app, base_url='http://localhost') as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/v1/course/zhihuishu/managed/accounts').status_code in (401, 403)
        response = client.get('/api/v1/course/zhihuishu/managed/accounts',
                              headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == 200
        assert response.json()['data'][0]['account_id'] == row['account_id']
        assert manager.list_queues('owner-a')[0]['status'] == 'interrupted'


def test_real_course_response_with_no_completed_courses_keeps_valid_login():
    from app.services.course.zhihuishu.learning import ZhihuishuLearning
    from types import SimpleNamespace
    learning = ZhihuishuLearning({})
    def post(url, payload, **kwargs):
        result = {'totalCount': 4, 'courseOpenDtos': [{'courseId': str(n)} for n in range(4)]} if payload['status'] == 0 else {
            'totalCount': 0, 'courseOpenDtos': None}
        return SimpleNamespace(status_code=200, json=lambda: {'code': 200, 'result': result})
    learning._signed_post = post
    assert len(learning.get_course_list(require_valid_response=True)) == 4


@pytest.mark.parametrize('code,http,result', [
    (401,200,{'totalCount':0,'courseOpenDtos':None}),
    (200,401,{'totalCount':0,'courseOpenDtos':None}),
    (200,200,{'totalCount':4,'courseOpenDtos':None}),
    (200,200,{'totalCount':0}),
    (200,200,{}),
    (200,200,{'totalCount':'0','courseOpenDtos':None}),
    (200,200,{'totalCount':False,'courseOpenDtos':None}),
    (200,200,{'totalCount':0,'courseOpenDtos':{}}),
])
def test_empty_course_tolerance_does_not_accept_unknown_or_expired_login(code,http,result):
    from app.services.course.zhihuishu.learning import _course_page
    with pytest.raises(ValueError):
        _course_page({'code':code,'result':result},http,strict=True)


def test_valid_empty_course_bucket_can_be_null_or_array():
    from app.services.course.zhihuishu.learning import _course_page
    for items in (None,[]):
        assert _course_page({'code':200,'result':{'totalCount':0,'courseOpenDtos':items}},200,strict=True)['courseOpenDtos']==[]
