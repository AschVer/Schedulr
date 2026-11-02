"""One-shot bind tokens exchanged between the Schedulr app and the QQ bot.

The app cannot talk to the bot's WebUI, so it hands the school session over in a
copy-paste friendly token: the user copies the token in the app and sends it to
the bot in a private chat. The bot decodes it, proves the cookie really works by
fetching the timetable once, and only then writes it into the plugin config.

The token is transport encoding, not encryption. Anyone holding it holds the
school session for as long as that session lives, so tokens are short-lived and
single-use.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

from .bitc_client import normalize_cookie

TOKEN_PREFIX = "SCHEDULR1."
TOKEN_VERSION = 1

MIN_PROFILE = 1
MAX_PROFILE = 2

MAX_TOKEN_LENGTH = 16 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_COOKIE_LENGTH = 4096
MAX_NONCE_LENGTH = 64
# Tokens are pasted by hand, so a small clock skew is normal; a large future
# timestamp is not, and would defeat the freshness check.
MAX_CLOCK_SKEW_SECONDS = 300

_TOKEN_BODY = re.compile(r"^[A-Za-z0-9_-]+$")
_COOKIE_OCTETS = re.compile(r"^[\x21-\x7e](?:[\x20-\x7e]*[\x21-\x7e])?$")


class BindError(ValueError):
    """The token is malformed, stale, or cannot be applied."""


@dataclass(frozen=True)
class BindPayload:
    """A decoded bind token."""

    profile: int
    cookie: str
    issued_at: datetime
    nonce: str

    @property
    def issued_at_text(self) -> str:
        return self.issued_at.strftime("%m-%d %H:%M")


def looks_like_bind_token(text: str) -> bool:
    return (text or "").strip().upper().startswith(TOKEN_PREFIX)


def encode_bind_token(payload: BindPayload) -> str:
    """Encode a payload. Used by tests and by ``tools/make_token.py``."""

    _validate_payload_fields(payload)
    document = {
        "v": TOKEN_VERSION,
        "p": payload.profile,
        "c": payload.cookie,
        "u": int(payload.issued_at.timestamp()),
        "n": payload.nonce,
    }
    raw = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    packed = zlib.compress(raw, 9)
    body = base64.urlsafe_b64encode(packed).rstrip(b"=").decode("ascii")
    token = TOKEN_PREFIX + body
    if len(token) > MAX_TOKEN_LENGTH:
        raise BindError("绑定口令太长")
    return token


def decode_bind_token(
    token: str,
    *,
    now: datetime,
    max_age_seconds: int,
) -> BindPayload:
    """Decode and freshness-check a token pasted into the chat."""

    text = _clean(token)
    if len(text) > MAX_TOKEN_LENGTH:
        raise BindError("绑定口令太长，可能粘贴不完整或混入了其它内容")
    if not text.upper().startswith(TOKEN_PREFIX):
        raise BindError("这不是 Schedulr 绑定口令")
    body = text[len(TOKEN_PREFIX) :].rstrip("=")
    if not _TOKEN_BODY.match(body):
        raise BindError("绑定口令含有无法识别的字符")

    raw = _unpack(body)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BindError("绑定口令内容已损坏") from error
    if not isinstance(document, dict):
        raise BindError("绑定口令内容已损坏")

    version = document.get("v")
    if version != TOKEN_VERSION:
        raise BindError("绑定口令版本不受支持，请更新 App 或机器人插件")

    profile = document.get("p")
    if not isinstance(profile, int) or isinstance(profile, bool):
        raise BindError("绑定口令缺少课表编号")
    if not MIN_PROFILE <= profile <= MAX_PROFILE:
        raise BindError(f"课表编号只能是 {MIN_PROFILE}–{MAX_PROFILE}")

    cookie = document.get("c")
    if not isinstance(cookie, str):
        raise BindError("绑定口令里没有 Cookie")
    normalized = normalize_cookie(cookie)
    if not normalized:
        raise BindError("绑定口令里的 Cookie 是空的")
    if len(normalized) > MAX_COOKIE_LENGTH:
        raise BindError("Cookie 太长")
    if not _COOKIE_OCTETS.match(normalized):
        raise BindError("Cookie 含有无法识别的字符")

    nonce = document.get("n")
    if (
        not isinstance(nonce, str)
        or not nonce
        or len(nonce) > MAX_NONCE_LENGTH
        or not _TOKEN_BODY.match(nonce)
    ):
        raise BindError("绑定口令缺少防重放标识")

    issued = document.get("u")
    if not isinstance(issued, int) or isinstance(issued, bool):
        raise BindError("绑定口令缺少生成时间")
    try:
        issued_at = datetime.fromtimestamp(issued, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise BindError("绑定口令的生成时间无效") from error

    age = (now.astimezone(timezone.utc) - issued_at).total_seconds()
    if age > max_age_seconds:
        minutes = max(1, int(max_age_seconds // 60))
        raise BindError(
            f"绑定口令已过期（超过 {minutes} 分钟），请在 App 里重新生成"
        )
    if age < -MAX_CLOCK_SKEW_SECONDS:
        raise BindError("绑定口令的生成时间在未来，请检查手机系统时间")

    return BindPayload(
        profile=profile,
        cookie=normalized,
        issued_at=issued_at,
        nonce=nonce,
    )


def _clean(token: str) -> str:
    # Chat clients sometimes wrap long lines or append invisible characters.
    return re.sub(r"[\s\u200b-\u200f\u2060\ufeff]+", "", token or "")


def _unpack(body: str) -> bytes:
    padding = "=" * (-len(body) % 4)
    try:
        packed = base64.urlsafe_b64decode(body + padding)
    except (binascii.Error, ValueError) as error:
        raise BindError("绑定口令格式无效") from error
    try:
        raw = zlib.decompress(packed, zlib.MAX_WBITS | 32)
    except zlib.error as error:
        raise BindError("绑定口令内容已损坏") from error
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise BindError("绑定口令内容过大")
    return raw


def _validate_payload_fields(payload: BindPayload) -> None:
    if not MIN_PROFILE <= payload.profile <= MAX_PROFILE:
        raise BindError(f"课表编号只能是 {MIN_PROFILE}–{MAX_PROFILE}")
    if not payload.cookie or len(payload.cookie) > MAX_COOKIE_LENGTH:
        raise BindError("Cookie 长度不合法")
    if not payload.nonce or not _TOKEN_BODY.match(payload.nonce):
        raise BindError("防重放标识不合法")
