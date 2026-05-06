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

    def _dismiss_confirm_dialog_if_present(self) -> None:
        confirm_button = self.page.locator(".layui-layer-btn0").first
        if confirm_button.count() == 0:
            return
        try:
            confirm_button.click()
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
        return click_first_available(page or self.page, selectors, self._locator_wait_timeout_ms())

    def _wait_for_any_selector(self, selectors: list[str], timeout: Optional[int] = None) -> None:
        wait_for_any_selector(
            page=self.page,
            selectors=selectors,
            timeout_seconds=float(timeout or self.config.page_timeout),
            poll_interval_seconds=self._page_poll_interval_seconds(),
            wait_timeout_ms=self._locator_wait_timeout_ms(),
            error_cls=TimeoutError,
            error_message=f"等待页面元素超时: {selectors[0]}",
        )

    def _action_timeout_ms(self) -> int:
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return int(timeout_seconds * 1000)

    def _navigation_timeout_ms(self) -> int:
        timeout_seconds = getattr(self.config, "navigation_timeout", self.config.page_timeout)
        return int(timeout_seconds * 1000)

    def _download_timeout_ms(self) -> int:
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return int(timeout_seconds * 1000)

    def _export_option_download_probe_timeout_ms(self) -> int:
        """返回探测导出选项是否会自动下载的短超时时间。"""
        return max(1000, int(self._action_timeout_ms() / 2))

    def _page_change_timeout_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return float(timeout_seconds)

    def _locator_wait_timeout_ms(self) -> int:
        return int(self._action_timeout_ms() / 60)

    def _action_poll_interval_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return float(timeout_seconds) / 100

    def _page_poll_interval_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "page_timeout", 30)
        return float(timeout_seconds) / 120

    def _page_change_poll_interval_seconds(self) -> float:
        return self._page_change_timeout_seconds() / 240

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
