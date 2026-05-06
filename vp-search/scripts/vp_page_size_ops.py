"""????????????"""

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

class VpPageSizeMixin:
    """????????????"""

    def _prefer_results_page_size(self) -> None:
        page_size_link = self._find_preferred_results_page_size_link()
        if page_size_link is None:
            logger.debug("未找到每页显示数量入口，保持当前分页大小: target=%s", self.PREFERRED_RESULTS_PAGE_SIZE)
            return

        class_name = page_size_link.get_attribute("class") or ""
        if "active" in class_name:
            logger.debug("每页显示数量已是目标值，无需切换: target=%s", self.PREFERRED_RESULTS_PAGE_SIZE)
            return

        previous_page = ""
        try:
            previous_page = self.parser.parse_results_summary()["page"]
        except Exception:
            previous_page = ""
        previous_row_count = self._result_checkbox_locator().count()

        try:
            logger.debug(
                "准备切换每页显示数量: target=%s, previous_page=%s, previous_row_count=%s",
                self.PREFERRED_RESULTS_PAGE_SIZE,
                previous_page,
                previous_row_count,
            )
            page_size_link.click()
            self._wait_for_results_page_size_applied(page_size_link, previous_page, previous_row_count)
            logger.debug("每页显示数量切换完成: target=%s", self.PREFERRED_RESULTS_PAGE_SIZE)
        except Exception as exc:
            logger.debug("切换每页显示数量失败: target=%s, error=%s", self.PREFERRED_RESULTS_PAGE_SIZE, exc)

    def _find_preferred_results_page_size_link(self) -> Optional[Locator]:
        """查找“每页 50 条”入口，兼容不同分页容器实现。"""
        page_size_selectors = [
            f"#selectPageSize a[data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
            f"#selectPageSize [data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
            f"[data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
        ]
        for selector in page_size_selectors:
            locator_group = self.page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                    return locator
                except Exception:
                    continue
        return None

    def _wait_for_results_page_size_applied(
        self,
        page_size_link: Locator,
        previous_page: str,
        previous_row_count: int,
    ) -> None:
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            class_name = page_size_link.get_attribute("class") or ""
            if "active" in class_name:
                return

            current_row_count = self._result_checkbox_locator().count()
            if previous_row_count and current_row_count and current_row_count != previous_row_count:
                return

            try:
                current_page = self.parser.parse_results_summary()["page"]
            except Exception:
                current_page = ""
            if previous_page and current_page and current_page != previous_page:
                return

            time.sleep(self._page_change_poll_interval_seconds())
        raise TimeoutError("等待每页显示数量切换超时")

    def _current_results_page_size(self) -> int:
        """读取当前结果页激活的每页条数。"""
        selector_candidates = [
            "#selectPageSize a.active[data-count]",
            "#selectPageSize [data-count].active",
            "#selectPageSize [data-count][class*='active']",
        ]
        for selector in selector_candidates:
            locator = self.page.locator(selector).first
            try:
                if locator.count() == 0:
                    continue
                raw_count = (locator.get_attribute("data-count") or "").strip()
                if raw_count.isdigit():
                    return int(raw_count)
            except Exception:
                continue
        return self.PREFERRED_RESULTS_PAGE_SIZE
