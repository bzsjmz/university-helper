"""Provider protocol and scoped audio preparation; every HTTP call is mocked."""
import base64
import json
from unittest.mock import Mock

import pytest
import requests

from app.services.course.english import speech, prepare_recording as cli
from app.services.course.english.welearn_recordings import (
    RecordingJob, RecordingUploadUnavailable, prepare_recording, recording_message,
)


AUDIO = b'ID3' + bytes(100)
DETAIL = {'id': 'recording-1', 'kind': 'recording', 'scored': False, 'script': '', 'app_required': True}


def line(value):
    return json.dumps(value).encode() + b'\n'


def audio_event(audio=AUDIO):
    return line({'code': 0, 'data': base64.b64encode(audio).decode()})


END = line({'code': 20000000, 'data': None})


@pytest.fixture
def provider():
    response = Mock(status_code=200)
    response.iter_content.return_value = iter([audio_event(), END])
    session = Mock()
    session.post.return_value = response
    client = speech.VolcanoSpeech(speech.SpeechConfig('test-key-never-real'), session=session)
    return client, session, response


def test_fixed_origin_protocol_and_chunk_assembly(provider):
    client, session, response = provider
    raw = audio_event(AUDIO[:50]) + line({'code': 0, 'data': None, 'sentence': {}}) + audio_event(AUDIO[50:]) + END
    response.iter_content.return_value = iter([raw[:9], raw[9:31], raw[31:]])
    assert client.synthesize('A complete English response.') == AUDIO
    args, kwargs = session.post.call_args
    assert args == (speech.ENDPOINT,)
    assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
    assert kwargs['headers']['X-Api-Resource-Id'] == 'seed-tts-2.0'
    assert kwargs['json']['req_params']['speaker'] == 'en_male_alberto_uranus_bigtts'
    assert json.loads(kwargs['json']['req_params']['additions'])['explicit_language'] == 'en'
    assert session.trust_env is False
    response.close.assert_called_once()
    assert 'test-key-never-real' not in repr(client.config)


@pytest.mark.parametrize('status', [302, 401, 403, 429, 500])
def test_http_failure_is_not_retried_or_redirected(provider, status):
    client, session, response = provider
    response.status_code = status
    with pytest.raises(speech.SpeechError, match='未自动重试'):
        client.synthesize('English text')
    session.post.assert_called_once()
    response.iter_content.assert_not_called()
    response.close.assert_called_once()


@pytest.mark.parametrize('chunks', [
    [audio_event()], [END], [audio_event(b'not audio'), END],
    [line({'code': 0, 'data': 'invalid-base64!'}), END],
    [line({'code': 500, 'message': 'secret-provider-error'})],
    [b'{truncated'], [line([])], [line({'code': False})],
    [audio_event(), END, audio_event()], [audio_event(), END, END],
    [line({'code': 20000000, 'data': 'not empty'})],
])
def test_unfinished_invalid_and_conflicting_streams_are_not_adopted(provider, chunks):
    client, session, response = provider
    response.iter_content.return_value = iter(chunks)
    with pytest.raises(speech.SpeechError) as error:
        client.synthesize('English text')
    assert 'secret-provider-error' not in str(error.value)
    session.post.assert_called_once()
    response.close.assert_called_once()


@pytest.mark.parametrize('limit', ['MAX_EVENT', 'MAX_RESPONSE', 'MAX_AUDIO'])
def test_stream_has_bounded_sizes(provider, monkeypatch, limit):
    client, _, response = provider
    monkeypatch.setattr(speech, limit, 12)
    with pytest.raises(speech.SpeechError):
        client.synthesize('English text')
    response.close.assert_called_once()


def test_stop_before_request_avoids_charge(provider):
    client, session, _ = provider
    with pytest.raises(speech.SpeechError, match='已停止'):
        client.synthesize('English text', should_stop=lambda: True)
    session.post.assert_not_called()


def test_stop_during_stream_discards_partial_audio(provider):
    client, _, response = provider
    stopped = iter([False, False, True])
    with pytest.raises(speech.SpeechError, match='已停止'):
        client.synthesize('English text', should_stop=lambda: next(stopped))
    response.close.assert_called_once()


def test_deadline_before_request_avoids_charge(provider):
    client, session, _ = provider
    with pytest.raises(speech.SpeechError, match='预算耗尽'):
        client.synthesize('English text', deadline=0)
    session.post.assert_not_called()


def test_network_error_does_not_leak_remote_exception_or_retry(provider):
    client, session, _ = provider
    session.post.side_effect = requests.ConnectionError('test-key-never-real secret text')
    with pytest.raises(speech.SpeechError) as error:
        client.synthesize('English text')
    assert 'test-key' not in str(error.value)
    session.post.assert_called_once()


@pytest.mark.parametrize('text', ['', '  ', None, 'x' * 2001, 'hello\x00'])
def test_invalid_text_is_rejected_before_request(provider, text):
    client, session, _ = provider
    with pytest.raises(speech.SpeechError):
        client.synthesize(text)
    session.post.assert_not_called()


