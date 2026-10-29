"""Prepare scoped audio; WE Learn App upload is not an implemented transport."""
from dataclasses import dataclass, field
import hashlib
import json

from .speech import SpeechError, validate_text


class RecordingUploadUnavailable(SpeechError):
    pass


@dataclass(frozen=True)
class RecordingJob:
    owner: str
    course: str
    item: str
    recorder: str
    text: str = field(repr=False)
    scored: bool = False

    def __post_init__(self):
        for value in (self.owner, self.course, self.item, self.recorder):
            if (not isinstance(value, str) or not value.strip() or value != value.strip()
                    or len(value) > 256 or any(ord(c) < 32 for c in value)):
                raise SpeechError('录音必须绑定有效的账号范围、课程、课节和录音题 ID')
        if type(self.scored) is not bool:
            raise SpeechError('录音题型无效')
        object.__setattr__(self, 'text', validate_text(self.text))

    @classmethod
    def from_detail(cls, *, owner, course, item, detail, answer=None):
        if not isinstance(detail, dict) or detail.get('kind') != 'recording':
            raise SpeechError('此录音控件尚未适配')
        scored = detail.get('scored')
        if type(scored) is not bool:
            raise SpeechError('未确认录音题型')
        # Read-aloud tasks must use their actual script, not a generic sentence.
        text = detail.get('script') if scored else answer
        return cls(owner, course, item, detail.get('id'), text, scored)

    def binding(self, speaker):
        return {'version': 1, 'platform': 'welearn', 'owner': self.owner,
                'course': self.course, 'item': self.item, 'recorder': self.recorder,
                'scored': self.scored, 'text_sha256': hashlib.sha256(self.text.encode()).hexdigest(),
                'resource_id': 'seed-tts-2.0', 'speaker': speaker, 'format': 'mp3', 'sample_rate': 24000}

    def artifact_id(self, speaker):
        binding = json.dumps(self.binding(speaker), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(binding.encode()).hexdigest()


def prepare_recording(job, speech, *, preview=False, deadline=None, should_stop=None):
    # Normal lesson execution must not spend money for an unavailable upload.
    # Explicit preview is a separate operator action; it never reports completion.
    if not preview:
        raise RecordingUploadUnavailable('WE Learn App 录音上传协议尚未接通，未调用语音合成')
    audio = speech.synthesize(job.text, deadline=deadline, should_stop=should_stop)
    return audio, {**job.binding(speech.config.speaker), 'status': 'prepared',
                   'audio_sha256': hashlib.sha256(audio).hexdigest(), 'platform_completed': False}


def recording_message(details, count, *, mixed=False):
    details = details if isinstance(details, list) else []
    scored = sum(1 for detail in details if isinstance(detail, dict) and detail.get('scored') is True)
    ordinary = sum(1 for detail in details if isinstance(detail, dict) and
                   detail.get('kind') == 'recording' and detail.get('scored') is False)
    kinds = []
    if ordinary:
        kinds.append(f'普通录音 {ordinary} 项')
    if scored:
        kinds.append(f'朗读评分 {scored} 项')
    suffix = '（' + '，'.join(kinds) + '）' if kinds else ''
    prefix = '文字题已填写复核，' if mixed else ''
    return (f'{prefix}本页还有 {count} 项录音{suffix}；WE Learn App 录音上传尚未接通，'
            '请在官方 App 完成口语录音；未自动提交' + ('，文字保存状态未确认' if mixed else ''))
