"""维普结果解析。"""

import re
from typing import Any, Dict

from playwright.sync_api import Page

from exceptions import UnsupportedPageError


class ResultParser:
    """负责解析维普结果页概要信息。"""

    PAGER_SELECTORS = ("#headerpager", "#footerpager")

    def __init__(self, page: Page):
        self.page = page

    def detect_page_type(self) -> str:
        """识别当前页面类型。

        Returns:
            str: 页面类型。
        """
        if any(self.page.query_selector(selector) for selector in self.PAGER_SELECTORS) or self.page.query_selector(".search-result"):
            return "results"
        if self.page.query_selector("input[name='advSearchKeywords']") or "Advance" in (self.page.url or ""):
            return "advanced_search"
        if "cqvip.com" in (self.page.url or ""):
            return "vp"
        return "unknown"

    def parse_results_summary(self) -> Dict[str, Any]:
        """提取结果页概要信息。

        Returns:
            Dict[str, Any]: 结果概要。

        Raises:
            UnsupportedPageError: 当前页面不是结果页时抛出。
        """
        if self.detect_page_type() != "results":
            raise UnsupportedPageError("当前页面不是维普搜索结果页")

        current_page, total_pages = self._extract_page_numbers()
        return {
            "query": "",
            "total": self._extract_total(),
            "page": f"{current_page}/{total_pages}",
            "current_page": current_page,
            "total_pages": total_pages,
            "has_next_page": self._has_next_page(current_page, total_pages),
            "url": self.page.url,
        }

    def _extract_total(self) -> int:
        total_value = self.page.locator("#hidShowTotalCount").first
        if total_value.count() > 0:
            value = (total_value.get_attribute("value") or "").replace(",", "").strip()
            if value.isdigit():
                return int(value)

        text = self._safe_text(self.page.query_selector(".search-result"))
        match = re.search(r"([\d,]+)", text)
        if not match:
            return 0
        return int(match.group(1).replace(",", ""))

    def _extract_page_numbers(self) -> tuple[int, int]:
        current_candidates: list[int] = []
        for pager_selector in self.PAGER_SELECTORS:
            current_text = self._safe_text(self.page.query_selector(f"{pager_selector} .layui-laypage-curr"))
            current_matches = re.findall(r"\d+", current_text)
            if current_matches:
                current_candidates.append(int(current_matches[-1]))
        current_page = max(current_candidates) if current_candidates else 1

        total_candidates: list[int] = []
        for pager_selector in self.PAGER_SELECTORS:
            last_page_locator = self.page.locator(f"{pager_selector} .layui-laypage-last").first
            if last_page_locator.count() == 0:
                continue
            last_page = (last_page_locator.get_attribute("data-page") or "").strip()
            if last_page.isdigit():
                total_candidates.append(int(last_page))
        if total_candidates:
            return current_page, max(total_candidates)

        last_number = current_page
        for pager_selector in self.PAGER_SELECTORS:
            page_links = self.page.locator(f"{pager_selector} a[data-page]")
            for index in range(page_links.count()):
                raw_value = (page_links.nth(index).get_attribute("data-page") or "").strip()
                if raw_value.isdigit():
                    last_number = max(last_number, int(raw_value))
        return current_page, last_number

    def _has_next_page(self, current_page: int, total_pages: int) -> bool:
        found_next_control = False
        for pager_selector in self.PAGER_SELECTORS:
            next_locator = self.page.locator(f"{pager_selector} .layui-laypage-next").first
            if next_locator.count() == 0:
                continue
            found_next_control = True
            class_name = next_locator.get_attribute("class") or ""
            if "layui-disabled" not in class_name:
                return True
        if found_next_control:
            return False
        return current_page < total_pages

    def _safe_text(self, element) -> str:
        if not element:
            return ""
        try:
            value = element.evaluate("(node) => node.value !== undefined ? node.value : null")
            if value is not None:
                return (value or "").strip()
        except Exception:
            pass
        try:
            return (element.inner_text() or "").strip()
        except Exception:
            return ""
