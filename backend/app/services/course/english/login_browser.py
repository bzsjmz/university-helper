"""Official password forms and human-operated verification, in an isolated worker.

Username/password selectors informed by the existing licensed reference projects;
authentication itself runs in the platform's page, never a forged token endpoint.
"""
import base64
import json
import re
import time
from urllib.parse import urlsplit

import requests

from .clients import PlatformError, Welearn, storage_state
from .login_view import login_picture
from .login_handoff import WechatHandoff, read_login_command

LOGIN_URLS = {
    'welearn': 'https://welearn.sflep.com/user/prelogin.aspx?loginret=https%3A%2F%2Fwelearn.sflep.com%2Fstudent%2Findex.aspx',
    'unipus_ai': 'https://uai.unipus.cn/portal/sso/login',
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
    if platform == 'unipus_ai':
        from .uai import decorative_portal_resource
        if decorative_portal_resource(request):
            return False
    if allowed_login_url(request.url, platform, method):
        return True
    parsed = urlsplit(request.url)
    if (platform == 'unipus_ai' and parsed.scheme == 'https'
            and parsed.hostname in ('static.geetest.com', 'api.geetest.com', 'apiv6.geetest.com', 'static.geevisit.com', 'api.geevisit.com')
            and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and request.method == 'GET' and not request.is_navigation_request()):
        return True
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
        except (PlatformError, requests.RequestException):
            return None
        finally:
            client.session.close()
        return {'credential': credential, 'courses': courses}
    if platform != 'unipus_ai' or parsed.hostname not in ('ucloud.unipus.cn', 'uai.unipus.cn'):
        return None
    from .uai import courses as uai_courses
    listed = uai_courses(context, page)
    if not listed:
        return None
    state = context.storage_state()
    state['cookies'] = [c for c in state['cookies'] if c['domain'].lstrip('.') == 'unipus.cn'
                        or c['domain'].endswith('.unipus.cn')]
    state['origins'] = [o for o in state['origins'] if o['origin'] in
                        ('https://ucloud.unipus.cn', 'https://uai.unipus.cn', 'https://ucontent.unipus.cn')]
    return {'credential': json.dumps(storage_state(json.dumps(state))), 'courses': listed}


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
    from playwright.sync_api import Error, TimeoutError
    try:
        _login_browser(request)
    except TimeoutError:
        print(json.dumps({'ok': False, 'error_code': 'platform_timeout'}), flush=True)
    except Error:
        print(json.dumps({'ok': False, 'error_code': 'browser_navigation'}), flush=True)


def _login_browser(request):
    from playwright.sync_api import Error, sync_playwright
    platform = request['platform']
    method = request.get('method', 'password')
    handoff = WechatHandoff()
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
                page.locator('[name="username"]:visible, input[placeholder*="手机号"]:visible, input[placeholder*="用户名"]:visible').fill(request.pop('username'))
                page.locator('[name="password"]:visible, input[type="password"]:visible').fill(request.pop('password'))
                # Leave agreements and verification to the user. A prefilled official
                # page is shown when these are required, without exporting cookies.
                checkboxes = page.locator('input[type="checkbox"]:visible').all()
                requires_agreement = any(not c.is_checked() and c.get_attribute('name') != 'rememberMe'
                                         for c in checkboxes)
                requires_code = any(not c.input_value() for c in
                                    page.locator('input[placeholder*="验证码"]:visible').all())
                if not requires_agreement and not requires_code:
                    buttons = page.get_by_role('button', name=re.compile(r'^登\s*录$')).all()
                    visible = [b for b in buttons if b.is_visible() and b.is_enabled()]
                    if len(visible) == 1:
                        visible[0].click()
            live = [tab for tab in context.pages if not tab.is_closed()]
            if live:
                page = live[-1]
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
                        returning = method == 'wechat' and handoff.seen_qr and qr_status is None and picture is None
                        if method == 'wechat' and result is None and handoff.observe(page, picture, qr_status):
                            page = handoff.resume(context, page)
                            picture, returning = None, True
                        image = base64.b64encode(picture).decode() if picture and result is None else ''
                        break
                    except Error:
                        # OAuth can close/replace a tab while captures run. Keep the
                        # private context and wait for its surviving official tab.
                        live = [tab for tab in context.pages if not tab.is_closed()
                                and allowed_login_url(tab.url, platform, method)]
                        if not live:
                            raise PlatformError('登录浏览器已关闭，请重新登录') from None
                        page = live[-1]
                        if attempt == 2:
                            result, image, display_mode, qr_status = None, '', 'page', None
                            returning = method == 'wechat' and handoff.seen_qr
                            break
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
                if platform == 'unipus_ai' and urlsplit(page.url).hostname in ('ucloud.unipus.cn', 'uai.unipus.cn'):
                    message = '正在连接 U校园 AI版并读取已开通的教程。'
                if returning:
                    message = '微信授权已离开扫码页，正在连接 WE Learn 并读取课程，请稍候。'
                elif not image:
                    message = '平台登录页面暂时空白，正在等待加载。可点击“重新加载页面”，或取消后重试登录。'
                print(json.dumps({'ok': True, 'status': 'verification', 'image': image,
                                  'image_type': 'png', 'display_mode': display_mode, 'qr_status': qr_status, 'view_state': 'loading' if returning else ('ready' if image else 'blank'),
                                  'message': message}), flush=True)
                raw = read_login_command(page, deadline)
                if not raw or len(raw) > 4096:
                    return
                command = json.loads(raw)
                command['platform'] = platform
                command['method'] = method
                try:
                    operate(page, command)
                    live = [tab for tab in context.pages if not tab.is_closed()]
                    if live:
                        page = live[-1]
                    page.wait_for_timeout(500)
                except Error:
                    # An action may complete by closing its own callback tab.
                    # Never replay it; inspect the surviving tab on the next read.
                    live = [tab for tab in context.pages if not tab.is_closed()
                            and allowed_login_url(tab.url, platform, method)]
                    if not live:
                        raise
                    page = live[-1]
        finally:
            browser.close()
