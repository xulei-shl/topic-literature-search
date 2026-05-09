"""万方结果解析。"""

import re
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page

from exceptions import ParseError, UnsupportedPageError


class ResultParser:
    """负责解析万方结果页。"""

    def __init__(self, page: Page):
        self.page = page

    def detect_page_type(self) -> str:
        """识别当前页面类型。"""
        url = self.page.url or ""
        if self.page.query_selector("span.total-number") or self.page.query_selector("div.tip-content"):
            return "results"
        if "advanced-search" in url or self.page.query_selector("input.ivu-input.ivu-input-default"):
            return "advanced_search"
        if "wanfangdata" in url:
            return "wanfang"
        return "unknown"

    def parse_results(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """解析当前结果页。"""
        if self.detect_page_type() != "results":
            raise UnsupportedPageError("当前页面不是万方搜索结果页")

        summary = self.parse_results_summary()
        results: List[Dict[str, Any]] = []

        result_items = self.page.query_selector_all("div.normal-list")
        for index, item in enumerate(result_items, start=1):
            title_link = item.query_selector("a.title")
            if not title_link:
                continue

            authors = [
                self._safe_text(author)
                for author in item.query_selector_all("span.author a")
                if self._safe_text(author)
            ]
            results.append(
                {
                    "n": index,
                    "title": self._safe_text(title_link),
                    "href": title_link.get_attribute("href") or "",
                    "authors": authors,
                    "journal": self._safe_text(item.query_selector("span.journal")),
                    "date": self._safe_text(item.query_selector("span.date")),
                    "abstract": self._safe_text(item.query_selector("p.abstract")),
                }
            )

        if limit is not None:
            results = results[:limit]

        return {
            "result_type": "search_results",
            "query": summary["query"],
            "total": summary["total"],
            "page": summary["page"],
            "current_page": summary["current_page"],
            "total_pages": summary["total_pages"],
            "has_next_page": summary["has_next_page"],
            "url": summary["url"],
            "results": results,
        }

    def parse_results_summary(self) -> Dict[str, Any]:
        """提取结果页概要信息。"""
        if self.detect_page_type() != "results":
            raise UnsupportedPageError("当前页面不是万方搜索结果页")

        total = self._extract_total()
        current_page, total_pages = self._extract_page_numbers()

        return {
            "query": self._extract_query_text(),
            "total": total,
            "page": f"{current_page}/{total_pages}",
            "current_page": current_page,
            "total_pages": total_pages,
            "has_next_page": current_page < total_pages,
            "url": self.page.url,
        }

    def _extract_total(self) -> int:
        text = self._safe_text(self.page.query_selector("span.total-number span.mark-number"))
        if not text:
            return 0
        match = re.search(r"([\d,]+)", text)
        if not match:
            return 0
        return int(match.group(1).replace(",", ""))

    def _extract_page_numbers(self) -> tuple[int, int]:
        current_page = 1
        total_pages = 1

        current_locator = self.page.query_selector("span.currentpage")
        if current_locator:
            text = self._safe_text(current_locator).strip().strip("/")
            try:
                current_page = int(text)
            except ValueError:
                pass

        page_number_locator = self.page.query_selector("span.page-number")
        if page_number_locator:
            full_text = self._safe_text(page_number_locator)
            match = re.search(r"(\d+)\s*/\s*(\d+)", full_text)
            if not match:
                match = re.search(r"/\s*(\d+)", full_text)
                if match:
                    total_pages = int(match.group(1))
            else:
                current_page = int(match.group(1))
                total_pages = int(match.group(2))

        return current_page, total_pages

    def _extract_query_text(self) -> str:
        input_locator = self.page.query_selector("input.ivu-input.ivu-input-default")
        if input_locator:
            return self._safe_text(input_locator)
        return ""

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
