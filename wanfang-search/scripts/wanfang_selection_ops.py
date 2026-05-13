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
        reached_end = False

        while remaining > 0 if strict_target else covered_count < export_limit:
            self._wait_for_results_ready()
            row_locator = self.page.locator("div.normal-list")
            current_row_count = row_locator.count()
            if current_row_count == 0:
                break

            if current_row_offset >= current_row_count:
                if not self._goto_next_results_page():
                    reached_end = True
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
            if page_selected_count < page_target_count:
                raise ValidationError(
                    f"当前页勾选返回缺口: row_offset={current_row_offset}, "
                    f"target={page_target_count}, actual={page_selected_count}"
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
                reached_end = True
                break
            current_row_offset = 0

        if selected_count <= 0:
            if reached_end:
                return {
                    "selected_count": 0,
                    "next_row_offset": current_row_offset,
                    "page_row_count": current_row_count,
                    "start_page": start_page,
                    "end_page": end_page,
                    "reached_end": True,
                }
            raise ValidationError("结果页未选中任何文献，无法导出")
        return {
            "selected_count": selected_count,
            "next_row_offset": current_row_offset,
            "page_row_count": current_row_count,
            "start_page": start_page,
            "end_page": end_page,
            "reached_end": reached_end,
        }

    def _select_rows_on_current_page(
        self,
        row_offset: int,
        page_target_count: int,
        row_count: int,
    ) -> int:
        """当前页勾选 + 实勾校验 + 缺口补勾。"""
        checkbox_locator = self._wait_for_result_checkboxes(row_offset + page_target_count)

        # 整页全选必须强一致，一旦无法确认成功，立刻失败并交给上层重建检索上下文。
        if row_offset == 0 and page_target_count == row_count:
            selected_before = self._extract_selected_count(0)
            if self._try_select_all_on_current_page(
                expected_increase=page_target_count,
                selected_before_page=selected_before,
                checkbox_locator=checkbox_locator,
            ):
                return page_target_count
            logger.warning(
                "整页全选校验未通过，终止当前页并交给上层重试: target=%s, row_count=%s",
                page_target_count,
                row_count,
            )
            raise ValidationError(
                f"整页全选未成功: target={page_target_count}, row_count={row_count}"
            )

        # 非整页场景仍按逐条勾选处理。
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
                "当前页勾选后存在缺口，尝试页内补勾: row_offset=%s, target=%s, missing=%s",
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
                "当前页补勾后仍未达标，判定本批次失败: row_offset=%s, target=%s, actual=%s",
                row_offset,
                page_target_count,
                selected_count,
            )
            raise ValidationError(
                f"当前页勾选未达标: row_offset={row_offset}, target={page_target_count}, actual={selected_count}"
            )
        return selected_count

    def _try_select_all_on_current_page(
        self,
        expected_increase: int,
        selected_before_page: int,
        checkbox_locator: Locator,
    ) -> bool:
        """尝试页级全选，并通过已选数量增量校验是否真正生效。"""
        select_all_container = self.page.locator("div.top-check-bar div.wf-checkbox").first
        select_all = select_all_container.locator("label.ivu-checkbox-wrapper").first
        if select_all_container.count() == 0 or select_all.count() == 0:
            logger.debug("当前页不存在全选复选框，跳过页级全选")
            return False

        if not self._wait_for_select_all_ready(select_all_container, select_all):
            logger.debug("全选复选框结构未就绪，跳过页级全选")
            return False

        # 优先使用更完整的节点链路尝试勾选全选框，减少误点空白区域导致的失败。
        self._ensure_select_all_checked(
            select_all_container=select_all_container,
            select_all=select_all,
        )
        time.sleep(0.3)

        if self._count_checked_rows(checkbox_locator, 0, expected_increase) >= expected_increase:
            return True

        selected_after = self._read_selected_count()
        actual_increase = max((selected_after or selected_before_page) - selected_before_page, 0)
        logger.debug(
            "页级全选数量校验: selected_before=%s, selected_after=%s, expected_increase=%s, actual_increase=%s",
            selected_before_page,
            selected_after,
            expected_increase,
            actual_increase,
        )
        if selected_after is not None and actual_increase >= expected_increase:
            return True

        # 仅当全选框本身仍未勾上时才重试点击，避免误把成功全选又点回未选状态
        if not self._is_checkbox_checked(select_all):
            logger.debug("页级全选首次点击后仍未选中，准备重试")
            time.sleep(0.5)
            self._ensure_select_all_checked(
                select_all_container=select_all_container,
                select_all=select_all,
                prefer_precise_targets=True,
            )
            time.sleep(0.3)
            if self._count_checked_rows(checkbox_locator, 0, expected_increase) >= expected_increase:
                return True

            selected_after = self._read_selected_count()
            actual_increase = max((selected_after or selected_before_page) - selected_before_page, 0)
            if selected_after is not None and actual_increase >= expected_increase:
                return True

        # 仍然失败，回退：取消全选并返回 False
        logger.warning(
            "页级全选校验未通过: expected_increase=%s, actual_increase=%s",
            expected_increase,
            actual_increase,
        )
        # 尝试取消全选，避免残留半选状态
        if self._is_checkbox_checked(select_all):
            select_all.click(timeout=self._action_timeout_ms())
            time.sleep(0.2)
        return False

    def _wait_for_select_all_ready(self, select_all_container: Locator, select_all: Locator) -> bool:
        """等待全选复选框结构完整可用。"""
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            try:
                if not self._locator_is_visible(select_all_container) or not self._locator_is_visible(select_all):
                    time.sleep(0.1)
                    continue
            except Exception as exc:
                logger.debug("等待全选容器可见时遇到瞬时异常: %s", exc)
                time.sleep(0.1)
                continue

            input_el = select_all.locator("input.ivu-checkbox-input").first
            checkbox_span = select_all.locator("span.ivu-checkbox").first
            inner_span = select_all.locator("span.ivu-checkbox-inner").first
            if input_el.count() > 0 and checkbox_span.count() > 0 and inner_span.count() > 0:
                return True
            time.sleep(0.1)

        logger.debug("等待全选复选框结构就绪超时")
        return False

    def _ensure_select_all_checked(
        self,
        select_all_container: Locator,
        select_all: Locator,
        prefer_precise_targets: bool = False,
    ) -> None:
        """按多个候选节点尝试勾选全选复选框。"""
        selector = "div.top-check-bar div.wf-checkbox label.ivu-checkbox-wrapper"
        if not prefer_precise_targets:
            self._ensure_checkbox_checked(select_all, selector=selector)
            if self._is_checkbox_checked(select_all):
                return

        candidate_steps = [
            ("span.ivu-checkbox", "div.top-check-bar div.wf-checkbox span.ivu-checkbox"),
            ("span.ivu-checkbox-inner", "div.top-check-bar div.wf-checkbox span.ivu-checkbox-inner"),
        ]
        for child_selector, child_label in candidate_steps:
            candidate = select_all.locator(child_selector).first
            if candidate.count() == 0:
                continue
            self._click_locator_if_present(candidate, child_label)
            if self._is_checkbox_checked(select_all):
                return

        input_el = select_all.locator("input.ivu-checkbox-input").first
        if input_el.count() > 0:
            try:
                input_el.check(force=True, timeout=self._action_timeout_ms())
                time.sleep(0.1)
            except Exception as exc:
                logger.debug("全选 input 强制勾选失败: error=%s", exc)
            if self._is_checkbox_checked(select_all):
                return

        if not prefer_precise_targets:
            return

        self._click_locator_if_present(select_all_container, "div.top-check-bar div.wf-checkbox")

    def _click_locator_if_present(self, locator: Locator, selector: str) -> None:
        """在节点存在时尝试直接点击，失败后使用 JS 兜底。"""
        try:
            locator.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("节点滚动到可视区域失败: selector=%s, error=%s", selector, exc)

        try:
            locator.click(timeout=self._action_timeout_ms())
            time.sleep(0.1)
            return
        except Exception as exc:
            logger.debug("节点点击失败，尝试使用 JS 兜底: selector=%s, error=%s", selector, exc)

        try:
            locator.evaluate(
                """
                (element) => {
                    element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                }
                """
            )
            time.sleep(0.1)
        except Exception as exc:
            logger.debug("节点 JS 点击失败: selector=%s, error=%s", selector, exc)

    def _locator_is_visible(self, locator: Locator) -> bool:
        """安全判断节点是否存在且可见。"""
        try:
            return locator.count() > 0 and locator.is_visible()
        except Exception:
            return False

    def _ensure_checkbox_checked(self, checkbox: Locator, selector: str = "") -> None:
        """稳定勾选 ivu-checkbox 复选框。"""
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
            self._click_checkbox_via_js(checkbox)

        try:
            if self._is_checkbox_checked(checkbox):
                return
        except Exception as exc:
            logger.debug("校验复选框勾选状态失败: selector=%s, error=%s", selector or "<locator>", exc)

        input_el = checkbox.locator("input.ivu-checkbox-input").first
        if input_el.count() > 0:
            try:
                input_el.check(force=True, timeout=self._action_timeout_ms())
                time.sleep(0.1)
            except Exception as exc:
                logger.debug("原生 input 勾选失败: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            if self._is_checkbox_checked(checkbox):
                return
        except Exception as exc:
            logger.debug("校验复选框勾选状态失败: selector=%s, error=%s", selector or "<locator>", exc)

        logger.warning("未能完成复选框勾选: %s", selector or "<locator>")

    def _wait_for_result_checkboxes(self, expected_count: int) -> Locator:
        """等待当前页结果复选框挂载完成。"""
        checkbox_locator = self.page.locator("div.normal-list div.wf-checkbox label.ivu-checkbox-wrapper")
        if expected_count <= 0:
            return checkbox_locator

        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            current_count = checkbox_locator.count()
            if current_count >= expected_count:
                return checkbox_locator
            time.sleep(0.2)

        logger.debug(
            "结果复选框等待超时，继续使用当前数量: expected=%s, actual=%s",
            expected_count,
            checkbox_locator.count(),
        )
        return checkbox_locator

    def _click_checkbox_via_js(self, checkbox: Locator) -> None:
        """通过事件触发复选框点击，避免直接篡改样式状态。"""
        checkbox.evaluate(
            """
            (label) => {
                const input = label.querySelector('input.ivu-checkbox-input');
                const target = input || label;
                target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """
        )

    def _prepare_next_batch_cursor(self, batch_selection: Dict[str, Any]) -> Dict[str, int]:
        """根据批次勾选结果计算下一批恢复游标。"""
        next_row_offset = int(batch_selection["next_row_offset"])
        page_row_count = int(batch_selection["page_row_count"])
        current_page = int(batch_selection.get("end_page") or self._current_results_page_number() or 1)
        if next_row_offset < page_row_count:
            return {
                "current_page": current_page,
                "current_row_offset": next_row_offset,
            }

        if self._goto_next_results_page():
            advanced_page = self._current_results_page_number()
            return {
                "current_page": advanced_page if advanced_page > 0 else current_page + 1,
                "current_row_offset": 0,
            }
        return {
            "current_page": current_page,
            "current_row_offset": next_row_offset,
        }

    def _extract_selected_count(self, default_value: int) -> int:
        """从 DOM 中读取已选数量。"""
        selected_count = self._read_selected_count()
        return selected_count if selected_count is not None else default_value

    def _read_selected_count(self) -> Optional[int]:
        """从 DOM 中读取已选数量，读取失败时返回 None。"""
        locator = self.page.locator("span.checked-tip span.mark-number").first
        if locator.count() == 0:
            return None
        text = locator.inner_text().replace(",", "").replace("，", "").strip()
        logger.debug("已选数量读取: DOM读取=%s", text)
        return int(text) if text.isdigit() else None

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
            input_el = checkbox.locator("input.ivu-checkbox-input").first
            if input_el.count() > 0:
                return input_el.is_checked()
            checkbox_span = checkbox.locator("span.ivu-checkbox").first
            if checkbox_span.count() > 0:
                class_name = checkbox_span.get_attribute("class") or ""
                return "ivu-checkbox-checked" in class_name
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
