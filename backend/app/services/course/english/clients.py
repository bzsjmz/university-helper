"""Platform clients. No credentials, question text or remote errors are logged.

WE Learn time protocol adapted from xuebi0218/welearn-gui (MulanPSL-2.0).
U-campus catalogue selectors adapted from hyhang915/AutoUnipus-plus (BSD-3-Clause).
See docs/english-learning.md and third_party/english-learning/ for provenance.
"""
import json
import re
import time
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

import requests


class PlatformError(Exception):
    pass


def course_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != 'u.unipus.cn'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.path != '/user/student/mycourse/courseCatalog'):
        raise PlatformError('请输入老版 U校园的 HTTPS 课程目录网址')
    if not parsed.query or len(value) > 4096:
        raise PlatformError('课程网址缺少参数或过长')
    return value


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
            if (parsed.scheme != 'https' or parsed.hostname not in ('u.unipus.cn', 'ucontent.unipus.cn')
                    or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment):
                raise ValueError()
            entries = origin.get('localStorage', [])
            if not isinstance(entries, list) or any(
                not isinstance(e.get('name'), str) or not isinstance(e.get('value'), str) for e in entries
            ):
                raise ValueError()
            origins.append({'origin': origin['origin'], 'localStorage': entries})
        if not cookies:
            raise ValueError()
        return {'cookies': cookies, 'origins': origins}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PlatformError('请提供仅含 unipus.cn 登录信息的 Playwright storage state JSON') from None


class Welearn:
    BASE = 'https://welearn.sflep.com'

    def __init__(self, credential):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({'Referer': self.BASE + '/student/index.aspx',
                                     'User-Agent': 'Mozilla/5.0'})
        if '\n' in credential or '\r' in credential:
            raise PlatformError('Cookie 不可包含换行')
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

    def request(self, path, *, data=None, params=None, html=False):
        # Fixed origin, no redirect following: credentials never follow SSO/CDN URLs.
        response = self.session.request('POST' if data is not None else 'GET',
            self.BASE + path, data=data, params=params, timeout=(10, 20),
            allow_redirects=False, stream=True)
        try:
            if response.status_code != 200:
                raise PlatformError('平台请求失败，请检查登录会话或稍后重试')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 2_000_000:
                    raise PlatformError('平台返回内容过大')
                chunks.append(chunk)
            text = b''.join(chunks).decode('utf-8-sig')
            if html:
                return text
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except (ValueError, UnicodeError):
            raise PlatformError('平台返回格式变化或登录会话已失效') from None
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
                items.append({'id': str(lesson['id']), 'name': str(lesson.get('location', lesson['id']))[:256],
                              'progress': str(lesson.get('iscomplete', ''))[:32]})
        return identity, items

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


# Only reading/preview entries are offered. Exercise/test icons are excluded.
UNIPUS_ITEMS_JS = r"""() => {
  const results = []; const seen = new Set();
  for (const mark of document.querySelectorAll('.icon-bixiu')) {
    let row = mark.parentElement;
    for (let n = 0; n < 8 && row; n++, row = row.parentElement) {
      if (row.querySelectorAll('.iconfont').length >= 2) break;
    }
    if (!row || seen.has(row)) continue;
    seen.add(row);
    for (const icon of row.querySelectorAll('.icon-yulan, .icon-wenben, .icon-lianxi')) {
      if (!icon.getClientRects().length) continue;
      const id = String(results.length);
      icon.setAttribute('data-uh-reading', id);
      results.push({id, name: (row.innerText || '').trim().slice(0,240) + (icon.classList.contains('icon-lianxi') ? ' · 练习/作业' : ' · 阅读'), progress: ''});
    }
  }
  return results;
}"""


@contextmanager
def unipus_browser(credential, platform='unipus'):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PlatformError('自动答题需要 Playwright 和 Chromium 运行环境，当前后端尚未安装') from None
    if platform == 'welearn':
        client = Welearn(credential)
        state = {'cookies': [{'name': c.name, 'value': c.value, 'domain': c.domain,
                             'path': c.path, 'secure': True} for c in client.session.cookies], 'origins': []}
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
    url = course_url(url)
    with unipus_browser(credential) as (context, page):
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_selector('.icon-bixiu')
        items = page.evaluate(UNIPUS_ITEMS_JS)
        if item is None:
            return {'courses': [{'id': url, 'name': (page.title() or 'U校园课程')[:256]}], 'items': items}
        selected = next((i for i in items if i['id'] == item), None)
        if not selected or selected['name'] != expected_name:
            raise PlatformError('课程目录已变化，请刷新后重新选择内容')
        before = list(context.pages)
        page.locator(f'[data-uh-reading="{item}"]').click()
        page.wait_for_timeout(1500)
        opened = [p for p in context.pages if p not in before]
        target = opened[-1] if opened else page
        target.wait_for_load_state('domcontentloaded')
        if solve is not None:
            from .forms import answer_page
            return answer_page(target, solve, submit=submit, deadline=time.monotonic() + seconds)
        for frame in target.frames:
            if frame.locator('input:not([type=hidden]), textarea, select').count():
                return {'status': 'needs_manual', 'message': '该内容含交互题目，请在官网人工处理；未提交答案'}
        # Real page stays open; no synthetic score/time/completion requests.
        target.wait_for_timeout(seconds * 1000)
        return {'status': 'finished', 'message': '阅读页面停留已结束，实际进度和时长以官网为准'}


def welearn_answer(credential, cid, item, seconds, solve, submit=False):
    client = Welearn(credential)
    try:
        identity, items = client.items(cid)
    finally:
        client.session.close()
    if item not in {i['id'] for i in items}:
        raise PlatformError('该小节不属于所选课程或尚未开放')
    url = Welearn.BASE + '/student/StudyCourse.aspx?' + urlencode({**identity, 'sco': item})
    with unipus_browser(credential, platform='welearn') as (_, page):
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_timeout(2000)
        from .forms import answer_page
        return answer_page(page, solve, submit=submit, deadline=time.monotonic() + seconds)
