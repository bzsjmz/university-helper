"""Exercise pacing with a virtual clock; no real API calls or wall-clock waits."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.course.chaoxing import query_control as control
from app.services.course.chaoxing.answer_cache import CacheDAO
from app.services.course.english import answering


QUESTION = {'type': 'single', 'title': 'Choose a color.',
            'options': ['red', 'blue'], 'count': 1}


@pytest.fixture
def paced_solver(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1000.0, on_sleep=None)

    def sleep(seconds):
        clock.now += seconds
        if clock.on_sleep:
            clock.on_sleep()

    monkeypatch.setattr(control, 'time', SimpleNamespace(
        monotonic=lambda: clock.now, sleep=sleep))
    cache = CacheDAO(str(tmp_path / 'cache.json'))
    monkeypatch.setattr(answering, 'CacheDAO', lambda: cache)
    calls = []
    provider = Mock(_httpx_client=None)
    provider._query.side_effect = lambda q: calls.append(clock.now) or 'B'
    monkeypatch.setattr(answering, 'get_provider', lambda: provider)
    jitter = Mock(side_effect=[20.0, 40.0])
    monkeypatch.setattr(answering.random, 'uniform', jitter)
    return answering.EnglishSolver('welearn', 'fixture', True), clock, calls, jitter


def test_each_new_ai_question_waits_but_cached_answer_does_not(paced_solver):
    solver, clock, calls, jitter = paced_solver
    assert solver.solve(QUESTION) == {'answer': [1], 'source': 'ai'}
    assert calls == pytest.approx([1020.0])
    assert solver.solve(QUESTION)['source'] == 'cache'
    assert clock.now == pytest.approx(1020.0)
    assert solver.solve({**QUESTION, 'title': 'Another color?'})['source'] == 'ai'
    assert calls == pytest.approx([1020.0, 1060.0])
    assert jitter.call_args_list == [((20.0, 40.0),), ((20.0, 40.0),)]


def test_native_answers_native_cache_and_ai_disabled_skip_wait(paced_solver):
    solver, clock, calls, jitter = paced_solver
    assert solver.solve({**QUESTION, 'native_answer': [0]})['source'] == 'native'
    assert solver.solve(QUESTION)['source'] == 'cache'
    solver.allow_ai = False
    assert solver.solve({**QUESTION, 'title': 'Uncached?'})['answer'] is None
    assert clock.now == 1000.0 and not calls
    jitter.assert_not_called()


def test_other_english_platform_does_not_wait(paced_solver):
    solver, clock, calls, jitter = paced_solver
    solver.platform = 'unipus_ai'
    assert solver.solve(QUESTION)['source'] == 'ai'
    assert calls == [1000.0]
    jitter.assert_not_called()


def test_stop_during_wait_prevents_ai_request(paced_solver):
    solver, clock, calls, jitter = paced_solver
    solver.should_stop = lambda: clock.now >= 1001.0
    assert solver.solve(QUESTION) == {'answer': None, 'source': 'unavailable'}
    assert clock.now < 1002.0 and not calls


def test_question_budget_expires_during_wait_without_paid_attempt(paced_solver, monkeypatch):
    solver, clock, calls, jitter = paced_solver
    monkeypatch.setenv('CHAOXING_QUESTION_TIMEOUT_SECONDS', '10')
    assert solver.solve(QUESTION) == {'answer': None, 'source': 'unavailable'}
    assert clock.now == pytest.approx(1010.0) and not calls


def test_native_answer_arriving_during_wait_avoids_ai_request(paced_solver):
    solver, clock, calls, jitter = paced_solver

    def arrive():
        clock.on_sleep = None
        assert solver.solve({**QUESTION, 'native_answer': [0]})['source'] == 'native'

    clock.on_sleep = arrive
    assert solver.solve(QUESTION) == {'answer': [0], 'source': 'cache'}
    assert not calls
