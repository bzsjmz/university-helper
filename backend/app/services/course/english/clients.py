"""Platform clients. No credentials, question text or remote errors are logged.

WE Learn time protocol adapted from xuebi0218/welearn-gui (MulanPSL-2.0).
U-campus AI uses official authenticated browser requests and visible course controls.
See docs/english-learning.md and third_party/english-learning/ for provenance.
"""
import json
from html import unescape
import re
import time
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

import requests


class PlatformError(Exception):
    pass


def completed_status(value):
    if isinstance(value, bool):
        return value
    if type(value) in (int, float):
        return value == 1
    if not isinstance(value, str) or len(value) > 1024:
        return False
    status = unescape(re.sub(r'<[^>]*>', '', value)).strip().lower()
    return status in ('1', 'true', 'completed', 'passed', '已完成', '完成', '已学完', '已通过')


def course_url(value):
    from .uai import course_id
    return course_id(value)


def storage_state(raw):
    try:
        state = json.loads(raw)
        if not isinstance(state, dict) or not isinstance(state.get('cookies'), list):
            raise ValueError()
        cookies = []
        for cookie in state['cookies']:
            domain = cookie['domain'].lstrip('.')
            if domain != 'unipus.cn' and not domain.endswith('.unipus.cn'):
                raise ValueError()
            cookies.append({key: cookie[key] for key in (
                'name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite'
            ) if key in cookie})
        origins = []
        for origin in state.get('origins', []):
            parsed = urlsplit(origin['origin'])
            if (parsed.scheme != 'https' or parsed.hostname not in ('ucloud.unipus.cn', 'uai.unipus.cn', 'ucontent.unipus.cn')
                    or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment):
                raise ValueError()
            entries = origin.get('localStorage', [])
            if not isinstance(entries, list) or any(
                not isinstance(e.get('name'), str) or not isinstance(e.get('value'), str) for e in entries
            ):
                raise ValueError()
            origins.append({'origin': origin['origin'], 'localStorage': entries})
        from .uai_auth import portal_token
        if not cookies and not portal_token({'origins': origins}):
            raise ValueError()
        return {'cookies': cookies, 'origins': origins}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PlatformError('请提供仅含 unipus.cn 登录信息的 Playwright storage state JSON') from None


