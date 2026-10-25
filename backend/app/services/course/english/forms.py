"""Native browser form answering, without synthetic grade/completion writes."""
import re
import time
from pathlib import Path

FORM_SCRIPT = Path(__file__).with_name('question_form.js').read_text(encoding='utf-8')
# Deliberately narrow: a click is not proof of a successful save/submission.
RECEIPT_SCRIPT = r"""() => Array.from(document.querySelectorAll(
  '[role=dialog], .ant-modal-content, .completion, .result, .save-success, .submit-success, [data-submitted], et-button[success-text="Submitted"] button'
)).filter(e => e.getClientRects().length).map(e => e.matches('et-button[success-text="Submitted"] button') && (e.innerText || '').trim() === 'Submitted' ? '课件已提交' : e.hasAttribute('data-submitted') && ['', 'true', '1', 'submitted'].includes(e.getAttribute('data-submitted')) ? '课件已提交' : (e.innerText || '')).join('\n').slice(0,10000)"""


def read_form(frame):
    return frame.evaluate(FORM_SCRIPT)


def frame_visible(frame):
    try:
        for _ in range(16):
            parent = frame.parent_frame
            if parent is None:
                return True
            if not frame.frame_element().is_visible():
                return False
            frame = parent
    except Exception:
        pass
    return False


def public_question(question):
    return {k: question[k] for k in ('type', 'title', 'context', 'options', 'count')}


def custom_selected(locator):
    return locator.evaluate("e => e.getAttribute('aria-checked') === 'true' || e.getAttribute('aria-selected') === 'true' || e.getAttribute('data-selected') === 'true' || ['selected','checked','active','ui-state-active'].some(c => e.classList.contains(c))")


def control_value(locator, kind):
    return locator.inner_text() if kind == 'contenteditable' else locator.input_value()


def fill_question(frame, question, answer):
    controls = [frame.locator(f'[data-uh-control="{i}"]') for i in question['controls']]
    kind = question['type']
    if kind in ('single', 'multiple'):
        if not isinstance(answer, list) or not answer or any(type(i) is not int or i < 0 or i >= len(question['options']) for i in answer):
            return False
        if len(set(answer)) != len(answer):
            return False
        if kind == 'single' and len(answer) != 1:
            return False
        if question.get('control_types', [None])[0] == 'welearn-option':
            controls[answer[0]].click()
            return all(custom_selected(locator) == (i in answer) for i, locator in enumerate(controls))
        if question['values']:
            controls[0].select_option(value=question['values'][answer[0]])
            return controls[0].input_value() == question['values'][answer[0]]
        for i, locator in enumerate(controls):
            if kind == 'multiple' and i not in answer:
                locator.uncheck()
            elif i in answer:
                locator.check()
        return all(locator.is_checked() == (i in answer) for i, locator in enumerate(controls))
    if (not isinstance(answer, list) or len(answer) != len(controls)
            or any(not isinstance(s, str) or not s.strip() for s in answer)):
        return False
    kinds = question.get('control_types', ['text'] * len(controls))
    for locator, value, control_kind in zip(controls, answer, kinds):
        locator.fill(value)
        if control_kind == 'contenteditable':
            locator.dispatch_event('input')
            locator.dispatch_event('keyup', {'key': 'Unidentified'})
            locator.press('Tab')
        locator.dispatch_event('change')
    return all(control_value(locator, kind) == value for locator, value, kind in zip(controls, answer, kinds))


def verify_question(frame, question, answer):
    controls = [frame.locator(f'[data-uh-control="{i}"]') for i in question['controls']]
    if question['type'] in ('single', 'multiple'):
        if question.get('control_types', [None])[0] == 'welearn-option':
            return all(custom_selected(locator) == (i in answer) for i, locator in enumerate(controls))
        if question['values']:
            return controls[0].input_value() == question['values'][answer[0]]
        return all(locator.is_checked() == (i in answer) for i, locator in enumerate(controls))
    return all(control_value(locator, kind) == value for locator, value, kind in zip(controls, answer, question.get('control_types', ['text'] * len(controls))))


