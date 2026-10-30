"""Native form answering with an explicit WE Learn recording experiment."""
import re
import time
from pathlib import Path

FORM_SCRIPT = Path(__file__).with_name('question_form.js').read_text(encoding='utf-8')
# Deliberately narrow: a click is not proof of a successful save/submission.
RECEIPT_SCRIPT = r"""() => {
 const visible = e => !!e?.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
 const receipts = Array.from(document.querySelectorAll(
  '[role=dialog], .ant-modal-content, .completion, .result, .save-success, .submit-success, [data-submitted], et-button[success-text="Submitted"] button'
 )).filter(visible).map(e => e.matches('et-button[success-text="Submitted"] button') && (e.innerText || '').trim() === 'Submitted' ? '课件已提交' : e.hasAttribute('data-submitted') && ['', 'true', '1', 'submitted'].includes(e.getAttribute('data-submitted')) ? '课件已提交' : (e.innerText || ''));
 for (const item of document.querySelectorAll('et-item')) {
   const reset = item.querySelector('et-button[action="item.reset()"] button');
   const submit = item.querySelector('et-button[action="item.submit()"] button');
   if (visible(reset) && submit && !visible(submit)) receipts.push('课件已提交');
 }
 return receipts.join('\n').slice(0,10000);
}"""


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
    return locator.evaluate("e => e.getAttribute('aria-checked') === 'true' || e.getAttribute('aria-selected') === 'true' || e.getAttribute('data-selected') === 'true' || ['selected','checked','active','ui-state-active','chosen'].some(c => e.classList.contains(c))")


def control_value(locator, kind):
    if kind == 'welearn-match':
        return locator.inner_text().strip()
    return locator.inner_text() if kind == 'contenteditable' else locator.input_value()


def matching_value_equal(locator, expected):
    actual = control_value(locator, 'welearn-match')
    if actual == expected:
        return True
    # The authored i/g flags explicitly allow case-insensitive answers. Other
    # text inputs and case-sensitive matching blanks retain exact comparison.
    insensitive = locator.evaluate("e => !!e.closest('et-blank[i],et-blank[g]')")
    return insensitive and re.sub(r'\s+', ' ', actual).strip().lower() == re.sub(r'\s+', ' ', expected).strip().lower()


def fill_matching(frame, locator, value):
    """Use the courseware picker, respecting its declared comparison rules."""
    if matching_value_equal(locator, value):
        return True
    locator.click()
    picker = frame.locator('.optionsPicker:visible')
    if picker.count() != 1:
        return False
    options = picker.locator('[option], [preoption]')
    insensitive = locator.evaluate("e => !!e.closest('et-blank[i],et-blank[g]')")
    matches = options.evaluate_all(r'''(es, args) => {
      const norm = s => (s || '').replace(/\s+/g,' ').trim();
      const values = es.map(e => e.hasAttribute('preoption') ? norm(e.textContent).slice(0,1) :
        norm(e.getAttribute('option') || e.textContent));
      const key = norm(args.answer);
      const exact = values.map((value,i) => value === key ? i : -1).filter(i => i >= 0);
      if (exact.length || !args.insensitive) return exact;
      return values.map((value,i) => !es[i].hasAttribute('preoption') &&
        value.toLowerCase() === key.toLowerCase() ? i : -1).filter(i => i >= 0);
    }''', {'answer': value, 'insensitive': insensitive})
    if len(matches) != 1:
        return False
    options.nth(matches[0]).click()
    return matching_value_equal(locator, value)


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
        if control_kind == 'welearn-match':
            if not fill_matching(frame, locator, value):
                return False
            continue
        locator.fill(value)
        if control_kind == 'contenteditable':
            locator.dispatch_event('input')
            locator.dispatch_event('keyup', {'key': 'Unidentified'})
            locator.press('Tab')
        locator.dispatch_event('change')
    return all(matching_value_equal(locator, value) if kind == 'welearn-match' else control_value(locator, kind) == value
               for locator, value, kind in zip(controls, answer, kinds))


