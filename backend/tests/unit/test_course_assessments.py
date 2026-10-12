"""Synthetic platform pages; no logged-in or paid service requests."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.api.v1.learning_accounts import AccountTask
from app.services.course.chaoxing import course_assessments as ca
from app.services.course.chaoxing import query_control as control

COURSE = {'courseId': '100', 'clazzId': '200', 'cpi': '300'}
WORK_URL = ca.HOST + '/mooc-ans/work/phone/doHomeWork?courseId=100&classId=200&cpi=300&workId=9'
EXAM_URL = ca.HOST + '/exam-ans/exam/test/reVersionTestStartNew?courseId=100&classId=200&cpi=300'


def hidden(**values):
    return ''.join(f'<input type="hidden" name="{k}" id="{k}" value="{v}">' for k, v in values.items())


def paper(kind='homework', qid='10', count=1, code='0', extra=''):
    identity = {'workRelationId': '9', 'workRelationAnswerId': '90'} if kind == 'homework' else {
        'testPaperId': '9', 'testUserRelationId': '90', 'enc': 'test-enc',
        'userId': '123456', 'encRemainTime': 'test-time', 'encLastUpdateTime': 'test-update'}
    wrap = 'singleQuesId' if kind == 'homework' else 'questionWrap'
    return '<form>' + hidden(courseId='100', classId='200', cpi='300', questionCount=count,
                            questionId=qid, **identity, **{f'type{qid}': code}) + (
        f'<div class="{wrap}" data="{qid}"><div class="tit">Question?</div>'
        '<div class="singleChoice" name="A"><cc>First, option.</cc></div>'
        '<div class="singleChoice" name="B"><cc>Second option</cc></div>'
        + extra + '</div></form>')


def list_page(kind='homework', status='未交'):
    entry = '/mooc-ans/work/phone/task-work' if kind == 'homework' else '/exam-ans/exam/phone/task-exam'
    return f'<ul class="nav"><li data="{entry}?taskrefId=9&courseId=100&clazzId=200&cpi=300"><p>Assessment</p><span>{status}</span></li></ul>'


@pytest.fixture
def service():
    session = Mock()
    session.get_adapter.return_value = SimpleNamespace(max_retries=3)
    session.post.return_value = SimpleNamespace(status_code=200, json=lambda: {'status': True})
    tiku = SimpleNamespace(DISABLE=False, COVER_RATE=.9, true_list=['是的'], false_list=['不是'],
                           query=Mock(return_value='First, option.'), get_submit_params=lambda: '')
    client = SimpleNamespace(session_manager=SimpleNamespace(get_session=lambda: session), tiku=tiku)
    return ca.CourseAssessmentService(client)


@pytest.mark.parametrize('kind', ['homework', 'exam'])
def test_ordinary_paper_answers_and_submits_once(service, kind):
    url = WORK_URL if kind == 'homework' else EXAM_URL
    service.get = Mock(side_effect=[(list_page(kind, '未交' if kind == 'homework' else '待考试'), ca.HOST),
                                    (paper(kind), url)] + ([(paper(kind), url)] if kind == 'exam' else []))
    result = service.run(COURSE, homework=kind == 'homework', exams=kind == 'exam')
    assert result == {'submitted': 1, 'saved': 0, 'skipped': 0, 'failed': 0}
    assert service.session.post.call_count == 1
    args = service.session.post.call_args.kwargs
    assert args['data']['answer10'] == 'A'
    assert args['data']['tempSave'] == 'false'
    assert args['allow_redirects'] is False
    assert service.session.get_adapter.return_value.max_retries == 3


def test_defaults_do_not_read_or_submit_assessments(service):
    service.get = Mock(side_effect=AssertionError('Unexpected platform request'))
    assert service.run(COURSE)['submitted'] == 0
    service.get.assert_not_called()
    service.session.post.assert_not_called()


@pytest.mark.parametrize('status', ['已提交', '已完成', '待批阅', '待互评', '已结束', '未开始', '待重做'])
def test_completed_unopened_and_retake_papers_are_never_entered(service, status):
    service.get = Mock(return_value=(list_page(status=status), ca.HOST))
    assert service.run(COURSE, homework=True)['skipped'] == 1
    assert service.get.call_count == 1
    service.session.post.assert_not_called()


def test_unknown_list_is_failure_not_successful_empty_course(service):
    service.get = Mock(return_value=('<html>Unsupported layout</html>', ca.HOST))
    assert service.run(COURSE, homework=True)['failed'] == 1
    service.session.post.assert_not_called()


def test_unknown_status_never_starts_exam(service):
    service.get = Mock(return_value=(list_page('exam', '未知'), ca.HOST))
    assert service.run(COURSE, exams=True)['failed'] == 1
    assert service.get.call_count == 1


@pytest.mark.parametrize('notice', ['请输入验证码', '人脸验证', '考试口令', '监考', '章节任务点未完成'])
def test_exam_verification_blocks_start(service, notice):
    service.get = Mock(side_effect=[(list_page('exam', '待考试'), ca.HOST),
                                    (hidden(testPaperId=9, testUserRelationId=90) + notice, ca.HOST)])
    assert service.run(COURSE, exams=True)['failed'] == 1
    assert service.get.call_count == 2
    service.tiku.query.assert_not_called()
    service.session.post.assert_not_called()


@pytest.mark.parametrize('url', ['http://mooc1.chaoxing.com/work/task-list', 'https://127.0.0.1/work/task-list',
    'https://mooc1.chaoxing.com.evil.test/work/task-list', 'https://user@mooc1.chaoxing.com/work/task-list',
    'https://mooc1.chaoxing.com:444/work/task-list', 'https://mooc1.chaoxing.com/unknown',
    'https://mooc1.chaoxing.com\\@127.0.0.1/work/task-list'])
def test_untrusted_addresses_rejected(url):
    with pytest.raises(ca.AssessmentUnavailable):
        ca.safe_url(url)


def test_redirect_to_other_host_is_rejected_before_credentials_sent(service):
    service.session.get.return_value = SimpleNamespace(status_code=302, headers={'Location': 'https://evil.test'})
    with pytest.raises(ca.AssessmentUnavailable):
        service.get(WORK_URL)
    assert service.session.get.call_count == 1


def test_other_course_or_class_cannot_be_entered(service):
    service.get = Mock(return_value=(list_page().replace('clazzId=200', 'clazzId=201'), ca.HOST))
    assert service.run(COURSE, homework=True)['failed'] == 1
    assert service.get.call_count == 1


def test_ambiguous_or_missing_answer_only_saves_without_random_answer(service):
    service.tiku.query.return_value = 'Cannot determine'
    service.get = Mock(side_effect=[(list_page(), ca.HOST), (paper(), WORK_URL)])
    result = service.run(COURSE, homework=True)
    assert result['saved'] == 1 and result['failed'] == 1 and result['submitted'] == 0
    data = service.session.post.call_args.kwargs['data']
    assert data['tempSave'] == 'true' and 'answer10' not in data


def test_save_only_never_finalizes_even_with_full_coverage(service):
    service.tiku.get_submit_params = lambda: '1'
    service.get = Mock(side_effect=[(list_page(), ca.HOST), (paper(), WORK_URL)])
    result = service.run(COURSE, homework=True)
    assert result['saved'] == 1 and result['failed'] == 0
    assert service.session.post.call_args.kwargs['data']['tempSave'] == 'true'


@pytest.mark.parametrize('kind', ['homework', 'exam'])
def test_all_pages_read_before_single_final_submission(service, kind):
    url = WORK_URL if kind == 'homework' else EXAM_URL
    service.get = Mock(side_effect=[(list_page(kind, '未交'), ca.HOST),
                                    (paper(kind, '10', 2), url), (paper(kind, '11', 2), url)] + ([(paper(kind, '10', 2), url), (paper(kind, '11', 2), url)] if kind == 'exam' else []))
    assert service.run(COURSE, homework=kind == 'homework', exams=kind == 'exam')['submitted'] == 1
    calls = service.session.post.call_args_list
    assert len(calls) == 2
    assert [call.kwargs['data']['tempSave'] for call in calls] == ['true', 'false']
    assert calls[1].kwargs['data']['answer11'] == 'A'


def test_repeated_question_page_never_submits_partial_paper(service):
    service.get = Mock(side_effect=[(list_page(), ca.HOST), (paper(count=2), WORK_URL), (paper(count=2), WORK_URL)])
    assert service.run(COURSE, homework=True)['failed'] == 1
    service.session.post.assert_not_called()
    service.tiku.query.assert_not_called()


def test_submission_connection_error_is_not_retried_or_logged_with_tokens(service):
    service.session.post.side_effect = RuntimeError('secret-token-in-url')
    logs = []
    service.get = Mock(side_effect=[(list_page(), ca.HOST), (paper(), WORK_URL)])
    assert service.run(COURSE, homework=True, report=lambda *args: logs.append(args))['failed'] == 1
    assert service.session.post.call_count == 1
    assert 'secret-token' not in str(logs)
    assert service.session.get_adapter.return_value.max_retries == 3


def test_cancellation_propagates_and_never_submits(service):
    service.tiku.query.side_effect = control.QueryCancelled()
    service.get = Mock(side_effect=[(list_page(), ca.HOST), (paper(), WORK_URL)])
    with pytest.raises(control.QueryCancelled):
        service.run(COURSE, homework=True)
    service.session.post.assert_not_called()


def test_exam_disabled_does_not_fetch_it_when_homework_enabled(service):
    service.get = Mock(return_value=('<p>暂无作业</p>', ca.HOST))
    assert service.run(COURSE, homework=True)['failed'] == 0
    assert service.get.call_count == 1
    assert '/work/task-list' in service.get.call_args.args[0]


def test_unknown_total_question_count_never_submits_first_page(service):
    html = paper().replace(hidden(questionCount=1), '')
    with pytest.raises(ca.AssessmentUnavailable, match='总题数'):
        ca.parse_paper(html, WORK_URL, 'homework', COURSE)


def test_multi_blank_exact_field_mapping_and_order(service):
    html = paper(code='2', extra='<textarea name="answer101"></textarea><textarea name="answer102"></textarea>')
    question = ca.parse_paper(html, WORK_URL, 'homework', COURSE)['questions'][0]
    assert ca.answer_fields(question, 'first\nsecond', service.tiku, 'homework') == {'answer101': 'first', 'answer102': 'second'}
    assert ca.answer_fields(question, 'only one', service.tiku, 'homework') is None


def test_exact_option_text_wins_over_letter_like_punctuation(service):
    question = ca.parse_paper(paper(), WORK_URL, 'homework', COURSE)['questions'][0]
    assert ca.answer_fields(question, 'First, option.', service.tiku, 'homework') == {'answer10': 'A'}
    assert ca.answer_fields(question, 'AB', service.tiku, 'homework') is None


def test_strict_boolean_scope_and_old_saved_config_compatibility():
    assert AccountTask(course_ids=['100']).learn_chapters is True
    assert AccountTask(course_ids=['100']).course_homework is False
    assert AccountTask(course_ids=['100'], learn_chapters=False, course_exams=True).course_exams is True
    with pytest.raises(ValueError):
        AccountTask(course_ids=['100'], learn_chapters=False)
    with pytest.raises(ValueError):
        AccountTask(course_ids=['100'], course_exams='false')


def test_exam_signature_matches_independent_javascript_vector(monkeypatch):
    monkeypatch.setattr(ca.time, 'time', lambda: 1700000000)
    digits = iter([1, 2])
    monkeypatch.setattr(ca.secrets, 'randbelow', lambda _: next(digits))
    monkeypatch.setattr(ca.secrets, 'token_hex', lambda n: '00' * n)
    monkeypatch.setattr(ca.secrets, 'randbits', lambda _: 0)
    signature = ca.exam_signature('123456', '10')
    assert signature['pos'] == 'beb032bb29db4c1af600000000'
    assert signature['_edt'] == '1700000000000124'
    assert signature['value'] == '%28NaN%7CNaN%29'
    assert signature['qid'] == '10'
    assert 'monitorStatus' not in signature


def test_conflicting_duplicate_scope_parameters_are_rejected():
    with pytest.raises(ca.AssessmentUnavailable):
        ca.safe_url(WORK_URL + '&courseId=999')


def test_exam_refresh_detects_changed_options_before_post(service):
    service.get = Mock(side_effect=[(list_page('exam', '待考试'), ca.HOST),
                                   (paper('exam'), EXAM_URL),
                                   (paper('exam').replace('Second option', 'Different option'), EXAM_URL)])
    assert service.run(COURSE, exams=True)['failed'] == 1
    service.session.post.assert_not_called()


def test_entry_total_cannot_be_replaced_by_a_single_page_count():
    parsed = ca.parse_paper(paper(count=1), WORK_URL, 'homework', COURSE, expected_count=5)
    assert parsed['count'] == 5


def test_identically_worded_options_are_not_ambiguous_single_choice(service):
    question = ca.parse_paper(paper().replace('Second option', 'First, option.'), WORK_URL, 'homework', COURSE)['questions'][0]
    assert ca.answer_fields(question, 'First, option.', service.tiku, 'homework') is None


def test_option_content_starting_with_its_letter_is_preserved():
    question = ca.parse_paper(paper().replace('First, option.', 'Australia'), WORK_URL, 'homework', COURSE)['questions'][0]
    assert question['_options'][0] == ('A', 'Australia')


def test_failed_early_page_save_never_finalizes_last_page(service):
    service.get = Mock(side_effect=[(list_page(), ca.HOST), (paper(qid='10', count=2), WORK_URL), (paper(qid='11', count=2), WORK_URL)])
    service.session.post.return_value.json = lambda: {'status': False}
    assert service.run(COURSE, homework=True)['failed'] == 1
    assert service.session.post.call_count == 1
    assert service.session.post.call_args.kwargs['data']['tempSave'] == 'true'
