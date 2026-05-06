"""CNKI ??????????"""

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

class CnkiPublicMixin:
    """CNKI ??????????"""

    def search(self, query: str, num_results: Optional[int] = None) -> Dict[str, Any]:
        """基础关键词检索。"""
        if not query.strip():
            raise ValidationError("检索词不能为空")

        limit = self._normalize_limit(num_results)
        self.browser_manager.restore_session(self.config.home_url)
        self._wait_for_any_selector(["textarea.search-input", "input.search-input", "#txt_SearchText"])
        self._ensure_captcha_cleared()

        self._first_visible_locator(["textarea.search-input", "input.search-input", "#txt_SearchText"]).fill(query.strip())
        self._click_first_available(["input.search-btn", "button.search-btn", ".search-btn"])

        self._wait_for_results_ready()
        results = self.parser.parse_results(limit=limit)
        results["command"] = "search"

        self.browser_manager.save_session(
            page_type="results",
            last_query=query,
            last_results_url=self.page.url,
            last_result_command="search",
        )
        return results

    def parse_current_results(self, url: Optional[str] = None, num_results: Optional[int] = None) -> Dict[str, Any]:
        """重新解析结果页。"""
        limit = self._normalize_limit(num_results)
        target_url = url or self._resolve_results_url()
        self.browser_manager.restore_session(target_url)
        self._wait_for_results_ready()

        results = self.parser.parse_results(limit=limit)
        results["command"] = "parse_page"
        self.browser_manager.save_session(
            page_type="results",
            last_results_url=self.page.url,
        )
        return results

    def navigate_results(
        self,
        action: str,
        value: Optional[str] = None,
        num_results: Optional[int] = None,
    ) -> Dict[str, Any]:
        """翻页或排序。"""
        limit = self._normalize_limit(num_results)
        self.browser_manager.restore_session(self._resolve_results_url())
        self._wait_for_results_ready()

        previous_url = self.page.url
        previous_page = self.parser.parse_results_summary()["page"]
        previous_title = self._first_result_title()
        previous_sort = self._current_sort_text()

        if action == "sort":
            sort_key = (value or "").strip().lower()
            sort_id = self.SORT_ID_MAP.get(sort_key)
            if not sort_id:
                raise ValidationError("排序方式仅支持 relevance/date/citations/downloads/comprehensive")
            self.page.locator(f"#orderList li#{sort_id}").first.click()
        elif action in {"next", "previous"}:
            target_text = "下一页" if action == "next" else "上一页"
            target_link = self.page.locator(".pages a").filter(has_text=target_text).first
            if target_link.count() == 0:
                raise NavigationStateError(f"当前页面无法执行 {action}")
            target_link.click()
        elif action == "page":
            if not value or not value.isdigit():
                raise ValidationError("page 操作需要提供数字页码")
            target_link = self.page.locator(".pages a").filter(has_text=value).first
            if target_link.count() == 0:
                raise NavigationStateError(f"当前结果页中未找到页码 {value}")
            target_link.click()
        else:
            raise ValidationError("navigate 仅支持 next/previous/page/sort")

        self._wait_for_results_changed(previous_url, previous_page, previous_title, previous_sort)
        results = self.parser.parse_results(limit=limit)
        results["command"] = "navigate"
        results["navigate"] = {"action": action, "value": value or ""}

        self.browser_manager.save_session(
            page_type="results",
            last_results_url=self.page.url,
        )
        return results

    def get_paper_detail(self, url: Optional[str] = None, index: Optional[int] = None) -> Dict[str, Any]:
        """提取论文详情。"""
        target_url = url or self._resolve_detail_url(index)
        self.browser_manager.restore_session(target_url)
        self._wait_for_any_selector([".brief h1"])
        self._ensure_captcha_cleared()

        detail = self.parser.parse_paper_detail()
        self.browser_manager.save_session(
            page_type="detail",
            last_detail_url=self.page.url,
        )
        return detail

    def _resolve_detail_url(self, index: Optional[int]) -> str:
        if index is not None:
            if index <= 0:
                raise ValidationError("论文序号必须大于 0")
            page_data = self.parse_current_results(num_results=self.config.max_results)
            for item in page_data["results"]:
                if item["n"] == index:
                    return item["href"]
            raise NavigationStateError(f"当前结果页不存在第 {index} 条论文")

        state = self.browser_manager.read_state()
        last_detail_url = state.get("last_detail_url")
        if last_detail_url:
            return last_detail_url
        raise NavigationStateError("未提供论文 URL，且当前没有可复用的详情页记录")

    def _resolve_results_url(self) -> str:
        state = self.browser_manager.read_state()
        last_results_url = state.get("last_results_url")
        if last_results_url:
            return last_results_url
        raise NavigationStateError("当前没有可复用的搜索结果页，请先执行 search 或 advanced-search")
