"""Core data model for the Schedulr AstrBot plugin.

Only timetable fields are kept here. Student identity (name / student number)
from the school response is deliberately never stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

WEEKDAY_SHORT = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
WEEKDAY_FULL = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _normalize_time(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


@dataclass(frozen=True)
class PeriodTime:
    """One teaching period, e.g. period 1 starts at 08:00."""

    number: int
    start: str
    end: str

    @property
    def start_minutes(self) -> int:
        return _minutes(self.start)

    @property
    def end_minutes(self) -> int:
        return _minutes(self.end)

    def to_dict(self) -> dict[str, Any]:
        return {"number": self.number, "start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PeriodTime":
        number = int(raw["number"])
        start = _normalize_time(raw["start"])
        end = _normalize_time(raw["end"])
        if number < 1 or start is None or end is None:
            raise ValueError("invalid period")
        return cls(number=number, start=start, end=end)


@dataclass(frozen=True)
class Course:
    """A single timetable cell (one course session)."""

    name: str
    teacher: str | None
    location: str | None
    weekday: int
    start_period: int
    end_period: int
    weeks: tuple[int, ...]
    profile_id: str | None = None

    def active_in(self, week: int) -> bool:
        return week in self.weeks

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "teacher": self.teacher,
            "location": self.location,
            "weekday": self.weekday,
            "start_period": self.start_period,
            "end_period": self.end_period,
            "weeks": list(self.weeks),
            "profile_id": self.profile_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Course":
        weeks = tuple(sorted({int(value) for value in raw["weeks"]}))
        start = int(raw["start_period"])
        end = int(raw["end_period"])
        weekday = int(raw["weekday"])
        if not weeks or start < 1 or end < start or not 1 <= weekday <= 7:
            raise ValueError("invalid course")
        name = str(raw["name"]).strip()
        if not name:
            raise ValueError("empty course name")
        teacher = raw.get("teacher")
        location = raw.get("location")
        return cls(
            name=name,
            teacher=str(teacher).strip() if teacher else None,
            location=str(location).strip() if location else None,
            weekday=weekday,
            start_period=start,
            end_period=end,
            weeks=weeks,
            profile_id=raw.get("profile_id"),
        )


@dataclass
class Timetable:
    """Normalized timetable plus the context needed to answer questions."""

    academic_year: str
    term: int
    term_label: str
    start_date: date | None
    teaching_weeks: int
    profiles: dict[str, list[PeriodTime]]
    courses: list[Course]
    fetched_at: datetime
    campus_names: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def week_for(self, day: date) -> int | None:
        """Return the teaching week for *day*.

        ``0`` means "before the semester", ``None`` means "after the semester"
        or "unknown start date".
        """

        if self.start_date is None:
            return None
        delta = (day - self.start_date).days
        if delta < 0:
            return 0
        week = delta // 7 + 1
        return week if week <= self.teaching_weeks else None

    def courses_on(self, day: date) -> list[Course]:
        week = self.week_for(day)
        if week is None or week == 0:
            return []
        return sorted(
            (
                course
                for course in self.courses
                if course.weekday == day.isoweekday() and course.active_in(week)
            ),
            key=lambda course: (course.start_period, course.name),
        )

    def courses_in_week(self, week: int) -> list[Course]:
        return sorted(
            (course for course in self.courses if course.active_in(week)),
            key=lambda course: (course.weekday, course.start_period, course.name),
        )

    def period_times(self, course: Course) -> tuple[str | None, str | None]:
        periods = self.profiles.get(course.profile_id or "") or next(
            iter(self.profiles.values()), []
        )
        by_number = {period.number: period for period in periods}
        start = by_number.get(course.start_period)
        end = by_number.get(course.end_period)
        if start is None or end is None:
            return None, None
        return start.start, end.end

    def course_start(self, course: Course, day: date) -> datetime | None:
        start, _ = self.period_times(course)
        if start is None:
            return None
        hour, minute = (int(part) for part in start.split(":"))
        return datetime.combine(day, time(hour, minute), tzinfo=self.fetched_at.tzinfo)

    def course_end(self, course: Course, day: date) -> datetime | None:
        _, end = self.period_times(course)
        if end is None:
            return None
        hour, minute = (int(part) for part in end.split(":"))
        return datetime.combine(day, time(hour, minute), tzinfo=self.fetched_at.tzinfo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "academic_year": self.academic_year,
            "term": self.term,
            "term_label": self.term_label,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "teaching_weeks": self.teaching_weeks,
            "profiles": {
                profile_id: [period.to_dict() for period in periods]
                for profile_id, periods in self.profiles.items()
            },
            "campus_names": self.campus_names,
            "courses": [course.to_dict() for course in self.courses],
            "fetched_at": self.fetched_at.isoformat(),
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Timetable":
        if raw.get("version") != 1:
            raise ValueError("unsupported timetable cache version")
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        if fetched_at.tzinfo is None:
            raise ValueError("cache timestamp must be timezone-aware")
        start_raw = raw.get("start_date")
        start_date = date.fromisoformat(start_raw) if start_raw else None
        profiles = {
            str(profile_id): [PeriodTime.from_dict(item) for item in periods]
            for profile_id, periods in raw.get("profiles", {}).items()
        }
        courses = [Course.from_dict(item) for item in raw.get("courses", [])]
        return cls(
            academic_year=str(raw["academic_year"]),
            term=int(raw["term"]),
            term_label=str(raw["term_label"]),
            start_date=start_date,
            teaching_weeks=max(1, int(raw["teaching_weeks"])),
            profiles=profiles,
            courses=courses,
            fetched_at=fetched_at,
            campus_names={
                str(key): str(value)
                for key, value in raw.get("campus_names", {}).items()
            },
            warnings=[str(value) for value in raw.get("warnings", [])],
        )


def next_course(
    timetable: Timetable, now: datetime, days: int = 14
) -> tuple[Course, datetime, datetime] | None:
    """Find the next course starting after *now* (or the one in progress)."""

    for offset in range(days + 1):
        day = (now + timedelta(days=offset)).date()
        week = timetable.week_for(day)
        if not week:
            continue
        for course in timetable.courses_on(day):
            start = timetable.course_start(course, day)
            end = timetable.course_end(course, day)
            if start is None or end is None:
                continue
            if end >= now:
                return course, start, end
    return None
