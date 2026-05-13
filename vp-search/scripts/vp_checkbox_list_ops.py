"""VP 结果复选框采集与状态判断。"""

import logging
from typing import Optional

from playwright.sync_api import Locator, Page

logger = logging.getLogger("vp_search.interactor")


class VpCheckboxListMixin:
    """VP 结果复选框采集与状态判断。"""

    def _result_checkbox_locator(self) -> Locator:
        for selector in self.RESULT_CHECKBOX_SELECTORS:
            locator = self.page.locator(selector)
            if locator.count() > 0:
                return locator
        return self.page.locator("input[lay-filter='selectArticle']")

    def _current_page_checkbox_items(self) -> list[Locator]:
        """返回当前页结果复选框列表（仅限 .search-list tbody，排除表单复选框）。"""
        locator = self.page.locator(".search-list tbody input[lay-filter='selectArticle']")
        count = locator.count()
        if count == 0:
            locator = self._result_checkbox_locator()
            count = locator.count()
        items = [locator.nth(i) for i in range(count)]
        logger.debug(
            "获取当前页复选框: count=%s",
            len(items),
        )
        return items

    def _resolve_checkbox_click_target(self, checkbox: Locator) -> Optional[Locator]:
        """查找 Layui 复选框可见点击目标（div.layui-form-checkbox 与 input 平级）。"""
        # 策略1: following-sibling (input 后紧跟 div)
        try:
            target = checkbox.locator("xpath=following-sibling::div[contains(@class,'layui-form-checkbox')][1]").first
            if target.count() > 0 and self._is_locator_visible(target):
                return target
        except Exception:
            pass

        # 策略2: preceding-sibling (input 前有 div)
        try:
            target = checkbox.locator("xpath=preceding-sibling::div[contains(@class,'layui-form-checkbox')][1]").first
            if target.count() > 0 and self._is_locator_visible(target):
                return target
        except Exception:
            pass

        # 策略3: 父容器下的 div 子元素（与 input 同级但通过父级关联）
        try:
            target = checkbox.locator("xpath=../div[contains(@class,'layui-form-checkbox')][1]").first
            if target.count() > 0 and self._is_locator_visible(target):
                return target
        except Exception:
            pass

        # 策略4: 兜底 - 就近查找任意 .layui-form-checkbox（即使不可见）
        try:
            target = checkbox.locator("xpath=ancestor::*[1]//div[contains(@class,'layui-form-checkbox')][1]").first
            if target.count() > 0:
                return target
        except Exception:
            pass

        return None

    def _is_checkbox_item_visible(self, checkbox: Locator) -> bool:
        """判断结果复选框是否属于当前可操作的行（允许隐藏元素，force 操作）。"""
        click_target = self._resolve_checkbox_click_target(checkbox)
        # 包装层存在即认为可操作（即使不可见也可 force click）
        if click_target is not None and click_target.count() > 0:
            return True
        # 兜底：checkbox 自身是否存在
        return checkbox.count() > 0

    def _is_locator_visible(self, locator: Locator) -> bool:
        """安全判断定位器是否可见。"""
        try:
            return bool(locator.is_visible())
        except Exception:
            return False

    def _is_checkbox_checked(self, checkbox: Locator, click_target: Optional[Locator] = None) -> bool:
        """安全读取复选框勾选状态（is_checked 失败时用 JS 兜底）。"""
        del click_target
        try:
            return bool(checkbox.is_checked())
        except Exception:
            pass
        try:
            return bool(checkbox.evaluate("(el) => el.checked"))
        except Exception:
            return False
