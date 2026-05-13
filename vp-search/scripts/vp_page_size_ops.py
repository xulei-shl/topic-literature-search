"""VP 结果页每页显示数量调整逻辑。"""

import logging
import time
from typing import Optional

from playwright.sync_api import Locator

from exceptions import TimeoutError

logger = logging.getLogger("vp_search.interactor")


class VpPageSizeMixin:
    """负责切换维普结果页每页显示数量。"""

    def _prefer_results_page_size(self) -> None:
        """将结果页切换到期望的每页显示数量。"""
        page_size_link = self._find_preferred_results_page_size_link()
        if page_size_link is None:
            logger.debug("未找到每页显示数量入口，保持当前分页大小: target=%s", self.PREFERRED_RESULTS_PAGE_SIZE)
            return

        class_name = page_size_link.get_attribute("class") or ""
        if "active" in class_name:
            logger.debug("每页显示数量已是目标值，无需切换: target=%s", self.PREFERRED_RESULTS_PAGE_SIZE)
            return

        previous_page = ""
        try:
            previous_page = self.parser.parse_results_summary()["page"]
        except Exception:
            previous_page = ""
        previous_row_count = self._result_checkbox_locator().count()

        try:
            page_size_link.click()
            self._wait_for_results_page_size_applied(page_size_link, previous_page, previous_row_count)
        except Exception as exc:
            logger.debug("切换每页显示数量失败: target=%s, error=%s", self.PREFERRED_RESULTS_PAGE_SIZE, exc)

    def _find_preferred_results_page_size_link(self) -> Optional[Locator]:
        """查找“每页 50 条”入口。"""
        page_size_selectors = [
            f"#selectPageSize a[data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
            f"#selectPageSize [data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
            f"[data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
        ]
        for selector in page_size_selectors:
            locator_group = self.page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                    return locator
                except Exception:
                    continue
        return None

    def _wait_for_results_page_size_applied(
        self,
        page_size_link: Locator,
        previous_page: str,
        previous_row_count: int,
    ) -> None:
        """等待每页显示数量切换生效。"""
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            class_name = page_size_link.get_attribute("class") or ""
            if "active" in class_name:
                return

            current_row_count = self._result_checkbox_locator().count()
            if previous_row_count and current_row_count and current_row_count != previous_row_count:
                return

            try:
                current_page = self.parser.parse_results_summary()["page"]
            except Exception:
                current_page = ""
            if previous_page and current_page and current_page != previous_page:
                return

            time.sleep(self._page_change_poll_interval_seconds())

        raise TimeoutError("等待每页显示数量切换超时")

    def _current_results_page_size(self) -> int:
        """读取当前结果页激活的每页条数。"""
        selector_candidates = [
            "#selectPageSize a.active[data-count]",
            "#selectPageSize [data-count].active",
            "#selectPageSize [data-count][class*='active']",
        ]
        for selector in selector_candidates:
            locator = self.page.locator(selector).first
            try:
                if locator.count() == 0:
                    continue
                raw_count = (locator.get_attribute("data-count") or "").strip()
                if raw_count.isdigit():
                    return int(raw_count)
            except Exception:
                continue
        return self.PREFERRED_RESULTS_PAGE_SIZE
