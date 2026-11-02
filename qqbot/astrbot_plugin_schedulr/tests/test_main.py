from __future__ import annotations

import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

# Minimal AstrBot stubs so the plugin module can be imported outside AstrBot.
astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
event_api = types.ModuleType("astrbot.api.event")
star_api = types.ModuleType("astrbot.api.star")


class AstrBotConfig(dict):
    pass


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class _Filter:
    def command(self, *args, **kwargs):
        def decorator(function):
            return function

        return decorator

    def regex(self, *args, **kwargs):
        def decorator(function):
            return function

        return decorator


class AstrMessageEvent:
    pass


class Star:
    def __init__(self, context):
        self.context = context


class Context:
    pass


class StarTools:
    _data_dir = Path(tempfile.mkdtemp(prefix="schedulr-plugin-test-"))

    @classmethod
    def get_data_dir(cls, plugin_name=None):
        return cls._data_dir


api.AstrBotConfig = AstrBotConfig
api.logger = _Logger()
event_api.AstrMessageEvent = AstrMessageEvent
event_api.filter = _Filter()
star_api.Context = Context
star_api.Star = Star
star_api.StarTools = StarTools
astrbot.api = api
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", api)
sys.modules.setdefault("astrbot.api.event", event_api)
sys.modules.setdefault("astrbot.api.star", star_api)

import main  # noqa: E402

from schedulr_core import BindPayload, build_timetable, encode_bind_token  # noqa: E402

from datetime import datetime, timedelta, timezone  # noqa: E402

TZ = timezone(timedelta(hours=8))
NOW = datetime.now(TZ)
COOKIE = "JSESSIONID=FRESH; _webvpn_key=fresh-key"


class FakeEvent:
    def __init__(
        self,
        text: str,
        *,
        sender: str = "10001",
        private: bool = True,
    ):
        self.message_str = text
        self._sender = sender
        self._private = private

    def plain_result(self, text: str):
        return ("plain", text)

    def get_sender_id(self) -> str:
        return self._sender

    def is_private_chat(self) -> bool:
        return self._private


class MainTest(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, **config):
        return main.SchedulrPlugin(
            None,
            AstrBotConfig(
                {
                    "profile_1_name": "一班",
                    "profile_1_cookie": "",
                    "profile_1_academic_year": "",
                    "profile_1_term": 0,
                    "profile_1_semester_start": "",
                    "profile_2_name": "二班",
                    "profile_2_cookie": "",
                    "profile_2_academic_year": "",
                    "profile_2_term": 0,
                    "profile_2_semester_start": "",
                    "teaching_weeks": 18,
                    "refresh_minutes": 10,
                    "query_refresh_minutes": 30,
                    "timeout": 20,
                    "show_footer": True,
                    **config,
                }
            ),
        )

    async def collect(self, plugin, text):
        return [item async for item in plugin.timetable_command(FakeEvent(text))]

    async def test_empty_answer_without_cookie(self):
        plugin = self.make_plugin()
        results = await self.collect(plugin, "/课表 今天")
        self.assertEqual(len(results), 1)
        text = results[0][1]
        self.assertIn("【一班】", text)
        self.assertIn("【二班】", text)
        self.assertIn("还没有课表数据", text)

    async def test_status_and_refresh_without_cookie(self):
        plugin = self.make_plugin()
        status = (await self.collect(plugin, "/课表 状态"))[0][1]
        self.assertIn("【一班】", status)
        self.assertIn("Cookie：未配置", status)

        refresh = (await self.collect(plugin, "/课表 刷新"))[0][1]
        self.assertIn("还没有配置这个档案的 WebVPN Cookie", refresh)

    async def test_profile_selector(self):
        plugin = self.make_plugin()
        self.assertEqual(plugin._split_profile("/课表 一班 明天"), (1, "明天"))
        self.assertEqual(plugin._split_profile("/课表 2 本周"), (2, "本周"))
        self.assertEqual(plugin._split_profile("/课表 二班"), (2, ""))
        self.assertEqual(plugin._split_profile("/课表 明天"), (None, "明天"))
        self.assertEqual(plugin._split_profile("/课表 第10周"), (None, "第10周"))

    async def test_settings_normalizes(self):
        plugin = self.make_plugin(
            profile_1_term=2,
            profile_1_academic_year="2026-2027",
            profile_1_semester_start="2026-09-07",
            refresh_minutes=1,
            teaching_weeks=99,
        )
        settings = plugin._settings()
        self.assertEqual(len(settings.profiles), 2)
        first = settings.profiles[0]
        self.assertEqual(first.name, "一班")
        self.assertEqual(first.term, 2)
        self.assertEqual(first.academic_year, "2026-2027")
        self.assertEqual(settings.refresh_minutes, 5)
        self.assertEqual(settings.teaching_weeks, 30)
        self.assertEqual(first.semester_start.isoformat(), "2026-09-07")


