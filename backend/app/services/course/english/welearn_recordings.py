"""Recording diagnostics and opt-in experimental completion records for WE Learn."""


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


def bypass_recordings(frame, *, apply=False):
    from pathlib import Path
    script = Path(__file__).with_name('recording_bypass.js').read_text(encoding='utf-8')
    try:
        result = frame.evaluate(script, {'apply': apply})
        if (isinstance(result, dict) and result.get('ok') is True
                and type(result.get('count')) is int and 0 <= result['count'] <= 100):
            return result['count']
    except Exception:
        pass
    return None
