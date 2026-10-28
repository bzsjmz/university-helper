"""Opt-in course assignments/exams, separate from chapter task points.

Only ordinary platform papers are handled. Verification, retakes, and unknown
page formats stay pending for manual handling. Never retry a submission whose
outcome is unknown, or turn an unanswered question into a random answer.
"""
import re
import secrets
import time
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from . import query_control as control
from .answer_quality import is_usable_answer
from .decode import FontDecoder, _extract_title

HOST = "https://mooc1-api.chaoxing.com"
HOSTS = {"mooc1-api.chaoxing.com", "mooc1.chaoxing.com", "mooc1-1.chaoxing.com",
         "mooc1-2.chaoxing.com", "mooc2-ans.chaoxing.com"}
PATHS = {
    "/work/task-list", "/mooc-ans/exam/phone/task-list", "/android/mtaskmsgspecial",
    "/exam-ans/android/mtaskmsgspecial", "/mooc-ans/android/mtaskmsgspecial",
    "/exam-ans/exam/phone/task-list", "/mooc-ans/work/phone/task-work",
    "/exam-ans/exam/phone/task-exam", "/mooc-ans/work/phone/doHomeWork",
    "/exam-ans/exam/phone/start", "/exam-ans/exam/test/reVersionTestStartNew",
    "/work/phone/doHomeWork", "/work/phone/doNormalHomeWorkSubmit",
    "/mooc-ans/work/phone/doNormalHomeWorkSubmit",
    "/exam-ans/exam/test/reVersionSubmitTestNew", "/mooc-ans/mooc2/work/task",
    "/mooc-ans/mooc2/work/dowork", "/mooc-ans/work/doHomeWorkNew",
    "/mooc-ans/work/addStudentWorkNew",
}
TYPES = {"0": "single", "1": "multiple", "2": "completion", "3": "judgement",
         "4": "shortanswer", "6": "shortanswer", "7": "shortanswer"}


class AssessmentUnavailable(Exception):
    """Detail is a fixed, safe message, never a URL, page, or platform token."""


def safe_url(url, base=HOST):
    if not isinstance(url, str) or re.search(r"[\s\\]", url) or re.search(r"%5c|%0[ad]", url, re.I):
        raise AssessmentUnavailable("不支持的试卷地址")
    absolute = urljoin(base, url)
    try:
        parsed = urlsplit(absolute)
        valid = (parsed.scheme == "https" and parsed.hostname in HOSTS and
                 not parsed.username and not parsed.password and parsed.port in (None, 443) and
                 parsed.path in PATHS and not parsed.fragment)
    except ValueError:
        valid = False
    if valid and any(len(set(values)) > 1 for values in parse_qs(parsed.query, keep_blank_values=True).values()):
        valid = False
    if not valid:
        raise AssessmentUnavailable("不支持的试卷地址")
    return absolute


def fields(soup):
    return {str(node.get("name") or node.get("id")): str(node.get("value", ""))
            for node in soup.select('input[type="hidden"], input:not([type])[value]') if node.get("name") or node.get("id")}


def assert_scope(values, course):
    for names, expected in [(('courseId', 'courseid'), course['courseId']),
                            (('classId', 'clazzId', 'clazzid'), course['clazzId']),
                            (('cpi',), course['cpi'])]:
        for name in names:
            value = values.get(name)
            if value and str(value) != str(expected):
                raise AssessmentUnavailable("试卷不属于当前所选课程或班级")


