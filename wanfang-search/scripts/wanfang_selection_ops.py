"""万方结果勾选 Mixin。"""

import logging
import time
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page

from exceptions import TimeoutError, ValidationError

logger = logging.getLogger("wanfang_search.interactor")


class WanfangSelectionMixin:
    """万方结果勾选操作。"""

    def _clear_selected_results(self) -> None:
        """清除已选结果。"""
        clear_btn = self.page.locator("span.clear-btn").first
        if clear_btn.count() == 0:
            return
        try:
            clear_btn.click()
            time.sleep(0.3)
        except Exception as exc:
            logger.debug("清除已选文献失败: %s", exc)

    def _select_batch_results(
        self,
        export_limit: int,
        row_offset: int,
        strict_target: bool,
    ) -> Dict[str, Any]:
        """勾选当前批次结果（双策略）。"""
        remaining = export_limit
        covered_count = 0
        selected_count = 0
        current_row_offset = row_offset
        current_row_count = 0
        start_page = 0
        end_page = 0

        while remaining > 0 if strict_target else covered_count < export_limit:
            self._wait_for_results_ready()
            row_locator = self.page.locator("div.normal-list")
            current_row_count = row_locator.count()
            if current_row_count == 0:
                break

            if current_row_offset >= current_row_count:
                if not self._goto_next_results_page():
                    break
                current_row_offset = 0
                continue

            current_page = self._current_results_page_number()
            if start_page <= 0:
                start_page = current_page
            end_page = current_page

            page_target_count = min(
                current_row_count - current_row_offset,
                remaining if strict_target else export_limit - covered_count,
            )
            page_selected_count = self._select_rows_on_current_page(
                current_row_offset,
                page_target_count,
                current_row_count,
            )
            covered_count += page_target_count
            selected_count += page_selected_count
            if strict_target:
                remaining = export_limit - selected_count
            logger.info(
                "当前页勾选完成: strict_target=%s, row_offset=%s, target=%s, actual=%s, covered=%s, selected=%s",
                strict_target,
                current_row_offset,
                page_target_count,
                page_selected_count,
                covered_count,
                selected_count,
            )
            current_row_offset += page_target_count
            if strict_target and remaining <= 0:
                break
            if not strict_target and covered_count >= export_limit:
                break

            if current_row_offset < current_row_count:
                continue

            if not self._goto_next_results_page():
                break
            current_row_offset = 0

        if selected_count <= 0:
            raise ValidationError("结果页未选中任何文献，无法导出")
        return {
            "selected_count": selected_count,
            "next_row_offset": current_row_offset,
            "page_row_count": current_row_count,
            "start_page": start_page,
            "end_page": end_page,
        }

    def _select_rows_on_current_page(
        self,
        row_offset: int,
        page_target_count: int,
        row_count: int,
    ) -> int:
        """当前页勾选 + 实勾校验 + 缺口补勾。"""
        checkbox_locator = self.page.locator("div.normal-list div.wf-checkbox label.ivu-checkbox-wrapper")

        # 全选优化: 当页内全选时使用全选复选框
        if row_offset == 0 and page_target_count == row_count:
            select_all = self.page.locator("div.top-check-bar div.wf-checkbox label.ivu-checkbox-wrapper").first
            if select_all.count() > 0:
                self._ensure_checkbox_checked(select_all, selector="div.top-check-bar div.wf-checkbox label.ivu-checkbox-wrapper")
        else:
            for index in range(row_offset, row_offset + page_target_count):
                checkbox = checkbox_locator.nth(index)
                self._ensure_checkbox_checked(
                    checkbox,
                    selector=f"div.normal-list div.wf-checkbox label.ivu-checkbox-wrapper[{index}]",
                )

        selected_count = self._count_checked_rows(checkbox_locator, row_offset, page_target_count)
        if selected_count >= page_target_count:
            return selected_count

        missing_indexes = self._find_unchecked_row_indexes(checkbox_locator, row_offset, page_target_count)
        if missing_indexes:
            logger.warning(
                "当前页批量勾选后存在缺口，尝试页内补勾: row_offset=%s, target=%s, missing=%s",
                row_offset,
                page_target_count,
                len(missing_indexes),
            )
            for index in missing_indexes:
                checkbox = checkbox_locator.nth(index)
                self._ensure_checkbox_checked(
                    checkbox,
                    selector=f"div.normal-list div.wf-checkbox label.ivu-checkbox-wrapper[{index}]",
                )
            selected_count = self._count_checked_rows(checkbox_locator, row_offset, page_target_count)

        if selected_count < page_target_count:
            logger.warning(
                "当前页补勾后仍未达标，将保留缺口继续处理: row_offset=%s, target=%s, actual=%s",
                row_offset,
                page_target_count,
                selected_count,
            )
        return selected_count

    def _ensure_checkbox_checked(self, checkbox: Locator, selector: str = "") -> None:
        """稳定勾选 ivu-checkbox 复选框。"""
        # 万方 ivu-checkbox 使用 span.ivu-checkbox-inner 的父 label 点击
        # 验证 span.ivu-checkbox 是否包含 ivu-checkbox-checked class
        try:
            if self._is_checkbox_checked(checkbox):
                return
        except Exception:
            pass

        try:
            checkbox.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("复选框滚动到可视区域失败: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.click(timeout=self._action_timeout_ms())
            time.sleep(0.1)
        except Exception as exc:
            logger.debug("常规勾选复选框失败，尝试使用 JS 兜底: selector=%s, error=%s", selector or "<locator>", exc)
            # JS 兜底: 设置 checked 属性并触发事件
            checkbox.evaluate(
                """
                (label) => {
                    const input = label.querySelector('input.ivu-checkbox-input');
                    if (input) {
                        input.checked = true;
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.dispatchEvent(new Event('click', { bubbles: true }));
                    }
                    const span = label.querySelector('span.ivu-checkbox');
                    if (span) {
                        span.classList.add('ivu-checkbox-checked');
                    }
                }
                """
            )

        try:
            if self._is_checkbox_checked(checkbox):
                return
        except Exception as exc:
            logger.debug("校验复选框勾选状态失败: selector=%s, error=%s", selector or "<locator>", exc)

        logger.warning("未能完成复选框勾选: %s", selector or "<locator>")

    def _prepare_next_batch_cursor(self, batch_selection: Dict[str, Any]) -> int:
        """根据批次勾选结果计算下一批起始偏移量。"""
        next_row_offset = batch_selection["next_row_offset"]
        page_row_count = batch_selection["page_row_count"]
        if next_row_offset < page_row_count:
            return next_row_offset

        if self._goto_next_results_page():
            return 0
        return next_row_offset

    def _extract_selected_count(self, default_value: int) -> int:
        """从 DOM 中读取已选数量。"""
        locator = self.page.locator("span.checked-tip span.mark-number").first
        if locator.count() == 0:
            return default_value
        text = locator.inner_text().replace(",", "").replace("，", "").strip()
        logger.debug("已选数量读取: DOM读取=%s, 期望=%s", text, default_value)
        return int(text) if text.isdigit() else default_value

    def _count_checked_rows(self, checkbox_locator: Locator, row_offset: int, page_target_count: int) -> int:
        """统计目标区间内实际勾选数量。"""
        selected_count = 0
        for index in range(row_offset, row_offset + page_target_count):
            if self._is_checkbox_checked(checkbox_locator.nth(index)):
                selected_count += 1
        return selected_count

    def _find_unchecked_row_indexes(self, checkbox_locator: Locator, row_offset: int, page_target_count: int) -> list[int]:
        """返回目标区间内仍未勾选的下标。"""
        unchecked_indexes: list[int] = []
        for index in range(row_offset, row_offset + page_target_count):
            if not self._is_checkbox_checked(checkbox_locator.nth(index)):
                unchecked_indexes.append(index)
        return unchecked_indexes

    def _is_checkbox_checked(self, checkbox: Locator) -> bool:
        """安全读取万方 ivu-checkbox 勾选状态。"""
        try:
            # 万方 ivu-checkbox: 通过 span.ivu-checkbox 是否包含 ivu-checkbox-checked class 判断
            checkbox_span = checkbox.locator("span.ivu-checkbox").first
            if checkbox_span.count() > 0:
                class_name = checkbox_span.get_attribute("class") or ""
                return "ivu-checkbox-checked" in class_name
            # 回退: 检查 input 的 checked 属性
            input_el = checkbox.locator("input.ivu-checkbox-input").first
            if input_el.count() > 0:
                return input_el.is_checked()
        except Exception as exc:
            logger.debug("读取复选框勾选状态失败: %s", exc)
        return False

    def _current_results_page_number(self) -> int:
        """返回当前结果页页码。"""
        try:
            # 万方: 从 span.pager.active 的文本中解析当前页码数字
            active_pager = self.page.locator("span.pager.active").first
            if active_pager.count() > 0:
                text = active_pager.inner_text().strip()
                if text.isdigit():
                    return int(text)
            return int(self.parser.parse_results_summary()["current_page"])
        except Exception as exc:
            logger.debug("读取当前结果页页码失败: %s", exc)
            return 0
