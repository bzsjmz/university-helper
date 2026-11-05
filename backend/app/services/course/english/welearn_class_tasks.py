"""WE Learn class assignments, separate from the SCORM lesson catalogue.

Protocol facts were checked against the account's official course page. Community
references and compatibility limits are documented in docs/welearn-class-tasks.md.
No community script or third-party answer service is executed.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timezone, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .clients import PlatformError, Welearn

FORM_SCRIPT = Path(__file__).with_name('welearn_class_form.js').read_text(encoding='utf-8')
TZ = timezone(timedelta(hours=8))


def plain(value, limit=256):
    if not isinstance(value, str) or len(value) > 12000:
        raise PlatformError('班级任务字段格式变化，请在官网核对')
    return unescape(re.sub(r'<[^>]*>', '', value)).strip()[:limit]


def task_date(value):
    if value in ('', '-'):
        return None
    try:
        return datetime.strptime(value, '%Y/%m/%d %H:%M').replace(tzinfo=TZ)
    except ValueError:
        raise PlatformError('班级任务日期格式变化，请在官网核对') from None


def normalize(row, now=None):
    """No evaluating TaskHref/TaskAction: only a known numeric native entry."""
    if not isinstance(row, dict):
        raise PlatformError('班级任务列表格式变化')
    name, kind, begin, end, status, action = [plain(row.get(k, '')) for k in
        ('TaskName', 'TaskTypeName', 'BeginDateStr', 'EndDateStr', 'Status', 'TaskAction')]
    if not name or not kind:
        raise PlatformError('班级任务名称或类型缺失')
    # Closed/not-yet-open assignments have no task id in the official response.
    # Identify them without inventing an entry URL; the identity survives grading.
    identity = json.dumps([name, kind, begin, end], ensure_ascii=True)
    key = hashlib.sha256(identity.encode()).hexdigest()[:32]
    href = row.get('TaskHref', '')
    entry = re.fullmatch(r"\s*StartTaskClassTest\(\s*['\"]?(\d{1,20})['\"]?\s*\)\s*;?\s*", href) if isinstance(href, str) else None
    score_match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*分', status)
    completed = bool(score_match) or status in ('已完成', '已提交', '待批阅', '已批阅', '已读')
    starts, ends = task_date(begin), task_date(end)
    now = now or datetime.now(TZ)
    if completed:
        state = 'completed'
    elif starts and ends and starts > ends:
        state = 'unknown'
    elif '未开始' in action or starts and now < starts:
        state = 'upcoming'
    elif any(s in action for s in ('已结束', '已截止', '已过期')) or ends and now > ends:
        state = 'expired'
    elif (kind == '测试' and entry and status == '未完成' and ends
          and re.search(r'开始|进入|继续|作答', action) and not re.search(r'重考|补考|重做|重新|查看', action)):
        state = 'available'
    else:
        state = 'manual'
    return {'id': key, 'name': name, 'type': kind, 'begin': begin, 'end': end,
            'progress': status, 'action': action, 'availability': state,
            'score': score_match.group(1) if score_match else None,
            'selectable': state == 'available',
            '_task_id': entry.group(1) if entry else None}


def public_task(row):
    return {k: v for k, v in row.items() if not k.startswith('_')}


def catalogue(client, cid):
    if cid not in {c['id'] for c in client.courses()}:
        raise PlatformError('该课程不在当前账号课程列表中')
    html = client.request('/student/course_info.aspx', params={'cid': cid}, html=True)
    uid = re.search(r'"uid":\s*(\d+)', html)
    clazz = re.search(r'"classid":"(\d*)"', html)
    if not uid or not clazz:
        raise PlatformError('课程身份格式变化或登录失效，请更新登录')
    if not clazz.group(1) or clazz.group(1) == '0':
        return []
    rows, seen = [], set()
    deadline = time.monotonic() + 90
    total = None
    for page in range(10):
        if time.monotonic() >= deadline:
            raise PlatformError('班级任务目录读取超时，请重新读取')
        data = client.request('/ajax/task.aspx', params={'action': 'gettasklist', 'cid': cid,
            'uid': uid.group(1), 'classid': clazz.group(1), 's': 100, 'pi': page})
        count = data.get('count')
        if (str(data.get('ret')) != '0' or not isinstance(data.get('list'), list)
                or type(count) is not int or not 0 <= count <= 1000
                or total is not None and count != total):
            raise PlatformError('班级任务目录不完整或格式变化，请重新读取')
        total = count
        for item in data['list']:
            row = normalize(item)
            if row['id'] in seen:
                raise PlatformError('班级任务存在重复或歧义，请在官网核对')
            seen.add(row['id'])
            rows.append(row)
        if len(rows) == total:
            return rows
        if len(rows) > total or not data['list']:
            break
    raise PlatformError('班级任务目录不完整，请在官网核对')


def result(message, counts=None, status='needs_manual'):
    return {'status': status, 'message': message, 'counts': counts or {}}


def answer(credential, cid, item, seconds, solve, submit=False, start_class_tests=False):
    client = Welearn(credential)
    try:
        rows = catalogue(client, cid)
        selected = next((r for r in rows if r['id'] == item), None)
        if not selected:
            raise PlatformError('班级任务已变更或移除，请重新读取并选择')
        if selected['availability'] == 'completed':
            return result('平台已确认班级任务完成，已跳过', {'skipped': 1}, 'finished')
        if not selected['selectable']:
            return result('班级任务未开放、已截止或需要人工处理，未进入测试')
        if not submit or not start_class_tests:
            return result('开始班级测试需要明确允许开始及提交；未进入测试或启动计时')
        from .clients import unipus_browser
        with unipus_browser(credential, platform='welearn') as (context, page):
            # Entry is constructed only from the fresh official list, never a caller URL.
            url = client.BASE + '/test/test.aspx?' + urlencode({'taskid': selected['_task_id'], 'cid': cid})
            page.goto(url, wait_until='domcontentloaded', timeout=min(25000, seconds * 1000))
            outcome = answer_test(page, solve, time.monotonic() + seconds)
            if not outcome['counts'].get('submission_attempted'):
                return outcome
            # A button click/absence is never a receipt. The account's class list
            # must independently confirm completion; uncertain writes are not replayed.
            for cookie in context.cookies([client.BASE]):
                if cookie['domain'].lstrip('.') in ('welearn.sflep.com', 'sflep.com'):
                    client.session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'])
            for attempt in range(3):
                if attempt:
                    page.wait_for_timeout(1000)
                current = next((r for r in catalogue(client, cid) if r['id'] == item), None)
                if current and current['availability'] == 'completed':
                    outcome['counts'].update(submitted=1, platform_completed=1, submission_unknown=0)
                    return result('平台班级任务列表已确认提交完成', outcome['counts'], 'finished')
            outcome['counts']['submission_unknown'] = 1
            return result('已尝试提交，班级任务状态尚未确认；请到官网核对，未自动重试', outcome['counts'])
    finally:
        client.session.close()


def answer_test(page, solve, deadline):
    """Preflight every native part, then answer; never bypass a platform challenge."""
    counts = {'questions': 0, 'filled': 0, 'submission_attempted': 0, 'submitted': 0}
    original = urlsplit(page.url)._replace(fragment='')
    host = urlsplit(page.url).hostname
    if host not in ('welearn.sflep.com', 'wetest.sflep.com'):
        return result('测试跳转到未支持的入口，请在官网处理', counts)
    page.wait_for_timeout(500)
    try:
        page.locator('.itemDiv').first.wait_for(state='attached', timeout=min(10000, max(1, int((deadline-time.monotonic())*1000))))
    except Exception:
        return result('测试正文未加载或需要人工验证；未填写或提交', counts)
    parts = page.locator('#ulParts > li')
    native_parts = parts.count()
    part_count = native_parts or 1
    if part_count > 20:
        return result('测试分部数量超过本轮范围，未填写或提交', counts)
    plans = []

    def goto(index):
        if (time.monotonic() >= deadline or urlsplit(page.url)._replace(fragment='') != original
                or page.locator('#spSubmit:visible').count() != 1 or parts.count() != native_parts):
            return False
        if parts.count():
            current = parts.nth(index)
            if 'active' not in (current.get_attribute('class') or '').split():
                current.locator('a').click(timeout=5000)
                page.wait_for_timeout(500)
                try:
                    page.wait_for_load_state('networkidle', timeout=min(8000, max(1, int((deadline-time.monotonic())*1000))))
                except Exception:
                    return False
            if 'active' not in (current.get_attribute('class') or '').split():
                return False
        return True

    for index in range(part_count):
        if not goto(index):
            return result('测试分部切换未确认或预算耗尽，未填写或提交', counts)
        snapshot = page.evaluate(FORM_SCRIPT)
        if snapshot['blocked'] or snapshot['unsupported'] or not snapshot['questions']:
            counts['unsupported'] = snapshot['unsupported'] or 1
            return result('测试存在不支持的题型、听力缺少文本或需要人工验证，未调用 AI、填写或提交', counts)
        counts['questions'] += len(snapshot['questions'])
        plans.append([snapshot, []])
    from .forms import fill_question, verify_question, public_question
    for snapshot, answers in plans:
        for question in snapshot['questions']:
            if time.monotonic() >= deadline:
                return result('测试答题预算耗尽，未填写或提交；平台计时不会暂停', counts)
            response = solve(public_question(question))
            if response.get('answer') is None:
                counts['missing'] = 1
                return result('测试答案不完整，未填写或提交；请在官网继续', counts)
            answers.append(response['answer'])
    for index, (snapshot, answers) in enumerate(plans):
        if not goto(index) or page.evaluate(FORM_SCRIPT) != snapshot:
            return result('测试页面已变化，未提交；请在官网核对', counts)
        for question, value in zip(snapshot['questions'], answers):
            if time.monotonic() >= deadline or not fill_question(page.main_frame, question, value):
                return result('测试填写未通过复核或预算耗尽，未提交', counts)
            counts['filled'] += 1
        if not all(verify_question(page.main_frame, q, a) for q, a in zip(snapshot['questions'], answers)):
            return result('测试答案复核失败，未提交', counts)
    # Revisit all parts before the one submission, including answers in earlier parts.
    for index, (snapshot, answers) in enumerate(plans):
        if (not goto(index) or page.evaluate(FORM_SCRIPT) != snapshot
                or not all(verify_question(page.main_frame, q, a) for q, a in zip(snapshot['questions'], answers))):
            return result('测试全部分部复核失败，未提交', counts)
    if time.monotonic() >= deadline:
        return result('测试预算耗尽，未提交', counts)
    button = page.locator('#aSubmit:visible')
    if button.count() != 1 or not re.fullmatch(r'\s*(?:提交|交卷|提交试卷|Submit)\s*', button.inner_text(), re.I):
        return result('未识别到唯一的原生交卷按钮，请在官网提交', counts)

    def confirm(dialog):
        if dialog.type == 'confirm' and re.search(r'提交|交卷', dialog.message):
            dialog.accept()
        else:
            dialog.dismiss()
    page.on('dialog', confirm)
    counts['submission_attempted'] = 1
    try:
        button.click(timeout=5000)
        page.wait_for_timeout(500)
        confirmation = page.locator('.layui-layer-dialog:visible, [role="dialog"]:visible, .modal:visible')
        if confirmation.count() == 1 and re.search(r'提交|交卷', confirmation.inner_text()):
            accept = confirmation.get_by_text(re.compile(r'^(?:确定|确认提交|确定提交|确认交卷|确定交卷)$'))
            if accept.count() == 1:
                accept.click(timeout=5000)
    except Exception:
        pass  # A replaced document/timeout may follow a successful write. Read back once.
    finally:
        page.remove_listener('dialog', confirm)
    return result('已尝试原生交卷，正在核对班级任务状态', counts)
