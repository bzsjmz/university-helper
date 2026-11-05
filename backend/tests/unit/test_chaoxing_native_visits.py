"""Offline scope, platform confirmation, cancellation and worker isolation tests."""
import copy
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from app.services.course.chaoxing import native_visits as review
from app.services.course.chaoxing import query_control as control
from app.services.course.chaoxing.course_grades import CourseGradeService
from app.services.course.chaoxing.course_service import ChaoxingCourseService
from app.services.course.chaoxing.native_visit_protocol import (
    NativeVisitError, permitted_resource, validate_course, validate_native_url, validate_point,
)

COURSE = {'courseId': '1', 'clazzId': '2', 'cpi': '3', 'title': 'Example'}
ENTRY = 'https://mooc1.chaoxing.com/mooc-ans/visit/stucoursemiddle?courseid=1&clazzid=2&cpi=3&enc=private'
POINTS = [{'id': '10', 'title': 'Done', 'has_finished': True},
          {'id': '20', 'title': 'Not done', 'has_finished': False}]


def grade(visits=18, *, weight=20, threshold=20, total=55):
    return {'state': 'verified', 'selected_passing_score': 60, 'total_score': total,
            'meets_selected_passing_score': total >= 60,
            'parts': {'visitTime': {'weighted_score': visits, 'weight_percent': weight}},
            'chapter_visits': {'applicable': weight > 0, 'full_mark_count': threshold,
                               'full_mark_reached': weight > 0 and visits == weight}}


class Clock:
    now = 0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class Grades:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0
    def read(self, *args):
        self.calls += 1
        return copy.deepcopy(self.values.pop(0) if len(self.values) > 1 else self.values[0])


class Client:
    def get_course_point(self, *args):
        # A completed point outside the selected course scope must not be used.
        return {'points': POINTS + [{'id': '30', 'has_finished': True}]}


def run_review(grades, *, enabled=True, partial=False, checkpoint=lambda: None, browser_error=None):
    visits, states = [], []
    class Browser:
        def __init__(self, client, course, points, check):
            assert [p['id'] for p in points] == ['10']
            self.check = check
        def __enter__(self):
            if browser_error:
                raise browser_error
            return self
        def __exit__(self, *args):
            states.append('closed')
        def visit(self, point):
            self.check()
            visits.append(point['id'])
    clock = Clock()
    runner = review.ChapterVisitReview(Client(), grade_service=grades, browser_factory=Browser,
                                      clock=clock, sleep=clock.sleep)
    result = runner.run(COURSE, POINTS, enabled=enabled, partial=partial, passing_score=60,
                        checkpoint=checkpoint, progress=lambda value: states.append(value['state']))
    return result, visits, states, clock


@pytest.mark.parametrize('value,enabled,partial,state', [
    ({'state': 'unverified'}, True, False, 'unverified'),
    (grade(), False, False, 'not_requested'),
    (grade(), True, True, 'partial_scope'),
    (grade(0, weight=0), True, False, 'not_graded'),
    (grade(20), True, False, 'full'),
    (grade(threshold=None), True, False, 'unknown_rule'),
])
def test_no_browsing_without_verified_rule_scope_and_need(value, enabled, partial, state):
    result, visits, states, _ = run_review(Grades(value), enabled=enabled, partial=partial)
    assert result['review_state'] == state
    assert visits == states == []


def test_only_platform_grade_can_confirm_full_marks():
    result, visits, states, _ = run_review(Grades(grade(), grade(20, total=61)))
    assert visits == ['10'] * 3
    assert result['visited_pages'] == 3  # Explicitly not a claimed platform visit count.
    assert result['score'] == 61 and result['review_state'] == 'full'
    assert result['meets_target'] is True
    assert states[-1] == 'closed'


def test_cache_wait_does_not_repeat_pages_when_platform_never_confirms():
    result, visits, states, clock = run_review(Grades(grade()))
    assert len(visits) == 3
    assert result['review_state'] == 'unconfirmed'
    assert result['score'] == 55 and result['meets_target'] is False
    assert clock.now == pytest.approx(review.GRADE_REFRESH_WAIT)
    assert 'waiting_grade' in states and states[-1] == 'closed'


