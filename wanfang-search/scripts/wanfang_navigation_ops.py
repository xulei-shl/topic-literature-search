"""万方分页导航 Mixin。"""

import logging
import time
from typing import Optional

from playwright.sync_api import Locator

from exceptions import TimeoutError, ValidationError

logger = logging.getLogger("wanfang_search.interactor")


class WanfangNavigationMixin:
    """万方分页导航操作。"""

    def _restore_results_position(self, target_page: int) -> None:
        """恢复到需要续跑的结果页。"""
        if target_page <= 1:
            return

        current_page = int(self.parser.parse_results_summary()["current_page"])
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
            current_page = int(self.parser.parse_results_summary()["current_page"])

    def _has_results_state_changed(
        self,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        previous_sort: str,
    ) -> bool:
        """判断结果页状态是否已经发生变化。"""
        summary = self.parser.parse_results_summary()
        current_url = self.page.url
        current_page = summary["page"]
        current_title = self._first_result_title()
        current_sort = self._current_sort_text()
        changed = any(
            [
                current_url != previous_url,
                current_page != previous_page,
                current_title != previous_title,
                current_sort != previous_sort,
            ]
        )
        if changed:
            logger.debug(
                "结果页状态变化: 当前页=%s, 目标页=%s, URL变化=%s→%s",
                previous_page,
                current_page,
                previous_url[:50],
                current_url[:50],
            )
        return changed

    def _goto_next_results_page(self) -> bool:
        """点击“下一页”并等待页面跳转。"""
        previous_url = self.page.url
        previous_summary = self.parser.parse_results_summary()
        previous_page = previous_summary["page"]
        target_page = int(previous_summary["current_page"]) + 1
        previous_title = self._first_result_title()
        previous_sort = self._current_sort_text()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            next_link = self._find_next_page_link()
            if next_link is None:
                return False

            try:
                logger.debug("翻页前: 当前页=%s, 目标页=%s, URL=%s", previous_page, "next", previous_url[:80])
                self._click_next_page_link(next_link, attempt)
                self._wait_for_target_results_page(
                    target_page=target_page,
                    timeout=self._page_change_timeout_seconds(),
                )
                return True
            except Exception as exc:
                last_error = exc
                try:
                    if int(self.parser.parse_results_summary()["current_page"]) == target_page:
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
        target_page: Optional[int] = None
        try:
            page_text = page_link.inner_text().strip()
            if page_text.isdigit():
                target_page = int(page_text)
        except Exception:
            target_page = None
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            try:
                self._click_page_number_link(page_link, attempt)
                if target_page is None:
                    self._wait_for_results_changed(
                        previous_url,
                        previous_page,
                        previous_title,
                        previous_sort,
                        timeout=self._page_change_timeout_seconds(),
                    )
                else:
                    self._wait_for_target_results_page(
                        target_page=target_page,
                        timeout=self._page_change_timeout_seconds(),
                    )
                return True
            except Exception as exc:
                last_error = exc
                try:
                    if target_page is not None and int(self.parser.parse_results_summary()["current_page"]) == target_page:
                        return True
                    if target_page is None and self._has_results_state_changed(
                        previous_url,
                        previous_page,
                        previous_title,
                        previous_sort,
                    ):
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

    def _click_next_page_link(self, next_link: Locator, attempt: int) -> None:
        """执行“下一页”点击。"""
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
        """执行数字页码点击。"""
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

    def _find_next_page_link(self) -> Optional[Locator]:
        """查找“下一页”按钮。"""
        next_span = self.page.locator("span.next").first
        if next_span.count() == 0:
            return None
        try:
            style = (next_span.get_attribute("style") or "").replace(" ", "")
            if "display:none" in style:
                return None
        except Exception:
            pass
        return next_span

    def _find_resume_target_page_link(self, current_page: int, target_page: int) -> Optional[Locator]:
        """查找恢复续跑时可直接点击的目标数字页。"""
        page_links = self.page.locator("span.pager")
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

            page_number = int(text)
            if page_number <= current_page or page_number > target_page:
                continue

            class_name = (link.get_attribute("class") or "").strip()
            if "active" in class_name:
                continue

            if page_number == target_page:
                return link

            if page_number > candidate_page:
                candidate_page = page_number
                candidate_link = link

        return candidate_link
