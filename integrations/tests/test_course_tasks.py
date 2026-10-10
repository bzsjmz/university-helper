import ast
import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

PLUGIN = Path(__file__).parents[1] / 'astrbot_plugin_course_tasks'
sys.path.insert(0, str(PLUGIN))
from status_reader import StatusUnavailable, format_status, read_status


def snapshot():
    return {'version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(), 'total': 7,
            'counts': {'running': 2, 'paused': 1}, 'tasks': [
                {'task_id': f'dummy-{n}', 'account_name': f'Account {n}', 'account_label': '138****0000',
                 'status': 'running', 'course': 'Synthetic course', 'chapters_done': 5, 'chapters_total': 10} for n in range(7)]}


class CourseTasksTests(unittest.TestCase):
    def test_fresh_snapshot_read_and_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            path.write_text(json.dumps(snapshot()))
            value = read_status(path)
        first = format_status(value)
        self.assertIn('Account 0', first)
        self.assertNotIn('Account 5', first)
        self.assertIn('章节 5/10', first)
        self.assertIn('/任务状态 2', first)
        self.assertIn('Account 5', format_status(value, 2))
        self.assertIn('页码超出范围', format_status(value, 3))

    def test_stale_invalid_missing_and_oversized_sources_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            for content in ['not-json', 'x' * (512 * 1024 + 1), json.dumps({'version': 1})]:
                path.write_text(content)
                with self.assertRaises(StatusUnavailable): read_status(path)
            value = snapshot()
            value['generated_at'] = (datetime.now(timezone.utc) - timedelta(seconds=35)).isoformat()
            path.write_text(json.dumps(value))
            with self.assertRaises(StatusUnavailable): read_status(path)
            path.unlink()
            with self.assertRaises(StatusUnavailable): read_status(path)

    def test_empty_snapshot_has_explicit_no_tasks_message(self):
        value = snapshot()
        value.update(tasks=[], total=0)
        self.assertIn('暂无任务记录', format_status(value))

    def test_command_is_admin_only_and_has_no_push_or_startup_handler(self):
        tree = ast.parse((PLUGIN / 'main.py').read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        handlers = [node for node in cls.body if isinstance(node, ast.AsyncFunctionDef)]
        self.assertEqual([node.name for node in handlers], ['task_status'])
        decorators = [ast.unparse(node) for node in handlers[0].decorator_list]
        self.assertIn("filter.command('任务状态')", decorators)
        self.assertIn('filter.permission_type(filter.PermissionType.ADMIN)', decorators)
        self.assertNotIn('send_message', (PLUGIN / 'main.py').read_text())

    def test_handler_queries_only_after_a_command_and_handles_unavailable_source(self):
        tree = ast.parse((PLUGIN / 'main.py').read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        handler = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef))
        handler.decorator_list = []
        queries = []
        def reader(path):
            queries.append(path)
            raise StatusUnavailable('unavailable')
        namespace = {'asyncio': asyncio, 'AstrMessageEvent': object, 'read_status': reader,
                     'StatusUnavailable': StatusUnavailable, 'format_status': format_status}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), 'handler', 'exec'), namespace)
        event = SimpleNamespace(plain_result=lambda message: message)
        async def replies(page):
            return [item async for item in namespace['task_status'](SimpleNamespace(status_file='synthetic'), event, page)]
        self.assertIn('用法', asyncio.run(replies('bad'))[0])
        self.assertEqual(queries, [])
        self.assertEqual(asyncio.run(replies('1')), ['unavailable'])
        self.assertEqual(queries, ['synthetic'])

    def test_installed_astrbot_permission_filter_rejects_non_admin(self):
        try:
            from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
        except ImportError:
            self.skipTest('Run in isolated AstrBot image for real permission filter')
        permission = PermissionTypeFilter(PermissionType.ADMIN)
        self.assertFalse(permission.filter(SimpleNamespace(is_admin=lambda: False), None))
        self.assertTrue(permission.filter(SimpleNamespace(is_admin=lambda: True), None))