def check_page(soup, *, listing=False):
    text = soup.get_text(" ", strip=True)
    for tokens, reason in [
        (("请输入验证码", "滑动验证", "人脸识别", "人脸验证", "考试口令", "监考", "客户端考试"), "需要人工验证或指定客户端"),
        (("已过时效", "已过期", "考试已结束", "尚未开始", "未到开始时间"), "尚未开放或已截止"),
        (("章节任务点未完成", "不能参加考试"), "未满足考试开放条件"),
        (("请先登录", "登录后"), "平台会话已失效"),
    ]:
        # An unavailable row must not prevent processing other open papers.
        if listing and reason in ("尚未开放或已截止", "未满足考试开放条件"):
            continue
        if any(token in text for token in tokens):
            raise AssessmentUnavailable(reason)
    data = fields(soup)
    for key in ("captchaCaptchaId", "faceRecognition", "faceDetection", "monitorStatus", "needCode"):
        if data.get(key, "").strip().lower() not in ("", "0", "false", "-1"):
            raise AssessmentUnavailable("需要人工验证或指定客户端")


def parse_list(html, base, kind, course):
    soup = BeautifulSoup(html, "lxml")
    check_page(soup, listing=True)
    items, seen = [], set()
    rows = soup.select("ul.nav li[data], li[data]")
    for row in rows:
        raw = row.get('data', '')
        if not raw:
            continue
        url = safe_url(raw, base)
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        assert_scope(query, course)
        identifier = query.get('taskrefId') or query.get('workId') or query.get('examId')
        if not identifier or not identifier.isdigit():
            raise AssessmentUnavailable("无法识别作业或考试编号")
        identity = (kind, identifier)
        if identity in seen:
            continue
        seen.add(identity)
        title = row.select_one('.overHidden2, p')
        status_node = row.select_one('.status, span')
        status = status_node.get_text(' ', strip=True) if status_node else ''
        text = row.get_text(' ', strip=True)
        completed = any(token in status for token in ('已交', '已提交', '已完成', '已批', '待批', '待互', '已互'))
        unavailable = any(token in text for token in ('未开始', '已结束', '已过期', '已截止', '待重做', '重考'))
        pending = any(token in status for token in ('未交', '未提交', '待完成', '待考试', '未考', '进行中'))
        items.append({'id': identifier, 'kind': kind, 'url': url,
                      'title': title.get_text(' ', strip=True) if title else f'{kind} {identifier}',
                      'skip': completed or unavailable, 'pending': pending and not completed and not unavailable})
    if not rows and not any(token in soup.get_text() for token in ('暂无', '没有作业', '没有考试', '无作业', '无考试')):
        raise AssessmentUnavailable("无法识别列表，未将其当作空列表")
    pages = [int(node.get_text()) for node in soup.select('#page li') if node.get_text().strip().isdigit()]
    total_pages = max(pages, default=1)
    if total_pages > 50:
        raise AssessmentUnavailable("列表页数过多，请人工处理")
    return items, total_pages


