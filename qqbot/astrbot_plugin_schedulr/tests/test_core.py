from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from schedulr_core import (  # noqa: E402
    CookieJar,
    Course,
    PeriodTime,
    Timetable,
    TimetableStore,
    build_timetable,
    infer_start_date,
    parse_period_range,
    parse_query,
    parse_weeks,
    render_answer,
    resolve_academic_year,
    resolve_term,
    term_code,
)

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=TZ)  # Monday, week 8


class WeekParsingTest(unittest.TestCase):
    def test_parses_range_parity_and_mixed(self):
        self.assertEqual(
            parse_weeks("2-4周(双),5-7周(单),8-14周"),
            (2, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14),
        )
        self.assertEqual(parse_weeks("1-3周,6周,10-12周(双)"), (1, 2, 3, 6, 10, 12))
        self.assertEqual(parse_weeks("第1-2周"), (1, 2))

    def test_rejects_invalid_weeks(self):
        with self.assertRaises(ValueError):
            parse_weeks("")
        with self.assertRaises(ValueError):
            parse_weeks("0-3周")
        with self.assertRaises(ValueError):
            parse_weeks("abc")


class PeriodParsingTest(unittest.TestCase):
    def test_range(self):
        self.assertEqual(parse_period_range("1-2"), (1, 2))
        self.assertEqual(parse_period_range("3-4节"), (3, 4))
        self.assertEqual(parse_period_range("5"), (5, 5))
        self.assertEqual(parse_period_range("", "7-8"), (7, 8))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_period_range("")
        with self.assertRaises(ValueError):
            parse_period_range("4-2")


class CalendarTest(unittest.TestCase):
    def test_infers_monday_of_week_one(self):
        anchors = [
            {"rq": "2026-09-23", "xqj": 3, "zc": 3},
            {"rq": "2026-10-05", "xqj": 1, "zc": 5},
        ]
        self.assertEqual(infer_start_date(anchors, 3), date(2026, 9, 7))

    def test_conflicting_anchors_are_ignored(self):
        anchors = [
            {"rq": "2026-09-23", "xqj": 3, "zc": 3},
            {"rq": "2026-10-05", "xqj": 1, "zc": 4},
        ]
        self.assertIsNone(infer_start_date(anchors, None))


class TermResolutionTest(unittest.TestCase):
    def test_academic_year(self):
        self.assertEqual(resolve_academic_year("2026-2027", NOW), "2026-2027")
        self.assertEqual(resolve_academic_year("2026", NOW), "2026-2027")
        self.assertEqual(resolve_academic_year("", NOW), "2026-2027")
        self.assertEqual(
            resolve_academic_year("", datetime(2026, 3, 1, tzinfo=TZ)),
            "2025-2026",
        )

    def test_term(self):
        self.assertEqual(resolve_term(1, NOW), 1)
        self.assertEqual(resolve_term(3, NOW), 3)
        self.assertEqual(resolve_term(0, datetime(2026, 3, 1, tzinfo=TZ)), 2)
        self.assertEqual(term_code(1), "3")
        self.assertEqual(term_code(2), "12")
        self.assertEqual(term_code(3), "16")


def _payload():
    return {
        "kbList": [
            {
                "jxb_id": "class-1",
                "kch": "MATH-101",
                "kcmc": "<span>高等数学</span>",
                "xm": "张老师",
                "xqj": "1",
                "jcs": "1-2",
                "jcor": "1-2",
                "zcd": "1-16周",
                "xqmc": "北校区",
                "cdmc": "教一楼101",
                "xqh_id": "01",
            },
            {
                "jxb_id": "class-2",
                "kch": "CS-201",
                "kcmc": "数据结构",
                "xm": "李老师",
                "xqj": 3,
                "jcs": "3-4",
                "jcor": "3-4",
                "zcd": "1-8周(单)",
                "xqmc": "北校区",
                "cdmc": "信息楼 302",
                "xqh_id": "01",
            },
        ],
        "zs": "3",
        "rqazcList": [{"rq": "2026-09-23", "xqj": 3, "zc": 3}],
    }


def _periods():
    return {
        "01": [
            {"jcmc": "1", "qssj": "8:00", "jssj": "8:45"},
            {"jcmc": "2", "qssj": "8:55", "jssj": "9:40"},
            {"jcmc": "3", "qssj": "10:00", "jssj": "10:45"},
            {"jcmc": "4", "qssj": "10:55", "jssj": "11:40"},
        ]
    }


