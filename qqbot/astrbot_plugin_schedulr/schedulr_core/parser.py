"""Parsing helpers for the BITC Zhengfang V9 timetable payload.

The parser is intentionally defensive: one broken row should not make the
whole timetable unusable. Invalid rows are skipped and reported as warnings.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .models import Course, PeriodTime, Timetable

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
TERM_LABELS = {1: "第一学期", 2: "第二学期", 3: "第三学期"}


class TimetableParseError(ValueError):
    """Raised when the payload cannot be interpreted as a timetable."""


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(text).strip()


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


def parse_weeks(value: str) -> tuple[int, ...]:
    """Parse BITC week expressions such as ``1-8周(单),10-16周``."""

    text = normalize_text(value).replace("，", ",").replace("；", ",").replace("、", ",")
    text = text.replace(" ", "").replace("第", "")
    weeks: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?周?(?:\(?([单双])周?\)?)?", part)
        if not match:
            raise TimetableParseError(f"无法识别的周次写法：{part}")
        start = int(match.group(1))
        end = int(match.group(2) or match.group(1))
        parity = match.group(3)
        if start < 1 or end < start or end > 60:
            raise TimetableParseError(f"周次范围无效：{part}")
        for week in range(start, end + 1):
            if parity == "单" and week % 2 == 0:
                continue
            if parity == "双" and week % 2 == 1:
                continue
            weeks.add(week)
    if not weeks:
        raise TimetableParseError(f"周次为空：{value}")
    return tuple(sorted(weeks))


def parse_period_range(value: str, fallback: str | None = None) -> tuple[int, int]:
    text = normalize_text(value or fallback or "")
    numbers = [int(item) for item in re.findall(r"\d+", text)]
    if not numbers or len(numbers) > 2:
        raise TimetableParseError(f"无法识别的节次：{value}")
    start = numbers[0]
    end = numbers[1] if len(numbers) == 2 else start
    if start < 1 or end < start or end > 30:
        raise TimetableParseError(f"节次范围无效：{value}")
    return start, end


def infer_start_date(
    anchors: Iterable[dict[str, Any]] | None,
    current_week: int | None,
) -> date | None:
    """Infer the Monday of teaching week 1 from BITC's calendar anchors."""

    if not anchors:
        return None
    starts: set[date] = set()
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        raw_date = anchor.get("rq")
        raw_weekday = anchor.get("xqj")
        raw_week = anchor.get("zc")
        try:
            day = date.fromisoformat(str(raw_date))
            weekday = int(raw_weekday)
            week = int(raw_week) if raw_week is not None else current_week
        except (TypeError, ValueError):
            continue
        if not 1 <= weekday <= 7 or week is None or week < 1:
            continue
        if day.isoweekday() != weekday:
            continue
        start = day - timedelta(days=(weekday - 1) + (week - 1) * 7)
        starts.add(start)
    return starts.pop() if len(starts) == 1 else None


def resolve_academic_year(value: str | None, now: datetime) -> str:
    text = normalize_text(value or "")
    if not text:
        start_year = now.year if now.month >= 8 else now.year - 1
        return f"{start_year}-{start_year + 1}"
    match = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", text)
    if not match:
        raise TimetableParseError("学年格式应为 2026-2027 或 2026")
    start_year = int(match.group(1))
    return f"{start_year}-{start_year + 1}"


def resolve_term(value: int | str | None, now: datetime) -> int:
    if value not in (None, "", 0, "0"):
        try:
            term = int(value)
        except (TypeError, ValueError) as error:
            raise TimetableParseError("学期必须是 1、2 或 3") from error
        if term not in (1, 2, 3):
            raise TimetableParseError("学期必须是 1、2 或 3")
        return term
    return 1 if now.month >= 8 or now.month == 1 else 2


def term_code(term: int) -> str:
    """Map display term to the real Zhengfang xqm code."""

    return {1: "3", 2: "12", 3: "16"}[term]


def term_label(academic_year: str, term: int) -> str:
    start_year = academic_year.split("-", 1)[0]
    return f"{start_year}-{int(start_year) + 1} 学年 {TERM_LABELS[term]}"


