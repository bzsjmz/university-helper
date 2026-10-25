"""Wait for actual courseware and let its own exit save native progress."""
import time
from urllib.parse import urlsplit

from .forms import frame_visible


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
                [...document.querySelectorAll('et-blank')].filter(e=>e.getClientRects().length).every(e=>e.querySelector('.blank[contenteditable],textarea'))""")
            except Exception:
                continue
            if loaded:
                ready.append(frame)
        if ready:
            # Angular directives and their answer keys settle after template load.
            page.wait_for_timeout(500)
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
