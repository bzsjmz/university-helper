import json
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.course.english.clients import PlatformError
from app.services.course.english.login_browser import allowed_url, courses_from_links, operate
from app.services.course.english.manager import EnglishManager


@pytest.fixture
def manager(monkeypatch):
    from app.services.course.english import manager as module
    monkeypatch.setattr(module, 'encrypt_str', lambda value: 'encrypted:' + value)
    value = EnglishManager()
    yield value
    value.shutdown()


def fixture_worker(temporary_dir=None):
    code = '''import json,sys
request=json.loads(sys.stdin.buffer.readline())
assert request['password']=='private-password'
print(json.dumps({'ok':True,'status':'verification','image':'fixture','message':'verify'}),flush=True)
request=json.loads(sys.stdin.buffer.readline())
print(json.dumps({'ok':True,'status':'connected','credential':'sid=private-cookie',
 'courses':[{'id':'c','name':'English'}]}),flush=True)
'''
    return subprocess.Popen([sys.executable, '-B', '-c', code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)


def test_real_private_login_pipe_handoff_never_returns_credentials(manager, monkeypatch):
    monkeypatch.setattr(manager, '_spawn', fixture_worker)
    pending = manager.logins.start('alice', 'welearn', 'user', 'private-password')
    lid = pending['login_id']
    assert pending['status'] == 'verification'
    assert 'private-' not in repr(pending) + repr(manager.logins.jobs[lid])
    with pytest.raises(PlatformError):
        manager.logins.exchange('bob', lid)
    assert manager.logins.list('bob') == []
    with pytest.raises(PlatformError):
        manager.logins.cancel('bob', lid)
    connected = manager.logins.exchange('alice', lid)
    assert connected['status'] == 'connected'
    assert 'private-' not in json.dumps(connected)
    session = manager.sessions[connected['session']['session_id']]
    assert session['credential'] == 'encrypted:sid=private-cookie'
    assert not manager.processes
    assert manager.logins.list('alice') == []


def test_pending_login_limit_expiry_and_shutdown_release_processes(manager, monkeypatch):
    monkeypatch.setattr(manager, '_spawn', fixture_worker)
    first = manager.logins.start('alice', 'welearn', 'user', 'private-password')
    with pytest.raises(PlatformError):
        manager.logins.start('alice', 'welearn', 'user', 'private-password')
    second = manager.logins.start('bob', 'welearn', 'user', 'private-password')
    with pytest.raises(PlatformError):
        manager.logins.start('charlie', 'welearn', 'user', 'private-password')
    job = manager.logins.jobs[first['login_id']]
    process = job['process']
    temporary = Path(job['temporary'].name)
    assert temporary.is_dir()
    job['created'] -= 601
    manager.cleanup()
    assert process.poll() is not None
    assert not temporary.exists()
    assert first['login_id'] not in manager.logins.jobs
    process = manager.logins.jobs[second['login_id']]['process']
    manager.shutdown()
    assert process.poll() is not None
    assert not manager.processes and not manager.logins.jobs


def test_cancel_racing_success_cannot_adopt_or_resurrect_login(manager, monkeypatch):
    monkeypatch.setattr(manager, '_spawn', fixture_worker)
    pending = manager.logins.start('alice', 'welearn', 'user', 'private-password')
    entered, released = threading.Event(), threading.Event()
    def late_reply(*args):
        entered.set()
        assert released.wait(2)
        return {'ok': True, 'status': 'connected', 'credential': 'sid=private-cookie', 'courses': []}
    monkeypatch.setattr(manager.logins, '_read', late_reply)
    thread = threading.Thread(target=lambda: manager.logins.exchange('alice', pending['login_id']))
    thread.start()
    assert entered.wait(2)
    manager.logins.cancel('alice', pending['login_id'])
    released.set()
    thread.join(2)
    assert not thread.is_alive()
    assert not manager.sessions and not manager.logins.jobs and not manager.processes


@pytest.mark.parametrize('url', ['http://sso.sflep.com/login', 'https://sflep.com.evil.test/',
    'https://127.0.0.1/', 'https://user@sso.sflep.com/', 'https://sso.sflep.com:8000/',
    'file:///etc/passwd', 'https://sso.unipus.cn/'])
def test_login_navigation_rejects_other_origins(url):
    assert not allowed_url(url, 'welearn')


def test_course_discovery_only_accepts_legacy_catalogues_without_manual_url():
    url = 'https://u.unipus.cn/user/student/mycourse/courseCatalog?courseId=1'
    result = courses_from_links([{'href': 'https://evil.test/', 'name': 'bad'},
        {'href': url, 'name': 'My English'}, {'href': url, 'name': 'duplicate'},
        {'href': url.replace('courseCatalog', 'exam'), 'name': 'exam'}])
    assert result == [{'id': url, 'name': 'My English'}]
    assert allowed_url('https://sso.sflep.com/idsvr/login.html', 'welearn')
    assert allowed_url('https://sso.unipus.cn/sso/login', 'unipus')


def test_manual_verification_cannot_execute_script_or_navigate_arbitrary_url():
    page = Mock()
    for command in ({'action': 'evaluate', 'script': 'fetch(...)'},
                    {'action': 'key', 'key': 'Control+L'},
                    {'action': 'click', 'x': -1, 'y': 3},
                    {'action': 'text', 'text': 'x' * 257}):
        with pytest.raises(PlatformError):
            operate(page, command)
    assert page.mock_calls == []
    operate(page, {'action': 'text', 'text': '123456'})
    page.keyboard.insert_text.assert_called_once_with('123456')


def test_login_api_owner_auth_no_cache_and_validation_never_echoes_password(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import english_learning as routes
    from app.dependencies import get_current_user_id
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    body = {'platform': 'unipus', 'username': 'user', 'password': 'private-password'}
    assert client.post('/logins', json=body).status_code == 401
    app.dependency_overrides[get_current_user_id] = lambda: 'alice'
    monkeypatch.setattr(routes.english_manager.logins, 'start', Mock(return_value={'status': 'verification'}))
    response = client.post('/logins', json=body)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    routes.english_manager.logins.start.assert_called_once_with('alice', 'unipus', 'user', 'private-password')
    for invalid in ({'password': {'value': 'private-password'}}, {'password': 'private-' * 200},
                    {'platform': 'private-platform'}):
        response = client.post('/logins', json={**body, **invalid})
        assert response.status_code == 422
        assert 'private-' not in response.text
    response = client.post('/logins/not-a-login/actions', json={'action': 'text', 'text': 'private-' * 200})
    assert response.status_code == 422 and 'private-' not in response.text