def parse_paper(html, url, kind, course, expected_count=None):
    soup = BeautifulSoup(html, 'lxml')
    check_page(soup)
    data = fields(soup)
    assert_scope(data, course)
    if not (data.get('courseId') or data.get('courseid')) or not (data.get('classId') or data.get('clazzId') or data.get('clazzid')):
        raise AssessmentUnavailable('缺少试卷课程或班级标识')
    decoder = FontDecoder(html) if soup.select_one('#cxSecretStyle') else None
    questions = []
    for node in soup.select('.singleQuesId, .questionWrap'):
        qid = str(node.get('data', ''))
        if not qid.isdigit():
            raise AssessmentUnavailable("无法识别题目编号")
        type_node = soup.find('input', attrs={'name': f'type{qid}'}) or soup.find('input', attrs={'name': f'answertype{qid}'})
        timu = node.select_one('.TiMu[data]')
        code = str(type_node.get('value', '')) if type_node else str(timu.get('data', '')) if timu else ''
        if code not in TYPES:
            raise AssessmentUnavailable("试卷包含尚不支持的题型")
        title = (node.select_one('.Zy_TItle, .tit, .questionTitle') or
                 node.select_one('.ans-cc.workWrap') or node.select_one('.fontLabel'))
        if not title:
            raise AssessmentUnavailable("无法识别题干")
        options = []
        for option in node.select('.centerSpan, .singleChoice, .mulChoice'):
            letter = option.get('name') or option.get('id', '')
            if not re.fullmatch('[A-Z]', letter):
                continue
            content = option.select_one('.answerInfo, cc') or option
            text = _extract_title(content, decoder).strip()
            if content is option:
                text = re.sub(rf'^{letter}[.、．\s]+', '', text)
            options.append((letter, text))
        if not options and TYPES[code] in ('single', 'multiple'):
            for option in node.select('ul li'):
                text = option.get('aria-label') or _extract_title(option, decoder).strip()
                match = re.match(r'^([A-Z])[.、．\s]*(.+)', text)
                if match:
                    options.append((match[1], match[2]))
        if TYPES[code] in ('single', 'multiple') and (not options or len({p[0] for p in options}) != len(options)):
            raise AssessmentUnavailable("无法识别题目选项")
        blank_names = [str(n.get('name')) for n in node.select('input[name], textarea[name]')
                       if re.fullmatch(rf'answer(?:Editor)?{qid}(?:_\d+|\d+)', str(n.get('name', '')))]
        questions.append({'id': qid, 'code': code, 'type': TYPES[code],
                          'title': _extract_title(title, decoder).strip(),
                          'options': '\n'.join(f'{letter}. {text}' for letter, text in options),
                          '_options': options, '_blanks': blank_names})
    if not questions or len({q['id'] for q in questions}) != len(questions):
        raise AssessmentUnavailable("无法完整识别试卷")
    form = soup.select_one('form[action]')
    action = form.get('action', '') if form else ''
    if action and action != '#':
        submit_url = safe_url(action, url)
        assert_scope({key: values[0] for key, values in parse_qs(urlsplit(submit_url).query).items()}, course)
    else:
        path = '/exam-ans/exam/test/reVersionSubmitTestNew' if kind == 'exam' else (
            '/mooc-ans/work/phone/doNormalHomeWorkSubmit' if '/phone/' in url else '/mooc-ans/work/addStudentWorkNew')
        submit_url = safe_url(path, url)
    allowed = {'/exam-ans/exam/test/reVersionSubmitTestNew'} if kind == 'exam' else {
        '/mooc-ans/work/phone/doNormalHomeWorkSubmit', '/work/phone/doNormalHomeWorkSubmit',
        '/mooc-ans/work/addStudentWorkNew'}
    if urlsplit(submit_url).path not in allowed:
        raise AssessmentUnavailable("不支持的试卷提交方式")
    required = ('testPaperId', 'testUserRelationId') if kind == 'exam' else ('workRelationId', 'workRelationAnswerId')
    if any(not data.get(key) for key in required):
        raise AssessmentUnavailable("缺少试卷提交参数")
    if kind == 'exam' and (len(questions) != 1 or not data.get('userId', '').isdigit()):
        raise AssessmentUnavailable('该考试格式或签名标识暂不支持')
    count_node = soup.select_one('input#questionCount, input#totalQuestionNum, input#totalQuestionCount')
    count_match = re.search(r'共(?:包含)?\s*(\d+)\s*(?:道题目|道题|题)', soup.get_text())
    if not count_node and not count_match and not expected_count and ('/phone/' in url or kind == 'exam'):
        raise AssessmentUnavailable('无法确认试卷总题数')
    count = int(count_node.get('value', '0')) if count_node else int(count_match[1]) if count_match else expected_count or len(questions)
    count = max(count, expected_count or 0)
    if count < len(questions) or count > 500:
        raise AssessmentUnavailable("试卷题目数量异常")
    return {'url': url, 'submit_url': submit_url, 'data': data, 'questions': questions, 'count': count}


