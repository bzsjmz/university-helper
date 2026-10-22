"""Owner-scoped experimental sessions and cancellable child processes.

Sessions expire after an hour; task records after two hours. Neither credentials
nor tasks persist across a backend restart. Stopping kills the whole worker
process group and waits for its exit before publishing a terminal status.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from app.core.credential_crypto import decrypt_str, encrypt_str
from .clients import PlatformError, course_url, storage_state


class EnglishManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.tasks = {}
        self.processes = set()
        self.closing = False

    def cleanup(self):
        now = time.monotonic()
        with self.lock:
            for sid, session in list(self.sessions.items()):
                if now - session['created'] > 3600 and not session.get('busy'):
                    del self.sessions[sid]
            for tid, task in list(self.tasks.items()):
                if task['status'] not in ('running', 'stopping') and now - task['updated'] > 7200:
                    del self.tasks[tid]

    @staticmethod
    def public(task):
        return {key: task[key] for key in ('id', 'platform', 'course_name', 'item_name',
                'seconds', 'status', 'message', 'started_at')}

    @staticmethod
    def kill(process):
        # A crashed worker may leave Chromium descendants in its process group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def execute(self, request, task=None):
        with self.lock:
            if self.closing:
                raise PlatformError('服务正在停止，请稍后重新连接')
            process = self._spawn()
            self.processes.add(process)
        try:
            return self._communicate(process, request, task)
        finally:
            self.kill(process)
            with self.lock:
                self.processes.discard(process)

    @staticmethod
    def _spawn():
        return subprocess.Popen([sys.executable, '-B', '-m',
            'app.services.course.english.worker'], cwd=Path(__file__).resolve().parents[4],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, env={key: value for key, value in os.environ.items()
                if key in {'PATH', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'PLAYWRIGHT_BROWSERS_PATH', 'XDG_RUNTIME_DIR'}})

    def _communicate(self, process, request, task):
        try:
            if task is not None:
                with self.lock:
                    task['process'] = process
                    cancelled = task['cancel'].is_set()
                if cancelled:
                    self.kill(process)
                    return {}
            raw, _ = process.communicate(json.dumps(request).encode(),
                                        timeout=request.get('seconds', 0) + 120)
            if len(raw) > 2_000_000 or process.returncode:
                raise PlatformError('平台操作失败或已停止')
            result = json.loads(raw)
            if not result.get('ok'):
                raise PlatformError(result.get('message', '平台操作失败'))
            return result
        except (subprocess.TimeoutExpired, ValueError):
            raise PlatformError('平台操作超时或返回格式异常') from None

    def connect(self, owner, platform, credential, course=''):
        self.cleanup()
        if platform == 'unipus':
            course_url(course)
            storage_state(credential)
        with self.lock:
            if sum(s['owner'] == owner for s in self.sessions.values()) >= 10:
                raise PlatformError('请先断开不用的试用会话（每个用户最多保留 10 个）')
        result = self.execute({'platform': platform, 'credential': credential,
                               'course': course, 'action': 'connect'})
        sid = uuid4().hex
        session = {'owner': owner, 'platform': platform, 'credential': encrypt_str(credential),
                   'courses': result['courses'], 'items': {}, 'created': time.monotonic(), 'busy': False}
        if platform == 'unipus':
            session['items'][course] = result['items']
        with self.lock:
            self.sessions[sid] = session
        return {'session_id': sid, 'courses': result['courses']}

    def session(self, owner, sid):
        self.cleanup()
        session = self.sessions.get(sid)
        if not session or session['owner'] != owner:
            raise PlatformError('会话不存在或已过期，请重新连接')
        return session

    def items(self, owner, sid, course):
        with self.lock:
            session = self.session(owner, sid)
            if session['busy']:
                raise PlatformError('该会话正在执行任务，请先停止并等待结束')
            if course not in {c['id'] for c in session['courses']}:
                raise PlatformError('请先选择当前账号中的课程')
            session['busy'] = True
        try:
            result = self.execute({'platform': session['platform'], 'credential': decrypt_str(session['credential']),
                                   'course': course, 'action': 'items'})
            with self.lock:
                session['items'][course] = result['items']
            return result
        finally:
            with self.lock:
                session['busy'] = False

    def start(self, owner, sid, course, item, seconds):
        with self.lock:
            session = self.session(owner, sid)
            if session['busy']:
                raise PlatformError('该会话已有操作正在执行')
            selected = next((i for i in session['items'].get(course, []) if i['id'] == item), None)
            if selected is None:
                raise PlatformError('请先读取并选择课程内容')
            task = {'id': uuid4().hex, 'owner': owner, 'platform': session['platform'],
                    'course_name': next(c['name'] for c in session['courses'] if c['id'] == course),
                    'item_name': selected['name'], 'seconds': seconds, 'status': 'running',
                    'message': '限时学习会话执行中', 'started_at': time.time(),
                    'updated': time.monotonic(), 'cancel': threading.Event()}
            session['busy'] = True
            self.tasks[task['id']] = task
            def run():
                try:
                    result = self.execute({'platform': session['platform'], 'action': 'study',
                        'credential': decrypt_str(session['credential']), 'course': course,
                        'item': item, 'seconds': seconds, 'expected_name': selected['name']}, task)
                    with self.lock:
                        task.update(status=result.get('status', 'failed'), message=result.get('message', '操作未完成'))
                except Exception as exc:
                    with self.lock:
                        task.update(status='failed', message=str(exc) if isinstance(exc, PlatformError) else '无法启动平台任务')
                finally:
                    with self.lock:
                        if task['cancel'].is_set():
                            task.update(status='cancelled', message='会话已停止，已提交的平台时长不会撤回')
                        task.pop('process', None)
                        task['updated'] = time.monotonic()
                        session['busy'] = False
            try:
                threading.Thread(target=run, daemon=True, name='english-learning').start()
            except Exception:
                session['busy'] = False
                del self.tasks[task['id']]
                raise PlatformError('无法启动任务线程，请稍后重试') from None
            return self.public(task)

    def list(self, owner):
        self.cleanup()
        with self.lock:
            return [self.public(t) for t in self.tasks.values() if t['owner'] == owner]

    def stop(self, owner, tid):
        with self.lock:
            task = self.tasks.get(tid)
            if not task or task['owner'] != owner:
                raise PlatformError('任务不存在')
            if task['status'] not in ('running', 'stopping'):
                return self.public(task)
            task['cancel'].set()
            task['status'] = 'stopping'
            process = task.get('process')
        if process:
            self.kill(process)
        with self.lock:
            return self.public(task)

    def disconnect(self, owner, sid):
        with self.lock:
            session = self.session(owner, sid)
            if session['busy']:
                raise PlatformError('请先停止任务并等待结束，再断开会话')
            del self.sessions[sid]

    def shutdown(self):
        with self.lock:
            self.closing = True
            processes = list(self.processes)
            tasks = [(t['owner'], t['id']) for t in self.tasks.values()
                     if t['status'] in ('running', 'stopping')]
        for owner, tid in tasks:
            self.stop(owner, tid)
        for process in processes:
            self.kill(process)


english_manager = EnglishManager()
