from __future__ import annotations

import sys
import tempfile
import types
import unittest
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


class FakeEvent:
    def __init__(self, text: str):
        self.message_str = text

    def plain_result(self, text: str):
        return ("plain", text)


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


if __name__ == "__main__":
    unittest.main()
