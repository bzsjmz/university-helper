"""Scope checks shared by the native review worker and its parent."""
import re
from urllib.parse import parse_qs, urlsplit


class NativeVisitError(ValueError):
    pass


def validate_course(course):
    try:
        result = {k: str(course[k]) for k in ("courseId", "clazzId", "cpi")}
        if not all(re.fullmatch(r"[0-9]{1,24}", value) for value in result.values()):
            raise ValueError()
        return result
    except (KeyError, TypeError, ValueError):
        raise NativeVisitError("invalid_course") from None


def validate_point(point_id):
    if not isinstance(point_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", point_id):
        raise NativeVisitError("invalid_chapter")
    return point_id


def platform_url(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return bool("\\" not in url and parsed.scheme == "https" and parsed.port in (None, 443)
                    and not parsed.username and not parsed.password
                    and (host == "chaoxing.com" or host.endswith(".chaoxing.com")))
    except (TypeError, ValueError):
        return False


def validate_native_url(url, course, *, study=False):
    if not platform_url(url):
        raise NativeVisitError("outside_course")
    parsed = urlsplit(url)
    allowed = {"/mycourse/studentstudy", "/mooc-ans/mycourse/studentstudy"} if study else {
        "/visit/stucoursemiddle", "/mooc-ans/visit/stucoursemiddle", "/mooc2-ans/mycourse/stu",
    }
    values = parse_qs(parsed.query)
    lowered = {key.lower(): value for key, value in values.items()}
    expected = {"courseid": course["courseId"], "clazzid": course["clazzId"], "cpi": course["cpi"]}
    if parsed.path not in allowed or any(lowered.get(k) != [v] for k, v in expected.items()):
        raise NativeVisitError("outside_course")
    # Reject query aliases that collide after case normalization.
    if len(lowered) != len(values):
        raise NativeVisitError("outside_course")
    return url


def permitted_resource(url, method="GET"):
    if not platform_url(url):
        return False
    path = urlsplit(url).path.lower()
    if re.search(r"/(?:exam-ans|exam)(?:/|$)", path) or "/work/phone/dohomework" in path:
        return False
    # This worker never submits answers, starts exams, or performs a login.
    return method in {"GET", "HEAD"}
