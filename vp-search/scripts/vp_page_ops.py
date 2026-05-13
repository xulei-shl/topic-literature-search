"""?????????"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams
from src.utils.playwright_page import click_first_available, disable_checkbox, enable_checkbox, has_visible_selector, set_native_select_value, wait_for_any_selector
from src.utils.result_output import build_export_file_path

from browser import BrowserManager
from config import VpSearchConfig
from exceptions import CaptchaError, TimeoutError, ValidationError
from export_processor import ExportResultProcessor
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("vp_search.interactor")

class VpPageMixin:
    """?????????"""

    def _has_visible_selector(self, selectors: list[str], page: Optional[Page] = None) -> bool:
        """判断任一选择器是否存在可见元素。"""
        return has_visible_selector(page or self.page, selectors, self._locator_wait_timeout_ms())

    def _wait_for_results_ready(self) -> None:
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            if self._is_results_page_ready():
                return
            if self.page.locator(".no-data, .empty, .none-data").count() > 0:
                return
            time.sleep(self._page_poll_interval_seconds())
        raise TimeoutError("等待结果页超时")

    def _is_results_page_ready(self) -> bool:
        for selector in self.RESULTS_READY_SELECTORS:
            if self.page.locator(selector).count() > 0:
                return True
        return self._result_checkbox_locator().count() > 0

    def _dismiss_confirm_dialog_if_present(self, timeout_ms: int = 500) -> bool:
        """关闭确认弹出框，先定位对话框容器再取内部按钮。"""
        dialog = self.page.locator(".layui-layer-dialog").first
        if dialog.count() == 0:
            return False
        try:
            dialog.wait_for(state="visible", timeout=timeout_ms)
        except Exception:
            return False
        confirm_button = dialog.locator(".layui-layer-btn0").first
        if confirm_button.count() == 0:
            # JS 兜底：直接点击页面上所有可见的确认按钮
            try:
                self.page.evaluate(
                    """
                    () => {
                        const btn = document.querySelector('.layui-layer-dialog .layui-layer-btn0');
                        if (btn) btn.click();
                    }
                    """
                )
                logger.debug("确认对话框已通过 JS 兜底关闭")
                return True
            except Exception as exc:
                logger.debug("JS 兜底关闭对话框失败: %s", exc)
                return False
        try:
            confirm_button.click(timeout=timeout_ms)
            self._wait_for_dialog_dismissed()
            logger.debug("确认对话框已关闭")
            return True
        except Exception as exc:
            logger.debug("确认按钮点击失败: %s", exc)
            return False

    def _wait_for_dialog_dismissed(self, timeout_ms: int = 2000) -> None:
        """等待对话框关闭"""
        try:
            dialog = self.page.locator(".layui-layer-dialog").first
            if dialog.count() > 0:
                dialog.wait_for(state="hidden", timeout=timeout_ms)
        except Exception:
            pass

    def _ensure_captcha_cleared(self) -> None:
        if not self.browser_manager.is_captcha_visible(self.page):
            return
        if not self.browser_manager.wait_for_captcha_completion(self.page):
            raise CaptchaError("验证码尚未完成，请手动完成后重试")

    def _first_result_title(self) -> str:
        for selector in [
            ".search-list a[title]",
            ".result-list a[title]",
            ".article-item a[title]",
            "h3 a",
        ]:
            locator = self.page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                return locator.inner_text().strip()
            except Exception:
                continue
        return ""

    def _click_first_available(self, selectors: list[str], page: Optional[Page] = None) -> bool:
        return click_first_available(page or self.page, selectors, timeout_ms=500)

    def _wait_for_any_selector(self, selectors: list[str], timeout: Optional[int] = None) -> None:
        wait_for_any_selector(
            page=self.page,
            selectors=selectors,
            timeout_seconds=float(timeout or self.config.page_timeout),
            poll_interval_seconds=0.5,
            wait_timeout_ms=500,
            error_cls=TimeoutError,
            error_message=f"等待页面元素超时: {selectors[0]}",
        )

    def _action_timeout_ms(self) -> int:
        """返回操作超时毫秒数（下限 1s，上限 15s，避免过短导致虚假超时，也避免过长浪费等待）。"""
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return max(min(int(timeout_seconds * 1000), 15000), 1000)

    def _navigation_timeout_ms(self) -> int:
        """返回导航超时毫秒数。"""
        timeout_seconds = getattr(self.config, "navigation_timeout", self.config.page_timeout)
        if timeout_seconds is None:
            timeout_seconds = self.config.page_timeout
        return max(int(timeout_seconds * 1000), 1000)

    def _download_timeout_ms(self) -> int:
        """返回下载超时毫秒数（下限 10 秒，避免大文件下载超时）。"""
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return max(int(timeout_seconds * 1000), 10000)

    def _export_option_download_probe_timeout_ms(self) -> int:
        """返回导出选项下载探测超时毫秒数（短超时，仅检测点击选项后是否触发下载）。"""
        return 3000

    def _page_change_timeout_seconds(self) -> float:
        """返回页面切换超时秒数（下限 1 秒，避免过短导致虚假超时重试）。"""
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return max(float(timeout_seconds), 1.0)

    def _locator_wait_timeout_ms(self) -> int:
        return max(min(int(self._action_timeout_ms() / 4), 1000), 300)

    def _action_poll_interval_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return min(max(float(timeout_seconds) / 20, 0.05), 0.3)

    def _page_poll_interval_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "page_timeout", 30)
        return min(max(float(timeout_seconds) / 30, 0.1), 0.5)

    def _page_change_poll_interval_seconds(self) -> float:
        return min(max(self._page_change_timeout_seconds() / 20, 0.1), 0.5)

    def _format_date_range(self, date_from: Optional[str], date_to: Optional[str]) -> str:
        if date_from and date_to:
            return f"{date_from} ~ {date_to}"
        return date_from or date_to or ""

    def _normalize_download_limit(self, num_results: Optional[int], total_results: int) -> int:
        if total_results <= 0:
            return 0
        if num_results is None:
            return total_results
        if num_results <= 0:
            raise ValidationError("下载数量必须大于 0")
        return min(num_results, total_results)