def button(frame, names):
    # Accessible exact names only; never click a generic 'next/confirm/start exam'.
    for name in names:
        candidate = frame.get_by_role('button', name=name, exact=True)
        if candidate.count() == 1 and candidate.is_visible() and candidate.is_enabled():
            return candidate
    candidate = frame.locator('[data-controltype="submit"], [class*="submit-bar-pc--btn"]').filter(
        has_text=re.compile('^(?:' + '|'.join(re.escape(n) for n in names) + ')$'))
    if candidate.count() == 1 and candidate.is_visible() and candidate.is_enabled():
        return candidate
    return None


def advance_tab(page, frames, deadline):
    if deadline is not None and time.monotonic() >= deadline:
        return False
    choices = []
    for frame in frames:
        locator = frame.locator('et-item et-button.nextTab-btn[action="item.switchTab(0)"] button:visible')
        if locator.count() == 1 and locator.is_enabled():
            choices.append((frame, locator))
    if len(choices) != 1:
        return False
    frame, locator = choices[0]
    before = frame.evaluate("() => [...document.querySelectorAll('et-tab .headerTab li.active')].map(e=>e.textContent.trim()).join('|')")
    url = frame.url
    locator.click(timeout=5000)
    for _ in range(12):
        page.wait_for_timeout(250)
        if frame.url != url:
            return False
        after = frame.evaluate("() => [...document.querySelectorAll('et-tab .headerTab li.active')].map(e=>e.textContent.trim()).join('|')")
        if after and after != before:
            return True
    return False


