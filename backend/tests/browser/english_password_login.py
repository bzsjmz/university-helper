"""Offline real-Chromium login handoff; all pages are synthetic, no real account."""
from contextlib import contextmanager, redirect_stdout
import io
import json
import types
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright as real_playwright
from app.services.course.english.login_browser import login_browser, operate
from app.services.course.english.login_view import blank_png, login_picture


class BrowserLoginTest(unittest.TestCase):
    def run_flow(self, platform, commands=(), *, captcha=False, agreement=False):
        host = 'welearn.sflep.com' if platform == 'welearn' else 'u.unipus.cn'
        destination = '/student/index.aspx' if platform == 'welearn' else '/user/student/mycourse/index'
        extra = ''
        if captcha:
            extra = '<input required placeholder="图形验证码" style="position:absolute;left:20px;top:105px;width:160px;height:30px">'
        if agreement:
            extra = '<input required type="checkbox" style="position:absolute;left:20px;top:105px;width:20px;height:20px">'
        form = f'''<html><body><form onsubmit="event.preventDefault();document.cookie='sid=fixture-cookie;path=/;Secure';location.href='{destination}'">
          <input name="username" style="position:absolute;left:20px;top:20px">
          <input name="password" type="password" style="position:absolute;left:20px;top:60px">
          {extra}<button style="position:absolute;left:20px;top:150px;width:160px;height:30px">登录</button>
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
                patch('app.services.course.english.login_browser.sys.stdin', fake_stdin), \
                patch('app.services.course.english.login_browser.Welearn.courses',
                      return_value=[{'id': '1', 'name': 'My English'}]), redirect_stdout(output):
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

    def test_password_without_verification_connects_directly(self):
        self.assertEqual(len(self.run_flow('welearn')), 1)

    def test_user_enters_captcha_in_same_browser_without_exporting_state(self):
        replies = self.run_flow('welearn', [
            {'action': 'click', 'x': 50, 'y': 120},
            {'action': 'text', 'text': '1234'}, {'action': 'key', 'key': 'Enter'},
        ], captcha=True)
        self.assertEqual(len(replies), 4)

    def test_unipus_agreement_is_explicit_and_course_url_is_discovered(self):
        replies = self.run_flow('unipus', [
            {'action': 'click', 'x': 30, 'y': 115}, {'action': 'click', 'x': 90, 'y': 165},
        ], agreement=True)
        self.assertEqual(len(replies), 3)
        self.assertTrue(replies[-1]['courses'][0]['id'].startswith('https://u.unipus.cn/'))
        self.assertEqual(json.loads(replies[-1]['credential'])['cookies'][0]['value'], 'fixture-cookie')


if __name__ == '__main__':
    unittest.main(verbosity=2)
