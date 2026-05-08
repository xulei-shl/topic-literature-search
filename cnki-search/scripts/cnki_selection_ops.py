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

    def _select_batch_results(
        self,
        export_limit: int,
        row_offset: int,
        strict_target: bool,
    ) -> Dict[str, Any]:
        remaining = export_limit
        covered_count = 0
        selected_count = 0
        current_row_offset = row_offset
        current_row_count = 0
        start_page = 0
        end_page = 0

        while remaining > 0 if strict_target else covered_count < export_limit:
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

            current_page = self._current_results_page_number()
            if start_page <= 0:
                start_page = current_page
            end_page = current_page
            page_target_count = min(
                current_row_count - current_row_offset,
                remaining if strict_target else export_limit - covered_count,
            )
            page_selected_count = self._select_rows_on_current_page(
                current_row_offset,
                page_target_count,
                current_row_count,
            )
            covered_count += page_target_count
            selected_count += page_selected_count
            if strict_target:
                remaining = export_limit - selected_count
            logger.info(
                "当前页勾选完成: strict_target=%s, row_offset=%s, target=%s, actual=%s, covered=%s, selected=%s",
                strict_target,
                current_row_offset,
                page_target_count,
                page_selected_count,
                covered_count,
                selected_count,
            )
            current_row_offset += page_target_count
            if strict_target and remaining <= 0:
                break
            if not strict_target and covered_count >= export_limit:
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
            "start_page": start_page,
            "end_page": end_page,
        }

    def _select_rows_on_current_page(self, row_offset: int, page_target_count: int, row_count: int) -> int:
        checkbox_locator = self.page.locator(".result-table-list tbody input.cbItem")
        if row_offset == 0 and page_target_count == row_count and self.page.locator("#selectCheckAll1").count() > 0:
            self._ensure_checkbox_checked(
                self.page.locator("#selectCheckAll1").first,
                selector="#selectCheckAll1",
            )
        else:
            for index in range(row_offset, row_offset + page_target_count):
                checkbox = checkbox_locator.nth(index)
                self._ensure_checkbox_checked(checkbox, selector=f".result-table-list tbody input.cbItem[{index}]")

        selected_count = self._count_checked_rows(checkbox_locator, row_offset, page_target_count)
        if selected_count >= page_target_count:
            return selected_count

        missing_indexes = self._find_unchecked_row_indexes(checkbox_locator, row_offset, page_target_count)
        if missing_indexes:
            logger.warning(
                "当前页批量勾选后存在缺口，尝试页内补勾: row_offset=%s, target=%s, missing=%s",
                row_offset,
                page_target_count,
                len(missing_indexes),
            )
            for index in missing_indexes:
                checkbox = checkbox_locator.nth(index)
                self._ensure_checkbox_checked(checkbox, selector=f".result-table-list tbody input.cbItem[{index}]")
            selected_count = self._count_checked_rows(checkbox_locator, row_offset, page_target_count)

        if selected_count < page_target_count:
            logger.warning(
                "当前页补勾后仍未达标，将保留缺口继续处理: row_offset=%s, target=%s, actual=%s",
                row_offset,
                page_target_count,
                selected_count,
            )
        return selected_count

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

    def _prepare_next_batch_cursor(self, batch_selection: Dict[str, Any]) -> Dict[str, int]:
        next_row_offset = int(batch_selection["next_row_offset"])
        page_row_count = int(batch_selection["page_row_count"])
        current_page = int(batch_selection.get("end_page") or self._current_results_page_number() or 1)
        if next_row_offset < page_row_count:
            return {
                "current_page": current_page,
                "current_row_offset": next_row_offset,
            }

        if self._goto_next_results_page():
            advanced_page = self._current_results_page_number()
            return {
                "current_page": advanced_page if advanced_page > 0 else current_page + 1,
                "current_row_offset": 0,
            }
        return {
            "current_page": current_page,
            "current_row_offset": next_row_offset,
        }

    def _extract_selected_count(self, default_value: int) -> int:
        locator = self.page.locator("#selectCount").first
        if locator.count() == 0:
            return default_value
        text = locator.inner_text().replace(",", "").replace("，", "").strip()
        return int(text) if text.isdigit() else default_value

    def _count_checked_rows(self, checkbox_locator: Locator, row_offset: int, page_target_count: int) -> int:
        """统计目标区间内实际勾选数量。"""
        selected_count = 0
        for index in range(row_offset, row_offset + page_target_count):
            if self._is_checkbox_checked(checkbox_locator.nth(index)):
                selected_count += 1
        return selected_count

    def _find_unchecked_row_indexes(self, checkbox_locator: Locator, row_offset: int, page_target_count: int) -> list[int]:
        """返回目标区间内仍未勾选的下标。"""
        unchecked_indexes: list[int] = []
        for index in range(row_offset, row_offset + page_target_count):
            if not self._is_checkbox_checked(checkbox_locator.nth(index)):
                unchecked_indexes.append(index)
        return unchecked_indexes

    def _is_checkbox_checked(self, checkbox: Locator) -> bool:
        """安全读取复选框勾选状态。"""
        try:
            return bool(checkbox.is_checked())
        except Exception as exc:
            logger.debug("读取复选框勾选状态失败: %s", exc)
            return False

    def _current_results_page_number(self) -> int:
        """返回当前结果页页码。"""
        try:
            return int(self.parser.parse_results_summary()["current_page"])
        except Exception as exc:
            logger.debug("读取当前结果页页码失败: %s", exc)
            return 0
