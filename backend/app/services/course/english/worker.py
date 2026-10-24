"""One disposable process per operation; private credentials arrive via stdin."""
import json
import sys

from .clients import PlatformError, Welearn, unipus, welearn_answer


def ask_parent(question):
    print(json.dumps({'event': 'question', 'question': question}, ensure_ascii=False), flush=True)
    response = sys.stdin.buffer.readline(150_001)
    if not response or len(response) > 150_000:
        raise PlatformError('答题通道已关闭')
    return json.loads(response)


def main():
    try:
        request = json.loads(sys.stdin.buffer.readline(150_001))
        platform, action = request['platform'], request['action']
        if action == 'login':
            from .login_browser import login_browser
            login_browser(request)
            return
        if platform == 'welearn':
            client = Welearn(request['credential'])
            try:
                if action == 'connect':
                    result = {'courses': client.courses()}
                elif action == 'items':
                    _, items = client.items(request['course'])
                    result = {'items': items}
                elif action == 'answer':
                    result = welearn_answer(request['credential'], request['course'], request['item'],
                        request['seconds'], ask_parent, submit=request.get('submit', False))
                else:
                    result = client.study(request['course'], request['item'], request['seconds'])
            finally:
                client.session.close()
        else:
            result = unipus(request['credential'], request['course'],
                item=request.get('item') if action in ('study', 'answer') else None,
                seconds=request.get('seconds', 0), expected_name=request.get('expected_name'),
                solve=ask_parent if action == 'answer' else None, submit=request.get('submit', False))
        print(json.dumps({'ok': True, **result}, ensure_ascii=False), flush=True)
    except PlatformError as exc:
        print(json.dumps({'ok': False, 'message': str(exc)}, ensure_ascii=False))
    except Exception:
        # Do not echo requests, browser errors, URLs, cookies or remote response bodies.
        print(json.dumps({'ok': False, 'message': '平台访问失败；请检查会话、课程页面和浏览器运行环境'}))


if __name__ == '__main__':
    main()
