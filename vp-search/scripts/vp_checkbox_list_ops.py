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

class VpCheckboxListMixin:
    """????????????"""

    def _result_checkbox_locator(self) -> Locator:
        for selector in self.RESULT_CHECKBOX_SELECTORS:
            locator = self.page.locator(selector)
            if locator.count() > 0:
                return locator
        return self.page.locator("input[name='selectArticle']")

    def _current_page_checkbox_items(self) -> list[Locator]:
        """返回当前页可操作的结果复选框列表。"""
        checkbox_locator = self._result_checkbox_locator()
        total_count = checkbox_locator.count()
        summary = self.parser.parse_results_summary()
        try:
            current_page_number = int(summary.get("current_page") or 1)
        except Exception:
            current_page_number = 1
        try:
            total_pages = int(summary.get("total_pages") or 0)
        except Exception:
            total_pages = 0
        page_size = self._current_results_page_size()
        paged_items = self._slice_checkbox_items_by_current_page(
            checkbox_locator=checkbox_locator,
            total_count=total_count,
            current_page=current_page_number,
            page_size=page_size,
        )
        if paged_items:
            # 后续页完整切到一整页时，优先直接复用，避免逐项探测带来额外耗时。
            if current_page_number > 1 and len(paged_items) == page_size:
                logger.debug(
                    "获取当前页复选框: total_count=%s, filtered_count=%s, current_page=%s, page_size=%s",
                    total_count,
                    len(paged_items),
                    current_page_number,
                    page_size,
                )
                logger.debug("按当前页码切分复选框成功: total_count=%s, page_items=%s", total_count, len(paged_items))
                return paged_items
            filtered_items = self._filter_result_row_checkbox_items(paged_items)
            if self._is_reliable_page_slice(
                filtered_items=filtered_items,
                current_page=current_page_number,
                total_pages=total_pages,
                page_size=page_size,
            ):
                logger.debug(
                    "获取当前页复选框: total_count=%s, filtered_count=%s, current_page=%s, page_size=%s",
                    total_count,
                    len(filtered_items),
                    current_page_number,
                    page_size,
                )
                logger.debug("按当前页码切分复选框成功: total_count=%s, page_items=%s", total_count, len(filtered_items))
                return filtered_items
            logger.debug(
                "按页码切分结果不可信，准备回退可见性筛选: current_page=%s, total_pages=%s, page_size=%s, sliced_count=%s, filtered_count=%s",
                current_page_number,
                total_pages,
                page_size,
                len(paged_items),
                len(filtered_items),
            )

        visible_items = self._collect_visible_result_row_checkboxes(
            checkbox_locator=checkbox_locator,
            total_count=total_count,
            page_size=page_size,
        )
        logger.debug("筛选当前页可见复选框: total_count=%s, visible_count=%s", total_count, len(visible_items))
        return visible_items

    def _is_reliable_page_slice(
        self,
        filtered_items: list[Locator],
        current_page: int,
        total_pages: int,
        page_size: int,
    ) -> bool:
        """判断按页码切出的候选集合是否足够可信。"""
        if not filtered_items:
            return False
        if len(filtered_items) >= page_size:
            return True
        if total_pages > 0 and current_page >= total_pages:
            return True
        return False

    def _collect_visible_result_row_checkboxes(
        self,
        checkbox_locator: Locator,
        total_count: int,
        page_size: int,
    ) -> list[Locator]:
        """回退到可见性扫描，找到当前页结果后立即停止。"""
        visible_items: list[Locator] = []
        for index in range(total_count):
            candidate = checkbox_locator.nth(index)
            if not self._is_result_row_checkbox(candidate):
                continue
            click_target = self._resolve_checkbox_click_target(candidate)
            if click_target is not None:
                if self._is_locator_visible(click_target):
                    visible_items.append(candidate)
            elif self._is_locator_visible(candidate):
                visible_items.append(candidate)
            if page_size > 0 and len(visible_items) >= page_size:
                break
        return visible_items

    def _filter_result_row_checkboxes(self, checkbox_locator: Locator, total_count: int) -> list[Locator]:
        """过滤掉页级全选等非结果行复选框。"""
        checkbox_items = [checkbox_locator.nth(index) for index in range(total_count)]
        return self._filter_result_row_checkbox_items(checkbox_items)

    def _filter_result_row_checkbox_items(self, checkbox_items: list[Locator]) -> list[Locator]:
        """过滤当前候选集合中的页级全选控件。"""
        filtered_items: list[Locator] = []
        skipped_indices: list[int] = []
        for index, checkbox in enumerate(checkbox_items):
            if self._is_result_row_checkbox(checkbox):
                filtered_items.append(checkbox)
                continue
            skipped_indices.append(index)
        if skipped_indices:
            logger.debug(
                "跳过非结果行复选框: skipped_count=%s, sample_indices=%s",
                len(skipped_indices),
                skipped_indices[:5],
            )
        return filtered_items

    def _is_result_row_checkbox(self, checkbox: Locator) -> bool:
        """判断复选框是否属于结果行，而非页级全选。"""
        try:
            name = (checkbox.get_attribute("name") or "").strip()
        except Exception:
            name = ""
        try:
            data_name = (checkbox.get_attribute("data-name") or "").strip()
        except Exception:
            data_name = ""

        if name in self.RESULT_SELECT_ALL_NAMES or data_name in self.RESULT_SELECT_ALL_NAMES:
            return False
        if name in self.RESULT_ROW_CHECKBOX_NAMES or data_name in self.RESULT_ROW_CHECKBOX_NAMES:
            return True
        return True

    def _slice_checkbox_items_by_current_page(
        self,
        checkbox_locator: Locator,
        total_count: int,
        current_page: int,
        page_size: int,
    ) -> list[Locator]:
        """按当前页码与每页条数切出当前页复选框。"""
        if total_count <= 0:
            return []
        if page_size <= 0 or total_count <= page_size:
            return []

        start_index = max((current_page - 1) * page_size, 0)
        end_index = min(start_index + page_size, total_count)
        if start_index >= total_count or end_index <= start_index:
            logger.debug(
                "按页码切分复选框失败，准备回退可见性筛选: current_page=%s, page_size=%s, total_count=%s",
                current_page,
                page_size,
                total_count,
            )
            return []

        return [checkbox_locator.nth(index) for index in range(start_index, end_index)]

    def _is_locator_visible(self, locator: Optional[Locator]) -> bool:
        """判断定位器是否处于可见状态。"""
        if locator is None:
            return False
        try:
            if locator.count() == 0:
                return False
        except Exception:
            return False
        try:
            return bool(locator.is_visible())
        except Exception:
            return False

    def _resolve_checkbox_click_target(self, checkbox: Locator) -> Optional[Locator]:
        for selector in [
            "xpath=following-sibling::div[contains(@class,'layui-form-checkbox')][1]",
            "xpath=../div[contains(@class,'layui-form-checkbox')][1]",
            "xpath=ancestor::dd[1]/dd[2]/div[contains(@class,'layui-form-checkbox')][1]",
            ".layui-form-checkbox",
        ]:
            try:
                target = checkbox.locator(selector).first
                if target.count() > 0:
                    return target
            except Exception:
                continue
        try:
            parent_dd = checkbox.locator("xpath=ancestor::dd[contains(@class,'sel')][1]").first
            if parent_dd.count() > 0:
                target = parent_dd.locator("div.layui-form-checkbox").first
                if target.count() > 0:
                    return target
        except Exception:
            pass
        return None

    def _is_checkbox_checked(self, checkbox: Locator, click_target: Optional[Locator] = None) -> bool:
        try:
            if checkbox.is_checked():
                return True
        except Exception:
            pass

        target = click_target or self._resolve_checkbox_click_target(checkbox)
        if target is None:
            return False

        try:
            class_name = target.get_attribute("class") or ""
        except Exception:
            return False
        return "layui-form-checked" in class_name or "layui-this" in class_name
