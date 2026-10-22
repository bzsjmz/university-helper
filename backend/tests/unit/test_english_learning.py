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
    routes.english_manager.start.assert_called_once_with('alice', 's', 'c', 'i', 60, 'study', False, False)
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