def test_dedicated_secret_file_and_speaker_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv('VOLCENGINE_TTS_API_KEY_FILE', raising=False)
    monkeypatch.setenv('CHAOXING_AI_KEY', 'do-not-reuse-this-key')
    with pytest.raises(speech.SpeechError, match='未配置'):
        speech.SpeechConfig.from_environment()
    key = tmp_path / 'tts-key'
    key.write_text('test-key-never-real\n')
    monkeypatch.setenv('VOLCENGINE_TTS_API_KEY_FILE', str(key))
    config = speech.SpeechConfig.from_environment()
    assert config.api_key == 'test-key-never-real'
    assert 'test-key' not in repr(config)
    key.write_text('x' * 4097)
    with pytest.raises(speech.SpeechError):
        speech.SpeechConfig.from_environment()


@pytest.mark.parametrize('key', ['', 'key\ninjection', '含中文', None])
def test_bad_key_cannot_enter_headers(key):
    with pytest.raises(speech.SpeechError):
        speech.SpeechConfig(key)


def job(**changes):
    return RecordingJob(**{'owner': 'owner-1', 'course': 'course-1', 'item': 'lesson-1',
                           'recorder': 'voice-1', 'text': 'A prepared English response.', **changes})


def test_binding_includes_account_lesson_question_text_and_voice():
    original = job().artifact_id(speech.DEFAULT_SPEAKER)
    for changes in ({'owner': 'owner-2'}, {'course': 'course-2'}, {'item': 'lesson-2'},
                    {'recorder': 'voice-2'}, {'text': 'Different answer'}, {'scored': True}):
        assert job(**changes).artifact_id(speech.DEFAULT_SPEAKER) != original
    assert job().artifact_id('en_female_joanne_uranus_bigtts') != original


@pytest.mark.parametrize('field', ['owner', 'course', 'item', 'recorder'])
def test_missing_binding_is_rejected(field):
    with pytest.raises(speech.SpeechError, match='绑定'):
        job(**{field: ''})


def test_reading_uses_exact_script_and_open_speaking_requires_answer():
    options = dict(owner='owner-1', course='course-1', item='lesson-1')
    with pytest.raises(speech.SpeechError):
        RecordingJob.from_detail(**options, detail=DETAIL)
    scored = {**DETAIL, 'scored': True, 'script': 'Read this exact sentence.'}
    assert RecordingJob.from_detail(**options, detail=scored, answer='Wrong sentence').text == scored['script']
    with pytest.raises(speech.SpeechError):
        RecordingJob.from_detail(**options, detail={**scored, 'script': ''}, answer='Generic filler')


def test_normal_execution_does_not_charge_for_missing_upload(provider):
    client, session, _ = provider
    with pytest.raises(RecordingUploadUnavailable):
        prepare_recording(job(), client)
    session.post.assert_not_called()
    audio, manifest = prepare_recording(job(), client, preview=True)
    assert audio == AUDIO and manifest['status'] == 'prepared'
    assert manifest['platform_completed'] is False
    assert 'text' not in manifest


def test_diagnostic_distinguishes_recording_from_scoring():
    message = recording_message([DETAIL, {**DETAIL, 'scored': True}], 2, mixed=True)
    assert '普通录音 1 项' in message and '朗读评分 1 项' in message
    assert '上传尚未接通' in message and '文字保存状态未确认' in message


def write_job(path):
    path.write_text(json.dumps({'owner': 'owner-1', 'course': 'course-1', 'item': 'lesson-1',
                               'detail': DETAIL, 'answer': 'A prepared response.'}))


def test_operator_default_only_validates_job_without_loading_key_or_charging(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'job.json'
    write_job(path)
    factory = Mock()
    monkeypatch.setattr(cli, 'VolcanoSpeech', factory)
    monkeypatch.setattr('sys.argv', ['prepare_recording', '--job', str(path), '--output-dir', str(tmp_path / 'out')])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)['synthesis_requested'] is False
    factory.assert_not_called()


def test_preview_private_files_and_repeated_job_does_not_charge_again(tmp_path, monkeypatch):
    path = tmp_path / 'job.json'
    write_job(path)
    output = tmp_path / 'out'
    config = speech.SpeechConfig('test-key-never-real')
    monkeypatch.setattr(cli.SpeechConfig, 'from_environment', lambda: config)
    client = Mock(config=config)
    client.synthesize.return_value = AUDIO
    monkeypatch.setattr(cli, 'VolcanoSpeech', lambda _: client)
    monkeypatch.setattr('sys.argv', ['prepare_recording', '--job', str(path), '--output-dir', str(output), '--preview'])
    assert cli.main() == 0
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
    manifest = json.loads(next(output.glob('*.json')).read_text())
    assert manifest['platform_completed'] is False
    assert cli.main() == 1
    client.synthesize.assert_called_once()


def test_failed_preview_cleans_reserved_outputs_without_leaking_exception(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'job.json'
    write_job(path)
    output = tmp_path / 'out'
    config = speech.SpeechConfig('test-key-never-real')
    monkeypatch.setattr(cli.SpeechConfig, 'from_environment', lambda: config)
    client = Mock(config=config)
    client.synthesize.side_effect = speech.SpeechError('secret remote error')
    monkeypatch.setattr(cli, 'VolcanoSpeech', lambda _: client)
    monkeypatch.setattr('sys.argv', ['prepare_recording', '--job', str(path), '--output-dir', str(output), '--preview'])
    assert cli.main() == 1
    assert not list(output.iterdir())
    assert 'secret remote' not in capsys.readouterr().err
