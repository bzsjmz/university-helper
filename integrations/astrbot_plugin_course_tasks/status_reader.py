import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LABELS = {'running': '进行中', 'pending': '准备中', 'paused': '已暂停', 'cancelling': '停止中',
          'stopping': '停止中', 'completed': '已完成', 'failed': '失败', 'error': '失败', 'cancelled': '已停止'}


class StatusUnavailable(Exception):
    pass


def read_status(path, now=None):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024: raise ValueError('size')
        snapshot = json.loads(raw)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('generated_at'), str): raise ValueError('shape')
        generated = datetime.fromisoformat(snapshot['generated_at'].replace('Z', '+00:00'))
        if generated.tzinfo is None: raise ValueError('timezone')
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
        if not -15 <= age <= 30: raise ValueError('stale')
        if snapshot.get('version') != 1 or not isinstance(snapshot.get('counts'), dict): raise ValueError('version')
        if not isinstance(snapshot.get('tasks'), list) or len(snapshot['tasks']) > 200: raise ValueError('records')
        for task in snapshot['tasks']:
            if not isinstance(task, dict): raise ValueError('record')
            if not isinstance(task.get('status'), str): raise ValueError('status')
        return snapshot
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise StatusUnavailable('课程状态源不可用或已超过 30 秒未更新，请稍后重试。') from None


def safe_text(value, limit=120):
    return ' '.join(str(value or '').split())[:limit]


def number(value):
    try: return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError): return 0


def format_status(snapshot, page=1):
    tasks = snapshot['tasks']
    pages = max(1, math.ceil(len(tasks) / 5))
    if not 1 <= page <= pages:
        return f'页码超出范围，当前共有 {pages} 页。用法：/任务状态 [页码]'
    counts = snapshot['counts']
    timestamp = datetime.fromisoformat(snapshot['generated_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Taipei'))
    lines = ['学习通任务状态',
             f"记录 {number(snapshot.get('total'))}｜运行 {number(counts.get('running'))}｜暂停 {number(counts.get('paused'))}｜失败 {number(counts.get('failed')) + number(counts.get('error'))}｜完成 {number(counts.get('completed'))}",
             f'同步时间 {timestamp:%m-%d %H:%M:%S}｜第 {page}/{pages} 页']
    if not tasks: lines.append('暂无任务记录。')
    for index, task in enumerate(tasks[(page - 1) * 5:page * 5], (page - 1) * 5 + 1):
        lines.extend(['', f"{index}. {safe_text(task.get('account_name'), 64) or '旧任务（账号未记录）'} · {safe_text(task.get('account_label'), 32) or '账号未记录'}【{LABELS.get(task.get('status'), '未知')}】",
                      f"课程：{safe_text(task.get('course')) or '—'}",
                      f"章节：{safe_text(task.get('chapter')) or '—'}",
                      f"进度：课程 {number(task.get('courses_done'))}/{number(task.get('courses_total'))}｜章节 {number(task.get('chapters_done'))}/{number(task.get('chapters_total'))}｜失败课程 {number(task.get('courses_failed'))}",
                      f"任务 {safe_text(task.get('task_id'), 8)}"])
    if snapshot.get('truncated'): lines.append('\n仅展示最近 200 条记录，完整记录请看网页工作台。')
    if pages > page: lines.append(f'\n下一页：/任务状态 {page + 1}')
    lines.append('\n进度来自执行记录，未核验平台任务点。')
    return '\n'.join(lines)
