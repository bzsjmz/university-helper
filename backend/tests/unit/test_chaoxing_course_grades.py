"""Teacher-specific grade parsing, without live platform requests."""
import json
import unittest

from app.services.course.chaoxing import course_grades as audit

def fixture(weights=None, actual=None, count=120, minutes=120):
    weights = dict(weights or zip(audit.FIELDS, (10, 25, 25, 5, 5, 5, 20, 5)))
    actual = dict(actual or zip(audit.FIELDS, (10, 0, 21.5, 5, 4.25, 1.5, 18.33, 0.01)))
    weights["score"] = 100
    actual["score"] = round(sum(actual.values()), 2)
    markup = "<div>章节学习次数达 %s 次为满分 阅读：5%% 按专题阅读计分，总时长达到 %s 分钟为满分</div>" % (count, minutes)
    pair = "<script>var weights='%s';var actual='%s';</script>" % (json.dumps(weights), json.dumps(actual))
    return markup + pair + pair


class GradeAuditFixtures(unittest.TestCase):
    def test_passed_but_visits_not_full(self):
        report = audit.parse_course_grade(fixture())
        self.assertEqual(report["total_score"], 60.59)
        self.assertTrue(report["meets_selected_passing_score"])
        self.assertFalse(report["chapter_visits"]["full_mark_reached"])
        self.assertFalse(report["chapter_visits"]["raw_visit_count_verified"])

    def test_other_course_uses_own_weight_and_threshold(self):
        weights = dict(zip(audit.FIELDS, (20, 25, 25, 5, 5, 5, 10, 5)))
        actual = dict(zip(audit.FIELDS, (20, 25, 25, 5, 5, 5, 10, 5)))
        report = audit.parse_course_grade(fixture(weights, actual, count=80, minutes=60))
        self.assertEqual(report["chapter_visits"]["full_mark_count"], 80)
        self.assertEqual(report["reading"]["full_mark_minutes"], 60)
        self.assertEqual(report["parts"]["visitTime"]["weight_percent"], 10)
        self.assertTrue(report["chapter_visits"]["full_mark_reached"])

    def test_native_percent_weight_and_plain_score_format(self):
        weights = {k: str(v) + "%" for k, v in dict(zip(audit.FIELDS, (10, 25, 25, 5, 5, 5, 20, 5))).items()}
        html = fixture(weights=weights).replace('"score": 100', '"score": "100%"')
        self.assertEqual(audit.parse_course_grade(html)["total_score"], 60.59)

    def test_percent_in_actual_score_is_not_silently_treated_as_points(self):
        html = fixture().replace('"visitTime": 18.33', '"visitTime": "18.33%"')
        with self.assertRaises(audit.GradeUnavailable):
            audit.parse_course_grade(html)

    def test_numeric_suffix_does_not_parse_as_a_shorter_number(self):
        with self.assertRaises(audit.GradeUnavailable):
            audit.parse_course_grade(fixture().replace('"visitTime": 18.33', '"visitTime": "18.33invalid"'))

    def test_teacher_or_platform_duplicate_disagreement_is_unverified(self):
        with self.assertRaises(audit.GradeUnavailable):
            audit.parse_course_grade(fixture().replace('"visitTime": 18.33', '"visitTime": 19.33', 1))

    def test_unrecognized_weight_cannot_look_complete(self):
        weights = dict(zip(audit.FIELDS, (10, 25, 20, 5, 5, 5, 20, 5)))
        actual = dict(zip(audit.FIELDS, (10, 0, 20, 5, 4.25, 1.5, 18.33, 0.01)))
        with self.assertRaisesRegex(audit.GradeUnavailable, "平台考核权重不完整"):
            audit.parse_course_grade(fixture(weights=weights, actual=actual))

    def test_zero_optional_live_weight_is_supported(self):
        html = fixture().replace('"score": 100', '"liveTime": 0, "score": 100')
        html = html.replace('"score": 60.59', '"liveTime": 0, "score": 60.59')
        self.assertEqual(audit.parse_course_grade(html)["parts"]["liveTime"]["weighted_score"], 0)

    def test_no_rule_is_not_assumed_to_be_120(self):
        report = audit.parse_course_grade(fixture().replace("章节学习次数达", "隐藏规则"))
        self.assertIsNone(report["chapter_visits"]["full_mark_count"])
        self.assertTrue(report["warnings"])

    def test_ambiguous_rule_is_not_guessed(self):
        report = audit.parse_course_grade(fixture() + "<div>章节学习次数达80次为满分</div>")
        self.assertIsNone(report["chapter_visits"]["full_mark_count"])

    def test_low_score_has_explicit_pass_result(self):
        self.assertFalse(audit.parse_course_grade(fixture(), 70)["meets_selected_passing_score"])

    def test_total_mismatch_is_rejected(self):
        with self.assertRaises(audit.GradeUnavailable):
            audit.parse_course_grade(fixture().replace('"score": 60.59', '"score": 61.59'))

    def test_missing_fields_cannot_pass_and_html_is_not_in_error(self):
        with self.assertRaises(audit.GradeUnavailable) as caught:
            audit.parse_course_grade("<html>private-sentinel login required</html>")
        self.assertNotIn("private-sentinel", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
