import json
import threading
from unittest.mock import Mock

import pytest

from app.services.course.english import clients
from app.services.course.english.clients import PlatformError, Welearn, course_url, storage_state
from app.services.course.english.manager import EnglishManager

URL = 'https://u.unipus.cn/user/student/mycourse/courseCatalog?courseId=1'
STATE = json.dumps({'cookies': [{'name': 'sid', 'value': 'private', 'domain': '.unipus.cn', 'path': '/'}], 'origins': []})


@pytest.mark.parametrize('url', [URL.replace('https:', 'http:'), URL.replace('u.unipus.cn', 'u.unipus.cn.evil.test'),
    URL.replace('u.unipus.cn', '127.0.0.1'), URL.replace('u.unipus.cn', 'secret@u.unipus.cn'),
    URL.replace('courseCatalog', 'other'), 'https://u.unipus.cn/user/student/mycourse/courseCatalog'])
def test_course_url_rejects_other_targets(url):
    with pytest.raises(PlatformError):
        course_url(url)


def test_storage_state_rejects_other_sites():
    assert storage_state(STATE)['cookies'][0]['value'] == 'private'
    with pytest.raises(PlatformError):
        storage_state(STATE.replace('.unipus.cn', '.example.com'))
    with pytest.raises(PlatformError):
        storage_state(json.dumps({'cookies': json.loads(STATE)['cookies'], 'origins': [
            {'origin': 'https://u.unipus.cn.evil.test', 'localStorage': []}]}))


def test_welearn_credentials_are_domain_scoped_and_no_redirects(monkeypatch):
    client = Welearn('sid=private; a=b=c')
    assert all(c.domain == 'welearn.sflep.com' for c in client.session.cookies)
    assert client.session.trust_env is False
    response = Mock(status_code=302)
    client.session.request = Mock(return_value=response)
    with pytest.raises(PlatformError):
        client.request('/ajax/authCourse.aspx')
    assert client.session.request.call_args.kwargs['allow_redirects'] is False
    response.close.assert_called_once()


