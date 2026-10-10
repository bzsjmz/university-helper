import asyncio

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .status_reader import StatusUnavailable, format_status, read_status


@register('astrbot_plugin_course_tasks', 'local', '独立学习通任务只读查询，不主动推送', '1.0.0')
class CourseTasksPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.status_file = (config or {}).get('status_file', '/course-task-status/status.json')

    @filter.command('任务状态')
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def task_status(self, event: AstrMessageEvent, page: str = '1'):
        """学习通任务查询：/任务状态 [页码]，仅管理员，不主动通知。"""
        try:
            page_number = int(page)
            if page_number < 1: raise ValueError('page')
        except (ValueError, TypeError):
            yield event.plain_result('用法：/任务状态 [正整数页码]，例如 /任务状态 2')
            return
        try:
            snapshot = await asyncio.to_thread(read_status, self.status_file)
            response = format_status(snapshot, page_number)
        except StatusUnavailable as exc:
            response = str(exc)
        yield event.plain_result(response)
