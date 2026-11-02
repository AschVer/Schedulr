"""AstrBot plugin: query two BITC timetables from QQ.

Each timetable profile keeps its own school WebVPN session (cookie) and
refreshes independently. Passwords are never read or stored here, and student
identity fields from the school response are discarded.

The Schedulr app can hand its own WebVPN session over with a one-shot bind
token; see ``schedulr_core/bind.py``.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

# AstrBot loads plugins as standalone modules; make sibling imports reliable.
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from schedulr_core import (  # noqa: E402
    BindError,
    BitcError,
    TOKEN_PREFIX,
    Timetable,
    TimetableStore,
    decode_bind_token,
    fetch_timetable,
    help_text,
    looks_like_bind_token,
    normalize_cookie,
    parse_query,
    render_answer,
    resolve_academic_year,
    resolve_term,
)

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    TZ = timezone(timedelta(hours=8))

PLUGIN_NAME = "astrbot_plugin_schedulr"
CACHE_FILE = "timetable_{index}.json"
BIND_NONCE_FILE = "bind_nonces.json"
CHINESE_INDEX = {1: "一", 2: "二", 3: "三", 4: "四"}
MAX_BOUND_NONCES = 200
BIND_COMMANDS = ("绑定", "绑定课表", "bind", "同步")
UNBIND_COMMANDS = ("解绑", "解绑课表", "unbind", "清除")


@dataclass
class ProfileSettings:
    index: int
    name: str
    cookie: str
    academic_year: str
    term: int
    semester_start: date | None


@dataclass
class AppSettings:
    profiles: list[ProfileSettings]
    teaching_weeks: int
    refresh_minutes: int
    query_refresh_minutes: int
    timeout: int
    show_footer: bool
    bind_ttl_minutes: int = 60
    bind_private_only: bool = True
    bind_allowed_senders: tuple[str, ...] = ()


@dataclass
class ProfileRuntime:
    settings: ProfileSettings
    store: TimetableStore
    timetable: Timetable | None = None
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    last_error: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SchedulrPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        try:
            self.data_dir = StarTools.get_data_dir()
        except Exception:
            self.data_dir = _PLUGIN_DIR / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        settings = self._settings()
        self.profiles: dict[int, ProfileRuntime] = {}
        for profile in settings.profiles:
            runtime = ProfileRuntime(
                settings=profile,
                store=TimetableStore(
                    self.data_dir / CACHE_FILE.format(index=profile.index)
                ),
            )
            runtime.timetable = runtime.store.load()
            runtime.last_success = (
                runtime.timetable.fetched_at if runtime.timetable else None
            )
            self.profiles[profile.index] = runtime

        self._stopping = False
        self._task: asyncio.Task[Any] | None = None
        self._start_warning_logged = False
        self._used_nonces: dict[str, float] = self._load_nonces()

    async def initialize(self) -> None:
        settings = self._apply_settings(self._settings())
        logger.info(
            "[Schedulr] 插件已加载：%s 个课表档案，%s",
            len(settings.profiles),
            "、".join(
                f"{profile.name}({'有缓存' if self.profiles[profile.index].timetable else '无缓存'}"
                f"{'，Cookie已配置' if profile.cookie else '，Cookie未配置'})"
                for profile in settings.profiles
            ),
        )
        if not any(profile.cookie for profile in settings.profiles):
            logger.warning(
                "[Schedulr] 还没有配置任何 WebVPN Cookie，机器人暂时只能读缓存。"
            )
        self._task = asyncio.create_task(self._auto_refresh_loop())

    async def terminate(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    # ------------------------------------------------------------------ commands

    @filter.command("课表", alias={"课程表", "timetable", "schedule"})
    async def timetable_command(self, event: AstrMessageEvent):
        """查询课表。例：/课表 明天、/课表 一班 周三、/课表 2 本周。"""
        raw = event.message_str
        selector, remainder = self._split_profile(raw)
        query = parse_query(remainder)
        now = datetime.now(TZ)
        settings = self._apply_settings(self._settings())

        action, argument = self._split_bind_action(remainder)
        if action is not None:
            yield event.plain_result(
                await self._handle_bind_action(
                    event, settings, action, argument, selector
                )
            )
            return

        if query.kind == "help" and selector is None:
            yield event.plain_result(help_text())
            return

        targets = (
            [self.profiles[selector]]
            if selector is not None
            else list(self.profiles.values())
        )

        if query.kind == "refresh":
            lines = []
            for profile in targets:
                lines.append(
                    f"【{profile.settings.name}】{await self._manual_refresh(profile, settings)}"
                )
            yield event.plain_result("\n\n".join(lines))
            return

        if query.kind == "status":
            yield event.plain_result(self._status_text(targets, settings, now))
            return

        blocks: list[str] = []
        for profile in targets:
            await self._ensure_fresh(profile, settings)
            if profile.timetable is None:
                blocks.append(
                    f"【{profile.settings.name}】\n{self._empty_text(profile, settings)}"
                )
                continue
            text = render_answer(
                profile.timetable,
                query,
                now,
                show_footer=settings.show_footer,
            )
            if selector is None and len(targets) > 1:
                text = f"【{profile.settings.name}】\n{text}"
            blocks.append(text)
        yield event.plain_result("\n\n".join(blocks))

    @filter.command("课表帮助")
    async def timetable_help(self, event: AstrMessageEvent):
        """查看课表插件的用法。"""
        yield event.plain_result(help_text())

    # -------------------------------------------------------------------- bind

    async def _handle_bind_action(
        self,
        event: AstrMessageEvent,
        settings: AppSettings,
        action: str,
        argument: str,
        selector: int | None,
    ) -> str:
        denied = self._bind_permission_error(event, settings)
        if denied is not None:
            return denied

        argument = (argument or "").strip()
        if action == "unbind":
            target = selector or self._profile_from_alias(argument)
            return self._unbind(target)

        if not argument:
            return (
                "在 App 里打开「设置 → QQ 机器人 → 同步登录状态」，\n"
                "生成口令后整条发给我，例如：\n"
                "/课表 绑定 SCHEDULR1.……"
            )
        if not looks_like_bind_token(argument):
            return "这不像 Schedulr 绑定口令（应以 SCHEDULR1. 开头）。"

        try:
            payload = decode_bind_token(
                argument,
                now=datetime.now(TZ),
                max_age_seconds=settings.bind_ttl_minutes * 60,
            )
        except BindError as error:
            return f"❌ 绑定失败：{error}"

        profile = self.profiles.get(payload.profile)
        if profile is None:
            return f"❌ 绑定失败：没有第 {payload.profile} 个课表档案。"

        if payload.nonce in self._used_nonces:
            return "❌ 这个口令已经用过了，请在 App 里重新生成一个。"

        previous = profile.settings.cookie
        profile.settings.cookie = payload.cookie
        profile.last_attempt = None
        ok = await self._refresh(profile, settings, force=True)
        if not ok:
            profile.settings.cookie = previous
            return (
                f"❌ 这份登录状态没通过验证：{profile.last_error or '未知错误'}\n"
                "在 App 里重新登录教务后，再生成一次口令。"
            )

        # _refresh only writes the config when the cookie was renewed, so a
        # first-time bind with an unchanged value still needs an explicit save.
        self._persist_cookie(profile.settings.index, profile.settings.cookie)
        self._remember_nonce(payload.nonce)

        timetable = profile.timetable
        week = timetable.week_for(datetime.now(TZ).date()) if timetable else None
        lines = [f"✅ 已绑定到【{profile.settings.name}】"]
        if timetable is not None:
            lines.append(
                f"课程：{len(timetable.courses)} 节"
                + (f"｜当前第 {week} 周" if week and week > 0 else "")
            )
            lines.append(f"数据时间：{timetable.fetched_at:%m-%d %H:%M}")
        if previous and previous != profile.settings.cookie:
            lines.append("（已覆盖该档案原来的登录状态）")
        lines.append("之后 App 里重新登录后，再发一次新口令即可刷新。")
        return "\n".join(lines)

    def _unbind(self, index: int | None) -> str:
        targets = (
            [self.profiles[index]]
            if index is not None and index in self.profiles
            else list(self.profiles.values())
        )
        cleared: list[str] = []
        for profile in targets:
            if not profile.settings.cookie:
                continue
            profile.settings.cookie = ""
            profile.last_error = None
            self._persist_cookie(profile.settings.index, "")
            cleared.append(profile.settings.name)
        if not cleared:
            return "这些档案本来就没有配置 Cookie。"
        return "🧹 已清除 " + "、".join(cleared) + " 的 Cookie。\n已缓存的课表仍可查询。"

    def _bind_permission_error(
        self, event: AstrMessageEvent, settings: AppSettings
    ) -> str | None:
        if settings.bind_private_only and not self._is_private(event):
            return "🔒 绑定口令只能在私聊里发送，避免学校登录态出现在群聊里。"
        allowed = settings.bind_allowed_senders
        if allowed:
            sender = ""
            try:
                sender = str(event.get_sender_id() or "")
            except Exception:  # pragma: no cover - defensive
                sender = ""
            if sender not in allowed:
                return "🔒 你没有绑定课表的权限。"
        return None

    def _is_private(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_private_chat())
        except Exception:  # pragma: no cover - older AstrBot
            return False

    def _profile_from_alias(self, text: str) -> int | None:
        value = (text or "").strip()
        if not value:
            return None
        for profile in self._settings().profiles:
            aliases = {
                profile.name,
                str(profile.index),
                f"{profile.index}班",
                f"{CHINESE_INDEX.get(profile.index, '')}班",
            }
            if value in aliases:
                return profile.index
        return None

    def _split_bind_action(self, remainder: str) -> tuple[str | None, str]:
        text = (remainder or "").strip()
        for keyword in sorted(UNBIND_COMMANDS, key=len, reverse=True):
            if text == keyword:
                return "unbind", ""
            if text.startswith(keyword):
                return "unbind", text[len(keyword) :].strip()
        for keyword in sorted(BIND_COMMANDS, key=len, reverse=True):
            if text == keyword:
                return "bind", ""
            if text.startswith(keyword):
                return "bind", text[len(keyword) :].strip()
        return None, text

    def _nonce_path(self) -> Path:
        return self.data_dir / BIND_NONCE_FILE

    def _load_nonces(self) -> dict[str, float]:
        try:
            raw = self._nonce_path().read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        now = datetime.now(TZ).timestamp()
        return {
            str(key): float(value)
            for key, value in data.items()
            if isinstance(value, (int, float)) and now - float(value) < 172800
        }

    def _remember_nonce(self, nonce: str) -> None:
        self._used_nonces[nonce] = datetime.now(TZ).timestamp()
        if len(self._used_nonces) > MAX_BOUND_NONCES:
            keep = sorted(
                self._used_nonces.items(), key=lambda item: item[1], reverse=True
            )[:MAX_BOUND_NONCES]
            self._used_nonces = dict(keep)
        try:
            self._nonce_path().write_text(
                json.dumps(self._used_nonces, indent=0), encoding="utf-8"
            )
        except OSError:
            logger.warning("[Schedulr] 无法写入绑定口令防重放文件")

    # ------------------------------------------------------------------ refresh

    async def _manual_refresh(
        self, profile: ProfileRuntime, settings: AppSettings
    ) -> str:
        if not profile.settings.cookie:
            return (
                "还没有配置这个档案的 WebVPN Cookie，"
                "请在插件配置里填写后再试。"
            )
        now = datetime.now(TZ)
        last = profile.last_attempt
        if last is not None and (now - last).total_seconds() < 30:
            if profile.last_error:
                return f"刚尝试过刷新，失败了：{profile.last_error}"
            return "刚刷新过，请稍后再试。"
        ok = await self._refresh(profile, settings, force=True)
        if not ok:
            return f"刷新失败：{profile.last_error or '未知错误'}"
        assert profile.timetable is not None
        week = profile.timetable.week_for(now.date())
        return (
            f"✅ 已更新：{len(profile.timetable.courses)} 节课程"
            + (f"，当前第 {week} 周" if week and week > 0 else "")
            + f"\n数据时间：{profile.timetable.fetched_at:%m-%d %H:%M}"
        )

    async def _ensure_fresh(
        self, profile: ProfileRuntime, settings: AppSettings
    ) -> None:
        if profile.timetable is None:
            if profile.settings.cookie:
                await self._refresh(profile, settings, force=True)
            return
        if not profile.settings.cookie or profile.last_success is None:
            return
        age_minutes = (
            datetime.now(TZ) - profile.last_success
        ).total_seconds() / 60
        if age_minutes < settings.query_refresh_minutes:
            return
        try:
            await asyncio.wait_for(
                self._refresh(profile, settings, force=True),
                timeout=settings.timeout + 5,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[Schedulr] %s 按需刷新超时，继续使用缓存",
                profile.settings.name,
            )
        except Exception:
            logger.exception("[Schedulr] %s 按需刷新失败", profile.settings.name)

    async def _refresh(
        self,
        profile: ProfileRuntime,
        settings: AppSettings,
        *,
        force: bool,
    ) -> bool:
        if profile.lock.locked():
            return False
        async with profile.lock:
            now = datetime.now(TZ)
            if not profile.settings.cookie:
                profile.last_error = "还没有配置 WebVPN Cookie"
                return False
            if (
                not force
                and profile.last_attempt is not None
                and (now - profile.last_attempt).total_seconds() < 45
            ):
                return False
            profile.last_attempt = now
            try:
                academic_year = resolve_academic_year(
                    profile.settings.academic_year, now
                )
                term = resolve_term(profile.settings.term, now)
                result = await fetch_timetable(
                    profile.settings.cookie,
                    academic_year=academic_year,
                    term=term,
                    now=now,
                    semester_start=profile.settings.semester_start,
                    teaching_weeks=settings.teaching_weeks,
                    timeout=settings.timeout,
                )
                timetable = result.timetable
                if result.cookie and result.cookie != profile.settings.cookie:
                    profile.settings.cookie = result.cookie
                    self._persist_cookie(profile.settings.index, result.cookie)
                    logger.info(
                        "[Schedulr] %s 的 WebVPN Cookie 已自动续期",
                        profile.settings.name,
                    )
                profile.timetable = timetable
                profile.last_success = now
                profile.last_error = None
                try:
                    profile.store.save(timetable)
                except OSError:
                    logger.exception(
                        "[Schedulr] 无法写入 %s 的课表缓存",
                        profile.settings.name,
                    )
                logger.info(
                    "[Schedulr] %s 课表已更新：%s 节，开学 %s",
                    profile.settings.name,
                    len(timetable.courses),
                    timetable.start_date or "未知",
                )
                return True
            except BitcError as error:
                profile.last_error = str(error)
                logger.warning(
                    "[Schedulr] %s 课表更新失败：%s",
                    profile.settings.name,
                    error,
                )
                return False
            except Exception:
                profile.last_error = "刷新课表时发生未知错误"
                logger.exception(
                    "[Schedulr] %s 课表更新异常", profile.settings.name
                )
                return False

    async def _auto_refresh_loop(self) -> None:
        try:
            await asyncio.sleep(3)
            while not self._stopping:
                settings = self._apply_settings(self._settings())
                for profile_setting in settings.profiles:
                    profile = self.profiles[profile_setting.index]
                    if not profile_setting.cookie:
                        continue
                    stale = (
                        profile.timetable is None
                        or profile.last_success is None
                        or (
                            datetime.now(TZ) - profile.last_success
                        ).total_seconds()
                        >= settings.refresh_minutes * 60
                    )
                    if stale:
                        await self._refresh(profile, settings, force=False)
                if not any(
                    profile_setting.cookie
                    for profile_setting in settings.profiles
                ) and not self._start_warning_logged:
                    logger.info("[Schedulr] 未配置 Cookie，跳过自动刷新")
                    self._start_warning_logged = True
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - keep the bot alive
            logger.exception("[Schedulr] 自动刷新循环异常退出")

    # ------------------------------------------------------------------ helpers

    def _status_text(
        self,
        targets: list[ProfileRuntime],
        settings: AppSettings,
        now: datetime,
    ) -> str:
        lines = ["📊 课表状态"]
        for profile in targets:
            lines.append("")
            lines.append(f"【{profile.settings.name}】")
            if profile.timetable is None:
                lines.append("  还没有课表数据")
            else:
                week = profile.timetable.week_for(now.date())
                lines.append(f"  课程：{len(profile.timetable.courses)} 节")
                lines.append(
                    f"  当前：{f'第 {week} 周' if week and week > 0 else '不在教学周'}"
                )
                lines.append(
                    f"  数据：{profile.timetable.fetched_at:%m-%d %H:%M}"
                )
            lines.append(
                f"  Cookie：{'已配置' if profile.settings.cookie else '未配置'}"
            )
            if profile.last_error:
                lines.append(f"  最近错误：{profile.last_error}")
        lines.append("")
        lines.append(f"自动刷新：每 {settings.refresh_minutes} 分钟")
        return "\n".join(lines)

    def _empty_text(self, profile: ProfileRuntime, settings: AppSettings) -> str:
        if profile.settings.cookie:
            return (
                "还没有取到课表，可能是学校登录态失效或网络异常。\n"
                f"原因：{profile.last_error or '未知'}\n"
                "发送对应档案的刷新指令重试。"
            )
        return (
            "还没有课表数据。\n"
            "在 AstrBot 的插件配置里填入这个档案的 WebVPN Cookie 后，"
            "发送刷新指令即可。"
        )

    def _split_profile(self, raw: str) -> tuple[int | None, str]:
        """Extract an optional leading profile selector from the command."""

        text = (raw or "").strip()
        text = re.sub(r"^@\S+\s*", "", text)
        text = re.sub(
            r"^[/／]?(课表|课程表|timetable|schedule)\b",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        profiles = self._settings().profiles
        for profile in profiles:
            aliases = {
                profile.name,
                str(profile.index),
                f"{profile.index}班",
                f"{CHINESE_INDEX.get(profile.index, '')}班",
                CHINESE_INDEX.get(profile.index, ""),
            }
            for alias in aliases:
                if not alias:
                    continue
                if text == alias:
                    return profile.index, ""
                if text.startswith(alias + " "):
                    return profile.index, text[len(alias) :].strip()
        return None, text

    def _persist_cookie(self, index: int, cookie: str) -> None:
        try:
            self.config[f"profile_{index}_cookie"] = cookie
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception:
            logger.warning(
                "[Schedulr] profile_%s 的续期 Cookie 未能写回配置", index
            )

    def _apply_settings(self, settings: AppSettings) -> AppSettings:
        for profile in settings.profiles:
            runtime = self.profiles.get(profile.index)
            if runtime is not None:
                runtime.settings = profile
        return settings

    def _settings(self) -> AppSettings:
        def as_int(key: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(self.config.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        def profile(index: int) -> ProfileSettings:
            prefix = f"profile_{index}_"
            raw_start = str(
                self.config.get(prefix + "semester_start", "") or ""
            ).strip()
            semester_start: date | None = None
            if raw_start:
                try:
                    semester_start = date.fromisoformat(raw_start)
                except ValueError:
                    logger.warning(
                        "[Schedulr] %s开学日期不是 YYYY-MM-DD，已忽略：%s",
                        prefix,
                        raw_start,
                    )
            return ProfileSettings(
                index=index,
                name=str(
                    self.config.get(prefix + "name", "") or f"{index}班"
                ).strip(),
                cookie=normalize_cookie(
                    str(self.config.get(prefix + "cookie", "") or "")
                ),
                academic_year=str(
                    self.config.get(prefix + "academic_year", "") or ""
                ).strip(),
                term=as_int(prefix + "term", 0, 0, 3),
                semester_start=semester_start,
            )

        def as_bool(key: str, default: bool) -> bool:
            value = self.config.get(key, default)
            if isinstance(value, str):
                return value.strip().lower() not in ("", "0", "false", "no")
            return bool(value)

        raw_allowed = self.config.get("bind_allowed_senders", "") or ""
        if isinstance(raw_allowed, (list, tuple)):
            allowed = tuple(
                str(item).strip() for item in raw_allowed if str(item).strip()
            )
        else:
            allowed = tuple(
                part
                for part in re.split(r"[\s,，;；]+", str(raw_allowed))
                if part
            )

        return AppSettings(
            profiles=[profile(1), profile(2)],
            teaching_weeks=as_int("teaching_weeks", 18, 1, 30),
            refresh_minutes=as_int("refresh_minutes", 10, 5, 180),
            query_refresh_minutes=as_int(
                "query_refresh_minutes", 30, 5, 180
            ),
            timeout=as_int("timeout", 20, 5, 60),
            show_footer=as_bool("show_footer", True),
            bind_ttl_minutes=as_int("bind_ttl_minutes", 60, 1, 1440),
            bind_private_only=as_bool("bind_private_only", True),
            bind_allowed_senders=allowed,
        )
