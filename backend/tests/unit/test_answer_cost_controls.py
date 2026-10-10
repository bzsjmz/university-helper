import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from app.services.course.chaoxing import answer_base
from app.services.course.chaoxing.answer_base import TikuFallback
from app.services.course.chaoxing.answer_providers import AI, TikuGo
from app.services.course.chaoxing.answer_providers import ai as ai_module
from app.services.course.chaoxing.answer_providers import go as go_module


@pytest.fixture
def cache(monkeypatch, tmp_path):
    instance = answer_base.CacheDAO(str(tmp_path / 'answers.json'))
    monkeypatch.setattr(answer_base, 'CacheDAO', lambda: instance)
    return instance


def question(options='A.单用户,多任务\nB.其他', kind='single'):
    return {'title': '模拟成本测试题', 'type': kind, 'options': options}


def chain(go_answer=None, ai_answer='A'):
    go = TikuGo(); go._query = Mock(return_value=go_answer)
    ai = AI(); ai._query = Mock(return_value=ai_answer)
    return TikuFallback([go, ai]), go, ai


def test_bank_exact_single_option_does_not_fall_back_to_ai(cache):
    provider, go, ai = chain('单用户,多任务')
    assert provider.query(question()) == '单用户,多任务'
    ai._query.assert_not_called()


def test_cache_option_order_and_type_are_separate_and_legacy_letters_are_not_reused(cache):
    cache.add_cache(question()['title'], 'A')
    provider, go, ai = chain()
    assert provider.query(question()) == 'A'
    ai._query.assert_called_once()
    assert provider.query(question()) == 'A'
    assert ai._query.call_count == 1
    assert provider.query(question('A.其他\nB.单用户,多任务')) == 'A'
    assert ai._query.call_count == 2
    assert provider.query(question(kind='completion')) == 'A'
    assert ai._query.call_count == 3


def test_legacy_exact_option_text_can_be_reused_safely(cache):
    cache.add_cache(question()['title'], '单用户,多任务')
    provider, go, ai = chain()
    assert provider.query(question()) == '单用户,多任务'
    go._query.assert_not_called(); ai._query.assert_not_called()


@pytest.mark.parametrize('answer', ['A', None])
def test_concurrent_identical_questions_share_success_or_failure(cache, answer):
    entered = threading.Event(); release = threading.Event()
    providers = [chain(), chain()]
    def generate(q):
        entered.set()
        assert release.wait(3)
        return answer
    for provider, go, ai in providers:
        ai._query.side_effect = generate
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(providers[0][0].query, question())
        assert entered.wait(3)
        second = pool.submit(providers[1][0].query, question())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with cache._lock:
                waiting = any(entry['users'] == 2 for entry in cache._state.get('flights', {}).values())
            if waiting: break
            time.sleep(0.01)
        assert waiting
        release.set()
        assert first.result(timeout=3) == answer
        assert second.result(timeout=3) == answer
    assert sum(ai._query.call_count for _, go, ai in providers) == 1
    assert not cache._state['flights']


def test_different_options_do_not_share_inflight_result(cache):
    gate = threading.Barrier(2)
    providers = [chain(), chain()]
    for provider, go, ai in providers:
        ai._query.side_effect = lambda q: (gate.wait(timeout=3), 'A')[1]
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(providers[0][0].query, question()),
                   pool.submit(providers[1][0].query, question('A.不同选项\nB.其他'))]
        assert [future.result(timeout=3) for future in futures] == ['A', 'A']
    assert sum(ai._query.call_count for _, go, ai in providers) == 2


def build_ai(monkeypatch, content='{"Answer":["A"]}', finish='stop'):
    provider = AI()
    provider.key = 'dummy-primary'; provider._keys = ('dummy-primary', 'dummy-fallback')
    provider.endpoint = 'https://api.deepseek.com/chat/completions'; provider.model = 'deepseek-flash'
    provider._respect_interval = Mock()
    monkeypatch.setattr(ai_module, 'is_public_endpoint', lambda value: True)
    response = Mock(status_code=200)
    response.json.return_value = {'choices': [{'message': {'content': content}, 'finish_reason': finish}]}
    provider._httpx_client = Mock()
    provider._httpx_client.post.return_value = response
    return provider, response


