"""Keep browser OAuth events flowing and resume the original WE Learn entry."""
import selectors
import sys
import time
from urllib.parse import urlsplit

from .process_runtime import pipe_selector


class LoginChannelError(RuntimeError):
    """The private login command pipe could not be monitored or read."""


def read_login_command(page, deadline):
    stream = sys.stdin.buffer
    try:
        stream.fileno()
    except (AttributeError, OSError):
        # In-memory transports used by offline browser fixtures.
        return stream.readline(4097)
    # Windows selectors accept sockets, not the worker's stdin pipe. Use the
    # same platform-aware polling as the parent while pumping browser events.
    try:
        return _read_login_command(page, deadline, stream)
    except (OSError, ValueError):
        raise LoginChannelError() from None


def _read_login_command(page, deadline, stream):
    with pipe_selector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if selector.select(timeout=0):
                return stream.readline(4097)
            # A blocking stdin read starves Playwright's synchronous event loop:
            # routed OAuth redirects can remain paused until another UI command.
            from playwright.sync_api import Error
            pages = [p for p in page.context.pages if not p.is_closed()]
            if not pages:
                return b''
            try:
                pages[-1].wait_for_timeout(100)
            except Error:
                if not any(not p.is_closed() for p in page.context.pages):
                    return b''
    return b''


class WechatHandoff:
    def __init__(self):
        self.seen_qr = False
        self.blank_reads = 0
        self.resumed = False
        self.pending_since = None

    def observe(self, page, picture, qr_status):
        if qr_status is not None:
            self.seen_qr = True
            self.blank_reads = 0
            self.pending_since = None
            return False
        if not self.seen_qr or self.resumed:
            return False
        parsed = urlsplit(page.url)
        # Let the official callback redeem its code before navigating away.
        # Only a stable callback without a QR/form gets one bounded fallback.
        if self.pending_since is None:
            self.pending_since = time.monotonic()
        settled = time.monotonic() - self.pending_since >= 5
        if picture is None:
            self.blank_reads += 1
            return settled and self.blank_reads >= 2
        self.blank_reads = 0
        # Some official callbacks stop at a landing/transfer page rather than
        # redeeming the original application's SSO session. Never submit forms
        # or try to complete account binding/registration here.
        return (settled and parsed.scheme == 'https' and parsed.hostname in ('sso.sflep.com', 'welearn.sflep.com')
                and not any(part in parsed.path.lower() for part in ('bind', 'register'))
                and page.locator('input:visible').count() == 0)

    def resume(self, context, page):
        from .login_browser import LOGIN_URLS
        self.resumed = True  # One bounded attempt; never retry a code-bearing callback.
        if page.is_closed():
            page = context.new_page()
        page.goto(LOGIN_URLS['welearn'], wait_until='domcontentloaded', timeout=20000)
        return page
