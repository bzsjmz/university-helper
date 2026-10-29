"""Owner-scoped experimental sessions and cancellable child processes.

Sessions expire after an hour; task records after two hours. Neither credentials
nor tasks persist across a backend restart. Stopping kills the whole worker
process group and waits for its exit before publishing a terminal status.
"""
import json
import os
import signal
import selectors
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from app.core.credential_crypto import decrypt_str, encrypt_str
from .clients import PlatformError, course_url, storage_state
from .process_runtime import pipe_selector, process_options, windows_environment, worker_command


class EnglishManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.tasks = {}
        self.processes = set()
        self.closing = False
        from .password_login import PasswordLogins
        self.logins = PasswordLogins(self)

    def cleanup(self):
        now = time.monotonic()
        with self.lock:
            for sid, session in list(self.sessions.items()):
                if now - session['created'] > 3600 and not session.get('busy'):
                    del self.sessions[sid]
            for tid, task in list(self.tasks.items()):
                if task['status'] not in ('running', 'stopping') and now - task['updated'] > 7200:
                    del self.tasks[tid]
        self.logins.cleanup()

    @staticmethod
    def public(task):
        result = {key: task[key] for key in ('id', 'platform', 'course_name', 'item_name',
                'seconds', 'status', 'message', 'started_at', 'mode', 'submit', 'allow_ai')}
        result['counts'] = dict(task.get('counts', {}))
        result.update(total_items=task.get('total_items', 1), completed_items=task.get('completed_items', 0),
                      current_item=task.get('current_item', task['item_name']), results=list(task.get('results', [])))
        return result

    @staticmethod
    def kill(process):
        # A crashed worker may leave Chromium descendants in its process group.
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
            process.wait(timeout=10)
            return
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
    def _spawn(temporary_dir=None):
        environment = {key: value for key, value in os.environ.items()
            if key in {'PATH', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'PLAYWRIGHT_BROWSERS_PATH', 'XDG_RUNTIME_DIR'}}
        if sys.platform == 'win32':
            environment.update(windows_environment())
        if getattr(sys, 'frozen', False):
            environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        if temporary_dir is not None:
            environment['TMPDIR'] = temporary_dir
        return subprocess.Popen(worker_command(), cwd=Path(__file__).resolve().parents[4],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            **process_options(), env=environment)

    def _communicate(self, process, request, task):
        try:
            if task is not None:
                with self.lock:
                    task['process'] = process
                    cancelled = task['cancel'].is_set()
                if cancelled:
                    self.kill(process)
                    return {}
            if request['action'] == 'answer':
                return self._answer_channel(process, request, task)
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

    def _answer_channel(self, process, request, task):
        from .answering import EnglishSolver
        deadline = time.monotonic() + request['seconds']
        solver = EnglishSolver(request['platform'], request['course'], request.get('allow_ai', False),
            lambda: (task is not None and task['cancel'].is_set()) or time.monotonic() >= deadline)
        selector = pipe_selector()
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            process.stdin.write(json.dumps(request).encode() + b'\n')
            process.stdin.flush()
            buffer = b''
            while time.monotonic() < deadline:
                if task is not None and task['cancel'].is_set():
                    raise PlatformError('任务已停止')
                if not selector.select(timeout=min(1, max(0, deadline - time.monotonic()))):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise PlatformError('答题工作进程已退出，平台保存/提交状态请在官网核对')
                buffer += chunk
                if len(buffer) > 2_000_000:
                    raise PlatformError('答题页面数据过大，已停止')
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    event = json.loads(line)
                    if event.get('event') == 'question':
                        result = solver.solve(event.get('question'))
                        if task is not None:
                            with self.lock:
                                task['counts']['questions'] = task['counts'].get('questions', 0) + 1
                                field = 'answered' if result.get('answer') is not None else 'missing'
                                task['counts'][field] = task['counts'].get(field, 0) + 1
                                source_field = {'native': 'native_answers', 'cache': 'cache_hits',
                                                'ai': 'ai_answers'}.get(result.get('source'))
                                if source_field:
                                    task['counts'][source_field] = task['counts'].get(source_field, 0) + 1
                        process.stdin.write(json.dumps(result).encode() + b'\n')
                        process.stdin.flush()
                    elif event.get('ok'):
                        process.wait(timeout=10)
                        return event
                    else:
                        raise PlatformError(event.get('message', '答题工作进程失败'))
            raise PlatformError('答题总预算耗尽，进程已停止；请在官网核对保存/提交状态')
        except (BrokenPipeError, ValueError, subprocess.TimeoutExpired):
            raise PlatformError('答题通道中断，请在官网核对保存/提交状态') from None
        finally:
            selector.close()
            solver.close()

    def connect(self, owner, platform, credential, course=''):
        if platform not in ('welearn', 'unipus_ai'):
            raise PlatformError('老版 U校园已移除，请选择 U校园 AI版')
        self.cleanup()
        if platform == 'unipus_ai':
            if course:
                course_url(course)
            storage_state(credential)
        with self.lock:
            if sum(s['owner'] == owner for s in self.sessions.values()) >= 10:
                raise PlatformError('请先断开不用的试用会话（每个用户最多保留 10 个）')
        result = self.execute({'platform': platform, 'credential': credential,
                               'course': course, 'action': 'connect'})
        return self.adopt_login(owner, platform, {'credential': credential, **result}, course)

    def adopt_login(self, owner, platform, result, course=''):
        credential = result['credential']
        courses = result['courses']
        if platform == 'unipus_ai':
            storage_state(credential)
            for entry in courses:
                course_url(entry['id'])
        sid = uuid4().hex
        session = {'owner': owner, 'platform': platform, 'credential': encrypt_str(credential),
                   'courses': courses, 'items': {}, 'created': time.monotonic(), 'busy': False}
        if platform == 'unipus_ai' and course and 'items' in result:
            session['items'][course] = result['items']
        with self.lock:
            if self.closing or sum(s['owner'] == owner for s in self.sessions.values()) >= 10:
                raise PlatformError('请先断开不用的会话，或稍后重试')
            self.sessions[sid] = session
        return {'session_id': sid, 'courses': courses}

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

    def start(self, owner, sid, course, item, seconds, mode='study', submit=False, allow_ai=False, end_item=''):
        with self.lock:
            session = self.session(owner, sid)
            if session['busy']:
                raise PlatformError('该会话已有操作正在执行')
            catalog = session['items'].get(course, [])
            ids = [i['id'] for i in catalog]
            end_item = end_item or item
            if item not in ids or end_item not in ids or len(ids) != len(set(ids)):
                raise PlatformError('请先读取并选择课程内容')
            first, last = ids.index(item), ids.index(end_item)
            if first > last:
                raise PlatformError('范围终点必须在起点之后')
            selected_items = [dict(i) for i in catalog[first:last + 1]]
            selected = selected_items[0]
            task = {'id': uuid4().hex, 'owner': owner, 'platform': session['platform'],
                    'course_name': next(c['name'] for c in session['courses'] if c['id'] == course),
                    'item_name': selected['name'] if first == last else selected['name'] + ' → ' + selected_items[-1]['name'],
                    'total_items': len(selected_items), 'completed_items': 0, 'current_item': selected['name'], 'results': [], 'seconds': seconds, 'status': 'running',
                    'message': '限时学习会话执行中', 'started_at': time.time(),
                    'updated': time.monotonic(), 'cancel': threading.Event(),
                    'mode': mode, 'submit': submit, 'allow_ai': allow_ai, 'counts': {}}
            session['busy'] = True
            self.tasks[task['id']] = task
            def run():
                try:
                    totals = {}
                    needs_manual = False
                    for selected in selected_items:
                        if task['cancel'].is_set():
                            break
                        with self.lock:
                            task.update(current_item=selected['name'], message='正在处理：' + selected['name'], counts=dict(totals))
                        result = self.execute({'platform': session['platform'], 'action': 'answer' if mode == 'answer' else 'study',
                            'submit': submit, 'allow_ai': allow_ai,
                            'credential': decrypt_str(session['credential']), 'course': course,
                            'item': selected['id'], 'seconds': seconds, 'expected_name': selected['name']}, task)
                        for key, value in result.get('counts', {}).items():
                            if type(value) is int and value >= 0:
                                totals[key] = totals.get(key, 0) + value
                        state = result.get('status', 'failed')
                        needs_manual |= state != 'finished'
                        with self.lock:
                            task['results'].append({'name': selected['name'], 'status': state,
                                                    'message': result.get('message', '操作未完成')})
                            task.update(completed_items=len(task['results']), counts=dict(totals))
                    with self.lock:
                        task.update(status='needs_manual' if needs_manual else 'finished',
                            message=(task['results'][0]['message'] if len(selected_items) == 1 and task['results'] else
                                     f"范围执行结束：{len(task['results'])}/{len(selected_items)} 节，" + ('部分内容需人工处理' if needs_manual else '请核对平台进度')))
                except Exception as exc:
                    with self.lock:
                        task.update(status='failed', message=str(exc) if isinstance(exc, PlatformError) else '平台任务执行失败，请在官网核对保存/提交状态')
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
            tasks = [(t['owner'], t['id']) for t in self.tasks.values()
                     if t['status'] in ('running', 'stopping')]
        self.logins.shutdown()
        for owner, tid in tasks:
            self.stop(owner, tid)
        with self.lock:
            processes = list(self.processes)
        for process in processes:
            self.kill(process)


english_manager = EnglishManager()