@pytest.mark.parametrize('content,finish', [('', 'stop'), ('{}', 'stop'), ('{"Answer":["A"]}', 'length')])
def test_received_unusable_answer_never_regenerates_or_switches_keys(monkeypatch, content, finish):
    provider, response = build_ai(monkeypatch, content, finish)
    assert provider._invoke_completion([]) is None
    provider._httpx_client.post.assert_called_once()


def test_malformed_http_response_does_not_repeat_paid_request(monkeypatch):
    provider, response = build_ai(monkeypatch)
    response.json.side_effect = ValueError('malformed response')
    assert provider._invoke_completion([]) is None
    provider._httpx_client.post.assert_called_once()


def test_array_answer_is_parsed_locally_and_deepseek_is_bounded_non_thinking(monkeypatch):
    provider, response = build_ai(monkeypatch, '["A"]')
    assert provider._invoke_completion([]) == 'A'
    provider._httpx_client.post.assert_called_once()
    payload = provider._httpx_client.post.call_args.kwargs['json']
    assert payload['thinking'] == {'type': 'disabled'}
    assert payload['max_tokens'] == 512


def test_deepseek_specific_parameter_is_not_sent_to_other_providers(monkeypatch):
    provider, response = build_ai(monkeypatch)
    provider.endpoint = 'https://another.example/chat/completions'
    assert provider._invoke_completion([]) == 'A'
    assert 'thinking' not in provider._httpx_client.post.call_args.kwargs['json']


def test_go_request_gate_is_shared_across_instances(monkeypatch):
    clock = [100.0]; starts = []
    monkeypatch.setattr(go_module, '_GO_LAST_START', 0.0)
    monkeypatch.setattr(go_module, '_GO_NOT_BEFORE', 0.0)
    monkeypatch.setattr(go_module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(go_module.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def post(*args, **kwargs):
        starts.append(clock[0]); return Mock()
    monkeypatch.setattr(go_module.requests, 'post', post)
    for provider in (TikuGo(), TikuGo()):
        provider._request_question('synthetic', 1)
    assert starts == [100.0, 103.0]


def test_unlabelled_option_text_is_not_truncated(cache):
    provider, go, ai = chain('Paris, France')
    assert provider.query(question(options=['Paris, France', 'London'])) == 'Paris, France'
    ai._query.assert_not_called()


@pytest.mark.parametrize('status,body', [(429, {}), (200, {'code': 0, 'msg': '流控限制', 'data': ''}),
                                       (200, {'code': 1, 'msg': '并发限制', 'data': '提示信息'})])
def test_go_throttle_cools_down_all_accounts_before_another_request(monkeypatch, status, body):
    clock = [100.0]; starts = []
    monkeypatch.setattr(go_module, '_GO_LAST_START', 0.0)
    monkeypatch.setattr(go_module, '_GO_NOT_BEFORE', 0.0)
    monkeypatch.setattr(go_module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(go_module.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    responses = [Mock(status_code=status), Mock(status_code=200)]
    responses[0].json.return_value = body
    responses[1].json.return_value = {'code': 1, 'data': '答案', 'msg': ''}
    def post(*args, **kwargs):
        starts.append(clock[0]); return responses[len(starts) - 1]
    monkeypatch.setattr(go_module.requests, 'post', post)
    first, second = TikuGo(), TikuGo()
    response = first._request_question('synthetic', 1)
    assert first._parse_response(response)['is_throttled']
    second._request_question('synthetic', 1)
    assert starts == [100.0, 115.0]


def test_go_defaults_are_slower_and_retry_http_429_before_ai(monkeypatch):
    go = TikuGo()
    assert go._min_interval == 3 and go._retry_times == 4 and go._retry_backoff == 15
    throttled = Mock(status_code=429)
    success = Mock(status_code=200)
    success.json.return_value = {'code': 1, 'data': '答案', 'msg': ''}
    go._request_question = Mock(side_effect=[throttled, success])
    go._sleep_retry = Mock()
    assert go._query_once('synthetic') == '答案'
    assert go._request_question.call_count == 2
    go._sleep_retry.assert_called_once_with(1, '触发流控', include_min_interval=True)
