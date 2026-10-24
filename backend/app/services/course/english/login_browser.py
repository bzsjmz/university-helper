"""Official password forms and human-operated verification, in an isolated worker.

Username/password selectors informed by the existing licensed reference projects;
authentication itself runs in the platform's page, never a forged token endpoint.
"""
import base64
import json
import sys
import time
from urllib.parse import urlsplit

from .clients import PlatformError, Welearn, course_url, storage_state

LOGIN_URLS = {
    'welearn': 'https://welearn.sflep.com/user/prelogin.aspx?loginret=https%3A%2F%2Fwelearn.sflep.com%2Fstudent%2Findex.aspx',
    'unipus': 'https://u.unipus.cn/user/student',
}
WIDTH, HEIGHT = 1000, 760


def allowed_url(url, platform):
    parsed = urlsplit(url)
    domain = 'sflep.com' if platform == 'welearn' else 'unipus.cn'
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and (host == domain or host.endswith('.' + domain))
            and parsed.port in (None, 443) and not parsed.username and not parsed.password)


def courses_from_links(links):
    courses, seen = [], set()
    for link in links[:1000]:
        try:
            url = course_url(link['href'])
        except (PlatformError, KeyError, TypeError, ValueError):
            continue
        if url not in seen:
            seen.add(url)
            courses.append({'id': url, 'name': str(link.get('name') or f'U校园课程 {len(courses)+1}')[:256]})
    return courses[:200]


def authenticated_result(context, page, platform):
    if not allowed_url(page.url, platform):
        return None
    parsed = urlsplit(page.url)
    if platform == 'welearn':
        if parsed.hostname != 'welearn.sflep.com' or not parsed.path.startswith('/student/'):
            return None
        cookies = context.cookies(['https://welearn.sflep.com'])
        credential = '; '.join(c['name'] + '=' + c['value'] for c in cookies)
        if not credential:
            return None
        client = Welearn(credential)
        try:
            courses = client.courses()
        except PlatformError:
            return None
        finally:
            client.session.close()
        return {'credential': credential, 'courses': courses}
    if parsed.hostname != 'u.unipus.cn':
        return None
    if page.locator('[name="password"]:visible').count():
        return None
    links = page.locator('a[href]').evaluate_all('''es => es.slice(0,1000).map(e => ({
        href:e.href,name:(e.innerText || e.title || e.querySelector('img')?.alt || '').trim().slice(0,256)}))''')
    if parsed.path == '/user/student/mycourse/courseCatalog':
        links.insert(0, {'href': page.url, 'name': page.title() or 'U校园课程'})
    courses = courses_from_links(links)
    if not courses:
        return None  # The user can click their course card in the same official page.
    state = context.storage_state()
    # Export only the existing adapter's exact platform scope, never SSO/other-site data.
    state['cookies'] = [c for c in state['cookies'] if c['domain'].lstrip('.') == 'unipus.cn'
                        or c['domain'].endswith('.unipus.cn')]
    state['origins'] = [o for o in state['origins'] if o['origin'] in
                        ('https://u.unipus.cn', 'https://ucontent.unipus.cn')]
    credential = json.dumps(storage_state(json.dumps(state)))
    return {'credential': credential, 'courses': courses}


def operate(page, command):
    action = command['action']
    if action in ('click', 'drag'):
        x, y = command['x'], command['y']
        if not 0 <= x < WIDTH or not 0 <= y < HEIGHT:
            raise PlatformError('操作位置无效')
        if action == 'click':
            page.mouse.click(x, y)
        else:
            end_x, end_y = command['end_x'], command['end_y']
            if not 0 <= end_x < WIDTH or not 0 <= end_y < HEIGHT:
                raise PlatformError('操作位置无效')
            page.mouse.move(x, y)
            page.mouse.down()
            try:
                page.mouse.move(end_x, end_y, steps=16)
            finally:
                page.mouse.up()
    elif action == 'text':
        text = command['text']
        if not isinstance(text, str) or not 1 <= len(text) <= 256:
            raise PlatformError('输入文字过长或为空')
        page.keyboard.insert_text(text)
    elif action == 'key':
        key = command['key']
        if key not in ('Tab', 'Backspace', 'Delete', 'Enter', 'Escape', 'ArrowLeft', 'ArrowRight', 'Space'):
            raise PlatformError('按键无效')
        page.keyboard.press(key)
    elif action == 'scroll':
        delta = command['delta_y']
        if type(delta) is not int or not -600 <= delta <= 600:
            raise PlatformError('滚动距离无效')
        page.mouse.wheel(0, delta)
    elif action != 'refresh':
        raise PlatformError('操作无效')


def login_browser(request):
    from playwright.sync_api import Error, sync_playwright
    platform = request['platform']
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(viewport={'width': WIDTH, 'height': HEIGHT},
                                          accept_downloads=False, service_workers='block')
            context.route('**/*', lambda route: route.continue_()
                          if allowed_url(route.request.url, platform) else route.abort())
            page = context.new_page()
            page.set_default_timeout(5000)
            page.set_default_navigation_timeout(20000)
            page.goto(LOGIN_URLS[platform], wait_until='domcontentloaded')
            page.locator('[name="username"]:visible').fill(request.pop('username'))
            page.locator('[name="password"]:visible').fill(request.pop('password'))
            # Leave agreements and verification to the user. A prefilled official
            # page is shown when these are required, without exporting cookies.
            checkboxes = page.locator('input[type="checkbox"]:visible').all()
            requires_agreement = any(not c.is_checked() and c.get_attribute('name') != 'rememberMe'
                                     for c in checkboxes)
            requires_code = any(not c.input_value() for c in
                                page.locator('input[placeholder*="验证码"]:visible').all())
            if not requires_agreement and not requires_code:
                buttons = page.get_by_role('button', name='登录', exact=True).all()
                visible = [b for b in buttons if b.is_visible() and b.is_enabled()]
                if len(visible) == 1:
                    visible[0].click()
            page.wait_for_timeout(1500)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                # Redirects can replace a document between reading the URL and
                # taking its picture. Retry only reads, never the user's action.
                for attempt in range(3):
                    try:
                        candidates = [p for p in context.pages if not p.is_closed() and allowed_url(p.url, platform)]
                        if candidates:
                            page = candidates[-1]
                        result = authenticated_result(context, page, platform)
                        image = None if result is not None else base64.b64encode(
                            page.screenshot(type='jpeg', quality=70, timeout=5000)).decode()
                        break
                    except Error:
                        if attempt == 2:
                            raise PlatformError('登录页面暂未就绪，请重新登录') from None
                        page.wait_for_timeout(400)
                if result is not None:
                    print(json.dumps({'ok': True, 'status': 'connected', **result}), flush=True)
                    return
                message = '请在下方官网画面中完成验证码或协议确认，再点击画面中的登录按钮。'
                if platform == 'unipus' and urlsplit(page.url).hostname == 'u.unipus.cn':
                    message = '请在下方官网画面中点选课程，系统会自动读取课程目录。'
                print(json.dumps({'ok': True, 'status': 'verification', 'image': image,
                                  'message': message}), flush=True)
                raw = sys.stdin.buffer.readline(4097)
                if not raw or len(raw) > 4096:
                    return
                command = json.loads(raw)
                operate(page, command)
                page.wait_for_timeout(500)
        finally:
            browser.close()
