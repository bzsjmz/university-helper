"""Read platform grade weights and scores without executing learning reports."""
from decimal import Decimal
from html.parser import HTMLParser
import math
import re


FIELDS = {
    "video": "章节任务点", "work": "作业", "test": "考试", "attend": "签到",
    "active": "课程积分", "bbs": "讨论", "visitTime": "章节学习次数", "readTime": "阅读",
}


class GradeUnavailable(ValueError):
    pass


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def field_pair(html, field):
    pattern = r'''["']''' + re.escape(field) + r'''["']\s*:\s*["']?([0-9]+(?:\.[0-9]+)?)(%)?(?=[\s,"'}])'''
    matches = re.findall(pattern, html)
    values = [Decimal(value) for value, _ in matches]
    if len(values) not in (2, 4):
        raise GradeUnavailable("平台分数字段结构无法确认")
    if len(set(values[::2])) != 1 or len(set(values[1::2])) != 1:
        raise GradeUnavailable("平台重复分数对象不一致")
    if any(percent for _, percent in matches[1::2]):
        raise GradeUnavailable("平台实得分单位无法确认")
    weight, actual = values[:2]
    if not 0 <= actual <= weight <= 100:
        raise GradeUnavailable("平台分数超出对应权重")
    return weight, actual


def unique_threshold(text, pattern):
    matches = {Decimal(v) for v in re.findall(pattern, text)}
    if len(matches) == 1:
        value = matches.pop()
        if 0 < value <= 10_000_000:
            return float(value)
    return None


def parse_course_grade(html, passing_score=60):
    if not math.isfinite(passing_score) or not 0 <= passing_score <= 100:
        raise GradeUnavailable("及格线必须在0到100之间")
    if len(html) > 3_000_000:
        raise GradeUnavailable("平台成绩页面超出解析范围")
    pairs = {field: field_pair(html, field) for field in FIELDS}
    # Known optional live score; unfamiliar positive weights fail the sum check.
    if re.search(r'''["']liveTime["']\s*:''', html):
        pairs["liveTime"] = field_pair(html, "liveTime")
    total_weight, total = field_pair(html, "score")
    if total_weight != 100 or sum(v[0] for v in pairs.values()) != 100:
        raise GradeUnavailable(f"平台考核权重不完整（分项权重合计{sum(v[0] for v in pairs.values())}、总权重{total_weight}），须人工核对")
    if abs(sum(v[1] for v in pairs.values()) - total) > Decimal("0.05"):
        raise GradeUnavailable("分项与平台总分不一致")
    parser = VisibleText()
    parser.feed(html)
    text = " ".join(" ".join(parser.parts).split())
    full_count = unique_threshold(text, r"章节学习次数达\s*(\d+(?:\.\d+)?)\s*次为满分")
    full_minutes = unique_threshold(
        text, r"阅读[：:]\s*\d+(?:\.\d+)?\s*%.{0,400}?总时长达到\s*(\d+(?:\.\d+)?)\s*分钟为满分",
    )
    warnings = []
    visit_weight, visit_score = pairs["visitTime"]
    if visit_weight > 0:
        if full_count is None:
            warnings.append("章节次数门槛未确认，不得套用其他课程的120次")
        if visit_score < visit_weight:
            warnings.append("章节次数项未满；任务点完成不代表此项达标")
    if pairs["readTime"][0] > 0 and full_minutes is None:
        warnings.append("阅读满分时长未确认")
    return {
        "state": "verified", "selected_passing_score": passing_score,
        "total_score": float(total), "meets_selected_passing_score": total >= Decimal(str(passing_score)),
        "parts": {field: {"label": FIELDS.get(field, "直播"), "weight_percent": float(weight),
                           "weighted_score": float(actual)} for field, (weight, actual) in pairs.items()},
        "chapter_visits": {"applicable": visit_weight > 0, "full_mark_count": full_count,
                           "full_mark_reached": visit_weight > 0 and visit_score == visit_weight,
                           "raw_visit_count_verified": False},
        "reading": {"full_mark_minutes": full_minutes, "credited_material_verified": False},
        "warnings": warnings,
    }



def unverified_grade(reason="平台成绩暂未核验"):
    return {"state":"unverified","reason":reason}


class CourseGradeService:
    def __init__(self, client):
        self.client = client

    def read(self, course, passing_score=60):
        from datetime import datetime, timezone
        try:
            from .native_visit_protocol import validate_course
            identity = validate_course(course)
            session = self.client.session_manager.get_session()
            response = session.get("https://stat2-ans.chaoxing.com/stat2/overall-score/stu-score",
                params={"courseid":identity["courseId"],"clazzid":identity["clazzId"],"cpi":identity["cpi"],"ut":"s"},
                timeout=20, allow_redirects=False)
            if response.status_code != 200:
                return unverified_grade("平台成绩页面未就绪或登录已失效")
            result = parse_course_grade(response.text, passing_score)
            result["observed_at"] = datetime.now(timezone.utc).isoformat()
            return result
        except GradeUnavailable as exc:
            return unverified_grade(str(exc))
        except Exception:
            return unverified_grade()
