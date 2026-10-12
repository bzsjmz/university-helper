import json
from unittest.mock import Mock

import pytest

import app.services.course.chaoxing.video_service as video_module
from app.services.course.chaoxing.video_service import ChaoxingVideoService


@pytest.mark.parametrize(
    ("rt", "status_codes"),
    [
        pytest.param("1", [403], id="403"),
        pytest.param("1", [500], id="non-200"),
        pytest.param("", [403, 500], id="rt-retry"),
    ],
)
def test_video_progress_log_redacts_sensitive_failure_context(monkeypatch, rt, status_codes):
    logger = Mock()
    monkeypatch.setattr(video_module, "logger", logger)

    dtoken = "sentinel-dtoken"
    cookie = "sentinel-cookie"
    authorization = "sentinel-authorization"
    custom_secret = "sentinel-custom-secret"
    body_secret = "sentinel-response-body"
    jobid = "sentinel-jobid"

    responses = [
        Mock(
            status_code=status_code,
            text=f"failure body: {body_secret}",
            url=f"https://mooc1.chaoxing.com/multimedia/log/a/cpi/{dtoken}?secret={custom_secret}",
        )
        for status_code in status_codes
    ]
    session = Mock(headers={"Cookie": cookie})
    session.get.side_effect = responses
    service = ChaoxingVideoService(
        get_fid_func=lambda: "fid",
        get_uid_func=lambda: "uid",
        rate_limiter=Mock(),
        video_log_limiter=Mock(),
    )
    job = {
        "otherinfo": "nodeId_x",
        "jobid": jobid,
        "objectid": "objectid",
        "videoFaceCaptureEnc": "",
        "attDuration": "",
        "attDurationEnc": "",
        "rt": rt,
    }
    headers = {
        "Authorization": authorization,
        "X-Custom-Secret": custom_secret,
    }

    result = service.video_progress_log(
        session,
        {"clazzId": "clazz", "courseId": "course", "cpi": "cpi"},
        job,
        {},
        dtoken,
        10,
        1,
        headers=headers,
    )

    assert result == (False, status_codes[-1])
    assert len(session.get.call_args_list) == len(status_codes)
    for request in session.get.call_args_list:
        assert request.args[0].endswith(f"/{dtoken}")
        assert request.kwargs["headers"] == headers

    raw_log_values = [value for call in logger.mock_calls for value in (*call.args, *call.kwargs.values())]
    raw_log_text = repr(raw_log_values)
    formatted_logs = "\n".join(str(call.args[0]).format(*call.args[1:]) for call in logger.mock_calls if call.args)
    formatted_error_logs = "\n".join(str(call.args[0]).format(*call.args[1:]) for call in logger.error.call_args_list)
    assert str(status_codes[-1]) in raw_log_text
    assert jobid in raw_log_text
    assert str(status_codes[-1]) in formatted_error_logs
    assert jobid in formatted_error_logs
    assert "请求url" not in formatted_logs
    assert "请求头" not in formatted_logs
    for secret in (dtoken, cookie, authorization, custom_secret, body_secret):
        assert secret not in raw_log_text
        assert secret not in formatted_logs