def test_welearn_study_preserves_platform_grade_and_status(monkeypatch):
    client = Welearn('sid=private')
    client.items = Mock(return_value=({'cid': 'c', 'uid': 'u', 'classid': 'cl'}, [{'id': 'i'}]))
    calls = []
    def sco(action, identity, **data):
        calls.append((action, data))
        return {'ret': 0, 'comment': json.dumps({'cmi': {'total_time': '80', 'progress_measure': '0.2',
            'completion_status': 'incomplete', 'score': {'scaled': '37'}}})}
    client.sco = sco
    now = [0]
    monkeypatch.setattr(clients.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(clients.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    result = client.study('c', 'i', 60)
    assert result['status'] == 'finished'
    data = calls[-1][1]
    assert data['progress'] == '0.2' and data['crate'] == '37' and data['cstatus'] == 'incomplete'
    assert data['session_time'] == '60' and data['total_time'] == '140'
    assert all(action != 'setscoinfo' for action, _ in calls)


def test_welearn_rejects_unselected_lesson():
    client = Welearn('sid=private')
    client.items = Mock(return_value=({}, [{'id': 'real'}]))
    client.sco = Mock()
    with pytest.raises(PlatformError):
        client.study('c', 'other', 60)
    client.sco.assert_not_called()


@pytest.fixture
def manager(monkeypatch):
    from app.services.course.english import manager as module
    monkeypatch.setattr(module, 'encrypt_str', lambda s: 'encrypted:' + s)
    monkeypatch.setattr(module, 'decrypt_str', lambda s: s.removeprefix('encrypted:'))
    manager = EnglishManager()
    manager.execute = Mock(return_value={'ok': True, 'courses': [{'id': 'c', 'name': 'English'}], 'items': [{'id': 'i', 'name': 'Reading'}]})
    return manager


def test_sessions_and_tasks_are_owner_scoped_without_credentials(manager):
    result = manager.connect('alice', 'welearn', 'sid=private')
    assert 'private' not in json.dumps(result)
    sid = result['session_id']
    assert manager.sessions[sid]['credential'].startswith('encrypted:')
    with pytest.raises(PlatformError):
        manager.items('bob', sid, 'c')
    with pytest.raises(PlatformError):
        manager.disconnect('bob', sid)
    assert manager.list('bob') == []


def test_temporary_session_retains_rotation_without_returning_it(manager):
    manager.execute.return_value['_credential'] = 'sid=connected'
    result = manager.connect('alice', 'welearn', 'sid=original')
    sid = result['session_id']
    assert manager.sessions[sid]['credential'] == 'encrypted:sid=connected'
    assert '_credential' not in result
    manager.execute.return_value['_credential'] = 'sid=items-renewed'
    result = manager.items('alice', sid, 'c')
    assert '_credential' not in result
    assert manager.sessions[sid]['credential'] == 'encrypted:sid=items-renewed'


def test_must_read_course_and_choose_actual_item_before_start(manager):
    sid = manager.connect('alice', 'welearn', 'sid=private')['session_id']
    with pytest.raises(PlatformError):
        manager.start('alice', sid, 'c', 'i', 60)
    with pytest.raises(PlatformError):
        manager.items('alice', sid, 'someone-elses-course')
    manager.items('alice', sid, 'c')
    with pytest.raises(PlatformError):
        manager.start('alice', sid, 'c', 'missing', 60)


def test_cancellation_does_not_release_session_until_worker_finishes(manager):
    sid = manager.connect('alice', 'welearn', 'sid=private')['session_id']
    manager.items('alice', sid, 'c')
    entered, released, finished = threading.Event(), threading.Event(), threading.Event()
    def execute(request, task=None):
        entered.set()
        released.wait(2)
        return {'status': 'finished', 'message': 'done'}
    manager.execute = execute
    task = manager.start('alice', sid, 'c', 'i', 60)
    assert entered.wait(2)
    with pytest.raises(PlatformError):
        manager.start('alice', sid, 'c', 'i', 60)
    with pytest.raises(PlatformError):
        manager.stop('bob', task['id'])
    assert manager.stop('alice', task['id'])['status'] == 'stopping'
    with pytest.raises(PlatformError):
        manager.disconnect('alice', sid)
    released.set()
    # Wait on the actual worker via session lock; no real network/process calls.
    import time
    for _ in range(100):
        if not manager.sessions[sid]['busy']:
            finished.set()
            break
        time.sleep(0.01)
    assert finished.is_set()
    assert manager.list('alice')[0]['status'] == 'cancelled'
    assert 'private' not in json.dumps(manager.list('alice'))
    manager.disconnect('alice', sid)


def test_expired_session_is_unusable(manager):
    sid = manager.connect('alice', 'welearn', 'sid=private')['session_id']
    manager.sessions[sid]['created'] -= 3601
    with pytest.raises(PlatformError):
        manager.items('alice', sid, 'c')


def test_real_worker_entrypoint_rejects_invalid_cookie_without_echoing_secret():
    manager = EnglishManager()
    with pytest.raises(PlatformError) as caught:
        manager.execute({'platform': 'welearn', 'action': 'connect', 'credential': 'private-invalid'})
    assert 'private-invalid' not in str(caught.value)


def test_api_authentication_and_owner_forwarding(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import english_learning as routes
    from app.dependencies import get_current_user_id
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    assert client.get('/tasks').status_code == 401
    app.dependency_overrides[get_current_user_id] = lambda: 'alice'
    monkeypatch.setattr(routes.english_manager, 'list', Mock(return_value=[]))
    assert client.get('/tasks').json() == {'tasks': []}
    routes.english_manager.list.assert_called_once_with('alice')
    monkeypatch.setattr(routes.english_manager, 'start', Mock(return_value={'id': 'task'}))
    assert client.post('/sessions/s/tasks', json={'course': 'c', 'item': 'i', 'seconds': 60}).status_code == 200
    routes.english_manager.start.assert_called_once_with('alice', 's', 'c', 'i', 60, 'study', False, False, '', False)
    for seconds in (0, 1801, True, 30.5, '60'):
        assert client.post('/sessions/s/tasks', json={'course': 'c', 'item': 'i', 'seconds': seconds}).status_code == 422


def test_worker_environment_excludes_server_credentials(monkeypatch):
    from app.services.course.english import manager as module
    monkeypatch.setenv('MAIN_DB_PASSWORD', 'private-db-password')
    monkeypatch.setenv('JWT_SECRET_KEY', 'private-jwt-secret')
    popen = Mock()
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    EnglishManager._spawn()
    environment = popen.call_args.kwargs['env']
    assert 'MAIN_DB_PASSWORD' not in environment and 'JWT_SECRET_KEY' not in environment
    assert popen.call_args.kwargs['start_new_session'] is True


def test_stop_also_cleans_orphan_browser_group(monkeypatch):
    from app.services.course.english import manager as module
    process = Mock(pid=12345)
    process.poll.return_value = 0
    kill = Mock()
    monkeypatch.setattr(module.os, 'killpg', kill)
    EnglishManager.kill(process)
    kill.assert_called_once_with(12345, module.signal.SIGKILL)
    process.wait.assert_called_once()


def test_answer_flags_are_strict_and_explicit():
    from pydantic import ValidationError
    from app.api.v1.english_learning import StartRequest
    with pytest.raises(ValidationError):
        StartRequest(course='c', item='i', allow_ai=True)
    with pytest.raises(ValidationError):
        StartRequest(course='c', item='i', mode='answer', submit='true')
    request = StartRequest(course='c', item='i', mode='answer')
    assert request.submit is False and request.allow_ai is False


def test_public_counts_snapshot_is_detached_from_worker_updates():
    task = {key: '' for key in ('id','platform','course_name','item_name','seconds','status','message',
        'started_at','mode','submit','allow_ai')}
    task['counts'] = {'answered': 1}
    public = EnglishManager.public(task)
    task['counts']['answered'] = 2
    task['counts']['missing'] = 1
    assert public['counts'] == {'answered': 1}


def test_range_executes_in_catalog_order_and_aggregates_manual_results(manager):
    import time
    sid = manager.connect('alice', 'welearn', 'sid=private')['session_id']
    manager.sessions[sid]['items']['c'] = [{'id': str(i), 'name': f'Lesson {i}'} for i in range(4)]
    requests = []
    def execute(request, task=None):
        requests.append(request)
        return {'status': 'needs_manual' if request['item'] == '1' else 'finished',
                'message': 'done', 'counts': {'questions': 2, 'filled': 2}}
    manager.execute = execute
    task = manager.start('alice', sid, 'c', '1', 60, mode='answer', end_item='3')
    for _ in range(200):
        if not manager.sessions[sid]['busy']: break
        time.sleep(.005)
    result = manager.list('alice')[0]
    assert [r['item'] for r in requests] == ['1', '2', '3']
    assert all(r['seconds'] == 60 for r in requests)
    assert result['total_items'] == result['completed_items'] == 3
    assert result['status'] == 'needs_manual'
    assert result['counts'] == {'questions': 6, 'filled': 6}
    with pytest.raises(PlatformError): manager.start('alice', sid, 'c', '3', 60, end_item='1')
    with pytest.raises(PlatformError): manager.start('alice', sid, 'c', '0', 60, end_item='other')
    assert 'private' not in json.dumps(result)


def test_uai_courses_use_existing_browser_jwt_only_on_fixed_official_origin():
    from app.services.course.english.uai import courses, COURSES
    response = Mock(status=200)
    response.body.return_value = json.dumps({'code': 1, 'value': {'groups': {'CLASS_COURSE': [{'instanceId': 'ai-1', 'name': 'AI English'},
                                                {'instanceId': 'ai-1', 'name': 'duplicate'}]}}}).encode()
    context = Mock()
    context.cookies.return_value = [{'name': 'jwt', 'value': 'private-jwt'}]
    context.request.get.return_value = response
    assert courses(context) == [{'id': 'ai-1', 'name': 'AI English'}]
    context.request.get.assert_called_once_with(COURSES, headers={'Authorization': 'private-jwt',
            'Referer': 'https://uai.unipus.cn/portal/', 'Origin': 'https://uai.unipus.cn'}, timeout=15000, max_redirects=0)
    response.dispose.assert_called_once()
    context.cookies.return_value = []
    with pytest.raises(PlatformError):
        courses(context)
    assert context.request.get.call_count == 1


def test_removed_legacy_platform_is_rejected_even_outside_api(manager):
    with pytest.raises(PlatformError):
        manager.connect('alice', 'unipus', STATE)
    with pytest.raises(PlatformError):
        manager.logins.start('alice', 'unipus', 'u', 'p')


def test_stopping_range_does_not_start_next_lesson(manager):
    import time
    sid = manager.connect('alice', 'welearn', 'sid=private')['session_id']
    manager.sessions[sid]['items']['c'] = [{'id': str(i), 'name': str(i)} for i in range(2)]
    entered, released = threading.Event(), threading.Event()
    visited = []
    def execute(request, task=None):
        visited.append(request['item'])
        entered.set()
        assert released.wait(2)
        return {'status': 'finished', 'counts': {}}
    manager.execute = execute
    task = manager.start('alice', sid, 'c', '0', 30, end_item='1')
    assert entered.wait(2)
    manager.stop('alice', task['id'])
    released.set()
    for _ in range(200):
        if not manager.sessions[sid]['busy']: break
        time.sleep(.005)
    assert visited == ['0']
    assert manager.list('alice')[0]['status'] == 'cancelled'


def test_ai_storage_state_accepts_only_ai_and_content_local_storage():
    state = json.loads(STATE)
    state['origins'] = [{'origin': 'https://uai.unipus.cn', 'localStorage': [{'name':'jwt','value':'private-jwt'}]}]
    assert storage_state(json.dumps(state))['origins'] == state['origins']
    state['origins'][0]['origin'] = 'https://u.unipus.cn'
    with pytest.raises(PlatformError): storage_state(json.dumps(state))


def test_welearn_completed_leaf_is_skipped_without_opening_browser(monkeypatch):
    client = Mock()
    client.items.return_value = ({}, [{'id':'item','progress':'true'}])
    monkeypatch.setattr(clients, 'Welearn', lambda credential: client)
    browser = Mock(side_effect=AssertionError('No browser for completed lesson'))
    monkeypatch.setattr(clients, 'unipus_browser', browser)
    result = clients.welearn_answer('sid=private', 'course', 'item', 30, Mock())
    assert result['status'] == 'finished' and result['counts']['skipped'] == 1


def test_portal_light_loading_keeps_authentication_and_course_media():
    from app.services.course.english.uai import decorative_portal_resource
    request = Mock(resource_type='image', frame=Mock(url='https://uai.unipus.cn/'))
    assert decorative_portal_resource(request)
    request.frame.url = 'https://sso.unipus.cn/sso/login'
    assert not decorative_portal_resource(request)
    request.frame.url = 'https://ucontent.unipus.cn/_explorationpc_default/pc.html'
    assert not decorative_portal_resource(request)
    request.frame.url = 'https://uai.unipus.cn/'
    request.resource_type = 'script'
    assert not decorative_portal_resource(request)


def test_ucloud_proxy_login_keeps_captcha_and_fonts_but_portal_cover_art_is_blocked():
    from app.services.course.english.uai import decorative_portal_resource
    request = Mock(resource_type='image', frame=Mock(url='https://ucloud.unipus.cn/sso/login?service=official'))
    assert not decorative_portal_resource(request)
    request.resource_type = 'font'
    assert not decorative_portal_resource(request)
    request.frame.url = 'https://ucloud.unipus.cn/'
    assert decorative_portal_resource(request)


def test_portal_prefixed_ai5_login_keeps_its_verification_resources():
    from app.services.course.english.uai import decorative_portal_resource
    assert not decorative_portal_resource(Mock(resource_type='image', frame=Mock(url='https://uai.unipus.cn/portal/sso/login')))


@pytest.mark.parametrize('done,pending', [(True, False), ('已完成', '未完成'), ('<span>已完成</span>', '<span>未完成</span>')])
def test_welearn_completion_reads_only_selected_unit_without_synthetic_writes(done, pending):
    client = clients.Welearn('sid=fixture')
    client._item_units = {'leaf': 2}
    client.request = Mock(return_value={'info': [{'id': 'leaf', 'iscomplete': done}]})
    identity = {'cid': 'course', 'uid': 'user', 'classid': 'class'}
    assert client.item_complete(identity, 'leaf')
    client.request.assert_called_once_with('/ajax/StudyStat.aspx', params={'action':'scoLeaves', **identity, 'unitidx':2})
    client.request.return_value = {'info': [{'id': 'leaf', 'iscomplete': pending}]}
    assert not client.item_complete(identity, 'leaf')
    client.session.close()


@pytest.mark.parametrize('completed', [True, False])
def test_welearn_reading_requires_platform_confirmation_not_just_zero_questions(monkeypatch, completed):
    from contextlib import nullcontext
    from app.services.course.english import forms, welearn_page
    client = Mock()
    client.items.return_value = ({'cid':'c'}, [{'id':'leaf', 'progress':'false'}])
    client.item_complete.return_value = completed
    factory = Mock(return_value=client, BASE='https://welearn.sflep.com')
    monkeypatch.setattr(clients, 'Welearn', factory)
    context, page, frame = Mock(), Mock(), Mock()
    context.cookies.return_value = []
    monkeypatch.setattr(clients, 'unipus_browser', lambda *a, **kw: nullcontext((context,page)))
    monkeypatch.setattr(welearn_page, 'wait_content', lambda *a: [frame])
    monkeypatch.setattr(welearn_page, 'return_to_catalog', Mock())
    monkeypatch.setattr(forms, 'answer_page', lambda *a, **kw: {'status':'visited','counts':{'questions':0,'visited':1}})
    result = clients.welearn_answer('sid=private', 'c', 'leaf', 30, Mock())
    assert result['status'] == ('finished' if completed else 'needs_manual')
    assert result['counts'].get('platform_completed', 0) == int(completed)
    client.sco.assert_not_called()
    client.session.close.assert_called_once()


def test_welearn_internal_tabs_aggregate_answers_before_progress_confirmation(monkeypatch):
    from contextlib import nullcontext
    from app.services.course.english import forms, welearn_page
    client = Mock()
    client.items.return_value = ({'cid':'c'}, [{'id':'leaf', 'progress':'false'}])
    client.item_complete.return_value = True
    monkeypatch.setattr(clients, 'Welearn', Mock(return_value=client, BASE='https://welearn.sflep.com'))
    context, page, frame = Mock(), Mock(), Mock()
    context.cookies.return_value = []
    monkeypatch.setattr(clients, 'unipus_browser', lambda *a, **kw: nullcontext((context,page)))
    monkeypatch.setattr(welearn_page, 'wait_content', lambda *a: [frame])
    monkeypatch.setattr(welearn_page, 'return_to_catalog', Mock())
    answer = Mock(side_effect=[{'status':'advanced','counts':{'questions':0,'visited':1}},
        {'status':'advanced','counts':{'questions':2,'filled':2,'native_answers':2}},
        {'status':'finished','counts':{'questions':1,'filled':1,'native_answers':1,'submitted':1}}])
    monkeypatch.setattr(forms, 'answer_page', answer)
    result = clients.welearn_answer('sid=private','c','leaf',30,Mock(),submit=True)
    assert answer.call_count == 3
    assert result['counts']['questions'] == 3 and result['counts']['native_answers'] == 3
    assert result['counts']['platform_completed'] == 1
    client.sco.assert_not_called()


@pytest.mark.parametrize('completed', [True, False])
def test_welearn_restored_recording_only_submission_requires_catalog_confirmation(monkeypatch, completed):
    from contextlib import nullcontext
    from app.services.course.english import forms, welearn_page
    client = Mock()
    client.items.return_value = ({'cid': 'c'}, [{'id': 'leaf', 'progress': 'false'}])
    client.item_complete.return_value = completed
    monkeypatch.setattr(clients, 'Welearn', Mock(return_value=client, BASE='https://welearn.sflep.com'))
    context, page, frame = Mock(), Mock(), Mock()
    context.cookies.return_value = []
    monkeypatch.setattr(clients, 'unipus_browser', lambda *a, **kw: nullcontext((context, page)))
    monkeypatch.setattr(welearn_page, 'wait_content', lambda *a: [frame])
    exit_page = Mock()
    monkeypatch.setattr(welearn_page, 'return_to_catalog', exit_page)
    monkeypatch.setattr(forms, 'answer_page', lambda *a, **kw: {
        'status': 'finished', 'message': '已收到平台提交回执', 'counts': {'questions': 0, 'submitted': 1}})
    result = clients.welearn_answer('sid=private', 'c', 'leaf', 30, Mock(), submit=True)
    exit_page.assert_called_once()
    assert client.item_complete.called
    assert result['status'] == ('finished' if completed else 'needs_manual')
    assert result['counts'].get('platform_completed', 0) == int(completed)
    assert result['counts']['submitted'] == 1
    client.sco.assert_not_called()


@pytest.mark.parametrize('token_fields', [{'tokenInfo': {'jwt':'private-fixture-token'}}, {'Authorization':'private-fixture-token'}])
def test_uai5_login_accepts_portal_storage_without_any_cookie(token_fields):
    from app.services.course.english.login_browser import authenticated_result
    from app.services.course.english.uai import COURSES
    state = {'cookies': [], 'origins': [{'origin': 'https://uai.unipus.cn', 'localStorage': [
        {'name':'PORTAL_STATE_PERSISTENT', 'value':json.dumps(token_fields)}]}]}
    assert storage_state(json.dumps(state)) == state
    context = Mock()
    context.storage_state.return_value = state
    context.cookies.return_value = []
    response = context.request.get.return_value
    response.status = 200
    response.body.return_value = json.dumps({'code':1, 'value':{'groups':{
        'CLASS_COURSE':[{'instanceId':'course-v2:Unipus+English+2026','name':'English'}],
        'PERSONAL':[{'instanceId':'course-v2:Unipus+English+2026','name':'duplicate'},{'instanceId':'personal-1','name':'Personal'}],
        'EXPIRED':[{'instanceId':'expired-1','name':'Expired'}]}}}).encode()
    result = authenticated_result(context, Mock(url='https://uai.unipus.cn/portal/'), 'unipus_ai')
    assert result['courses'] == [{'id':'course-v2:Unipus+English+2026','name':'English'},{'id':'personal-1','name':'Personal'}]
    assert json.loads(result['credential']) == state
    assert context.request.get.call_args.args == (COURSES,)
    assert context.request.get.call_args.kwargs['headers']['Authorization'] == 'private-fixture-token'
    response.dispose.assert_called_once()


def test_uai5_empty_bookshelf_finishes_login_but_missing_auth_does_not():
    from app.services.course.english.login_browser import authenticated_result
    context = Mock()
    context.storage_state.return_value = {'cookies':[], 'origins':[]}
    context.cookies.return_value = []
    page = Mock(url='https://uai.unipus.cn/portal/')
    assert authenticated_result(context, page, 'unipus_ai') is None
    context.request.get.assert_not_called()
    context.cookies.return_value = [{'name':'jwt','value':'fixture-token'}]
    context.request.get.return_value.status = 200
    context.request.get.return_value.body.return_value = b'{"code":1,"value":{"groups":{}}}'
    # A cookie-based session remains supported; use a valid stored cookie shape.
    context.storage_state.return_value = {'cookies':[{'name':'jwt','value':'fixture-token','domain':'uai.unipus.cn','path':'/'}], 'origins':[]}
    assert authenticated_result(context, page, 'unipus_ai')['courses'] == []


@pytest.mark.parametrize('value', ['not-json', '{"tokenInfo":{"jwt":"bad\\ntoken"}}', '{"Authorization":42}', '[]'])
def test_uai5_malformed_portal_storage_cannot_replace_login(value):
    from app.services.course.english.uai_auth import portal_token
    state = {'cookies':[], 'origins':[{'origin':'https://uai.unipus.cn','localStorage':[{'name':'PORTAL_STATE_PERSISTENT','value':value}]}]}
    assert portal_token(state) is None
    with pytest.raises(PlatformError): storage_state(json.dumps(state))


@pytest.mark.parametrize('origin', ['https://evil.example','https://uai.unipus.cn.evil.example','https://ucontent.unipus.cn'])
def test_uai5_only_uses_portal_origin_token(origin):
    from app.services.course.english.uai_auth import portal_token
    assert portal_token({'origins':[{'origin':origin,'localStorage':[{'name':'PORTAL_STATE_PERSISTENT','value':'{"Authorization":"private-token"}'}]}]}) is None


@pytest.mark.parametrize('status,payload', [(401, {}), (200, {'code':6001,'value':{'groups':{}}}), (200, {'code':1,'value':{'groups':[]}})])
def test_uai5_failed_or_changed_bookshelf_is_not_success(status, payload):
    from app.services.course.english.uai import courses
    context = Mock()
    context.cookies.return_value = [{'name':'jwt','value':'fixture-token'}]
    response = context.request.get.return_value
    response.status = status
    response.body.return_value = json.dumps(payload).encode()
    with pytest.raises(PlatformError): courses(context)
    response.dispose.assert_called_once()


@pytest.mark.parametrize('mode,submit', [('study', False), ('answer', False)])
def test_recording_bypass_requires_explicit_answer_submission(mode, submit):
    from pydantic import ValidationError
    from app.api.v1.english_learning import StartRequest
    with pytest.raises(ValidationError):
        StartRequest(course='c', item='i', mode=mode, submit=submit, recording_bypass=True)


def test_recording_bypass_flag_is_strict_and_off_by_default():
    from pydantic import ValidationError
    from app.api.v1.english_learning import StartRequest
    assert StartRequest(course='c', item='i', mode='answer', submit=True).recording_bypass is False
    with pytest.raises(ValidationError):
        StartRequest(course='c', item='i', mode='answer', submit=True, recording_bypass='true')


@pytest.mark.parametrize('completed', [True, False])
def test_experimental_recording_submission_requires_catalog_confirmation(monkeypatch, completed):
    from contextlib import nullcontext
    from app.services.course.english import forms, welearn_page
    client = Mock()
    client.items.return_value = ({'cid': 'c'}, [{'id': 'leaf', 'progress': 'false'}])
    client.item_complete.return_value = completed
    monkeypatch.setattr(clients, 'Welearn', Mock(return_value=client, BASE='https://welearn.sflep.com'))
    context, page, frame = Mock(), Mock(), Mock()
    context.cookies.return_value = []
    monkeypatch.setattr(clients, 'unipus_browser', lambda *a, **kw: nullcontext((context, page)))
    monkeypatch.setattr(welearn_page, 'wait_content', lambda *a: [frame])
    monkeypatch.setattr(welearn_page, 'return_to_catalog', Mock())
    answer = Mock(return_value={'status':'finished', 'message':'native receipt',
        'counts':{'questions':1,'filled':1,'submitted':1,'recordings_bypassed':2}})
    monkeypatch.setattr(forms, 'answer_page', answer)
    result = clients.welearn_answer('sid=fixture', 'c', 'leaf', 60, Mock(), submit=True, recording_bypass=True)
    assert answer.call_args.kwargs['recording_bypass'] is True
    assert result['status'] == ('finished' if completed else 'needs_manual')
    assert result['counts'].get('platform_completed', 0) == int(completed)
    client.sco.assert_not_called()


def test_list_sessions_is_owner_scoped_and_excludes_credentials(manager):
    alice = manager.connect('alice', 'welearn', 'sid=private')['session_id']
    manager.connect('bob', 'welearn', 'sid=other-private')
    sessions = manager.list_sessions('alice')
    assert len(sessions) == 1 and sessions[0]['session_id'] == alice
    assert sessions[0]['platform'] == 'welearn'
    assert 'credential' not in sessions[0]
    assert 'private' not in json.dumps(sessions)
    sessions[0]['courses'][0]['name'] = 'changed'
    assert manager.sessions[alice]['courses'][0]['name'] != 'changed'
