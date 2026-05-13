"""VP 结果页批量勾选逻辑。"""

import logging
import re
import time
from typing import Any, Dict

from playwright.sync_api import Locator

from exceptions import TimeoutError, ValidationError

logger = logging.getLogger("vp_search.interactor")


class VpSelectionMixin:
    """负责维普结果页的清空、全选、逐条勾选与数量校验。"""

    def _clear_selected_results(self) -> None:
        """清空当前已选结果，并尽量等待计数归零。"""
        clear_link = self.page.locator("span.selected-count a[title='清空已选文章']").first
        if clear_link.count() == 0:
            clear_link = self.page.locator("span.selected-count a").nth(1)
        if clear_link.count() == 0:
            clear_link = self.page.locator(".selected-count .icon-del, .selected-count a.icon-del").first
        if clear_link.count() == 0:
            return

        try:
            clear_link.click()
            time.sleep(self._action_poll_interval_seconds())
            self._dismiss_confirm_dialog_if_present()
            self._wait_for_selected_count_reset()
        except Exception as exc:
            logger.debug("清除已选文献失败: %s", exc)

    def _wait_for_selected_count_reset(self) -> None:
        """等待已选数量回到 0。"""
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            if self._extract_selected_count(0) <= 0:
                return
            time.sleep(self._action_poll_interval_seconds())

    def _wait_for_selection_area_ready(self) -> None:
        """等待翻页后选择区域（全选框 + 已选计数）加载完成。"""
        try:
            timeout_ms = self._page_change_timeout_seconds() * 1000
            self.page.wait_for_selector(".selection", state="attached", timeout=timeout_ms)
            self.page.wait_for_selector(
                ".selection .select-all .layui-form-checkbox",
                state="visible",
                timeout=timeout_ms
            )
        except Exception:
            logger.debug("等待选择区域就绪超时（超时 %dms）", timeout_ms)

    def _select_batch_results(
        self,
        export_limit: int,
        row_offset: int,
        strict_target: bool,
    ) -> Dict[str, Any]:
        """按页勾选当前批次结果。"""
        remaining = export_limit
        covered_count = 0
        selected_count = 0
        current_row_offset = row_offset
        current_row_count = 0
        start_page = 0
        end_page = 0

        while remaining > 0 if strict_target else covered_count < export_limit:
            self._wait_for_results_ready()
            checkbox_items = self._wait_for_current_page_checkbox_items()
            current_row_count = len(checkbox_items)

            if current_row_count == 0:
                if not self._goto_next_results_page():
                    break
                current_row_offset = 0
                continue

            if current_row_offset >= current_row_count:
                if not self._goto_next_results_page():
                    break
                current_row_offset = 0
                continue

            current_page = self._coerce_current_results_page(self.parser.parse_results_summary())
            if current_page > 0:
                if start_page <= 0:
                    start_page = current_page
                end_page = current_page

            page_target_count = min(
                current_row_count - current_row_offset,
                remaining if strict_target else export_limit - covered_count,
            )
            selected_before_page = self._extract_selected_count(selected_count)
            page_selected_count = self._select_rows_on_current_page(
                row_offset=current_row_offset,
                page_target_count=page_target_count,
                row_count=current_row_count,
                selected_before_page=selected_before_page,
            )

            covered_count += page_target_count
            selected_count += page_selected_count
            current_row_offset += page_target_count
            if strict_target:
                remaining = export_limit - selected_count

            logger.info(
                "当前页勾选完成: strict_target=%s, current_page=%s, row_offset=%s, page_target=%s, page_selected=%s, selected=%s, covered=%s",
                strict_target,
                current_page,
                current_row_offset - page_target_count,
                page_target_count,
                page_selected_count,
                selected_count,
                covered_count,
            )

            if strict_target and remaining <= 0:
                break
            if not strict_target and covered_count >= export_limit:
                break

            if current_row_offset < current_row_count:
                continue

            if not self._goto_next_results_page():
                break
            current_row_offset = 0

            self._wait_for_selection_area_ready()

        if selected_count <= 0:
            raise ValidationError("结果页未选中任何文献，无法导出")

        ui_selected = self._extract_selected_count(selected_count)
        if ui_selected > selected_count:
            logger.warning("页面已选数量大于本地计数，改用页面值: local=%s, ui=%s", selected_count, ui_selected)
            selected_count = ui_selected

        return {
            "selected_count": selected_count,
            "next_row_offset": current_row_offset,
            "page_row_count": current_row_count,
            "already_at_target": selected_count >= export_limit,
            "start_page": start_page,
            "end_page": end_page,
        }

    def _wait_for_current_page_checkbox_items(self) -> list[Locator]:
        """等待当前页结果复选框稳定出现。"""
        if not self.page or not hasattr(self.page, "locator"):
            return self._current_page_checkbox_items()

        deadline = time.time() + self._page_change_timeout_seconds()
        last_count = -1
        stable_rounds = 0

        while time.time() < deadline:
            checkbox_items = self._current_page_checkbox_items()
            current_count = len(checkbox_items)
            if current_count > 0:
                if current_count == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                if stable_rounds >= 1:
                    return checkbox_items
                last_count = current_count
            elif self.page.locator(".no-data, .empty, .none-data").count() > 0:
                return []
            time.sleep(self._page_change_poll_interval_seconds())

        return self._current_page_checkbox_items()

    def _coerce_current_results_page(self, summary: Dict[str, Any]) -> int:
        """安全解析当前页页码。"""
        try:
            return int(summary.get("current_page") or 0)
        except (TypeError, ValueError) as exc:
            logger.debug("解析当前页码失败: summary=%s, error=%s", summary, exc)
            return 0

    def _select_rows_on_current_page(
        self,
        row_offset: int,
        page_target_count: int,
        row_count: int,
        selected_before_page: int,
    ) -> int:
        """勾选当前页：优先全选（可见区域），多选部分 JS 批量取消。"""
        if page_target_count <= 0:
            return 0

        if row_offset == 0 and self._try_select_all_on_current_page(
            expected_increase=page_target_count,
            selected_before_page=selected_before_page,
        ):
            ui_selected_after = self._extract_selected_count(selected_before_page + page_target_count)
            actual_increase = max(ui_selected_after - selected_before_page, 0)
            if actual_increase > page_target_count:
                excess = actual_increase - page_target_count
                logger.info("全选后超出目标，JS 批量取消多出部分: target=%s, actual=%s, excess=%s", page_target_count, actual_increase, excess)
                self._uncheck_extra_items(page_target_count, actual_increase)
            return min(page_target_count, max(self._extract_selected_count(selected_before_page + page_target_count) - selected_before_page, 0))

        checkbox_items = self._current_page_checkbox_items()
        if not checkbox_items:
            return 0

        if self.page and hasattr(self.page, "evaluate"):
            self.page.evaluate(
                """
                ({ row_offset, end_index }) => {
                    const wrappers = document.querySelectorAll("dd.sel > .layui-form-checkbox");
                    for (let i = row_offset; i < end_index && i < wrappers.length; i++) {
                        if (!wrappers[i].classList.contains('layui-form-checked')) {
                            wrappers[i].click();
                        }
                    }
                }
                """,
                {"row_offset": row_offset, "end_index": row_offset + page_target_count},
            )
        else:
            for index in range(row_offset, min(row_offset + page_target_count, len(checkbox_items))):
                self._ensure_checkbox_checked(checkbox_items[index], selector=f"result_checkbox[{index}]")

        ui_selected_after = self._extract_selected_count(selected_before_page + page_target_count)
        return min(page_target_count, max(ui_selected_after - selected_before_page, 0))

    def _uncheck_extra_items(self, start_index: int, end_index: int) -> None:
        """批量取消勾选 [start_index, end_index) 区间的复选框（JS 绕过不可见问题）。"""
        if not self.page or not hasattr(self.page, "evaluate"):
            return
        self.page.evaluate(
            """
            ({ start_index, end_index }) => {
                const wrappers = document.querySelectorAll("dd.sel > .layui-form-checkbox");
                for (let i = start_index; i < end_index && i < wrappers.length; i++) {
                    if (wrappers[i].classList.contains('layui-form-checked')) {
                        wrappers[i].click();
                    }
                }
            }
            """,
            {"start_index": start_index, "end_index": end_index},
        )

    def _wait_for_select_all_settled(
        self,
        checkbox_items: list[Locator],
        expected_increase: int,
        selected_before_page: int,
    ) -> int:
        """等待全选后的页面计数稳定（以页面已选计数器为准）。"""
        del checkbox_items
        deadline = time.time() + self._page_change_timeout_seconds()
        last_count = -1
        stable_rounds = 0

        while time.time() < deadline:
            current = self._extract_selected_count(selected_before_page)
            increase = max(current - selected_before_page, 0)
            if increase >= expected_increase:
                return increase
            if increase == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_count = increase
            if stable_rounds >= 2:
                break
            time.sleep(self._page_change_poll_interval_seconds())

        return max(self._extract_selected_count(selected_before_page) - selected_before_page, 0)

    def _try_select_all_on_current_page(self, expected_increase: int, selected_before_page: int) -> bool:
        """尝试点击当前页全选控件，并校验增量是否符合预期。"""
        if not self.page or not hasattr(self.page, "locator"):
            return False
        wrapper = self.page.locator(".selection .select-all .layui-form-checkbox").first

        if wrapper.count() == 0:
            select_all = self.page.locator("input[name='selectArticleAll']").first
            if select_all.count() == 0:
                select_all = self.page.locator("input[lay-filter='selectArticleAll']").first
            if select_all.count() == 0:
                logger.debug("当前页不存在全选控件，回退逐条勾选")
                return False
            try:
                self._ensure_checkbox_checked(select_all, selector="input[name='selectArticleAll']")
            except Exception as exc:
                logger.debug("当前页全选失败，回退逐条勾选: %s", exc)
                return False
        else:
            class_name = wrapper.get_attribute("class") or ""
            if "layui-form-checked" not in class_name:
                try:
                    timeout_ms = self._action_timeout_ms()
                    wrapper.wait_for(state="visible", timeout=timeout_ms)
                    wrapper.click(force=True, no_wait_after=True)
                except Exception as exc:
                    logger.debug("全选(Layui wrapper)失败，回退 JS 强制: %s", exc)
                    try:
                        wrapper.evaluate(
                            """
                            (el) => {
                                el.classList.add('layui-form-checked');
                                const container = el.closest('.select-all');
                                if (container) {
                                    const input = container.querySelector('input[type="checkbox"]');
                                    if (input) {
                                        input.checked = true;
                                        input.dispatchEvent(new Event('change', { bubbles: true }));
                                        input.dispatchEvent(new Event('click', { bubbles: true }));
                                    }
                                }
                            }
                            """
                        )
                    except Exception as js_exc:
                        logger.debug("JS 强制设置也失败: %s", js_exc)
                        return False

        checkbox_items = self._current_page_checkbox_items()
        selected_count = self._wait_for_select_all_settled(
            checkbox_items=checkbox_items,
            expected_increase=expected_increase,
            selected_before_page=selected_before_page,
        )
        if selected_count >= expected_increase:
            return True

        logger.warning(
            "当前页全选后数量仍不足，尝试全局 JS 批量勾选: expected=%s, actual=%s",
            expected_increase,
            selected_count,
        )

        try:
            self.page.evaluate(
                """
                () => {
                    document.querySelectorAll("input[lay-filter='selectArticle']").forEach(el => {
                        el.checked = true;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('click', { bubbles: true }));
                    });
                }
                """
            )
            selected_count = max(self._extract_selected_count(selected_before_page) - selected_before_page, 0)
            if selected_count >= expected_increase:
                logger.info("全局 JS 批量勾选成功: count=%s", selected_count)
                return True
        except Exception as js_exc:
            logger.debug("全局 JS 批量勾选失败: %s", js_exc)

        logger.warning(
            "当前页全选后数量仍不足，改为逐条补勾: expected=%s, actual=%s",
            expected_increase,
            selected_count,
        )
        return False

    def _ensure_checkbox_checked(self, checkbox: Locator, selector: str = "") -> None:
        """稳定勾选复选框，跳过滚动，直接 force 操作。"""
        click_target = self._resolve_checkbox_click_target(checkbox)
        if self._is_checkbox_checked(checkbox, click_target):
            return

        try:
            checkbox.check(force=True, timeout=self._action_timeout_ms())
            if self._is_checkbox_checked(checkbox, click_target):
                return
        except Exception as exc:
            logger.debug("原生勾选失败，尝试点击 Layui 包装层: selector=%s, error=%s", selector or "<locator>", exc)

        if click_target is not None:
            try:
                click_target.click(force=True, timeout=self._action_timeout_ms())
                if self._is_checkbox_checked(checkbox, click_target):
                    return
            except Exception as exc:
                logger.debug("Layui 包装层点击失败，尝试 JS 兜底: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.evaluate(
                """
                (element) => {
                    element.checked = true;
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.dispatchEvent(new Event('click', { bubbles: true }));
                }
                """
            )
            if self._is_checkbox_checked(checkbox, click_target):
                return
        except Exception as exc:
            logger.debug("JS 直接勾选失败: selector=%s, error=%s", selector or "<locator>", exc)

        if click_target is not None:
            try:
                click_target.evaluate(
                    """
                    (element) => {
                        element.click();
                    }
                    """
                )
                if self._is_checkbox_checked(checkbox, click_target):
                    return
            except Exception as exc:
                logger.debug("JS 点击 Layui 包装层失败: selector=%s, error=%s", selector or "<locator>", exc)

        raise TimeoutError(f"未能完成复选框勾选: {selector or '<locator>'}")

    def _ensure_checkbox_unchecked(self, checkbox: Locator, selector: str = "") -> None:
        """稳定取消复选框勾选（跳过滚动）。"""
        click_target = self._resolve_checkbox_click_target(checkbox)
        if not self._is_checkbox_checked(checkbox, click_target):
            return

        if click_target is not None:
            try:
                click_target.click(force=True, timeout=self._action_timeout_ms())
                if not self._is_checkbox_checked(checkbox, click_target):
                    return
            except Exception as exc:
                logger.debug("Layui 包装层取消勾选失败，回退原生 input: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.uncheck(force=True, timeout=self._action_timeout_ms())
            if not self._is_checkbox_checked(checkbox, click_target):
                return
        except Exception as exc:
            logger.debug("原生取消勾选失败，尝试 JS 兜底: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.evaluate(
                """
                (element) => {
                    element.checked = false;
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.dispatchEvent(new Event('click', { bubbles: true }));
                }
                """
            )
            if not self._is_checkbox_checked(checkbox, click_target):
                return
        except Exception as exc:
            logger.debug("JS 取消勾选失败: selector=%s, error=%s", selector or "<locator>", exc)

        raise TimeoutError(f"未能取消复选框勾选: {selector or '<locator>'}")

    def _rollback_excess_selection(
        self,
        target_count: int,
        checkbox_items: list[Locator],
        max_uncheck_count: int,
    ) -> None:
        """回滚超出目标数量的勾选项。"""
        current_selected = self._extract_selected_count(0)
        if current_selected <= target_count:
            return

        excess_count = current_selected - target_count
        uncheck_count = 0
        for index in range(len(checkbox_items)):
            if uncheck_count >= excess_count or uncheck_count >= max_uncheck_count:
                break
            checkbox = checkbox_items[index]
            if self._is_checkbox_checked(checkbox, None):
                try:
                    self._ensure_checkbox_unchecked(checkbox, selector=f"result_checkbox[{index}]")
                    uncheck_count += 1
                    current_selected = self._extract_selected_count(0)
                    if current_selected <= target_count:
                        break
                except Exception as exc:
                    logger.debug("取消选中失败: index=%s, error=%s", index, exc)

    def _clear_page_selection(self, checkbox_items: list[Locator]) -> None:
        """清除当前页已勾选项。"""
        for index, checkbox in enumerate(checkbox_items):
            if self._is_checkbox_checked(checkbox, None):
                try:
                    self._ensure_checkbox_unchecked(checkbox, selector=f"result_checkbox[{index}]")
                except Exception as exc:
                    logger.debug("清除当前页勾选失败: index=%s, error=%s", index, exc)

    def _prepare_next_batch_cursor(self, batch_selection: Dict[str, Any]) -> Dict[str, int]:
        """根据当前批次结果计算下一批恢复游标。"""
        next_row_offset = int(batch_selection["next_row_offset"])
        page_row_count = int(batch_selection["page_row_count"])
        current_page = int(batch_selection.get("end_page") or self._coerce_current_results_page(self.parser.parse_results_summary()) or 1)
        if next_row_offset < page_row_count:
            return {"current_page": current_page, "current_row_offset": next_row_offset}
        if self._goto_next_results_page():
            advanced_page = self._coerce_current_results_page(self.parser.parse_results_summary())
            return {
                "current_page": advanced_page if advanced_page > 0 else current_page + 1,
                "current_row_offset": 0,
            }
        return {"current_page": current_page, "current_row_offset": next_row_offset}

    def _count_checked_rows(self, checkbox_items: list[Locator], row_offset: int, page_target_count: int) -> int:
        """统计目标区间内已勾选数量。"""
        selected_count = 0
        end_index = min(row_offset + page_target_count, len(checkbox_items))
        for index in range(row_offset, end_index):
            if self._is_checkbox_checked(checkbox_items[index], None):
                selected_count += 1
        return selected_count

    def _find_unchecked_row_indexes(self, checkbox_items: list[Locator], row_offset: int, page_target_count: int) -> list[int]:
        """返回目标区间中仍未勾选的下标。"""
        unchecked_indexes: list[int] = []
        end_index = min(row_offset + page_target_count, len(checkbox_items))
        for index in range(row_offset, end_index):
            if not self._is_checkbox_checked(checkbox_items[index], None):
                unchecked_indexes.append(index)
        return unchecked_indexes

    def _extract_selected_count(self, default_value: int) -> int:
        """读取页面上的已选数量。"""
        if not self.page or not hasattr(self.page, "locator"):
            return default_value
        for selector in [
            "span[data-topcount='topcount']",
            ".checked-tip .mark-number",
            "span.selected-count span",
            "span.selected-count",
            ".selected-count span",
            "#selectCount",
        ]:
            locator = self.page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                text = locator.inner_text().replace(",", "").replace("，", "").strip()
                numbers = re.findall(r"\d+", text)
                if numbers:
                    return int(numbers[0])
            except Exception as exc:
                logger.debug("获取选中数量失败: selector=%s, error=%s", selector, exc)
        return default_value
