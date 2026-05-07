"""?????????"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams
from src.utils.playwright_page import click_first_available, disable_checkbox, enable_checkbox, has_visible_selector, set_native_select_value, wait_for_any_selector
from src.utils.result_output import build_export_file_path

from browser import BrowserManager
from config import VpSearchConfig
from exceptions import CaptchaError, TimeoutError, ValidationError
from export_processor import ExportResultProcessor
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("vp_search.interactor")

class VpSelectionMixin:
    """?????????"""

    def _clear_selected_results(self) -> None:
        clear_link = self.page.locator("span.selected-count a[title='清空已选文章']").first
        if clear_link.count() == 0:
            clear_link = self.page.locator("span.selected-count a").nth(1)
        if clear_link.count() == 0:
            return
        try:
            clear_link.click()
            self._dismiss_confirm_dialog_if_present()
            time.sleep(self._action_poll_interval_seconds())
        except Exception as exc:
            logger.debug("清除已选文献失败: %s", exc)

    def _select_batch_results(
        self,
        export_limit: int,
        row_offset: int,
        strict_target: bool,
    ) -> Dict[str, Any]:
        remaining = export_limit
        covered_count = 0
        selected_count = 0
        current_row_offset = row_offset
        current_row_count = 0
        start_page = 0
        end_page = 0

        while remaining > 0 if strict_target else covered_count < export_limit:
            page_prepare_started_at = time.perf_counter()
            self._wait_for_results_ready()
            logger.debug(
                "结果页已就绪，准备采集当前页复选框: row_offset=%s, wait_elapsed_ms=%s",
                current_row_offset,
                int((time.perf_counter() - page_prepare_started_at) * 1000),
            )
            checkbox_collect_started_at = time.perf_counter()
            checkbox_items = self._current_page_checkbox_items()
            current_row_count = len(checkbox_items)
            logger.debug(
                "当前页复选框采集完成: row_offset=%s, row_count=%s, elapsed_ms=%s",
                current_row_offset,
                current_row_count,
                int((time.perf_counter() - checkbox_collect_started_at) * 1000),
            )
            if current_row_count == 0:
                logger.debug("当前页未获取到复选框，可能页面未加载完成，重试")
                retry_started_at = time.perf_counter()
                time.sleep(1)
                checkbox_items = self._current_page_checkbox_items()
                current_row_count = len(checkbox_items)
                logger.debug(
                    "当前页复选框二次采集完成: row_offset=%s, row_count=%s, elapsed_ms=%s",
                    current_row_offset,
                    current_row_count,
                    int((time.perf_counter() - retry_started_at) * 1000),
                )
            summary = self.parser.parse_results_summary()
            current_page = self._coerce_current_results_page(summary)
            logger.debug(
                "批量勾选循环: strict_target=%s, export_limit=%s, selected_count=%s, covered_count=%s, remaining=%s, row_offset=%s, row_count=%s, current_page=%s",
                strict_target,
                export_limit,
                selected_count,
                covered_count,
                remaining,
                current_row_offset,
                current_row_count,
                current_page,
            )
            if current_row_count == 0:
                logger.debug("当前结果页未找到可勾选记录，结束本轮勾选")
                break

            if current_row_offset >= current_row_count:
                if strict_target and remaining <= 0:
                    logger.debug("已达成目标数量，无需翻页: remaining=%s", remaining)
                    break
                if not strict_target and covered_count >= export_limit:
                    logger.debug("当前批次目标窗口已覆盖完成，无需翻页: covered_count=%s", covered_count)
                    break
                logger.debug(
                    "当前页剩余记录不足，准备翻页继续: row_offset=%s, row_count=%s",
                    current_row_offset,
                    current_row_count,
                )
                page_turn_started_at = time.perf_counter()
                if not self._goto_next_results_page():
                    logger.debug("翻页失败或不存在下一页，结束本轮勾选")
                    break
                logger.debug(
                    "翻页成功，准备进入下一页勾选判定: previous_row_offset=%s, elapsed_ms=%s",
                    current_row_offset,
                    int((time.perf_counter() - page_turn_started_at) * 1000),
                )
                current_row_offset = 0
                continue

            if current_page > 0:
                if start_page <= 0:
                    start_page = current_page
                end_page = current_page

            page_target_count = min(
                current_row_count - current_row_offset,
                remaining if strict_target else export_limit - covered_count,
            )
            page_select_started_at = time.perf_counter()
            page_selected_count = self._select_rows_on_current_page(
                checkbox_items=checkbox_items,
                row_offset=current_row_offset,
                page_target_count=page_target_count,
                row_count=current_row_count,
                selected_before_page=selected_count,
            )
            logger.debug(
                "当前页勾选策略执行完成: current_page=%s, row_offset=%s, page_target_count=%s, page_selected_count=%s, elapsed_ms=%s",
                current_page,
                current_row_offset,
                page_target_count,
                page_selected_count,
                int((time.perf_counter() - page_select_started_at) * 1000),
            )
            covered_count += page_target_count
            selected_count += page_selected_count
            if strict_target:
                remaining = export_limit - selected_count
            current_row_offset += page_target_count
            logger.info(
                "当前页勾选完成: strict_target=%s, row_offset=%s, target=%s, actual=%s, covered=%s, selected=%s, remaining=%s",
                strict_target,
                current_row_offset - page_target_count,
                page_target_count,
                page_selected_count,
                selected_count,
                covered_count,
                remaining,
            )

            if strict_target and remaining <= 0:
                break
            if not strict_target and covered_count >= export_limit:
                break
            if current_row_offset < current_row_count:
                continue
            page_turn_started_at = time.perf_counter()
            if not self._goto_next_results_page():
                logger.debug("当前页已勾满但翻页失败或不存在下一页，结束本轮勾选")
                break
            logger.debug(
                "当前页已勾满并翻页成功，准备进入下一页勾选判定: elapsed_ms=%s",
                int((time.perf_counter() - page_turn_started_at) * 1000),
            )
            current_row_offset = 0

        if selected_count <= 0:
            raise ValidationError("结果页未选中任何文献，无法导出")

        already_at_target = export_limit - selected_count <= 0

        return {
            "selected_count": selected_count,
            "next_row_offset": current_row_offset,
            "page_row_count": current_row_count,
            "already_at_target": already_at_target,
            "start_page": start_page,
            "end_page": end_page,
        }

    def _coerce_current_results_page(self, summary: Dict[str, Any]) -> int:
        """将结果页摘要中的页码安全转换为整数。"""
        try:
            return int(self._resolve_current_results_page_number(summary))
        except (TypeError, ValueError) as exc:
            logger.debug("解析当前页码失败: summary=%s, error=%s", summary, exc)
            return 0

    def _select_rows_on_current_page(
        self,
        checkbox_items: list[Locator],
        row_offset: int,
        page_target_count: int,
        row_count: int,
        selected_before_page: int,
    ) -> int:
        strategy_started_at = time.perf_counter()
        current_page_size = self.PREFERRED_RESULTS_PAGE_SIZE
        try:
            current_page_size = self._current_results_page_size()
        except Exception as exc:
            logger.debug("读取当前分页大小失败，使用默认值继续: error=%s", exc)
        if row_offset == 0 and page_target_count == row_count and row_count >= current_page_size:
            logger.debug(
                "当前页满足整页勾选条件，准备尝试页级全选: row_count=%s, selected_before_page=%s",
                row_count,
                selected_before_page,
            )
            if self._try_select_all_on_current_page(
                expected_increase=page_target_count,
                selected_before_page=selected_before_page,
            ):
                logger.debug(
                    "当前页页级全选校验通过: elapsed_ms=%s",
                    int((time.perf_counter() - strategy_started_at) * 1000),
                )
                return page_target_count

        logger.debug(
            "当前页改为逐条勾选: row_offset=%s, page_target_count=%s, row_count=%s",
            row_offset,
            page_target_count,
            row_count,
        )

        if row_offset == 0 and page_target_count < row_count:
            logger.debug(
                "部分页目标，跳过页级全选，仅逐条勾选: page_target_count=%s, row_count=%s",
                page_target_count,
                row_count,
            )
        if row_offset == 0 and page_target_count == row_count and row_count < current_page_size:
            logger.debug(
                "当前页候选数量小于分页大小，跳过页级全选保护: row_count=%s, current_page_size=%s",
                row_count,
                current_page_size,
            )

        self._select_rows_incrementally(
            checkbox_items=checkbox_items,
            row_offset=row_offset,
            page_target_count=page_target_count,
            selected_before_page=0,
        )
        selected_count = self._count_checked_rows(
            checkbox_items=checkbox_items,
            row_offset=row_offset,
            page_target_count=page_target_count,
        )
        if selected_count >= page_target_count:
            logger.debug(
                "当前页逐条勾选执行完成: row_offset=%s, page_target_count=%s, actual=%s, elapsed_ms=%s",
                row_offset,
                page_target_count,
                selected_count,
                int((time.perf_counter() - strategy_started_at) * 1000),
            )
            return selected_count

        missing_indexes = self._find_unchecked_row_indexes(
            checkbox_items=checkbox_items,
            row_offset=row_offset,
            page_target_count=page_target_count,
        )
        if missing_indexes:
            logger.warning(
                "当前页逐条勾选后存在缺口，尝试页内补勾: row_offset=%s, target=%s, missing=%s",
                row_offset,
                page_target_count,
                len(missing_indexes),
            )
            for index in missing_indexes:
                self._ensure_checkbox_checked(checkbox_items[index], selector=f"result_checkbox[{index}]")
            selected_count = self._count_checked_rows(
                checkbox_items=checkbox_items,
                row_offset=row_offset,
                page_target_count=page_target_count,
            )

        if selected_count < page_target_count:
            logger.warning(
                "当前页补勾后仍未达标，将保留缺口继续处理: row_offset=%s, target=%s, actual=%s",
                row_offset,
                page_target_count,
                selected_count,
            )
        logger.debug(
            "当前页逐条勾选执行完成: row_offset=%s, page_target_count=%s, actual=%s, elapsed_ms=%s",
            row_offset,
            page_target_count,
            selected_count,
            int((time.perf_counter() - strategy_started_at) * 1000),
        )
        return selected_count

    def _try_select_all_on_current_page(self, expected_increase: int, selected_before_page: int) -> bool:
        """尝试对当前页执行页级全选，并校验已选数量增量是否符合预期。"""
        selectors = [
            "input[name='selectArticleAll']",
            "input[type='checkbox'][name='selectArticleAll']",
            "#selectAll",
            ".select-all input",
            "th input[type='checkbox']",
            "thead input[type='checkbox']",
        ]
        select_all = None
        for sel in selectors:
            loc = self.page.locator(sel).first
            if loc.count() > 0:
                select_all = loc
                logger.debug("找到全选控件: selector=%s", sel)
                break

        if select_all is None or select_all.count() == 0:
            logger.debug("当前页不存在页级全选控件，回退逐条勾选")
            return False

        self._ensure_checkbox_checked(select_all, selector="input[name='selectArticleAll']")
        selected_after_page = self._extract_selected_count(0)
        if selected_after_page <= selected_before_page:
            selected_after_page = self._extract_selected_count(selected_before_page + expected_increase)
        actual_increase = max(selected_after_page - selected_before_page, 0)
        logger.debug(
            "页级全选后数量校验: selected_before=%s, selected_after=%s, expected_increase=%s, actual_increase=%s",
            selected_before_page,
            selected_after_page,
            expected_increase,
            actual_increase,
        )
        if actual_increase == expected_increase:
            return True
        if actual_increase == 0:
            selected_after_page = self._extract_selected_count(selected_before_page + expected_increase)
            actual_increase = max(selected_after_page - selected_before_page, 0)
            if actual_increase >= expected_increase:
                logger.debug("页级全选后数量校验(兜底): actual_increase=%s >= expected_increase=%s", actual_increase, expected_increase)
                return True

        logger.warning(
            "结果页全选数量异常，回退逐条勾选: expected_increase=%s, actual_increase=%s",
            expected_increase,
            actual_increase,
        )
        self._disable_checkbox("input[name='selectArticleAll']")
        selected_after_rollback = self._extract_selected_count(selected_before_page)
        logger.debug(
            "结果页全选回退后数量校验: selected_before=%s, selected_after_rollback=%s",
            selected_before_page,
            selected_after_rollback,
        )
        return False

    def _select_rows_incrementally(
        self,
        checkbox_items: list[Locator],
        row_offset: int,
        page_target_count: int,
        selected_before_page: int,
    ) -> None:
        """逐条勾选当前页记录，页内不再逐条读取已选数量。"""
        del selected_before_page
        incremental_started_at = time.perf_counter()
        for index in range(row_offset, row_offset + page_target_count):
            if index >= len(checkbox_items):
                logger.debug(
                    "复选框索引超出范围: index=%s, len=%s",
                    index,
                    len(checkbox_items),
                )
                break
            checkbox = checkbox_items[index]

            max_retries = 3
            for retry in range(max_retries):
                try:
                    self._ensure_checkbox_checked(checkbox, selector=f"result_checkbox[{index}]")
                    break
                except Exception as exc:
                    if retry < max_retries - 1:
                        logger.debug(
                            "复选框勾选失败，准备重试: index=%s, attempt=%s/%s, error=%s",
                            index,
                            retry + 1,
                            max_retries,
                            exc,
                        )
                        self.page.evaluate("window.scrollBy(0, 50)")
                        time.sleep(0.3)
                    else:
                        raise
        logger.debug(
            "逐条勾选阶段完成: row_offset=%s, page_target_count=%s, elapsed_ms=%s",
            row_offset,
            page_target_count,
            int((time.perf_counter() - incremental_started_at) * 1000),
        )

    def _rollback_excess_selection(
        self,
        target_count: int,
        checkbox_items: list[Locator],
        max_uncheck_count: int,
    ) -> None:
        """回滚多余的选中项，只保留目标数量。"""
        current_selected = self._extract_selected_count(0)
        if current_selected <= target_count:
            logger.debug("当前选中数量未超过目标，无需回滚: current=%s, target=%s", current_selected, target_count)
            return

        excess_count = current_selected - target_count
        logger.debug(
            "开始回滚多余的选中项: current_selected=%s, target_count=%s, excess_count=%s",
            current_selected,
            target_count,
            excess_count,
        )

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
                        logger.debug("回滚完成，当前选中数量已达到目标: %s", current_selected)
                        break
                except Exception as exc:
                    logger.debug("取消选中失败: index=%s, error=%s", index, exc)

        logger.debug("回滚完成: 取消选中数量=%s, 当前选中=%s", uncheck_count, self._extract_selected_count(0))

    def _clear_page_selection(self, checkbox_items: list[Locator]) -> None:
        """清除当前页所有选中项。"""
        for checkbox in checkbox_items:
            if self._is_checkbox_checked(checkbox, None):
                try:
                    click_target = self._resolve_checkbox_click_target(checkbox)
                    if click_target is not None:
                        click_target.evaluate(
                            """
                            (el) => {
                                el.classList.remove('layui-form-checked');
                                const event = new MouseEvent('click', { bubbles: true, cancelable: true });
                                el.dispatchEvent(event);
                            }
                            """
                        )
                        if not self._is_checkbox_checked(checkbox, click_target):
                            continue
                    checkbox.uncheck(force=True, timeout=self._action_timeout_ms())
                except Exception as exc:
                    logger.debug("清除选中项失败: error=%s", exc)

        time.sleep(0.2)
        logger.debug("页面选中状态已清除: 当前选中=%s", self._extract_selected_count(0))

    def _ensure_checkbox_checked(self, checkbox: Locator, selector: str = "") -> None:
        click_target = self._resolve_checkbox_click_target(checkbox)
        if self._is_checkbox_checked(checkbox, click_target):
            return

        if click_target is not None:
            try:
                click_target.evaluate(
                    """
                    (el) => {
                        el.classList.add('layui-form-checked');
                        const event = new MouseEvent('click', { bubbles: true, cancelable: true });
                        el.dispatchEvent(event);
                    }
                    """
                )
                if self._is_checkbox_checked(checkbox, click_target):
                    return
            except Exception as exc:
                logger.debug("JS 点击 Layui 复选框失败: selector=%s, error=%s", selector or "<locator>", exc)

            try:
                click_target.scroll_into_view_if_needed(timeout=3000)
            except Exception as exc:
                logger.debug("Layui 复选框滚动到可视区域失败: selector=%s, error=%s", selector or "<locator>", exc)

            try:
                click_target.click(force=True, timeout=self._action_timeout_ms())
                if self._is_checkbox_checked(checkbox, click_target):
                    return
            except Exception as exc:
                logger.debug("点击 Layui 复选框失败，准备回退到原生 input: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.scroll_into_view_if_needed(timeout=3000)
        except Exception as exc:
            logger.debug("复选框滚动到可视区域失败: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.check(force=True, timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("常规勾选复选框失败，尝试使用 JS 兜底: selector=%s, error=%s", selector or "<locator>", exc)

        if self._is_checkbox_checked(checkbox, click_target):
            return
        raise TimeoutError(f"未能完成复选框勾选: {selector or '<locator>'}")

    def _prepare_next_batch_cursor(self, batch_selection: Dict[str, Any]) -> int:
        next_row_offset = batch_selection["next_row_offset"]
        page_row_count = batch_selection["page_row_count"]
        if next_row_offset < page_row_count:
            return next_row_offset
        if self._goto_next_results_page():
            return 0
        return next_row_offset

    def _count_checked_rows(
        self,
        checkbox_items: list[Locator],
        row_offset: int,
        page_target_count: int,
    ) -> int:
        """统计目标区间内实际勾选数量。"""
        selected_count = 0
        end_index = min(row_offset + page_target_count, len(checkbox_items))
        for index in range(row_offset, end_index):
            if self._is_checkbox_checked(checkbox_items[index], None):
                selected_count += 1
        return selected_count

    def _find_unchecked_row_indexes(
        self,
        checkbox_items: list[Locator],
        row_offset: int,
        page_target_count: int,
    ) -> list[int]:
        """返回目标区间内仍未勾选的下标。"""
        unchecked_indexes: list[int] = []
        end_index = min(row_offset + page_target_count, len(checkbox_items))
        for index in range(row_offset, end_index):
            if not self._is_checkbox_checked(checkbox_items[index], None):
                unchecked_indexes.append(index)
        return unchecked_indexes

    def _extract_selected_count(self, default_value: int) -> int:
        selectors = [
            "span[data-topcount='topcount']",
            ".checked-tip .mark-number",
            ".selected-count",
            "span.selected-count",
            ".selected-count span",
            "#selectCount",
            "span[data-count]",
        ]
        for selector in selectors:
            locator = self.page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                text = locator.inner_text().replace(",", "").replace("，", "").strip()
                import re
                numbers = re.findall(r'\d+', text)
                if numbers:
                    logger.debug(
                        "获取选中数量成功: selector=%s, text=%s, count=%s",
                        selector,
                        text,
                        numbers[0],
                    )
                    return int(numbers[0])
            except Exception as exc:
                logger.debug("获取选中数量失败: selector=%s, error=%s", selector, exc)
                continue
        logger.debug("未能获取选中数量，使用默认值: %s", default_value)
        return default_value
