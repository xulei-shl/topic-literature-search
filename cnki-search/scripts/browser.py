"""浏览器与会话管理。"""

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from camoufox.sync_api import NewBrowser
from playwright.sync_api import Page, sync_playwright

from config import CnkiSearchConfig
from exceptions import BrowserError

logger = logging.getLogger("cnki_search.browser")


class BrowserManager:
    """负责 Camoufox 浏览器生命周期与会话持久化。"""

    def __init__(self, config: CnkiSearchConfig):
        self.config = config
        self._playwright = None
        self._browser = None
        self._context = None
        self._page: Optional[Page] = None

        self._session_dir = self.config.ensure_session_dir()
        self._cookies_file = self._session_dir / "cookies.json"
        self._local_storage_file = self._session_dir / "local_storage.json"
        self._state_file = self._session_dir / "state.json"

    @property
    def page(self) -> Optional[Page]:
        """返回当前页面。"""
        return self._page

    def start(self) -> Page:
        """启动浏览器。"""
        logger.info("正在启动 Camoufox 浏览器")

        try:
            self._reset_asyncio_loop()
            self._cleanup_virtual_display()
            self._playwright = sync_playwright().start()

            proxy_dict = {"server": self.config.proxy} if self.config.proxy else None
            self._browser = NewBrowser(
                self._playwright,
                headless=CnkiSearchConfig.get_headless_mode(self.config.headless),
                geoip=self.config.geoip,
                proxy=proxy_dict,
            )

            self._context = self._browser.new_context(
                proxy=proxy_dict,
                locale=self.config.language,
                accept_downloads=True,
            )
            self._context.set_default_timeout(self.config.page_timeout * 1000)
            self._page = self._context.new_page()
            self.load_cookies()
            return self._page
        except Exception as exc:
            raise BrowserError(f"浏览器启动失败: {exc}") from exc

    def close(self) -> None:
        """关闭浏览器。"""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._browser = None
        self._reset_asyncio_loop()

    def restore_session(self, target_url: Optional[str] = None) -> None:
        """恢复会话并导航到目标页面。"""
        if not self._page:
            raise BrowserError("浏览器尚未启动")

        self._page.goto(
            self.config.home_url,
            timeout=self.config.navigation_timeout * 1000,
            wait_until="domcontentloaded",
        )
        storage_loaded = self.load_local_storage()
        if target_url and (storage_loaded or self._page.url != target_url):
            self._page.goto(
                target_url,
                timeout=self.config.navigation_timeout * 1000,
                wait_until="domcontentloaded",
            )

    def save_session(self, page_type: Optional[str] = None, **extra_state: Any) -> None:
        """保存 cookies、localStorage 和状态。"""
        if not self._context or not self._page:
            return

        try:
            cookies = self._context.cookies()
            self._write_json(self._cookies_file, cookies)
        except Exception as exc:
            logger.warning("保存 cookies 失败: %s", exc)

        try:
            local_storage = self._page.evaluate(
                """
                () => {
                    const items = {};
                    for (let i = 0; i < localStorage.length; i += 1) {
                        const key = localStorage.key(i);
                        items[key] = localStorage.getItem(key);
                    }
                    return items;
                }
                """
            )
            self._write_json(self._local_storage_file, local_storage)
        except Exception as exc:
            logger.warning("保存 localStorage 失败: %s", exc)

        state = self.read_state()
        state.update(extra_state)
        if self._page.url:
            state["last_url"] = self._page.url
        if page_type:
            state["last_page_type"] = page_type
        state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_json(self._state_file, state)

    def read_state(self) -> Dict[str, Any]:
        """读取状态文件。"""
        if not self._state_file.exists():
            return {}
        try:
            with open(self._state_file, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return {}

    def load_cookies(self) -> bool:
        """加载 cookies。"""
        if not self._context or not self._cookies_file.exists():
            return False

        try:
            with open(self._cookies_file, "r", encoding="utf-8") as file:
                cookies = json.load(file)
            if cookies:
                self._context.add_cookies(cookies)
            return True
        except Exception as exc:
            logger.warning("加载 cookies 失败: %s", exc)
            return False

    def load_local_storage(self) -> bool:
        """加载 localStorage。"""
        if not self._page or not self._local_storage_file.exists():
            return False

        try:
            with open(self._local_storage_file, "r", encoding="utf-8") as file:
                local_storage = json.load(file)
            self._page.evaluate(
                """
                (items) => {
                    Object.entries(items).forEach(([key, value]) => {
                        localStorage.setItem(key, value);
                    });
                }
                """,
                local_storage,
            )
            return True
        except Exception as exc:
            logger.warning("加载 localStorage 失败: %s", exc)
            return False

    def is_captcha_visible(self, page: Optional[Page] = None) -> bool:
        """判断验证码是否可见。"""
        target_page = page or self._page
        if not target_page:
            return False

        try:
            return bool(
                target_page.evaluate(
                    """
                    () => {
                        const captcha = document.querySelector('#tcaptcha_transform_dy');
                        if (!captcha) {
                            return false;
                        }
                        const rect = captcha.getBoundingClientRect();
                        return rect.top >= 0 && rect.width > 0 && rect.height > 0;
                    }
                    """
                )
            )
        except Exception:
            return False

    def wait_for_captcha_completion(self, page: Optional[Page] = None, timeout: Optional[int] = None) -> bool:
        """等待用户手动完成验证码。"""
        target_page = page or self._page
        if not target_page:
            return False

        timeout = timeout or self.config.captcha_timeout
        if not self.is_captcha_visible(target_page):
            return True

        print("\n检测到 CNKI 滑块验证码，请在浏览器中手动完成。")
        print("完成后可直接按回车继续，或等待脚本自动检测。")

        confirmed = threading.Event()

        def wait_input() -> None:
            try:
                input()
                confirmed.set()
            except Exception:
                return

        thread = threading.Thread(target=wait_input, daemon=True)
        thread.start()

        started = time.time()
        while time.time() - started < timeout:
            if confirmed.is_set():
                time.sleep(2)
                return not self.is_captcha_visible(target_page)
            if not self.is_captcha_visible(target_page):
                time.sleep(2)
                return True
            time.sleep(2)

        return not self.is_captcha_visible(target_page)

    def wait_for_manual_login(self) -> None:
        """等待用户手动登录。"""
        if not self._page:
            raise BrowserError("浏览器尚未启动")

        self.restore_session(self.config.home_url)
        print("\n请在打开的 Camoufox 浏览器中完成知网登录。")
        print("登录完成后按回车继续保存会话。")
        input()
        time.sleep(2)

    def _write_json(self, path: Path, data: Any) -> None:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def _reset_asyncio_loop(self) -> None:
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop and not loop.is_closed():
                loop.close()
            asyncio.set_event_loop(None)
        except RuntimeError:
            return

    def _cleanup_virtual_display(self) -> None:
        cleaned = 0

        try:
            result = subprocess.run(
                ["pgrep", "-f", "Xvfb"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                for pid in result.stdout.strip().splitlines():
                    subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
                    cleaned += 1
        except Exception:
            pass

        for display_num in range(99, 110):
            try:
                lock_file = Path(f"/tmp/.X{display_num}-lock")
                if lock_file.exists():
                    lock_file.unlink()
                    cleaned += 1
            except Exception:
                pass

        display = os.environ.get("DISPLAY", "")
        if display.startswith(":"):
            try:
                display_num = int(display[1:].split(".", maxsplit=1)[0])
                if 90 <= display_num <= 109:
                    del os.environ["DISPLAY"]
                    cleaned += 1
            except Exception:
                pass

        if cleaned:
            logger.info("已清理 %s 个残留虚拟显示资源", cleaned)
        time.sleep(0.5)

    def __enter__(self) -> "BrowserManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
