"""Saved WE Learn logins, per-account exclusion and independent task threads."""
import copy
import json
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from app.core.credential_crypto import decrypt_str, encrypt_str
from .clients import PlatformError
from .manager import english_manager
from .welearn_store import WelearnStore, account_fingerprint

ACTIVE = ('running', 'stopping')


class WelearnWorkbench:
    def __init__(self, engine, store=None):
        self.engine = engine
        self.store = store or WelearnStore()
        self.lock = engine.lock
        self.tasks = {}
        self.busy = set()
        self.catalogs = {}
        self.saved_sessions = {}
        self.closing = False

    @staticmethod
    def public_account(row):
        return {k: str(row[k]) for k in ('id', 'label', 'created_at', 'updated_at')}

    @staticmethod
    def public_task(task):
        return copy.deepcopy({k: v for k, v in task.items()
                              if k not in ('owner', 'cancel', 'process', '_config', '_key')})

    def persist(self, task):
        public = self.public_task(task)
        public['_config'] = task['_config']
        self.store.put_task(task['owner'], public)

    @contextmanager
    def reserve(self, owner, aid):
        with self.lock:
            row = self.store.account(owner, aid)
            key = row['account_key']
            if key in self.busy or self.closing:
                raise PlatformError('该账号正在执行操作，请等待任务结束或停止后再操作')
            self.busy.add(key)
        try:
            yield row
        finally:
            with self.lock:
                self.busy.discard(key)

    def accounts(self, owner):
        with self.lock:
            return [{**self.public_account(row), 'busy': row['account_key'] in self.busy}
                    for row in self.store.accounts(owner)]

    def save_session(self, owner, sid, label='', aid=''):
        # Reserve the temporary login while independently checking official uid.
        with self.lock:
            now = time.monotonic()
            self.saved_sessions = {key: value for key, value in self.saved_sessions.items() if now - value[1] < 3600}
            receipt = self.saved_sessions.get((str(owner), sid))
            if receipt:
                row = self.store.account(owner, receipt[0])
                return {**self.public_account(row), 'busy': row['account_key'] in self.busy}
            session = self.engine.session(owner, sid)
            if session['platform'] != 'welearn' or session['busy']:
                raise PlatformError('请选择空闲的 WE Learn 登录')
            if aid:
                row = self.store.account(owner, aid)
                if row['account_key'] in self.busy:
                    raise PlatformError('请先停止该账号任务并等待结束')
            session['busy'] = True
            credential = decrypt_str(session['credential'])
        try:
            identity = self.engine.execute({'platform': 'welearn', 'action': 'account', 'credential': credential})
            key = account_fingerprint('welearn:' + identity['uid'])
            with self.lock:
                if key in self.busy or self.closing:
                    raise PlatformError('该账号正在执行任务，请结束后更新登录')
                row = self.store.save_account(owner, identity['uid'], credential, label, aid)
                self.catalogs.pop((str(owner), row['id']), None)
                # Saved account now owns the encrypted login. Free the transient slot.
                self.engine.sessions.pop(sid, None)
                self.saved_sessions[(str(owner), sid)] = (row['id'], time.monotonic())
                return {**self.public_account(row), 'busy': False}
        finally:
            with self.lock:
                session['busy'] = False

    def rename(self, owner, aid, label):
        with self.reserve(owner, aid):
            self.store.rename(owner, aid, label)

    def delete_account(self, owner, aid):
        with self.reserve(owner, aid):
            self.store.delete_account(owner, aid)
            self.catalogs.pop((str(owner), aid), None)

    def courses(self, owner, aid):
        with self.reserve(owner, aid) as row:
            result = self.engine.execute({'platform': 'welearn', 'action': 'connect',
                                          'credential': decrypt_str(row['credential_ciphertext'])})
            with self.lock:
                cache = self.catalogs.setdefault((str(owner), aid), {'items': {}})
                cache['courses'] = copy.deepcopy(result['courses'])
            return result

    def items(self, owner, aid, course):
        with self.reserve(owner, aid) as row:
            with self.lock:
                cache = self.catalogs.get((str(owner), aid), {})
                if course not in {c['id'] for c in cache.get('courses', [])}:
                    raise PlatformError('请先读取该账号的课程')
            result = self.engine.execute({'platform': 'welearn', 'action': 'items', 'course': course,
                                          'credential': decrypt_str(row['credential_ciphertext'])})
            with self.lock:
                cache['items'][course] = copy.deepcopy(result['items'])
            return result

    def start(self, owner, aid, config):
        with self.lock:
            row = self.store.account(owner, aid)
            if row['account_key'] in self.busy or self.closing or self.engine.closing:
                raise PlatformError('该账号已有操作正在执行，请等待结束')
            cache = self.catalogs.get((str(owner), aid), {})
            courses = {c['id']: c for c in cache.get('courses', [])}
            chosen = []
            seen = set()
            for selection in config['selections']:
                cid = selection['course']
                ids = selection['items']
                catalog = cache.get('items', {}).get(cid, [])
                known = {i['id'] for i in catalog}
                if (cid in seen or cid not in courses or not ids or len(ids) != len(set(ids))
                        or len(known) != len(catalog) or not set(ids).issubset(known)):
                    raise PlatformError('所选课程或课节已变化，请重新读取目录并选择范围')
                seen.add(cid)
                chosen.extend({'course': cid, 'course_name': courses[cid]['name'], **dict(i)}
                              for i in catalog if i['id'] in set(ids))
            if not chosen or len(chosen) > 5000:
                raise PlatformError('请选择 1–5000 个课节')
            # Never widen restart scope; only the exact selected ids are stored.
            ciphertext = encrypt_str(json.dumps(config))
            if not ciphertext.startswith('fernet:'):
                raise PlatformError('任务配置保存需要启用凭据加密')
            task = {'id': uuid4().hex, 'owner': str(owner), 'account_id': aid,
                    'account_label': row['label'], 'status': 'running', 'message': '任务启动中',
                    'started_at': time.time(), 'total_items': len(chosen), 'completed_items': 0,
                    'current_item': '', 'results': [], 'counts': {},
                    'seconds': config['seconds'], 'submit': config['submit'],
                    'allow_ai': config['allow_ai'], 'recording_bypass': config['recording_bypass'],
                    '_config': ciphertext, '_key': row['account_key'], 'cancel': threading.Event()}
            self.persist(task)  # Fail admission before starting any platform writes.
            self.tasks[task['id']] = task
            self.busy.add(row['account_key'])
            try:
                threading.Thread(target=self.run, args=(task, row, chosen), daemon=True,
                                 name='welearn-workbench').start()
            except Exception:
                self.busy.discard(row['account_key'])
                task.update(status='failed', message='任务线程启动失败，请稍后重新启动')
                self.persist(task)
                raise PlatformError(task['message']) from None
            return self.public_task(task)

    def run(self, task, row, chosen):
        totals = {}
        try:
            for item in chosen:
                with self.lock:
                    if task['cancel'].is_set():
                        break
                    task.update(current_item=item['course_name'] + ' > ' + item['name'],
                                message='正在处理：' + item['name'], counts=dict(totals))
                try:
                    result = self.engine.execute({'platform': 'welearn', 'action': 'answer',
                        'course': item['course'], 'item': item['id'], 'expected_name': item['name'],
                        'credential': decrypt_str(row['credential_ciphertext']),
                        **{k: task[k] for k in ('seconds', 'submit', 'allow_ai', 'recording_bypass')}}, task)
                except PlatformError:
                    # A transport failure may follow a real submission: never replay it automatically.
                    raise PlatformError('平台操作中断，请核对登录和平台进度后重新启动；已完成课节会跳过') from None
                with self.lock:
                    if task['cancel'].is_set():
                        break
                    for key, value in result.get('counts', {}).items():
                        if type(value) is int and value >= 0:
                            totals[key] = totals.get(key, 0) + value
                    task['results'].append({'course': item['course_name'], 'name': item['name'],
                        'status': result.get('status', 'needs_manual'),
                        'message': result.get('message', '请核对平台进度')})
                    task.update(completed_items=len(task['results']), counts=dict(totals))
                    self.persist(task)
            with self.lock:
                manual = any(r['status'] != 'finished' for r in task['results'])
                task.update(status='needs_manual' if manual else 'finished',
                            message='所选范围执行结束，部分课节需人工处理' if manual else '所选范围执行结束，请查看逐节结果')
        except Exception:
            with self.lock:
                task.update(status='failed', message='任务中断，请核对登录、平台进度及保存状态后重新启动')
        finally:
            with self.lock:
                if task['cancel'].is_set():
                    task.update(status='cancelled', message='任务已停止，平台已提交内容会保留')
                task.pop('process', None)
                try:
                    self.persist(task)
                finally:
                    self.busy.discard(row['account_key'])
                    # History is durable; retain only currently executing threads in memory.
                    self.tasks.pop(task['id'], None)

    def list_tasks(self, owner):
        with self.lock:
            records = {t['id']: t for t in self.store.tasks(owner)}
            for tid, record in records.items():
                if record['status'] in ACTIVE and tid not in self.tasks:
                    record.update(status='interrupted', message='服务重启中断了执行，请重新启动；会读取平台进度并跳过已完成课节')
                    self.store.put_task(owner, record)
            records.update({tid: self.public_task(t) for tid, t in self.tasks.items() if t['owner'] == str(owner)})
            return [self.public_task(t) for t in sorted(records.values(), key=lambda t: t['started_at'], reverse=True)]

    def stop(self, owner, tid):
        with self.lock:
            task = self.tasks.get(tid)
            if not task or task['owner'] != str(owner):
                return self.public_task(self.store.task(owner, tid))
            task['cancel'].set()
            task.update(status='stopping', message='正在停止，请等待工作进程退出')
            self.persist(task)
            process = task.get('process')
        if process:
            self.engine.kill(process)
        with self.lock:
            return self.public_task(task)

    def restart(self, owner, tid):
        with self.lock:
            if tid in self.tasks:
                raise PlatformError('请先停止并等待原任务结束')
            old = self.store.task(owner, tid)
            config = json.loads(decrypt_str(old['_config']))
        aid = old['account_id']
        self.courses(owner, aid)
        for selection in config['selections']:
            self.items(owner, aid, selection['course'])
        # Same validation as a manual start, including deleted/closed lesson rejection.
        return self.start(owner, aid, config)

    def delete_task(self, owner, tid):
        with self.lock:
            if tid in self.tasks:
                raise PlatformError('请先停止并等待任务结束')
            self.store.delete_task(owner, tid)

    def shutdown(self):
        with self.lock:
            self.closing = True
            tasks = [(t['owner'], t['id']) for t in self.tasks.values()]
        for owner, tid in tasks:
            self.stop(owner, tid)


welearn_workbench = WelearnWorkbench(english_manager)
