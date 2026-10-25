"""Offline real-Chromium login handoff; all pages are synthetic, no real account."""
from contextlib import contextmanager, redirect_stdout
import io
import os
import threading
import json
import types
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright as real_playwright
from app.services.course.english.login_browser import login_browser, operate
from app.services.course.english.login_view import blank_png, login_picture


class BrowserLoginTest(unittest.TestCase):
    def run_flow(self, platform, commands=(), *, captcha=False, agreement=False):
        host = 'welearn.sflep.com' if platform == 'welearn' else 'uai.unipus.cn'
        destination = '/student/index.aspx' if platform == 'welearn' else '/home'
        extra = ''
        if captcha:
            extra = '<input required placeholder="图形验证码" style="position:absolute;left:20px;top:105px;width:160px;height:30px">'
        if agreement:
            extra = '<input required type="checkbox" style="position:absolute;left:20px;top:105px;width:20px;height:20px">'
        user_attrs = 'name="username"' if platform == 'welearn' else 'placeholder="请输入手机号、邮箱或用户名"'
        pass_attrs = 'name="password"' if platform == 'welearn' else 'placeholder="请输入密码"'
        login_label = '登录' if platform == 'welearn' else '登 录'
        form = f'''<html><body><form onsubmit="event.preventDefault();document.cookie='sid=fixture-cookie;path=/;Secure';location.href='https://{host}{destination}'">
          <input {user_attrs} style="position:absolute;left:20px;top:20px">
          <input {pass_attrs} type="password" style="position:absolute;left:20px;top:60px">
          {extra}<button style="position:absolute;left:20px;top:150px;width:160px;height:30px">{login_label}</button>
          </form></body></html>'''
        catalog = 'https://u.unipus.cn/user/student/mycourse/courseCatalog?courseId=1'
        portal = f'<a href="{catalog}">My English</a><a href="https://evil.example/">Other site</a>'

        @contextmanager
        def playwright_fixture():
            with real_playwright() as p:
                browser = p.chromium.launch(headless=True, chromium_sandbox=True)
                def new_context(**kwargs):
                    context = browser.new_context(**kwargs)
                    def on_page(page):
                        page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8',
                            body=portal if route.request.url == 'https://' + host + destination else form))
                    context.on('page', on_page)
                    return context
                def launch(**kwargs):
                    self.assertTrue(kwargs['chromium_sandbox'])
                    return types.SimpleNamespace(new_context=new_context, close=browser.close)
                try:
                    yield types.SimpleNamespace(chromium=types.SimpleNamespace(launch=launch))
                finally:
                    browser.close()

        raw_commands = ''.join(json.dumps(command) + '\n' for command in commands).encode()
        fake_stdin = types.SimpleNamespace(buffer=io.BytesIO(raw_commands))
        output = io.StringIO()
        request = {'platform': platform, 'username': 'fixture-user', 'password': 'fixture-password'}
        with patch('playwright.sync_api.sync_playwright', playwright_fixture), \
                patch('app.services.course.english.login_handoff.sys.stdin', fake_stdin), \
                patch('app.services.course.english.login_browser.Welearn.courses',
                      return_value=[{'id': '1', 'name': 'My English'}]), \
                patch('app.services.course.english.uai.courses', side_effect=lambda context, page: ([{'id': 'ai-1', 'name': 'My English'}] if any(c['name'] == 'sid' for c in context.cookies()) else [])), redirect_stdout(output):
            login_browser(request)
        self.assertNotIn('password', request)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[-1]['status'], 'connected')
        self.assertEqual(replies[-1]['courses'][0]['name'], 'My English')
        for reply in replies[:-1]:
            self.assertEqual(reply['status'], 'verification')
            self.assertTrue(reply['image'])
            self.assertNotIn('credential', reply)
            self.assertNotIn('fixture-password', json.dumps(reply))
        self.assertIn('fixture-cookie', replies[-1]['credential'])
        return replies

    def test_blank_capture_is_suppressed_and_explicit_reload_recovers(self):
        with real_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = browser.new_context(viewport={'width': 1000, 'height': 760})
                body = {'value': '<html><body style="background:white"></body></html>'}
                context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=body['value']))
                page = context.new_page()
                page.goto('https://sso.sflep.com/idsvr/login.html')
                self.assertTrue(blank_png(page.screenshot()))
                chosen, picture = login_picture(context, page, 'welearn')
                self.assertIs(chosen, page)
                self.assertIsNone(picture)
                body['value'] = '<html><body><h1>Login</h1><input name="username"></body></html>'
                operate(page, {'action': 'reload', 'platform': 'welearn'})
                chosen, picture = login_picture(context, page, 'welearn')
                self.assertIs(chosen, page)
                self.assertFalse(blank_png(picture))
            finally:
                browser.close()

    def test_blank_popup_does_not_replace_rendered_login_tab(self):
        with real_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = browser.new_context(viewport={'width': 1000, 'height': 760})
                context.route('**/*', lambda route: route.fulfill(content_type='text/html',
                    body='<h1>Login</h1><input>' if '/login' in route.request.url else '<html><body></body></html>'))
                page = context.new_page()
                page.goto('https://sso.sflep.com/login')
                popup = context.new_page()
                popup.goto('https://sso.sflep.com/redirect')
                chosen, picture = login_picture(context, popup, 'welearn')
                self.assertIs(chosen, page)
                self.assertFalse(blank_png(picture))
                popup.set_content('<h1>Verification</h1>')
                chosen, _ = login_picture(context, page, 'welearn')
                self.assertIs(chosen, popup)
            finally:
                browser.close()

    def test_wechat_qr_status_refresh_and_popup_callback_handoff(self):
        holder = {}
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180"><rect width="180" height="180" fill="white"/><path d="M10 10h60v60H10zm90 0h60v60h-60zM10 100h60v60H10z" fill="black"/></svg>'
        @contextmanager
        def fixture():
            with real_playwright() as p:
                browser = p.chromium.launch(headless=True, chromium_sandbox=True)
                def new_context(**kwargs):
                    context = browser.new_context(**kwargs)
                    holder['context'] = context
                    def route(r):
                        if r.request.url.endswith('.svg') or '/connect/qrcode/' in r.request.url:
                            r.fulfill(content_type='image/svg+xml', body=svg)
                        elif 'open.weixin.qq.com' in r.request.url:
                            r.fulfill(content_type='text/html; charset=utf-8', body='<h1>微信登录</h1><img class="js_qrcode_img" src="/connect/qrcode/fixture"><p>微信扫一扫</p>')
                        else:
                            r.fulfill(content_type='text/html; charset=utf-8', body='<h1>Official SSO</h1><a target="_blank" href="https://open.weixin.qq.com/connect/qrconnect"><img src="/wx-fixture.svg"></a>')
                    # Popup first navigations need context routing. Replace only
                    # the fixture's install hook; network policy is tested separately.
                    install_route = context.route
                    context.route = lambda *args, **kwargs: install_route('**/*', route)
                    return context
                try:
                    yield types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda **kwargs:
                        types.SimpleNamespace(new_context=new_context, close=browser.close)))
                finally: browser.close()
        class Commands:
            step = 0
            def readline(self, _):
                context = holder['context']
                page = context.pages[-1]
                self.step += 1
                if self.step == 1:
                    page.locator('p').last.evaluate('(e)=>e.textContent="扫码成功，请在手机上确认"')
                elif self.step == 2:
                    page.locator('p').last.evaluate('(e)=>e.textContent="二维码已过期"')
                elif self.step == 3:
                    return b'{"action":"reload"}\n'
                else:
                    # Complete in the opener while the QR popup still exists.
                    context.add_cookies([{'name':'sid','value':'fixture-cookie','url':'https://welearn.sflep.com'}])
                    context.pages[0].goto('https://welearn.sflep.com/student/index.aspx')
                    page.close()  # Real OAuth callbacks can close the QR popup.
                return b'{"action":"refresh"}\n'
        output = io.StringIO()
        with patch('playwright.sync_api.sync_playwright', fixture), \
                patch('app.services.course.english.login_handoff.sys.stdin', types.SimpleNamespace(buffer=Commands())), \
                patch('app.services.course.english.login_browser.Welearn.courses', return_value=[{'id':'c','name':'English'}]), redirect_stdout(output):
            login_browser({'platform':'welearn','method':'wechat'})
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r.get('qr_status') for r in replies[:-1]], ['waiting','scanned','expired','waiting'])
        for reply in replies[:-1]:
            self.assertEqual(reply['display_mode'], 'qr')
            self.assertNotIn('credential', reply)
            self.assertLess(len(reply['image']), 50000)
        self.assertEqual(replies[-1]['status'], 'connected')
        self.assertIn('fixture-cookie', replies[-1]['credential'])

    def test_oauth_redirect_is_processed_while_no_ui_command_is_available(self):
        from app.services.course.english.login_handoff import read_login_command
        import time
        observed = threading.Event()
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, 'rb', buffering=0)
        with real_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = browser.new_context()
                def route(r):
                    if '/callback' in r.request.url:
                        observed.set()
                        r.fulfill(content_type='text/html', body='<h1>Authorized</h1>')
                    else:
                        r.fulfill(content_type='text/html', body='<script>setTimeout(()=>location.href="/callback", 400)</script>')
                context.route('**/*', route)
                page = context.new_page()
                page.goto('https://sso.sflep.com/login')
                def writer():
                    # Only send the next command after the browser's routed callback
                    # has actually executed; a blocking stdin implementation fails.
                    success = observed.wait(4)
                    os.write(write_fd, b'{"action":"refresh"}\n' if success else b'')
                    os.close(write_fd)
                thread = threading.Thread(target=writer)
                thread.start()
                with patch('app.services.course.english.login_handoff.sys.stdin', types.SimpleNamespace(buffer=stream)):
                    command = read_login_command(page, time.monotonic() + 6)
                thread.join(5)
                self.assertTrue(observed.is_set())
                self.assertEqual(json.loads(command), {'action': 'refresh'})
                self.assertFalse(thread.is_alive())
            finally:
                stream.close()
                browser.close()

    def test_blank_sso_callback_reopens_original_welearn_with_same_context(self):
        from app.services.course.english.login_handoff import WechatHandoff
        with real_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = browser.new_context(viewport={'width':1000,'height':760})
                def route(r):
                    if '/signin-weixin' in r.request.url:
                        r.fulfill(content_type='text/html', body='<html><body></body></html>')
                    else:
                        self.assertTrue(any(c['name']=='sso-fixture' for c in context.cookies()))
                        r.fulfill(content_type='text/html', body='<h1>Course portal</h1>')
                context.route('**/*', route)
                page = context.new_page()
                context.add_cookies([{'name':'sso-fixture','value':'private-fixture','url':'https://sso.sflep.com'}])
                page.goto('https://sso.sflep.com/idsvr/signin-weixin?code=fixture')
                handoff = WechatHandoff()
                handoff.observe(page, b'qr', 'waiting')
                self.assertFalse(handoff.observe(page, None, None))
                self.assertFalse(handoff.observe(page, None, None))
                handoff.pending_since -= 6
                self.assertTrue(handoff.observe(page, None, None))
                page = handoff.resume(context, page)
                self.assertIn('welearn.sflep.com/user/prelogin.aspx', page.url)
                self.assertEqual(page.locator('h1').inner_text(), 'Course portal')
                self.assertFalse(handoff.observe(page, None, None))
            finally: browser.close()

    def test_password_without_verification_connects_directly(self):
        self.assertEqual(len(self.run_flow('welearn')), 1)

    def test_user_enters_captcha_in_same_browser_without_exporting_state(self):
        replies = self.run_flow('welearn', [
            {'action': 'click', 'x': 50, 'y': 120},
            {'action': 'text', 'text': '1234'}, {'action': 'key', 'key': 'Enter'},
        ], captcha=True)
        self.assertEqual(len(replies), 4)

    def test_unipus_ai_agreement_is_explicit_and_course_list_is_read_after_sso(self):
        replies = self.run_flow('unipus_ai', [
            {'action': 'click', 'x': 30, 'y': 115}, {'action': 'click', 'x': 90, 'y': 165},
        ], agreement=True)
        self.assertEqual(len(replies), 3)
        self.assertEqual(replies[-1]['courses'][0]['id'], 'ai-1')
        self.assertEqual(json.loads(replies[-1]['credential'])['cookies'][0]['value'], 'fixture-cookie')


if __name__ == '__main__':
    unittest.main(verbosity=2)
