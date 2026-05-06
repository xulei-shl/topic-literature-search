"""CNKI ?????????"""

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

class CnkiFormMixin:
    """CNKI ?????????"""

    def _fill_advanced_search_form(
        self,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
        core_only: bool,
        include_no_fulltext: bool,
    ) -> None:
        self._disable_checkbox("input[data-id='EN'][name='onlyChecked']")
        self._ensure_advanced_condition_rows(2)
        self._set_advanced_condition(0, "主题", query)
        self._set_advanced_condition(1, "篇关摘", query, logic="OR")
        if date_from:
            self._set_input_value(["input[placeholder='起始年']", "input[placeholder*='起始']"], date_from)

        if date_to:
            self._set_input_value(["input[placeholder='结束年']", "input[placeholder*='结束']"], date_to)

        if include_no_fulltext:
            self._disable_checkbox("#onlyfulltext")

        if core_only:
            self._disable_checkbox("input[name='all']")
            for selector in [
                "input[key='LYBSM'][value='P12']",
                "input[key='SI'][value='Y']",
                "input[key='EI'][value='Y']",
                "input[key='HX'][value='Y']",
                "input[key='CSI'][value='Y']",
                "input[key='CSD'][value='Y']",
                "input[key='AMI'][value='P13']",
            ]:
                self._enable_checkbox(selector)

    def _open_advanced_search_page(self) -> None:
        self.browser_manager.restore_session(self.config.home_url)
        self._ensure_captcha_cleared()

        link = self.page.locator("#highSearch").first
        if link.count() == 0:
            raise ValidationError("首页未找到“高级检索”入口")

        link.click()
        time.sleep(0.5)
        self.page.goto(self.config.advanced_search_url, timeout=self.config.navigation_timeout * 1000)
        self.page.wait_for_load_state("domcontentloaded")

        if not self._is_advanced_search_page(self.page):
            raise ValidationError("打开统一高级检索页面失败")

        self.browser_manager._page = self.page
        self.parser = ResultParser(self.page)

    def _is_advanced_search_page(self, target_page: Page) -> bool:
        selectors = ["#gradetxt", "input[placeholder='结束年']", "#onlyfulltext", "input.btn-search"]
        for selector in selectors:
            locator_group = target_page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=200)
                    return True
                except Exception:
                    continue
        return False

    def _submit_advanced_search(self) -> None:
        if not self._click_first_available(["input.btn-search", "div.search", ".btn-search"]):
            raise ValidationError("未找到高级检索提交按钮")
        self._dismiss_dialog_if_present()

    def _set_results_per_page(self, export_limit: int, total_results: int) -> None:
        if export_limit <= 20 or total_results <= 20:
            return

        try:
            dropdown = self.page.locator("#perPageDiv .sort-default").first
            if dropdown.count() == 0:
                return

            previous_rows = self.page.locator(".result-table-list tbody tr").count()
            dropdown.click()
            option = self.page.locator("#perPageDiv .sort-list li[data-val='50'] a").first
            if option.count() == 0:
                option = self.page.locator("#perPageDiv .sort-list a").filter(has_text="50").first
            if option.count() == 0:
                return

            previous_page = self.parser.parse_results_summary()["page"]
            option.click()
            self._wait_for_results_page_changed(previous_page, previous_rows)
        except Exception as exc:
            logger.debug("设置每页 50 条失败: %s", exc)

    def _disable_checkbox(self, selector: str) -> None:
        disable_checkbox(self.page, selector, logger=logger, verify_unchecked=True)

    def _enable_checkbox(self, selector: str) -> None:
        enable_checkbox(self.page, selector, logger=logger)

    def _ensure_advanced_condition_rows(self, required_count: int) -> None:
        rows = self.page.locator("#gradetxt dd")
        deadline = time.time() + self.config.page_timeout
        while rows.count() < required_count and time.time() < deadline:
            if not self._click_first_available(
                ["#gradetxt a.add-group", "#gradetxt .add-group", "a.add-group"],
                page=self.page,
            ):
                break
            time.sleep(0.3)
        if rows.count() < required_count:
            raise ValidationError("高级检索条件行不足，无法配置双条件检索")

    def _set_advanced_condition(self, row_index: int, field_title: str, query: str, logic: str = "") -> None:
        row = self.page.locator("#gradetxt dd").nth(row_index)
        if row.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条高级检索条件")

        if logic:
            self._select_dropdown_option(
                row.locator(".sort.logical").first,
                row.locator(f".sort.logical .sort-list a[value='{logic}']").first,
            )

        trigger = row.locator(".sort.reopt").first
        option = row.locator(f".sort.reopt .sort-list a[title='{field_title}']").first
        self._select_dropdown_option(trigger, option, force=True)

        query_input = row.locator(".input-box > input[type='text']").first
        if query_input.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条检索词输入框")
        query_input.fill(query)

    def _select_dropdown_option(self, trigger: Locator, option: Locator, force: bool = False) -> None:
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            try:
                trigger.wait_for(state="visible", timeout=500)
                trigger.click()
                option.wait_for(state="visible", timeout=500)
                option.click(force=force)
                return
            except Exception as exc:
                logger.debug("等待高级检索下拉项失败: %s", exc)
                time.sleep(0.2)

        raise ValidationError("高级检索下拉项不存在")

    def _set_input_value(self, selectors: list[str], value: str) -> None:
        set_input_value(self._first_visible_locator(selectors), value)