def verify_question(frame, question, answer):
    controls = [frame.locator(f'[data-uh-control="{i}"]') for i in question['controls']]
    if question['type'] in ('single', 'multiple'):
        if question.get('control_types', [None])[0] == 'welearn-option':
            return all(custom_selected(locator) == (i in answer) for i, locator in enumerate(controls))
        if question['values']:
            return controls[0].input_value() == question['values'][answer[0]]
        return all(locator.is_checked() == (i in answer) for i, locator in enumerate(controls))
    return all(matching_value_equal(locator, value) if kind == 'welearn-match' else control_value(locator, kind) == value
               for locator, value, kind in zip(controls, answer, question.get('control_types', ['text'] * len(controls))))


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


def platform_rejection(frame):
    # Fixed known messages only; never echo a body containing questions/answers.
    text = frame.locator('div.tipsClass:visible').all_text_contents()
    for value in text:
        missing = re.search(r'还有\s*(\d{1,3})\s*题未完成', value)
        if missing:
            count = int(missing[1])
            recording = frame.locator('et-button.recorder:visible').count()
            detail = '，本页还含口语录音要求' if recording else ''
            return f'平台拒绝提交：还有 {count} 题未完成{detail}；已填写的文字未计为整节提交成功'
    return None


def native_et_confirmation(frame):
    dialogs = frame.locator('.ng-confirm-box:visible')
    if dialogs.count() != 1:
        return None
    title = dialogs.locator('.ng-confirm-title').inner_text().strip()
    if title not in ('确认提交？', '确认提交?'):
        return None
    button = dialogs.get_by_role('button', name='继续提交', exact=True)
    return button if button.count() == 1 and button.is_enabled() else None


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


