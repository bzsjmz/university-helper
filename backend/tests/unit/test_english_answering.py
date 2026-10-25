import json
from unittest.mock import Mock

import pytest

from app.services.course.english import answering as answers
from app.services.course.english import forms
from app.services.course.english.answering import EnglishSolver, normalize_answer, validate_question


def question(kind='single', **kwargs):
    return validate_question({'type': kind, 'title': 'Choose the correct word.',
        'options': ['Apple', 'Babbage machine'] if kind in ('single', 'multiple') else [],
        'count': 1, **kwargs})


@pytest.mark.parametrize('raw,expected', [('A', [0]), ('Babbage machine', [1]), ('B.', None),
    ('Because it is correct', None), ('Z', None), ('', None), ('A\nB', None)])
def test_single_choice_never_guesses_first_letter(raw, expected):
    assert normalize_answer(question(), raw) == expected


def test_blank_count_and_order_are_exact():
    q = question('completion', count=2)
    assert normalize_answer(q, 'one\ntwo') == ['one', 'two']
    assert normalize_answer(q, 'one') is None
    assert normalize_answer(q, ['one', '']) is None
    assert normalize_answer(question('multiple'), 'AB') == [0, 1]


@pytest.fixture
def solver(tmp_path, monkeypatch):
    from app.services.course.chaoxing.answer_cache import CacheDAO
    path = str(tmp_path / 'english-cache.json')
    monkeypatch.setattr(answers, 'CacheDAO', lambda: CacheDAO(path))
    provider = Mock()
    provider._query.return_value = 'B'
    provider._httpx_client = None
    monkeypatch.setattr(answers, 'get_provider', lambda: provider)
    return EnglishSolver('welearn', 'c', True), provider


def test_cache_reuses_answer_and_respects_option_order(solver):
    solver, provider = solver
    q = question()
    assert solver.solve(q) == {'answer': [1], 'source': 'ai'}
    assert solver.solve(q) == {'answer': [1], 'source': 'cache'}
    assert provider._query.call_count == 1
    q['options'].reverse()
    solver.solve(q)
    assert provider._query.call_count == 2


def test_failed_shape_not_persisted_and_cache_only_never_calls_ai(solver):
    solver, provider = solver
    provider._query.return_value = 'B. maybe'
    assert solver.solve(question())['answer'] is None
    provider._query.return_value = 'A'
    assert solver.solve(question())['answer'] == [0]
    solver.allow_ai = False
    assert solver.solve(question(title='new question'))['answer'] is None
    assert provider._query.call_count == 2


def test_cancelled_question_has_no_paid_attempt(solver):
    solver, provider = solver
    solver.should_stop = lambda: True
    assert solver.solve(question())['answer'] is None
    provider._query.assert_not_called()


def test_new_task_gets_new_provider_but_reuses_shared_request_gate(monkeypatch):
    candidates = []
    def provider():
        instance = Mock()
        candidates.append(instance)
        return instance
    monkeypatch.setattr(answers, 'AI', provider)
    one, two = answers.get_provider(), answers.get_provider()
    assert one is not two
    assert one._request_semaphore is two._request_semaphore


def fake_page(monkeypatch, *, missing=False, unsupported=0, submit=True, receipt='提交成功'):
    frame = Mock(parent_frame=None)
    snapshot = {'questions': [{**question(), 'controls': ['0','1'], 'control_types': ['radio','radio'], 'values': []}],
                'unsupported': unsupported, 'blocked': False}
    monkeypatch.setattr(forms, 'read_form', lambda f: snapshot)
    monkeypatch.setattr(forms, 'fill_question', Mock(return_value=True))
    monkeypatch.setattr(forms, 'verify_question', Mock(return_value=True))
    action = Mock()
    frame.locator.return_value.count.return_value = 0
    frame.locator.return_value.all_text_contents.return_value = []
    frame.locator.return_value.filter.return_value.count.return_value = 0
    monkeypatch.setattr(forms, 'button', lambda f, names: action)
    frame.evaluate.side_effect = ['', receipt]
    page = Mock(frames=[frame])
    solve = Mock(return_value={'answer': None if missing else [0], 'source': 'ai'})
    result = forms.answer_page(page, solve, submit=submit)
    return result, action


