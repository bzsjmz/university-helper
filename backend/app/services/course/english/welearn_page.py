"""Wait for actual courseware and let its own exit save native progress."""
import time
from urllib.parse import urlsplit

from .forms import frame_visible

MINIMUM_CONTENT_SECONDS = 10.0


def wait_content(page, deadline):
    end = min(deadline, time.monotonic() + 25)
    while time.monotonic() < end:
        ready = []
        for frame in page.frames:
            if not frame_visible(frame):
                continue
            host = urlsplit(frame.url).hostname or ''
            if not (host.endswith('.sflep.com') and '/student/' not in urlsplit(frame.url).path.lower()):
                continue
            try:
                loaded = frame.evaluate("""() => document.readyState !== 'loading' &&
                !!document.querySelector('et-item,et-blank,et-choice,et-select,et-tof,[data-controltype="pagecontrol"],[data-controltype="filling"],[data-controltype="choice"],.question') &&
                (document.body?.innerText || '').trim().length > 20 &&
                [...document.querySelectorAll('et-blank')].filter(e=>e.getClientRects().length &&
                  getComputedStyle(e).visibility !== 'hidden').every(e=>
                  e.querySelector('.blank[contenteditable],textarea') ||
                  (e.closest('[noinput]') && e.querySelector('.blank[tabindex]')))""")
            except Exception:
                continue
            if loaded:
                ready.append(frame)
        if ready:
            # Native reading pages commit after three seconds. Keep every loaded
            # lesson/tab open for at least ten, including unsupported content.
            # Small waits keep browser events running; the parent can still stop
            # this disposable worker and the existing lesson budget still applies.
            settled = time.monotonic() + MINIMUM_CONTENT_SECONDS
            while time.monotonic() < settled:
                remaining = min(settled, deadline) - time.monotonic()
                if remaining <= 0:
                    return []
                page.wait_for_timeout(min(200, remaining * 1000))
            return ready
        page.wait_for_timeout(250)
    return []


def return_to_catalog(page):
    button = page.locator('a[href="javascript:ReturnMain();"]')
    try:
        if button.count() == 1 and button.is_visible():
            button.click(timeout=5000)
            page.wait_for_timeout(1000)
    except Exception:
        # The real exit may replace its document. A separate progress read is
        # authoritative; never repeat an uncertain exit or submission.
        pass
