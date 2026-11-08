"""Owner-scoped encrypted sessions and serial course queues.

Sessions survive restart; execution does not. Interrupted queues require an
explicit new start. Only existing video/answer behavior is used.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import threading
import time
from uuid import uuid4

import requests

from app.core.credential_crypto import decrypt_str, encrypt_str
from app.services.course.task_store import task_store
from .adapter import ZhihuishuAdapter

ACCOUNT_KIND = "zhihuishu_account"
QUEUE_KIND = "zhihuishu_queue"
BUSY = frozenset({"queued", "running", "paused", "cancelling"})


class ManagedError(RuntimeError):
    def __init__(self, detail, status_code=400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def now():
    return datetime.now(UTC).isoformat()


def public_account(row):
    return {key: row.get(key) for key in ("account_id", "label", "status", "created_at", "updated_at")}


def session_data(adapter):
    cookies = {}
    # Start with original domain-bearing CAS cookies; later learning cookies win.
    for jar in (adapter.auth.session.cookies, adapter.learning.session.cookies):
        for cookie in jar:
            domain = cookie.domain.lstrip(".").lower()
            if domain and domain != "zhihuishu.com" and not domain.endswith(".zhihuishu.com"):
                continue
            item = {"name": cookie.name, "value": cookie.value,
                    "domain": cookie.domain or ".zhihuishu.com", "path": cookie.path or "/",
                    "secure": cookie.secure, "expires": cookie.expires}
            cookies[(item["name"], item["domain"], item["path"])] = item
    return {"cookies": list(cookies.values()), "uuid": str(adapter.auth.uuid or "")}


def restore_adapter(data):
    adapter = ZhihuishuAdapter()
    jar = requests.cookies.RequestsCookieJar()
    for item in data["cookies"]:
        domain = str(item.get("domain", "")).lstrip(".").lower()
        if domain != "zhihuishu.com" and not domain.endswith(".zhihuishu.com"):
            raise ManagedError("保存的登录状态无效，请重新登录。", 401)
        jar.set(item["name"], item["value"], domain=item["domain"], path=item.get("path") or "/",
                secure=bool(item.get("secure")), expires=item.get("expires"))
    adapter.auth.cookies = jar
    if str(adapter.auth.uuid) != data["uuid"]:
        raise ManagedError("保存的登录身份不一致，请重新登录。", 401)
    adapter._init_services(adapter.auth.cookies)
    for target in (adapter.auth.session, adapter.learning.session, adapter.answer.session):
        target.cookies.clear()
        target.cookies.update(jar)
    return adapter


class ZhihuishuManager:
    def __init__(self, store=None, adapter_factory=ZhihuishuAdapter):
        self.store = store or task_store
        self.adapter_factory = adapter_factory
        self.lock = threading.RLock()
        self.adapters = {}
        self.queues = {}
        self.workers = {}
        self.account_locks = {}
        self.qr = {}
        self.qr_workers = {}
        self.closing = threading.Event()

    def _save(self, kind, row):
        row["updated_at"] = now()
        if not self.store.upsert_task(kind, deepcopy(row)):
            raise ManagedError("无法保存智慧树状态，请稍后重试。", 503)

    def _row(self, owner, account_id):
        row = self.store.get_task(ACCOUNT_KIND, account_id, str(owner))
        if row is None:
            raise ManagedError("账号不存在。", 404)
        return row

    def accounts(self, owner):
        return [public_account(row) for row in self.store.list_tasks(ACCOUNT_KIND, str(owner), limit=10000)]

    def _busy(self, owner, account_id):
        row = self._row(owner, account_id)
        gate = self.account_locks.get(row["account_key"])
        if gate is not None and gate.locked():
            return True
        return any(q["user_id"] == str(owner) and q["account_id"] == account_id and
                   (q["status"] in BUSY or bool(self.workers.get(q["task_id"]) and
                    self.workers[q["task_id"]].is_alive()))
                   for q in self.queues.values())

    def save_login(self, owner, adapter, label="", account_id=None):
        identity = str(adapter.auth.uuid or "")
        if not identity:
            raise ManagedError("未能确认智慧树账号身份。", 401)
        key = hashlib.sha256(identity.encode()).hexdigest()
        data = session_data(adapter)
        # Clone and validate the cookie payload before persisting anything.
        restored = restore_adapter(data)
        cipher = encrypt_str(json.dumps(data, ensure_ascii=False))
        if not cipher.startswith("fernet:"):
            raise ManagedError("登录状态加密未配置，无法保存账号。", 503)
        with self.lock:
            rows = self.store.list_tasks(ACCOUNT_KIND, str(owner), limit=10000)
            matches = [r for r in rows if r.get("account_key") == key]
            row = self._row(owner, account_id) if account_id else (matches[0] if matches else None)
            if row and row["account_key"] != key:
                raise ManagedError("请登录所选账号，或选择新增账号。", 409)
            if row and self._busy(owner, row["account_id"]):
                raise ManagedError("账号仍有活动任务，请先停止任务再更新登录。", 409)
            if row is None:
                aid = uuid4().hex
                row = {"task_id": aid, "account_id": aid, "user_id": str(owner),
                       "account_key": key, "created_at": now()}
            row.update(label=label.strip() or row.get("label") or f"智慧树账号 {len(rows) + 1}",
                       status="saved", session_ciphertext=cipher)
            self._save(ACCOUNT_KIND, row)
            self.adapters[(str(owner), row["account_id"])] = restored
            return public_account(row)

    def password_login(self, owner, username, password, label="", account_id=None):
        adapter = self.adapter_factory()
        try:
            adapter.login_with_password(username, password)
        except Exception:
            raise ManagedError("智慧树登录失败，请核对账号或改用扫码登录。", 401) from None
        return self.save_login(owner, adapter, label, account_id)

    def adapter(self, owner, account_id):
        with self.lock:
            row = self._row(owner, account_id)
            key = (str(owner), account_id)
            if key not in self.adapters:
                try:
                    value = row.get("session_ciphertext", "")
                    if not value.startswith("fernet:"):
                        raise ValueError("missing encrypted session")
                    self.adapters[key] = restore_adapter(json.loads(decrypt_str(value)))
                except Exception:
                    raise ManagedError("保存的登录状态无法读取，请重新登录。", 401) from None
            return self.adapters[key]

    def courses(self, owner, account_id):
        adapter = self.adapter(owner, account_id)
        try:
            courses = adapter.learning.get_course_list(require_valid_response=True)
        except Exception:
            with self.lock:
                row = self._row(owner, account_id)
                row["status"] = "needs_login"
                self._save(ACCOUNT_KIND, row)
            raise ManagedError("登录已失效或平台返回异常，请更新登录后再试。", 401) from None
        with self.lock:
            row = self._row(owner, account_id)
            cipher = encrypt_str(json.dumps(session_data(adapter), ensure_ascii=False))
            if not cipher.startswith("fernet:"):
                raise ManagedError("登录状态加密未配置。", 503)
            row.update(status="saved", session_ciphertext=cipher)
            self._save(ACCOUNT_KIND, row)
        # The public catalogue does not expose signed course secrets.
        return [{"course_id": str(c.get("courseId") or c.get("id")),
                 "course_name": c.get("courseName") or c.get("name") or "未命名课程",
                 "progress": c.get("progress")} for c in courses]

    def videos(self, owner, account_id, course_id):
        if not any(c["course_id"] == course_id for c in self.courses(owner, account_id)):
            raise ManagedError("课程不属于当前账号。", 404)
        return [{k: v.get(k) for k in ("id", "title", "duration", "chapter_id", "chapter_title", "status")}
                for v in self.adapter(owner, account_id).get_videos(course_id)]

    def rename(self, owner, account_id, label):
        with self.lock:
            row = self._row(owner, account_id)
            row["label"] = label.strip()
            self._save(ACCOUNT_KIND, row)
            return public_account(row)

    def delete(self, owner, account_id):
        with self.lock:
            self._row(owner, account_id)
            if self._busy(owner, account_id):
                raise ManagedError("账号仍有活动任务，停止完成后才能删除。", 409)
            self.store.delete_task(ACCOUNT_KIND, account_id, str(owner))
            self.adapters.pop((str(owner), account_id), None)

    def recover(self):
        # No execution is resumed on boot; the user must explicitly requeue.
        with self.lock:
            self.closing.clear()
            for row in self.store.list_tasks(QUEUE_KIND, limit=10000):
                if row["status"] in BUSY:
                    row.update(status="interrupted", message="服务已重启，请重新排队未完成课程。")
                    for item in row["courses"]:
                        if item["status"] in {"running", "starting"}:
                            item["status"] = "interrupted"
                    self._save(QUEUE_KIND, row)

    def list_queues(self, owner, account_id=None):
        rows = self.store.list_tasks(QUEUE_KIND, str(owner), limit=10000)
        return [row for row in rows if account_id is None or row["account_id"] == account_id]

    def _queue(self, owner, queue_id):
        row = self.queues.get(queue_id) or self.store.get_task(QUEUE_KIND, queue_id, str(owner))
        if row is None or row["user_id"] != str(owner):
            raise ManagedError("队列不存在。", 404)
        return row

    def start_queue(self, owner, account_id, selections, speed=1.0, task_type="course", auto_answer=True):
        catalogue = {c["course_id"]: c for c in self.courses(owner, account_id)}
        ids = [i["course_id"] for i in selections]
        if not ids or len(set(ids)) != len(ids) or any(i not in catalogue for i in ids):
            raise ManagedError("请选择当前账号的课程，不能重复选择。")
        if task_type not in {"course", "ai-course"} or not 0.5 <= speed <= 2:
            raise ManagedError("任务模式或倍速无效。")
        with self.lock:
            if self.closing.is_set():
                raise ManagedError("服务正在退出，请稍后重试。", 503)
            account = self._row(owner, account_id)
            gate = self.account_locks.setdefault(account["account_key"], threading.Lock())
            if not gate.acquire(blocking=False):
                raise ManagedError("同一智慧树账号已有活动队列。", 409)
            qid = uuid4().hex
            row = {"task_id": qid, "user_id": str(owner), "account_id": account_id,
                   "account_label": account["label"], "status": "queued", "created_at": now(),
                   "speed": speed, "task_type": task_type, "auto_answer": auto_answer,
                   "message": "等待执行", "courses": [dict(course_id=i["course_id"],
                       course_name=catalogue[i["course_id"]]["course_name"],
                       end_chapter_id=i.get("end_chapter_id"), status="waiting") for i in selections]}
            self.queues[qid] = row
            try:
                self._save(QUEUE_KIND, row)
                worker = threading.Thread(target=self._run, args=(row, gate), daemon=True)
                self.workers[qid] = worker
                worker.start()
            except Exception:
                row.update(status="failed", message="无法启动队列。")
                gate.release()
                self._save(QUEUE_KIND, row)
                raise ManagedError("无法启动队列。", 503) from None
            return deepcopy(row)

    def _run(self, row, gate):
        adapter = None
        task_id = None
        try:
            adapter = self.adapter(row["user_id"], row["account_id"])
            for item in row["courses"]:
                while True:
                    with self.lock:
                        if row["status"] == "cancelling" or self.closing.is_set():
                            row.update(status="interrupted" if self.closing.is_set() else "cancelled",
                                       message="服务退出，队列已中断。" if self.closing.is_set() else "队列已停止。")
                            return
                        paused = row["status"] == "paused"
                    if not paused:
                        break
                    self.closing.wait(0.3)
                # Refresh login and scope before each course, including queued waits.
                catalogue = self.courses(row["user_id"], row["account_id"])
                if not any(c["course_id"] == item["course_id"] for c in catalogue):
                    raise ManagedError("排队课程已不可访问，队列停止。")
                with self.lock:
                    if row["status"] == "cancelling" or self.closing.is_set():
                        row.update(status="interrupted" if self.closing.is_set() else "cancelled",
                                   message="服务退出，队列已中断。" if self.closing.is_set() else "队列已停止。")
                        return
                    if row["status"] == "paused":
                        # A pause arriving during network validation must also hold the next course.
                        while row["status"] == "paused" and not self.closing.is_set():
                            self.lock.release()
                            self.closing.wait(0.3)
                            self.lock.acquire()
                        if row["status"] == "cancelling" or self.closing.is_set():
                            row.update(status="interrupted" if self.closing.is_set() else "cancelled",
                                       message="服务退出，队列已中断。" if self.closing.is_set() else "队列已停止。")
                            return
                    item["status"] = "starting"
                    row.update(status="running", message="正在学习：" + item["course_name"])
                    self._save(QUEUE_KIND, row)
                    # The queue lock serializes controls with actual task admission.
                    if row["task_type"] == "ai-course":
                        result = adapter.start_ai_course_task(item["course_id"], speed=row["speed"],
                            end_chapter_id=item["end_chapter_id"])
                    else:
                        result = adapter.start_course_task(item["course_id"], speed=row["speed"],
                            auto_answer=row["auto_answer"], end_chapter_id=item["end_chapter_id"])
                    task_id = result["task_id"]
                    item.update(status="running", task_id=task_id)
                    self._save(QUEUE_KIND, row)
                while True:
                    if self.closing.is_set():
                        adapter.cancel_task_by_id(task_id)
                    progress = adapter.get_task(task_id)
                    if progress is None:
                        raise ManagedError("课程任务状态丢失，队列停止。")
                    with self.lock:
                        public_progress = {k: progress.get(k) for k in
                            ("total", "completed", "failed", "percentage", "current_video", "status")}
                        if item.get("progress") != public_progress:
                            item["progress"] = public_progress
                            self._save(QUEUE_KIND, row)
                    terminal = progress["status"] in {"completed", "failed", "error", "cancelled"}
                    if terminal and not adapter.task_worker_alive(task_id):
                        break
                    self.closing.wait(1)
                with self.lock:
                    if row["status"] == "cancelling" or progress["status"] == "cancelled" or self.closing.is_set():
                        item["status"] = "interrupted" if self.closing.is_set() else "cancelled"
                        row.update(status=item["status"], message="服务退出，队列已中断。" if self.closing.is_set()
                                   else "队列已停止，后续课程未启动。")
                        return
                    if progress["status"] != "completed" or progress.get("failed", 0):
                        item["status"] = "failed"
                        raise ManagedError("课程存在失败项目，已停止后续课程。")
                    if not progress.get("total"):
                        item["status"] = "failed"
                        raise ManagedError("未取得有效视频列表，已停止后续课程。")
                    item["status"] = "completed"
                    self._save(QUEUE_KIND, row)
                    task_id = None
            row.update(status="completed", message="所选课程视频任务处理完成，请核对平台进度和成绩。")
        except Exception as exc:
            with self.lock:
                row.update(status="failed", message=exc.detail if isinstance(exc, ManagedError)
                           else "平台操作异常，队列已停止；请检查登录与课程状态。")
        finally:
            # Keep the real-account lock until the adapter worker has exited.
            if adapter is not None and task_id is not None:
                adapter.cancel_task_by_id(task_id)
                while adapter.task_worker_alive(task_id):
                    time.sleep(0.3)
            with self.lock:
                try:
                    self._save(QUEUE_KIND, row)
                finally:
                    gate.release()

    def control(self, owner, queue_id, action):
        with self.lock:
            row = self._queue(owner, queue_id)
            if row["status"] not in BUSY:
                raise ManagedError("队列已结束，不能执行该操作。", 409)
            if action == "pause" and row["status"] not in {"running", "queued"}:
                raise ManagedError("当前队列不能暂停。", 409)
            if action == "resume" and row["status"] != "paused":
                raise ManagedError("当前队列未暂停。", 409)
            adapter = self.adapter(owner, row["account_id"])
            current = next((c for c in row["courses"] if c["status"] == "running"), None)
            if current:
                if action == "cancel":
                    adapter.cancel_task_by_id(current["task_id"])
                elif action == "pause":
                    adapter.pause_task()
                else:
                    adapter.resume_task()
            row["status"] = {"pause": "paused", "resume": "running", "cancel": "cancelling"}[action]
            row["message"] = {"pause": "队列已暂停。", "resume": "队列已继续。", "cancel": "正在停止队列。"}[action]
            self._save(QUEUE_KIND, row)
            return deepcopy(row)

    def qr_login(self, owner, label="", account_id=None):
        if account_id:
            self._row(owner, account_id)
        ready = threading.Event()
        cancel = threading.Event()
        sid = uuid4().hex
        with self.lock:
            self.cleanup()
            for old in self.qr.values():
                if old["owner"] == str(owner):
                    old["cancel"].set()
            self.qr = {k: v for k, v in self.qr.items() if v["owner"] != str(owner)}
            state = {"owner": str(owner), "status": "pending", "created": time.time(), "cancel": cancel}
            self.qr[sid] = state
        def callback(raw):
            import base64
            with self.lock:
                state["qr_code"] = base64.b64encode(raw).decode()
                ready.set()
        def login():
            try:
                adapter = self.adapter_factory()
                adapter.login_with_qr(callback, cancel_event=cancel)
                with self.lock:
                    if cancel.is_set():
                        return
                    state["account"] = self.save_login(owner, adapter, label, account_id)
                    state["status"] = "success"
            except Exception as exc:
                with self.lock:
                    state.update(status="failed", message=exc.detail if isinstance(exc, ManagedError)
                                 else "扫码登录失败，请重新获取二维码。")
                ready.set()
        try:
            worker = threading.Thread(target=login, daemon=True)
            with self.lock:
                self.qr_workers[sid] = worker
            worker.start()
        except Exception:
            cancel.set()
            with self.lock:
                self.qr.pop(sid, None)
            raise ManagedError("无法启动扫码登录，请稍后重试。", 503) from None
        if not ready.wait(12):
            cancel.set()
            raise ManagedError("获取二维码超时，请重试。", 504)
        return self.qr_status(owner, sid)

    def qr_status(self, owner, sid):
        with self.lock:
            state = self.qr.get(sid)
            if state is None or state["owner"] != str(owner):
                raise ManagedError("二维码会话不存在。", 404)
            if time.time() - state["created"] >= 600:
                state["cancel"].set()
                self.qr.pop(sid, None)
                raise ManagedError("二维码会话已过期。", 410)
            return {"session_id": sid, **{k: state[k] for k in ("status", "message", "qr_code", "account") if k in state}}

    def cleanup(self):
        with self.lock:
            for sid, state in list(self.qr.items()):
                if time.time() - state["created"] >= 600:
                    state["cancel"].set()
                    self.qr.pop(sid, None)
            self.qr_workers = {sid: worker for sid, worker in self.qr_workers.items() if worker.is_alive()}

    def shutdown(self):
        self.closing.set()
        with self.lock:
            for state in self.qr.values():
                state["cancel"].set()
        deadline = time.monotonic() + 20
        for worker in [*self.workers.values(), *self.qr_workers.values()]:
            if worker.is_alive():
                worker.join(timeout=max(0, deadline - time.monotonic()))


zhihuishu_manager = ZhihuishuManager()
