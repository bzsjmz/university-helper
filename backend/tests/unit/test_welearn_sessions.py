import json
from unittest.mock import Mock

import pytest
import requests

from app.services.course.english.clients import PlatformError, Welearn
from app.services.course.english.login_browser import authenticated_result


def cookie_state(**changes):
    cookie = {'name': 'sid', 'value': 'private', 'domain': '.sflep.com', 'path': '/',
              'expires': 2000000000, 'secure': True, 'httpOnly': True, 'sameSite': 'Strict'}
    cookie.update(changes)
    return json.dumps({'welearn_session': 1, 'cookies': [cookie]})


def test_cookie_metadata_and_same_name_scopes_survive_restart():
    client = Welearn(cookie_state())
    client.session.cookies.set('sid', 'other-path', domain='welearn.sflep.com', path='/student', secure=True)
    restored = Welearn(client.export_credential())
    assert [(cookie.domain, cookie.path, cookie.value) for cookie in restored.session.cookies] == [
        ('.sflep.com', '/', 'private'), ('welearn.sflep.com', '/student', 'other-path')]
    saved = next(iter(restored.session.cookies))
    assert saved.expires == 2000000000 and saved.secure
    assert saved.has_nonstandard_attr('HttpOnly')
    assert saved.get_nonstandard_attr('SameSite') == 'Strict'
    request = restored.session.prepare_request(requests.Request('GET', 'https://welearn.sflep.com/student/'))
    assert request.headers['Cookie'] == 'sid=other-path; sid=private'
    other = restored.session.prepare_request(requests.Request('GET', 'https://example.com/'))
    assert 'Cookie' not in other.headers


@pytest.mark.parametrize('expires', [-1, None, 0, 1])
def test_session_cookies_remain_usable_but_expired_cookies_are_not_resurrected(expires):
    client = Welearn(cookie_state(expires=expires))
    restored = Welearn(client.export_credential())
    request = restored.session.prepare_request(requests.Request('GET', Welearn.BASE))
    assert ('Cookie' in request.headers) is (expires in (-1, None))


@pytest.mark.parametrize('changes', [
    {'domain': 'welearn.sflep.com.evil.test'}, {'domain': 'sso.sflep.com'},
    {'path': 'relative'}, {'name': 'sid\r\ninjected'}, {'value': 'private; injected=1'},
    {'expires': 'tomorrow'}, {'secure': 'false'}, {'expires': float('nan')},
])
def test_structured_cookie_rejects_other_hosts_and_invalid_fields(changes):
    with pytest.raises(PlatformError, match='凭据格式无效'):
        Welearn(cookie_state(**changes))


def test_legacy_cookie_is_still_accepted_and_upgraded():
    restored = Welearn(Welearn('sid=private; token=a=b').export_credential())
    assert restored.session.cookies.get('sid') == 'private'
    assert restored.session.cookies.get('token') == 'a=b'


def test_browser_login_exports_metadata_and_cookies_rotated_by_course_read(monkeypatch):
    def courses(client):
        client.session.cookies.set('sid', 'renewed', domain='.sflep.com', path='/', secure=True, expires=2000000100)
        return [{'id': 'course', 'name': 'English'}]
    monkeypatch.setattr(Welearn, 'courses', courses)
    context = Mock()
    context.cookies.return_value = json.loads(cookie_state())['cookies']
    result = authenticated_result(context, Mock(url=Welearn.BASE + '/student/index.aspx'), 'welearn')
    saved = json.loads(result['credential'])['cookies'][0]
    assert saved['value'] == 'renewed' and saved['expires'] == 2000000100 and saved['secure']


@pytest.mark.parametrize('body,message', [
    ('<form action="https://sso.sflep.com/login"><input type="password"></form>', '登录已失效或需要验证'),
    ('<html>maintenance</html>', '无法识别的课程数据'),
    ('[1, 2]', '无法识别的课程数据'),
])
def test_login_html_and_unknown_responses_have_distinct_safe_errors(body, message):
    client = Welearn('sid=private')
    response = Mock(status_code=200)
    response.iter_content.return_value = [body.encode()]
    client.session.request = Mock(return_value=response)
    with pytest.raises(PlatformError, match=message) as error:
        client.courses()
    assert 'private' not in str(error.value) and body not in str(error.value)
    response.close.assert_called_once()
