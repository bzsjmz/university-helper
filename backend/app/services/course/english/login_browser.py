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
from .login_view import login_picture

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


def allowed_login_url(url, platform, method='password'):
    if allowed_url(url, platform):
        return True
    parsed = urlsplit(url)
    return (platform == 'welearn' and method == 'wechat' and parsed.scheme == 'https'
            and parsed.hostname == 'open.weixin.qq.com' and parsed.port in (None, 443)
            and not parsed.username and not parsed.password)


def allowed_login_request(request, platform, method):
    if allowed_login_url(request.url, platform, method):
        return True
    parsed = urlsplit(request.url)
    # Exact official QR asset/polling hosts only. Desktop WeChat's localhost
    # helper and unrelated QQ/WeChat sites remain unavailable to this browser.
    return (platform == 'welearn' and method == 'wechat' and parsed.scheme == 'https'
            and parsed.hostname in ('res.wx.qq.com', 'lp.open.weixin.qq.com')
            and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and request.method == 'GET' and not request.is_navigation_request())


def wechat_picture(page):
    for frame in page.frames:
        if urlsplit(frame.url).hostname != 'open.weixin.qq.com':
            continue
        text = frame.locator('body').inner_text(timeout=2000)[:10000]
        status = 'expired' if any(t in text for t in ('已失效', '已过期')) else (
            'scanned' if any(t in text for t in ('扫描成功', '扫码成功', '请在微信中确认', '请在手机上确认')) else 'waiting')
        for image in frame.locator('img.js_qrcode_img, img.qrcode').all()[:8]:
            if image.is_visible() and image.evaluate('(e)=>e.complete && e.naturalWidth >= 120'):
                return image.screenshot(type='png', timeout=3000), status
    return None, None


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
    elif action == 'reload':
        # GET the current official URL; never replay a password form POST.
        if not allowed_login_url(page.url, command.get('platform', 'welearn'), command.get('method', 'password')):
            raise PlatformError('登录页面地址异常，请取消后重新登录')
        page.goto(page.url, wait_until='domcontentloaded')
    elif action == 'top':
        page.evaluate('window.scrollTo(0, 0)')
    elif action != 'refresh':
        raise PlatformError('操作无效')


def login_browser(request):
    from playwright.sync_api import Error, sync_playwright
    platform = request['platform']
    method = request.get('method', 'password')
    if method not in ('password', 'wechat') or (method == 'wechat' and platform != 'welearn'):
        raise PlatformError('不支持此登录方式')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(viewport={'width': WIDTH, 'height': HEIGHT},
                                          accept_downloads=False, service_workers='block')
            context.route('**/*', lambda route: route.continue_()
                          if allowed_login_request(route.request, platform, method) else route.abort())
            page = context.new_page()
            page.set_default_timeout(5000)
            page.set_default_navigation_timeout(20000)
            page.goto(LOGIN_URLS[platform], wait_until='domcontentloaded')
            if method == 'wechat':
                # Click the real SSO entry so its OAuth state and redirect remain
                # paired with this private context; never construct our own token URL.
                page.locator('img[src*="/wx-"]').click()
            else:
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
                        result = None
                        for candidate in reversed(context.pages[-4:]):
                            if not candidate.is_closed():
                                result = authenticated_result(context, candidate, platform)
                                if result is not None:
                                    break
                        picture = None
                        if result is None:
                            page, picture = login_picture(context, page, platform, method)
                        display_mode, qr_status = 'page', None
                        if method == 'wechat' and result is None:
                            qr_picture, qr_status = wechat_picture(page)
                            if qr_picture:
                                picture, display_mode = qr_picture, 'qr'
                        image = base64.b64encode(picture).decode() if picture and result is None else ''
                        break
                    except Error:
                        if attempt == 2:
                            raise PlatformError('登录页面暂未就绪，请重新登录') from None
                        page.wait_for_timeout(400)
                if result is not None:
                    print(json.dumps({'ok': True, 'status': 'connected', **result}), flush=True)
                    return
                message = '请按下方平台官网提示完成登录，登录成功后自动读取课程。'
                if method == 'wechat':
                    message = {'waiting': '请用已绑定 WE Learn 账号的微信扫描二维码，并在手机上确认登录。',
                               'scanned': '扫码成功，请在手机微信中确认登录。',
                               'expired': '二维码已过期，请点击“刷新二维码”重新获取。'}.get(qr_status,
                        '正在等待微信授权返回。未绑定的微信请先到 WE Learn 官网完成绑定。')
                if platform == 'unipus' and urlsplit(page.url).hostname == 'u.unipus.cn':
                    message = '请在下方官网画面中点选课程，系统会自动读取课程目录。'
                if not image:
                    message = '平台登录页面暂时空白，正在等待加载。可点击“重新加载页面”，或取消后重试登录。'
                print(json.dumps({'ok': True, 'status': 'verification', 'image': image,
                                  'image_type': 'png', 'display_mode': display_mode, 'qr_status': qr_status, 'view_state': 'ready' if image else 'blank',
                                  'message': message}), flush=True)
                raw = sys.stdin.buffer.readline(4097)
                if not raw or len(raw) > 4096:
                    return
                command = json.loads(raw)
                command['platform'] = platform
                command['method'] = method
                operate(page, command)
                page.wait_for_timeout(500)
        finally:
            browser.close()