def test_partial_or_unknown_form_never_fills_or_submits(monkeypatch):
    result, action = fake_page(monkeypatch, missing=True)
    assert result['counts']['missing'] == 1
    forms.fill_question.assert_not_called()
    action.click.assert_not_called()
    result, action = fake_page(monkeypatch, unsupported=1)
    assert result['status'] == 'needs_manual'
    action.click.assert_not_called()


def test_submission_requires_new_platform_receipt(monkeypatch):
    result, action = fake_page(monkeypatch)
    assert result['counts']['submitted'] == 1
    action.click.assert_called_once()
    result, action = fake_page(monkeypatch, receipt='')
    assert result['counts']['submitted'] == 0
    assert result['counts']['submission_unknown'] == 1
    action.click.assert_called_once()


def test_save_and_submit_are_distinct(monkeypatch):
    result, action = fake_page(monkeypatch, submit=False, receipt='保存成功')
    assert result['counts']['saved'] == 1
    assert result['counts']['submitted'] == 0


def test_post_fill_recheck_failure_prevents_submit(monkeypatch):
    # Override only the second verification step, after the native fill.
    original = forms.answer_page
    frame = Mock(parent_frame=None)
    snapshot = {'questions': [{**question(), 'controls': ['0','1'], 'values': []}], 'unsupported': 0, 'blocked': False}
    monkeypatch.setattr(forms, 'read_form', lambda f: snapshot)
    monkeypatch.setattr(forms, 'fill_question', Mock(return_value=True))
    monkeypatch.setattr(forms, 'verify_question', Mock(return_value=False))
    action = Mock()
    frame.locator.return_value.count.return_value = 0
    frame.locator.return_value.all_text_contents.return_value = []
    frame.locator.return_value.filter.return_value.count.return_value = 0
    monkeypatch.setattr(forms, 'button', lambda f, names: action)
    result = original(Mock(frames=[frame]), lambda q: {'answer': [0]}, submit=True)
    assert result['status'] == 'needs_manual'
    action.click.assert_not_called()


def test_native_error_receipt_is_not_a_success(monkeypatch):
    result, action = fake_page(monkeypatch, receipt='正确率 0% 提交失败 未作答')
    assert result['counts']['submitted'] == 0
    assert result['counts']['submission_unknown'] == 1
    action.click.assert_called_once()


def test_parent_child_answer_channel_without_browser_or_api(monkeypatch):
    import subprocess
    import sys
    from app.services.course.english.manager import EnglishManager
    manager = EnglishManager()
    child_script = '''import json,sys
request=json.loads(sys.stdin.buffer.readline())
print(json.dumps({'event':'question','question':{'type':'single','title':'fixture','options':['one','two'],'count':1}}),flush=True)
answer=json.loads(sys.stdin.buffer.readline())
assert answer['answer']==[0]
print(json.dumps({'ok':True,'status':'finished','message':'fixture done','counts':{'answered':1}}),flush=True)
'''
    monkeypatch.setattr(manager, '_spawn', lambda: subprocess.Popen(
        [sys.executable, '-B', '-c', child_script], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True))
    solver = Mock()
    solver.solve.return_value = {'answer': [0], 'source': 'cache'}
    monkeypatch.setattr(answers, 'EnglishSolver', lambda *args: solver)
    result = manager.execute({'platform':'welearn', 'action':'answer', 'course':'c', 'seconds':30})
    assert result['status'] == 'finished'
    solver.solve.assert_called_once()
    solver.close.assert_called_once()
    assert not manager.processes


