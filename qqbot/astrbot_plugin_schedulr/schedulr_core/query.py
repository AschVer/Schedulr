"""Question parsing and QQ-friendly rendering."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .models import (
    Course,
    Timetable,
    WEEKDAY_FULL,
    WEEKDAY_SHORT,
    next_course,
)

WEEKDAY_ALIASES = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
}


@dataclass(frozen=True)
class Query:
    kind: str
    target: int | None = None


def parse_query(raw: str) -> Query:
    text = unicodedata.normalize("NFKC", raw or "").strip()
    text = re.sub(r"^@\S+\s*", "", text)
    text = re.sub(
        r"^[/／]?(课表|课程表|timetable|schedule)\b",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    compact = text.replace(" ", "")

    if compact in ("", "今天", "今日", "today"):
        return Query("today")
    if compact in ("明天", "明日", "tomorrow"):
        return Query("tomorrow")
    if compact in ("后天",):
        return Query("day_offset", 2)
    if compact in ("本周", "这周", "current", "week"):
        return Query("this_week")
    if compact in ("下周", "下一周", "nextweek"):
        return Query("next_week")
    if compact in ("下一节", "下节课", "next", "nextclass"):
        return Query("next")
    if compact in ("状态", "status"):
        return Query("status")
    if compact in ("刷新", "refresh", "更新"):
        return Query("refresh")
    if compact in ("帮助", "help", "?", "？"):
        return Query("help")

    match = re.fullmatch(r"第?(\d{1,2})周", compact)
    if match:
        return Query("week", int(match.group(1)))

    match = re.fullmatch(r"(?:周|星期)([一二三四五六日天1-7])", compact)
    if match:
        target = WEEKDAY_ALIASES[match.group(1)]
        return Query("weekday", target)

    return Query("help")


def render_answer(
    timetable: Timetable,
    query: Query,
    now: datetime,
    *,
    show_footer: bool = True,
) -> str:
    if query.kind == "help":
        return help_text()
    if query.kind == "weekday":
        assert query.target is not None
        delta = (query.target - now.isoweekday()) % 7
        return _day_answer(timetable, now + timedelta(days=delta), now, show_footer)
    if query.kind == "day_offset":
        return _day_answer(
            timetable,
            now + timedelta(days=query.target or 0),
            now,
            show_footer,
        )
    if query.kind == "tomorrow":
        return _day_answer(timetable, now + timedelta(days=1), now, show_footer)
    if query.kind == "this_week":
        week = timetable.week_for(now.date())
        if not week:
            return _no_week_text(timetable, now)
        return _week_answer(timetable, week, now, show_footer)
    if query.kind == "next_week":
        week = timetable.week_for(now.date())
        if not week:
            return _no_week_text(timetable, now)
        return _week_answer(timetable, week + 1, now, show_footer)
    if query.kind == "week":
        assert query.target is not None
        return _week_answer(timetable, query.target, now, show_footer)
    if query.kind == "next":
        return _next_answer(timetable, now, show_footer)
    return _day_answer(timetable, now, now, show_footer)


def render_status(
    timetable: Timetable | None,
    now: datetime,
    *,
    has_cookie: bool,
    last_error: str | None,
    refresh_minutes: int,
) -> str:
    lines = ["📊 课表状态"]
    if timetable is None:
        lines.append("还没有课表数据。")
    else:
        lines.append(f"课程：{len(timetable.courses)} 节")
        lines.append(
            f"学期：{timetable.term_label}（{timetable.teaching_weeks} 周）"
        )
        week = timetable.week_for(now.date())
        if week and week > 0:
            lines.append(f"当前：第 {week} 周")
        lines.append(f"数据：{_stamp(timetable.fetched_at)}（{_age(timetable.fetched_at, now)}）")
    lines.append(f"Cookie：{'已配置' if has_cookie else '未配置'}")
    lines.append(f"自动刷新：每 {refresh_minutes} 分钟")
    if last_error:
        lines.append(f"最近错误：{last_error}")
    return "\n".join(lines)


def help_text() -> str:
    return "\n".join(
        [
            "📚 课表查询",
            "/课表              两版今天的课",
            "/课表 明天         两版明天的课",
            "/课表 一班         一班的今天",
            "/课表 二班 明天    二班的明天",
            "/课表 1 本周       一班本周",
            "/课表 2 第10周     二班第 10 周",
            "/课表 一班 下一节",
            "/课表 状态",
            "/课表 刷新",
        ]
    )


def _day_answer(
    timetable: Timetable,
    day: datetime,
    now: datetime,
    show_footer: bool,
) -> str:
    date_value = day.date()
    week = timetable.week_for(date_value)
    header = f"📅 {_date_label(date_value)}"
    if week:
        header += f" · 第 {week} 周"
    elif week == 0:
        header += " · 未开学"
    elif timetable.start_date:
        header += " · 假期"
    courses = timetable.courses_on(date_value)
    lines = [header]

    if not courses:
        if week is None and timetable.start_date:
            lines.append("本学期已经结束。")
        elif week == 0:
            lines.append(f"还没开学（开学日期 {timetable.start_date:%m-%d}）。")
        else:
            lines.append("今天没课。")
        upcoming = next_course(timetable, now)
        if upcoming is not None:
            course, start, end = upcoming
            if start <= now <= end:
                lines.append(
                    f"正在上：{course.name}（{start:%H:%M}–{end:%H:%M}）"
                )
            else:
                lines.append(
                    f"下一节：{start:%m-%d %H:%M} {course.name}"
                    f"（{_meta(course) or '地点未填'}）"
                )
    else:
        lines.append(f"共 {len(courses)} 节课：")
        for index, course in enumerate(courses, start=1):
            lines.append(f"{index}. {_time_range(timetable, course)} {course.name}")
            meta = _meta(course)
            if meta:
                lines.append(f"   {meta}")
    if show_footer:
        lines.append("")
        lines.append(f"— 数据更新 {_stamp(timetable.fetched_at)}")
    return "\n".join(lines)


def _week_answer(
    timetable: Timetable,
    week: int,
    now: datetime,
    show_footer: bool,
) -> str:
    if week < 1 or week > timetable.teaching_weeks:
        return f"第 {week} 周不在本学期范围内（共 {timetable.teaching_weeks} 周）。"
    if timetable.start_date is None:
        monday = None
    else:
        monday = timetable.start_date + timedelta(weeks=week - 1)
    if monday is None:
        header = f"📅 第 {week} 周"
    else:
        sunday = monday + timedelta(days=6)
        header = f"📅 第 {week} 周 · {monday:%m/%d} - {sunday:%m/%d}"
    lines = [header]
    if monday is None:
        courses = timetable.courses_in_week(week)
        if not courses:
            lines.append("这一周没有课。")
        for course in courses:
            lines.append(
                f"{WEEKDAY_SHORT[course.weekday - 1]} "
                f"{_start_time(timetable, course)} {course.name}"
                f"{_at(course)}"
            )
    else:
        for offset in range(7):
            day = monday + timedelta(days=offset)
            courses = timetable.courses_on(day)
            lines.append(f"{WEEKDAY_SHORT[offset]} {day:%m/%d}")
            if not courses:
                lines.append("  无课")
                continue
            for course in courses:
                lines.append(
                    f"  · {_start_time(timetable, course)} {course.name}"
                    f"{_at(course)}"
                )
    if show_footer:
        lines.append("")
        lines.append(f"— 数据更新 {_stamp(timetable.fetched_at)}")
    return "\n".join(lines)


def _next_answer(timetable: Timetable, now: datetime, show_footer: bool) -> str:
    upcoming = next_course(timetable, now)
    if upcoming is None:
        return "未来两周没有找到课程。"
    course, start, end = upcoming
    if start <= now <= end:
        text = (
            f"⏳ 正在上：{course.name}\n"
            f"{start:%H:%M}–{end:%H:%M}｜{_meta(course) or '地点未填'}"
        )
    else:
        text = (
            f"⏭ 下一节：{start:%m-%d %H:%M} {course.name}\n"
            f"{_meta(course) or '地点未填'}"
        )
    if show_footer:
        text += f"\n\n— 数据更新 {_stamp(timetable.fetched_at)}"
    return text


def _no_week_text(timetable: Timetable, now: datetime) -> str:
    if timetable.start_date is None:
        return "还没配置开学日期，先在 App 里设置学期或刷新一次课表。"
    if now.date() < timetable.start_date:
        return f"还没开学（开学日期 {timetable.start_date:%m-%d}）。"
    return "本学期已经结束。"


def _time_range(timetable: Timetable, course: Course) -> str:
    start, end = timetable.period_times(course)
    if start and end:
        return f"{start}–{end}"
    return f"第{course.start_period}-{course.end_period}节"


def _start_time(timetable: Timetable, course: Course) -> str:
    start, _ = timetable.period_times(course)
    return start or f"第{course.start_period}节"


def _meta(course: Course) -> str:
    return " · ".join(value for value in (course.location, course.teacher) if value)


def _at(course: Course) -> str:
    return f" @ {course.location}" if course.location else ""


def _date_label(day: date) -> str:
    return f"{day.month}月{day.day}日 {WEEKDAY_FULL[day.isoweekday() - 1]}"


def _stamp(value: datetime) -> str:
    return value.strftime("%m-%d %H:%M")


def _age(then: datetime, now: datetime) -> str:
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    return f"{hours // 24} 天前"
