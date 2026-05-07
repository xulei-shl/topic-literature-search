"""??????????"""

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

class VpNavigationMixin:
    """??????????"""

    def _restore_results_position(self, target_page: int) -> None:
        """恢复到需要续跑的结果页，优先使用页码输入框跳转。"""
        if target_page <= 1:
            return

        summary = self.parser.parse_results_summary()
        current_page = int(summary["current_page"])
        total_pages = int(summary.get("total_pages") or 0)
        if current_page > target_page:
            raise ValidationError(f"当前结果页码大于目标恢复页码: {current_page} > {target_page}")
        if total_pages and target_page > total_pages:
            raise ValidationError(f"目标恢复页码超出总页数: {target_page} > {total_pages}")

        while current_page < target_page:
            moved = self._jump_to_results_page(target_page)
            if not moved:
                page_link = self._find_resume_target_page_link(current_page=current_page, target_page=target_page)
                if page_link is not None:
                    moved = self._goto_results_page_by_link(page_link)
            if not moved:
                moved = self._goto_next_results_page()
            if not moved:
                raise ValidationError(f"未能恢复到目标页码: {target_page}")
            next_page = int(self.parser.parse_results_summary()["current_page"])
            if next_page <= current_page:
                raise ValidationError(f"恢复页码未前进: {current_page} -> {next_page}")
            current_page = next_page

    def _find_resume_skip_controls(self) -> tuple[Optional[Locator], Optional[Locator]]:
        """查找结果页“到第 X 页”输入框与确认按钮。"""
        if not self.page or not hasattr(self.page, "locator"):
            return None, None

        for pager_selector in self.RESULTS_PAGER_SELECTORS:
            input_locator = self.page.locator(f"{pager_selector} .layui-laypage-skip input.layui-input").first
            button_locator = self.page.locator(f"{pager_selector} .layui-laypage-skip .layui-laypage-btn").first
            if input_locator.count() > 0 and button_locator.count() > 0:
                return input_locator, button_locator

        input_locator = self.page.locator(".layui-laypage-skip input.layui-input").first
        button_locator = self.page.locator(".layui-laypage-skip .layui-laypage-btn").first
        if input_locator.count() > 0 and button_locator.count() > 0:
            return input_locator, button_locator
        return None, None

    def _click_results_page_link(self, page_link: Locator, attempt: int) -> None:
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
        except Exception as exc:
            logger.debug("常规点击页码失败，尝试使用 JS 点击: %s", exc)
            page_link.evaluate("(element) => element.click()")

    def _click_skip_page_button(self, button_locator: Locator, attempt: int) -> None:
        """执行结果页页码输入跳转确认。"""
        try:
            button_locator.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("跳页确认按钮滚动到可视区域失败: %s", exc)

        if attempt < self.NEXT_PAGE_MAX_RETRIES:
            button_locator.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            return

        try:
            button_locator.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        except Exception as exc:
            logger.debug("常规点击跳页确认按钮失败，尝试使用 JS 点击: %s", exc)
            button_locator.evaluate("(element) => element.click()")

    def _jump_to_results_page(self, target_page: int) -> bool:
        """通过分页输入框跳转到目标页。"""
        input_locator, button_locator = self._find_resume_skip_controls()
        if input_locator is None or button_locator is None:
            return False

        summary = self.parser.parse_results_summary()
        previous_current_page = int(summary["current_page"])
        previous_url = self.page.url
        previous_page = summary["page"]
        previous_title = self._first_result_title()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            try:
                input_locator.fill(str(target_page))
                self._click_skip_page_button(button_locator, attempt)
                self._wait_for_results_changed(previous_url, previous_page, previous_title)
                return int(self.parser.parse_results_summary()["current_page"]) > previous_current_page
            except Exception as exc:
                last_error = exc
                try:
                    if self._has_results_state_changed(previous_url, previous_page, previous_title):
                        return int(self.parser.parse_results_summary()["current_page"]) > previous_current_page
                except Exception:
                    pass
                logger.debug(
                    "结果页输入框跳转失败，准备重试: attempt=%s/%s, target_page=%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    target_page,
                    exc,
                )
                self._dismiss_confirm_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self._page_change_poll_interval_seconds())

        logger.debug("结果页输入框跳转最终失败: target_page=%s, error=%s", target_page, last_error)
        return False

    def _goto_results_page_by_link(self, page_link: Locator) -> bool:
        """点击数字页码并等待结果页完成跳转。"""
        summary = self.parser.parse_results_summary()
        previous_current_page = int(summary["current_page"])
        previous_url = self.page.url
        previous_page = summary["page"]
        previous_title = self._first_result_title()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            try:
                self._click_results_page_link(page_link, attempt)
                self._wait_for_results_changed(previous_url, previous_page, previous_title)
                return int(self.parser.parse_results_summary()["current_page"]) > previous_current_page
            except Exception as exc:
                last_error = exc
                try:
                    if self._has_results_state_changed(previous_url, previous_page, previous_title):
                        return int(self.parser.parse_results_summary()["current_page"]) > previous_current_page
                except Exception:
                    pass
                logger.debug(
                    "结果页数字页跳转失败，准备重试: attempt=%s/%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    exc,
                )
                self._dismiss_confirm_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self._page_change_poll_interval_seconds())

        logger.debug("结果页数字页跳转最终失败: error=%s", last_error)
        return False

    def _find_resume_target_page_link(self, current_page: int, target_page: int) -> Optional[Locator]:
        """查找恢复续跑时可直接点击的目标数字页。"""
        if not self.page or not hasattr(self.page, "locator"):
            return None

        candidate_link: Optional[Locator] = None
        candidate_page = current_page

        for pager_selector in self.RESULTS_PAGER_SELECTORS:
            page_links = self.page.locator(f"{pager_selector} a[data-page]")
            for index in range(page_links.count()):
                link = page_links.nth(index)
                try:
                    text = link.inner_text().strip()
                except Exception:
                    continue
                if not text.isdigit():
                    continue

                raw_page = (link.get_attribute("data-page") or text).strip()
                if not raw_page.isdigit():
                    continue

                page_number = int(raw_page)
                if page_number <= current_page or page_number > target_page:
                    continue

                class_name = (link.get_attribute("class") or "").strip()
                if "layui-disabled" in class_name or "disabled" in class_name or "layui-laypage-curr" in class_name:
                    continue

                if page_number == target_page:
                    return link

                if page_number > candidate_page:
                    candidate_page = page_number
                    candidate_link = link

        return candidate_link

    def _click_next_page_link(self, next_link: Locator, attempt: int) -> None:
        try:
            next_link.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("翻页按钮滚动到可视区域失败: %s", exc)

        if attempt < self.NEXT_PAGE_MAX_RETRIES:
            next_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            return
        try:
            next_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        except Exception as exc:
            logger.debug("常规点击“下一页”失败，尝试使用 JS 点击: %s", exc)
            next_link.evaluate("(element) => element.click()")

    def _goto_next_results_page(self) -> bool:
        navigation_started_at = time.perf_counter()
        previous_url = self.page.url
        summary = self.parser.parse_results_summary()
        previous_page = summary["page"]
        previous_current_page = int(summary.get("current_page") or 1)
        previous_title = self._first_result_title()
        setattr(self, "_last_results_title_before_page_turn", previous_title)
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            next_link = self._find_next_page_link()
            if next_link is None:
                logger.debug("未找到下一页控件: previous_page=%s, previous_url=%s", previous_page, previous_url)
                return False

            class_name = next_link.get_attribute("class") or ""
            data_page = next_link.get_attribute("data-page") or ""
            target_page = int(data_page) if data_page.isdigit() else (previous_current_page + 1)
            try:
                next_text = next_link.inner_text().strip()
            except Exception:
                next_text = ""
            logger.debug(
                "准备执行结果页翻页: attempt=%s/%s, previous_page=%s, next_text=%s, data_page=%s, class=%s",
                attempt,
                self.NEXT_PAGE_MAX_RETRIES,
                previous_page,
                next_text,
                data_page,
                class_name,
            )
            if "layui-disabled" in class_name or "disabled" in class_name:
                logger.debug("下一页控件处于禁用状态，停止翻页: previous_page=%s", previous_page)
                return False

            try:
                self._click_next_page_link(next_link, attempt)
                current_summary = self._wait_for_results_page_advanced(
                    previous_current_page=previous_current_page,
                    target_page=target_page,
                    previous_url=previous_url,
                    previous_page=previous_page,
                    previous_title=previous_title,
                )
                if current_summary is None:
                    current_summary = self.parser.parse_results_summary()
                current_page = current_summary["page"]
                current_current_page = int(current_summary.get("current_page") or 0)
                self._mark_known_results_page(max(current_current_page, target_page))
                logger.debug(
                    "结果页翻页完成: previous_page=%s, current_page=%s, target_page=%s, elapsed_ms=%s",
                    previous_page,
                    current_page,
                    target_page,
                    int((time.perf_counter() - navigation_started_at) * 1000),
                )
                if current_current_page < target_page:
                    raise TimeoutError(f"结果页页码未推进到目标页: current={current_current_page}, target={target_page}")
                return True
            except Exception as exc:
                last_error = exc
                try:
                    if self._is_results_page_advanced(previous_current_page=previous_current_page, target_page=target_page):
                        return True
                except Exception:
                    pass
                logger.warning(
                    "结果页翻页失败，准备重试: attempt=%s/%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    exc,
                )
                self._dismiss_confirm_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self._page_change_poll_interval_seconds())
        raise TimeoutError(f"结果页翻页失败，已重试 {self.NEXT_PAGE_MAX_RETRIES} 次: {last_error}")

    def _has_results_state_changed(self, previous_url: str, previous_page: str, previous_title: str) -> bool:
        current_url = self.page.url
        current_page = self.parser.parse_results_summary()["page"]
        current_title = self._first_result_title()
        return any([current_url != previous_url, current_page != previous_page, current_title != previous_title])

    def _is_results_page_advanced(self, previous_current_page: int, target_page: int) -> bool:
        """判断结果页页码是否已经推进到目标页。"""
        try:
            current_current_page = int(self.parser.parse_results_summary().get("current_page") or 0)
        except Exception:
            return False
        if current_current_page >= target_page and current_current_page > previous_current_page:
            self._mark_known_results_page(current_current_page)
            return True
        return current_current_page >= target_page and current_current_page > previous_current_page

    def _wait_for_results_page_advanced(
        self,
        previous_current_page: int,
        target_page: int,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """等待结果页页码真正推进到目标页。"""
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        logger.debug(
            "开始等待结果页翻页完成: previous_page=%s, target_page=%s, previous_url=%s",
            previous_page,
            target_page,
            previous_url,
        )
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            try:
                current_summary = self.parser.parse_results_summary()
                current_page = current_summary["page"]
                current_current_page = int(current_summary.get("current_page") or 0)
                if current_current_page >= target_page and current_current_page > previous_current_page:
                    self._mark_known_results_page(current_current_page)
                    logger.debug(
                        "检测到结果页页码已推进: previous_page=%s, current_page=%s, target_page=%s",
                        previous_page,
                        current_page,
                        target_page,
                    )
                    return current_summary
                if self.page.url != previous_url or current_page != previous_page:
                    logger.debug(
                        "检测到结果页状态变化但页码未推进，继续等待: previous_page=%s, current_page=%s, target_page=%s",
                        previous_page,
                        current_page,
                        target_page,
                    )
                else:
                    current_title = self._first_result_title()
                    if current_title != previous_title:
                        logger.debug(
                            "检测到结果首条已变化但页码未推进，继续等待: previous_page=%s, current_page=%s, target_page=%s",
                            previous_page,
                            current_page,
                            target_page,
                        )
            except Exception as exc:
                logger.debug("等待结果页翻页完成时状态刷新中，继续等待: %s", exc)
            time.sleep(self._page_change_poll_interval_seconds())
        try:
            current_page = self.parser.parse_results_summary()["page"]
        except Exception:
            current_page = ""
        raise TimeoutError(f"等待翻页完成超时: previous_page={previous_page}, current_page={current_page}, target_page={target_page}")

    def _mark_known_results_page(self, page_number: int) -> None:
        """记录最近一次已确认推进到的结果页码。"""
        if page_number > 0:
            setattr(self, "_known_results_page", page_number)

    def _wait_for_results_changed(
        self,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        timeout: Optional[float] = None,
    ) -> None:
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        logger.debug(
            "开始等待结果页变化: previous_page=%s, previous_url=%s, previous_title=%s",
            previous_page,
            previous_url,
            previous_title,
        )
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            try:
                if self._has_results_state_changed(previous_url, previous_page, previous_title):
                    try:
                        current_page = self.parser.parse_results_summary()["page"]
                    except Exception:
                        current_page = ""
                    logger.debug("检测到结果页变化: previous_page=%s, current_page=%s", previous_page, current_page)
                    return
            except Exception as exc:
                logger.debug("结果页状态刷新中，继续等待: %s", exc)
            time.sleep(self._page_change_poll_interval_seconds())
        try:
            current_page = self.parser.parse_results_summary()["page"]
        except Exception:
            current_page = ""
        logger.debug(
            "等待结果页变化超时: previous_page=%s, current_page=%s, current_url=%s",
            previous_page,
            current_page,
            self.page.url,
        )
        raise TimeoutError("等待翻页完成超时")

    def _find_next_page_link(self) -> Optional[Locator]:
        pager_selectors = []
        for pager_selector in self.RESULTS_PAGER_SELECTORS:
            pager_selectors.extend(
                [
                    f"{pager_selector} .layui-laypage-next",
                    f"{pager_selector} a[data-page]",
                    f"{pager_selector} a",
                    f"{pager_selector} button",
                    f"{pager_selector} span",
                ]
            )
        pager_selectors.extend(["a.layui-laypage-next", "a", "button", "span"])

        for selector in pager_selectors:
            locator_group = self.page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    text = locator.inner_text().strip()
                except Exception:
                    text = ""
                if selector.endswith("a[data-page]") and text and text.isdigit():
                    continue
                if not self._is_next_page_control(selector=selector, text=text):
                    continue
                class_name = (locator.get_attribute("class") or "").strip()
                if "layui-disabled" in class_name or "disabled" in class_name:
                    continue
                return locator
        return None

    def _is_next_page_control(self, selector: str, text: str) -> bool:
        """判断候选节点是否为“下一页”控件。"""
        if selector.endswith(".layui-laypage-next"):
            return True

        normalized_text = (text or "").replace(" ", "").strip()
        if normalized_text in {"下一页", "下页", ">", ">>"}:
            return True
        return "下一页" in normalized_text