def answer_page(page, solve, *, submit=False, deadline=None, native=None, frames=None, allow_reading=False, allow_tabs=False):
    """All frames are checked before filling; one unknown control blocks submission.

    Default callers handle one selected page. WE Learn may opt into its
    declared ET tab pager; no caller retakes a paper or retries an uncertain click.
    """
    counts = dict(questions=0, answered=0, missing=0, unsupported=0, filled=0,
                  saved=0, submitted=0, submission_unknown=0, native_answers=0, cache_hits=0, ai_answers=0)
    frames = list(page.frames) if frames is None else list(frames)
    forms = []
    for frame in frames:
        if not frame_visible(frame):
            continue
        snapshot = read_form(frame)
        if snapshot.get('blocked'):
            return {'status': 'needs_manual', 'message': '页面要求人工验证或尚未开放，未填写或提交', 'counts': counts}
        counts['unsupported'] += snapshot['unsupported']
        if snapshot['questions']:
            forms.append((frame, snapshot))
            counts['questions'] += len(snapshot['questions'])
    if not counts['questions']:
        if allow_tabs and not counts['unsupported'] and advance_tab(page, frames, deadline):
            return {'status': 'advanced', 'message': '已访问课节内阅读页，继续下一题页', 'counts': {**counts, 'visited': 1, 'pages_advanced': 1}}
        if allow_reading and not counts['unsupported'] and any(frame.evaluate(r'''() => {
          const item = document.querySelector('et-item[notscored]');
          return !!item && (item.innerText || '').trim().length >= 30 &&
            !item.querySelector('et-blank,et-choice,et-select,et-tof,et-matching,et-sort,input,textarea,select,[contenteditable],[data-controltype="filling"],[data-controltype="choice"]');
        }''') for frame in frames):
            return {'status': 'visited', 'message': '已访问无需填写的课件，正在核对平台进度',
                    'counts': {**counts, 'visited': 1}}
        if allow_reading and any(frame.locator('et-talk').count() for frame in frames):
            return {'status': 'needs_manual', 'message': '本节是口语对话任务，请在平台完成口语录音', 'counts': counts}
        return {'status': 'needs_manual', 'message': '未识别到支持的题目，未提交', 'counts': counts}
    if counts['unsupported']:
        return {'status': 'needs_manual', 'message': '存在不支持的控件，未调用 AI、填写或提交', 'counts': counts}
    plans = []
    for frame, snapshot in forms:
        answers = []
        for question in snapshot['questions']:
            if not frame_visible(frame):
                return {'status': 'needs_manual', 'message': '课件页面已切换，未继续答题或提交', 'counts': counts}
            if deadline is not None and time.monotonic() >= deadline:
                counts['missing'] += 1
                answers.append(None)
                continue
            request = public_question(question)
            if native is not None:
                value = native.answer(frame, question)
                if value is not None:
                    request['native_answer'] = value
            result = ({'answer': None} if question.get('requires_native') and 'native_answer' not in request
                      else solve(request))
            answer = result.get('answer')
            answers.append(answer)
            if answer is None:
                counts['missing'] += 1
            else:
                counts['answered'] += 1
                counts['native_answers'] += int(result.get('source') == 'native')
                counts['cache_hits'] += int(result.get('source') == 'cache')
                counts['ai_answers'] += int(result.get('source') == 'ai')
        plans.append((frame, snapshot, answers))
    # Unknown page structure or missing answers: preserve platform data.
    if counts['missing'] or counts['unsupported']:
        return {'status': 'needs_manual', 'message': '存在缺失答案或不支持题型，未填写或提交', 'counts': counts}
    for frame, snapshot, answers in plans:
        if not frame_visible(frame) or read_form(frame) != snapshot:
            return {'status': 'needs_manual', 'message': '答题期间页面发生变化，请刷新后重试；未提交', 'counts': counts}
        for question, answer in zip(snapshot['questions'], answers):
            if deadline is not None and time.monotonic() >= deadline:
                return {'status': 'needs_manual', 'message': '答题预算耗尽，未提交', 'counts': counts}
            if not fill_question(frame, question, answer):
                return {'status': 'needs_manual', 'message': '页面填写复核失败，未提交', 'counts': counts}
            counts['filled'] += 1
    # Recheck the whole page after all inputs, before selecting one native action.
    for frame, snapshot, answers in plans:
        if not frame_visible(frame) or read_form(frame) != snapshot or not all(verify_question(frame, q, a) for q, a in zip(snapshot['questions'], answers)):
            return {'status': 'needs_manual', 'message': '填写后页面或答案变化，未提交', 'counts': counts}
    names = ('提交', '提交作业', '提交答案', 'Submit') if submit else ('保存', '暂存', '保存答案', '保存作业', 'Save', 'Save answers')
    candidates = [(frame, button(frame, names)) for frame in frames if frame_visible(frame)]
    candidates = [(frame, candidate) for frame, candidate in candidates if candidate is not None]
    if not candidates and allow_tabs and advance_tab(page, frames, deadline):
        return {'status': 'advanced', 'message': '当前题页已填写复核，继续同一课节下一题页', 'counts': {**counts, 'pages_advanced': 1}}
    if len(candidates) != 1:
        return {'status': 'needs_manual', 'message': '已填写，但未找到唯一保存/提交按钮，平台保存状态未确认', 'counts': counts}
    frame, action = candidates[0]
    before = frame.evaluate(RECEIPT_SCRIPT)
    if deadline is not None and time.monotonic() >= deadline:
        return {'status': 'needs_manual', 'message': '答题预算耗尽，未点击保存/提交', 'counts': counts}
    # From this point, failure must never cause an automatic re-submit.
    try:
        confirmation = frame.locator('.cmd_submit:visible').filter(has_text=re.compile(r'^(?:Submit|提交|确认提交|确定提交)$'))
        had_confirmation = confirmation.count() > 0
        action.click(timeout=10000)
        page.wait_for_timeout(500)
        if submit and not had_confirmation and confirmation.count() == 1:
            confirmation.click(timeout=10000)
        page.wait_for_timeout(1500)
        receipt = frame.evaluate(RECEIPT_SCRIPT)
        marker = ('提交成功', '作业已提交', '本次共完成', '正确率', '课件已提交') if submit else ('保存成功', '已保存')
        if (receipt != before and any(text in receipt for text in marker)
                and not any(text in receipt for text in ('提交失败', '保存失败', '频繁', '操作过快', '验证码', '未答完', '未作答', '网络错误'))):
            counts['submitted' if submit else 'saved'] = 1
            return {'status': 'finished', 'message': '已收到平台提交回执' if submit else '已收到平台保存回执', 'counts': counts}
    except Exception:
        pass
    counts['submission_unknown'] = 1
    return {'status': 'needs_manual', 'message': '已点击保存/提交，但结果未确认；请在官网核对，勿直接重复提交', 'counts': counts}
