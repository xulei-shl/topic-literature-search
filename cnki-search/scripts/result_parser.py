"""CNKI 结果解析。"""

import re
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page

from exceptions import ParseError, UnsupportedPageError


class ResultParser:
    """负责解析结果页与详情页。"""

    def __init__(self, page: Page):
        self.page = page

    def detect_page_type(self) -> str:
        """识别当前页面类型。"""
        url = self.page.url or ""
        if self.page.query_selector(".brief h1"):
            return "detail"
        if (
            self.page.query_selector(".result-table-list")
            or self.page.query_selector(".pagerTitleCell")
            or self.page.query_selector("#ModuleSearchResult .no-content")
        ):
            return "results"
        if "AdvSearch" in url or self.page.query_selector("#txt_1_value1"):
            return "advanced_search"
        if "cnki.net" in url:
            return "cnki"
        return "unknown"

    def parse_results(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """解析当前结果页。"""
        if self.detect_page_type() != "results":
            raise UnsupportedPageError("当前页面不是 CNKI 搜索结果页")

        rows = self.page.query_selector_all(".result-table-list tbody tr")
        checkboxes = self.page.query_selector_all(".result-table-list tbody input.cbItem")
        results: List[Dict[str, Any]] = []
        summary = self.parse_results_summary()

        for index, row in enumerate(rows, start=1):
            title_link = row.query_selector("td.name a.fz14")
            if not title_link:
                continue

            authors = [
                self._safe_text(author)
                for author in row.query_selector_all("td.author a.KnowledgeNetLink")
                if self._safe_text(author)
            ]
            results.append(
                {
                    "n": index,
                    "title": self._safe_text(title_link),
                    "href": title_link.get_attribute("href") or "",
                    "export_id": checkboxes[index - 1].get_attribute("value") if len(checkboxes) >= index else "",
                    "authors": authors,
                    "journal": self._extract_source_text(row),
                    "date": self._safe_text(row.query_selector("td.date")),
                    "database": self._safe_text(row.query_selector("td.data span")) or self._safe_text(row.query_selector("td.data")),
                    "citations": self._safe_text(row.query_selector("td.quote")),
                    "downloads": self._safe_text(row.query_selector("td.download")),
                    "is_online_first": row.query_selector("td.name .marktip") is not None,
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
            "sort_by": self._safe_text(self.page.query_selector("#orderList li.cur")),
            "url": summary["url"],
            "results": results,
        }

    def parse_results_summary(self) -> Dict[str, Any]:
        """提取结果页概要信息。"""
        if self.detect_page_type() != "results":
            raise UnsupportedPageError("当前页面不是 CNKI 搜索结果页")

        page_text = self._safe_text(self.page.query_selector(".countPageMark")) or "1/1"
        current_page, total_pages = self._extract_page_numbers(page_text)
        total = self._extract_total()

        return {
            "query": self._extract_query_text(),
            "total": total,
            "page": page_text,
            "current_page": current_page,
            "total_pages": total_pages,
            "has_next_page": self._has_next_page(current_page, total_pages),
            "url": self.page.url,
        }

    def parse_paper_detail(self) -> Dict[str, Any]:
        """解析论文详情页。"""
        brief = self.page.query_selector(".brief")
        if not brief:
            raise UnsupportedPageError("当前页面不是论文详情页")

        author_blocks = brief.query_selector_all("h3.author")
        authors = []
        if author_blocks:
            for author in author_blocks[0].query_selector_all("a"):
                raw_name = self._safe_text(author)
                if not raw_name:
                    continue
                match = re.search(r"(\d+)$", raw_name)
                authors.append(
                    {
                        "name": re.sub(r"\d+$", "", raw_name).strip(),
                        "affiliation_num": match.group(1) if match else "",
                    }
                )

        affiliations = []
        if len(author_blocks) > 1:
            affiliations = [
                self._safe_text(item)
                for item in author_blocks[1].query_selector_all("a")
                if self._safe_text(item)
            ]

        keywords = [
            self._safe_text(item).rstrip(";")
            for item in self.page.query_selector_all("p.keywords a")
            if self._safe_text(item)
        ]

        citation_info: Dict[str, Dict[str, Any]] = {}
        for tab in self.page.query_selector_all("ul.module-tab.tpl_lieteratures li"):
            tab_id = tab.get_attribute("data-id") or ""
            text = self._safe_text(tab)
            if not tab_id or not text:
                continue
            count_match = re.search(r"(\d+)", text)
            citation_info[tab_id] = {
                "label": re.sub(r"\d+", "", text).strip(),
                "count": int(count_match.group(1)) if count_match else 0,
            }

        title = self._safe_text(brief.query_selector("h1"))
        if not title:
            raise ParseError("未能解析论文标题")

        return {
            "result_type": "paper_detail",
            "title": title.replace("附视频", "").replace("网络首发", "").strip(),
            "journal": self._safe_text(self.page.query_selector(".doc-top a")),
            "pub_info": self._safe_text(self.page.query_selector(".head-time")),
            "is_online_first": brief.query_selector(".icon-shoufa") is not None,
            "authors": authors,
            "affiliations": affiliations,
            "abstract": self._safe_text(self.page.query_selector(".abstract-text")),
            "keywords": keywords,
            "fund": self._safe_text(self.page.query_selector("p.funds")),
            "classification": self._safe_text(self.page.query_selector(".clc-code")),
            "toc": self._safe_text(self.page.query_selector(".catalog-list, .catalog-listDiv")),
            "citation_info": citation_info,
            "url": self.page.url,
        }

    def _extract_total(self) -> int:
        text = self._safe_text(self.page.query_selector(".pagerTitleCell"))
        match = re.search(r"([\d,]+)", text)
        if not match:
            return 0
        return int(match.group(1).replace(",", ""))

    def _extract_query_text(self) -> str:
        current_input = self.page.query_selector("textarea.search-input") or self.page.query_selector("input.search-input")
        if current_input:
            return self._safe_text(current_input)

        advanced_input = self.page.query_selector("#txt_1_value1")
        if advanced_input:
            return self._safe_text(advanced_input)

        return ""

    def _extract_page_numbers(self, page_text: str) -> tuple[int, int]:
        match = re.search(r"(\d+)\s*/\s*(\d+)", page_text)
        if not match:
            return 1, 1
        return int(match.group(1)), int(match.group(2))

    def _has_next_page(self, current_page: int, total_pages: int) -> bool:
        next_button = self.page.query_selector("#PageNext")
        if next_button:
            class_name = next_button.get_attribute("class") or ""
            if "disabled" in class_name:
                return False
        return current_page < total_pages

    def _extract_source_text(self, row) -> str:
        source_link = row.query_selector("td.source a")
        if source_link:
            text = self._safe_text(source_link)
            if text:
                return text

        source_node = row.query_selector("td.source p") or row.query_selector("td.source")
        return self._safe_text(source_node)

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
