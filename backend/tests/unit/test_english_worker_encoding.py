"""Real worker IPC under Windows code pages; no browser, account or network."""
import json
import os
import subprocess
import sys

import pytest

from app.services.course.english.clients import PlatformError
from app.services.course.english.manager import EnglishManager
from app.services.course.english.process_runtime import process_options


WORKER = r'''
import runpy, sys, types
client_module = types.ModuleType('app.services.course.english.clients')
class PlatformError(Exception):
    pass
class Welearn:
    def __init__(self, credential):
        self.session = types.SimpleNamespace(close=lambda: None)
        self.expired = credential == 'expired-fixture'
    def courses(self):
        if self.expired:
            raise PlatformError('登录已过期，请更新登录')
        return [{'id': 'book1', 'name': '大学英语读写教程'}]
    def items(self, course):
        return None, [{'id': 'lesson1', 'name': '第一单元 阅读'}]
    def study(self, *args):
        return {'message': '课节读取完成'}
    def export_credential(self):
        return 'sid=encoding-fixture'
def answer(credential, course, item, seconds, solve, **kwargs):
    reply = solve({'title': '请选择正确答案', 'options': ['正确', '错误']})
    return {'message': '处理完成', 'answer': reply['answer']}
client_module.PlatformError = PlatformError
client_module.Welearn = Welearn
client_module.unipus = lambda *args, **kwargs: {}
client_module.welearn_answer = answer
sys.modules[client_module.__name__] = client_module
runpy.run_module('app.services.course.english.worker', run_name='__main__')
'''


def spawn_worker(encoding):
    environment = dict(os.environ, PYTHONIOENCODING=encoding)
    return subprocess.Popen([sys.executable, '-u', '-c', WORKER],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=environment, **process_options())


@pytest.mark.parametrize('encoding', ['cp936', 'cp1252', 'utf-8'])
@pytest.mark.parametrize('action,field,text', [
    ('connect', 'courses', '大学英语读写教程'),
    ('items', 'items', '第一单元 阅读'),
    ('study', 'message', '课节读取完成'),
])
def test_parent_reads_chinese_worker_results_under_system_code_page(encoding, action, field, text):
    process = spawn_worker(encoding)
    try:
        result = EnglishManager.__new__(EnglishManager)._communicate(process, {
            'platform': 'welearn', 'action': action, 'credential': 'cookie-fixture',
            'course': 'book1', 'item': 'lesson1', 'seconds': 1,
        }, None)
        value = result[field][0]['name'] if isinstance(result[field], list) else result[field]
        assert value == text
        if action in ('connect', 'items'):
            assert result['_credential'] == 'sid=encoding-fixture'
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('encoding', ['cp936', 'cp1252'])
def test_platform_error_keeps_safe_chinese_message(encoding):
    process = spawn_worker(encoding)
    try:
        with pytest.raises(PlatformError, match='登录已过期，请更新登录'):
            EnglishManager.__new__(EnglishManager)._communicate(process, {
                'platform': 'welearn', 'action': 'connect', 'credential': 'expired-fixture',
            }, None)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('encoding', ['cp936', 'cp1252'])
def test_question_channel_and_final_reply_keep_unicode(encoding):
    from app.services.course.english.process_runtime import pipe_selector
    import selectors
    process = spawn_worker(encoding)
    try:
        request = {'platform': 'welearn', 'action': 'answer', 'credential': 'cookie-fixture',
                   'course': 'book1', 'item': 'lesson1', 'seconds': 1}
        process.stdin.write(json.dumps(request).encode() + b'\n')
        process.stdin.flush()
        with pipe_selector() as poller:
            poller.register(process.stdout, selectors.EVENT_READ)
            assert poller.select(timeout=10), 'Worker did not ask the parent'
        question = json.loads(process.stdout.readline())
        assert question['question']['title'] == '请选择正确答案'
        process.stdin.write(json.dumps({'answer': '正确'}).encode() + b'\n')
        process.stdin.flush()
        with pipe_selector() as poller:
            poller.register(process.stdout, selectors.EVENT_READ)
            assert poller.select(timeout=10), 'Worker did not finish'
        result = json.loads(process.stdout.readline())
        assert result['ok'] and result['answer'] == '正确' and result['message'] == '处理完成'
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