def _empty_timetable():
    return build_timetable(
        {
            "kbList": [
                {
                    "jxb_id": "class-1",
                    "kch": "MATH-101",
                    "kcmc": "Math",
                    "xm": "Teacher",
                    "xqj": "1",
                    "jcs": "1-2",
                    "jcor": "1-2",
                    "zcd": "1-16周",
                    "xqmc": "North",
                    "cdmc": "Room 101",
                    "xqh_id": "01",
                }
            ]
        },
        {},
        academic_year="2026-2027",
        term=1,
        now=NOW,
        teaching_weeks=18,
    )


class BindCommandTest(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, **config):
        return MainTest().make_plugin(**config)

    def token(
        self,
        *,
        profile: int = 1,
        cookie: str = COOKIE,
        issued_at: datetime | None = None,
        nonce: str | None = None,
    ) -> str:
        return encode_bind_token(
            BindPayload(
                profile=profile,
                cookie=cookie,
                issued_at=issued_at or NOW,
                nonce=nonce or uuid.uuid4().hex[:16],
            )
        )

    def stub_refresh(self, plugin, *, ok: bool = True) -> list[str]:
        calls: list[str] = []

        async def fake_refresh(profile, settings, *, force):
            calls.append(profile.settings.cookie)
            if not ok:
                profile.last_error = "学校登录态已失效"
                return False
            profile.timetable = _empty_timetable()
            profile.last_success = NOW
            profile.last_error = None
            return True

        plugin._refresh = fake_refresh
        return calls

    async def collect(self, plugin, event):
        return [item async for item in plugin.timetable_command(event)]

    async def test_bind_writes_config_and_uses_cookie(self):
        plugin = self.make_plugin()
        calls = self.stub_refresh(plugin)
        text = (
            await self.collect(plugin, FakeEvent("/课表 绑定 " + self.token()))
        )[0][1]
        self.assertIn("已绑定到【一班】", text)
        self.assertEqual(calls, [COOKIE])
        self.assertEqual(plugin.config["profile_1_cookie"], COOKIE)
        self.assertEqual(plugin.profiles[1].settings.cookie, COOKIE)

    async def test_bind_second_profile(self):
        plugin = self.make_plugin()
        self.stub_refresh(plugin)
        text = (
            await self.collect(
                plugin, FakeEvent("/课表 绑定 " + self.token(profile=2))
            )
        )[0][1]
        self.assertIn("已绑定到【二班】", text)
        self.assertEqual(plugin.config["profile_2_cookie"], COOKIE)
        self.assertEqual(plugin.config["profile_1_cookie"], "")

    async def test_replayed_token_is_rejected(self):
        plugin = self.make_plugin()
        self.stub_refresh(plugin)
        token = self.token(nonce="nonce-replay")
        await self.collect(plugin, FakeEvent("/课表 绑定 " + token))
        again = (
            await self.collect(plugin, FakeEvent("/课表 绑定 " + token))
        )[0][1]
        self.assertIn("已经用过", again)

    async def test_expired_token_is_rejected(self):
        plugin = self.make_plugin()
        self.stub_refresh(plugin)
        stale = self.token(
            issued_at=NOW - timedelta(minutes=120), nonce="nonce-stale"
        )
        text = (await self.collect(plugin, FakeEvent("/课表 绑定 " + stale)))[0][1]
        self.assertIn("绑定失败", text)
        self.assertIn("过期", text)
        self.assertEqual(plugin.config["profile_1_cookie"], "")

    async def test_rejected_by_school_keeps_old_cookie(self):
        plugin = self.make_plugin(profile_1_cookie="OLD=1")
        self.stub_refresh(plugin, ok=False)
        text = (await self.collect(plugin, FakeEvent("/课表 绑定 " + self.token())))[
            0
        ][1]
        self.assertIn("没通过验证", text)
        self.assertEqual(plugin.config["profile_1_cookie"], "OLD=1")
        self.assertEqual(plugin.profiles[1].settings.cookie, "OLD=1")

    async def test_group_chat_is_denied(self):
        plugin = self.make_plugin()
        self.stub_refresh(plugin)
        text = (
            await self.collect(
                plugin,
                FakeEvent("/课表 绑定 " + self.token(), private=False),
            )
        )[0][1]
        self.assertIn("私聊", text)
        self.assertEqual(plugin.config["profile_1_cookie"], "")

    async def test_allowlist_is_enforced(self):
        plugin = self.make_plugin(bind_allowed_senders="20002")
        self.stub_refresh(plugin)
        text = (
            await self.collect(
                plugin,
                FakeEvent("/课表 绑定 " + self.token(), sender="10001"),
            )
        )[0][1]
        self.assertIn("没有绑定课表的权限", text)

    async def test_unbind_clears_cookie(self):
        plugin = self.make_plugin(profile_1_cookie="OLD=1")
        text = (await self.collect(plugin, FakeEvent("/课表 解绑 一班")))[0][1]
        self.assertIn("已清除", text)
        self.assertEqual(plugin.config["profile_1_cookie"], "")

    async def test_bind_without_token_shows_instructions(self):
        plugin = self.make_plugin()
        text = (await self.collect(plugin, FakeEvent("/课表 绑定")))[0][1]
        self.assertIn("SCHEDULR1", text)


if __name__ == "__main__":
    unittest.main()