def test_video_status_refresh_redacts_non_200_response_context(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(video_module, "logger", logger)

    body_secret = "sentinel-video-refresh-body"
    url_secret = "sentinel-video-refresh-url"
    cookie_secret = "sentinel-video-refresh-cookie"
    authorization_secret = "sentinel-video-refresh-authorization"
    token_secret = "sentinel-video-refresh-token"
    jobid = "sentinel-video-refresh-jobid"
    response = Mock(
        status_code=500,
        text=f"failure body: {body_secret} token={token_secret}",
        url=f"https://example.invalid/status?secret={url_secret}",
        headers={"Cookie": cookie_secret, "Authorization": authorization_secret},
    )
    session = Mock()
    session.get.return_value = response
    service = ChaoxingVideoService(
        get_fid_func=lambda: "fid",
        get_uid_func=lambda: "uid",
        rate_limiter=Mock(),
        video_log_limiter=Mock(),
    )

    result = service._refresh_video_status(session, {"objectid": "objectid", "jobid": jobid}, "Video")

    assert result is None
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs["timeout"] == 8
    assert session.get.call_args.kwargs["headers"] is video_module.gc.VIDEO_HEADERS
    assert logger.debug.call_count == 1
    raw_args = logger.debug.call_args.args
    raw_kwargs = logger.debug.call_args.kwargs
    formatted_logs = "\n".join(str(call.args[0]).format(*call.args[1:]) for call in logger.mock_calls if call.args)
    assert 500 in raw_args
    assert jobid in raw_args
    assert raw_kwargs == {}
    assert "500" in formatted_logs
    assert jobid in formatted_logs
    for secret in (body_secret, url_secret, cookie_secret, authorization_secret, token_secret):
        assert secret not in repr(raw_args)
        assert secret not in repr(raw_kwargs)
        assert secret not in formatted_logs


@pytest.mark.parametrize(
    ("rt", "status_codes"),
    [
        pytest.param("1", [200], id="normal-rt"),
        pytest.param("", [403, 200], id="rt-fallback-retry"),
    ],
)
def test_video_progress_success_does_not_log_response_body(monkeypatch, rt, status_codes):
    logger = Mock()
    monkeypatch.setattr(video_module, "logger", logger)

    body_secret = "sentinel-video-success-body-secret"
    dtoken = "sentinel-video-success-token"
    jobid = "sentinel-video-success-jobid"
    responses = []
    for status_code in status_codes:
        response = Mock(
            status_code=status_code,
            text=f'{{"isPassed": true, "secret": "{body_secret}"}}',
            url=f"https://example.invalid/status?dtoken={dtoken}",
            headers={"Authorization": body_secret},
        )
        response.json.side_effect = lambda response=response: json.loads(response.text)
        responses.append(response)
    session = Mock()
    session.get.side_effect = responses
    service = ChaoxingVideoService(
        get_fid_func=lambda: "fid",
        get_uid_func=lambda: "uid",
        rate_limiter=Mock(),
        video_log_limiter=Mock(),
    )
    job = {
        "otherinfo": "nodeId_x",
        "jobid": jobid,
        "objectid": "objectid",
        "videoFaceCaptureEnc": "",
        "attDuration": "",
        "attDurationEnc": "",
        "rt": rt,
    }

    result = service.video_progress_log(
        session,
        {"clazzId": "clazz", "courseId": "course", "cpi": "cpi"},
        job,
        {},
        dtoken,
        10,
        1,
        headers={"Authorization": body_secret},
    )

    assert result == (True, 200)
    assert len(session.get.call_args_list) == len(status_codes)
    assert [response.json.call_count for response in responses if response.status_code == 200] == [1]
    raw_log_values = [value for call in logger.mock_calls for value in (*call.args, *call.kwargs.values())]
    raw_log_text = repr(raw_log_values)
    formatted_logs = "\n".join(str(call.args[0]).format(*call.args[1:]) for call in logger.mock_calls if call.args)
    assert str(status_codes[-1]) not in raw_log_text
    assert jobid not in raw_log_text
    for secret in (body_secret, dtoken):
        assert secret not in raw_log_text
        assert secret not in formatted_logs


def _forbidden_video_service(monkeypatch):
    session_manager = Mock()
    session_manager.get_session.return_value.get.return_value.json.return_value = {
        "status": "success", "dtoken": "fake", "crc": "fake", "key": "fake", "duration": 3600,
    }
    service = ChaoxingVideoService(lambda: "fid", lambda: "uid", Mock(), Mock(), session_manager)
    monkeypatch.setattr(video_module, "tqdm", Mock())
    service.video_progress_log = Mock(return_value=(False, 403))
    service._recover_after_forbidden = Mock(return_value=None)
    return service


def test_repeated_403_exits_without_waiting_for_video_playback(monkeypatch):
    service = _forbidden_video_service(monkeypatch)
    monkeypatch.setattr(video_module.random, "uniform", lambda *args: 0)
    sleep = Mock()
    monkeypatch.setattr(video_module.time, "sleep", sleep)
    result = service.study_video({}, {"objectid": "fake", "playTime": 0, "name": "fake"}, {})
    assert result == video_module.StudyResult.FORBIDDEN
    assert service.video_progress_log.call_count == 1 + video_module.MAX_FORBIDDEN_RETRY
    assert service._recover_after_forbidden.call_count == video_module.MAX_FORBIDDEN_RETRY
    sleep.assert_not_called()


def test_403_recovery_can_succeed_without_waiting_for_playback(monkeypatch):
    service = _forbidden_video_service(monkeypatch)
    service.video_progress_log.side_effect = [(False, 403), (True, 200)]
    service._recover_after_forbidden.return_value = {"dtoken": "new-fake", "duration": 3600}
    monkeypatch.setattr(video_module.random, "uniform", lambda *args: 0)
    result = service.study_video({}, {"objectid": "fake", "playTime": 0, "name": "fake"}, {})
    assert result == video_module.StudyResult.SUCCESS
    assert service.video_progress_log.call_count == 2
    assert service.video_progress_log.call_args.args[4] == "new-fake"


def test_stop_interrupts_403_recovery_backoff(monkeypatch):
    service = _forbidden_video_service(monkeypatch)
    stopped = False

    def sleep(_delay):
        nonlocal stopped
        stopped = True

    monkeypatch.setattr(video_module.time, "sleep", sleep)
    monkeypatch.setattr(video_module.random, "uniform", lambda *args: 2)
    result = service.study_video({}, {"objectid": "fake", "playTime": 0, "name": "fake"}, {},
                                 should_stop=lambda: stopped)
    assert result == video_module.StudyResult.CANCELLED
    service._recover_after_forbidden.assert_not_called()
    assert service.video_progress_log.call_count == 1
