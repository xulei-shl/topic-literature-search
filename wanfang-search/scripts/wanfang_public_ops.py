"""万方公共检索方法 Mixin。"""

import logging
from typing import Any, Dict, Optional

from playwright.sync_api import Page

from exceptions import ValidationError

logger = logging.getLogger("wanfang_search.interactor")


class WanfangPublicMixin:
    """万方公共检索操作。"""

    def search(self, query: str, num_results: Optional[int] = None) -> Dict[str, Any]:
        """基础关键词检索。"""
        if not query.strip():
            raise ValidationError("检索词不能为空")

        limit = self._normalize_limit(num_results)
        self.browser_manager.restore_session(self.config.home_url)
        self._wait_for_any_selector(["input.ivu-input", "span.submit-btn"])
        self._ensure_captcha_cleared()

        search_input = self._first_visible_locator(["input.ivu-input.ivu-input-default"])
        search_input.fill(query.strip())
        self._click_first_available(["span.submit-btn"])

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
