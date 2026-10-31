"""Minimal BITC WebVPN client used by the QQ bot.

The app normally talks to the school through a WebView. Headless, the only
thing we need is the WebVPN cookie; the timetable endpoint itself is a normal
form POST returning JSON. No account password is read or stored here.

The WebVPN refreshes ``_webvpn_key`` on every portal request (sliding 24-hour
session). The client keeps a small cookie jar and returns the refreshed cookie
so the plugin can persist it and stay logged in without a browser.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .models import Timetable
from .parser import TimetableParseError, build_timetable

BASE_URL = "https://jwxt.vpn.bitc.edu.cn"
PORTAL_URL = "https://vpn.bitc.edu.cn/"
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


@dataclass(frozen=True)
class FetchResult:
    timetable: Timetable
    cookie: str


class CookieJar:
    """Tiny Cookie header helper that also absorbs ``Set-Cookie`` responses."""

    def __init__(self, header: str):
        self._cookies: dict[str, str] = {}
        for part in header.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if name:
                self._cookies[name] = value.strip()

    def header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self._cookies.items())

    def update_from_headers(self, headers: list[str] | None) -> bool:
        changed = False
        for raw in headers or []:
            pair = raw.split(";", 1)[0]
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            if "max-age=0" in raw.lower() or value == "":
                if name in self._cookies:
                    del self._cookies[name]
                    changed = True
                continue
            if self._cookies.get(name) != value:
                self._cookies[name] = value
                changed = True
        return changed


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
) -> FetchResult:
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
) -> FetchResult:
    normalized = normalize_cookie(cookie)
    if not normalized:
        raise BitcAuthError("还没有填写 WebVPN Cookie")

    start_year = academic_year.split("-", 1)[0]
    term_code = {1: "3", 2: "12", 3: "16"}.get(term)
    if term_code is None:
        raise BitcProtocolError("学期必须是 1、2 或 3")

    jar = CookieJar(normalized)
    # Touching the portal slides the 24-hour WebVPN session and returns fresh
    # _webvpn_key / _astraeus_session cookies.
    _refresh_portal(jar, timeout)

    common = {"xnm": start_year, "xqm": term_code}
    payload = _post_json(
        jar,
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
                jar,
                "/kbcx/xskbcx_cxRjc.html?gnmkdm=N2151",
                {**common, "xqh_id": campus_id},
                timeout,
            )
        except BitcError:
            periods_by_campus[campus_id] = []

    try:
        timetable = build_timetable(
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
    return FetchResult(timetable=timetable, cookie=jar.header())


def _refresh_portal(jar: CookieJar, timeout: float) -> None:
    status, final_url, raw, set_cookies = _request(
        jar,
        PORTAL_URL,
        None,
        {"Accept-Encoding": "identity"},
        timeout,
    )
    jar.update_from_headers(set_cookies)
    if "/login" in final_url.lower():
        raise BitcAuthError(_auth_message(raw))
    if status in (401, 403):
        raise BitcAuthError(_auth_message(raw))
    if status >= 400:
        raise BitcGatewayError(f"学校 VPN 门户返回 HTTP {status}")


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
    jar: CookieJar,
    path: str,
    values: dict[str, str],
    timeout: float,
) -> Any:
    url = BASE_URL + path
    body = urllib.parse.urlencode(values).encode("utf-8")
    status, final_url, raw, set_cookies = _request(
        jar,
        url,
        body,
        {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": ENTRY_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout,
    )
    jar.update_from_headers(set_cookies)

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
    if status in (401, 403):
        raise BitcAuthError(_auth_message(raw))
    if status >= 400:
        raise BitcProtocolError(f"课表接口返回 HTTP {status}")
    if text.lstrip().startswith("<"):
        raise BitcProtocolError("课表接口返回了 HTML，可能登录态已失效")

    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise BitcProtocolError("课表接口返回的不是有效 JSON") from error


def _request(
    jar: CookieJar,
    url: str,
    data: bytes | None,
    extra_headers: dict[str, str],
    timeout: float,
) -> tuple[int, str, bytes, list[str]]:
    headers = {
        "Accept-Encoding": "identity",
        "User-Agent": USER_AGENT,
        "Cookie": jar.header(),
        **extra_headers,
    }
    request = urllib.request.Request(
        url, data=data, method="POST" if data is not None else "GET", headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BODY_BYTES)
            return (
                response.status,
                response.geturl(),
                raw,
                response.headers.get_all("Set-Cookie") or [],
            )
    except urllib.error.HTTPError as error:
        raw = error.read(MAX_BODY_BYTES)
        return (
            error.code,
            getattr(error, "url", url),
            raw,
            error.headers.get_all("Set-Cookie") if error.headers else [],
        )
    except urllib.error.URLError as error:
        raise BitcGatewayError(f"无法连接学校 VPN：{error.reason}") from error
    except TimeoutError as error:
        raise BitcGatewayError("连接学校 VPN 超时") from error


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
