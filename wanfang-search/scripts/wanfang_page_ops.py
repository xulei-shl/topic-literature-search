"""万方页面等待与工具 Mixin。"""

import logging
import time
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page

from src.utils.playwright_page import click_first_available, first_visible_locator, wait_for_any_selector

from exceptions import CaptchaError, TimeoutError, ValidationError

logger = logging.getLogger("wanfang_search.interactor")


class WanfangPageMixin:
    """万方页面工具操作。"""

    RESULTS_READY_SELECTORS = (
        "span.total-number",
        "span.total-number span.mark-number",
    )

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
        """等待万方结果页完成加载。"""
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()

            if self._has_results_ready_marker():
                return

            time.sleep(self._page_poll_interval_seconds())

        raise TimeoutError("等待万方结果摘要超时")

    def _wait_for_results_changed(
        self,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        previous_sort: str,
        timeout: Optional[float] = None,
    ) -> None:
        """等待结果页状态变化。"""
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        while time.time() < deadline:
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()

            try:
                if self._has_results_state_changed(previous_url, previous_page, previous_title, previous_sort):
                    return
            except Exception as exc:
                logger.debug("结果页状态刷新中，继续等待: %s", exc)
            time.sleep(self._page_change_poll_interval_seconds())

        raise TimeoutError("等待翻页完成超时")

    def _wait_for_results_page_changed(self, previous_page: str, previous_rows: int) -> None:
        """等待结果页分页状态变化。"""
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            try:
                current_page = self.parser.parse_results_summary()["page"]
                current_rows = self.page.locator("div.normal-list").count()
                if current_page != previous_page or current_rows != previous_rows:
                    return
            except Exception as exc:
                logger.debug("等待结果页分页状态变化时遇到瞬时异常: %s", exc)
            time.sleep(self._page_change_poll_interval_seconds())

    def _wait_for_target_results_page(self, target_page: int, timeout: Optional[float] = None) -> None:
        """等待结果页推进到目标页码。"""
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        while time.time() < deadline:
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()

            try:
                current_page = int(self.parser.parse_results_summary()["current_page"])
            except Exception as exc:
                logger.debug("等待目标结果页时状态刷新中，继续等待: %s", exc)
            else:
                if current_page == target_page:
                    return
                if current_page > target_page:
                    raise TimeoutError(f"结果页已越过目标页码: {current_page} > {target_page}")

            time.sleep(self._page_change_poll_interval_seconds())

        raise TimeoutError(f"等待结果页翻到目标页超时: {target_page}")

    def _wait_for_results_page_size_applied(self, expected_count: int, timeout: Optional[float] = None) -> None:
        """等待每页显示条数切换完成。"""
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        target_text = f"显示 {expected_count} 条"

        while time.time() < deadline:
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()

            try:
                current_text = self._read_results_page_size_text()
                if target_text in current_text:
                    return
            except Exception as exc:
                logger.debug("等待每页显示条数切换时状态刷新中，继续等待: %s", exc)

            time.sleep(self._page_change_poll_interval_seconds())

        raise TimeoutError(f"等待每页显示条数切换超时: {expected_count}")

    def _read_results_page_size_text(self) -> str:
        """读取结果页当前每页显示条数文本。"""
        text_selectors = (
            "div.wf-select.right-items span.cur-text",
            "div.select-box span.cur-text",
        )
        for selector in text_selectors:
            locator = self.page.locator(selector).first
            try:
                if locator.count() == 0:
                    continue
                text = (locator.inner_text() or "").strip()
            except Exception:
                continue
            if text:
                return text
        return ""

    def _dismiss_dialog_if_present(self) -> None:
        """关闭可能出现的弹窗。"""
        dialog_selectors = [
            "div.ivu-modal-confirm",
            "div.layui-layer-dialog",
            "div.modal-dialog",
        ]
        for selector in dialog_selectors:
            dialog = self.page.locator(selector).first
            if dialog.count() == 0:
                continue
            confirm = self.page.locator(f"{selector} .ivu-btn-primary, {selector} .layui-layer-btn0, {selector} .btn-primary").first
            if confirm.count() > 0:
                confirm.click()
                return

    def _ensure_captcha_cleared(self) -> None:
        """确保万方验证码已处理。"""
        if not self.browser_manager.is_captcha_visible(self.page):
            return
        if not self.browser_manager.wait_for_captcha_completion(self.page):
            raise CaptchaError("验证码尚未完成，请手动完成后重试")

    def _first_result_title(self) -> str:
        """获取首条结果标题，用于状态变化检测。"""
        locator = self.page.locator("div.normal-list a.title").first
        if locator.count() == 0:
            return ""
        return locator.inner_text().strip()

    def _current_sort_text(self) -> str:
        """获取当前排序文本。"""
        locator = self.page.locator("span.sort-item.active, li.sort-item.active").first
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
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return max(int(timeout_seconds * 1000), 1000)

    def _page_change_timeout_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return max(float(timeout_seconds), 1.0)

    def _page_poll_interval_seconds(self) -> float:
        timeout_seconds = max(float(self.config.page_timeout), 1.0)
        return min(0.5, max(timeout_seconds / 100, 0.2))

    def _page_change_poll_interval_seconds(self) -> float:
        timeout_seconds = self._page_change_timeout_seconds()
        return min(1.0, max(timeout_seconds / 120, 0.2))

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

    def _make_parser(self, page: Page):
        """创建结果解析器。"""
        from result_parser import ResultParser
        return ResultParser(page)

    def _has_results_ready_marker(self) -> bool:
        """判断结果摘要是否已出现。"""
        for selector in self.RESULTS_READY_SELECTORS:
            if self.page.locator(selector).count() > 0:
                return True
        return False
