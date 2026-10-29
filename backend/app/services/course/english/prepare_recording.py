"""Operator-only TTS preview. No WE Learn upload, API route or browser key access."""
import argparse
import json
import os
from pathlib import Path
import sys

from .speech import SpeechConfig, SpeechError, VolcanoSpeech
from .welearn_recordings import RecordingJob, prepare_recording


def main():
    parser = argparse.ArgumentParser(description='准备绑定课节的语音试听文件；不会提交 WE Learn')
    parser.add_argument('--job', type=Path, required=True, help='私有录音任务 JSON')
    parser.add_argument('--output-dir', type=Path, required=True, help='本项目专用私有输出目录')
    parser.add_argument('--preview', action='store_true', help='明确调用付费 TTS 生成试听；不指定则只检查任务')
    args = parser.parse_args()
    created = []
    try:
        with args.job.open('rb') as handle:
            raw = handle.read(50001)
        if len(raw) > 50000:
            raise SpeechError('录音任务文件过大')
        job = RecordingJob.from_detail(**json.loads(raw))
        if not args.preview:
            print(json.dumps({'status': 'validated', 'upload_available': False,
                              'synthesis_requested': False, 'platform_completed': False}))
            return 0
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if args.output_dir.is_symlink() or args.output_dir.stat().st_mode & 0o077:
            raise SpeechError('音频输出目录必须为私有目录（权限 0700）')
        config = SpeechConfig.from_environment()
        name = job.artifact_id(config.speaker)
        audio_path = args.output_dir / (name + '.mp3')
        manifest_path = args.output_dir / (name + '.json')
        if audio_path.exists() or manifest_path.exists():
            raise SpeechError('此任务已有试听文件，请先核对，未重复调用 TTS')
        # Reserve both private paths before the chargeable call; never overwrite
        # another operator's output or leave a successful-looking partial file.
        for path in (audio_path, manifest_path):
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created.append(path)
            os.close(fd)
        speech = VolcanoSpeech(config)
        try:
            audio, manifest = prepare_recording(job, speech, preview=True)
        finally:
            speech.close()
        audio_path.write_bytes(audio)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        created.clear()
        print(json.dumps({'status': 'prepared', 'artifact_id': name, 'platform_completed': False}))
        return 0
    except (SpeechError, OSError, ValueError, TypeError):
        # No traceback, job contents, key values or remote errors in console logs.
        print('录音准备未完成；请检查任务字段、私有目录和语音配置，未提交 WE Learn', file=sys.stderr)
        return 1
    finally:
        for path in created:
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
