"""Private user-imported font resources; no production map or platform requests."""
import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import desktop_settings
from app.api.v1 import desktop
from app.services.course.chaoxing import cxsecret_font, learning, quiz_service
from app.services.course.chaoxing.exceptions import FontDecodeError

MAP = {'uni5982': 'a' * 32}
HEADERS = {'Origin': 'http://127.0.0.1:45678'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('UH_DESKTOP_DATA', str(tmp_path))
    for key in ('CHAOXING_AI_ENDPOINT', 'CHAOXING_AI_MODEL', 'CHAOXING_AI_PRIMARY_KEY_FILE', 'CHAOXING_AI_KEY_FILE'):
        monkeypatch.delenv(key, raising=False)
    desktop_settings.apply_settings(tmp_path)
    app = FastAPI()
    app.include_router(desktop.router)
    with TestClient(app, base_url=HEADERS['Origin']) as value:
        yield value


def test_valid_map_is_private_and_retained_without_changing_keys(client, tmp_path, monkeypatch):
    (tmp_path / 'ai-primary.key').write_text('private-fixture')
    reply = client.put('/font-map', json=MAP, headers=HEADERS)
    assert reply.status_code == 200
    assert reply.json() == {'entries': 1, 'restart_required': True}
    assert 'uni5982' not in reply.text and 'private-fixture' not in reply.text
    target = tmp_path / 'resource/font_map_table.json'
    assert json.loads(target.read_text()) == MAP
    assert (tmp_path / 'ai-primary.key').read_text() == 'private-fixture'
    assert client.get('/settings').json()['font_map_entries'] == 1
    if os.name != 'nt':
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    # The next backend process resolves this stable app-data path, rather than
    # PyInstaller's disposable extraction directory.
    assert cxsecret_font.resource_path('resource/font_map_table.json') == str(target)
    dao = cxsecret_font.FontHashDAO()
    assert dao.find_char('a' * 32) == 'uni5982'
    monkeypatch.setattr(cxsecret_font, 'fonthash_dao', dao)
    assert cxsecret_font.decrypt({'uni4E00': 'a' * 32}, '一') == '如'


@pytest.mark.parametrize('payload', [[], {}, {'secret-key': 'private-data'},
                                   {'uni5982': 'bad-hash'}, {'uni110000': 'b' * 32}])
def test_invalid_map_does_not_replace_saved_resource(client, tmp_path, payload):
    assert client.put('/font-map', json=MAP, headers=HEADERS).status_code == 200
    reply = client.put('/font-map', json=payload, headers=HEADERS)
    assert reply.status_code == 422
    assert 'private-data' not in reply.text
    assert json.loads((tmp_path / 'resource/font_map_table.json').read_text()) == MAP


def test_malformed_or_oversized_input_is_bounded(client):
    assert client.put('/font-map', content=b'not-json', headers=HEADERS).status_code == 422
    assert client.put('/font-map', content=b' ' * (desktop_settings.FONT_MAP_LIMIT + 1), headers=HEADERS).status_code == 413
    assert client.get('/settings').json()['font_map_entries'] == 0


@pytest.mark.parametrize('headers', [{}, {'Origin': 'https://other.example'}])
def test_cross_origin_import_is_rejected(client, tmp_path, headers):
    assert client.put('/font-map', json=MAP, headers=headers).status_code == 403
    assert not (tmp_path / 'resource/font_map_table.json').exists()


def test_missing_map_is_reported_for_the_quiz_without_querying_or_submitting(monkeypatch):
    def encrypted(*args):
        raise FontDecodeError('secret path or page must not be displayed')
    monkeypatch.setattr(quiz_service, 'decode_questions_info', encrypted)
    session = Mock()
    session.get.return_value = SimpleNamespace(status_code=200, text='<form>encrypted fixture</form>')
    tiku = Mock(DISABLE=False)
    service = quiz_service.ChaoxingQuizService(tiku, 0, {}, SimpleNamespace(get_session=lambda: session))
    client = SimpleNamespace(
        rate_limiter=SimpleNamespace(limit_rate=lambda **kwargs: None),
        get_job_list=lambda *args: ([{'type': 'workid', 'jobid': 'work-1', 'enc': 'fixture'}],
                                  {'knowledgeid': '1', 'ktoken': 'fixture', 'cpi': '3'}),
        study_work=service.study_work,
    )
    logs = []
    task = learning.ChapterTask(0, {'title': '加密测验', 'has_finished': False})
    proc = learning.JobProcessor(client, {'courseId': '1', 'clazzId': '2', 'title': '测试课程'}, [task], {
        'speed': 1.5, 'jobs': 1, 'notopen_action': 'retry',
        'chapter_log_callback': lambda course, point, message, level: logs.append(message),
    })
    proc.max_tries = 1
    proc._run_inline()
    assert proc.failed_tasks == [task]
    assert '章节测验' in logs[0] and '可能缺少字体映射或映射不完整' in logs[0]
    assert 'secret' not in logs[0]
    tiku.query.assert_not_called()
    session.post.assert_not_called()
