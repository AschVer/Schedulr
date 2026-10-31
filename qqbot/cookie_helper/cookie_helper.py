"""Schedulr Cookie 获取小工具（Windows 桌面版）。

打开一个干净的 Chrome，让你手动完成学校 WebVPN 登录（验证码 + 手机/微信
动态密码），然后自动提取 Cookie、验证课表接口，并保存成文件。

Cookie 只在本机内存、剪贴板和 %USERPROFILE%\\SchedulrBot 下保存。
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import tkinter as tk
from tkinter import messagebox, ttk

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "缺少 Selenium",
        "请先双击「启动.bat」，它会自动安装依赖。\n"
        "或者手动运行：pip install -r requirements.txt",
    )
    raise SystemExit(1)

APP_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = APP_DIR.parent / "astrbot_plugin_schedulr"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

try:
    from schedulr_core import (
        BitcError,
        fetch_timetable,
        resolve_academic_year,
        resolve_term,
    )

    CORE_AVAILABLE = True
except Exception:
    CORE_AVAILABLE = False

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    TZ = timezone(timedelta(hours=8))

LOGIN_URL = "https://vpn.bitc.edu.cn/login"
PORTAL_URL = "https://vpn.bitc.edu.cn/"
ENTRY_URL = (
    "https://jwxt.vpn.bitc.edu.cn/kbcx/"
    "xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"
)
OUTPUT_DIR = Path.home() / "SchedulrBot"
ACCOUNTS = {1: "一班", 2: "二班"}


def merge_cookie_header(cookies: list[dict]) -> str:
    """Merge Selenium cookie dicts into a single Cookie request header."""

    merged: dict[str, str] = {}
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if "bitc.edu.cn" not in domain or not name:
            continue
        merged[name] = value
    return "; ".join(f"{name}={value}" for name, value in sorted(merged.items()))


def webvpn_account(cookie: str) -> tuple[str | None, datetime | None]:
    """Read the account id and expiry from the ``_webvpn_key`` JWT."""

    token = ""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("_webvpn_key="):
            token = part.split("=", 1)[1]
            break
    if not token or token.count(".") < 2:
        return None, None
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None, None
    account = data.get("user")
    exp = data.get("exp")
    expires_at = (
        datetime.fromtimestamp(int(exp), TZ) if isinstance(exp, (int, float)) else None
    )
    return (str(account) if account else None, expires_at)


class BrowserSession:
    def __init__(self) -> None:
        self.driver: webdriver.Chrome | None = None
        self.profile_dir: Path | None = None
        self.lock = threading.Lock()

    def open(self) -> None:
        self.close()
        self.profile_dir = Path(tempfile.mkdtemp(prefix="schedulr-chrome-"))
        options = Options()
        options.add_argument(f"--user-data-dir={self.profile_dir}")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        self.driver = webdriver.Chrome(options=options)
        self.driver.get(LOGIN_URL)

    def collect(self) -> tuple[str, str]:
        """Return (cookie_header, current_url)."""

        with self.lock:
            if self.driver is None:
                raise RuntimeError("浏览器还没打开")
            cookies: list[dict] = []
            for url in (PORTAL_URL, ENTRY_URL):
                try:
                    self.driver.get(url)
                    WebDriverWait(self.driver, 25).until(
                        lambda driver: driver.execute_script(
                            "return document.readyState"
                        )
                        == "complete"
                    )
                except Exception:
                    pass
                cookies.extend(self.driver.get_cookies())
            return merge_cookie_header(cookies), self.driver.current_url

    def close(self) -> None:
        with self.lock:
            if self.driver is not None:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
        if self.profile_dir is not None:
            shutil.rmtree(self.profile_dir, ignore_errors=True)
            self.profile_dir = None


class CookieApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Schedulr Cookie 获取工具")
        self.root.geometry("760x520")
        self.root.minsize(680, 460)

        self.sessions = {index: BrowserSession() for index in ACCOUNTS}
        self.cookies: dict[int, str] = {1: "", 2: ""}
        self.accounts: dict[int, str | None] = {1: None, 2: None}
        self.status_labels: dict[int, ttk.Label] = {}
        self.buttons: list[ttk.Button] = []
        self._copy_buttons: dict[int, ttk.Button] = {}
        self.busy = False

        self._build_ui()

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(18, 16, 18, 8))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Schedulr Cookie 获取工具",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "每个账号点一次「打开登录页」，在 Chrome 里完成验证码和动态密码登录，"
                "再点「提取并验证」。"
            ),
            wraplength=700,
        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(
            header,
            text="Cookie 等同于登录态，只保存在本机，不要发群或提交到 Git。",
            foreground="#b45309",
        ).pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(self.root, padding=(18, 8, 18, 8))
        body.pack(fill="both", expand=True)

        for index, default_name in ACCOUNTS.items():
            frame = ttk.LabelFrame(
                body, text=f"账号 {index} · {default_name}", padding=12
            )
            frame.pack(fill="x", pady=6)
            row = ttk.Frame(frame)
            row.pack(fill="x")
            open_button = ttk.Button(
                row, text="打开登录页", command=lambda i=index: self._open(i)
            )
            open_button.pack(side="left")
            self.buttons.append(open_button)
            extract_button = ttk.Button(
                row, text="提取并验证", command=lambda i=index: self._extract(i)
            )
            extract_button.pack(side="left", padx=8)
            self.buttons.append(extract_button)
            copy_button = ttk.Button(
                row,
                text="复制 Cookie",
                command=lambda i=index: self._copy(i),
                state="disabled",
            )
            copy_button.pack(side="left")
            self.buttons.append(copy_button)
            self._copy_buttons[index] = copy_button

            status = ttk.Label(frame, text="未开始", foreground="#555")
            status.pack(anchor="w", pady=(8, 0))
            self.status_labels[index] = status

        log_frame = ttk.LabelFrame(body, text="日志", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

        footer = ttk.Frame(self.root, padding=(18, 0, 18, 12))
        footer.pack(fill="x")
        self.open_dir_button = ttk.Button(
            footer, text="打开保存目录", command=self._open_output_dir
        )
        self.open_dir_button.pack(side="left")
        ttk.Label(
            footer,
            text=f"保存位置：{OUTPUT_DIR}",
            foreground="#555",
        ).pack(side="left", padx=12)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, index: int, text: str, color: str = "#555") -> None:
        self.status_labels[index].configure(text=text, foreground=color)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in self.buttons:
            if button not in self._copy_buttons.values():
                button.configure(state=state)
        for index, button in self._copy_buttons.items():
            button.configure(
                state="normal" if self.cookies.get(index) and not busy else "disabled"
            )

    def _open(self, index: int) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self._set_status(index, "正在打开 Chrome…")

        def worker() -> None:
            try:
                self.sessions[index].open()
                self.root.after(
                    0,
                    lambda: self._set_status(
                        index, "Chrome 已打开，请完成学校登录", "#1d4ed8"
                    ),
                )
                self.root.after(
                    0,
                    lambda: self._append_log(
                        f"账号 {index}：已打开登录页，请完成验证码和动态密码登录。"
                    ),
                )
            except Exception as error:
                message = f"打开浏览器失败：{error}"
                self.root.after(0, lambda: self._set_status(index, message, "#b91c1c"))
                self.root.after(0, lambda: self._append_log(message))
            finally:
                self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _extract(self, index: int) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self._set_status(index, "正在提取并验证 Cookie…")

        def worker() -> None:
            try:
                cookie, current_url = self.sessions[index].collect()
                if not cookie:
                    raise RuntimeError("没有拿到 bitc.edu.cn 的 Cookie，请确认已登录")
                account, _expires_at = webvpn_account(cookie)
                if (
                    index == 2
                    and account is not None
                    and account == self.accounts.get(1)
                ):
                    raise RuntimeError(
                        f"这个 Cookie 还是账号 {account}，不是第二个账号。"
                        "请先退出登录/换账号，再重新提取。"
                    )
                self.accounts[index] = account
                summary, refreshed = self._validate(cookie)
                if refreshed and refreshed != cookie:
                    cookie = refreshed
                    self._append_log("Cookie 已自动续期")
                self.cookies[index] = cookie
                self._save(index, cookie)
                label = f"账号 {account} · " if account else ""
                self.root.after(
                    0,
                    lambda: self._set_status(
                        index, f"✅ {label}验证成功：{summary}", "#047857"
                    ),
                )
                self.root.after(0, lambda: self._on_cookie_ready(index, summary))
            except Exception as error:
                message = str(error)
                self.root.after(
                    0, lambda: self._set_status(index, f"❌ {message}", "#b91c1c")
                )
                self.root.after(0, lambda: self._append_log(f"账号 {index}：{message}"))
            finally:
                self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _validate(self, cookie: str) -> tuple[str, str]:
        if not CORE_AVAILABLE:
            return "已提取（未找到插件核心，跳过接口验证）", cookie
        now = datetime.now(TZ)
        year = resolve_academic_year("", now)
        term = resolve_term(0, now)
        try:
            result = asyncio.run(
                fetch_timetable(
                    cookie,
                    academic_year=year,
                    term=term,
                    now=now,
                    timeout=25,
                )
            )
        except BitcError as error:
            raise RuntimeError(str(error)) from error
        timetable = result.timetable
        week = timetable.week_for(now.date())
        suffix = f"，第 {week} 周" if week and week > 0 else ""
        return f"{len(timetable.courses)} 节课程{suffix}", result.cookie

    def _on_cookie_ready(self, index: int, summary: str) -> None:
        self._copy_buttons[index].configure(state="normal")
        self._append_log(f"账号 {index}：Cookie 已保存并验证（{summary}）。")
        self._copy(index)
        self._append_log(f"账号 {index}：Cookie 已复制到剪贴板。")

    def _save(self, index: int, cookie: str) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / f"profile_{index}.cookie.txt").write_text(
            cookie, encoding="utf-8"
        )
        payload = {
            "profile_1_name": ACCOUNTS[1],
            "profile_1_cookie": self.cookies.get(1, ""),
            "profile_2_name": ACCOUNTS[2],
            "profile_2_cookie": self.cookies.get(2, ""),
        }
        (OUTPUT_DIR / "cookies.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _copy(self, index: int) -> None:
        cookie = self.cookies.get(index)
        if not cookie:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(cookie)
        self.root.update()

    def _open_output_dir(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            import os

            os.startfile(OUTPUT_DIR)  # type: ignore[attr-defined]
        except Exception as error:
            messagebox.showinfo("保存位置", f"{OUTPUT_DIR}\n\n{error}")

    def close(self) -> None:
        for session in self.sessions.values():
            session.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        style.theme_use("vista")
    except Exception:
        pass
    app = CookieApp(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
