"""Cooperative deadlines for answering; no watchdog or abandoned worker threads."""
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import math
import threading


class QueryControlError(Exception):
    """A cancelled or expired operation must not submit fabricated answers."""


class QueryCancelled(QueryControlError):
    pass


class QueryExpired(QueryControlError):
    pass


class ProviderUnavailable(Exception):
    """This provider exhausted its budget; a later provider may still work."""


def setting(name, default):
    try:
        value = float(os.environ.get(name, default))
        return value if math.isfinite(value) and value > 0 else default
    except (TypeError, ValueError):
        return default


@dataclass
class JobControl:
    should_stop: object = None
    deadline: float | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)


@dataclass
class QueryControl:
    deadline: float
    provider_deadline: float | None = None
    attempts: dict = field(default_factory=dict)


_JOB = ContextVar("chaoxing_job_control", default=None)
_QUERY = ContextVar("chaoxing_query_control", default=None)


def active_query():
    return _QUERY.get()


def check_control():
    job, query = _JOB.get(), _QUERY.get()
    if job and (job.cancelled.is_set() or (callable(job.should_stop) and job.should_stop())):
        raise QueryCancelled()
    now = time.monotonic()
    if job and job.deadline is not None and now >= job.deadline:
        raise QueryExpired()
    if query:
        if now >= query.deadline:
            raise QueryExpired()
        if query.provider_deadline is not None and now >= query.provider_deadline:
            raise ProviderUnavailable()


def remaining():
    check_control()
    deadlines = []
    job, query = _JOB.get(), _QUERY.get()
    if job and job.deadline is not None:
        deadlines.append(job.deadline)
    if query:
        deadlines.append(query.deadline)
        if query.provider_deadline is not None:
            deadlines.append(query.provider_deadline)
    return max(0.001, min(deadlines) - time.monotonic()) if deadlines else float("inf")


def request_timeout(default=30):
    if not _JOB.get() and not _QUERY.get():
        return default
    limit = min(setting("CHAOXING_REQUEST_TIMEOUT_SECONDS", 30), remaining())
    if isinstance(default, (tuple, list)):
        return tuple(min(float(value), limit) if value is not None else limit for value in default)
    return min(float(default), limit) if default is not None else limit


def cancel_job():
    job = _JOB.get()
    if job:
        job.cancelled.set()


def sleep(seconds):
    if not _JOB.get() and not _QUERY.get():
        time.sleep(seconds)
        return
    end = time.monotonic() + max(0, seconds)
    while time.monotonic() < end:
        time.sleep(max(0, min(0.2, end - time.monotonic(), remaining())))
    check_control()


@contextmanager
def acquired(lock):
    taken = False
    try:
        if not _JOB.get() and not _QUERY.get():
            lock.acquire(); taken = True
        else:
            while not taken:
                taken = lock.acquire(timeout=min(0.2, remaining()))
            check_control()
        yield
    finally:
        if taken:
            lock.release()


def claim_attempt(provider, maximum):
    check_control()
    query = _QUERY.get()
    if query:
        count = query.attempts.get(provider, 0)
        if count >= maximum:
            raise ProviderUnavailable()
        query.attempts[provider] = count + 1


@contextmanager
def job_scope(should_stop, answering=False):
    deadline = time.monotonic() + setting("CHAOXING_ANSWER_JOB_TIMEOUT_SECONDS", 300) if answering else None
    token = _JOB.set(JobControl(should_stop, deadline))
    try:
        check_control()
        yield
        check_control()
    finally:
        _JOB.reset(token)


@contextmanager
def provider_scope(name):
    query = _QUERY.get()
    previous = query.provider_deadline if query else None
    if query:
        # A congested free bank must not consume the entire AI fallback budget.
        seconds = 15 if "GO" in str(name) else 30 if "言溪" in str(name) else None
        if seconds is not None:
            query.provider_deadline = min(query.deadline, time.monotonic() + seconds)
    try:
        yield
    finally:
        if query:
            query.provider_deadline = previous


def bounded_query(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        token = _QUERY.set(QueryControl(time.monotonic() + setting("CHAOXING_QUESTION_TIMEOUT_SECONDS", 120)))
        try:
            check_control()
            with provider_scope(getattr(self, "name", "")):
                result = function(self, *args, **kwargs)
                check_control()
                return result
        except ProviderUnavailable:
            return None
        finally:
            _QUERY.reset(token)
    return wrapped