def answer_fields(question, response, tiku, kind):
    """Match exact text/letters; reject ambiguous matches and missing blanks."""
    text = str(response or '').strip()
    if not is_usable_answer(text):
        return None
    qid, code = question['id'], question['code']
    if code in ('0', '1'):
        opts = question['_options']
        exact = [letter for letter, option in opts if option == text]
        if exact:
            letters = ''.join(exact)
        elif re.fullmatch(r'[A-Z](?:[\s,，、;；]*[A-Z])*', text):
            letters = ''.join(dict.fromkeys(re.findall('[A-Z]', text)))
        else:
            parts = [part.strip() for part in re.split(r'\n|#|\|', text) if part.strip()]
            matched = [[letter for letter, option in opts if option == part] for part in parts]
            if not matched or any(len(values) != 1 for values in matched):
                return None
            letters = ''.join(dict.fromkeys(values[0] for values in matched))
        if (code == '0' and len(letters) != 1) or not set(letters) <= {letter for letter, _ in opts}:
            return None
        return {('answers' if code == '1' and kind != 'chapter' else 'answer') + qid: letters}
    if code == '3':
        # Use the configured true/false vocabulary, never treat an unknown
        # string as false just because judgement_select returned false.
        true_values = {'true', '对', '正确', '是', '√'} | set(getattr(tiku, 'true_list', []) or [])
        false_values = {'false', '错', '错误', '否', '×'} | set(getattr(tiku, 'false_list', []) or [])
        if text.lower() not in true_values | false_values:
            return None
        return {f'answer{qid}': 'true' if text.lower() in true_values else 'false'}
    if code == '2':
        blanks = question['_blanks']
        parts = text.splitlines()
        if not blanks or len(parts) != len(blanks) or any(not part.strip() for part in parts):
            return None
        return dict(zip(blanks, parts))
    return {f'answer{qid}': text}


def exam_signature(user_id, question_id):
    """Ordinary request envelope, with no mouse event coordinates.

    Protocol reference: yatori-dev/yatori-go-core, XueXiTongExamApi.go.
    This does not supply captcha/face/monitor credentials or disable monitoring.
    """
    if not str(user_id).isdigit() or not str(question_id).isdigit():
        raise AssessmentUnavailable('缺少考试请求签名标识')
    timestamp = str(int(time.time() * 1000))
    a, b = secrets.randbelow(9), secrets.randbelow(9)
    seed = secrets.token_hex(16) + timestamp[4:] + str(a) + str(b) + str(question_id)
    checksum = 0
    for character in seed:
        checksum = (checksum * 31 + ord(character)) & 0xffffffff
    salt = f'{a}{b}{(checksum & 0x7fffffff) % 10}'
    identity = f'{user_id}_{question_id}|{salt}'
    digits = ''.join(str(ord(character)) for character in identity)
    step = len(digits) // 5
    multiplier = int(''.join(digits[step * index] for index in range(1, 5)))
    increment = len(identity) // 2 + 1
    modulus = 0x7fffffff
    state = (multiplier * int(digits[:10]) + increment) % modulus
    # The platform envelope represents absence of a mouse event as NaN.
    # Do not invent click positions or alter any proctoring fields.
    value = '(NaN|NaN)'
    encoded = ''
    for character in value:
        encoded += f'{ord(character) ^ (state * 255 // modulus):02x}'
        state = (multiplier * state + increment) % modulus
    return {'pos': encoded + secrets.token_hex(4), 'rd': secrets.randbits(53) / 2**53,
            'value': quote(value, safe=''), '_edt': timestamp + salt,
            'qid': str(question_id), '_csign': '1', '_signcode': '3', '_signc': '0', '_signe': '3-1'}