class Welearn:
    BASE = 'https://welearn.sflep.com'

    def __init__(self, credential):
        self._item_units = {}
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({'Referer': self.BASE + '/student/index.aspx',
                                     'User-Agent': 'Mozilla/5.0'})
        if not isinstance(credential, str) or len(credential) > 100_000:
            raise PlatformError('WE Learn 登录凭据格式无效，请更新登录')
        if '\n' in credential or '\r' in credential:
            raise PlatformError('Cookie 不可包含换行')
        if credential.lstrip().startswith('{'):
            try:
                state = json.loads(credential)
                if state.get('welearn_session') != 1 or not isinstance(state.get('cookies'), list):
                    raise ValueError()
                if not 1 <= len(state['cookies']) <= 100:
                    raise ValueError()
                for cookie in state['cookies']:
                    name, value = cookie['name'], cookie['value']
                    domain, path = cookie['domain'], cookie.get('path', '/')
                    expires = cookie.get('expires')
                    if (not isinstance(name, str) or not name or not isinstance(value, str)
                            or re.search(r'[\x00-\x20\x7f;,=]', name)
                            or re.search(r'[\x00-\x1f\x7f;]', value)
                            or domain not in ('welearn.sflep.com', '.welearn.sflep.com', 'sflep.com', '.sflep.com')
                            or not isinstance(path, str) or not path.startswith('/')
                            or re.search(r'[\x00-\x20\x7f]', path)
                            or type(cookie.get('secure', False)) is not bool
                            or type(cookie.get('httpOnly', False)) is not bool
                            or cookie.get('sameSite', 'Lax') not in ('Lax', 'Strict', 'None')
                            or expires is not None and (type(expires) not in (int, float)
                                or not -1 <= expires <= 253402300799)):
                        raise ValueError()
                    self.session.cookies.set(name, value, domain=domain, path=path,
                        secure=cookie.get('secure', False),
                        expires=int(expires) if expires is not None and expires >= 0 else None,
                        rest={**({'HttpOnly': None} if cookie.get('httpOnly', False) else {}),
                              **({'SameSite': cookie['sameSite']} if 'sameSite' in cookie else {})})
                return
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                raise PlatformError('WE Learn 登录凭据格式无效，请更新登录') from None
        values = {}
        for part in credential.split(';'):
            if '=' in part:
                key, value = part.strip().split('=', 1)
                if key:
                    values[key] = value
        if not values:
            raise PlatformError('请输入浏览器请求中的 Cookie')
        for key, value in values.items():
            self.session.cookies.set(key, value, domain='welearn.sflep.com', path='/')

    def export_credential(self):
        cookies = [{'name': cookie.name, 'value': cookie.value, 'domain': cookie.domain,
                    'path': cookie.path, 'expires': cookie.expires, 'secure': cookie.secure,
                    'httpOnly': cookie.has_nonstandard_attr('HttpOnly')
                        and cookie.get_nonstandard_attr('HttpOnly') is not False,
                    **({'sameSite': cookie.get_nonstandard_attr('SameSite')}
                       if cookie.get_nonstandard_attr('SameSite') in ('Lax', 'Strict', 'None') else {})}
                   for cookie in self.session.cookies
                   if cookie.domain in ('welearn.sflep.com', '.welearn.sflep.com', 'sflep.com', '.sflep.com')]
        return json.dumps({'welearn_session': 1, 'cookies': cookies}, ensure_ascii=True)

    def request(self, path, *, data=None, params=None, html=False):
        # Fixed origin, no redirect following: credentials never follow SSO/CDN URLs.
        response = self.session.request('POST' if data is not None else 'GET',
            self.BASE + path, data=data, params=params, timeout=(10, 20),
            allow_redirects=False, stream=True)
        try:
            if response.status_code in (301, 302, 303, 307, 308, 401, 403):
                raise PlatformError('WE Learn 登录已失效或需要验证，请点击“更新登录”后重试')
            if response.status_code != 200:
                raise PlatformError('WE Learn 平台请求失败，请稍后重试')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 2_000_000:
                    raise PlatformError('平台返回内容过大')
                chunks.append(chunk)
            text = b''.join(chunks).decode('utf-8-sig')
            if re.search(r'<(?:form|input)\b', text, re.I) and re.search(
                    r'(?:prelogin\.aspx|sso\.sflep\.com|type\s*=\s*[\'"]?password\b)', text, re.I):
                raise PlatformError('WE Learn 登录已失效或需要验证，请点击“更新登录”后重试')
            if html:
                return text
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except (ValueError, UnicodeError):
            raise PlatformError('WE Learn 返回了无法识别的课程数据，请稍后重试；若仍失败，请更新登录') from None
        finally:
            response.close()

    def courses(self):
        data = self.request('/ajax/authCourse.aspx', params={'action': 'gmc'})
        if not isinstance(data.get('clist'), list):
            raise PlatformError('课程读取失败，请重新导入登录 Cookie')
        return [{'id': str(c['cid']), 'name': str(c.get('name', '未命名课程'))[:256],
                 'progress': str(c.get('per', ''))[:32]} for c in data['clist'][:200]]

    def items(self, cid):
        if cid not in {c['id'] for c in self.courses()}:
            raise PlatformError('该课程不在当前账号课程列表中')
        html = self.request('/student/course_info.aspx', params={'cid': cid}, html=True)
        uid = re.search(r'"uid":\s*(\d+)', html)
        classid = re.search(r'"classid":"(.*?)"', html)
        if not uid or not classid:
            raise PlatformError('课程详情格式变化或登录会话已失效')
        identity = {'cid': cid, 'uid': uid.group(1), 'classid': classid.group(1)}
        units = self.request('/ajax/StudyStat.aspx', params={
            'action': 'courseunits', 'cid': cid, 'uid': identity['uid']}).get('info')
        if not isinstance(units, list):
            raise PlatformError('无法读取课程单元')
        items = []
        for index, unit in enumerate(units[:200]):
            if str(unit.get('visible', 'true')).lower() == 'false':
                continue
            lessons = self.request('/ajax/StudyStat.aspx', params={
                'action': 'scoLeaves', **identity, 'unitidx': index}).get('info')
            if not isinstance(lessons, list):
                raise PlatformError('无法读取课程小节')
            for lesson in lessons:
                if str(lesson.get('isvisible', 'true')).lower() == 'false':
                    continue
                self._item_units[str(lesson['id'])] = index
                items.append({'id': str(lesson['id']), 'name': str(lesson.get('location', lesson['id']))[:256],
                              'progress': str(lesson.get('iscomplete', ''))[:32]})
        return identity, items

    def item_complete(self, identity, item):
        unit = self._item_units.get(item)
        if unit is None:
            return False
        rows = self.request('/ajax/StudyStat.aspx', params={
            'action': 'scoLeaves', **identity, 'unitidx': unit}).get('info')
        if not isinstance(rows, list):
            raise PlatformError('平台进度回读失败')
        row = next((row for row in rows if str(row.get('id')) == item), None)
        return bool(row and completed_status(row.get('iscomplete')))

    def sco(self, action, identity, **data):
        result = self.request('/Ajax/SCO.aspx', data={'action': action, **identity, **data})
        if str(result.get('ret')) != '0':
            raise PlatformError('平台未接受学习会话，请在官网检查登录和课程状态')
        return result

    def study(self, cid, item_id, seconds):
        identity, items = self.items(cid)
        if item_id not in {i['id'] for i in items}:
            raise PlatformError('所选小节已关闭或不属于该课程')
        identity.pop('classid')
        identity['scoid'] = item_id
        result = self.sco('getscoinfo_v7', identity)
        try:
            comment = result['comment']
            cmi = (json.loads(comment) if isinstance(comment, str) else comment)['cmi']
            total = int(float(cmi.get('total_time', 0)))
            if total < 0:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise PlatformError('请先在官网打开该小节，初始化学习记录后重试') from None
        started = time.monotonic()
        self.sco('keepsco_with_getticket_with_updatecmitime', identity, session_time='0', total_time=str(total))
        elapsed = 0
        while elapsed < seconds:
            time.sleep(min(30, max(0, seconds - (time.monotonic() - started))))
            elapsed = min(seconds, int(time.monotonic() - started))
            self.sco('keepsco_with_getticket_with_updatecmitime', identity,
                     session_time=str(elapsed), total_time=str(total + elapsed))
        # Preserve platform progress, score and completion status exactly.
        self.sco('savescoinfo160928', identity, progress=cmi.get('progress_measure', '0'),
                 crate=cmi.get('score', {}).get('scaled', ''), cstatus=cmi.get('completion_status', 'not_attempted'),
                 status='unknown', trycount='0', session_time=str(elapsed), total_time=str(total + elapsed))
        return {'status': 'finished', 'message': '限时会话已结束，请在官网核对累计时长；未修改分数或完成状态'}


