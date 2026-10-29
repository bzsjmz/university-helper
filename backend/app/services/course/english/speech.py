"""Volcano's fixed-origin TTS API. Keys and raw provider errors stay server-side."""
import base64
import binascii
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

import requests


ENDPOINT = 'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
RESOURCE_ID = 'seed-tts-2.0'
DEFAULT_SPEAKER = 'en_male_alberto_uranus_bigtts'
MAX_TEXT = 2000
MAX_AUDIO = 8 * 1024 * 1024
MAX_EVENT = 512 * 1024
MAX_RESPONSE = 16 * 1024 * 1024


class SpeechError(Exception):
    pass


@dataclass(frozen=True)
class SpeechConfig:
    api_key: str = field(repr=False)
    speaker: str = DEFAULT_SPEAKER

    def __post_init__(self):
        if (not isinstance(self.api_key, str) or not self.api_key
                or len(self.api_key) > 4096 or any(c.isspace() for c in self.api_key)
                or not self.api_key.isascii()):
            raise SpeechError('语音 API Key 格式无效')
        if not isinstance(self.speaker, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.speaker):
            raise SpeechError('语音音色 ID 格式无效')

    @classmethod
    def from_environment(cls):
        # Use a dedicated mounted secret; never reuse a text-model/Ark key.
        filename = os.environ.get('VOLCENGINE_TTS_API_KEY_FILE', '')
        if not filename:
            raise SpeechError('未配置 VOLCENGINE_TTS_API_KEY_FILE')
        try:
            with Path(filename).open('rb') as handle:
                raw = handle.read(4097)
            if len(raw) > 4096:
                raise ValueError()
            key = raw.decode('ascii').strip()
        except (OSError, ValueError, UnicodeError):
            raise SpeechError('无法读取语音 API Key 文件') from None
        return cls(key, os.environ.get('VOLCENGINE_TTS_SPEAKER', DEFAULT_SPEAKER))


def validate_text(text):
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT
            or any(ord(c) < 32 and c not in '\n\t\r' for c in text)):
        raise SpeechError('合成文本必须为 1–2000 字符的有效文本')
    return text.strip()


def mp3_audio(audio):
    # A signature check, not a playback/duration guarantee.
    return (isinstance(audio, bytes) and 4 <= len(audio) <= MAX_AUDIO and
            (audio.startswith(b'ID3') or audio[0] == 0xff and audio[1] & 0xe0 == 0xe0))


class VolcanoSpeech:
    def __init__(self, config, *, session=None):
        self.config = config
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        self._owns_session = session is None

    def close(self):
        if self._owns_session:
            self.session.close()

    def synthesize(self, text, *, deadline=None, should_stop=None):
        text = validate_text(text)
        deadline = min(deadline, time.monotonic() + 60) if deadline is not None else time.monotonic() + 60

        def check():
            if should_stop is not None and should_stop():
                raise SpeechError('语音合成已停止')
            if time.monotonic() >= deadline:
                raise SpeechError('语音合成预算耗尽')

        response = None
        try:
            check()
            response = self.session.post(ENDPOINT, headers={
                'X-Api-Key': self.config.api_key, 'X-Api-Resource-Id': RESOURCE_ID,
                'X-Api-Request-Id': str(uuid4()), 'Content-Type': 'application/json',
            }, json={'user': {'uid': 'university-helper'}, 'req_params': {
                'text': text, 'speaker': self.config.speaker,
                'audio_params': {'format': 'mp3', 'sample_rate': 24000, 'speech_rate': 0},
                'additions': json.dumps({'explicit_language': 'en'}),
            }}, timeout=(min(5, deadline-time.monotonic()), min(10, deadline-time.monotonic())),
                allow_redirects=False, stream=True)
            if response.status_code != 200:
                raise SpeechError('语音服务未接受请求；请检查开通状态、Key 和音色权限，未自动重试')
            pending, audio, received, finished = b'', bytearray(), 0, False

            def event(line):
                nonlocal finished
                if not line.strip():
                    return
                if len(line) > MAX_EVENT or finished:
                    raise SpeechError('语音服务响应格式异常')
                payload = json.loads(line)
                if not isinstance(payload, dict) or type(payload.get('code')) is not int:
                    raise SpeechError('语音服务响应格式异常')
                if payload['code'] == 20000000:
                    if payload.get('data') not in (None, ''):
                        raise SpeechError('语音服务结束事件异常')
                    finished = True
                elif payload['code'] == 0:
                    value = payload.get('data')
                    if value not in (None, ''):
                        if not isinstance(value, str):
                            raise SpeechError('语音音频响应格式异常')
                        audio.extend(base64.b64decode(value, validate=True))
                        if len(audio) > MAX_AUDIO:
                            raise SpeechError('合成音频超过大小限制')
                else:
                    raise SpeechError('语音合成失败，未自动重试')

            for chunk in response.iter_content(16384):
                check()
                received += len(chunk)
                if received > MAX_RESPONSE:
                    raise SpeechError('语音服务响应超过大小限制')
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    event(line)
                if len(pending) > MAX_EVENT:
                    raise SpeechError('语音服务单条响应超过大小限制')
            if pending:
                event(pending)
            check()
            result = bytes(audio)
            if not finished or not mp3_audio(result):
                raise SpeechError('未收到完整的 MP3 合成成功结果，音频未采用')
            return result
        except SpeechError:
            raise
        except (requests.RequestException, ValueError, UnicodeError, binascii.Error):
            # Never echo the provider body, URL, headers, text or API key.
            raise SpeechError('语音请求中断或响应无效，音频未采用；未自动重试') from None
        finally:
            if response is not None:
                response.close()
