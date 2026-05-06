"""CNKI ????????"""

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

class CnkiNavigationMixin:
    """CNKI ????????"""

    def _restore_results_position(self, target_page: int) -> None:
        """恢复到需要续跑的结果页，优先使用可见数字页跳转。"""
        if target_page <= 1:
            return

        current_page = self.parser.parse_results_summary()["current_page"]
        if current_page > target_page:
            raise ValidationError(f"当前结果页码大于目标恢复页码: {current_page} > {target_page}")

        while current_page < target_page:
            page_link = self._find_resume_target_page_link(current_page=current_page, target_page=target_page)
            if page_link is not None:
                moved = self._goto_results_page_by_link(page_link)
            else:
                moved = self._goto_next_results_page()
            if not moved:
                raise ValidationError(f"未能恢复到目标页码: {target_page}")
            current_page = self.parser.parse_results_summary()["current_page"]

    def _has_results_state_changed(
        self,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        previous_sort: str,
    ) -> bool:
        """判断结果页状态是否已经发生变化。"""
        current_url = self.page.url
        current_page = self.parser.parse_results_summary()["page"]
        current_title = self._first_result_title()
        current_sort = self._current_sort_text()
        return any(
            [
                current_url != previous_url,
                current_page != previous_page,
                current_title != previous_title,
                current_sort != previous_sort,
            ]
        )

    def _click_next_page_link(self, next_link: Locator, attempt: int) -> None:
        """执行结果页“下一页”点击。"""
        try:
            next_link.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("翻页按钮滚动到可视区域失败: %s", exc)

        if attempt < self.NEXT_PAGE_MAX_RETRIES:
            next_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            return

        try:
            next_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        except Exception as click_exc:
            logger.debug("常规点击“下一页”失败，尝试使用 JS 点击: %s", click_exc)
            next_link.evaluate("(element) => element.click()")

    def _click_page_number_link(self, page_link: Locator, attempt: int) -> None:
        """执行结果页数字页码点击。"""
        try:
            page_link.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("页码按钮滚动到可视区域失败: %s", exc)

        if attempt < self.NEXT_PAGE_MAX_RETRIES:
            page_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            return

        try:
            page_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        except Exception as click_exc:
            logger.debug("常规点击页码失败，尝试使用 JS 点击: %s", click_exc)
            page_link.evaluate("(element) => element.click()")

    def _goto_next_results_page(self) -> bool:
        previous_url = self.page.url
        previous_page = self.parser.parse_results_summary()["page"]
        previous_title = self._first_result_title()
        previous_sort = self._current_sort_text()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            next_link = self._find_next_page_link()
            if next_link is None:
                return False

            class_name = next_link.get_attribute("class") or ""
            if "disabled" in class_name:
                return False

            try:
                self._click_next_page_link(next_link, attempt)
                self._wait_for_results_changed(
                    previous_url,
                    previous_page,
                    previous_title,
                    previous_sort,
                    timeout=self._page_change_timeout_seconds(),
                )
                return True
            except Exception as exc:
                last_error = exc
                try:
                    if self._has_results_state_changed(previous_url, previous_page, previous_title, previous_sort):
                        return True
                except Exception:
                    pass

                logger.warning(
                    "结果页翻页失败，准备重试: attempt=%s/%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    exc,
                )
                self._dismiss_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self.NEXT_PAGE_RETRY_DELAY)

        raise TimeoutError(f"结果页翻页失败，已重试 {self.NEXT_PAGE_MAX_RETRIES} 次: {last_error}")

    def _goto_results_page_by_link(self, page_link: Locator) -> bool:
        """点击数字页码并等待结果页完成跳转。"""
        previous_url = self.page.url
        previous_page = self.parser.parse_results_summary()["page"]
        previous_title = self._first_result_title()
        previous_sort = self._current_sort_text()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            try:
                self._click_page_number_link(page_link, attempt)
                self._wait_for_results_changed(
                    previous_url,
                    previous_page,
                    previous_title,
                    previous_sort,
                    timeout=self._page_change_timeout_seconds(),
                )
                return True
            except Exception as exc:
                last_error = exc
                try:
                    if self._has_results_state_changed(previous_url, previous_page, previous_title, previous_sort):
                        return True
                except Exception:
                    pass

                logger.warning(
                    "结果页页码跳转失败，准备重试: attempt=%s/%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    exc,
                )
                self._dismiss_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self.NEXT_PAGE_RETRY_DELAY)

        raise TimeoutError(f"结果页页码跳转失败，已重试 {self.NEXT_PAGE_MAX_RETRIES} 次: {last_error}")

    def _find_next_page_link(self) -> Optional[Locator]:
        selectors = ["#PageNext", "#Page_next_top", "a#Page_next_top", ".pages a"]
        for selector in selectors:
            locator_group = self.page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    text = locator.inner_text().strip()
                except Exception:
                    text = ""
                if selector == ".pages a" and "下一页" not in text:
                    continue
                return locator
        return None

    def _find_resume_target_page_link(self, current_page: int, target_page: int) -> Optional[Locator]:
        """查找恢复续跑时可直接点击的目标数字页。"""
        page_links = self.page.locator(".pages a[data-curpage]")
        candidate_link: Optional[Locator] = None
        candidate_page = current_page

        for index in range(page_links.count()):
            link = page_links.nth(index)
            try:
                text = link.inner_text().strip()
            except Exception:
                continue
            if not text.isdigit():
                continue

            raw_page = link.get_attribute("data-curpage") or text
            if not raw_page.isdigit():
                continue

            page_number = int(raw_page)
            if page_number <= current_page or page_number > target_page:
                continue

            class_name = (link.get_attribute("class") or "").strip()
            if "cur" in class_name:
                continue

            if page_number == target_page:
                return link

            if page_number > candidate_page:
                candidate_page = page_number
                candidate_link = link

        return candidate_link