def _parse_periods(
    raw_periods: Any,
) -> list[PeriodTime]:
    if isinstance(raw_periods, dict):
        raw_periods = raw_periods.get("items")
    if not isinstance(raw_periods, list):
        return []
    parsed: dict[int, PeriodTime] = {}
    for row in raw_periods:
        if not isinstance(row, dict):
            continue
        raw_number = row.get("jcmc", row.get("number"))
        number_match = re.search(r"\d+", str(raw_number or ""))
        if not number_match:
            continue
        number = int(number_match.group(0))
        start = _normalize_clock(row.get("qssj", row.get("startTime")))
        end = _normalize_clock(row.get("jssj", row.get("endTime")))
        if number < 1 or start is None or end is None:
            continue
        if number in parsed:
            continue
        parsed[number] = PeriodTime(number=number, start=start, end=end)
    return [parsed[number] for number in sorted(parsed)]


def _normalize_clock(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _location(row: dict[str, Any]) -> str | None:
    campus = normalize_text(str(row.get("xqmc") or ""))
    room = normalize_text(str(row.get("cdmc") or ""))
    if campus and room:
        return f"{campus} · {room}"
    return campus or room or None


def _campus_name(row: dict[str, Any], index: int) -> str:
    name = normalize_text(str(row.get("xqmc") or ""))
    return name or f"校区 {index + 1}"


def build_timetable(
    payload: dict[str, Any],
    periods_by_campus: dict[str, Any],
    *,
    academic_year: str,
    term: int,
    now: datetime,
    semester_start: date | None = None,
    teaching_weeks: int = 18,
) -> Timetable:
    """Turn a raw ``xskbcx_cxXsgrkb`` payload into a normalized timetable."""

    rows = payload.get("kbList")
    if not isinstance(rows, list):
        raise TimetableParseError("响应里没有 kbList 数组")

    warnings: list[str] = []
    campus_order: list[str] = []
    campus_names: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        campus_id = str(row.get("xqh_id") or "").strip()
        if campus_id in campus_order:
            continue
        campus_order.append(campus_id)
        campus_names[campus_id] = _campus_name(row, len(campus_order) - 1)

    profile_ids = {
        campus_id: f"profile-{index}"
        for index, campus_id in enumerate(campus_order or [""])
    }
    campus_to_profile = {
        campus_id: profile_ids[campus_id] for campus_id in campus_order or [""]
    }
    profiles: dict[str, list[PeriodTime]] = {}
    for campus_id in campus_order or [""]:
        profile_id = campus_to_profile[campus_id]
        periods = _parse_periods(periods_by_campus.get(campus_id))
        if not periods:
            warnings.append(f"{campus_names.get(campus_id, '默认校区')}：作息时间缺失")
        profiles[profile_id] = periods

    courses: list[Course] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            name = strip_html(str(row.get("kcmc") or ""))
            if not name:
                continue
            weekday = int(row.get("xqj"))
            if not 1 <= weekday <= 7:
                continue
            start, end = parse_period_range(
                str(row.get("jcs") or ""), str(row.get("jcor") or "")
            )
            weeks = parse_weeks(str(row.get("zcd") or ""))
            campus_id = str(row.get("xqh_id") or "").strip()
            course = Course(
                name=name,
                teacher=normalize_text(str(row.get("xm") or "")) or None,
                location=_location(row),
                weekday=weekday,
                start_period=start,
                end_period=end,
                weeks=weeks,
                profile_id=campus_to_profile.get(campus_id)
                or campus_to_profile.get(""),
            )
        except (TimetableParseError, TypeError, ValueError) as error:
            warnings.append(f"跳过一条课程：{error}")
            continue
        key = (
            course.name,
            course.teacher,
            course.location,
            course.weekday,
            course.start_period,
            course.end_period,
            course.weeks,
        )
        if key in seen:
            continue
        seen.add(key)
        courses.append(course)

    if not courses:
        raise TimetableParseError("课表为空，或所有课程字段都无法解析")

    anchors = payload.get("rqazcList")
    current_week = _positive_int(payload.get("zs"))
    start_date = semester_start or infer_start_date(anchors, current_week)
    max_week = max((max(course.weeks) for course in courses), default=1)
    effective_weeks = max(1, int(teaching_weeks), max_week)

    return Timetable(
        academic_year=academic_year,
        term=term,
        term_label=term_label(academic_year, term),
        start_date=start_date,
        teaching_weeks=effective_weeks,
        profiles=profiles,
        courses=courses,
        fetched_at=now,
        campus_names={
            profile_id: campus_names.get(campus_id, "默认校区")
            for campus_id, profile_id in campus_to_profile.items()
        },
        warnings=warnings,
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
