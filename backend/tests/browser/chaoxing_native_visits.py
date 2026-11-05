"""Sandboxed browser fixture: all requests fulfilled offline, no real account."""
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright

from app.services.course.chaoxing.native_visit_protocol import NativeVisitError
from app.services.course.chaoxing.native_visit_worker import NativeChapterBrowser, install_network_policy

COURSE = {'courseId': '1', 'clazzId': '2', 'cpi': '3'}
ENTRY = 'https://mooc1.chaoxing.com/mooc-ans/visit/stucoursemiddle?courseid=1&clazzid=2&cpi=3'
STUDY = 'https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=1&clazzid=2&cpi=3&chapterId=10'
payload = {'course': COURSE, 'entry_url': ENTRY, 'points': [{'id': '10'}, {'id': '20'}]}
seen = []
mode = {'value': 'normal'}


def fixture(route):
    url = urlsplit(route.request.url)
    seen.append((url.hostname, url.path, route.request.method))
    if url.path.endswith('stucoursemiddle'):
        body = f'<button>章节</button><div id="cur10"><a class="clicktitle" href="{STUDY}">Chapter</a></div>'
    elif url.path.endswith('studentstudy'):
        point = parse_qs(url.query)['chapterId'][0]
        if mode['value'] == 'manual':
            body = '<div>请输入验证码</div>'
        elif mode['value'] == 'no_log':
            body = '<div>Chapter</div>'
        else:
            # The native page initiates its log script. Python never replays it.
            body = f'''<div>Chapter {point}</div>
<script src="https://fystat-ans.chaoxing.com/log/setlog?courseId=1&classId=2&chapterId={point}"></script>
<script>fetch('https://other.example/private').catch(()=>{{}});
fetch('https://mooc1.chaoxing.com/work/phone/doHomeWork',{{method:'POST',body:'answer=blocked'}}).catch(()=>{{}});
fetch('https://mooc1.chaoxing.com/exam-ans/exam/start').catch(()=>{{}});</script>'''
    elif url.path == '/log/setlog':
        route.fulfill(status=200, content_type='application/javascript', body='void 0;')
        return
    else:
        raise AssertionError('Unexpected fixture resource')
    route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
        args=['--disable-features=BackForwardCache'])
    try:
        context = browser.new_context(service_workers='block', accept_downloads=False)
        context.route('**/*', fixture)
        install_network_policy(context)
        native = NativeChapterBrowser(context, payload, dwell_seconds=0.1)
        native.bootstrap()
        assert native.visit('10') == {'state': 'visited', 'log_observed': True}
        assert native.visit('20')['log_observed'] is True
        assert native.log_receipts == 1  # A diagnostic receipt, not a platform count.
        assert len([item for item in seen if item[1] == '/log/setlog']) == 2
        assert all(host.endswith('.chaoxing.com') and method == 'GET' for host, _, method in seen)
        assert not any('/exam' in path or '/work' in path for _, path, _ in seen)
        try:
            native.visit('999')
            raise AssertionError('Wrong course point accepted')
        except NativeVisitError as exc:
            assert str(exc) == 'outside_course'
        for value, expected in [('manual', 'manual_required'), ('no_log', 'visit_log_unconfirmed')]:
            mode['value'] = value
            try:
                native.visit('10')
                raise AssertionError('Unconfirmed visit accepted')
            except NativeVisitError as exc:
                assert str(exc) == expected
        context.close()
    finally:
        browser.close()
print('Native chapter browser fixture passed: normal navigation, native logs, scope and manual gates')
