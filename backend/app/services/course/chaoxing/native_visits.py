"""Cancellable native chapter review, verified against platform grade scores."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .course_grades import CourseGradeService
from .native_visit_protocol import NativeVisitError, validate_course, validate_native_url
from . import query_control as control
from ..english.process_runtime import pipe_selector, process_options, windows_environment


# This gates only the expensive native browser, not account/chapter tasks.
_BROWSER_SLOT = threading.Semaphore(1)
MAX_REVIEW_SECONDS = 7200
MAX_REVIEW_PAGES = 240
GRADE_REFRESH_WAIT = 300


def worker_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--chaoxing-visit-worker"]
    return [sys.executable, "-B", "-m", "app.services.course.chaoxing.native_visit_worker"]


def temporary_root():
    configured = os.environ.get("CHAOXING_COOKIES_FILE")
    if configured and Path(configured).is_absolute():
        return Path(configured).parent
    from app.config import settings
    if settings.STORAGE_BACKEND == "sqlite" and settings.SQLITE_PATH:
        return Path(settings.SQLITE_PATH).resolve().parent
    return Path(tempfile.gettempdir())


class NativeBrowserClient:
    def __init__(self, client, course, points, checkpoint):
        self.client, self.course, self.points = client, validate_course(course), points
        self.checkpoint = checkpoint
        self.process = None
        self.temporary = None
        self.locked = False
        self.buffer = b""

    def __enter__(self):
        try:
            admission_deadline = time.monotonic() + MAX_REVIEW_SECONDS
            while not _BROWSER_SLOT.acquire(timeout=0.2):
                self.checkpoint()
                if time.monotonic() >= admission_deadline:
                    raise NativeVisitError("browser_capacity_timeout")
            self.locked = True
            self.checkpoint()
            entry = self.client.course_service.native_course_entry(self.course)
            if not entry:
                raise NativeVisitError("native_entry_missing")
            validate_native_url(entry, self.course)
            cookies = []
            for cookie in self.client.session_manager.get_session().cookies:
                # SessionManager flattens authenticated login cookies into a
                # dict, losing their domains. Narrow those account-local
                # cookies to Chaoxing rather than dropping the login session.
                cookie_domain = cookie.domain or ".chaoxing.com"
                domain = cookie_domain.lstrip(".").lower()
                if domain != "chaoxing.com" and not domain.endswith(".chaoxing.com"):
                    continue
                item = {"name": cookie.name, "value": cookie.value, "domain": cookie_domain,
                        "path": cookie.path or "/", "secure": cookie.secure, "httpOnly": False, "sameSite": "Lax"}
                if cookie.expires and cookie.expires > time.time():
                    item["expires"] = cookie.expires
                cookies.append(item)
            root = temporary_root()
            root.mkdir(parents=True, exist_ok=True)
            self.temporary = tempfile.TemporaryDirectory(prefix="chaoxing-visit-browser-", dir=root)
            environment = {k: v for k, v in os.environ.items() if k in {
                "PATH", "LANG", "LC_ALL", "TZ", "PLAYWRIGHT_BROWSERS_PATH", "XDG_RUNTIME_DIR"}}
            if sys.platform == "win32":
                environment.update(windows_environment())
            environment.update(TMPDIR=self.temporary.name, TMP=self.temporary.name, TEMP=self.temporary.name)
            if getattr(sys, "frozen", False):
                environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            self.process = subprocess.Popen(worker_command(), cwd=Path(__file__).resolve().parents[4],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=environment, **process_options())
            self._send({"course": self.course, "entry_url": entry, "points": self.points, "cookies": cookies})
            if self._receive().get("state") != "ready":
                raise NativeVisitError("browser_unavailable")
            return self
        except BaseException:
            self.close()
            raise

    def _send(self, value):
        raw = json.dumps(value, ensure_ascii=True).encode() + b"\n"
        if len(raw) > 1_000_000:
            raise NativeVisitError("invalid_request")
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    def _receive(self):
        deadline = time.monotonic() + 120
        with pipe_selector() as selector:
            selector.register(self.process.stdout, 1)
            while True:
                started = time.monotonic()
                self.checkpoint()
                deadline += time.monotonic() - started  # pause time does not consume the reply budget
                if b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    result = json.loads(line)
                    if not isinstance(result, dict):
                        raise NativeVisitError("invalid_reply")
                    if result.get("state") == "error":
                        codes = {"manual_required", "visit_log_unconfirmed", "outside_course", "native_entry_missing"}
                        raise NativeVisitError(result.get("code") if result.get("code") in codes else "browser_unavailable")
                    return result
                if time.monotonic() >= deadline:
                    raise NativeVisitError("browser_timeout")
                if selector.select(0.2):
                    data = os.read(self.process.stdout.fileno(), 8192)
                    if not data:
                        raise NativeVisitError("browser_unavailable")
                    self.buffer += data
                    if len(self.buffer) > 65536:
                        raise NativeVisitError("invalid_reply")

    def visit(self, point):
        self.checkpoint()
        self._send({"action": "visit", "point_id": point["id"]})
        result = self._receive()
        if result.get("state") != "visited" or result.get("log_observed") is not True:
            raise NativeVisitError("visit_log_unconfirmed")

    def close(self):
        try:
            if self.process is not None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                else:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.process.wait(timeout=10)
                for stream in (self.process.stdin, self.process.stdout):
                    if stream:
                        stream.close()
                self.process = None
        finally:
            try:
                if self.temporary is not None:
                    self.temporary.cleanup()
                    self.temporary = None
            finally:
                if self.locked:
                    self.locked = False
                    _BROWSER_SLOT.release()

    def __exit__(self, *args):
        self.close()


def visits_full(grade):
    return grade.get("state") == "verified" and grade["chapter_visits"]["full_mark_reached"]


def summarize_grade(course, grade, review_state="not_requested", visited=0, message=""):
    result = {"course_name": str(course.get("title") or course.get("name") or "课程")[:160],
              "state": grade.get("state", "unverified"), "review_state": review_state,
              "visited_pages": visited, "message": message or grade.get("reason", "")}
    if grade.get("state") == "verified":
        result.update(score=grade["total_score"], passing_score=grade["selected_passing_score"],
            meets_target=grade["meets_selected_passing_score"], observed_at=grade.get("observed_at"),
            visits_score=grade["parts"]["visitTime"]["weighted_score"],
            visits_weight=grade["parts"]["visitTime"]["weight_percent"],
            full_mark_count=grade["chapter_visits"]["full_mark_count"])
    return result


class ChapterVisitReview:
    def __init__(self, client, *, grade_service=None, browser_factory=NativeBrowserClient,
                 clock=time.monotonic, sleep=time.sleep):
        self.client = client
        self.grades = grade_service or CourseGradeService(client)
        self.browser_factory, self.clock, self.sleep = browser_factory, clock, sleep

    def run(self, course, points, *, enabled, partial, passing_score, checkpoint, progress):
        checkpoint()
        grade = self.grades.read(course, passing_score)
        if grade.get("state") != "verified":
            return summarize_grade(course, grade, "unverified")
        visits = grade["chapter_visits"]
        if not enabled:
            return summarize_grade(course, grade, "not_requested")
        if partial:
            return summarize_grade(course, grade, "partial_scope", message="仅执行所选章节，未扩展为整课次数复习")
        if not visits["applicable"]:
            return summarize_grade(course, grade, "not_graded", message="本课程章节学习次数不计分")
        if visits_full(grade):
            return summarize_grade(course, grade, "full", message="平台章节次数项已满分")
        if visits["full_mark_count"] is None:
            return summarize_grade(course, grade, "unknown_rule", message="未识别本课次数门槛，未自动复习")
        allowed = {str(point["id"]) for point in points}
        try:
            fresh = self.client.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
            finished = [{"id": str(p["id"]), "title": str(p.get("title") or "章节")[:160]}
                        for p in fresh if str(p["id"]) in allowed and p.get("has_finished") is True]
        except Exception:
            return summarize_grade(course, grade, "unavailable", message="章节完成状态未确认，未自动复习")
        if not finished:
            return summarize_grade(course, grade, "unavailable", message="没有可确认已完成的章节用于正常复习")
        started, visited = self.clock(), 0

        def update(state, title=""):
            progress({"state": state, "visited_pages": visited, "current_chapter": title,
                      "visits_score": grade["parts"]["visitTime"]["weighted_score"],
                      "visits_weight": grade["parts"]["visitTime"]["weight_percent"]})

        def remaining():
            item = grade["parts"]["visitTime"]
            return max(1, math.ceil(visits["full_mark_count"] *
                (1 - item["weighted_score"] / item["weight_percent"])) + 1)

        def wait(seconds):
            deadline = self.clock() + seconds
            while self.clock() < deadline:
                checkpoint()
                self.sleep(min(0.2, max(0, deadline - self.clock())))

        planned = min(MAX_REVIEW_PAGES, remaining())
        previous = grade["parts"]["visitTime"]["weighted_score"]
        try:
            update("waiting_browser")
            with self.browser_factory(self.client, course, finished, checkpoint) as browser:
                while visited < MAX_REVIEW_PAGES and self.clock() - started < MAX_REVIEW_SECONDS:
                    checkpoint()
                    point = finished[visited % len(finished)]
                    update("reviewing", point["title"])
                    browser.visit(point)
                    visited += 1
                    if visited % 5 == 0 or visited >= planned:
                        checkpoint()
                        current = self.grades.read(course, passing_score)
                        if current.get("state") != "verified":
                            return summarize_grade(course, current, "unverified", visited)
                        grade = current
                        if visits_full(grade):
                            return summarize_grade(course, grade, "full", visited, "平台章节次数项已满分")
                    if visited >= planned:
                        # Local page visits are not platform counts. Wait for the
                        # platform cache rather than blindly visiting more pages.
                        refresh_deadline = min(self.clock() + GRADE_REFRESH_WAIT, started + MAX_REVIEW_SECONDS)
                        update("waiting_grade")
                        while (self.clock() < refresh_deadline
                               and grade["parts"]["visitTime"]["weighted_score"] <= previous):
                            wait(min(15, max(0, refresh_deadline - self.clock())))
                            current = self.grades.read(course, passing_score)
                            if current.get("state") != "verified":
                                return summarize_grade(course, current, "unverified", visited)
                            grade = current
                            if visits_full(grade):
                                return summarize_grade(course, grade, "full", visited, "平台章节次数项已满分")
                            if grade["parts"]["visitTime"]["weighted_score"] > previous:
                                break
                        if grade["parts"]["visitTime"]["weighted_score"] <= previous:
                            return summarize_grade(course, grade, "unconfirmed", visited, "平台次数分尚未增加，停止本轮自动复习")
                        previous = grade["parts"]["visitTime"]["weighted_score"]
                        planned = min(MAX_REVIEW_PAGES, visited + remaining())
        except (control.QueryCancelled, control.QueryExpired):
            raise
        except Exception:
            return summarize_grade(course, grade, "unavailable", visited,
                "原生章节复习未完成，可能需要人工验证或浏览器不可用；未重做作业或考试")
        return summarize_grade(course, grade, "limit_reached", visited, "本轮章节复习达到时限或次数上限，请核对平台成绩")