def test_progress_already_observed_replans_without_waiting_for_another_increment():
    result, visits, states, clock = run_review(Grades(grade(), grade(19), grade(20)))
    assert len(visits) == 5
    assert result['review_state'] == 'full'
    assert clock.now == 0


def test_delayed_grade_refresh_is_used_instead_of_local_count():
    result, visits, states, clock = run_review(Grades(grade(), grade(), grade(20)))
    assert len(visits) == 3 and result['review_state'] == 'full'
    assert clock.now == pytest.approx(15)


def test_unverified_after_visits_never_claims_success():
    result, visits, states, _ = run_review(Grades(grade(), {'state': 'unverified'}))
    assert result['state'] == 'unverified' and 'score' not in result
    assert len(visits) == 3 and states[-1] == 'closed'


def test_browser_manual_verification_returns_warning_without_answering():
    result, visits, _, _ = run_review(Grades(grade()), browser_error=NativeVisitError('manual_required'))
    assert result['review_state'] == 'unavailable' and not visits
    assert result['score'] == 55


def test_cancellation_propagates_out_of_browser_and_releases_it():
    checks = []
    def checkpoint():
        checks.append(True)
        if len(checks) == 4:
            raise control.QueryCancelled()
    with pytest.raises(control.QueryCancelled):
        run_review(Grades(grade()), checkpoint=checkpoint)


@pytest.mark.parametrize('url', [
    ENTRY.replace('courseid=1', 'courseid=9'), ENTRY + '&courseId=1',
    ENTRY + '&courseid=1', ENTRY.replace('https:', 'http:'),
    ENTRY.replace('mooc1.chaoxing.com', 'mooc1.chaoxing.com.attacker.example'),
    ENTRY.replace('mooc1.chaoxing.com', 'private@mooc1.chaoxing.com'),
    ENTRY.replace('stucoursemiddle', 'exam/start'),
])
def test_native_entry_never_accepts_wrong_scope_or_host(url):
    with pytest.raises(NativeVisitError):
        validate_native_url(url, COURSE)


@pytest.mark.parametrize('value', ['../10', '10] a', '', None, 'x' * 65])
def test_chapter_selector_cannot_escape(value):
    with pytest.raises(NativeVisitError):
        validate_point(value)


@pytest.mark.parametrize('url,method', [
    ('https://mooc1.chaoxing.com/exam-ans/exam/start', 'GET'),
    ('https://mooc1.chaoxing.com/work/phone/doHomeWork', 'GET'),
    ('https://mooc1.chaoxing.com/mycourse/studentstudy', 'POST'),
    ('http://127.0.0.1/', 'GET'), ('https://other.example/a', 'GET'),
])
def test_worker_blocks_external_targets_and_assessment_actions(url, method):
    assert not permitted_resource(url, method)


def test_native_resources_and_course_validation():
    assert validate_native_url(ENTRY, COURSE) == ENTRY
    assert permitted_resource('https://fystat-ans.chaoxing.com/log/setlog?native=1')
    assert validate_course(COURSE) == {k: COURSE[k] for k in ('courseId', 'clazzId', 'cpi')}
    with pytest.raises(NativeVisitError):
        validate_course({**COURSE, 'cpi': '../3'})


def test_signed_entry_is_private_and_matched_to_account_course():
    service = ChaoxingCourseService()
    service.remember_native_entries(f'<div class="course"><input class="courseId" value="1">'
        f'<input class="clazzId" value="2"><a href="{ENTRY}">Example</a></div>')
    assert service.native_course_entry(COURSE) == ENTRY
    assert service.native_course_entry({**COURSE, 'cpi': '9'}) is None


