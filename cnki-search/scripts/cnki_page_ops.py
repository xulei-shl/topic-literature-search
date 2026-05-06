"""CNKI ????????????"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams
from src.utils.playwright_page import click_first_available, disable_checkbox, enable_checkbox, first_visible_locator, set_input_value, wait_for_any_selector
from src.utils.result_output import build_export_file_path
from src.utils.search_timeout import find_env_path, parse_env_file

from browser import BrowserManager
from config import CnkiSearchConfig
from export_processor import ExportResultProcessor
from exceptions import CaptchaError, NavigationStateError, TimeoutError, ValidationError
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("cnki_search.interactor")

class CnkiPageMixin:
    """CNKI ????????????"""

    def _wait_for_selector(self, selector: str, timeout: Optional[int] = None) -> None:
        self.page.locator(selector).first.wait_for(
            state="visible",
            timeout=(timeout or self.config.page_timeout) * 1000,
        )

    def _wait_for_any_selector(self, selectors: list[str], timeout: Optional[int] = None) -> None:
        wait_for_any_selector(
            page=self.page,
            selectors=selectors,
            timeout_seconds=float(timeout or self.config.page_timeout),
            poll_interval_seconds=0.2,
            wait_timeout_ms=300,
            error_cls=TimeoutError,
            error_message=f"等待页面元素超时: {selectors[0]}",
        )

    def _wait_for_results_ready(self) -> None:
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()

            if self.page.locator(".result-table-list tbody tr").count() > 0:
                return

            if self.page.locator("#ModuleSearchResult .no-content").count() > 0:
                return

            if self.page.locator(".pagerTitleCell").count() > 0:
                title_text = self.page.locator(".pagerTitleCell").first.inner_text()
                if "条结果" in title_text:
                    return

            time.sleep(0.5)

        raise TimeoutError("等待结果页超时")

    def _wait_for_results_changed(
        self,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        previous_sort: str,
        timeout: Optional[float] = None,
    ) -> None:
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        while time.time() < deadline:
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()

            try:
                if self._has_results_state_changed(previous_url, previous_page, previous_title, previous_sort):
                    return
            except Exception as exc:
                logger.debug("结果页状态刷新中，继续等待: %s", exc)
            time.sleep(0.5)

        raise TimeoutError("等待翻页或排序完成超时")

    def _wait_for_results_page_changed(self, previous_page: str, previous_rows: int) -> None:
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            try:
                current_page = self.parser.parse_results_summary()["page"]
                current_rows = self.page.locator(".result-table-list tbody tr").count()
                if current_page != previous_page or current_rows != previous_rows:
                    return
            except Exception as exc:
                logger.debug("等待结果页分页状态变化时遇到瞬时异常: %s", exc)
            time.sleep(0.5)

    def _dismiss_dialog_if_present(self) -> None:
        dialog = self.page.locator(".layui-layer-dialog").first
        if dialog.count() == 0:
            return
        confirm_button = self.page.locator(".layui-layer-btn0").first
        if confirm_button.count() == 0:
            return
        confirm_button.click()

    def _ensure_captcha_cleared(self) -> None:
        if not self.browser_manager.is_captcha_visible(self.page):
            return
        if not self.browser_manager.wait_for_captcha_completion(self.page):
            raise CaptchaError("验证码尚未完成，请手动完成后重试")

    def _first_result_title(self) -> str:
        locator = self.page.locator("td.name a.fz14").first
        if locator.count() == 0:
            return ""
        return locator.inner_text().strip()

    def _current_sort_text(self) -> str:
        locator = self.page.locator("#orderList li.cur").first
        if locator.count() == 0:
            return ""
        return locator.inner_text().strip()

    def _first_visible_locator(self, selectors: list[str], page: Optional[Page] = None) -> Locator:
        return first_visible_locator(
            page=page or self.page,
            selectors=selectors,
            timeout_ms=500,
            error_cls=ValidationError,
            error_message=f"未找到页面元素: {selectors[0]}",
        )

    def _click_first_available(self, selectors: list[str], page: Optional[Page] = None) -> bool:
        return click_first_available(page or self.page, selectors, timeout_ms=500)

    def _action_timeout_ms(self) -> int:
        """返回单次页面动作超时。"""
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return max(int(timeout_seconds * 1000), 1000)

    def _page_change_timeout_seconds(self) -> float:
        """返回结果页变化等待超时。"""
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return max(float(timeout_seconds), 1.0)

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

    def _normalize_limit(self, num_results: Optional[int]) -> int:
        if num_results is None:
            return self.config.max_results
        if num_results <= 0:
            raise ValidationError("结果数量必须大于 0")
        return min(num_results, self.config.max_results)
