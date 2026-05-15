"""???????????"""

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
from exceptions import CaptchaError, NavigationStateError, TimeoutError, ValidationError
from export_processor import ExportResultProcessor
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("vp_search.interactor")

class VpFormMixin:
    """???????????"""

    def _fill_advanced_search_form(
        self,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
        core_only: bool,
    ) -> None:
        self._set_advanced_condition(0, "题名或关键词", query)
        self._set_advanced_condition(1, "摘要", query, logic="OR")
        self._set_year_select_value("#basic_beginYear", date_from)
        self._set_year_select_value("#basic_endYear", date_to)

        if core_only:
            self._disable_checkbox("input[name='basic_journalRange'][title='全部期刊']")
            for title in self.CORE_JOURNAL_TITLES:
                self._enable_checkbox(f"input[name='basic_journalRange'][title='{title}']")

    def _set_advanced_condition(self, row_index: int, field_title: str, query: str, logic: str = "", exact: bool = True) -> None:
        row = self._get_advanced_condition_row(row_index)
        if exact and hasattr(row, "locator"):
            exact_dropdown = row.locator(".sel-c .layui-form-select")
            try:
                self._dismiss_layui_shade()
                exact_dropdown.click(force=True, no_wait_after=True, timeout=self._action_timeout_ms())
                time.sleep(0.3)
                exact_option = exact_dropdown.locator("dd[lay-value='1']").first
                exact_option.wait_for(state="visible", timeout=self._action_timeout_ms())
                exact_option.click(force=True, no_wait_after=True, timeout=self._action_timeout_ms())
            except Exception as exc:
                logger.debug("精确选项点击失败，尝试JS点击: %s", exc)
                try:
                    self.page.evaluate(
                        """
                        () => {
                            const dd = document.querySelector('.sel-c .layui-form-select dd[lay-value="1"]');
                            if (dd) dd.click();
                        }
                        """
                    )
                except Exception as js_exc:
                    logger.debug("JS点击精确选项也失败: %s", js_exc)
        if logic:
            self._select_dropdown_option(row, logic, display_text={"OR": "或", "AND": "与", "NOT": "非"}[logic])
        self._select_dropdown_option(row, field_title, display_text=field_title)

        query_input = self.page.locator("input[name='advSearchKeywords']").nth(row_index)
        if query_input.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条检索词输入框")
        query_input.fill(query)

    def _get_advanced_condition_row(self, row_index: int) -> Locator:
        query_input = self.page.locator("input[name='advSearchKeywords']").nth(row_index)
        if query_input.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条高级检索条件")
        for xpath in [
            "xpath=ancestor::li[1]",
            "xpath=ancestor::dd[1]",
            "xpath=ancestor::div[contains(@class,'input-group')][1]",
        ]:
            row = query_input.locator(xpath)
            if row.count() > 0:
                return row
        return query_input

    def _select_dropdown_option(self, root: Locator, option_value: str, display_text: str) -> None:
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            dropdowns = root.locator(".layui-form-select")
            for index in range(dropdowns.count()):
                dropdown = dropdowns.nth(index)
                option = dropdown.locator(f"dd[lay-value='{option_value}']").first
                if option.count() == 0:
                    option = dropdown.locator("dd").filter(has_text=display_text).first
                if option.count() == 0:
                    continue
                try:
                    self._dismiss_layui_shade()
                    dropdown.click(force=True, no_wait_after=True)
                    time.sleep(0.3)
                    option.wait_for(state="visible", timeout=self._action_timeout_ms())
                    option.click(force=True)
                    return
                except Exception as exc:
                    logger.debug("等待下拉项失败: %s", exc)
                    try:
                        dropdown.evaluate("(el) => el.click()")
                        time.sleep(0.3)
                        option.evaluate("(el) => el.click()")
                        return
                    except Exception as js_exc:
                        logger.debug("JS 下拉兜底也失败: %s", js_exc)
            time.sleep(self._action_poll_interval_seconds())
        raise ValidationError(f"高级检索下拉项不存在: {display_text}")

    def _open_advanced_search_page(self) -> None:
        if hasattr(self.browser_manager, "restore_session"):
            self.browser_manager.restore_session()
        self._dismiss_layui_shade()
        self._ensure_captcha_cleared()

        opened = self._click_first_available(
            [
                "a[href='/Qikan/Search/Advance?from=index']",
                "a[href*='/Qikan/Search/Advance']",
                "a:has-text('高级检索')",
            ]
        )
        if opened:
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=self._navigation_timeout_ms())
            except Exception as exc:
                logger.debug("点击高级检索入口后等待页面稳定失败，准备直接校验页面状态: %s", exc)
            if self._is_advanced_search_page(self.page):
                if hasattr(self.browser_manager, "_page"):
                    self.browser_manager._page = self.page
                self.parser = ResultParser(self.page)
                return

        advanced_search_url = getattr(self.config, "advanced_search_url", "")
        if not advanced_search_url or not hasattr(self.page, "goto"):
            return

        self.page.goto(advanced_search_url, timeout=self._navigation_timeout_ms())
        self.page.wait_for_load_state("domcontentloaded", timeout=self._navigation_timeout_ms())
        if not self._is_advanced_search_page(self.page):
            raise NavigationStateError("打开维普高级检索页面失败")

        if hasattr(self.browser_manager, "_page"):
            self.browser_manager._page = self.page
        self.parser = ResultParser(self.page)

    def _is_advanced_search_page(self, target_page: Page) -> bool:
        for selector in ["input[name='advSearchKeywords']", "#basic_beginYear", "#basic_endYear", ".behavior-advancesearch"]:
            locator_group = target_page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                    return True
                except Exception:
                    continue
        return False

    def _dismiss_layui_dropdowns(self) -> None:
        """点击页面左上角关闭可能残留的 layui 下拉框遮罩。"""
        try:
            self.page.locator("body").click(position={"x": 0, "y": 0}, force=True, timeout=3000)
            time.sleep(0.3)
        except Exception:
            pass

    def _submit_advanced_search(self) -> None:
        self._dismiss_layui_dropdowns()
        search_btn = self.page.locator("button.behavior-advancesearch").first
        if search_btn.count() == 0:
            if not self._click_first_available(["button.behavior-advancesearch", ".behavior-advancesearch", "button:has-text('检索')"]):
                raise ValidationError("未找到高级检索提交按钮")
            return
        try:
            search_btn.click(force=True, timeout=self._action_timeout_ms())
        except Exception:
            if not self._click_first_available(["button.behavior-advancesearch", ".behavior-advancesearch", "button:has-text('检索')"]):
                raise ValidationError("未找到高级检索提交按钮")

    def _set_native_select_value(self, selector: str, value: str) -> None:
        set_native_select_value(self.page, selector, value, ValidationError)

    def _set_year_select_value(self, selector: str, value: Optional[str]) -> None:
        """显式覆盖年份下拉，空值时保持页面当前值不变。"""
        if not value:
            return
        self._set_native_select_value(selector, value)

    def _disable_checkbox(self, selector: str) -> None:
        disable_checkbox(self.page, selector, logger=logger)

    def _enable_checkbox(self, selector: str) -> None:
        enable_checkbox(self.page, selector, logger=logger)