class CourseAssessmentService:
    def __init__(self, client):
        self.client = client
        self.session = client.session_manager.get_session()
        self.tiku = client.tiku

    def get(self, url):
        for _ in range(6):
            url = safe_url(url)
            control.check_control()
            response = self.session.get(url, timeout=control.request_timeout(30), allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                url = safe_url(response.headers.get('Location', ''), url)
                continue
            if response.status_code != 200:
                raise AssessmentUnavailable("读取作业或考试失败")
            return response.text, url
        raise AssessmentUnavailable("平台重定向次数过多")

    def list(self, course, kind):
        path = '/work/task-list' if kind == 'homework' else '/mooc-ans/exam/phone/task-list'
        params = {'courseId': course['courseId'], 'classId': course['clazzId'], 'cpi': course['cpi']}
        items, seen, page, total = [], set(), 1, 1
        while page <= total:
            html, url = self.get(HOST + path + '?' + urlencode({**params, 'pageNum': page}))
            batch, pages = parse_list(html, url, kind, course)
            for item in batch:
                if item['id'] not in seen:
                    items.append(item)
                    seen.add(item['id'])
            if page > 1 and batch and all(item['id'] in previous for item in batch):
                raise AssessmentUnavailable("平台未返回下一页，列表读取不完整")
            previous = set(seen)
            total = max(total, pages)
            page += 1
        return items

    def open_paper(self, item, course):
        entry = urlsplit(item['url'])
        if entry.path in ('/mooc-ans/work/phone/task-work', '/exam-ans/exam/phone/task-exam'):
            entry = entry._replace(path='/android/mtaskmsgspecial' if item['kind'] == 'homework' else '/exam-ans/android/mtaskmsgspecial')
        html, url = self.get(entry.geturl())
        soup = BeautifulSoup(html, 'lxml')
        check_page(soup)
        count_match = re.search(r'共(?:包含)?\s*(\d+)\s*(?:道题目|道题|题)', soup.get_text())
        expected_count = int(count_match[1]) if count_match else None
        if not soup.select_one('.singleQuesId, .questionWrap'):
            data = fields(soup)
            assert_scope(data, course)
            if item['kind'] == 'exam':
                if not data.get('testPaperId') or not data.get('testUserRelationId'):
                    raise AssessmentUnavailable("考试入口需人工处理")
                path = '/exam-ans/exam/phone/start'
                params = {'examId': data['testPaperId'], 'examAnswerId': data['testUserRelationId'], 'source': '0'}
            else:
                path = '/mooc-ans/work/phone/doHomeWork'
                # Assignment entry pages often embed this link in script.
                links = re.findall(r'["\']([^"\']*(?:/work/phone/doHomeWork)[^"\']*)["\']', html)
                if links:
                    target = safe_url(links[0].replace('&amp;', '&'), url)
                    assert_scope({key: values[0] for key, values in parse_qs(urlsplit(target).query).items()}, course)
                    html, url = self.get(target)
                    return parse_paper(html, url, item['kind'], course, expected_count)
                params = {'workId': item['id'], 'workAnswerId': data.get('workRelationAnswerId', ''),
                          'enc': data.get('enc', ''), 'mooc': '1', 'source': '0', 'msgId': '0'}
                if not params['enc']:
                    raise AssessmentUnavailable("作业入口缺少访问参数")
            params.update(courseId=course['courseId'], classId=course['clazzId'], cpi=course['cpi'])
            html, url = self.get(HOST + path + '?' + urlencode(params))
        return parse_paper(html, url, item['kind'], course, expected_count)

    def paper_pages(self, first, kind, course):
        pages = [first]
        count = first['count']
        if len(first['questions']) == count:
            return pages
        if len(first['questions']) != 1:
            raise AssessmentUnavailable("暂不支持该试卷分页方式")
        for index in range(1, count):
            data = pages[-1]['data']
            if kind == 'homework':
                parsed = urlsplit(first['url'])
                params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                # The initial integrity check resets navigation to question 0.
                # Follow the platform's next-question envelope, using the
                # relation identifiers returned in the paper, not entry IDs.
                for key in ('checkIntegrity', 'oldWorkId'):
                    params.pop(key, None)
                params.update(workId=data['workRelationId'], workAnswerId=data['workRelationAnswerId'],
                              knowledgeid=data.get('knowledgeid', ''),
                              enc=data.get('enc', params.get('enc', '')), mooc='1', source='0', index=index)
                target = parsed._replace(query=urlencode(params)).geturl()
            else:
                required = ('testPaperId', 'testUserRelationId', 'enc', 'encRemainTime', 'encLastUpdateTime')
                if any(not data.get(key) for key in required):
                    raise AssessmentUnavailable("缺少考试翻页参数")
                params = {'courseId': course['courseId'], 'classId': course['clazzId'], 'cpi': course['cpi'],
                          'tId': data['testPaperId'], 'id': data['testUserRelationId'], 'p': '1', 'start': index + 1,
                          'enc': data['enc'], 'remainTimeParam': data['encRemainTime'],
                          'relationAnswerLastUpdateTime': data['encLastUpdateTime']}
                target = HOST + '/exam-ans/exam/test/reVersionTestStartNew?' + urlencode(params)
            html, url = self.get(target)
            page = parse_paper(html, url, kind, course, count)
            if len(page['questions']) != 1 or page['questions'][0]['id'] in {p['questions'][0]['id'] for p in pages}:
                raise AssessmentUnavailable("试卷分页重复或不完整")
            for key in ('testPaperId', 'testUserRelationId', 'workRelationId', 'workRelationAnswerId'):
                if page['data'].get(key) != first['data'].get(key):
                    raise AssessmentUnavailable("试卷分页标识发生变化")
            pages.append(page)
        return pages

    def submit(self, page, answers, kind, final):
        control.check_control()
        data = {**page['data'], **answers}
        legacy = urlsplit(page['submit_url']).path.endswith('addStudentWorkNew')
        if legacy:
            data['pyFlag'] = '' if final else '1'
            data['answerwqbid'] = ','.join(q['id'] for q in page['questions']) + ','
        else:
            data['tempSave'] = 'false' if final else 'true'
        if kind == 'exam':
            data['version'] = '1'
            if len(page['questions']) != 1:
                raise AssessmentUnavailable('暂不支持整卷模式的课程考试')
            signature = exam_signature(data.get('userId', ''), page['questions'][0]['id'])
        else:
            signature = {}
        params = {'tempSave': data.get('tempSave', ''), **signature} if not legacy else None
        if kind == 'exam':
            params.update({key: data[key] for key in ('classId', 'courseId', 'testPaperId', 'testUserRelationId')})
            params.update(version='1', cpi=data.get('cpi', ''))
        # One attempt, including transport retries: a connection failure after
        # sending may mean the platform already accepted the submission.
        adapter = self.session.get_adapter(page['submit_url'])
        retry = adapter.max_retries
        try:
            adapter.max_retries = 0
            response = self.session.post(page['submit_url'], data=data,
                params=params,
                headers={'Referer': page['url'], 'X-Requested-With': 'XMLHttpRequest'},
                timeout=control.request_timeout(30), allow_redirects=False)
        except control.QueryControlError:
            raise
        except Exception:
            raise AssessmentUnavailable("保存或提交结果未确认，请在平台核对") from None
        finally:
            adapter.max_retries = retry
        try:
            result = response.json()
        except Exception:
            result = None
        if response.status_code != 200 or not isinstance(result, dict) or result.get('status') not in (True, 1, 'true'):
            raise AssessmentUnavailable("平台未确认保存或提交成功，请人工核对")

    def run(self, course, *, homework=False, exams=False, homework_titles=None,
            checkpoint=lambda: None, report=lambda *args: None):
        if homework_titles is not None and (not isinstance(homework_titles, list) or
                any(not isinstance(title, str) or not title.strip() for title in homework_titles)):
            raise ValueError('Homework selection must be a list of nonempty titles')
        selected_titles = None if homework_titles is None else set(homework_titles)
        summary = {'submitted': 0, 'saved': 0, 'skipped': 0, 'failed': 0}
        for kind, enabled, label in [('homework', homework, '课程作业'), ('exam', exams, '课程考试')]:
            if not enabled:
                continue
            checkpoint()
            try:
                with control.job_scope(checkpoint, answering=True):
                    checkpoint()
                    items = self.list(course, kind)
            except control.QueryCancelled:
                raise
            except AssessmentUnavailable as exc:
                summary['failed'] += 1
                report(f'{label}列表读取失败：{exc}', 'warning')
                continue
            except Exception:
                summary['failed'] += 1
                report(f'{label}列表读取失败，需人工核对', 'warning')
                continue
            for item in items:
                checkpoint()
                if kind == 'homework' and selected_titles is not None and item['title'] not in selected_titles:
                    summary['skipped'] += 1
                    continue
                if item['skip']:
                    summary['skipped'] += 1
                    continue
                if not item['pending']:
                    summary['failed'] += 1
                    report(f'{label}：状态无法确认，未进入试卷', 'warning')
                    continue
                try:
                    if not self.tiku or self.tiku.DISABLE:
                        raise AssessmentUnavailable("未启用题库")
                    with control.job_scope(checkpoint, answering=True):
                        report(f'开始处理{label}：{item["title"]}', 'info')
                        first = self.open_paper(item, course)
                        identity_key = 'testPaperId' if kind == 'exam' else 'workRelationId'
                        if first['data'].get(identity_key) != item['id']:
                            raise AssessmentUnavailable('试卷编号与列表不一致')
                        pages = self.paper_pages(first, kind, course)
                        answers_per_page, found, total = [], 0, 0
                        for page in pages:
                            answer = {}
                            for question in page['questions']:
                                checkpoint()
                                response = self.tiku.query({key: question[key] for key in ('id', 'title', 'type', 'options')})
                                mapped = answer_fields(question, response, self.tiku,
                                    'chapter' if urlsplit(page['submit_url']).path.endswith('addStudentWorkNew') else kind)
                                total += 1
                                if mapped:
                                    answer.update(mapped)
                                    found += 1
                            answers_per_page.append(answer)
                        # Course assessments never inherit the chapter retry
                        # override that permits submitting random answers.
                        final = self.tiku.get_submit_params() != '1' and found == total and found / total >= self.tiku.COVER_RATE
                        for index, page in enumerate(pages):
                            checkpoint()
                            if kind == 'exam':
                                # Refresh time/session fields after answer lookup; do
                                # not replay stale countdown or last-update tokens.
                                fresh_html, fresh_url = self.get(page['url'])
                                fresh = parse_paper(fresh_html, fresh_url, kind, course, first['count'])
                                if ([q['id'] for q in fresh['questions']] != [q['id'] for q in page['questions']] or
                                    any(fresh['data'].get(key) != page['data'].get(key) for key in ('testPaperId', 'testUserRelationId')) or
                                    any((new['title'], new['options'], new['code']) != (old['title'], old['options'], old['code'])
                                        for new, old in zip(fresh['questions'], page['questions']))):
                                    raise AssessmentUnavailable('试卷刷新后题目发生变化，未提交旧答案')
                                page = fresh
                            self.submit(page, answers_per_page[index], kind, final and index == len(pages) - 1)
                        summary['submitted' if final else 'saved'] += 1
                        if not final and self.tiku.get_submit_params() != '1':
                            summary['failed'] += 1
                        report(f'{label}已{"提交" if final else "保存，待人工完成"}（匹配 {found}/{total} 题）',
                               'success' if final else 'warning')
                except control.QueryCancelled:
                    raise
                except control.QueryExpired:
                    summary['failed'] += 1
                    report(f'{label}处理超时，需人工核对', 'warning')
                except AssessmentUnavailable as exc:
                    summary['failed'] += 1
                    report(f'{label}未完成：{exc}', 'warning')
                except Exception:
                    summary['failed'] += 1
                    report(f'{label}处理失败，需人工核对', 'warning')
        return summary