def test_grade_read_does_not_follow_auth_redirect_or_expose_body():
    requests = []
    def get(url, **kwargs):
        requests.append((url, kwargs))
        return SimpleNamespace(status_code=302, text='private-sentinel')
    client = SimpleNamespace(session_manager=SimpleNamespace(get_session=lambda: SimpleNamespace(get=get)))
    result = CourseGradeService(client).read(COURSE)
    assert result['state'] == 'unverified' and 'private-sentinel' not in str(result)
    assert requests[0][1]['allow_redirects'] is False
    assert requests[0][1]['params'] == {'courseid': '1', 'clazzid': '2', 'cpi': '3', 'ut': 's'}


def test_pipe_worker_isolated_environment_private_tmp_and_cleanup(monkeypatch, tmp_path):
    from requests.cookies import RequestsCookieJar
    cookies = RequestsCookieJar()
    cookies.set('session', 'private-cookie', domain='.chaoxing.com')
    cookies.set('other', 'must-not-cross', domain='.other.example')
    cookies.set('flat_session', 'flattened-login-cookie')
    client = SimpleNamespace(course_service=SimpleNamespace(native_course_entry=lambda _: ENTRY),
        session_manager=SimpleNamespace(get_session=lambda: SimpleNamespace(cookies=cookies)))
    monkeypatch.setenv('JWT_SECRET', 'private-secret')
    monkeypatch.setattr(review, 'temporary_root', lambda: tmp_path)
    # A real child and pipes exercise EOF, independent environment and teardown.
    child = '''import json, os, sys
payload=json.loads(sys.stdin.readline())
assert 'JWT_SECRET' not in os.environ
assert payload['cookies'][0]['value']=='private-cookie' and len(payload['cookies'])==2
assert payload['cookies'][1]['value']=='flattened-login-cookie'
assert payload['cookies'][1]['domain']=='.chaoxing.com'
assert os.stat(os.environ['TMPDIR']).st_mode & 0o777 == 0o700
print(json.dumps({'state':'ready'}),flush=True)
for line in sys.stdin:
 print(json.dumps({'state':'visited','log_observed':True}),flush=True)
'''
    monkeypatch.setattr(review, 'worker_command', lambda: [sys.executable, '-I', '-B', '-c', child])
    with review.NativeBrowserClient(client, COURSE, POINTS, lambda: None) as browser:
        process = browser.process
        assert process.poll() is None
        assert Path(browser.temporary.name).parent == tmp_path
        browser.visit(POINTS[0])
    assert process.poll() is not None
    assert not list(tmp_path.iterdir())
    assert review._BROWSER_SLOT.acquire(blocking=False)
    review._BROWSER_SLOT.release()


def test_frozen_worker_dispatch_uses_same_packaged_executable(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert review.worker_command() == [sys.executable, '--chaoxing-visit-worker']


@pytest.mark.parametrize('kwargs', [
    {'passing_score': float('nan')}, {'passing_score': float('inf')},
    {'passing_score': -1}, {'passing_score': 101},
    {'verify_grades': 'false'}, {'review_chapter_visits': 'false'},
])
def test_new_grade_api_parameters_reject_ambiguous_flags_or_invalid_target(kwargs):
    from pydantic import ValidationError
    from app.api.v1.course import CourseStartRequest
    with pytest.raises(ValidationError):
        CourseStartRequest(platform='chaoxing', username='fixture', password='fixture', **kwargs)


def test_broken_child_reply_releases_slot_and_removes_temporary_state(monkeypatch, tmp_path):
    from requests.cookies import RequestsCookieJar
    client = SimpleNamespace(course_service=SimpleNamespace(native_course_entry=lambda _: ENTRY),
        session_manager=SimpleNamespace(get_session=lambda: SimpleNamespace(cookies=RequestsCookieJar())))
    monkeypatch.setattr(review, 'temporary_root', lambda: tmp_path)
    monkeypatch.setattr(review, 'worker_command', lambda: [sys.executable, '-I', '-B', '-c',
        "import sys;sys.stdin.readline();print('{invalid}',flush=True)"])
    with pytest.raises(ValueError):
        with review.NativeBrowserClient(client, COURSE, POINTS, lambda: None):
            pass
    assert not list(tmp_path.iterdir())
    assert review._BROWSER_SLOT.acquire(blocking=False)
    review._BROWSER_SLOT.release()
