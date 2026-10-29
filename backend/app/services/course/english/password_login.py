"""Short-lived, owner-scoped login browsers; credentials never reach API replies."""
import json
import os
import selectors
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from .clients import PlatformError
from .process_runtime import pipe_selector


class PasswordLogins:
    def __init__(self, manager):
        self.manager = manager
        # Session methods can call cleanup while holding the manager lock.
        # One reentrant state lock avoids inverse login/session lock ordering.
        self.lock = manager.lock
        self.jobs = {}

    def start(self, owner, platform, username, password):
        return self._start(owner, platform, {'username': username, 'password': password})

    def start_wechat(self, owner):
        return self._start(owner, 'welearn', {'method': 'wechat'})

    def _start(self, owner, platform, login_request):
        if platform not in ('welearn', 'unipus_ai'):
            raise PlatformError('老版 U校园已移除，请选择 U校园 AI版')
        self.cleanup()
        with self.lock, self.manager.lock:
            if self.manager.closing:
                raise PlatformError('服务正在停止，请稍后登录')
            pending = [j for j in self.jobs.values() if j['process'] is not None]
            if any(j['owner'] == owner for j in pending):
                raise PlatformError('请先完成或取消当前登录')
            # Only pending login browsers are bounded; course task concurrency is unchanged.
            if len(pending) >= 2:
                raise PlatformError('当前登录窗口繁忙，请稍后重试')
            temporary = tempfile.TemporaryDirectory(prefix='english-login-')
            try:
                process = self.manager._spawn(temporary_dir=temporary.name)
            except Exception:
                temporary.cleanup()
                raise PlatformError('无法打开登录页面，请稍后重试') from None
            self.manager.processes.add(process)
            lid = uuid4().hex
            job = {'owner': owner, 'platform': platform, 'method': login_request.get('method', 'password'), 'process': process,
                   'created': time.monotonic(), 'io': threading.Lock(), 'buffer': b'', 'temporary': temporary,
                   'public': {'login_id': lid, 'status': 'starting', 'message': '正在登录'}}
            self.jobs[lid] = job
        return self.exchange(owner, lid, {'action': 'login', 'platform': platform,
                            **login_request})

    def _job(self, owner, lid):
        job = self.jobs.get(lid)
        if not job or job['owner'] != owner:
            raise PlatformError('登录窗口不存在或已过期，请重新登录')
        return job

    def list(self, owner):
        self.cleanup()
        with self.lock:
            return [dict(j['public']) for j in self.jobs.values()
                    if j['owner'] == owner and j['process'] is not None]

    def _close(self, job):
        process, job['process'] = job['process'], None
        if process is not None:
            # EOF lets Playwright close its browser/profile on normal handoff or
            # cancellation. A hung worker is still killed as one process group.
            try:
                process.stdin.close()
                process.wait(timeout=3)
            except (subprocess.TimeoutExpired, BrokenPipeError, OSError):
                self.manager.kill(process)
            with self.manager.lock:
                self.manager.processes.discard(process)
            for stream in (process.stdin, process.stdout):
                stream.close()
            job['temporary'].cleanup()

    @staticmethod
    def _read(job, process):
        deadline = time.monotonic() + 45
        with pipe_selector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if job['process'] is not process:
                    break
                if b'\n' in job['buffer']:
                    line, job['buffer'] = job['buffer'].split(b'\n', 1)
                    return json.loads(line)
                if not selector.select(timeout=min(1, max(0, deadline - time.monotonic()))):
                    continue
                data = os.read(process.stdout.fileno(), 65536)
                if not data:
                    break
                job['buffer'] += data
                if len(job['buffer']) > 2_000_000:
                    break
        raise PlatformError('登录页面未能正常响应，请重新登录')

    def exchange(self, owner, lid, command=None):
        self.cleanup()
        with self.lock:
            job = self._job(owner, lid)
        acquired = job['io'].acquire(timeout=2) if command is not None else job['io'].acquire(blocking=False)
        if not acquired:
            if command is not None:
                raise PlatformError('正在处理上一步操作，请稍候')
            return dict(job['public'])
        failure_code = None
        try:
            with self.lock:
                self._job(owner, lid)
                process = job['process']
                if process is None:
                    return dict(job['public'])
            # A slow OAuth callback can outlive one HTTP poll. Keep one
            # outstanding worker command; never enqueue duplicate actions.
            if not job.get('awaiting_reply'):
                process.stdin.write(json.dumps(command or {'action': 'refresh'}).encode() + b'\n')
                process.stdin.flush()
                job['awaiting_reply'] = True
            result = self._read(job, process)
            job['awaiting_reply'] = False
            if not result.get('ok'):
                failure_code = result.get('error_code')
                raise PlatformError('平台登录未完成，请重新登录')
            with self.lock:
                self._job(owner, lid)  # Cancellation must not resurrect a session.
                if result.get('status') == 'connected':
                    session = self.manager.adopt_login(owner, job['platform'], result)
                    job['public'] = {'login_id': lid, 'status': 'connected', 'session': session}
                    self._close(job)
                elif result.get('status') == 'verification':
                    image = result.get('image', '')
                    if not isinstance(image, str) or len(image) > 1_500_000:
                        raise PlatformError('登录验证画面过大，请重新登录')
                    job['public'] = {'login_id': lid, 'status': 'verification', 'image': image,
                                     'message': result.get('message', '请完成平台登录验证'),
                                     'image_type': 'png' if result.get('image_type') == 'png' else 'jpeg',
                                     'view_state': result.get('view_state') if result.get('view_state') in ('blank', 'loading') else 'ready',
                                     'display_mode': 'qr' if result.get('display_mode') == 'qr' else 'page',
                                     'qr_status': result.get('qr_status') if result.get('qr_status') in ('waiting', 'scanned', 'expired') else None}
                else:
                    raise PlatformError('登录页面返回异常，请重新登录')
                return dict(job['public'])
        except Exception as exc:
            if (isinstance(exc, PlatformError) and str(exc) == '登录页面未能正常响应，请重新登录'
                    and job['process'] is process and process.poll() is None
                    and time.monotonic() - job['created'] < 600):
                with self.lock:
                    job['public'] = {'login_id': lid, 'status': 'verification', 'image': '',
                                     'view_state': 'loading', 'display_mode': 'page',
                                     'message': '正在等待平台官网完成登录跳转，请稍候；可以取消登录。'}
                return dict(job['public'])
            if failure_code is None:
                failure_code = ('worker_response_timeout' if isinstance(exc, PlatformError)
                                and str(exc) == '登录页面未能正常响应，请重新登录' else 'login_failed')
            reasons = {
                'platform_timeout': '平台官网加载或跳转超时，请重新获取二维码后重试',
                'browser_navigation': '微信授权后浏览器跳转失败，请重新获取二维码后重试',
                'worker_response_timeout': '登录浏览器未及时响应，请重试；这不代表二维码过期',
            }
            with self.lock:
                self._close(job)
                job['public'] = {'login_id': lid, 'status': 'failed',
                                 'error_code': failure_code if failure_code in reasons else 'login_failed',
                                 'message': reasons.get(failure_code, ('微信授权未完成，请重新获取二维码；若重复失败请反馈此提示' if job['method'] == 'wechat'
                                             else '登录未完成，请检查账号密码或官网验证后重试'))}
            return dict(job['public'])
        finally:
            job['io'].release()

    def cancel(self, owner, lid):
        with self.lock:
            job = self._job(owner, lid)
            del self.jobs[lid]
            self._close(job)
            # A successful reply can race with cancelling an abandoned browser.
            session = job['public'].get('session')
        if session:
            try:
                self.manager.disconnect(owner, session['session_id'])
            except PlatformError:
                pass

    def cleanup(self):
        now = time.monotonic()
        with self.lock:
            for lid, job in list(self.jobs.items()):
                ttl = 600 if job['process'] is not None else 660
                if now - job['created'] > ttl:
                    del self.jobs[lid]
                    self._close(job)

    def shutdown(self):
        with self.lock:
            for job in self.jobs.values():
                self._close(job)
            self.jobs.clear()