@contextmanager
def unipus_browser(credential, platform='unipus_ai'):
    if platform not in ('welearn', 'unipus_ai'):
        raise PlatformError('该平台已移除')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PlatformError('自动答题需要 Playwright 和 Chromium 运行环境，当前后端尚未安装') from None
    if platform == 'welearn':
        client = Welearn(credential)
        cookies = json.loads(client.export_credential())['cookies']
        for cookie in cookies:
            if cookie['expires'] is None:
                del cookie['expires']
        state = {'cookies': cookies, 'origins': []}
        client.session.close()
        domain = 'sflep.com'
    else:
        state = storage_state(credential)
        domain = 'unipus.cn'
    with sync_playwright() as p:
        # Keep Chromium's sandbox enabled. Never request camera/microphone access.
        browser = p.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(storage_state=state, accept_downloads=False,
                                          service_workers='block')
            def guard(route):
                if platform == 'unipus_ai':
                    from .uai import decorative_portal_resource
                    if decorative_portal_resource(route.request):
                        route.abort()
                        return
                parsed = urlsplit(route.request.url)
                host = parsed.hostname or ''
                if (parsed.scheme == 'https' and (host == domain or host.endswith('.' + domain))
                        and parsed.port in (None, 443) and not parsed.username and not parsed.password):
                    route.continue_()
                else:
                    route.abort()
            context.route('**/*', guard)
            page = context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(20000)
            yield context, page
        finally:
            browser.close()


