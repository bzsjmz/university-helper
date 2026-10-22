"""One disposable process per operation; private credentials arrive via stdin."""
import json
import sys

from .clients import PlatformError, Welearn, unipus


def main():
    try:
        request = json.loads(sys.stdin.buffer.read(150_001))
        platform, action = request['platform'], request['action']
        if platform == 'welearn':
            client = Welearn(request['credential'])
            try:
                if action == 'connect':
                    result = {'courses': client.courses()}
                elif action == 'items':
                    _, items = client.items(request['course'])
                    result = {'items': items}
                else:
                    result = client.study(request['course'], request['item'], request['seconds'])
            finally:
                client.session.close()
        else:
            result = unipus(request['credential'], request['course'],
                item=request.get('item') if action == 'study' else None,
                seconds=request.get('seconds', 0), expected_name=request.get('expected_name'))
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    except PlatformError as exc:
        print(json.dumps({'ok': False, 'message': str(exc)}, ensure_ascii=False))
    except Exception:
        # Do not echo requests, browser errors, URLs, cookies or remote response bodies.
        print(json.dumps({'ok': False, 'message': '平台访问失败；请检查会话、课程页面和浏览器运行环境'}))


if __name__ == '__main__':
    main()
