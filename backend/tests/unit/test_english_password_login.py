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


def test_reload_stays_on_platform_and_uses_get_instead_of_replaying_post():
    page = Mock(url='https://sso.sflep.com/login')
    operate(page, {'action': 'reload', 'platform': 'welearn'})
    page.goto.assert_called_once_with(page.url, wait_until='domcontentloaded')
    page.reload.assert_not_called()
    page.url = 'https://untrusted.example/'
    with pytest.raises(PlatformError):
        operate(page, {'action': 'reload', 'platform': 'welearn'})


def test_wechat_entry_requires_auth_and_has_no_password_or_platform_input(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import english_learning as routes
    from app.dependencies import get_current_user_id
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    assert client.post('/logins/wechat').status_code == 401
    app.dependency_overrides[get_current_user_id] = lambda: 'alice'
    monkeypatch.setattr(routes.english_manager.logins, 'start_wechat', Mock(return_value={'status': 'verification'}))
    response = client.post('/logins/wechat')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    routes.english_manager.logins.start_wechat.assert_called_once_with('alice')


def test_wechat_hosts_are_exact_scoped_and_cannot_be_exported_as_platform_credentials():
    from app.services.course.english.login_browser import allowed_login_url, allowed_login_request
    for host in ('open.weixin.qq.com.evil.test', 'localhost.weixin.qq.com', 'evil.qq.com', '127.0.0.1'):
        assert not allowed_login_url('https://' + host, 'welearn', 'wechat')
    url = 'https://open.weixin.qq.com/connect/qrconnect'
    assert allowed_login_url(url, 'welearn', 'wechat')
    assert not allowed_url(url, 'welearn')
    assert not allowed_login_url(url, 'welearn', 'password')
    assert not allowed_login_url(url, 'unipus', 'wechat')
    for host in ('res.wx.qq.com', 'lp.open.weixin.qq.com'):
        req = Mock(url='https://' + host + '/asset', method='GET')
        req.is_navigation_request.return_value = False
        assert allowed_login_request(req, 'welearn', 'wechat')
        assert not allowed_login_request(req, 'welearn', 'password')
        req.method = 'POST'
        assert not allowed_login_request(req, 'welearn', 'wechat')
        req.method = 'GET'
        req.is_navigation_request.return_value = True
        assert not allowed_login_request(req, 'welearn', 'wechat')


def test_wechat_uses_same_owner_limit_cleanup_and_never_sends_credentials(manager, monkeypatch):
    def worker(temporary_dir=None):
        code = '''import json,sys
request=json.loads(sys.stdin.buffer.readline())
assert request=={'action':'login','platform':'welearn','method':'wechat'}
print(json.dumps({'ok':True,'status':'verification','image':'fixture','image_type':'png',
 'display_mode':'qr','qr_status':'waiting'}),flush=True)
sys.stdin.buffer.readline()
'''
        return subprocess.Popen([sys.executable, '-B', '-c', code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    monkeypatch.setattr(manager, '_spawn', worker)
    result = manager.logins.start_wechat('alice')
    assert result['display_mode'] == 'qr' and result['qr_status'] == 'waiting'
    lid = result['login_id']
    with pytest.raises(PlatformError): manager.logins.start('alice', 'welearn', 'user', 'password')
    with pytest.raises(PlatformError): manager.logins.exchange('bob', lid)
    manager.logins.cancel('alice', lid)
    assert not manager.processes and not manager.logins.jobs


def test_wechat_blank_callback_resumes_fixed_course_entry_once_without_replaying_code():
    from app.services.course.english.login_handoff import WechatHandoff
    from app.services.course.english.login_browser import LOGIN_URLS
    handoff = WechatHandoff()
    page = Mock(url='https://sso.sflep.com/idsvr/signin-weixin?code=private-code')
    page.is_closed.return_value = False
    assert not handoff.observe(page, None, None)  # Never resume an initial blank login.
    assert not handoff.observe(page, b'qr', 'waiting')
    assert not handoff.observe(page, None, None)
    assert handoff.observe(page, None, None)
    assert handoff.resume(Mock(), page) is page
    page.goto.assert_called_once_with(LOGIN_URLS['welearn'], wait_until='domcontentloaded', timeout=20000)
    assert not handoff.observe(page, None, None)
    assert 'private-code' not in str(page.goto.call_args)


def test_wechat_handoff_does_not_interfere_with_binding_or_password_forms():
    from app.services.course.english.login_handoff import WechatHandoff
    handoff = WechatHandoff()
    page = Mock(url='https://sso.sflep.com/idsvr/bind.html')
    handoff.observe(page, b'qr', 'waiting')
    page.locator.return_value.count.return_value = 0
    assert not handoff.observe(page, b'form', None)
    page.url = 'https://sso.sflep.com/idsvr/login.html'
    page.locator.return_value.count.return_value = 2
    assert not handoff.observe(page, b'form', None)
