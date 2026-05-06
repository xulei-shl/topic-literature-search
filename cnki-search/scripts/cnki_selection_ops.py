"""CNKI ???????"""

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

class CnkiSelectionMixin:
    """CNKI ???????"""

    def _clear_selected_results(self) -> None:
        clear_link = self.page.locator(".checkcount a").filter(has_text="清除").first
        if clear_link.count() == 0:
            return
        try:
            clear_link.click()
            time.sleep(0.3)
        except Exception as exc:
            logger.debug("清除已选文献失败: %s", exc)

    def _select_batch_results(self, export_limit: int, row_offset: int) -> Dict[str, Any]:
        remaining = export_limit
        selected_count = 0
        current_row_offset = row_offset
        current_row_count = 0

        while remaining > 0:
            self._wait_for_results_ready()
            row_locator = self.page.locator(".result-table-list tbody tr")
            current_row_count = row_locator.count()
            if current_row_count == 0:
                break

            if current_row_offset >= current_row_count:
                if not self._goto_next_results_page():
                    break
                current_row_offset = 0
                continue

            page_target_count = min(current_row_count - current_row_offset, remaining)
            self._select_rows_on_current_page(current_row_offset, page_target_count, current_row_count)
            selected_count = self._extract_selected_count(selected_count + page_target_count)
            remaining = export_limit - selected_count
            current_row_offset += page_target_count
            if remaining <= 0:
                break

            if current_row_offset < current_row_count:
                continue

            if not self._goto_next_results_page():
                break
            current_row_offset = 0

        if selected_count <= 0:
            raise ValidationError("结果页未选中任何文献，无法导出")
        return {
            "selected_count": selected_count,
            "next_row_offset": current_row_offset,
            "page_row_count": current_row_count,
        }

    def _select_rows_on_current_page(self, row_offset: int, page_target_count: int, row_count: int) -> None:
        checkbox_locator = self.page.locator(".result-table-list tbody input.cbItem")
        if row_offset == 0 and page_target_count == row_count and self.page.locator("#selectCheckAll1").count() > 0:
            self._ensure_checkbox_checked(
                self.page.locator("#selectCheckAll1").first,
                selector="#selectCheckAll1",
            )
            return

        for index in range(row_offset, row_offset + page_target_count):
            checkbox = checkbox_locator.nth(index)
            self._ensure_checkbox_checked(checkbox, selector=f".result-table-list tbody input.cbItem[{index}]")

    def _ensure_checkbox_checked(self, checkbox: Locator, selector: str = "") -> None:
        """稳定勾选复选框，必要时退回 JS 兜底。"""
        try:
            if checkbox.is_checked():
                return
        except Exception:
            pass

        try:
            checkbox.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("复选框滚动到可视区域失败: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.check(force=True, timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("常规勾选复选框失败，尝试使用 JS 兜底: selector=%s, error=%s", selector or "<locator>", exc)
            checkbox.evaluate(
                """
                (element) => {
                    element.checked = true;
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.dispatchEvent(new Event('click', { bubbles: true }));
                }
                """
            )

        try:
            if checkbox.is_checked():
                return
        except Exception as exc:
            logger.debug("校验复选框勾选状态失败: selector=%s, error=%s", selector or "<locator>", exc)

        raise TimeoutError(f"未能完成复选框勾选: {selector or '<locator>'}")

    def _prepare_next_batch_cursor(self, batch_selection: Dict[str, Any]) -> int:
        next_row_offset = batch_selection["next_row_offset"]
        page_row_count = batch_selection["page_row_count"]
        if next_row_offset < page_row_count:
            return next_row_offset

        if self._goto_next_results_page():
            return 0
        return next_row_offset

    def _extract_selected_count(self, default_value: int) -> int:
        locator = self.page.locator("#selectCount").first
        if locator.count() == 0:
            return default_value
        text = locator.inner_text().replace(",", "").replace("，", "").strip()
        return int(text) if text.isdigit() else default_value