def test_fill_and_read_back_use_native_controls():
    class Input:
        def __init__(self):
            self.checked = False
            self.value = ''
        def check(self): self.checked = True
        def uncheck(self): self.checked = False
        def is_checked(self): return self.checked
        def fill(self, value): self.value = value
        def dispatch_event(self, name): pass
        def input_value(self): return self.value
        def select_option(self, value): self.value = value
    controls = {'0': Input(), '1': Input()}
    frame = Mock(parent_frame=None)
    frame.locator.side_effect = lambda selector: controls[selector.split('"')[1]]
    q = {**question(), 'controls': ['0', '1'], 'values': []}
    assert forms.fill_question(frame, q, [1]) is True
    assert controls['1'].checked and not controls['0'].checked
    assert forms.verify_question(frame, q, [1]) is True
    controls['1'].checked = False
    assert forms.verify_question(frame, q, [1]) is False
    q = {**question('completion', count=2), 'controls': ['0', '1'], 'values': []}
    assert forms.fill_question(frame, q, ['word one', 'word two'])
    assert forms.verify_question(frame, q, ['word one', 'word two'])
    q = {**question(), 'controls': ['0'], 'values': ['past', 'future']}
    assert forms.fill_question(frame, q, [1])
    assert controls['0'].value == 'future'


def test_et_talk_reports_recording_requirement_instead_of_generic_zero_questions(monkeypatch):
    from app.services.course.english import forms
    frame = Mock()
    frame.evaluate.return_value = False
    frame.locator.return_value.count.return_value = 1
    monkeypatch.setattr(forms, 'frame_visible', lambda frame: True)
    monkeypatch.setattr(forms, 'read_form', lambda frame: {'questions': [], 'unsupported': 0, 'blocked': False})
    solve = Mock()
    result = forms.answer_page(Mock(frames=[frame]), solve, allow_reading=True)
    assert result['status'] == 'needs_manual' and '口语' in result['message']
    solve.assert_not_called()


@pytest.mark.parametrize('value,expected', [
    ('已完成', True), ('<span class="done">已完成</span>', True),
    ('未完成', False), ('<span>未完成</span>', False), ('尚未完成', False),
    ('true', True), (True, True), (1, True), ('false', False), (None, False),
    ('已完成 1/2', False), ('已通过', True), ('未通过', False),
])
def test_welearn_completion_status_uses_exact_platform_value(value, expected):
    from app.services.course.english.clients import completed_status
    assert completed_status(value) is expected


def test_et_rejection_reports_unfinished_recordings_without_exposing_body():
    frame = Mock()
    frame.locator.return_value.all_text_contents.return_value = ['还有3题未完成，请全部完成再提交！']
    frame.locator.return_value.count.return_value = 3
    message = forms.platform_rejection(frame)
    assert '还有 3 题未完成' in message
    assert '口语录音' in message


def test_et_confirmation_requires_unique_known_title():
    frame = Mock()
    dialog = frame.locator.return_value
    dialog.count.return_value = 1
    dialog.locator.return_value.inner_text.return_value = '确认提交？'
    candidate = dialog.get_by_role.return_value
    candidate.count.return_value = 1
    candidate.is_enabled.return_value = True
    assert forms.native_et_confirmation(frame) is candidate
    dialog.locator.return_value.inner_text.return_value = '重新作答？'
    assert forms.native_et_confirmation(frame) is None
    dialog.count.return_value = 2
    assert forms.native_et_confirmation(frame) is None


def test_native_submission_rejection_is_not_unknown_or_success(monkeypatch):
    monkeypatch.setattr(forms, 'platform_rejection', lambda frame: '平台拒绝提交：还有 3 题未完成')
    result, action = fake_page(monkeypatch)
    assert result['counts']['submission_rejected'] == 1
    assert result['counts']['submission_unknown'] == 0
    assert result['counts']['submitted'] == 0
    action.click.assert_called_once()
