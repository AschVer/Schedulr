"""Minimal BITC WebVPN client used by the QQ bot.

The app normally talks to the school through a WebView. Headless, the only
thing we need is the WebVPN cookie; the timetable endpoint itself is a normal
form POST returning JSON. No account password is read or stored here.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any

from .models import Timetable
from .parser import TimetableParseError, build_timetable

BASE_URL = "https://jwxt.vpn.bitc.edu.cn"
ENTRY_URL = (
    f"{BASE_URL}/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MAX_BODY_BYTES = 4 * 1024 * 1024

_GATEWAY_MARKERS = ("权限表不同步", "站点访问异常")
_LOGIN_MARKERS = ("统一身份认证", "iam/login", "请登录", "cas/login")


class BitcError(RuntimeError):
    """Base class for user-facing BITC fetch errors."""


class BitcAuthError(BitcError):
    """The WebVPN cookie is missing, expired, or rejected."""


class BitcGatewayError(BitcError):
    """The school web-VPN gateway cannot route to the timetable host."""


class BitcProtocolError(BitcError):
    """The endpoint returned HTML or malformed JSON."""


def normalize_cookie(value: str | None) -> str:
    text = (value or "").strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:") :].strip()
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s*;\s*", "; ", text).strip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    return text


async def fetch_timetable(
    cookie: str,
    *,
    academic_year: str,
    term: int,
    now: datetime,
    semester_start: date | None = None,
    teaching_weeks: int = 18,
    timeout: float = 20.0,
) -> Timetable:
    """Fetch and parse the current timetable without blocking the bot loop."""

    return await asyncio.to_thread(
        _fetch_timetable_sync,
        cookie,
        academic_year,
        term,
        now,
        semester_start,
        teaching_weeks,
        timeout,
    )


def _fetch_timetable_sync(
    cookie: str,
    academic_year: str,
    term: int,
    now: datetime,
    semester_start: date | None,
    teaching_weeks: int,
    timeout: float,
) -> Timetable:
    normalized = normalize_cookie(cookie)
    if not normalized:
        raise BitcAuthError("还没有填写 WebVPN Cookie")

    start_year = academic_year.split("-", 1)[0]
    term_code = {1: "3", 2: "12", 3: "16"}.get(term)
    if term_code is None:
        raise BitcProtocolError("学期必须是 1、2 或 3")

    common = {"xnm": start_year, "xqm": term_code}
    payload = _post_json(
        normalized,
        "/kbcx/xskbcx_cxXsgrkb.html",
        {**common, "kzlx": "ck", "xsdm": ""},
        timeout,
    )
    rows = payload.get("kbList")
    if not isinstance(rows, list):
        raise BitcProtocolError("课表接口没有返回 kbList")

    periods_by_campus: dict[str, Any] = {}
    for campus_id in _unique_campuses(rows):
        try:
            periods_by_campus[campus_id] = _post_json(
                normalized,
                "/kbcx/xskbcx_cxRjc.html?gnmkdm=N2151",
                {**common, "xqh_id": campus_id},
                timeout,
            )
        except BitcError:
            periods_by_campus[campus_id] = []

    try:
        return build_timetable(
            payload,
            periods_by_campus,
            academic_year=academic_year,
            term=term,
            now=now,
            semester_start=semester_start,
            teaching_weeks=teaching_weeks,
        )
    except TimetableParseError as error:
        raise BitcProtocolError(f"课表数据解析失败：{error}") from error


def _unique_campuses(rows: list[Any]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        campus_id = str(row.get("xqh_id") or "").strip()
        if campus_id not in seen:
            seen.append(campus_id)
    return seen or [""]


def _post_json(
    cookie: str,
    path: str,
    values: dict[str, str],
    timeout: float,
) -> Any:
    url = BASE_URL + path
    body = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Cookie": cookie,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "Referer": ENTRY_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            raw = response.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as error:
        raw = error.read(MAX_BODY_BYTES)
        status = error.code
        final_url = error.geturl()
        content_type = error.headers.get_content_type() if error.headers else ""
        if status in (401, 403):
            raise BitcAuthError(_auth_message(raw)) from error
    except urllib.error.URLError as error:
        raise BitcGatewayError(f"无法连接学校 VPN：{error.reason}") from error
    except TimeoutError as error:
        raise BitcGatewayError("连接学校 VPN 超时") from error

    text = _decode(raw)
    lowered = text.lower()
    if any(marker in text for marker in _GATEWAY_MARKERS) or re.search(
        r"\bsite\s+[a-z0-9._-]+\s+not\s+found\b", lowered
    ):
        raise BitcGatewayError(
            "学校 VPN 网关提示「权限表不同步」。请重新登录 WebVPN 后更新 Cookie，"
            "或稍后重试。"
        )
    if "iam/login" in final_url.lower() or any(
        marker in text for marker in _LOGIN_MARKERS
    ):
        raise BitcAuthError(_auth_message(raw))
    if status >= 400:
        raise BitcProtocolError(f"课表接口返回 HTTP {status}")
    if text.lstrip().startswith("<"):
        raise BitcProtocolError("课表接口返回了 HTML，可能登录态已失效")

    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise BitcProtocolError("课表接口返回的不是有效 JSON") from error


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _auth_message(raw: bytes) -> str:
    text = _decode(raw)
    if "权限表不同步" in text or "站点访问异常" in text:
        return "学校 VPN 网关权限表异常，请重新登录后更新 Cookie"
    return "学校登录态已失效，请在电脑上重新登录 WebVPN 并更新 Cookie"
