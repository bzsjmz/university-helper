"""Task-visible diagnostics must survive retries without leaking request data."""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
import requests

from app.services.course.chaoxing import learning
from app.services.course.chaoxing.course_data_service import ChaoxingCourseDataService
from app.services.course.chaoxing.exceptions import diagnostic_reason


def client_with_jobs(jobs, info):
    return SimpleNamespace(
        rate_limiter=SimpleNamespace(limit_rate=lambda **kwargs: None),
        get_job_list=lambda *args: (jobs, info),
    )


def processor(client, logs, **config):
    tasks = [learning.ChapterTask(0, {"title": "第一章", "has_finished": False})]
    return learning.JobProcessor(client, {"title": "数学"}, tasks, {
        "speed": 1.5, "jobs": 1, "notopen_action": "retry",
        "chapter_log_callback": lambda course, point, msg, level: logs.append((point['title'], msg, level)),
        **config,
    })


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("info,reason", [
    ({"_read_error": "章节任务列表请求失败（HTTP 403）"}, "HTTP 403"),
    ({"notOpen": True}, "章节尚未开放"),
    ({}, "未识别到章节任务数据"),
])
def test_final_failure_explains_stage_attempts_and_chapter(monkeypatch, inline, info, reason):
    logs = []
    proc = processor(client_with_jobs([], info), logs)
    proc.max_tries = 2
    monkeypatch.setattr(proc, '_wait_retry', lambda *args: True)
    proc._run_inline() if inline else proc.run()
    assert len(logs) == 2
    assert all(title == '第一章' and reason in msg for title, msg, _ in logs)
    assert '第 1/2 次尝试' in logs[0][1] and '稍后重试' in logs[0][1]
    assert '第 2/2 次尝试' in logs[-1][1] and '本章未完成' in logs[-1][1]
    assert logs[-1][2] == 'error'
    assert proc.failed_tasks == proc.tasks


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("result,reason", [
    (learning.StudyResult.FORBIDDEN, 'HTTP 403'),
    (learning.StudyResult.TIMEOUT, '处理超时'),
    (learning.StudyResult.NOT_OPEN, '尚未开放'),
    (learning.StudyResult.ERROR, '平台未确认完成'),
])
def test_job_failure_keeps_task_type_and_safe_reason(monkeypatch, inline, result, reason):
    monkeypatch.setattr(learning, 'process_job', lambda *args, **kwargs: result)
    logs = []
    proc = processor(client_with_jobs([{'type': 'workid', 'jobid': 'private-id'}], {'valid': True}), logs)
    if inline:
        # Exercise the executor fallback, as well as normal concurrent jobs.
        class NoThreads:
            def __init__(self, **kwargs):
                pass
            def submit(self, *args, **kwargs):
                raise RuntimeError("can't start new thread")
            def shutdown(self, **kwargs):
                pass
        monkeypatch.setattr(learning, 'ThreadPoolExecutor', NoThreads)
    proc.max_tries = 1
    proc._run_inline()
    assert '章节测验' in logs[0][1] and reason in logs[0][1]
    assert 'private-id' not in logs[0][1]


def test_concurrent_chapters_keep_own_reasons(monkeypatch):
    barrier = threading.Barrier(2)
    def run(client, course, point, speed, config):
        config['chapter_diagnostic_callback'](point['title'] + '独立原因')
        barrier.wait(timeout=3)
        return learning.ChapterResult.ERROR
    monkeypatch.setattr(learning, 'process_chapter', run)
    logs = []
    proc = processor(object(), logs, jobs=2)
    proc.tasks.append(learning.ChapterTask(1, {'title': '第二章'}))
    proc.max_tries = 1
    proc.run()
    assert len(logs) == 2
    for title, msg, _ in logs:
        assert title + '独立原因' in msg
        assert ('第二章' if title == '第一章' else '第一章') not in msg


