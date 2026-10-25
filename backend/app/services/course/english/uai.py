"""U-campus AI: use official authenticated requests and current browser controls.

No legacy catalogue URLs, signed-token generation or synthetic completion writes.
"""
import json
import re
import time
from urllib.parse import urlsplit

from .clients import PlatformError

HOME = 'https://uai.unipus.cn/portal/'
COURSES = 'https://uai.unipus.cn/api/cmgt/course/my/bookshelf'


def decorative_portal_resource(request):
    # The portal's cover art/media can exhaust a small headless renderer. Keep
    # authentication forms and actual ucontent exercises/media unaffected.
    try:
        frame_url = urlsplit(request.frame.url)
        return (request.resource_type in ('image', 'media', 'font')
                and frame_url.hostname in ('ucloud.unipus.cn', 'uai.unipus.cn')
                and not re.match(r'^/(?:portal/)?sso(?:/|$)', frame_url.path))
    except Exception:
        return False


def course_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_:+.\-]{1,256}', value):
        raise PlatformError('请选择 U校园 AI版账号下的课程；不再支持老版课程网址')
    return value


def courses(context, page=None, *, include_groups=False):
    from .uai_auth import browser_token
    jwt = browser_token(context)
    if not jwt:
        raise PlatformError('U校园登录状态尚未就绪，请完成官网登录')
    response = context.request.get(COURSES, headers={'Authorization': jwt,
                                            'Referer': HOME, 'Origin': 'https://' + urlsplit(HOME).netloc},
                                   timeout=15000, max_redirects=0)
    try:
        if response.status != 200:
            raise PlatformError('U校园 AI版课程读取失败，请重新登录')
        raw = response.body()
        if len(raw) > 2_000_000:
            raise PlatformError('U校园 AI版课程列表过大')
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise PlatformError('U校园 AI版课程列表格式变化') from None
        if not isinstance(data, dict) or data.get('code') != 1:
            raise PlatformError('U校园 AI版课程读取失败，请重新登录')
        value = data.get('value')
        groups = value.get('groups') if isinstance(value, dict) else None
        if not isinstance(groups, dict):
            raise PlatformError('U校园 AI版课程列表格式变化')
        result, seen = [], set()
        # Official bookshelf: active class courses first, then personal study.
        # Expired materials are deliberately not selectable.
        for group in ('CLASS_COURSE', 'PERSONAL'):
            rows = groups.get(group, [])
            if not isinstance(rows, list):
                raise PlatformError('U校园 AI版课程列表格式变化')
            for entry in rows[:200]:
                if not isinstance(entry, dict):
                    continue
                raw_id = entry.get('instanceId')
                if not isinstance(raw_id, (str, int)) or not re.fullmatch(r'[A-Za-z0-9_:+.\-]{1,256}', str(raw_id)):
                    continue
                cid = str(raw_id)
                if cid in seen:
                    continue
                seen.add(cid)
                record = {'id': cid, 'name': str(entry.get('name') or 'U校园 AI课程')[:256]}
                if include_groups:
                    record['group_name'] = record['name']
                result.append(record)
        return result
    finally:
        response.dispose()


ITEMS = r'''() => {
 const seen = new Set(), items = [];
 const candidates = document.querySelectorAll('[data-group-id], [data-leaf-id], [role="treeitem"], .ant-tree-node-content-wrapper, .catalog-item, .catalogue-item');
 for (const e of candidates) {
   if (!e.getClientRects().length || e.closest('[hidden], [aria-hidden="true"]')) continue;
   if (e.querySelector('[role="treeitem"], [data-group-id], [data-leaf-id]') || e.hasAttribute('aria-expanded')) continue;
   if (e.matches('.ant-tree-node-content-wrapper') && e.closest('[role="treeitem"]')?.hasAttribute('aria-expanded')) continue;
   const name = (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
   if (!name || name.length > 256 || /考试|测验口令|监考|已截止|未开放/.test(name) || seen.has(name)) continue;
   seen.add(name);
   const id = String(items.length);
   e.setAttribute('data-uh-uai-item', id);
   items.push({id, name, progress: ''});
   if (items.length >= 2000) break;
 }
 return items;
}'''


def open_course(context, page, cid):
    listed = courses(context, include_groups=True)
    selected = next((c for c in listed if c['id'] == course_id(cid)), None)
    if not selected:
        raise PlatformError('该课程不在当前 U校园 AI版账号中')
    page.goto(HOME, wait_until='domcontentloaded')
    page.wait_for_timeout(2000)
    def matching(name):
        links = [e for e in page.get_by_role('link', name=name, exact=True).all() if e.is_visible()]
        return links or [e for e in page.get_by_text(name, exact=True).all() if e.is_visible()]
    visible = matching(selected['name'])
    if not visible and selected.get('group_name') and selected['group_name'] != selected['name']:
        groups = matching(selected['group_name'])
        if len(groups) == 1:
            groups[0].click()
            page.wait_for_timeout(1000)
            visible = matching(selected['name'])
    if len(visible) != 1:
        raise PlatformError('未找到唯一 U校园 AI版课程卡片，请在官网检查教程是否已开通')
    before = list(context.pages)
    visible[0].click()
    page.wait_for_timeout(2000)
    opened = [p for p in context.pages if p not in before and not p.is_closed()]
    target = opened[-1] if opened else page
    target.wait_for_load_state('domcontentloaded')
    host = urlsplit(target.url).hostname
    if host not in ('ucloud.unipus.cn', 'uai.unipus.cn', 'ucontent.unipus.cn'):
        raise PlatformError('U校园 AI版课程打开失败，请在官网核对登录状态')
    # Expand only directory switches; never an exercise confirmation/submit.
    for _ in range(80):
        switch = target.locator('.ant-tree-switcher_close:visible')
        if not switch.count():
            break
        switch.first.click()
        target.wait_for_timeout(100)
    return target


def run(credential, cid='', *, item=None, seconds=0, expected_name=None, solve=None, submit=False):
    from .clients import unipus_browser
    with unipus_browser(credential, platform='unipus_ai') as (context, page):
        if not cid:
            return {'courses': courses(context)}
        target = open_course(context, page, cid)
        catalog = []
        for frame in target.frames:
            from .forms import frame_visible
            if frame_visible(frame):
                entries = frame.evaluate(ITEMS)
                catalog.extend((frame, entry) for entry in entries)
        # Stable references within a freshly re-read catalogue; expose no URLs or tokens.
        public = [{'id': str(n), 'name': entry['name'], 'progress': entry['progress']}
                  for n, (_, entry) in enumerate(catalog)]
        if item is None:
            return {'items': public}
        selected = next((i for i in public if i['id'] == item), None)
        if not selected or selected['name'] != expected_name:
            raise PlatformError('U校园 AI版目录已变化，请刷新范围后重试')
        frame, entry = catalog[int(item)]
        frame.locator(f'[data-uh-uai-item="{entry["id"]}"]').click()
        target.wait_for_timeout(1500)
        if solve is not None:
            from .forms import answer_page
            return answer_page(target, solve, submit=submit, deadline=time.monotonic() + seconds)
        target.wait_for_timeout(seconds * 1000)
        return {'status': 'finished', 'message': 'AI版页面停留结束，实际完成状态以平台为准'}