def unipus(credential, url, *, item=None, seconds=0, expected_name=None, solve=None, submit=False):
    from .uai import run
    return run(credential, url, item=item, seconds=seconds, expected_name=expected_name, solve=solve, submit=submit)


def welearn_answer(credential, cid, item, seconds, solve, submit=False, recording_bypass=False):
    from .welearn_page import wait_content, return_to_catalog
    client = Welearn(credential)
    try:
        identity, items = client.items(cid)
        selected = next((i for i in items if i['id'] == item), None)
        if selected is None:
            raise PlatformError('该小节不属于所选课程或尚未开放')
        if completed_status(selected.get('progress')):
            return {'status': 'finished', 'message': '平台已标记该课节完成，已跳过', 'counts': {'skipped': 1}}
        url = Welearn.BASE + '/student/StudyCourse.aspx?' + urlencode({**identity, 'sco': item})
        with unipus_browser(credential, platform='welearn') as (context, page):
            from .native_answers import NativeAnswers
            native = NativeAnswers()
            context.on('response', native.observe)
            deadline = time.monotonic() + seconds
            page.goto(url, wait_until='domcontentloaded')
            frames = wait_content(page, deadline)
            if not frames:
                return {'status': 'needs_manual', 'message': '课件正文未加载完成，请重试；没有将空白页面当作无题章节', 'counts': {}}
            from .forms import answer_page
            totals = {}
            for _ in range(20):
                result = answer_page(page, solve, submit=submit, deadline=deadline, native=native,
                                     frames=frames, allow_reading=True, allow_tabs=True,
                                     **({'recording_bypass': True} if recording_bypass and submit else {}))
                for key, value in result['counts'].items():
                    if type(value) is int and value >= 0:
                        totals[key] = totals.get(key, 0) + value
                if result['status'] != 'advanced':
                    break
                if time.monotonic() >= deadline:
                    result = {'status': 'needs_manual', 'message': '同一课节题页尚未完成，答题预算已用完', 'counts': {}}
                    break
                frames = wait_content(page, deadline)
                if not frames:
                    result = {'status': 'needs_manual', 'message': '下一题页未加载完成，未提交', 'counts': {}}
                    break
            else:
                result = {'status': 'needs_manual', 'message': '课节题页超过本轮处理范围，未继续切换', 'counts': {}}
            result['counts'] = totals
            fully_filled = (result['counts'].get('questions', 0) > 0
                            and result['counts'].get('filled') == result['counts']['questions']
                            and not result['counts'].get('unsupported') and not result['counts'].get('missing'))
            if result['status'] == 'visited' or fully_filled or result['counts'].get('submitted') or result['counts'].get('recordings_bypassed'):
                # Use the real courseware exit to let its SCORM runtime save.
                # Native UI receipts also require catalog confirmation for recording experiments.
                return_to_catalog(page)
                for cookie in context.cookies([Welearn.BASE]):
                    client.session.cookies.set(cookie['name'], cookie['value'], domain='welearn.sflep.com', path='/')
                completed = False
                for attempt in range(3):
                    try:
                        completed = client.item_complete(identity, item)
                    except (PlatformError, requests.RequestException):
                        break
                    if completed or attempt == 2 or time.monotonic() + 1 >= deadline:
                        break
                    page.wait_for_timeout(1000)
                if completed:
                    result['counts']['platform_completed'] = 1
                    result['counts']['submission_unknown'] = 0
                    result.update(status='finished', message='平台已确认课节完成')
                elif result['counts'].get('recordings_bypassed'):
                    result.update(status='needs_manual', message='录音实验提交后平台尚未确认课节完成；请在官网核对，勿直接重复提交')
                elif result['status'] == 'visited':
                    result.update(status='needs_manual', message='已访问无需填写的课件；平台尚未确认完成，未伪报完成')
                elif not result['counts'].get('questions') and result['counts'].get('submitted'):
                    result.update(status='needs_manual', message='录音页已收到提交回执，但平台尚未确认课节完成；请在官网核对，勿直接重复提交')
            return result
    finally:
        client.session.close()