def answer_page(page, solve, *, submit=False, deadline=None, native=None, frames=None, allow_reading=False, allow_tabs=False, recording_bypass=False):
    """All frames are checked before filling; one unknown control blocks submission.

    Default callers handle one selected page. WE Learn may opt into its
    declared ET tab pager; no caller retakes a paper or retries an uncertain click.
    """
    counts = dict(questions=0, answered=0, missing=0, unsupported=0, filled=0,
                  saved=0, submitted=0, submission_unknown=0, native_answers=0, cache_hits=0, ai_answers=0)
    frames = list(page.frames) if frames is None else list(frames)
    forms = []
    recordings = 0
    recording_details = []
    recording_state_known = True
    snapshots = []
    for frame in frames:
        if not frame_visible(frame):
            continue
        snapshot = read_form(frame)
        if snapshot.get('blocked'):
            return {'status': 'needs_manual', 'message': '页面要求人工验证或尚未开放，未填写或提交', 'counts': counts}
        counts['unsupported'] += snapshot['unsupported']
        recording_state_known &= type(snapshot.get('recordings')) is int
        recordings += snapshot.get('recordings', 0)
        recording_details.extend(snapshot.get('recording_details', []))
        snapshots.append((frame, snapshot))
        if snapshot['questions']:
            forms.append((frame, snapshot))
            counts['questions'] += len(snapshot['questions'])
    bypass_frames = []
    if recording_bypass and submit and allow_reading and recording_state_known and recordings and not counts['unsupported']:
        from .welearn_recordings import bypass_recordings
        for frame, snapshot in snapshots:
            if not snapshot.get('recordings'):
                continue
            expected = bypass_recordings(frame)
            if expected is None or expected != snapshot['recordings']:
                return {'status': 'needs_manual', 'message': '当前录音组件不兼容实验处理，未提交', 'counts': counts}
            bypass_frames.append((frame, expected))
    recording_only_ready = (allow_reading and recording_state_known and not counts['questions'] and not counts['unsupported']
                            and (not recordings or bypass_frames) and any(
                                frame.locator('et-recorder:visible, et-follow-me:visible, et-talk:visible').count() for frame, _ in snapshots))
    if not counts['questions'] and not recording_only_ready:
        if allow_tabs and not counts['unsupported'] and not recordings and advance_tab(page, frames, deadline):
            return {'status': 'advanced', 'message': '已访问课节内阅读页，继续下一题页', 'counts': {**counts, 'visited': 1, 'pages_advanced': 1}}
        if allow_reading and any(frame.locator('et-talk:visible, et-follow-me:visible, et-recorder:visible').count() for frame in frames):
            from .welearn_recordings import recording_message
            required = recordings or sum(frame.locator('et-talk:visible, et-follow-me:visible, et-recorder:visible').count() for frame in frames)
            return {'status': 'needs_manual', 'message': recording_message(recording_details, required),
                    'counts': {**counts, 'recordings_required': required}}
        if allow_reading and not counts['unsupported'] and any(frame.evaluate(r'''() => {
          const visible = e => !!e.getClientRects().length &&
            !['hidden','collapse'].includes(getComputedStyle(e).visibility) &&
            !e.closest('[hidden],[aria-hidden="true"]');
          const item = document.querySelector('et-item[notscored]');
          return !!item && (item.innerText || '').trim().length >= 30 &&
            ![...item.querySelectorAll('et-blank,et-choice,et-select,et-tof,et-matching,et-sort,et-recorder,et-follow-me,et-talk,input,textarea,select,[contenteditable],[data-controltype="filling"],[data-controltype="choice"]')].some(visible);
        }''') for frame in frames):
            return {'status': 'visited', 'message': '已访问无需填写的课件，正在核对平台进度',
                    'counts': {**counts, 'visited': 1}}
        message = '本页存在尚未适配的题目控件，未提交' if counts['unsupported'] else '未识别到支持的题目，未提交'
        return {'status': 'needs_manual', 'message': message, 'counts': counts}
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
    if allow_reading and recordings and not bypass_frames:
        from .welearn_recordings import recording_message
        return {'status': 'needs_manual',
                'message': recording_message(recording_details, recordings, mixed=True),
                'counts': {**counts, 'recordings_required': recordings}}
    if recording_only_ready and any(not frame_visible(frame) or read_form(frame) != snapshot
                                    for frame, snapshot in snapshots):
        return {'status': 'needs_manual', 'message': '录音页面状态变化，未保存或提交', 'counts': counts}
    if bypass_frames:
        if deadline is not None and time.monotonic() >= deadline:
            return {'status': 'needs_manual', 'message': '答题预算耗尽，未进行录音实验提交', 'counts': counts}
        for frame, expected in bypass_frames:
            applied = bypass_recordings(frame, apply=True)
            if applied != expected:
                return {'status': 'needs_manual', 'message': '录音实验处理未确认，未点击提交；请在官网核对', 'counts': counts}
            counts['recordings_bypassed'] = counts.get('recordings_bypassed', 0) + applied
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
        had_et_confirmation = frame.locator('.ng-confirm-box:visible').count() > 0
        action.click(timeout=10000)
        page.wait_for_timeout(500)
        rejected = platform_rejection(frame)
        if rejected:
            counts['submission_rejected'] = 1
            return {'status': 'needs_manual', 'message': rejected, 'counts': counts}
        if submit and not had_confirmation and confirmation.count() == 1:
            confirmation.click(timeout=10000)
        elif submit and not had_et_confirmation and frame.locator('et-item').count():
            et_confirmation = None
            for _ in range(8):
                et_confirmation = native_et_confirmation(frame)
                if et_confirmation is not None or (deadline is not None and time.monotonic() >= deadline):
                    break
                page.wait_for_timeout(250)
            if et_confirmation is not None:
                et_confirmation.click(timeout=10000)
        page.wait_for_timeout(1500)
        rejected = platform_rejection(frame)
        if rejected:
            counts['submission_rejected'] = 1
            return {'status': 'needs_manual', 'message': rejected, 'counts': counts}
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