def _timetable() -> Timetable:
    return build_timetable(
        _payload(),
        _periods(),
        academic_year="2026-2027",
        term=1,
        now=NOW,
        teaching_weeks=18,
    )


class BuildTimetableTest(unittest.TestCase):
    def test_normalizes_courses_and_calendar(self):
        timetable = _timetable()
        self.assertEqual(timetable.start_date, date(2026, 9, 7))
        self.assertEqual(len(timetable.courses), 2)
        self.assertEqual(timetable.courses[0].name, "高等数学")
        self.assertEqual(timetable.courses[0].location, "北校区 · 教一楼101")
        self.assertEqual(timetable.courses[1].weeks, (1, 3, 5, 7))
        self.assertEqual(timetable.profiles["profile-0"][0].start, "08:00")

    def test_skips_broken_row_without_failing(self):
        payload = _payload()
        payload["kbList"].append({"kcmc": "坏课程", "xqj": 9, "jcs": "1-2", "zcd": "1周"})
        timetable = build_timetable(
            payload,
            _periods(),
            academic_year="2026-2027",
            term=1,
            now=NOW,
        )
        self.assertEqual(len(timetable.courses), 2)
        self.assertTrue(timetable.warnings)

    def test_semester_start_uses_containing_monday(self):
        timetable = build_timetable(
            _payload(),
            _periods(),
            academic_year="2026-2027",
            term=1,
            now=NOW,
            semester_start=date(2026, 9, 3),
        )
        self.assertEqual(timetable.start_date, date(2026, 8, 31))
        self.assertEqual(timetable.week_for(date(2026, 10, 31)), 9)


class QueryTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_query("/课表 明天").kind, "tomorrow")
        self.assertEqual(parse_query("课表 周三").target, 3)
        self.assertEqual(parse_query("/课表 第10周").target, 10)
        self.assertEqual(parse_query("课表 本周").kind, "this_week")
        self.assertEqual(parse_query("课表").kind, "today")

    def test_day_render(self):
        text = render_answer(_timetable(), parse_query("课表 今天"), NOW)
        self.assertIn("10月26日", text)
        self.assertIn("第 8 周", text)
        self.assertIn("08:00–09:40 高等数学", text)
        self.assertIn("教一楼101", text)
        self.assertNotIn("数据结构", text)

    def test_odd_week_filter(self):
        # Week 8 is even, so the odd-week course is not shown.
        text = render_answer(_timetable(), parse_query("课表 周三"), NOW)
        self.assertIn("没课", text)
        week_text = render_answer(_timetable(), parse_query("课表 第7周"), NOW)
        self.assertIn("数据结构", week_text)

    def test_next_course(self):
        text = render_answer(_timetable(), parse_query("课表 下一节"), NOW)
        self.assertIn("正在上：高等数学", text)
        later = NOW + timedelta(hours=2)
        text = render_answer(_timetable(), parse_query("课表 下一节"), later)
        self.assertIn("下一节", text)


class StoreTest(unittest.TestCase):
    def test_roundtrip(self):
        timetable = _timetable()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "timetable.json"
            store = TimetableStore(path)
            store.save(timetable)
            loaded = store.load()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.start_date, timetable.start_date)
        self.assertEqual(len(loaded.courses), 2)
        self.assertEqual(loaded.fetched_at, timetable.fetched_at)


class CookieJarTest(unittest.TestCase):
    def test_sliding_cookie_update(self):
        jar = CookieJar("_webvpn_key=old; JSESSIONID=abc")
        self.assertEqual(jar.header(), "_webvpn_key=old; JSESSIONID=abc")
        changed = jar.update_from_headers(
            [
                "_webvpn_key=new; Path=/; HttpOnly; Secure",
                "webvpn_username=20263201236%7C123; Path=/",
            ]
        )
        self.assertTrue(changed)
        self.assertIn("_webvpn_key=new", jar.header())
        self.assertIn("webvpn_username=", jar.header())

    def test_deletion(self):
        jar = CookieJar("a=1; b=2")
        jar.update_from_headers(["a=; Max-Age=0; Path=/"])
        self.assertEqual(jar.header(), "b=2")


if __name__ == "__main__":
    unittest.main()
