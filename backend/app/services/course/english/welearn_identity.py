"""Read the authenticated uid from the official course page; never trust a cookie field."""
import re

from .clients import PlatformError


def account_identity(client):
    courses = client.courses()
    if not courses:
        raise PlatformError('该账号暂无课程，请在官方平台加入课程后再保存')
    html = client.request('/student/course_info.aspx', params={'cid': courses[0]['id']}, html=True)
    match = re.search(r'"uid":\s*(\d+)', html)
    if not match:
        raise PlatformError('无法核验账号身份，请重新登录')
    return {'uid': match.group(1)}
