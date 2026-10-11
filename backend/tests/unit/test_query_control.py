import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.course.chaoxing import answer_base, learning, query_control as control
from app.services.course.chaoxing.answer_base import TikuFallback
from app.services.course.chaoxing.answer_providers import AI, TikuGo
from app.services.course.chaoxing.answer_providers import ai as ai_module
from app.services.course.chaoxing.quiz_service import QuizAnswerProcessor


@pytest.fixture
def cache(monkeypatch, tmp_path):
    instance = answer_base.CacheDAO(str(tmp_path / 'answers.json'))
    monkeypatch.setattr(answer_base, 'CacheDAO', lambda: instance)
    return instance


def question():
    return {'title': 'isolated fixture', 'type': 'single', 'options': 'A.first\nB.second'}


def fake_clock(monkeypatch):
    clock = [0.0]
    def advance(seconds):
        clock[0] += seconds
    monkeypatch.setattr(control, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=advance))
    return clock


def test_waiting_same_question_can_cancel_and_releases_flight_reference(cache):
    provider = TikuFallback([])
    stop = threading.Event()
    result = []
    def query():
        try:
            with control.job_scope(stop.is_set):
                provider.query(question())
        except control.QueryCancelled:
            result.append('cancelled')
    with cache.question_lock(question(), provider.name):
        thread = threading.Thread(target=query)
        thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with cache._lock:
                waiting = any(entry['users'] == 2 for entry in cache._state['flights'].values())
            if waiting:
                break
            time.sleep(0.001)
        assert waiting
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert result == ['cancelled']
    assert not cache._state['flights']


def test_congested_go_yields_to_ai_within_its_stage_budget(cache, monkeypatch):
    clock = fake_clock(monkeypatch)
    go, ai = TikuGo(), AI()
    def congested(_):
        control.sleep(60)
    go._query = Mock(side_effect=congested)
    ai._query = Mock(return_value='A')
    assert TikuFallback([go, ai]).query(question()) == 'A'
    assert 15 <= clock[0] <= 15.01
    ai._query.assert_called_once()


def test_question_total_deadline_does_not_generate_or_cache_an_answer(cache, monkeypatch):
    fake_clock(monkeypatch)
    ai = AI()
    ai._query = Mock(side_effect=lambda _: control.sleep(150))
    with pytest.raises(control.QueryExpired):
        TikuFallback([ai]).query(question())
    assert cache.get_question_cache(question()) is None
    assert not cache._state['flights']


def test_primary_and_fallback_share_three_attempts_and_request_timeout(cache, monkeypatch):
    provider = AI()
    provider.endpoint = 'https://api.deepseek.com/chat/completions'
    provider.model = 'fixture-model'
    provider.key = 'fixture-primary'
    provider._keys = ('fixture-primary', 'fixture-fallback')
    provider.max_retries = 99
    provider.retry_delay = 0
    provider.timeout = 120
    provider._respect_interval = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"Answer":["A"]}'}}]}
    provider._httpx_client = Mock()
    provider._httpx_client.post.side_effect = [Mock(status_code=503), Mock(status_code=503), response]
    monkeypatch.setattr(ai_module, 'is_public_endpoint', lambda _: True)
    assert provider.query(question()) == 'A'
    calls = provider._httpx_client.post.call_args_list
    assert len(calls) == 3
    assert [c.kwargs['headers']['Authorization'] for c in calls] == ['Bearer fixture-primary', 'Bearer fixture-primary', 'Bearer fixture-fallback']
    assert all(0 < call.kwargs['timeout'] <= 30 for call in calls)


def test_answer_workers_inherit_cancel_context_and_timeout_aborts_siblings():
    processor = QuizAnswerProcessor(SimpleNamespace(DISABLE=False, wants_concurrent_query=True), {'ai_concurrency': 2})
    entered = threading.Event()
    exited = threading.Event()
    def handle(q, *args):
        assert control._JOB.get() is not None
        if q['id'] == 1:
            assert entered.wait(2)
            raise control.QueryExpired()
        entered.set()
        try:
            while True:
                control.sleep(0.01)
        finally:
            exited.set()
    processor.handle_question = handle
    with pytest.raises(control.QueryExpired):
        with control.job_scope(lambda: False, answering=True):
            processor.process_questions({'questions': [{'id': 1}, {'id': 2}]})
    assert exited.is_set()


def test_chapter_failure_waits_for_cancelled_internal_worker_exit(monkeypatch):
    entered, exited = threading.Event(), threading.Event()
    def run_job(client, course, job, info, speed, progress_callback=None, should_stop_callback=None):
        if job['id'] == 'failed':
            assert entered.wait(2)
            return learning.StudyResult.ERROR
        entered.set()
        while not should_stop_callback():
            exited.wait(0.01)
        exited.set()
        return learning.StudyResult.CANCELLED
    monkeypatch.setattr(learning, 'process_job', run_job)
    client = SimpleNamespace(rate_limiter=SimpleNamespace(limit_rate=lambda **kwargs: None),
        get_job_list=lambda *args: ([{'id': 'failed'}, {'id': 'sibling'}], {'valid': True}))
    result = learning.process_chapter(client, {}, {'title': 'fixture', 'has_finished': False}, 1.5, {})
    assert result == learning.ChapterResult.ERROR
    assert exited.is_set()


def test_retry_wait_can_stop_without_enqueuing_or_leaking_pending_count():
    processor = learning.JobProcessor(object(), {}, [], {'speed': 1.5, 'jobs': 1})
    assert processor.max_tries == 3
    stop = threading.Event(); stop.set()
    processor._enqueue_retry(learning.ChapterTask(index=0, point={}), stop)
    assert processor.retry_queue.empty()
    assert processor._pending_retries == 0


def test_partial_thread_start_failure_never_replays_running_jobs_inline(monkeypatch):
    processed = []
    entered = threading.Event()
    class PartialExecutor:
        def __init__(self, **kwargs):
            self.pool = ThreadPoolExecutor(max_workers=1)
            self.calls = 0
        def submit(self, *args, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("can't start new thread")
            future = self.pool.submit(*args, **kwargs)
            assert entered.wait(2)
            return future
        def shutdown(self, **kwargs):
            self.pool.shutdown(**kwargs)
    def run_job(client, course, job, info, speed, progress_callback=None, should_stop_callback=None):
        processed.append(job['id'])
        entered.set()
        while not should_stop_callback():
            time.sleep(0.001)
        return learning.StudyResult.CANCELLED
    monkeypatch.setattr(learning, 'ThreadPoolExecutor', PartialExecutor)
    monkeypatch.setattr(learning, 'process_job', run_job)
    client = SimpleNamespace(rate_limiter=SimpleNamespace(limit_rate=lambda **kwargs: None),
        get_job_list=lambda *args: ([{'id': 'one'}, {'id': 'two'}], {'valid': True}))
    result = learning.process_chapter(client, {}, {'title': 'fixture', 'has_finished': False}, 1.5, {})
    assert result == learning.ChapterResult.ERROR
    assert processed == ['one']
