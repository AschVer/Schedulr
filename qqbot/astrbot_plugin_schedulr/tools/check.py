#!/usr/bin/env python3
"""Fetch the BITC timetable once and print a short summary.

Run this on the same machine as AstrBot before configuring the plugin:

    python tools/check.py --cookie "wrdvpn_session=..." --term 1

The cookie is never printed back.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from schedulr_core import (  # noqa: E402
    BitcError,
    fetch_timetable,
    normalize_cookie,
    resolve_academic_year,
    resolve_term,
)

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    TZ = timezone(timedelta(hours=8))


async def _run(args: argparse.Namespace) -> int:
    cookie = args.cookie or ""
    if args.cookie_file:
        cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    if not cookie:
        print("缺少 Cookie：用 --cookie 或 --cookie-file 传入", file=sys.stderr)
        return 2

    now = datetime.now(TZ)
    year = resolve_academic_year(args.year, now)
    term = resolve_term(args.term, now)
    semester_start = date.fromisoformat(args.start) if args.start else None

    try:
        result = await fetch_timetable(
            cookie,
            academic_year=year,
            term=term,
            now=now,
            semester_start=semester_start,
            teaching_weeks=args.weeks,
            timeout=args.timeout,
        )
    except BitcError as error:
        print(f"失败：{error}", file=sys.stderr)
        return 1

    timetable = result.timetable
    if result.cookie and result.cookie != normalize_cookie(cookie):
        print("Cookie 已自动续期。")
        if args.cookie_out:
            Path(args.cookie_out).write_text(result.cookie, encoding="utf-8")
            print(f"新 Cookie 已写入：{args.cookie_out}")

    week = timetable.week_for(now.date())
    print("✅ 拉取成功")
    print(f"学期：{timetable.term_label}（{timetable.teaching_weeks} 周）")
    print(f"开学：{timetable.start_date or '未识别（请配置 semester_start）'}")
    print(f"当前：{f'第 {week} 周' if week and week > 0 else '不在教学周'}")
    print(f"课程：{len(timetable.courses)} 节")
    for course in timetable.courses[:6]:
        days = "一二三四五六日"[course.weekday - 1]
        print(
            f"  - 周{days} 第{course.start_period}-{course.end_period}节 "
            f"{course.name} {course.location or ''}"
        )
    if timetable.warnings:
        print("提示：")
        for warning in timetable.warnings[:5]:
            print(f"  - {warning}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="测试 BITC 课表拉取")
    parser.add_argument("--cookie", default="", help="WebVPN Cookie 请求头")
    parser.add_argument("--cookie-file", default="", help="从文件读取 Cookie")
    parser.add_argument("--cookie-out", default="", help="把自动续期后的 Cookie 写入文件")
    parser.add_argument("--year", default="", help="学年，如 2026-2027；留空自动")
    parser.add_argument("--term", type=int, default=0, help="学期 1/2/3；0 自动")
    parser.add_argument("--start", default="", help="开学日期 YYYY-MM-DD")
    parser.add_argument("--weeks", type=int, default=18, help="教学周数")
    parser.add_argument("--timeout", type=int, default=20, help="超时秒数")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
