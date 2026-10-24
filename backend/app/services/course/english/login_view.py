"""Detect empty browser captures without persisting private login pictures."""
import struct
import time
import zlib


def blank_png(data):
    """Chromium emits bounded 8-bit RGB/RGBA PNGs. Reject a uniform capture.

    Decode row filters locally: the platform never receives its screenshot back,
    and no additional image package is needed in the isolated worker.
    """
    width = height = channels = None
    compressed = bytearray()
    offset = 8
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return False
    while offset + 12 <= len(data):
        size = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + size]
        if kind == b'IHDR':
            width, height, depth, color, _, _, interlace = struct.unpack('>IIBBBBB', chunk)
            if depth != 8 or color not in (2, 6) or interlace or not (0 < width <= 1280 and 0 < height <= 1000):
                return False
            channels = 3 if color == 2 else 4
        elif kind == b'IDAT':
            compressed.extend(chunk)
        offset += size + 12
    if channels is None:
        return False
    stride = width * channels
    raw = zlib.decompressobj().decompress(bytes(compressed), (stride + 1) * height + 1)
    if len(raw) != (stride + 1) * height:
        return False
    previous = bytearray(stride)
    low, high = [255] * 3, [0] * 3
    for y in range(height):
        start = y * (stride + 1)
        filtering = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        if filtering > 4:
            return False
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = previous[i]
            c = previous[i - channels] if i >= channels else 0
            if filtering == 1:
                predictor = a
            elif filtering == 2:
                predictor = b
            elif filtering == 3:
                predictor = (a + b) // 2
            elif filtering == 4:
                p = a + b - c
                distances = (abs(p - a), abs(p - b), abs(p - c))
                predictor = (a, b, c)[distances.index(min(distances))]
            else:
                predictor = 0
            row[i] = (row[i] + predictor) & 255
            channel = i % channels
            if channel < 3:
                low[channel] = min(low[channel], row[i])
                high[channel] = max(high[channel], row[i])
                if high[channel] - low[channel] > 4:
                    return False
        previous = row
    return True


def login_picture(context, current, platform, method='password'):
    """Prefer the newest rendered official tab; ignore empty redirect popups."""
    from playwright.sync_api import Error
    from .login_browser import allowed_login_url
    deadline = time.monotonic() + 8
    for attempt in range(3):
        candidates = [p for p in context.pages if not p.is_closed() and allowed_login_url(p.url, platform, method)][-4:]
        for page in reversed(candidates):
            if time.monotonic() >= deadline:
                break
            try:
                page.wait_for_load_state('domcontentloaded', timeout=min(1000, max(1, int((deadline - time.monotonic()) * 1000))))
                picture = page.screenshot(type='png', timeout=min(2000, max(1, int((deadline - time.monotonic()) * 1000))))
                if not blank_png(picture):
                    return page, picture
            except Error:
                continue
        if time.monotonic() >= deadline:
            break
        if attempt < 2:
            context.pages[0].wait_for_timeout(500)
    # Keep a safe tab available for an explicit reload, but never show a blank image.
    candidates = [p for p in context.pages if not p.is_closed() and allowed_login_url(p.url, platform, method)]
    return (candidates[-1] if candidates else current), None