def test_retry_recovery_is_reported(monkeypatch):
    results = iter([learning.ChapterResult.ERROR, learning.ChapterResult.SUCCESS])
    monkeypatch.setattr(learning, 'process_chapter', lambda *args: next(results))
    logs = []
    proc = processor(object(), logs)
    monkeypatch.setattr(proc, '_wait_retry', lambda *args: True)
    proc._run_inline()
    assert not proc.failed_tasks
    assert '第 2/3 次尝试成功' in logs[-1][1]
    assert logs[-1][2] == 'success'


def test_reporting_failure_does_not_replay_successful_job(monkeypatch):
    run = Mock(return_value=learning.ChapterResult.SUCCESS)
    monkeypatch.setattr(learning, 'process_chapter', run)
    proc = processor(object(), [], chapter_log_callback=Mock(side_effect=RuntimeError('store down')))
    proc.tasks[0].tries = 1
    proc._run_inline()
    assert run.call_count == 1 and not proc.failed_tasks


@pytest.mark.parametrize('exc,reason', [
    (requests.Timeout('secret-url?token=secret'), '超时'),
    (requests.ConnectionError('Cookie: secret'), '连接失败'),
    (ValueError('private response body'), '解析异常'),
    (RuntimeError("can't start new thread: secret"), '线程资源不足'),
    (RuntimeError('password=secret'), '处理异常'),
])
def test_exception_diagnostics_do_not_copy_sensitive_text(exc, reason):
    assert reason in diagnostic_reason(exc)
    assert str(exc) not in diagnostic_reason(exc)


def test_card_http_error_preserves_status_without_response_body():
    session = Mock()
    session.get.return_value = SimpleNamespace(status_code=429, text='Cookie: secret')
    service = ChaoxingCourseDataService(Mock(), Mock(), Mock(), Mock(get_session=lambda: session))
    jobs, info = service.get_job_list({'clazzId': '1', 'courseId': '2', 'cpi': '3'}, {'id': '4'})
    assert not jobs
    assert 'HTTP 429' in info['_read_error']
    assert 'secret' not in str(info)


def test_cancellation_does_not_emit_failure(monkeypatch):
    def cancel(*args):
        raise learning.control.QueryCancelled()
    monkeypatch.setattr(learning, 'process_chapter', cancel)
    logs = []
    proc = processor(object(), logs)
    proc._run_inline()
    assert not logs and not proc.failed_tasks


def test_timeout_budget_retains_timeout_result(monkeypatch):
    def expire(*args):
        raise learning.control.QueryExpired()
    monkeypatch.setattr(learning, '_process_job', expire)
    assert learning.process_job(object(), {}, {'type': 'video'}, {}, 1.5) == learning.StudyResult.TIMEOUT


@pytest.mark.parametrize('inline', [False, True])
def test_job_exception_reports_stage_without_exception_text(monkeypatch, inline):
    def fail(*args, **kwargs):
        raise requests.Timeout('https://platform.invalid/?token=secret')
    monkeypatch.setattr(learning, 'process_job', fail)
    logs = []
    client = client_with_jobs([{'type': 'video'}], {'valid': True})
    if inline:
        details = []
        with pytest.raises(requests.Timeout):
            learning._process_jobs_inline(client, {}, [{'type': 'video'}], {}, 1.5,
                                          {'chapter_diagnostic_callback': details.append})
        message = details[0]
    else:
        proc = processor(client, logs)
        proc.max_tries = 1
        proc._run_inline()
        message = logs[0][1]
    assert '视频/音频' in message and '网络请求超时' in message
    assert 'token' not in message and 'secret' not in message


def test_skipped_locked_chapter_is_not_reported_as_completed():
    logs = []
    proc = processor(client_with_jobs([], {'notOpen': True}), logs, notopen_action='continue')
    proc._run_inline()
    assert not proc.failed_tasks
    assert '已按配置跳过，不代表平台已完成' in logs[0][1]
