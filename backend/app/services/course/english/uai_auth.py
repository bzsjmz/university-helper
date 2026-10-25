"""Read the current portal's own stored login token, without generating one."""
import json

PORTAL_ORIGIN = 'https://uai.unipus.cn'
PORTAL_STATE = 'PORTAL_STATE_PERSISTENT'


def valid_token(value):
    return isinstance(value, str) and 0 < len(value) <= 16384 and not any(ord(c) < 33 or ord(c) > 126 for c in value)


def portal_token(state):
    if not isinstance(state, dict) or not isinstance(state.get('origins'), list):
        return None
    for origin in state['origins']:
        if not isinstance(origin, dict) or origin.get('origin') != PORTAL_ORIGIN:
            continue
        entries = origin.get('localStorage', [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('name') != PORTAL_STATE:
                continue
            raw = entry.get('value')
            if not isinstance(raw, str) or len(raw) > 128000:
                continue
            try:
                stored = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(stored, dict):
                continue
            info = stored.get('tokenInfo')
            token = info.get('jwt') if isinstance(info, dict) else None
            token = token or stored.get('Authorization')
            if valid_token(token):
                return token
    return None


def browser_token(context):
    token = portal_token(context.storage_state())
    if token:
        return token
    return next((c['value'] for c in context.cookies(['https://uai.unipus.cn/portal/'])
                 if c.get('name') == 'jwt' and valid_token(c.get('value'))), None)
