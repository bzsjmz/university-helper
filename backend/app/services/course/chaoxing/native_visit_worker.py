"""Private pipe worker: native chapter pages only, no answer providers."""
import json
import logging
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

from .native_visit_protocol import (
    NativeVisitError, permitted_resource, validate_course, validate_native_url, validate_point,
)


def install_network_policy(context):
    def route_request(route):
        if permitted_resource(route.request.url, route.request.method):
            route.fallback()
        else:
            route.abort()
    context.route("**/*", route_request)


class NativeChapterBrowser:
    def __init__(self, context, payload, dwell_seconds=30):
        self.context = context
        self.course = validate_course(payload["course"])
        self.entry = validate_native_url(payload["entry_url"], self.course)
        self.points = {validate_point(p["id"]): p for p in payload["points"]}
        if not self.points or len(self.points) > 2000:
            raise NativeVisitError("invalid_chapter")
        self.dwell_seconds = dwell_seconds
        self.page = context.new_page()
        self.study = None
        self.chapter_frame = None
        self.current_point = None
        self.log_receipts = 0
        context.on("response", self._response)

    def _response(self, response):
        try:
            url = urlsplit(response.url)
            values = {k.lower(): v for k, v in parse_qs(url.query).items()}
            if (url.hostname == "fystat-ans.chaoxing.com" and url.path == "/log/setlog"
                    and 200 <= response.status < 300 and self.current_point
                    and values.get("chapterid") == [self.current_point]
                    and values.get("courseid") == [self.course["courseId"]]
                    and values.get("classid") == [self.course["clazzId"]]):
                self.log_receipts += 1
        except (TypeError, ValueError):
            pass

    def bootstrap(self):
        self.page.goto(self.entry, wait_until="domcontentloaded", timeout=40000)
        menu = self.page.get_by_text("章节", exact=True)
        menu.first.wait_for(state="visible", timeout=20000)
        menu.first.click(timeout=15000)
        for _ in range(40):
            self.chapter_frame = next((f for f in self.page.frames if f.locator("a.clicktitle").count()), None)
            if self.chapter_frame is not None:
                break
            self.page.wait_for_timeout(500)
        if self.chapter_frame is None:
            raise NativeVisitError("chapter_directory_unavailable")

    def _manual_check(self):
        for text in ("请输入验证码", "拖动滑块", "请先登录"):
            if self.study.get_by_text(text, exact=True).count() and self.study.get_by_text(text, exact=True).first.is_visible():
                raise NativeVisitError("manual_required")

    def visit(self, point_id):
        point_id = validate_point(point_id)
        if point_id not in self.points:
            raise NativeVisitError("outside_course")
        self.current_point = point_id
        self.log_receipts = 0
        if self.study is None:
            link = self.chapter_frame.locator("#cur" + point_id + " a.clicktitle").first
            try:
                link.click(timeout=20000, no_wait_after=True)
            except Exception:
                # The native link may replace its original page/window.
                if not self.context.pages:
                    raise NativeVisitError("page_unavailable") from None
            self.study = self.context.pages[-1]
            self.study.wait_for_timeout(5000)
            validate_native_url(self.study.url, self.course, study=True)
            self.native_url = urlsplit(self.study.url)
            self.native_params = {k: v[0] for k, v in parse_qs(self.native_url.query, keep_blank_values=True).items()}
        else:
            params = {k: v for k, v in self.native_params.items() if k.lower() != "chapterid"}
            params["chapterId"] = point_id
            target = self.native_url._replace(query=urlencode(params)).geturl()
            validate_native_url(target, self.course, study=True)
            self.study.goto(target, wait_until="domcontentloaded", timeout=40000)
        validate_native_url(self.study.url, self.course, study=True)
        self._manual_check()
        self.study.wait_for_timeout(int(self.dwell_seconds * 500))
        self._manual_check()
        readers = [frame for frame in self.study.frames if "/screen/v2/" in frame.url]
        if readers:
            readers[0].locator("body").press("PageDown", timeout=5000)
        self.study.wait_for_timeout(int(self.dwell_seconds * 500))
        validate_native_url(self.study.url, self.course, study=True)
        self._manual_check()
        if not self.log_receipts:
            raise NativeVisitError("visit_log_unconfirmed")
        # Receipts are diagnostic only; the parent verifies the actual grade.
        return {"state": "visited", "log_observed": True}


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    try:
        line = sys.stdin.buffer.readline(1_000_001)
        if len(line) > 1_000_000:
            raise NativeVisitError("invalid_request")
        payload = json.loads(line)
        validate_course(payload["course"])
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                args=["--disable-features=BackForwardCache"])
            try:
                context = browser.new_context(storage_state={"cookies": payload["cookies"], "origins": []},
                    accept_downloads=False, service_workers="block")
                install_network_policy(context)
                review = NativeChapterBrowser(context, payload)
                review.bootstrap()
                emit({"state": "ready"})
                while True:
                    line = sys.stdin.buffer.readline(10001)
                    if not line:
                        break
                    if len(line) > 10000:
                        raise NativeVisitError("invalid_request")
                    command = json.loads(line)
                    if command.get("action") == "close":
                        break
                    if command.get("action") != "visit":
                        raise NativeVisitError("invalid_request")
                    emit(review.visit(command.get("point_id")))
            finally:
                browser.close()
    except NativeVisitError as exc:
        emit({"state": "error", "code": str(exc)})
    except Exception:
        emit({"state": "error", "code": "browser_unavailable"})


if __name__ == "__main__":
    main()
